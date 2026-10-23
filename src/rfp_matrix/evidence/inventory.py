"""Derived segment attribution and pending human correction; no classifier dispatch."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from rfp_matrix.contracts import M3Error, canonical_json, digest, validate_record
from rfp_matrix.source import structural_segments, validate_artifact
from .engine import _method, _verify_corpus, registered_source_candidates


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise M3Error("invalid_inventory_identity", f"{field} must be an exact SHA-256 identity")
    return value


def inventory_audit(artifact: Mapping[str, Any], inventory_report: Mapping[str, Any]) -> dict[str, Any]:
    """Account for structural candidates, not recall of all semantic obligations.

    Missing dispositions stay visible. Duplicate/conflicting dispositions or
    pointers outside the frozen registry fail closed, never silently deduplicate.
    The original machine report is never modified by this derived view.
    """
    source = validate_artifact(artifact)
    report = dict(inventory_report)
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1 or report.get("artifact_sha") != source["artifact_sha"]:
        raise M3Error("stale_inventory_report", "Inventory report does not belong to this immutable extraction")
    _method(report.get("method"))
    _hash(report.get("policy_hash"), "inventory policy")
    serialized = canonical_json(report)
    if len(serialized.encode("utf-8")) > 16 * 1024 * 1024:
        raise M3Error("inventory_audit_limit", "Machine inventory report exceeds bounded audit input")
    registry = registered_source_candidates(source)
    if "registry_sha" in report and report["registry_sha"] != registry["registry_sha"]:
        raise M3Error("stale_inventory_registry", "Frozen inventory registry policy or source coordinates changed")
    candidates = registry["candidates"]
    by_anchor = {canonical_json(item["anchor"]): item for item in candidates}
    disposition: dict[str, tuple[str, dict[str, Any]]] = {}
    judgments: dict[str, dict[str, Any]] = {}

    def registered(pointer: Mapping[str, Any]) -> dict[str, Any]:
        anchor = validate_record("anchor", pointer)
        candidate = by_anchor.get(canonical_json(anchor))
        if candidate is None:
            raise M3Error("stale_inventory_pointer", "Machine disposition is not an exact registered candidate span")
        return candidate

    for field, kind in (("requirements", "retained"), ("rejected_candidates", "rejected")):
        values = report.get(field)
        if not isinstance(values, list) or len(values) > 2000:
            raise M3Error("invalid_inventory_report", "Machine report needs bounded retained and rejected arrays")
        for raw in values:
            if not isinstance(raw, Mapping):
                raise M3Error("invalid_inventory_report", "Disposition must be an original registered mapping")
            value = validate_record("requirement", raw) if kind == "retained" else dict(raw)
            if kind == "retained":
                if value["policy_hash"] != report["policy_hash"] or value["method"] != report["method"] or len(value["anchors"]) != 1:
                    raise M3Error("stale_inventory_policy", "Machine requirement policy/method/atomic source differs from its report")
                candidate = registered(value["anchors"][0])
                if candidate["candidate_id"] != value["requirement_id"] or candidate["literal_clause"] != value["literal_clause"]:
                    raise M3Error("stale_inventory_pointer", "Machine requirement identity/literal differs from registered candidate")
            else:
                candidate = registered(value.get("anchor", {}))
                if value.get("method") != report["method"]:
                    raise M3Error("stale_inventory_policy", "Rejected candidate method differs from its immutable report")
                if not isinstance(value.get("reason"), str) or not value["reason"].strip():
                    raise M3Error("invalid_inventory_report", "Excluded context needs an explicit recorded reason")
                uncertainty = value.get("uncertainty", [])
                if not isinstance(uncertainty, list) or len(uncertainty) > 100 or any(not isinstance(item, str) or len(item) > 4000 for item in uncertainty):
                    raise M3Error("invalid_inventory_report", "Excluded context uncertainty must be bounded strings")
            identity = candidate["candidate_id"]
            if identity in disposition:
                raise M3Error("inventory_disposition_conflict", "A candidate has duplicate or conflicting machine dispositions")
            disposition[identity] = (kind, value)
    raw_judgments = report.get("judgments")
    if not isinstance(raw_judgments, list) or len(raw_judgments) > 2000:
        raise M3Error("invalid_inventory_report", "Machine judgment audit must be bounded")
    for raw in raw_judgments:
        if not isinstance(raw, Mapping):
            raise M3Error("invalid_inventory_report", "Judgment audit must preserve its registered anchor")
        identity = registered(raw.get("anchor", {}))["candidate_id"]
        if identity in judgments:
            raise M3Error("inventory_disposition_conflict", "A candidate has duplicate typed outcomes")
        judgments[identity] = dict(raw)
    entries = []
    for candidate in candidates:
        identity = candidate["candidate_id"]
        record = disposition.get(identity)
        judgment = judgments.get(identity, {})
        outcome = judgment.get("outcome") if isinstance(judgment.get("outcome"), Mapping) else {}
        consumer_status = judgment.get("consumer_status")
        provider_missing = report["method"] != "B0a" and consumer_status not in {"ok", "labels"}
        uncertainty = list(candidate["uncertainty"])
        if record is not None:
            uncertainty.extend(record[1].get("uncertainty", []))
        if provider_missing:
            uncertainty.append("inventory_provider_unavailable_or_unattributed")
        if consumer_status == "labels":
            uncertainty.append("uncalibrated_generated_classification")
        kind = record[0] if record is not None else "unattributed"
        if record is None:
            reason = "registered_candidate_missing_machine_disposition"
        elif uncertainty:
            reason = "retained_requires_review" if kind == "retained" else "context_exclusion_requires_review"
            kind = "uncertain"
        else:
            reason = "machine_retained_source_bound_requirement" if kind == "retained" else record[1]["reason"]
        retained = record is not None and record[0] == "retained"
        entries.append({"candidate_id": identity, "disposition": kind, "reason": reason,
            "machine_disposition": record[0] if record else None,
            "machine_reason": record[1].get("reason") if record else None,
            "anchor": candidate["anchor"], "literal_clause": candidate["literal_clause"],
            "mandatory": candidate["mandatory"], "conditional": candidate["conditional"], "administrative": candidate["administrative"],
            "requirement_ids": [record[1]["requirement_id"]] if retained else [],
            "uncertainty": sorted(set(uncertainty)), "promotable": not retained,
            "provider_status": {"response_status": outcome.get("status"), "consumer_status": consumer_status,
                "origin": outcome.get("origin"), "model": outcome.get("model"), "reason": outcome.get("reason"),
                "unavailable": provider_missing}, "segment_ids": []})
    structure = structural_segments(source, max_segments=20000)
    entry_by_id = {item["candidate_id"]: item for item in entries}
    intervals: dict[int, list[dict[str, Any]]] = {}
    for item in entries:
        intervals.setdefault(item["anchor"]["page_index"], []).append(item)
    segments = []
    for segment in structure["segments"]:
        anchor = segment["anchor"]
        overlapping = [item for item in intervals.get(anchor["page_index"], [])
                       if item["anchor"]["start"] < anchor["end"] and anchor["start"] < item["anchor"]["end"]]
        containing = [item for item in overlapping
                      if item["anchor"]["start"] <= anchor["start"] < anchor["end"] <= item["anchor"]["end"]]
        item = containing[0] if len(containing) == 1 else None
        for linked in overlapping:
            entry_by_id[linked["candidate_id"]]["segment_ids"].append(segment["segment_id"])
        segments.append({"segment_id": segment["segment_id"], "kind": segment["kind"], "anchor": anchor,
            "candidate_ids": [linked["candidate_id"] for linked in overlapping],
            "disposition": item["disposition"] if item else ("uncertain" if overlapping else "unattributed"),
            "reason": item["reason"] if item else ("structural_span_spans_atomic_candidates" if overlapping else "structural_span_not_attributed")})
    gaps = []
    for cell in structure["table_cells"]:
        gaps.append({"gap_id": digest("m3.inventory-structural-gap.v1", {"artifact_sha": source["artifact_sha"], **cell}),
            "page_index": cell["page_index"], "table_index": cell["table_index"], "row_index": cell["row_index"],
            "column_index": cell["column_index"], "literal_excerpt": cell["literal"][:1000],
            "literal_truncated": len(cell["literal"]) > 1000, "anchor": None,
            "disposition": "unattributed", "reason": "table_cell_original_alignment_unavailable", "promotable": False})
    counts = {kind: sum(item["disposition"] == kind for item in entries) for kind in ("retained", "rejected", "uncertain", "unattributed")}
    counts.update(candidates=len(entries), structural_segments=len(segments), structural_gaps=len(gaps),
                  unattributed_segments=sum(item["disposition"] == "unattributed" for item in segments),
                  provider_unavailable=sum(item["provider_status"]["unavailable"] for item in entries),
                  promotable=sum(item["promotable"] for item in entries))
    content = {"schema_version": 1, "policy": "m3-derived-inventory-disposition-v1", "artifact_sha": source["artifact_sha"],
        "registry_sha": registry["registry_sha"], "inventory_report_sha": digest("m3.machine-inventory-report.v1", report),
        "inventory_policy_hash": report["policy_hash"], "method": report["method"],
        "parser": source["parser"], "parser_version": source["parser_version"], "normalization_policy": source["normalization_policy"],
        "entries": entries, "segments": segments, "structural_gaps": gaps,
        "page_coverage": [{key: page[key] for key in ("page_index", "text_sha", "coverage", "issues")} for page in source["pages"]],
        "artifact_issues": list(source["issues"]), "counts": counts, "coverage_complete": source["complete"],
        "machine_report_complete": report.get("complete") is True,
        "attribution_complete": not counts["unattributed"] and not counts["unattributed_segments"] and not gaps,
        "semantic_recall": "not_assessed", "all_requirements_found": None, "truncated": False,
        "limits": {"candidates": 2000, "structural_items": 20000, "report_bytes": 16 * 1024 * 1024}}
    return {**content, "audit_sha": digest("m3.inventory-audit.v1", content)}


def promote_inventory_candidate(artifact: Mapping[str, Any], corpus: Mapping[str, Any], machine_report: Mapping[str, Any], *,
        candidate_id: str, assessment_generation: str, policy: Mapping[str, Any], current_rows: Sequence[Mapping[str, Any]],
        interpretation: str | None = None, mandatory: bool | None = None, conditional: bool | None = None,
        administrative: bool | None = None, applicability: str | None = None) -> dict[str, Any]:
    """Prepare one source-bound PENDING atom; application owns actor/CAS/event.

    Current rows are a preflight only: D6 repeats duplicate/limit checks in its
    commit transaction. No provider, retrieved support or positive answer exists.
    """
    source, vendor = validate_artifact(artifact), _verify_corpus(corpus)
    _hash(assessment_generation, "current assessment generation")
    if not isinstance(policy, Mapping) or len(canonical_json(dict(policy)).encode("utf-8")) > 2 * 1024 * 1024:
        raise M3Error("invalid_promotion_policy", "Promotion requires bounded application-owned current policy")
    if not isinstance(current_rows, (list, tuple)) or len(current_rows) >= 2000:
        raise M3Error("requirement_limit", "Promotion requires the bounded current row inventory")
    audit = inventory_audit(source, machine_report)
    entry = next((item for item in audit["entries"] if item["candidate_id"] == candidate_id), None)
    if entry is None:
        raise M3Error("unknown_inventory_candidate", "Only a registered source candidate may be promoted")
    if not entry["promotable"]:
        raise M3Error("duplicate_inventory_promotion", "This machine candidate already has a retained requirement")
    anchor_identity = canonical_json(entry["anchor"])
    for row in current_rows:
        if not isinstance(row, Mapping):
            raise M3Error("invalid_promotion_inventory", "Current rows must be application-owned mappings")
        origin = row.get("human_origin") if isinstance(row.get("human_origin"), Mapping) else {}
        requirement = row.get("requirement") if isinstance(row.get("requirement"), Mapping) else {}
        if ((origin.get("candidate_id") == candidate_id and origin.get("artifact_sha") == source["artifact_sha"])
                or any(canonical_json(pointer) == anchor_identity for pointer in requirement.get("anchors", []))):
            raise M3Error("duplicate_inventory_promotion", "An existing or retired atom already uses this exact source candidate")
    if interpretation is not None and (not isinstance(interpretation, str) or not interpretation.strip() or len(interpretation) > 200000):
        raise M3Error("invalid_promotion_interpretation", "Human interpretation must be nonblank bounded text")
    overrides = {"mandatory": mandatory, "conditional": conditional, "administrative": administrative}
    if any(value is not None and type(value) is not bool for value in overrides.values()):
        raise M3Error("invalid_promotion_flags", "Independent human flags require explicit booleans")
    flags = {key: entry[key] if value is None else value for key, value in overrides.items()}
    effective_applicability = applicability if applicability is not None else ("unknown" if flags["conditional"] else "applies")
    if not isinstance(effective_applicability, str) or effective_applicability not in {"applies", "unknown", "not_applies"}:
        raise M3Error("invalid_promotion_applicability", "Unsupported applicability override")
    lineage = {"kind": "human_inventory_promotion", "candidate_id": candidate_id, "artifact_sha": source["artifact_sha"],
        "corpus_generation": vendor["generation"], "registry_sha": audit["registry_sha"],
        "machine_report_sha": audit["inventory_report_sha"], "machine_disposition": entry["machine_disposition"],
        "audit_disposition": entry["disposition"],
        "application_policy_sha": digest("m3.current-application-policy.v1", dict(policy)),
        "blind_method_prediction": False, "created_without_assessment": True, "actor_event_owned_by_application": True}
    content = {"schema_version": 1, "requirement_id": "H-" + digest("m3.human-promoted-atom.v1", lineage)[:24],
        "parent_id": None, "child_ids": [], "anchors": [entry["anchor"]], "literal_clause": entry["literal_clause"],
        "interpretation": interpretation.strip() if interpretation is not None else entry["literal_clause"],
        **flags, "applicability": effective_applicability, "condition": entry["literal_clause"] if flags["conditional"] else None,
        "uncertainty": list(entry["uncertainty"]),
        "policy_hash": digest("m3.human-inventory-promotion-policy.v1", lineage), "method": audit["method"]}
    content["revision"] = digest("m3.requirement-revision.v1", content)
    requirement = validate_record("requirement", content)
    validation = {"schema_version": 1, "state": "pending", "method": "human_inventory_promotion",
                  "provenance": "human-correction-not-blind-prediction", "reason": "Separate source/evidence assessment and row review required"}
    assessment = validate_record("assessment", {"schema_version": 1, "requirement_id": requirement["requirement_id"],
        "requirement_revision": requirement["revision"], "corpus_generation": vendor["generation"], "status": "needs_review",
        "support": [], "counterevidence": [], "candidates": [], "complete": False, "method": audit["method"],
        "reason": "Human-promoted source atom has not received an evidence assessment", "origin": "unavailable", "model": None,
        "policy_hash": digest("m3.pending-promotion-assessment.v1", lineage),
        "judgments": [{"kind": "human_inventory_promotion", "candidate_id": candidate_id, "assessment_executed": False}],
        "usage": None, "validation": validation})
    row = {"schema_version": 1, "row_id": requirement["requirement_id"], "requirement": requirement, "assessment": assessment,
        "answer_text": "Pending human-added requirement: evidence assessment and explicit review are required. Fictional evidence only.",
        "validation": validation, "assessment_generation": assessment_generation, "unvalidated_note": "", "human_origin": lineage}
    row["row_revision"] = digest("m3.row-revision.v1", row)
    row.update(approval="pending", decision_event_id=None)
    return row
