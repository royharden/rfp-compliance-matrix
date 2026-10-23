"""Actual LangGraph interrupt/checkpoint bridge, optional until runtime activation.

The domain decision and resume intent commit before Command(resume=event_id).
The pause node has no provider calls or writes before interrupt(). This module
never silently replaces LangGraph with a plain loop or in-memory checkpoint.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
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

        def review_node(state: _ReviewState) -> dict:
            presentation = state["presentation"]
            event_id = interrupt(presentation)
            if not isinstance(event_id, str):
                raise ReviewError("invalid_resume", "Resume payload must identify one committed decision event.")
            event = store.event(presentation["run_id"], event_id)
            if (event["row_id"] != presentation["row_id"]
                    or event["assessment_generation"] != presentation["assessment_generation"]
                    or event["expected_row_revision"] != presentation["row_revision"]):
                raise ReviewError("invalid_resume", "Decision does not match the interrupted row revision.")
            return {"decision_event_id": event_id}

        with SqliteSaver.from_conn_string(str(path)) as checkpointer:
            builder = StateGraph(_ReviewState)
            builder.add_node("review", review_node)
            builder.add_edge(START, "review")
            builder.add_edge("review", END)
            yield builder.compile(checkpointer=checkpointer), Command

    @staticmethod
    def _config(presentation: dict) -> dict:
        # Epoch changes are explicit recovery records; ordinary restart reuses ID.
        thread_id = "m3:" + digest("m3.graph-thread.v1", [presentation["run_id"], presentation["request_id"], presentation["epoch"]])
        return {"configurable": {"thread_id": thread_id}}

    def pause(self, store: ReviewStore, run_id: str, row_id: str) -> dict:
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
        if event["assessment_generation"] != snapshot["assessment_generation"]:
            store.mark_applied(run_id, event["event_id"], store.graph_epoch(run_id), "superseded_by_source_generation")
            return {"status": "superseded", "event_id": event["event_id"]}
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
