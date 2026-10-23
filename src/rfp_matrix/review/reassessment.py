"""Explicit evidence recomputation; no policy-only relabelling of old evidence."""
from contextlib import closing
from copy import deepcopy
import json

from .store import ReviewError, canonical, digest, row_revision, safe_id, utc_now
from .currentness import require_execution_context


def reassess(store, run_id: str, *, expected_matrix_generation: str, request_id: str, reason: str,
             method: str = "B0a", provider=None, provider_identity: str | None = None) -> dict:
    safe_id(run_id)
    safe_id(request_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 8000:
        raise ReviewError("reason_required", "Explicit evidence reassessment needs a bounded rationale.")
    if method not in {"B0a", "B0b", "F1", "F2"}:
        raise ReviewError("invalid_method", "Unknown reassessment method.")
    from rfp_matrix.evidence.consumer import provider_identity as describe_provider, judgment_port
    actual_provider = describe_provider(judgment_port(provider, method), method)
    intent = {"operation": "evidence_reassessment", "run_id": run_id, "expected_matrix_generation": expected_matrix_generation,
              "request_id": request_id, "reason": reason, "method": method, "provider_identity": provider_identity,
              "actual_provider": actual_provider, "actor": store.actor}
    payload_hash = digest("m3.evidence-reassessment-request.v1", intent)

    def retry(connection):
        prior = connection.execute("SELECT payload_hash,snapshot FROM revision_requests WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise ReviewError("idempotency_conflict", "Reassessment request key already has a different intent.")
            return json.loads(prior["snapshot"])
        for table in ("decisions", "coverage_events", "correspondence_events", "inventory_promotions", "export_intents"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone():
                raise ReviewError("idempotency_conflict", "Reassessment key belongs to another operation.")
        return None

    with closing(store._connect()) as connection:
        prior = retry(connection)
    if prior is not None:
        return prior
    before = store.load_run(run_id)
    if before["matrix_generation"] != expected_matrix_generation:
        raise ReviewError("stale_generation", "Refresh the matrix before recomputing evidence.", matrix_generation=before["matrix_generation"])
    require_execution_context(before, method)
    from rfp_matrix.evidence import reassess_rows
    # This is the real producer, not a hash refresh. Disabled typed methods
    # return their actual unavailable/needs-review assessments; no live fallback.
    rows = reassess_rows(before["artifact"], before["corpus"], before["rows"], method=method, provider=provider)
    require_execution_context(before, method)
    policy = deepcopy(before["policy"])
    policy["provider_config_sha256"] = provider_identity
    result = store._prepare(run_id, before["artifact"], before["corpus"], rows, policy, before.get("inventory_report"))
    result["created_at"] = before["created_at"]
    result["requirement_revision_events"] = deepcopy(before.get("requirement_revision_events", []))
    if "coverage_revision" in before:
        result["coverage"], result["coverage_revision"] = deepcopy(before["coverage"]), before["coverage_revision"]
    prior_rows = {row["row_id"]: row for row in before["rows"]}
    for row in result["rows"]:
        previous = prior_rows[row["row_id"]]
        row.update(approval="stale", invalidation_reason="explicit_evidence_reassessment",
                   previous_decision_event_id=previous.get("decision_event_id"), previous_row_revision=previous["row_revision"])
    for row in before["rows"]:
        if row.get("retired"):
            historical = deepcopy(row)
            historical.update(approval="stale", decision_event_id=None, assessment_generation=result["assessment_generation"])
            historical["row_revision"] = row_revision(historical)
            result["rows"].append(historical)
    with store.transaction(run_id) as connection:
        prior = retry(connection)
        if prior is not None:
            return prior
        current = store._load(connection, run_id)
        if current["matrix_generation"] != expected_matrix_generation:
            raise ReviewError("stale_generation", "Matrix changed during recomputation; results were not committed.", matrix_generation=current["matrix_generation"])
        require_execution_context(current, method)
        event = {**intent, "schema_version": 1, "created_at": utc_now(), "event_id": "Q-" + payload_hash[:32],
                 "payload_hash": payload_hash, "assessment_generation": result["assessment_generation"], "approvals_carried": False}
        result["requirement_revision_events"].append(event)
        store._save(connection, result, "explicit_evidence_reassessment")
        connection.execute("INSERT INTO revision_requests VALUES(?,?,?,?,?,?)", (run_id, request_id, payload_hash, canonical(event), canonical(result), canonical(intent)))
        return result
