"""Same-run historical correspondence. Human lineage never carries approval."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
import json
from typing import Any

from .store import ReviewError, canonical, digest, matrix_generation, safe_id, utc_now


FIELDS = {"relation", "old_rows", "new_rows", "expected_old_matrix_generation", "expected_matrix_generation",
          "expected_old_assessment_generation", "expected_new_assessment_generation", "expected_old_artifact_sha",
          "expected_new_artifact_sha", "expected_correspondence_revision", "request_id", "reason"}


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ReviewError("invalid_correspondence", field + " must be an exact SHA-256 identity.")
    return value


def _refs(value: Any) -> list[dict]:
    if not isinstance(value, list) or len(value) > 50:
        raise ReviewError("invalid_correspondence", "Select at most 50 registered rows per side.")
    seen = set()
    result = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {"row_id", "row_revision"}:
            raise ReviewError("invalid_correspondence", "Selections contain only registered row_id and content row_revision.")
        identity = safe_id(row["row_id"])
        if identity in seen:
            raise ReviewError("invalid_correspondence", "Duplicate correspondence row selection.")
        seen.add(identity)
        result.append({"row_id": identity, "row_revision": _sha(row["row_revision"], "row_revision")})
    return sorted(result, key=lambda row: row["row_id"])


def _history(connection, run_id: str, current: dict, previous: str | None) -> tuple[int, dict]:
    if previous is not None:
        _sha(previous, "previous_matrix_generation")
        if previous == current["matrix_generation"]:
            raise ReviewError("invalid_amendment_history", "Choose a distinct historical matrix generation.")
    records = connection.execute("SELECT seq,snapshot FROM history WHERE run_id=? ORDER BY seq DESC LIMIT 10001", (run_id,)).fetchall()
    if len(records) > 10000:
        raise ReviewError("amendment_history_limit", "Historical lookup exceeds this bounded source adapter.")
    for record in records:
        snapshot = json.loads(record["snapshot"])
        match = snapshot["matrix_generation"] == previous if previous is not None else snapshot["assessment_generation"] != current["assessment_generation"]
        if match:
            if snapshot.get("run_id") != run_id or matrix_generation(snapshot) != snapshot["matrix_generation"]:
                raise ReviewError("domain_history_inconsistent", "Historical snapshot identity does not match this run.")
            return record["seq"], snapshot
    raise ReviewError("amendment_history_unavailable", "No matching historical snapshot is owned by this run.", matrix_generation=current["matrix_generation"])


def _report(connection, run_id: str, current: dict, previous: str | None) -> tuple[int, dict, dict]:
    from rfp_matrix.evidence import amendment_candidates
    sequence, old = _history(connection, run_id, current, previous)
    return sequence, old, amendment_candidates(old, current, limit=200)


def _events(connection, run_id: str, comparison_id: str) -> list[dict]:
    rows = connection.execute("SELECT event FROM correspondence_events WHERE run_id=? AND comparison_id=? ORDER BY seq LIMIT 10001",
                              (run_id, comparison_id)).fetchall()
    if len(rows) > 10000:
        raise ReviewError("correspondence_history_limit", "Comparison event history exceeds replay bound.")
    events = [json.loads(row[0]) for row in rows]
    previous = None
    for event in events:
        content = {key: item for key, item in event.items() if key != "revision"}
        if (event["comparison_id"] != comparison_id or event["previous_revision"] != previous
                or event["revision"] != digest("m3.correspondence-revision.v1", content)):
            raise ReviewError("correspondence_history_corrupt", "Correspondence history is incomplete, reordered or changed.")
        previous = event["revision"]
    return events


def amendments(store, run_id: str, *, previous_matrix_generation: str | None = None) -> dict:
    safe_id(run_id)
    with closing(store._connect()) as connection:
        connection.execute("BEGIN")
        current = store._load(connection, run_id)
        try:
            sequence, _old, report = _report(connection, run_id, current, previous_matrix_generation)
        except ReviewError as exc:
            if exc.code != "amendment_history_unavailable" or previous_matrix_generation is not None:
                raise
            return {"schema_version": 1, "run_id": run_id, "matrix_generation": current["matrix_generation"],
                    "status": "unavailable", "code": exc.code, "comparison": {}, "human_events": [],
                    "correspondence_revision": None, "approval_carry_allowed": False}
        events = _events(connection, run_id, report["comparison_id"])
        # Do not mutate D5's hashed pure report to attach application history.
        return {"schema_version": 1, "run_id": run_id, "matrix_generation": current["matrix_generation"],
                "comparison": report, "human_events": events, "old_history_sequence": sequence,
                "correspondence_revision": events[-1]["revision"] if events else None,
                "approval_carry_allowed": False, "decision_changed": False}


def record_correspondence(store, run_id: str, payload: Mapping[str, Any]) -> dict:
    safe_id(run_id)
    if not isinstance(payload, Mapping) or set(payload) != FIELDS:
        raise ReviewError("invalid_correspondence", "Unknown/missing correspondence fields; actor/path/span/approval are forbidden.")
    value = dict(payload)
    if value["expected_correspondence_revision"] == "":
        value["expected_correspondence_revision"] = None
    safe_id(value["request_id"])
    if not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 8000:
        raise ReviewError("correspondence_reason_required", "A bounded human correspondence rationale is required.")
    if not isinstance(value["relation"], str) or value["relation"] not in {"one_to_one", "split", "merge", "added", "removed"}:
        raise ReviewError("invalid_correspondence", "Unknown correspondence relation.")
    for name in FIELDS:
        if name.startswith("expected_") and name != "expected_correspondence_revision":
            _sha(value[name], name)
    if value["expected_correspondence_revision"] is not None:
        _sha(value["expected_correspondence_revision"], "expected_correspondence_revision")
    value["old_rows"], value["new_rows"] = _refs(value["old_rows"]), _refs(value["new_rows"])
    payload_hash = digest("m3.correspondence-request.v1", {"run_id": run_id, "actor": store.actor, "payload": value})
    with store.transaction(run_id) as connection:
        prior = connection.execute("SELECT payload_hash,event FROM correspondence_events WHERE run_id=? AND request_id=?", (run_id, value["request_id"])).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise ReviewError("idempotency_conflict", "Correspondence request ID already binds another exact intent.")
            event = json.loads(prior["event"])
            current = store._load(connection, run_id)
            return {"event": event, "replayed": True, "matrix_generation": current["matrix_generation"],
                    "historical_event": event["new_identity"]["matrix_generation"] != current["matrix_generation"],
                    "correspondence_revision": event["revision"], "approval_carry_allowed": False, "decision_changed": False}
        for table in ("decisions", "revision_requests", "coverage_events", "export_intents", "inventory_promotions"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE run_id=? AND request_id=?", (run_id, value["request_id"])).fetchone():
                raise ReviewError("idempotency_conflict", "Request ID belongs to another domain operation.")
        current = store._load(connection, run_id)
        if value["expected_matrix_generation"] != current["matrix_generation"]:
            raise ReviewError("stale_correspondence", "Refresh the current matrix before confirming correspondence.",
                              matrix_generation=current["matrix_generation"], assessment_generation=current["assessment_generation"])
        old_sequence, _old, report = _report(connection, run_id, current, value["expected_old_matrix_generation"])
        old_identity, new_identity = report["old_identity"], report["new_identity"]
        if (value["expected_old_assessment_generation"] != old_identity["assessment_generation"]
                or value["expected_new_assessment_generation"] != new_identity["assessment_generation"]
                or value["expected_old_artifact_sha"] != old_identity["artifact_sha"]
                or value["expected_new_artifact_sha"] != new_identity["artifact_sha"]):
            raise ReviewError("stale_correspondence", "Exact old/new artifact or assessment generation changed.",
                              matrix_generation=current["matrix_generation"], old_identity=old_identity, new_identity=new_identity)
        events = _events(connection, run_id, report["comparison_id"])
        if len(events) >= 10000:
            raise ReviewError("correspondence_history_limit", "Comparison event history reached its replay bound.")
        previous_revision = events[-1]["revision"] if events else None
        if value["expected_correspondence_revision"] != previous_revision:
            raise ReviewError("correspondence_conflict", "Correspondence changed; inspect the current ledger before appending.",
                              matrix_generation=current["matrix_generation"], correspondence_revision=previous_revision)
        from rfp_matrix.evidence import validate_correspondence_selection
        try:
            validate_correspondence_selection(report, comparison_id=report["comparison_id"],
                expected_old_identity=old_identity, expected_new_identity=new_identity, relation=value["relation"],
                old_row_revisions={row["row_id"]: row["row_revision"] for row in value["old_rows"]},
                new_row_revisions={row["row_id"]: row["row_revision"] for row in value["new_rows"]})
        except ValueError as exc:
            if getattr(exc, "code", "").startswith("stale_"):
                raise ReviewError(exc.code, str(exc), matrix_generation=current["matrix_generation"],
                    assessment_generation=current["assessment_generation"], correspondence_revision=previous_revision,
                    old_identity=old_identity, new_identity=new_identity) from exc
            raise
        latest = connection.execute("SELECT seq,snapshot FROM history WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        if not latest or json.loads(latest["snapshot"])["matrix_generation"] != current["matrix_generation"]:
            raise ReviewError("domain_history_inconsistent", "Current matrix has no matching durable history record.")
        event_id = "A-" + digest("m3.correspondence-event.v1", [store.store_id, run_id, value["request_id"], payload_hash])[:32]
        inserted = connection.execute("INSERT INTO correspondence_events(event_id,run_id,comparison_id,request_id,payload_hash,payload,old_history_seq,new_history_seq,event) VALUES(?,?,?,?,?,?,?,?,?)",
            (event_id, run_id, report["comparison_id"], value["request_id"], payload_hash, canonical(value), old_sequence, latest["seq"], "{}"))
        event = {"schema_version": 1, "event_id": event_id, "sequence": inserted.lastrowid, "run_id": run_id,
            "comparison_id": report["comparison_id"], "relation": value["relation"], "old_rows": value["old_rows"], "new_rows": value["new_rows"],
            "old_row_ids": [row["row_id"] for row in value["old_rows"]], "new_row_ids": [row["row_id"] for row in value["new_rows"]],
            "old_identity": old_identity, "new_identity": new_identity, "old_history_sequence": old_sequence, "new_history_sequence": latest["seq"],
            "actor": store.actor, "request_id": value["request_id"], "reason": value["reason"], "created_at": utc_now(),
            "payload_hash": payload_hash, "previous_revision": previous_revision, "approval_carry_allowed": False, "decision_changed": False,
            "semantics": "human_recorded_lineage_not_compliance_or_semantic_equivalence"}
        event["revision"] = digest("m3.correspondence-revision.v1", event)
        # SQLite allocates sequence inside this transaction; the final event is
        # committed exactly once. No post-commit history edits occur.
        connection.execute("UPDATE correspondence_events SET event=? WHERE seq=?", (canonical(event), inserted.lastrowid))
        return {"event": event, "replayed": False, "matrix_generation": current["matrix_generation"],
                "correspondence_revision": event["revision"], "approval_carry_allowed": False, "decision_changed": False}


def correspondence_history(store, run_id: str, *, limit: int = 100) -> dict:
    safe_id(run_id)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ReviewError("input_limit", "Correspondence history limit must be 1..500.")
    with closing(store._connect()) as connection:
        connection.execute("BEGIN")
        current = store._load(connection, run_id)
        total = connection.execute("SELECT COUNT(*) FROM correspondence_events WHERE run_id=?", (run_id,)).fetchone()[0]
        rows = connection.execute("SELECT event FROM correspondence_events WHERE run_id=? ORDER BY seq DESC LIMIT ?", (run_id, limit)).fetchall()
        events = [json.loads(row[0]) for row in reversed(rows)]
        return {"schema_version": 1, "run_id": run_id, "matrix_generation": current["matrix_generation"], "events": events,
                "total_events": total, "truncated": total > len(events), "limit": limit, "approval_carry_allowed": False}


def validate_durable_history(connection, histories: dict[int, dict]) -> int:
    """Maintenance structural lineage check, not authenticity or product acceptance."""
    from rfp_matrix.evidence import amendment_candidates, validate_correspondence_selection
    records = connection.execute("SELECT * FROM correspondence_events ORDER BY seq LIMIT 10001").fetchall()
    if len(records) > 10000:
        raise ReviewError("correspondence_history_limit", "Maintenance correspondence bound exceeded.")
    reports: dict[tuple[int, int], dict] = {}
    groups = set()
    for record in records:
        event = json.loads(record["event"])
        payload = json.loads(record["payload"])
        old = histories.get(record["old_history_seq"])
        new = histories.get(record["new_history_seq"])
        if (not old or not new or old["run_id"] != record["run_id"] or new["run_id"] != record["run_id"]
                or event["sequence"] != record["seq"] or event["event_id"] != record["event_id"]
                or event["run_id"] != record["run_id"] or event["request_id"] != record["request_id"]
                or event["old_history_sequence"] != record["old_history_seq"] or event["new_history_sequence"] != record["new_history_seq"]
                or event["comparison_id"] != record["comparison_id"] or event["approval_carry_allowed"] is not False
                or event["decision_changed"] is not False):
            raise ReviewError("correspondence_history_corrupt", "Correspondence event does not bind its same-run historical records.")
        key = (record["old_history_seq"], record["new_history_seq"])
        if key not in reports:
            if len(reports) >= 1000:
                raise ReviewError("correspondence_history_limit", "Maintenance comparison bound exceeded.")
            reports[key] = amendment_candidates(old, new, limit=200)
        report = reports[key]
        if (event["old_identity"] != report["old_identity"] or event["new_identity"] != report["new_identity"]
                or event["comparison_id"] != report["comparison_id"]
                or payload["expected_old_matrix_generation"] != old["matrix_generation"]
                or payload["expected_matrix_generation"] != new["matrix_generation"]
                or payload["expected_old_assessment_generation"] != old["assessment_generation"]
                or payload["expected_new_assessment_generation"] != new["assessment_generation"]
                or payload["expected_old_artifact_sha"] != old["artifact"]["artifact_sha"]
                or payload["expected_new_artifact_sha"] != new["artifact"]["artifact_sha"]
                or payload["expected_correspondence_revision"] != event["previous_revision"]
                or payload["old_rows"] != event["old_rows"] or payload["new_rows"] != event["new_rows"]
                or payload["request_id"] != event["request_id"]
                or event["old_row_ids"] != [row["row_id"] for row in event["old_rows"]]
                or event["new_row_ids"] != [row["row_id"] for row in event["new_rows"]]
                or payload["reason"] != event["reason"] or payload["relation"] != event["relation"]):
            raise ReviewError("correspondence_history_corrupt", "Correspondence pins/payload differ from their immutable source snapshots.")
        expected_hash = digest("m3.correspondence-request.v1", {"run_id": record["run_id"], "actor": event["actor"], "payload": payload})
        if expected_hash != record["payload_hash"] or expected_hash != event["payload_hash"]:
            raise ReviewError("correspondence_history_corrupt", "Correspondence request digest mismatch.")
        validate_correspondence_selection(report, comparison_id=report["comparison_id"],
            expected_old_identity=report["old_identity"], expected_new_identity=report["new_identity"], relation=event["relation"],
            old_row_revisions={row["row_id"]: row["row_revision"] for row in event["old_rows"]},
            new_row_revisions={row["row_id"]: row["row_revision"] for row in event["new_rows"]})
        groups.add((record["run_id"], record["comparison_id"]))
    for run_id, comparison_id in groups:
        _events(connection, run_id, comparison_id)
    return len(records)
