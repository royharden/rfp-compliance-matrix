"""Frozen machine inventory and separate durable human-to-pending correction."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from copy import deepcopy
import json
from typing import Any

from .store import ReviewError, canonical, decision_revision, digest, row_revision, safe_id, utc_now


REQUIRED = {"candidate_id", "expected_artifact_sha", "expected_assessment_generation", "expected_matrix_generation", "request_id", "reason"}
OPTIONAL = {"interpretation", "mandatory", "conditional", "administrative", "applicability"}


def promotion_events(connection, run_id: str) -> list[dict]:
    records = connection.execute("SELECT event FROM inventory_promotions WHERE run_id=? ORDER BY seq LIMIT 10001", (run_id,)).fetchall()
    if len(records) > 10000:
        raise ReviewError("promotion_history_limit", "Human promotion history exceeds the supported audit bound.")
    events = [json.loads(record[0]) for record in records]
    previous = None
    for event in events:
        if (event["run_id"] != run_id or event["previous_revision"] != previous
                or event["revision"] != digest("m3.inventory-promotion-event.v1", {k: v for k, v in event.items() if k != "revision"})):
            raise ReviewError("promotion_history_corrupt", "Promotion history is incomplete, reordered or altered.")
        previous = event["revision"]
    return events


def capture_inventory(connection, snapshot: dict) -> dict:
    """Read-only frozen audit plus separate lineage; never reclassifies source."""
    from rfp_matrix.evidence import inventory_audit
    events = promotion_events(connection, snapshot["run_id"])
    current_events = [event for event in events if event["artifact_sha"] == snapshot["artifact"]["artifact_sha"]]
    historical = [event for event in events if event["artifact_sha"] != snapshot["artifact"]["artifact_sha"]]
    report = snapshot.get("inventory_report")
    if not isinstance(report, dict):
        return {"schema_version": 1, "status": "unavailable", "code": "machine_inventory_report_missing", "audit": {},
                "promotions": current_events, "historical_promotions": historical,
                "semantic_recall": "not_assessed", "all_requirements_found": None}
    audit = inventory_audit(snapshot["artifact"], report)
    return {"schema_version": 1, "status": "available", "audit": audit, "promotions": current_events,
            "historical_promotions": historical, "semantic_recall": "not_assessed", "all_requirements_found": None}


def inventory_audit_state(store, run_id: str) -> dict:
    safe_id(run_id)
    with closing(store._connect()) as connection:
        connection.execute("BEGIN")
        snapshot = store._load(connection, run_id)
        snapshot["coverage"] = store._coverage(connection, snapshot)
        return {"run_id": run_id, "matrix_generation": snapshot["matrix_generation"],
                "assessment_generation": snapshot["assessment_generation"], "snapshot": snapshot,
                **capture_inventory(connection, snapshot)}


def promote_inventory_candidate(store, run_id: str, payload: Mapping[str, Any]) -> dict:
    safe_id(run_id)
    if not isinstance(payload, Mapping) or REQUIRED - set(payload) or set(payload) - REQUIRED - OPTIONAL:
        raise ReviewError("invalid_promotion", "Unknown/missing promotion fields; raw spans/actor/provider/approval/path are forbidden.")
    value = dict(payload)
    safe_id(value["candidate_id"])
    safe_id(value["request_id"])
    for name in ("expected_artifact_sha", "expected_assessment_generation", "expected_matrix_generation"):
        token = value[name]
        if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            raise ReviewError("invalid_promotion", "Expected source/assessment/matrix identities must be exact hashes.")
    if not isinstance(value["reason"], str) or not value["reason"].strip() or len(value["reason"]) > 8000:
        raise ReviewError("promotion_reason_required", "Promotion needs a bounded human rationale.")
    if "interpretation" in value and (not isinstance(value["interpretation"], str) or not value["interpretation"].strip() or len(value["interpretation"]) > 20000):
        raise ReviewError("invalid_promotion", "Interpretation must be bounded nonblank text.")
    for name in ("mandatory", "conditional", "administrative"):
        if name in value and value[name] is not None and type(value[name]) is not bool:
            raise ReviewError("invalid_promotion", "Independent flag overrides must be JSON booleans or null.")
    if "applicability" in value and (not isinstance(value["applicability"], str) or value["applicability"] not in {"applies", "unknown", "not_applies"}):
        raise ReviewError("invalid_promotion", "Unsupported applicability override.")
    payload_hash = digest("m3.inventory-promotion-request.v1", {"run_id": run_id, "actor": store.actor, "payload": value})
    with store.transaction(run_id) as connection:
        prior = connection.execute("SELECT payload_hash,event FROM inventory_promotions WHERE run_id=? AND request_id=?", (run_id, value["request_id"])).fetchone()
        if prior:
            if prior["payload_hash"] != payload_hash:
                raise ReviewError("idempotency_conflict", "Promotion request ID already binds different content.")
            event = json.loads(prior["event"])
            current = store._load(connection, run_id)
            return {"event": event, "replayed": True, "matrix_generation": current["matrix_generation"],
                    "historical_event": event["matrix_generation"] != current["matrix_generation"],
                    "row_id": event["row_id"], "promotion_confers_approval": False}
        for table in ("decisions", "revision_requests", "coverage_events", "correspondence_events", "export_intents"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE run_id=? AND request_id=?", (run_id, value["request_id"])).fetchone():
                raise ReviewError("idempotency_conflict", "Request ID belongs to another authoritative operation.")
        snapshot = store._load(connection, run_id)
        if (snapshot["matrix_generation"] != value["expected_matrix_generation"]
                or snapshot["assessment_generation"] != value["expected_assessment_generation"]
                or snapshot["artifact"]["artifact_sha"] != value["expected_artifact_sha"]):
            raise ReviewError("stale_promotion", "Refresh exact source/assessment/matrix versions before promotion.",
                              matrix_generation=snapshot["matrix_generation"], assessment_generation=snapshot["assessment_generation"],
                              artifact_sha=snapshot["artifact"]["artifact_sha"])
        from .currentness import require_execution_context
        require_execution_context(snapshot, snapshot["inventory_report"]["method"] if isinstance(snapshot.get("inventory_report"), dict) else "B0a")
        prior_atom = connection.execute("SELECT row_id FROM inventory_promotions WHERE run_id=? AND artifact_sha=? AND candidate_id=?",
            (run_id, value["expected_artifact_sha"], value["candidate_id"])).fetchone()
        if prior_atom:
            raise ReviewError("duplicate_inventory_promotion", "This registered atom already has a human promotion; revise its existing row instead.", row_id=prior_atom["row_id"])
        if len(snapshot["rows"]) >= 2000:
            raise ReviewError("row_limit", "Promotion would exceed the complete row inventory bound.")
        if not isinstance(snapshot.get("inventory_report"), dict):
            raise ReviewError("machine_inventory_report_missing", "Original machine audit is absent; promotion cannot invent it.")
        immutable_input = digest("m3.promotion-input.v1", snapshot)
        unchanged_rows = canonical(snapshot["rows"])
        current_policy_sha = digest("m3.current-application-policy.v1", snapshot["policy"])
        from rfp_matrix.evidence import inventory_audit, promote_inventory_candidate as prepare_pending
        from rfp_matrix.contracts import validate_record
        audit = inventory_audit(snapshot["artifact"], snapshot["inventory_report"])
        row = prepare_pending(snapshot["artifact"], snapshot["corpus"], snapshot["inventory_report"],
            candidate_id=value["candidate_id"], assessment_generation=snapshot["assessment_generation"],
            policy=snapshot["policy"], current_rows=snapshot["rows"], **{name: value[name] for name in OPTIONAL if name in value})
        if digest("m3.promotion-input.v1", snapshot) != immutable_input or not isinstance(row, dict):
            raise ReviewError("promotion_policy_mismatch", "Producer modified immutable input or returned an invalid pending row.")
        row["requirement"] = validate_record("requirement", row["requirement"])
        row["assessment"] = validate_record("assessment", row["assessment"])
        safe_id(row["row_id"])
        origin = row.get("human_origin", {})
        if (not isinstance(origin, Mapping) or origin.get("artifact_sha") != snapshot["artifact"]["artifact_sha"]
                or origin.get("corpus_generation") != snapshot["corpus"]["generation"]
                or origin.get("application_policy_sha") != current_policy_sha
                or origin.get("machine_report_sha") != audit["inventory_report_sha"]
                or origin.get("registry_sha") != audit["registry_sha"]
                or origin.get("candidate_id") != value["candidate_id"] or origin.get("blind_method_prediction") is not False
                or row.get("assessment_generation") != snapshot["assessment_generation"]
                or row.get("approval") != "pending" or row.get("decision_event_id") is not None
                or row.get("retired") is True
                or row["assessment"]["complete"] is not False or row["assessment"]["status"] != "needs_review"
                or row["assessment"]["requirement_id"] != row["requirement"]["requirement_id"]
                or row["assessment"]["requirement_revision"] != row["requirement"]["revision"]
                or row["assessment"]["corpus_generation"] != snapshot["corpus"]["generation"]
                or row["assessment"]["support"] or row["assessment"]["counterevidence"]
                or row["assessment"]["model"] is not None or row["assessment"]["usage"] is not None
                or row["assessment"]["validation"]["state"] != "pending"
                or row.get("validation") != row["assessment"]["validation"]):
            raise ReviewError("promotion_policy_mismatch", "Producer did not return an exact-source, current-policy, unassessed pending human atom.")
        registered = next((entry for entry in audit["entries"] if entry["candidate_id"] == value["candidate_id"]), None)
        if (not registered or not registered["promotable"] or row["requirement"]["anchors"] != [registered["anchor"]]
                or row["requirement"]["literal_clause"] != registered["literal_clause"]
                or row["requirement"]["requirement_id"] != row["row_id"]):
            raise ReviewError("promotion_policy_mismatch", "Pending row does not bind the eligible registered literal atom.")
        for existing in snapshot["rows"]:
            previous_origin = existing.get("human_origin") if isinstance(existing.get("human_origin"), Mapping) else {}
            if (existing["row_id"] == row["row_id"] or registered["anchor"] in existing["requirement"]["anchors"]
                    or (previous_origin.get("candidate_id") == value["candidate_id"]
                        and previous_origin.get("artifact_sha") == value["expected_artifact_sha"])):
                raise ReviewError("duplicate_inventory_promotion", "An active or retired row already uses this source atom.")
        events = promotion_events(connection, run_id)
        if len(events) >= 10000:
            raise ReviewError("promotion_history_limit", "Promotion history reached its supported bound.")
        event_id = "I-" + digest("m3.inventory-promotion-id.v1", [store.store_id, run_id, value["request_id"], payload_hash])[:32]
        created_at = utc_now()
        row = deepcopy(row)
        row["human_origin"].update(event_id=event_id, actor=store.actor, reason=value["reason"], created_at=created_at)
        row["row_revision"] = row_revision(row)
        row["decision_revision"] = decision_revision(row)
        store.validate_row(snapshot, row)
        previous_generation = snapshot["matrix_generation"]
        previous_history = connection.execute("SELECT seq FROM history WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        if not previous_history:
            raise ReviewError("domain_history_inconsistent", "Promotion requires its original immutable matrix history.")
        snapshot["rows"].append(row)
        # Keep the global assessment/policy and every existing row unchanged.
        # The matrix alone advances because its unresolved inventory grew.
        store._save(connection, snapshot, "inventory_promotion")
        if canonical(snapshot["rows"][:-1]) != unchanged_rows:
            raise ReviewError("promotion_policy_mismatch", "Promotion changed an unrelated row/version/approval; refusing commit.")
        history = connection.execute("SELECT seq FROM history WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        inserted = connection.execute("INSERT INTO inventory_promotions(event_id,run_id,request_id,payload_hash,payload,candidate_id,artifact_sha,row_id,history_seq,event) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, run_id, value["request_id"], payload_hash, canonical(value), value["candidate_id"], value["expected_artifact_sha"], row["row_id"], history["seq"], "{}"))
        event = {"schema_version": 1, "event_id": event_id, "sequence": inserted.lastrowid, "run_id": run_id,
            "request_id": value["request_id"], "payload_hash": payload_hash, "candidate_id": value["candidate_id"], "row_id": row["row_id"],
            "artifact_sha": value["expected_artifact_sha"], "assessment_generation": snapshot["assessment_generation"],
            "corpus_generation": snapshot["corpus"]["generation"], "previous_matrix_generation": previous_generation,
            "matrix_generation": snapshot["matrix_generation"], "row_revision": row["row_revision"], "decision_revision": row["decision_revision"],
            "actor": store.actor, "reason": value["reason"], "created_at": created_at, "history_sequence": history["seq"],
            "previous_history_sequence": previous_history["seq"],
            "machine_inventory_report_sha": audit["inventory_report_sha"], "audit_sha": audit["audit_sha"], "registry_sha": audit["registry_sha"],
            "human_origin": row["human_origin"], "previous_revision": events[-1]["revision"] if events else None,
            "blind_method_prediction": False, "resulting_approval": "pending", "promotion_confers_approval": False}
        event["revision"] = digest("m3.inventory-promotion-event.v1", event)
        connection.execute("UPDATE inventory_promotions SET event=? WHERE seq=?", (canonical(event), inserted.lastrowid))
        return {"event": event, "replayed": False, "run_id": run_id, "matrix_generation": snapshot["matrix_generation"],
                "assessment_generation": snapshot["assessment_generation"], "row_id": row["row_id"], "row": row,
                "promotion_confers_approval": False, "resulting_approval": "pending"}


def validate_promotion_history(connection, histories: dict[int, dict]) -> int:
    """Backup/diagnostic lineage check; no classifier/provider or approval call."""
    from rfp_matrix.source import resolve_anchor
    records = connection.execute("SELECT * FROM inventory_promotions ORDER BY seq LIMIT 10001").fetchall()
    if len(records) > 10000:
        raise ReviewError("promotion_history_limit", "Maintenance promotion history bound exceeded.")
    runs = set()
    for record in records:
        event, payload = json.loads(record["event"]), json.loads(record["payload"])
        after, before = histories.get(record["history_seq"]), histories.get(event["previous_history_sequence"])
        if (not after or not before or after["run_id"] != record["run_id"] or before["run_id"] != record["run_id"]
                or event["run_id"] != record["run_id"] or event["event_id"] != record["event_id"]
                or event["sequence"] != record["seq"] or event["history_sequence"] != record["history_seq"]
                or event["request_id"] != record["request_id"] or payload["request_id"] != record["request_id"]
                or event["candidate_id"] != record["candidate_id"] or payload["candidate_id"] != record["candidate_id"]
                or event["artifact_sha"] != record["artifact_sha"] or event["row_id"] != record["row_id"]
                or before["matrix_generation"] != event["previous_matrix_generation"]
                or payload["expected_matrix_generation"] != before["matrix_generation"]
                or after["matrix_generation"] != event["matrix_generation"]
                or after["assessment_generation"] != before["assessment_generation"]
                or payload["expected_assessment_generation"] != before["assessment_generation"]
                or payload["expected_artifact_sha"] != before["artifact"]["artifact_sha"]
                or before["artifact"] != after["artifact"] or before["corpus"] != after["corpus"]
                or before["policy"] != after["policy"] or before.get("inventory_report") != after.get("inventory_report")
                or event["resulting_approval"] != "pending" or event["promotion_confers_approval"] is not False
                or event["blind_method_prediction"] is not False):
            raise ReviewError("promotion_history_corrupt", "Promotion is not bound to an unchanged owned source/corpus/policy/machine report.")
        expected_hash = digest("m3.inventory-promotion-request.v1", {"run_id": record["run_id"], "actor": event["actor"], "payload": payload})
        if expected_hash != record["payload_hash"] or expected_hash != event["payload_hash"] or event["reason"] != payload["reason"]:
            raise ReviewError("promotion_history_corrupt", "Promotion request payload/actor digest mismatch.")
        matches = [row for row in after["rows"] if row["row_id"] == record["row_id"]]
        unchanged = [row for row in after["rows"] if row["row_id"] != record["row_id"]]
        if len(matches) != 1 or unchanged != before["rows"]:
            raise ReviewError("promotion_history_corrupt", "Promotion changed unrelated rows or created a duplicate atom.")
        row = matches[0]
        for anchor in row["requirement"]["anchors"]:
            resolve_anchor(before["artifact"], anchor)
        origin = row["human_origin"]
        # Historical integrity uses the original event/frame hashes. Running a
        # NEW classifier/registry/producer over old data is not compatibility
        # proof and can falsely make a sound backup look corrupt after upgrade.
        if (row["approval"] != "pending" or row.get("decision_event_id") is not None
                or row["assessment"]["status"] != "needs_review" or row["assessment"]["support"]
                or row["assessment"]["counterevidence"] or row["assessment"]["complete"] is not False
                or origin["event_id"] != event["event_id"] or origin["actor"] != event["actor"]
                or origin["reason"] != event["reason"] or origin["created_at"] != event["created_at"]
                or origin["candidate_id"] != payload["candidate_id"] or origin["artifact_sha"] != event["artifact_sha"]
                or origin["machine_report_sha"] != digest("m3.machine-inventory-report.v1", before["inventory_report"])
                or event["assessment_generation"] != after["assessment_generation"]
                or event["corpus_generation"] != after["corpus"]["generation"]
                or event["row_revision"] != row["row_revision"]
                or event["decision_revision"] != matches[0]["decision_revision"]
                or event["human_origin"] != matches[0]["human_origin"]
                or event["machine_inventory_report_sha"] != origin["machine_report_sha"]
                or event["registry_sha"] != origin["registry_sha"]):
            raise ReviewError("promotion_history_corrupt", "Stored human pending row differs from its immutable promotion lineage.")
        runs.add(record["run_id"])
    for run_id in runs:
        promotion_events(connection, run_id)
    return len(records)
