"""Trusted single-local-reviewer application port; no HTTP/session state here.

The injected store is the ownership boundary. Authentication and CSRF belong to
the optional web adapter. Request data never selects an actor, store, provider,
module, file path or export destination. SQLite remains the only writable truth.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .store import ReviewError, ReviewStore, canonical, digest, safe_id


def _payload(value: Any, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> dict:
    if not isinstance(value, Mapping) or set(value) - required - optional or required - set(value):
        raise ReviewError("invalid_payload", "Unknown or missing application request fields.")
    return dict(value)


def _text(value: Any, name: str, *, maximum: int = 8000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ReviewError("invalid_payload", name + " must be bounded text.")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ReviewError("invalid_payload", name + " must be a JSON boolean.")
    return value


class ReviewerApplication:
    """One explicitly injected local store, never a caller-selected tenant/path."""

    def __init__(self, store: ReviewStore, *, registry: Any = None, export_root: str | Path | None = None):
        from rfp_matrix.source import SourceRegistry
        from .operations import _path
        self.store = store
        self.registry = registry if registry is not None else SourceRegistry(store.root / "sources")
        self.export_root = _path(export_root if export_root is not None else store.root / "exports")
        self.owner_id = store.store_id
        self.reviewer_subject = "reviewer-" + digest("m3.local-reviewer-subject.v1", {"store_id": store.store_id, "actor": store.actor})[:32]

    def list_runs(self, *, limit: int = 200) -> dict:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ReviewError("input_limit", "Run-list limit must be 1..200.")
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            records = connection.execute("SELECT snapshot FROM runs ORDER BY run_id LIMIT ?", (limit,)).fetchall()
            runs = []
            for record in records:
                snapshot = json.loads(record[0])
                runs.append({"run_id": snapshot["run_id"], "matrix_generation": snapshot["matrix_generation"],
                             "assessment_generation": snapshot["assessment_generation"],
                             "source_id": snapshot["artifact"]["source"]["source_id"], "row_count": len(snapshot["rows"]),
                             "fictional_vendor": True})
        return {"store_id": self.owner_id, "runs": runs, "total_runs": total, "truncated": total > len(runs)}

    def get_run(self, run_id: str) -> dict:
        safe_id(run_id)
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN")
            snapshot = self.store._load(connection, run_id)
            # Same read transaction: the row generation and coverage cannot be
            # assembled from different concurrent commits.
            snapshot["coverage"] = self.store._coverage(connection, snapshot)
            snapshot["decision_events"] = [json.loads(row[0]) for row in connection.execute("SELECT event FROM decisions WHERE run_id=? ORDER BY seq", (run_id,))]
            from .currentness import presented
            return presented(snapshot, provider=self.store.policy_provider)

    def list_rows(self, run_id: str, queue: str = "all") -> dict:
        if queue not in {"all", "pending", "approved_positive", "approved_gap", "rejected", "stale", "unresolved", "retired"}:
            raise ReviewError("invalid_queue", "Unknown reviewer queue.")
        snapshot = self.get_run(run_id)
        from .currentness import in_queue
        rows = sorted((row for row in snapshot["rows"] if in_queue(row, queue)), key=lambda row: row["row_id"])
        return {"run_id": run_id, "queue": queue, "rows": rows, "total_rows": len(snapshot["rows"]),
                "matrix_generation": snapshot["matrix_generation"], "assessment_generation": snapshot["assessment_generation"],
                "coverage": snapshot["coverage"], "change_report": snapshot.get("change_report"),
                "policy_status": snapshot["policy_status"],
                "change_record": snapshot.get("change_record")}

    def get_row(self, run_id: str, row_id: str) -> dict:
        safe_id(row_id)
        snapshot = self.get_run(run_id)
        row = next((row for row in snapshot["rows"] if row["row_id"] == row_id), None)
        if row is None:
            raise ReviewError("row_missing", "Row is not owned by this run.", run_id=run_id, row_id=row_id)
        return {"snapshot": snapshot, "row": row}

    def source_context(self, run_id: str, row_id: str) -> dict:
        from rfp_matrix.source import resolve_context
        from rfp_matrix.evidence import resolve_evidence
        selected = self.get_row(run_id, row_id)
        snapshot, row = selected["snapshot"], selected["row"]
        artifact = snapshot["artifact"]
        registered = self.registry.load(artifact["artifact_sha"])
        if canonical(registered) != canonical(artifact):
            raise ReviewError("artifact_corrupt", "Registry extraction does not match this run's immutable source.")
        anchors = row["requirement"]["anchors"]
        context = resolve_context(artifact, anchors)
        return {**context, "run_id": run_id, "row_id": row_id, "matrix_generation": snapshot["matrix_generation"],
                "row_revision": row["row_revision"], "source": artifact["source"], "context": context,
                "corpus_generation": snapshot["corpus"]["generation"],
                "support": [{"pointer": pointer, "resolved": resolve_evidence(snapshot["corpus"], pointer, allow_historical=True)} for pointer in row["assessment"]["support"]],
                "counterevidence": [{"pointer": pointer, "resolved": resolve_evidence(snapshot["corpus"], pointer, allow_historical=True)} for pointer in row["assessment"]["counterevidence"]],
                "policy_status": row["policy_status"], "literal_history_only": True,
                "context_is_evidence": False, "fictional_vendor": True}

    def coverage(self, run_id: str) -> dict:
        # The service verifies run ownership and reads its exact artifact ledger.
        safe_id(run_id)
        return self.store.coverage_state(run_id)

    def amendments(self, run_id: str, *, previous_matrix_generation: str | None = None) -> dict:
        return self.store.amendments(run_id, previous_matrix_generation=previous_matrix_generation)

    def record_correspondence(self, run_id: str, payload: Mapping[str, Any]) -> dict:
        return self.store.record_correspondence(run_id, payload)

    def inventory_audit(self, run_id: str) -> dict:
        wrapper = self.store.inventory_audit(run_id)
        from .currentness import presented
        wrapper["snapshot"] = presented(wrapper["snapshot"], provider=self.store.policy_provider)
        return wrapper

    def promote_inventory_candidate(self, run_id: str, payload: Mapping[str, Any]) -> dict:
        return self.store.promote_inventory_candidate(run_id, payload)

    def run_envelope(self, run_id: str, policy_context: Mapping[str, Any]) -> dict:
        return self.store.run_envelope(run_id, policy_context)

    def resume(self, run_id: str) -> dict:
        from .graph import LangGraphReviewDriver
        self.get_run(run_id)
        try:
            return self.store.reconcile(run_id, driver=LangGraphReviewDriver(self.store.root / "checkpoints.sqlite3"))
        except Exception as exc:
            return {"run_id": run_id, "status": "checkpoint_unavailable", "code": getattr(exc, "code", "dependency_unavailable"),
                    "reason": str(exc), "domain_decisions_preserved": True, "runtime_verified": False}

    def decide(self, run_id: str, row_id: str, payload: Mapping[str, Any]) -> dict:
        value = _payload(payload, {"action", "expected_assessment_generation", "expected_row_revision", "request_id"},
                         {"reason", "answer_text", "human_validated"})
        self.get_row(run_id, row_id)
        for name in ("action", "expected_assessment_generation", "expected_row_revision", "request_id"):
            _text(value[name], name, maximum=160)
        reason = _text(value.get("reason", ""), "reason", empty=True)
        answer = value.get("answer_text")
        if answer is not None:
            _text(answer, "answer_text", maximum=20000, empty=True)
        event = self.store.decide(run_id, row_id, value["action"], expected_assessment_generation=value["expected_assessment_generation"],
                                  expected_row_revision=value["expected_row_revision"], request_id=value["request_id"],
                                  reason=reason, answer_text=answer, human_validated=_boolean(value.get("human_validated", False), "human_validated"))
        # Decision is committed before optional projection work. A missing graph
        # remains visible alongside the durable event, never a rolled-back claim.
        reconcile = self.resume(run_id)
        snapshot = self.get_run(run_id)
        return {"event": event, "snapshot": snapshot, "reconcile": reconcile,
                "historical_event": event["matrix_generation"] != snapshot["matrix_generation"]}

    def inspect_coverage(self, run_id: str, payload: Mapping[str, Any]) -> dict:
        required = {"page_index", "action", "expected_artifact_sha", "expected_page_hash", "expected_matrix_generation",
                    "expected_coverage_revision", "request_id", "reason"}
        value = _payload(payload, required, {"inspected_original"})
        self.get_run(run_id)
        if type(value["page_index"]) is not int:
            raise ReviewError("invalid_payload", "page_index must be a zero-based integer.")
        for name in required - {"page_index", "expected_coverage_revision"}:
            _text(value[name], name, maximum=4000 if name == "reason" else 160)
        if value["expected_coverage_revision"] is not None:
            _text(value["expected_coverage_revision"], "expected_coverage_revision", maximum=160)
        return self.store.inspect_coverage(run_id, value["page_index"], value["action"],
            expected_artifact_sha=value["expected_artifact_sha"], expected_page_hash=value["expected_page_hash"],
            expected_matrix_generation=value["expected_matrix_generation"], expected_coverage_revision=value["expected_coverage_revision"],
            request_id=value["request_id"], reason=value["reason"],
            inspected_original=_boolean(value.get("inspected_original", False), "inspected_original"))

    def export(self, run_id: str, payload: Mapping[str, Any]) -> dict:
        from rfp_matrix.export import export_matrix
        value = _payload(payload, {"expected_matrix_generation", "request_id"}, {"formats"})
        self.get_run(run_id)
        for name in ("expected_matrix_generation", "request_id"):
            _text(value[name], name, maximum=160)
        formats = value.get("formats", ["html", "csv", "md"])
        if not isinstance(formats, list) or not formats or any(not isinstance(item, str) or item not in {"html", "csv", "md"} for item in formats):
            raise ReviewError("invalid_payload", "formats must be a nonempty list of html/csv/md.")
        return export_matrix(self.store, run_id, self.export_root, request_id=value["request_id"],
                             expected_matrix_generation=value["expected_matrix_generation"], formats=formats)

    def export_history(self, run_id: str, *, limit: int = 50) -> dict:
        self.get_run(run_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ReviewError("input_limit", "Export-history limit must be 1..200.")
        with closing(self.store._connect()) as connection:
            connection.execute("BEGIN")
            current = self.store._load(connection, run_id)
            rows = connection.execute("SELECT export_id,created_at,state,snapshot,artifacts FROM export_intents WHERE run_id=? ORDER BY created_at DESC,export_id LIMIT ?", (run_id, limit)).fetchall()
            exports = []
            for row in rows:
                frozen = json.loads(row["snapshot"])
                from .currentness import policy_review
                policy = policy_review(frozen, provider=self.store.policy_provider)
                exports.append({"export_id": row["export_id"], "created_at": row["created_at"], "state": row["state"],
                                "matrix_generation": frozen["matrix_generation"],
                                "historical": frozen["matrix_generation"] != current["matrix_generation"] or not policy["allows_current_claim"],
                                "policy_status": policy,
                                "files": sorted(json.loads(row["artifacts"])) if row["artifacts"] else []})
        return {"run_id": run_id, "matrix_generation": current["matrix_generation"], "exports": exports, "limit": limit}

    def export_file(self, run_id: str, export_id: str, filename: str, *, allow_historical: bool = False) -> dict:
        """Return bounded verified bytes, never an HTTP-selected file path."""
        from .operations import _path
        safe_id(run_id)
        if not isinstance(export_id, str) or not re.fullmatch(r"X-[0-9a-f]{32}", export_id):
            raise ReviewError("invalid_id", "Expected an application export identifier.")
        media = {"matrix.html": "text/html; charset=utf-8", "matrix.csv": "text/csv; charset=utf-8",
                 "matrix.md": "text/markdown; charset=utf-8", "manifest.json": "application/json", "coverage.json": "application/json",
                 "inventory-audit.json": "application/json"}
        if not isinstance(filename, str) or filename not in media:
            raise ReviewError("invalid_artifact", "Filename is not an export artifact.")
        _boolean(allow_historical, "allow_historical")
        with self.store.run_lock(run_id), closing(self.store._connect()) as connection:
            connection.execute("BEGIN")
            current = self.store._load(connection, run_id)
            record = connection.execute("SELECT snapshot,artifacts,state FROM export_intents WHERE run_id=? AND export_id=?", (run_id, export_id)).fetchone()
            if not record:
                raise ReviewError("export_missing", "Export is not owned by this run/store.")
            artifacts = json.loads(record["artifacts"]) if record["artifacts"] else {}
            if record["state"] != "published" or filename not in artifacts:
                raise ReviewError("export_unavailable", "Export artifact is not durably published.")
            frozen = json.loads(record["snapshot"])
            from .currentness import policy_review
            policy = policy_review(frozen, provider=self.store.policy_provider)
            historical = frozen["matrix_generation"] != current["matrix_generation"] or not policy["allows_current_claim"]
            if historical and not allow_historical:
                raise ReviewError("historical_export", "Explicitly request the labelled historical export; it is no longer current.", matrix_generation=current["matrix_generation"])
            path = _path(self.export_root / export_id / filename)
            if path.parent.parent != self.export_root or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
                raise ReviewError("invalid_artifact", "Expected a bounded published artifact under the configured export root.")
            with path.open("rb") as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
            if len(data) != artifacts[filename]["size_bytes"] or hashlib.sha256(data).hexdigest() != artifacts[filename]["sha256"]:
                raise ReviewError("artifact_corrupt", "Published bytes differ from committed artifact hashes.")
        return {"content": data, "filename": filename, "media_type": media[filename], "export_id": export_id,
                "run_id": run_id, "matrix_generation": frozen["matrix_generation"], "historical": historical,
                "policy_status": policy,
                "current_at_read": not historical, "currentness_warning": "Frozen snapshot; later changes can make this historical."}

    def download_export(self, run_id: str, request_id: str, filename: str) -> dict:
        """D4 route seam: request ID lookup, scoped ownership, verified bytes and label."""
        safe_id(run_id)
        safe_id(request_id)
        with closing(self.store._connect()) as connection:
            self.store._load(connection, run_id)
            row = connection.execute("SELECT export_id FROM export_intents WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if not row:
                raise ReviewError("export_missing", "No export request is owned by this run.")
            export_id = row["export_id"]
        # Historical downloads are explicitly labelled by D4's attachment name,
        # response header and the immutable document's own static warning.
        result = self.export_file(run_id, export_id, filename, allow_historical=True)
        return {**result, "sha256": hashlib.sha256(result["content"]).hexdigest(), "current": result["current_at_read"]}
