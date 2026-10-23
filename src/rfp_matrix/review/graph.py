"""Actual LangGraph interrupt/checkpoint bridge, optional until runtime activation.

The domain decision and resume intent commit before Command(resume=event_id).
The pause node has no provider calls or writes before interrupt(). This module
never silently replaces LangGraph with a plain loop or in-memory checkpoint.
"""

from __future__ import annotations

from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator, TypedDict

from .store import ReviewError, ReviewStore, digest


class _ReviewState(TypedDict, total=False):
    presentation: dict
    decision_event_id: str


class LangGraphReviewDriver:
    def __init__(self, checkpoint_path: str | Path | None = None):
        self.checkpoint_path = Path(checkpoint_path).expanduser().resolve() if checkpoint_path else None

    @contextmanager
    def _graph(self, store: ReviewStore) -> Iterator[tuple[Any, Any]]:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.graph import END, START, StateGraph
            from langgraph.types import Command, interrupt
        except ImportError as exc:
            raise ReviewError("graph_unavailable", "Durable LangGraph dependencies are unavailable; no graph resume occurred.") from exc

        path = self.checkpoint_path or store.root / "checkpoints.sqlite3"
        if path.is_symlink() or path == store.db_path:
            raise ReviewError("unsafe_checkpoint_path", "Checkpoint database must be separate from the domain database.")
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = path.with_suffix(path.suffix + ".format.json")
        if metadata_path.is_symlink():
            raise ReviewError("unsafe_checkpoint_path", "Checkpoint compatibility record cannot be a symlink.")
        versions = {package: importlib.metadata.version(package)
                    for package in ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite")}
        prior_format = None
        if path.exists():
            if not metadata_path.is_file():
                raise ReviewError("checkpoint_unverified", "Existing checkpoint lacks an exact compatibility record; preserve it and explicitly recover in a new projection.")
            try:
                prior_format = json.loads(metadata_path.read_text(encoding="utf-8"))
                if prior_format.get("schema_version") != 1 or prior_format.get("versions") != versions:
                    raise ReviewError("checkpoint_unsupported", "Checkpoint dependencies changed; preserve it for explicit migration/recovery.")
                if prior_format.get("sqlite_schema") != self._schema(path):
                    raise ReviewError("checkpoint_unsupported", "Checkpoint schema differs from the exact recorded format.")
            except (OSError, ValueError, sqlite3.Error) as exc:
                if isinstance(exc, ReviewError):
                    raise
                raise ReviewError("checkpoint_corrupt", "Checkpoint database or compatibility record is corrupt/partial.") from exc
        elif metadata_path.exists():
            raise ReviewError("checkpoint_missing", "Checkpoint file is missing beside its compatibility record; explicit recovery is required.")

        def review_node(state: _ReviewState) -> dict:
            presentation = state["presentation"]
            event_id = interrupt(presentation)
            if not isinstance(event_id, str):
                raise ReviewError("invalid_resume", "Resume payload must identify one committed decision event.")
            event = store.event(presentation["run_id"], event_id)
            if (event["row_id"] != presentation["row_id"]
                    or event["assessment_generation"] != presentation["assessment_generation"]
                    or event["expected_row_revision"] != presentation["row_revision"]
                    or event.get("expected_decision_revision", event["expected_row_revision"]) != presentation.get("decision_revision", presentation["row_revision"])):
                raise ReviewError("invalid_resume", "Decision does not match the interrupted row revision.")
            return {"decision_event_id": event_id}

        with SqliteSaver.from_conn_string(str(path)) as checkpointer:
            # Public read API initializes the saver without running a graph node.
            checkpointer.get_tuple({"configurable": {"thread_id": "__m3_format_probe__"}})
            if prior_format is None:
                from .store import canonical
                record = {"schema_version": 1, "versions": versions, "sqlite_schema": self._schema(path)}
                with metadata_path.open("x", encoding="utf-8") as stream:
                    stream.write(canonical(record))
                    stream.flush()
                    os.fsync(stream.fileno())
            builder = StateGraph(_ReviewState)
            builder.add_node("review", review_node)
            builder.add_edge(START, "review")
            builder.add_edge("review", END)
            yield builder.compile(checkpointer=checkpointer), Command

    @staticmethod
    def _schema(path: Path) -> list:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReviewError("checkpoint_corrupt", "Checkpoint integrity failed.")
            schema = [list(row) for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
            tables = {row[1] for row in schema if row[0] == "table"}
            if not {"checkpoints", "writes"}.issubset(tables):
                raise ReviewError("checkpoint_unsupported", "Checkpoint schema is partial or unsupported.")
            return schema
        finally:
            connection.close()

    @staticmethod
    def _config(presentation: dict) -> dict:
        # Epoch changes are explicit recovery records; ordinary restart reuses ID.
        thread_id = "m3:" + digest("m3.graph-thread.v1", [presentation["run_id"], presentation["request_id"], presentation["epoch"]])
        return {"configurable": {"thread_id": thread_id}}

    def pause(self, store: ReviewStore, run_id: str, row_id: str) -> dict:
        snapshot = store.load_run(run_id)
        from .currentness import require_current
        require_current(snapshot, store._row(snapshot, row_id), provider=store.policy_provider)
        with store.run_lock(run_id):
            presentation = store.presentation(run_id, row_id)
            with self._graph(store) as (graph, _command):
                config = self._config(presentation)
                state = graph.get_state(config)
                rebuilt = False
                if not state.values and presentation["persisted_before"]:
                    store.recover_graph_epoch(run_id, "checkpoint_missing_during_pause")
                    presentation = store.presentation(run_id, row_id)
                    config = self._config(presentation)
                    state = graph.get_state(config)
                    rebuilt = True
                if state.values and state.values.get("decision_event_id"):
                    return {"status": "already_completed", "presentation": presentation,
                            "decision_event_id": state.values["decision_event_id"]}
                result = graph.invoke({"presentation": presentation}, config) if not state.values else None
                current = graph.get_state(config)
                if not current.next:
                    raise ReviewError("graph_not_interrupted", "Graph did not persist the expected review interrupt.")
                return {"status": "interrupted", "presentation": presentation,
                        "recovery_epoch_started": rebuilt,
                        "thread_id": config["configurable"]["thread_id"],
                        "checkpoint_id": (current.config or {}).get("configurable", {}).get("checkpoint_id"),
                        "interrupt_count": len(result.get("__interrupt__", ())) if isinstance(result, dict) else 1}

    def apply(self, store: ReviewStore, run_id: str, event: dict) -> dict:
        if store.applied(event["event_id"]):
            return {"status": "already_applied", "event_id": event["event_id"]}
        snapshot = store.load_run(run_id)
        current_row = next((row for row in snapshot["rows"] if row["row_id"] == event["row_id"]), None)
        if (event["assessment_generation"] != snapshot["assessment_generation"] or not current_row
                or current_row.get("decision_event_id") != event["event_id"] or current_row.get("retired")):
            store.mark_applied(run_id, event["event_id"], store.graph_epoch(run_id), "superseded_by_source_generation")
            return {"status": "superseded", "event_id": event["event_id"]}
        from .currentness import require_current
        if event.get("approval") in {"approved_positive", "approved_gap"}:
            require_current(snapshot, current_row, provider=store.policy_provider)
        presentation = store.event_presentation(run_id, event)
        rebuilt = False
        with self._graph(store) as (graph, command):
            config = self._config(presentation)
            state = graph.get_state(config)
            if not state.values:
                # Domain truth survives. Never claim an absent checkpoint resumed.
                epoch = store.recover_graph_epoch(run_id, "checkpoint_missing_or_no_prior_pause")
                presentation = store.event_presentation(run_id, event, epoch)
                config = self._config(presentation)
                graph.invoke({"presentation": presentation}, config)
                state = graph.get_state(config)
                rebuilt = True
            completed_id = state.values.get("decision_event_id")
            if completed_id and completed_id != event["event_id"]:
                raise ReviewError("checkpoint_conflict", "Checkpoint contains a different committed decision.")
            if not completed_id:
                if not state.next:
                    raise ReviewError("checkpoint_invalid", "Checkpoint has no resumable review node.")
                graph.invoke(command(resume=event["event_id"]), config)
            final = graph.get_state(config)
            if final.values.get("decision_event_id") != event["event_id"] or final.next:
                raise ReviewError("resume_unconfirmed", "Graph did not confirm the committed event.")
        store.mark_applied(run_id, event["event_id"], presentation["epoch"], "rebuilt_epoch" if rebuilt else "resumed_checkpoint")
        return {"status": "rebuilt_epoch" if rebuilt else "resumed_checkpoint", "event_id": event["event_id"], "epoch": presentation["epoch"]}
