"""Pure closed template mappings. No HTML, authority, files, models or I/O.

All text must be autoescaped by templates. Route/session code supplies CSRF and
fresh request IDs and rechecks every domain precondition at mutation time.
"""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = 1
_APPROVALS = {"pending", "approved_positive", "approved_gap", "rejected", "stale", "deferred", "not_applies"}
_STATUSES = {"supported", "partial", "cannot_answer_absent", "cannot_answer_conflicting", "needs_review"}


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any, limit: int = 2000) -> list[Any]:
    return list(value[:limit]) if isinstance(value, (list, tuple)) else []


def _text(value: Any, limit: int = 200000) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _strings(value: Any, limit: int = 100) -> list[str]:
    return [_text(item, 4000) for item in _items(value, limit) if isinstance(item, str)]


def _page(value: Any) -> int | None:
    return value + 1 if type(value) is int and 0 <= value < 100 else None


def _pointer(value: Any) -> dict[str, Any]:
    pointer = _map(value)
    return {"source_id": _text(pointer.get("source_id"), 160),
            "source_sha": _text(pointer.get("source_sha"), 64),
            "artifact_sha": _text(pointer.get("artifact_sha"), 64),
            "record_id": _text(pointer.get("record_id"), 160),
            "corpus_generation": _text(pointer.get("corpus_generation"), 64),
            "sentence_id": _text(pointer.get("sentence_id"), 300),
            "page_number": _page(pointer.get("page_index")),
            "section_id": _text(pointer.get("section_id"), 160),
            "start": pointer.get("start") if type(pointer.get("start")) is int else None,
            "end": pointer.get("end") if type(pointer.get("end")) is int else None,
            "quote": _text(pointer.get("quote")), "excerpt_sha": _text(pointer.get("excerpt_sha"), 64),
            "broad_fallback": pointer.get("broad_fallback") is True,
            "literal_verified": False}  # Presentation alone never resolves/authenticates a pointer.


def coverage_view(summary: Any) -> dict[str, Any]:
    envelope = _map(summary)
    effective = _map(envelope.get("effective", envelope))
    available = (isinstance(effective.get("complete"), bool) and bool(effective.get("artifact_sha"))
                 and isinstance(effective.get("pages"), list) and 1 <= len(effective["pages"]) <= 100)
    pages = []
    for value in _items(effective.get("pages"), 100):
        page = _map(value)
        pages.append({"page_number": _page(page.get("page_index")),
            "page_index": page.get("page_index") if type(page.get("page_index")) is int else None,
            "page_sha": _text(page.get("page_sha"), 64), "coverage": _text(page.get("original_coverage"), 30),
            "resolved": page.get("resolved") is True, "disposition": _text(page.get("disposition"), 80),
            "issues": _strings(page.get("issues")), "reason": _text(page.get("reason"), 4000),
            "actor": _text(page.get("actor"), 160), "decision_revision": _text(page.get("decision_revision"), 64),
            "expected_coverage_revision": page.get("decision_revision") if isinstance(page.get("decision_revision"), str) else None})
    pages.sort(key=lambda value: value["page_number"] if value["page_number"] is not None else 101)
    return {"schema_version": 1, "kind": "coverage", "available": available,
        "artifact_sha": _text(effective.get("artifact_sha"), 64), "policy": _text(effective.get("policy"), 160),
        "complete": (available and effective.get("complete") is True and all(page["resolved"] for page in pages)
                     and all(page["page_number"] is not None for page in pages)
                     and len({page["page_number"] for page in pages}) == len(pages)
                     and effective.get("unresolved_pages") == []),
        "summary_revision": _text(effective.get("summary_revision"), 64),
        "pages": pages, "unresolved_pages": [page for page in (_page(item) for item in _items(effective.get("unresolved_pages"), 100)) if page is not None],
        "artifact_issues": _strings(effective.get("artifact_issues"), 1000),
        "audit_revisions": _strings(effective.get("audit_revisions"), 10000),
        "warnings": [] if available else ["Coverage inspection state is unavailable; extraction completeness is unverified."]}


def _snippet(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    pointer = _pointer(value)
    return {key: pointer[key] for key in ("artifact_sha", "source_sha", "page_number", "start", "end", "quote")} | {
        "full_span_end": value.get("full_span_end") if type(value.get("full_span_end")) is int else None,
        "quote_truncated": value.get("quote_truncated") is True}


def changes_view(changes: Any) -> dict[str, Any]:
    value = _map(changes)
    source, corpus = _map(value.get("source")), _map(value.get("corpus"))
    entries = []
    for raw in _items(value.get("entries"), 100):
        item = _map(raw)
        entries.append({"kind": _text(item.get("kind"), 80), "page_number": _page(item.get("page_index")),
            "section_id": _text(item.get("section_id"), 160), "record_id": _text(item.get("record_id"), 160),
            "old_sha": _text(item.get("old_sha"), 64), "new_sha": _text(item.get("new_sha"), 64),
            "old": _snippet(item.get("old")), "new": _snippet(item.get("new")),
            "old_quote": _text(item.get("old_quote"), 2000), "new_quote": _text(item.get("new_quote"), 2000),
            "comparison": _text(item.get("comparison"), 100)})
    policies = []
    for name in ("parser", "parser_version", "policy_hash", "normalization_policy"):
        delta = _map(_map(source.get("parser_policy_changes")).get(name))
        if delta:
            policies.append({"name": name, "old": _text(delta.get("old"), 160), "new": _text(delta.get("new"), 160)})
    record_ids = _map(corpus.get("record_ids"))
    counts = {name: number for name in ("pages_added", "pages_removed", "pages_changed", "sections_added",
        "sections_removed", "sections_changed", "records_added", "records_removed", "records_changed")
        if type(number := _map(value.get("counts")).get(name)) is int and number >= 0}
    return {"schema_version": 1, "kind": "changes", "available": bool(value.get("context_sha")),
        "context_sha": _text(value.get("context_sha"), 64), "entries": entries, "counts": counts,
        "all_dependent_approvals_stale": value.get("all_dependent_approvals_stale") is True,
        "ancestry": _text(source.get("ancestry"), 160) or "unknown",
        "semantic_equivalence": "not_assessed", "parser_policy_changes": policies,
        "old_artifact_sha": _text(source.get("old_artifact_sha"), 64), "new_artifact_sha": _text(source.get("new_artifact_sha"), 64),
        "old_corpus_generation": _text(corpus.get("old_generation"), 64), "new_corpus_generation": _text(corpus.get("new_generation"), 64),
        "record_ids": {name: _strings(record_ids.get(name), 200) for name in ("added", "removed", "changed")},
        "truncated": value.get("truncated") is True or len(_items(value.get("entries"), 2001)) > 100,
        "display_omitted_entries": max(0, len(_items(value.get("entries"), 2001)) - len(entries)),
        "omitted_entries": value.get("omitted_entries") if type(value.get("omitted_entries")) is int else 0,
        "warnings": ["Change excerpts do not establish semantic equivalence or preserve approvals after a corpus change."]}


def _context_view(context: Any) -> dict[str, Any]:
    value = _map(context)
    raw = value.get("anchors", value.get("source", []))
    anchors = []
    for item in _items(raw, 100):
        entry = _map(item)
        anchors.append({**_pointer(entry), "context": _text(entry.get("context")),
            "previous_page_tail": _text(entry.get("previous_page_tail"), 2000),
            "next_page_head": _text(entry.get("next_page_head"), 2000),
            "context_is_evidence": False, "issues": _strings(entry.get("issues"))})
    return {"available": bool(anchors), "anchors": anchors,
            "cross_page": value.get("cross_page") is True,
            "warnings": [] if anchors else ["Resolved source context is unavailable; inspect source before deciding."]}


def _policy_view(snapshot: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    # Only application-computed currentness, never the captured provenance label,
    # may enable current claims. Templates do not collect disk hashes or SDK data.
    supplied = _map(row.get("policy_status", snapshot.get("policy_status")))
    status = _text(supplied.get("status"), 40)
    if status not in {"current", "missing", "legacy", "mismatch", "restart_required"}:
        status = "legacy" if not _map(_map(row.get("assessment")).get("policy_provenance")) else "missing"
    allowed = status == "current" and supplied.get("allows_current_claim") is True
    reasons = _strings(supplied.get("reasons"))
    if not allowed and not reasons:
        reasons = ["Application-verified current semantic policy is unavailable; inspect history or explicitly reimport/reassess."]
    if status == "missing" and _map(row.get("assessment")).get("method") in {"B0b", "F1", "F2"}:
        reasons.append("Supply trusted --review-policy-config FILE for this run; this read-only policy never enables inference or repairs old evidence.")
    return {"status": status, "reasons": reasons, "allows_current_claim": allowed,
            "recorded_fingerprint": _text(supplied.get("recorded_fingerprint"), 64),
            "current_fingerprint": _text(supplied.get("current_fingerprint"), 64),
            "action": _text(supplied.get("action"), 80),
            "policy_input": "review-policy-identity-only-v1" if supplied.get("policy_input") == "review-policy-identity-only-v1" else None,
            "execution_verified": False}


def row_view(snapshot: Any, row: Any, *, context: Any = None, coverage: Any = None, changes: Any = None) -> dict[str, Any]:
    snapshot, row = _map(snapshot), _map(row)
    req, assessment = _map(row.get("requirement")), _map(row.get("assessment"))
    validation = _map(row.get("validation"))
    raw_status, raw_approval = _text(assessment.get("status"), 80), _text(row.get("approval"), 80)
    status = raw_status if raw_status in _STATUSES else "needs_review"
    approval = raw_approval if raw_approval in _APPROVALS else "pending"
    retired = row.get("retired") is True
    expected_generation = _text(snapshot.get("assessment_generation"), 64)
    expected_row_revision = _text(row.get("row_revision"), 64)
    # Application-owned opaque decision CAS is distinct from content identity.
    # Only legacy rows lacking the field use the content digest as their token.
    supplied_decision_revision = row.get("decision_revision") if "decision_revision" in row else row.get("row_revision")
    decision_revision = supplied_decision_revision if isinstance(supplied_decision_revision, str) and 0 < len(supplied_decision_revision) <= 256 else ""
    semantic_status = _policy_view(snapshot, row)
    current = (snapshot.get("historical") is not True and snapshot.get("is_current") is not False
        and bool(expected_generation) and bool(expected_row_revision) and bool(decision_revision) and bool(snapshot.get("matrix_generation"))
        and bool(snapshot.get("run_id")) and bool(row.get("row_id"))
        and row.get("assessment_generation") == expected_generation)
    current = current and semantic_status["allows_current_claim"]
    coverage_input = coverage if coverage is not None else snapshot.get("coverage")
    coverage_envelope = _map(coverage_input)
    if ("matrix_generation" in coverage_envelope
            and coverage_envelope["matrix_generation"] != snapshot.get("matrix_generation")):
        current = False
    context_metadata = _map(context)
    context_mismatch = (("matrix_generation" in context_metadata and context_metadata["matrix_generation"] != snapshot.get("matrix_generation"))
                        or ("row_revision" in context_metadata and context_metadata["row_revision"] != row.get("row_revision")))
    if context_mismatch:
        current = False
    label = "retired" if retired else ("stale" if not current or approval == "stale" else approval)
    coverage_data = coverage_view(coverage_input)
    expected_artifact = _map(snapshot.get("artifact")).get("artifact_sha")
    if expected_artifact and coverage_data["artifact_sha"] != expected_artifact:
        current = False
        label = "retired" if retired else "stale"
    warnings = []
    warnings.extend(semantic_status["reasons"] if not semantic_status["allows_current_claim"] else [])
    if not current:
        warnings.append("This row is historical, stale or missing exact version identity; mutation controls are unavailable.")
    if req.get("uncertainty"):
        warnings.append("Requirement interpretation or source structure remains uncertain.")
    if assessment.get("complete") is not True:
        warnings.append("Evidence assessment is incomplete; this row cannot become a positive commitment.")
    if not coverage_data["complete"] or coverage_data["artifact_issues"]:
        warnings.append("Source coverage is unresolved or has provenance warnings.")
    actions = ["edit", "reject", "defer", "not_applies"] if current and not retired else []
    complete = (assessment.get("complete") is True and coverage_data["complete"]
                and not coverage_data["artifact_issues"] and _map(snapshot.get("corpus")).get("complete") is True)
    if actions and complete:
        if (status == "supported" and assessment.get("support") and not assessment.get("counterevidence")
                and req.get("applicability") == "applies" and not row.get("unvalidated_note")
                and not req.get("uncertainty") and assessment.get("method") != "F2"
                and validation.get("method") != "reviewer_edit"):
            actions.append("approve_positive")
        if status in {"cannot_answer_absent", "cannot_answer_conflicting"}:
            actions.append("accept_gap")
    event = _map(row.get("decision_event", row.get("decision")))
    if not event and row.get("decision_event_id"):
        event = next((_map(item) for item in _items(snapshot.get("decision_events"), 10000)
                      if _map(item).get("event_id") == row["decision_event_id"]), {})
    positive = (current and not retired and approval == "approved_positive" and bool(row.get("decision_event_id"))
                and complete and status == "supported" and bool(assessment.get("support"))
                and not assessment.get("counterevidence") and req.get("applicability") == "applies"
                and not req.get("uncertainty") and assessment.get("method") != "F2"
                and _text(validation.get("state"), 80) in {"validated", "valid"} and not row.get("unvalidated_note"))
    gap = (current and not retired and approval == "approved_gap" and bool(row.get("decision_event_id"))
           and complete and status in {"cannot_answer_absent", "cannot_answer_conflicting"})
    if approval == "approved_positive" and not positive:
        warnings.append("Recorded positive decision cannot be presented as a current validated answer from the available data.")
    return {"schema_version": 1, "kind": "row", "run_id": _text(snapshot.get("run_id"), 128),
        "matrix_generation": _text(snapshot.get("matrix_generation"), 64), "fictional": True,
        "policy_status": semantic_status,
        "row": {"row_id": _text(row.get("row_id"), 128), "row_revision": expected_row_revision,
            "decision_revision": decision_revision,
            "assessment_generation": _text(row.get("assessment_generation"), 64), "label": label,
            "approval": approval, "current": current, "retired": retired, "status": status,
            "answer_text": _text(row.get("answer_text")), "approved_answer": _text(row.get("answer_text")) if positive else None,
            "gap_acknowledged": gap, "decision_event_id": _text(row.get("decision_event_id"), 160),
            "decision_actor": _text(event.get("actor"), 160) or "unavailable",
            "decision_time": _text(event.get("created_at"), 100) or "unavailable",
            "invalidation_reason": _text(row.get("invalidation_reason"), 1000)},
        "requirement": {"requirement_id": _text(req.get("requirement_id"), 160),
            "revision": _text(req.get("revision"), 64), "literal_clause": _text(req.get("literal_clause")),
            "interpretation": _text(req.get("interpretation")), "mandatory": req.get("mandatory") is True,
            "conditional": req.get("conditional") is True, "administrative": req.get("administrative") is True,
            "applicability": _text(req.get("applicability"), 80) or "unknown", "condition": _text(req.get("condition")),
            "uncertainty": _strings(req.get("uncertainty")), "anchors": [_pointer(item) for item in _items(req.get("anchors"), 100)]},
        "evidence": {"status": status, "complete": assessment.get("complete") is True,
            "reason": _text(assessment.get("reason"), 4000), "method": _text(assessment.get("method"), 80),
            "origin": _text(assessment.get("origin"), 80), "model": _text(assessment.get("model"), 160),
            "policy_hash": _text(assessment.get("policy_hash"), 64),
            "probability_semantics": "generated_labels_no_probabilities" if assessment.get("method") == "F2" else "inspect_raw_judgment_receipts",
            "support": [_pointer(item) for item in _items(assessment.get("support"), 100)],
            "counterevidence": [_pointer(item) for item in _items(assessment.get("counterevidence"), 100)],
            "validation_state": _text(validation.get("state"), 80) or "pending",
            "validation_method": _text(validation.get("method"), 160)},
        "controls": {"expected_generation": expected_generation, "expected_assessment_generation": expected_generation,
            "expected_row_revision": decision_revision, "expected_matrix_generation": _text(snapshot.get("matrix_generation"), 64),
            "actions": actions, "requires_request_id": True, "requires_csrf": True,
            "requires_explicit_human_validation": "approve_positive" in actions, "domain_rechecks_required": True},
        "lineage": {"parent_id": _text(req.get("parent_id"), 160), "child_ids": _strings(req.get("child_ids"), 2000),
            "revision_ancestry": _strings(row.get("revision_ancestry"), 2000),
            "dependency_revision": _text(row.get("dependency_revision"), 64)},
        "notes": {"text": _text(row.get("unvalidated_note")), "unvalidated": True, "excluded_from_approved_answer": True,
                  "human_created": bool(_map(row.get("human_origin"))),
                  "blind_method_prediction": False if _map(row.get("human_origin")) else None,
                  "human_origin": {name: _text(_map(row.get("human_origin")).get(name), 160) for name in
                                   ("kind", "candidate_id", "artifact_sha", "machine_report_sha", "application_policy_sha")}},
        "context": _context_view(None if context_mismatch else context), "coverage": coverage_data,
        "changes": changes_view(changes if changes is not None else snapshot.get("change_report")), "warnings": warnings}


def matrix_view(snapshot: Any, *, queue: str = "all") -> dict[str, Any]:
    snapshot = _map(snapshot)
    accepted_queues = {"all", "pending", "approved_positive", "approved_gap", "rejected", "stale", "retired", "deferred", "not_applies", "unresolved"}
    selected_queue = queue if isinstance(queue, str) and queue in accepted_queues else "all"
    coverage_envelope = _map(snapshot.get("coverage"))
    coverage_effective = _map(coverage_envelope.get("effective", coverage_envelope))
    compact_coverage = {key: item for key, item in coverage_effective.items() if key != "audit_revisions"}
    if "matrix_generation" in coverage_envelope:
        compact_coverage["matrix_generation"] = coverage_envelope["matrix_generation"]
    rows = []
    for item in _items(snapshot.get("rows")):
        full = row_view(snapshot, item, coverage=compact_coverage, changes={})
        compact = {key: full[key] for key in ("row", "requirement", "evidence", "controls", "lineage", "notes", "warnings", "policy_status")}
        compact["text_truncated"] = False
        for owner, fields in ((compact["row"], ("answer_text", "approved_answer")),
                              (compact["requirement"], ("literal_clause", "interpretation", "condition")),
                              (compact["notes"], ("text",))):
            for field in fields:
                text = owner[field]
                if isinstance(text, str) and len(text) > 600:
                    owner[field] = text[:600]
                    compact["text_truncated"] = True
        for owner, field in ((compact["requirement"], "anchors"), (compact["evidence"], "support"), (compact["evidence"], "counterevidence")):
            owner[field + "_count"] = len(owner[field])
            compact["text_truncated"] |= len(owner[field]) > 4
            owner[field] = owner[field][:4]
            for pointer in owner[field]:
                compact["text_truncated"] |= len(pointer["quote"]) > 600
                pointer["quote"] = pointer["quote"][:600]
        rows.append(compact)
    rows.sort(key=lambda item: (item["row"]["row_id"], item["row"]["row_revision"]))
    counts = {label: sum(item["row"]["label"] == label for item in rows) for label in sorted(accepted_queues - {"all"})}
    counts["unresolved"] = sum(item["row"]["label"] not in {"approved_positive", "approved_gap"} for item in rows)
    visible = [item for item in rows if selected_queue == "all" or item["row"]["label"] == selected_queue
               or (selected_queue == "unresolved" and item["row"]["label"] not in {"approved_positive", "approved_gap"})]
    total_input = len(snapshot.get("rows", [])) if isinstance(snapshot.get("rows"), (list, tuple)) else 0
    warnings = []
    if not snapshot.get("run_id") or not snapshot.get("matrix_generation"):
        warnings.append("Run identity/version is unavailable.")
    if total_input > 2000:
        warnings.append("Matrix display truncated at 2,000 rows; remaining inventory is unavailable here.")
    if queue != selected_queue:
        warnings.append("Unknown queue; showing all available rows.")
    return {"schema_version": 1, "kind": "matrix", "run_id": _text(snapshot.get("run_id"), 128),
        "matrix_generation": _text(snapshot.get("matrix_generation"), 64),
        "assessment_generation": _text(snapshot.get("assessment_generation"), 64), "fictional": True,
        "queue": selected_queue, "rows": visible, "counts": {**counts, "total": total_input, "visible": len(visible),
                                                               "displayed": len(rows), "label_counts_complete": total_input <= 2000},
        "coverage": coverage_view(snapshot.get("coverage")), "changes": changes_view(snapshot.get("change_report")),
        "historical": snapshot.get("historical") is True or snapshot.get("is_current") is False,
        "warnings": warnings, "notice": "Fictional evidence. Human decisions do not establish legal compliance or vendor commitments."}


def amendments_view(comparison: Any) -> dict[str, Any]:
    """Source-linked proposals and separately supplied human lineage records.

    Recording correspondence never populates approved-answer fields, changes a
    row's review state or confirms any other proposed candidate.
    """
    envelope = _map(comparison)
    report = _map(envelope.get("comparison", envelope))
    old_identity = {name: _text(_map(report.get("old_identity")).get(name), 64) for name in
                    ("artifact_sha", "raw_sha", "assessment_generation", "matrix_generation", "corpus_generation", "snapshot_pin")}
    new_identity = {name: _text(_map(report.get("new_identity")).get(name), 64) for name in old_identity}

    def record(value: Any) -> dict[str, Any]:
        row = _map(value)
        anchors = []
        for raw in _items(row.get("anchors"), 4):
            anchor = _map(raw)
            anchors.append({**_pointer(anchor), "quote_excerpt": _text(anchor.get("quote_excerpt"), 400),
                "quote_excerpt_end": anchor.get("quote_excerpt_end") if type(anchor.get("quote_excerpt_end")) is int else None,
                "quote_truncated": anchor.get("quote_truncated") is True, "context": _text(anchor.get("context"), 640),
                "context_is_evidence": False})
        return {"row_id": _text(row.get("row_id"), 160), "row_revision": _text(row.get("row_revision"), 64),
            "requirement_id": _text(row.get("requirement_id"), 160), "requirement_revision": _text(row.get("requirement_revision"), 64),
            "literal_excerpt": _text(row.get("literal_excerpt"), 1000), "literal_sha": _text(row.get("literal_sha"), 64),
            "literal_truncated": row.get("literal_truncated") is True,
            "interpretation_excerpt": _text(row.get("interpretation_excerpt"), 1000),
            "mandatory": row.get("mandatory") is True, "conditional": row.get("conditional") is True,
            "administrative": row.get("administrative") is True, "applicability": _text(row.get("applicability"), 80),
            "condition": _text(row.get("condition"), 1000), "condition_sha": _text(row.get("condition_sha"), 64),
            "condition_truncated": row.get("condition_truncated") is True,
            "uncertainty": _strings(row.get("uncertainty")), "parent_id": _text(row.get("parent_id"), 160),
            "child_ids": _strings(row.get("child_ids"), 2000), "retired": row.get("retired") is True,
            "anchors": anchors, "anchors_truncated": row.get("anchors_truncated") is True,
            "unresolved_historical_anchors": row.get("unresolved_historical_anchors") is True,
            "row_pin": _text(row.get("row_pin"), 64)}

    old_rows = [record(item) for item in _items(report.get("old_rows"))]
    new_rows = [record(item) for item in _items(report.get("new_rows"))]
    old_rows.sort(key=lambda row: row["row_id"])
    new_rows.sort(key=lambda row: row["row_id"])
    old_by_id, new_by_id = ({row["row_id"]: row for row in rows} for rows in (old_rows, new_rows))
    candidates = []
    missing = 0
    for raw in _items(report.get("candidates"), 1000):
        proposal = _map(raw)
        old, new = old_by_id.get(_text(proposal.get("old_row_id"), 160)), new_by_id.get(_text(proposal.get("new_row_id"), 160))
        if old is None or new is None or old["row_revision"] != proposal.get("old_row_revision") or new["row_revision"] != proposal.get("new_row_revision"):
            missing += 1
            continue
        score = proposal.get("lexical_score")
        score = score if type(score) in (int, float) and 0 <= score <= 1 else None
        ambiguity = _map(proposal.get("ambiguity"))
        field_changes = []
        for field in ("mandatory", "conditional", "administrative", "applicability", "condition"):
            delta = _map(_map(proposal.get("field_changes")).get(field))
            if delta:
                field_changes.append({"field": field,
                    "old": delta.get("old") if type(delta.get("old")) is bool else _text(delta.get("old"), 1000),
                    "new": delta.get("new") if type(delta.get("new")) is bool else _text(delta.get("new"), 1000)})
        candidates.append({"candidate_id": _text(proposal.get("candidate_id"), 64), "old": old, "new": new,
            "lexical_score": score, "score_semantics": "token_overlap_not_equivalence", "confirmed": False,
            "label": "unconfirmed_lexical_proposal", "field_changes": field_changes,
            "literal_changed": proposal.get("literal_changed") is True,
            "split_possible": ambiguity.get("split_possible") is True,
            "merge_possible": ambiguity.get("merge_possible") is True,
            "degrees_are_lower_bounds": ambiguity.get("degrees_are_lower_bounds") is True})
    candidates.sort(key=lambda item: (-(item["lexical_score"] or 0), item["old"]["row_id"], item["new"]["row_id"]))
    raw_events = envelope.get("human_events", envelope.get("events", report.get("human_events", [])))
    events = []
    for raw in _items(raw_events, 500):
        event = _map(raw)
        relation = _text(event.get("relation"), 40)
        events.append({"event_id": _text(event.get("event_id"), 160),
            "relation": relation if relation in {"one_to_one", "split", "merge", "added", "removed"} else "unknown",
            "actor": _text(event.get("actor"), 160) or "unavailable", "reason": _text(event.get("reason"), 4000),
            "created_at": _text(event.get("created_at"), 100), "comparison_id": _text(event.get("comparison_id"), 64),
            "old_row_ids": _strings(event.get("old_row_ids"), 100), "new_row_ids": _strings(event.get("new_row_ids"), 100),
            "old_identity": {name: _text(_map(event.get("old_identity")).get(name), 64) for name in old_identity},
            "new_identity": {name: _text(_map(event.get("new_identity")).get(name), 64) for name in new_identity},
            "revision": _text(event.get("revision"), 64),
            "previous_revision": event.get("previous_revision") if isinstance(event.get("previous_revision"), str) else None,
            "current_comparison": bool(report.get("comparison_id")) and event.get("comparison_id") == report.get("comparison_id"),
            "label": "application_supplied_human_correspondence_record", "approval_carry_allowed": False})
    available = bool(report.get("comparison_id")) and all(old_identity.values()) and all(new_identity.values())
    correspondence_revision = envelope.get("correspondence_revision")
    valid_correspondence_revision = correspondence_revision is None or (
        isinstance(correspondence_revision, str) and len(correspondence_revision) == 64
        and all(character in "0123456789abcdef" for character in correspondence_revision))
    warnings = ["Lexical correspondence is a proposal, not semantic equivalence. Every new generation requires its own review."]
    if report.get("retrieval_incomplete") or report.get("truncated"):
        warnings.append("Candidate retrieval/display is incomplete; inspect the unmapped mandatory inventory.")
    if missing:
        warnings.append("Some candidate references do not match registered row versions and were withheld.")
    if not valid_correspondence_revision:
        warnings.append("The application correspondence concurrency token is invalid; refresh this comparison.")
    return {"schema_version": 1, "kind": "amendments", "run_id": _text(report.get("run_id"), 128),
        "available": bool(available), "comparison_id": _text(report.get("comparison_id"), 64),
        "report_sha": _text(report.get("report_sha"), 64), "old_identity": old_identity, "new_identity": new_identity,
        "old_rows": old_rows, "new_rows": new_rows, "candidates": candidates,
        "unmapped_old_ids": _strings(report.get("unmapped_old_ids"), 2000),
        "unmapped_new_ids": _strings(report.get("unmapped_new_ids"), 2000),
        "unmapped_mandatory_old": [old_by_id[row_id] for row_id in _strings(report.get("unmapped_mandatory_old_ids"), 2000) if row_id in old_by_id],
        "unmapped_mandatory_new": [new_by_id[row_id] for row_id in _strings(report.get("unmapped_mandatory_new_ids"), 2000) if row_id in new_by_id],
        "counts": {name: number for name in ("old_active", "new_active", "candidate_pairs", "displayed_candidates", "unmapped_old", "unmapped_new", "added", "removed")
                   if type(number := _map(report.get("counts")).get(name)) is int and number >= 0},
        "truncated": report.get("truncated") is True, "retrieval_incomplete": report.get("retrieval_incomplete") is True,
        "human_events": events, "human_events_truncated": isinstance(raw_events, (list, tuple)) and len(raw_events) > 500,
        "ancestry": "unknown_until_human_correspondence", "approval_carry_allowed": False,
        "controls": {"enabled": bool(available) and valid_correspondence_revision, "comparison_id": _text(report.get("comparison_id"), 64),
            "expected_correspondence_revision": correspondence_revision if valid_correspondence_revision else None,
            "expected_matrix_generation": new_identity["matrix_generation"],
            "expected_old_matrix_generation": old_identity["matrix_generation"],
            "expected_new_matrix_generation": new_identity["matrix_generation"],
            "expected_old_artifact_sha": old_identity["artifact_sha"], "expected_new_artifact_sha": new_identity["artifact_sha"],
            "expected_old_assessment_generation": old_identity["assessment_generation"],
            "expected_new_assessment_generation": new_identity["assessment_generation"],
            "relations": ["one_to_one", "split", "merge", "added", "removed"] if available else [],
            "requires_request_id": True, "requires_csrf": True, "requires_reason": True,
            "confers_approval": False, "domain_rechecks_required": True},
        "fictional": True, "warnings": warnings}


def inventory_audit_view(payload: Any) -> dict[str, Any]:
    """Display immutable machine attribution and separate human pending atoms."""
    envelope = _map(payload)
    audit = _map(envelope.get("audit", envelope))
    snapshot = _map(envelope.get("snapshot"))
    artifact_sha = _text(audit.get("artifact_sha"), 64)
    current = (bool(artifact_sha) and _map(snapshot.get("artifact")).get("artifact_sha") == artifact_sha
        and bool(snapshot.get("assessment_generation")) and bool(snapshot.get("matrix_generation"))
        and snapshot.get("historical") is not True and snapshot.get("is_current") is not False)
    promoted = set()
    for raw in _items(snapshot.get("rows")):
        origin = _map(_map(raw).get("human_origin"))
        if origin.get("artifact_sha") == artifact_sha:
            candidate_id = _text(origin.get("candidate_id"), 160)
            if candidate_id:
                promoted.add(candidate_id)
    entries = []
    for raw in _items(audit.get("entries")):
        entry = _map(raw)
        disposition = _text(entry.get("disposition"), 40)
        disposition = disposition if disposition in {"retained", "rejected", "uncertain", "unattributed"} else "unattributed"
        candidate_id = _text(entry.get("candidate_id"), 160)
        provider = _map(entry.get("provider_status"))
        entries.append({"candidate_id": candidate_id, "disposition": disposition,
            "reason": _text(entry.get("reason"), 4000), "machine_disposition": _text(entry.get("machine_disposition"), 40),
            "machine_reason": _text(entry.get("machine_reason"), 4000), "anchor": _pointer(entry.get("anchor")),
            "literal_excerpt": _text(entry.get("literal_clause"), 1000),
            "literal_truncated": len(_text(entry.get("literal_clause"))) > 1000,
            "mandatory": entry.get("mandatory") is True, "conditional": entry.get("conditional") is True,
            "administrative": entry.get("administrative") is True, "uncertainty": _strings(entry.get("uncertainty")),
            "requirement_ids": _strings(entry.get("requirement_ids"), 2000),
            "segment_ids": _strings(entry.get("segment_ids"), 20000),
            "provider_status": {"response_status": _text(provider.get("response_status"), 80),
                "consumer_status": _text(provider.get("consumer_status"), 80), "origin": _text(provider.get("origin"), 80),
                "model": _text(provider.get("model"), 160), "reason": _text(provider.get("reason"), 4000),
                "unavailable": provider.get("unavailable") is True},
            "already_promoted": candidate_id in promoted,
            "promotion_state": "human_atom_exists_separate_from_machine_audit" if candidate_id in promoted else "not_recorded",
            "eligible": current and bool(candidate_id) and entry.get("promotable") is True and candidate_id not in promoted})
    entries.sort(key=lambda item: (item["anchor"]["page_number"] or 101,
                                  item["anchor"]["start"] if item["anchor"]["start"] is not None else -1, item["candidate_id"]))
    segments = []
    for raw in _items(audit.get("segments"), 20000):
        item = _map(raw)
        segments.append({"segment_id": _text(item.get("segment_id"), 64), "kind": _text(item.get("kind"), 80),
            "anchor": _pointer(item.get("anchor")), "candidate_ids": _strings(item.get("candidate_ids"), 2000),
            "disposition": _text(item.get("disposition"), 40), "reason": _text(item.get("reason"), 4000)})
    gaps = []
    for raw in _items(audit.get("structural_gaps"), 20000):
        item = _map(raw)
        gaps.append({"gap_id": _text(item.get("gap_id"), 64), "page_number": _page(item.get("page_index")),
            "literal_excerpt": _text(item.get("literal_excerpt"), 1000), "literal_truncated": item.get("literal_truncated") is True,
            "disposition": "unattributed", "reason": _text(item.get("reason"), 4000), "anchor": None, "eligible": False})
    pages = []
    for raw in _items(audit.get("page_coverage"), 100):
        page = _map(raw)
        pages.append({"page_number": _page(page.get("page_index")), "text_sha": _text(page.get("text_sha"), 64),
            "coverage": _text(page.get("coverage"), 40), "issues": _strings(page.get("issues"))})
    promotions = []
    for raw in _items(envelope.get("promotions"), 2000):
        event = _map(raw)
        promotions.append({"event_id": _text(event.get("event_id"), 160), "candidate_id": _text(event.get("candidate_id"), 160),
            "row_id": _text(event.get("row_id", event.get("new_row_id")), 160), "actor": _text(event.get("actor"), 160),
            "reason": _text(event.get("reason"), 4000), "created_at": _text(event.get("created_at"), 100),
            "label": "human_correction_to_pending_not_blind_prediction", "confers_approval": False})
    warnings = ["Segment attribution does not measure semantic recall or establish that all requirements were found.",
                "Human promotions create pending review data only and cannot improve blind-method evaluation predictions."]
    if not current:
        warnings.append("Current application source/assessment/matrix versions are missing or differ from this audit; promotion is unavailable.")
    if gaps or audit.get("coverage_complete") is not True:
        warnings.append("Parser, source structure or coverage gaps remain unresolved.")
    return {"schema_version": 1, "kind": "inventory", "run_id": _text(snapshot.get("run_id", envelope.get("run_id")), 128),
        "artifact_sha": artifact_sha, "audit_sha": _text(audit.get("audit_sha"), 64),
        "registry_sha": _text(audit.get("registry_sha"), 64), "inventory_report_sha": _text(audit.get("inventory_report_sha"), 64),
        "inventory_policy_hash": _text(audit.get("inventory_policy_hash"), 64),
        "parser": _text(audit.get("parser"), 160), "parser_version": _text(audit.get("parser_version"), 80),
        "normalization_policy": _text(audit.get("normalization_policy"), 160), "current": current,
        "entries": entries, "segments": segments, "structural_gaps": gaps, "page_coverage": pages,
        "tabs": {name: [entry for entry in entries if entry["disposition"] == name]
                 for name in ("retained", "rejected", "uncertain", "unattributed")},
        "counts": {name: number for name in ("retained", "rejected", "uncertain", "unattributed", "candidates",
            "structural_segments", "structural_gaps", "unattributed_segments", "provider_unavailable", "promotable")
            if type(number := _map(audit.get("counts")).get(name)) is int and number >= 0},
        "coverage_complete": audit.get("coverage_complete") is True, "attribution_complete": audit.get("attribution_complete") is True,
        "artifact_issues": _strings(audit.get("artifact_issues"), 1000),
        "machine_report_complete": audit.get("machine_report_complete") is True,
        "semantic_recall": "not_assessed", "all_requirements_found": None,
        "truncated": audit.get("truncated") is True, "promotions": promotions,
        "controls": {"enabled": current, "expected_artifact_sha": artifact_sha,
            "expected_assessment_generation": _text(snapshot.get("assessment_generation"), 64),
            "expected_matrix_generation": _text(snapshot.get("matrix_generation"), 64),
            "candidate_ids": [entry["candidate_id"] for entry in entries if entry["eligible"]],
            "requires_csrf": True, "requires_request_id": True, "requires_reason": True,
            "resulting_approval": "pending", "domain_rechecks_required": True},
        "fictional": True, "warnings": warnings}


def build_view(kind: str, payload: Any) -> dict[str, Any]:
    """Closed D4 route dispatcher; generations only come from a full snapshot.

    Accepts D6 get_row's nested {snapshot,row} as well as the flattened seam.
    Route CSRF/request IDs and actor/paths are intentionally not copied through.
    """
    value = _map(payload)
    if kind == "amendments":
        return amendments_view(value)
    if kind == "inventory":
        return inventory_audit_view(value)
    if kind == "matrix":
        original = _map(value.get("snapshot", value.get("run")))
        snapshot = dict(original)
        selected_rows = _map(value.get("rows"))
        incoming_coverage = _map(value.get("coverage"))
        mismatch = any(field in selected_rows and selected_rows[field] != original.get(field)
                       for field in ("matrix_generation", "assessment_generation"))
        mismatch |= ("matrix_generation" in incoming_coverage
                     and incoming_coverage["matrix_generation"] != original.get("matrix_generation"))
        if "rows" not in snapshot:
            selected = value.get("rows")
            snapshot["rows"] = _items(_map(selected).get("rows", selected))
        if "coverage" in value:
            snapshot["coverage"] = value["coverage"]
        if "changes" in value:
            snapshot["change_report"] = value["changes"]
        if mismatch:
            snapshot["is_current"] = False
        result = matrix_view(snapshot, queue="all" if mismatch else value.get("queue", "all"))
        if mismatch:
            result["warnings"].append("Snapshot, row-list or coverage versions disagree; refresh the full run. All available rows are shown read-only.")
        return result
    if kind == "row":
        selected = _map(value.get("row"))
        snapshot = _map(value.get("snapshot", selected.get("snapshot")))
        row = _map(selected.get("row", selected))
        context = value.get("context")
        context_mapping = _map(context)
        stale_context = (("matrix_generation" in context_mapping and context_mapping["matrix_generation"] != snapshot.get("matrix_generation"))
            or ("row_revision" in context_mapping and context_mapping["row_revision"] != row.get("row_revision")))
        result = row_view(snapshot, row, context=None if stale_context else context,
            coverage=value.get("coverage", snapshot.get("coverage")), changes=value.get("changes", snapshot.get("change_report")))
        if stale_context:
            result["warnings"].append("Source context came from a different generation; refresh before making a decision.")
            result["controls"]["actions"] = []
            result["row"].update(current=False, label="retired" if result["row"]["retired"] else "stale",
                                 approved_answer=None, gap_acknowledged=False)
        return result
    if kind == "coverage":
        summary = _map(value.get("coverage"))
        return {**coverage_view(summary), "run_id": _text(value.get("run_id", summary.get("run_id")), 128),
                "matrix_generation": _text(summary.get("matrix_generation", value.get("matrix_generation")), 64), "fictional": True}
    if kind == "runs":
        envelope = _map(value.get("runs"))
        raw = envelope.get("runs", value.get("runs"))
        runs = []
        for item in _items(raw, 200):
            entry = _map(item)
            runs.append({"run_id": _text(entry.get("run_id"), 128), "source_id": _text(entry.get("source_id"), 160),
                "matrix_generation": _text(entry.get("matrix_generation"), 64),
                "assessment_generation": _text(entry.get("assessment_generation"), 64),
                "row_count": entry.get("row_count") if type(entry.get("row_count")) is int else None,
                "fictional_vendor": True})
        runs.sort(key=lambda item: item["run_id"])
        total = envelope.get("total_runs", value.get("total_runs"))
        total = total if type(total) is int and total >= 0 else len(raw) if isinstance(raw, list) else 0
        return {"schema_version": 1, "kind": "runs", "runs": runs, "total_runs": total,
            "truncated": envelope.get("truncated") is True or total > len(runs), "fictional": True,
            "warnings": [] if runs else ["No local runs are available in this session."]}
    if kind == "changes":
        return changes_view(value.get("changes", value))
    return {"schema_version": 1, "kind": "error", "code": "unknown_presentation",
            "warnings": ["The requested presentation is unavailable."], "fictional": True}
