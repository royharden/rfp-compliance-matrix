"""Plain terminal review over the same durable services as the CLI."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any


def _text(value: Any) -> str:
    """Render document text inertly, including terminal escape/control bytes."""
    text = str(value)
    return "".join(c if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127 and not 128 <= ord(c) <= 159) else f"\\u{ord(c):04x}" for c in text)


def _anchor(anchor: Mapping[str, Any]) -> str:
    page = anchor.get("page_index")
    page_label = str(page + 1) if isinstance(page, int) else "?"
    location = f"page {page_label}" if page is not None else f"sentence {anchor.get('sentence_id', '?')}"
    return f"{anchor.get('source_id', anchor.get('record_id', '?'))} {location} [{anchor.get('start', '?')}:{anchor.get('end', '?')}] {_text(anchor.get('quote', ''))}"


def format_inventory(snapshot: Mapping[str, Any], queue: str = "all") -> str:
    artifact = snapshot.get("artifact", {})
    source = artifact.get("source", {})
    lines = [f"Run: {snapshot['run_id']} | fictional vendor evidence", f"Generation: {snapshot['matrix_generation']}", f"Source: {_text(source.get('source_id', '?'))}; SHA256 {source.get('raw_sha', '?')}", f"Parser: {_text(artifact.get('parser', '?'))} {_text(artifact.get('parser_version', '?'))}; pages {len(artifact.get('pages', []))}", f"Terms: {_text(source.get('terms', 'unknown'))}; rights status: {_text(source.get('rights_status', 'pending'))}", "Coverage issues: " + _text(artifact.get("issues", [])), "ROW | MANDATORY | CONDITIONAL | ADMIN | EVIDENCE | APPROVAL | REQUIREMENT"]
    rows = snapshot.get("rows", [])
    lines.append("Policy currentness: " + _text(snapshot.get("policy_status", {}).get("status", "unverified")) + "; stored approvals are historical unless current semantics are established.")
    from rfp_matrix.review.currentness import in_queue
    selected = [r for r in rows if in_queue(r, queue)]
    for row in selected:
        req = row["requirement"]
        lines.append(" | ".join(str(v) for v in (row["row_id"], req.get("mandatory", False), req.get("conditional", False), req.get("administrative", False), row["assessment"].get("status", "needs_review"), row.get("current_approval", "historical_" + row.get("approval", "pending")), _text(req.get("literal_clause", req.get("text", ""))).replace("\n", " "))))
    lines.append(f"Visible {len(selected)} / total {len(rows)} rows; unresolved rows remain part of every export.")
    retired = [row["row_id"] for row in selected if row.get("retired", False)]
    if retired:
        lines.append("Historical retired rows (cannot be reapproved): " + ", ".join(retired))
    if not artifact.get("complete", False):
        lines.append("COVERAGE NEEDS REVIEW: extraction is not a complete-coverage guarantee.")
    if snapshot.get("coverage"):
        effective = snapshot["coverage"]["effective"]
        lines.append("Effective human-inspection coverage: " + _text(effective["complete"]) +
                     "; unresolved pages (one-based): " + _text([p + 1 for p in effective["unresolved_pages"]]) +
                     "; revision " + effective["summary_revision"])
    if queue == "stale" and snapshot.get("change_report"):
        change = snapshot["change_report"]
        lines.append("Last source/corpus update (other invalidation reasons remain per row): " + change["context_sha"])
        lines.append("Whole-corpus changes invalidate all dependent approvals, even when their cited record is unchanged.")
        lines.append("Change counts: " + _text(json.dumps(change["counts"], sort_keys=True)))
        lines.append("Parser/policy changes: " + _text(json.dumps(change["source"]["parser_policy_changes"], sort_keys=True)))
        lines.append("Bounded change excerpts (first 12; use changes --run for the recorded report): " +
                     _text(json.dumps(change["entries"][:12], ensure_ascii=False, sort_keys=True)))
        lines.append("Report omitted entries: " + str(change["omitted_entries"]) + "; display is not a semantic-equivalence judgment.")
    return "\n".join(lines)


def format_row(snapshot: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    req = row["requirement"]
    assessment = row["assessment"]
    lines = [f"Row {row['row_id']} — {_text(row.get('current_approval', 'historical_' + row.get('approval', 'pending')))}", f"Requirement: {_text(req.get('literal_clause', req.get('text', '')))}", f"Interpretation: {_text(req.get('interpretation', req.get('normalized_interpretation', '')))}", f"Mandatory={req.get('mandatory', False)} conditional={req.get('conditional', False)} administrative={req.get('administrative', False)}", f"Applicability: {_text(req.get('applicability', 'unknown'))}; uncertainty: {_text(req.get('uncertainty', req.get('uncertain_split', '')))}", f"Parents: {_text(req.get('parent_ids', req.get('parent_id', [])))}; children: {_text(req.get('child_ids', []))}", f"Evidence: {_text(assessment.get('status', 'needs_review'))}; method: {_text(assessment.get('method', assessment.get('provenance', {})))}", f"Answer template: {_text(row.get('answer_text', ''))}", f"Unvalidated note (excluded from commitments): {_text(row.get('unvalidated_note', ''))}", f"Validation: {_text(json.dumps(row.get('validation', {}), ensure_ascii=False, sort_keys=True))}", f"Assessment generation: {snapshot['assessment_generation']}", f"Row revision: {row['row_revision']}", f"Decision event: {row.get('decision_event_id') or 'none'}", f"Invalidation reason: {_text(row.get('invalidation_reason', 'none'))}", "Source anchors:"]
    lines.insert(7, "Evidence reason: " + _text(assessment.get("reason", "unknown")))
    lines.insert(8, f"Assessment complete: {assessment.get('complete', False)}; origin: {_text(assessment.get('origin', 'unknown'))}; condition: {_text(req.get('condition', 'none'))}")
    lines.insert(9, f"Retired: {row.get('retired', False)}; revision ancestry: {_text(row.get('revision_ancestry', []))}")
    lines.append("Decision CAS (submit as --row-revision): " + row.get("decision_revision", row["row_revision"]))
    lines.append("Policy status/action: " + _text(json.dumps(row.get("policy_status", {"status": "unverified", "action": "policy-status"}), sort_keys=True)))
    for anchor in req.get("anchors", req.get("source_anchors", [])):
        lines.append("  " + _anchor(anchor))
    for heading, keys in (("Supporting evidence", ("support", "supporting_evidence")), ("Counterevidence", ("counterevidence", "contradictions"))):
        lines.append(heading + " (fictional):")
        anchors = assessment.get(keys[0], assessment.get(keys[1], []))
        for item in anchors:
            lines.append("  " + (_anchor(item.get("anchor", item)) if isinstance(item, Mapping) else _text(item)))
        if not anchors:
            lines.append("  none recorded")
    lines.append("Human approval records a review decision; it does not establish legal compliance or commit a vendor.")
    return "\n".join(lines)


def format_amendments(envelope: Mapping[str, Any]) -> str:
    """Readable bounded proposals; all source/reason text stays terminal-inert."""
    report = envelope.get("comparison", {})
    if not report:
        return "No owned preceding source/assessment generation is available for comparison. Import/update first; no correspondence or approval was inferred."
    old_rows = {row["row_id"]: row for row in report["old_rows"]}
    new_rows = {row["row_id"]: row for row in report["new_rows"]}
    lines = ["Amendment correspondence proposals — NOT semantic equivalence or compliance.",
             "Changed generations require fresh review; human correspondence never carries approval.",
             "Comparison: " + report["comparison_id"], "Old matrix: " + report["old_identity"]["matrix_generation"],
             "New/current matrix: " + report["new_identity"]["matrix_generation"],
             "Correspondence CAS: " + str(envelope.get("correspondence_revision")),
             "Counts: " + _text(report["counts"]),
             f"Truncated={report['truncated']}; retrieval incomplete={report['retrieval_incomplete']}"]
    for candidate in report["candidates"]:
        old, new = old_rows[candidate["old_row_id"]], new_rows[candidate["new_row_id"]]
        lines.extend(["\nUNCONFIRMED " + candidate["candidate_id"] + "; lexical overlap=" + str(candidate["lexical_score"]),
                      "OLD " + old["row_id"] + "=" + old["row_revision"] + " " + _text(old["literal_excerpt"]),
                      "NEW " + new["row_id"] + "=" + new["row_revision"] + " " + _text(new["literal_excerpt"]),
                      "Independent attribute changes: " + _text(candidate["field_changes"]),
                      "Split/merge ambiguity: " + _text(candidate["ambiguity"])])
    for label, key in (("Unmapped old mandatory", "unmapped_mandatory_old_ids"), ("Unmapped new mandatory", "unmapped_mandatory_new_ids")):
        ids = report[key]
        lines.append(label + ": " + _text(ids[:100]) + (f" (display omitted {len(ids)-100}; full JSON retains inventory)" if len(ids) > 100 else ""))
    lines.append("Human correspondence events (not row approvals):")
    for event in envelope.get("human_events", [])[-100:]:
        lines.append(_text({key: event[key] for key in ("event_id", "relation", "old_row_ids", "new_row_ids", "actor", "reason", "revision")}))
    return "\n".join(lines)


def format_inventory_audit(envelope: Mapping[str, Any]) -> str:
    audit = envelope.get("audit", {})
    if not audit:
        return "Original machine inventory report is unavailable; no classifier was rerun and no completeness was inferred."
    snapshot = envelope["snapshot"]
    promoted = {event["candidate_id"] for event in envelope.get("promotions", [])}
    lines = ["Frozen machine inventory attribution; semantic recall and all-requirements-found are UNKNOWN.",
             "Machine report remains original. Human promotions create PENDING rows, never approval or blind predictions.",
             "Run: " + snapshot["run_id"] + "; matrix " + snapshot["matrix_generation"],
             "Assessment: " + snapshot["assessment_generation"] + "; artifact " + audit["artifact_sha"],
             "Audit: " + audit["audit_sha"] + "; original machine report " + audit["inventory_report_sha"],
             "Counts (original machine attribution): " + _text(audit["counts"]),
             "Coverage complete=" + str(audit["coverage_complete"]) + "; attribution complete=" + str(audit["attribution_complete"])]
    for entry in audit["entries"]:
        pointer = entry["anchor"]
        literal = entry["literal_clause"]
        lines.append(" | ".join((_text(entry["candidate_id"]), entry["disposition"],
            "already human-promoted" if entry["candidate_id"] in promoted else "eligible source atom" if entry["promotable"] else "retained atom",
            f"page {pointer['page_index'] + 1} [{pointer['start']}:{pointer['end']}]", _text(entry["reason"]),
            _text(literal[:800]) + (" [excerpt truncated; full registered literal in JSON]" if len(literal) > 800 else ""))))
    lines.append("Unanchored structural gaps cannot be promoted: " + str(len(audit["structural_gaps"])))
    lines.append("Current-source human promotion events: " + _text([event["event_id"] for event in envelope.get("promotions", [])]))
    return "\n".join(lines)


def review_terminal(store: Any, run_id: str, *, row_id: str | None = None, queue: str = "all", source_context: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None, prepare_review: Callable[[str], Mapping[str, Any]] | None = None, revise_requirement: Callable[..., Mapping[str, Any]] | None = None, after_decision: Callable[[], Any] | None = None, read: Callable[[str], str] = input, write: Callable[[str], Any] = print) -> None:
    """Refresh generations before each decision; never infer approval from an edit."""
    while True:
        snapshot = store.load_run(run_id)
        from rfp_matrix.review.currentness import presented
        snapshot = presented(snapshot, provider=store.policy_provider)
        from rfp_matrix.review.currentness import in_queue
        rows = [r for r in snapshot["rows"] if (row_id is None or r["row_id"] == row_id) and in_queue(r, queue)]
        if not rows:
            write("No matching rows. Current inventory remains available with --queue all.")
            return
        write(format_inventory(snapshot, queue))
        selected = rows[0] if row_id else None
        if selected is None:
            wanted = read("Row ID, or Q to leave: ").strip()
            if wanted.lower() == "q":
                return
            selected = next((r for r in rows if r["row_id"] == wanted), None)
            if selected is None:
                write("Unknown row in this queue.")
                continue
        presentation = prepare_review(selected["row_id"]) if prepare_review else {}
        write("Graph presentation: " + _text(json.dumps(presentation, ensure_ascii=False, sort_keys=True)))
        write(format_row(snapshot, selected))
        action = read("Enter/S source · V page coverage · A approve positive · G accept gap · E edit note · R reject · D defer · N not applicable · I interpretation · C applicability · X split · M merge · Q leave: ").strip().lower()
        if action == "q":
            return
        if action == "v":
            coverage_terminal(store, run_id, read=read, write=write)
            continue
        if action in ("", "s"):
            write(source_context(snapshot, selected) if source_context else "Source context unavailable.")
            continue
        if action in ("i", "c", "x", "m"):
            if revise_requirement is None:
                write("Requirement revision service is unavailable.")
                continue
            operation = {"i": "interpretation", "c": "applicability", "x": "split", "m": "merge"}[action]
            old_ids = [selected["row_id"]]
            if action == "m":
                old_ids.extend(item.strip() for item in read("Other row IDs to merge, comma separated: ").split(",") if item.strip())
            interpretations = None
            applicability = None
            if action == "x":
                interpretations = json.loads(read('Split child interpretations as a JSON array, e.g. ["First obligation", "Second obligation"]: '))
                if not isinstance(interpretations, list) or any(not isinstance(item, str) for item in interpretations):
                    raise ValueError("Split interpretations must be a JSON array of strings")
            elif action in ("i", "m"):
                interpretations = [read("New interpretation (literal source anchors remain unchanged): ")]
            else:
                applicability = read("Applicability applies / unknown / not_applies: ").strip()
            reason = read("Required rationale for requirement revision: ").strip()
            request_id = "V-" + str(uuid.uuid4())
            write(f"Revision request ID: {request_id}; retain to retry this exact payload.")
            revised = revise_requirement(operation=operation, row_ids=old_ids, interpretations=interpretations, applicability=applicability, reason=reason, method="B0a", expected_matrix_generation=snapshot["matrix_generation"], request_id=request_id)
            write("Requirement revision committed; changed rows are pending. Generation: " + str(revised["matrix_generation"]))
            row_id = None
            continue
        actions = {"a": "approve_positive", "g": "accept_gap", "e": "edit", "r": "reject", "d": "defer", "n": "not_applies"}
        if action not in actions:
            write("Choose one of the displayed review actions.")
            continue
        reason = read("Reason / human review rationale: ").strip()
        answer_text = read("Unvalidated edited response (returns row to pending): ") if action == "e" else None
        human_validated = action == "a" and read("Only after checking this exact restricted answer template and quoted evidence, type VALIDATE to attest human validation (otherwise press Enter): ").strip() == "VALIDATE"
        request_id = presentation.get("presentation", {}).get("request_id") or str(uuid.uuid4())
        write(f"Decision request ID: {request_id}; retain this ID to retry the same payload.")
        event = store.decide(run_id, selected["row_id"], actions[action], expected_assessment_generation=snapshot["assessment_generation"], expected_row_revision=selected.get("decision_revision", selected["row_revision"]), request_id=request_id, reason=reason, answer_text=answer_text, human_validated=human_validated)
        write("Decision committed: " + _text(json.dumps(event, ensure_ascii=False, sort_keys=True)))
        if after_decision:
            write("Graph reconcile: " + _text(json.dumps(after_decision(), ensure_ascii=False, sort_keys=True)))


def coverage_terminal(store: Any, run_id: str, *, page_number: int | None = None,
                      read: Callable[[str], str] = input, write: Callable[[str], Any] = print) -> None:
    """Show original page identity before each explicit, CAS-bound inspection."""
    while True:
        snapshot = store.load_run(run_id)
        state = store.coverage_state(run_id)
        if state["matrix_generation"] != snapshot["matrix_generation"]:
            write("Matrix changed while loading coverage; refreshing.")
            continue
        artifact = snapshot["artifact"]
        summary = state["effective"]
        write("Coverage revision: " + summary["summary_revision"])
        for page in summary["pages"]:
            write(f"Page {page['page_index'] + 1}: {_text(page['disposition'])}; original {_text(page['original_coverage'])}; issues {_text(page['issues'])}; decision {_text(page['decision_revision'])}")
        wanted = str(page_number) if page_number is not None else read("One-based page to inspect, or Q to leave: ").strip()
        page_number = None
        if wanted.lower() == "q":
            return
        try:
            index = int(wanted) - 1
            if not 0 <= index < len(artifact["pages"]):
                raise ValueError()
        except ValueError:
            write("Choose an existing one-based page number.")
            continue
        page = artifact["pages"][index]
        disposition = summary["pages"][index]
        write("Artifact SHA256: " + artifact["artifact_sha"] + "; full-page SHA256: " + disposition["page_sha"])
        write("Original source SHA256: " + artifact["source"]["raw_sha"])
        if artifact["source"]["media_type"] == "application/pdf":
            write("Inspect the original PDF page yourself before acceptance: " + _text(store.root / "sources" / (artifact["source"]["raw_sha"] + ".pdf")))
        else:
            write("Synthetic text source; this is not public-PDF evaluation evidence.")
        write("Extracted text (empty extraction alone never proves a blank original):\n" + _text(page["text"]))
        action = read("B accept genuinely blank · T accept inspected native text/structure · U flag unresolved · Q leave: ").strip().lower()
        if action == "q":
            return
        decision = {"b": "accept_blank", "t": "accept_text", "u": "flag_unresolved"}.get(action)
        if decision is None:
            write("Choose a displayed coverage action.")
            continue
        inspected = action in {"b", "t"} and read("After inspecting this exact original page and reading order, type INSPECTED ORIGINAL: ").strip() == "INSPECTED ORIGINAL"
        reason = read("Required inspection rationale: ").strip()
        request_id = "C-" + str(uuid.uuid4())
        write("Coverage request ID: " + request_id + "; retain for the identical retry payload.")
        result = store.inspect_coverage(run_id, index, decision, expected_artifact_sha=artifact["artifact_sha"],
                                       expected_page_hash=disposition["page_sha"], expected_matrix_generation=snapshot["matrix_generation"],
                                       expected_coverage_revision=disposition["decision_revision"], request_id=request_id,
                                       reason=reason, inspected_original=inspected)
        write("Coverage event committed: " + _text(json.dumps(result, ensure_ascii=False, sort_keys=True)))
        write("Prior approvals are stale. If coverage is now complete, run reassess-coverage with this generation to regenerate pending restricted templates; then review exact rows again.")
