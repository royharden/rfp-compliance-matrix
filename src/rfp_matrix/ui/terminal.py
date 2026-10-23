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
    selected = [r for r in rows if queue == "all" or r.get("approval", "pending") == queue]
    for row in selected:
        req = row["requirement"]
        lines.append(" | ".join(str(v) for v in (row["row_id"], req.get("mandatory", False), req.get("conditional", False), req.get("administrative", False), row["assessment"].get("status", "needs_review"), row.get("approval", "pending"), _text(req.get("literal_clause", req.get("text", ""))).replace("\n", " "))))
    lines.append(f"Visible {len(selected)} / total {len(rows)} rows; unresolved rows remain part of every export.")
    retired = [row["row_id"] for row in selected if row.get("retired", False)]
    if retired:
        lines.append("Historical retired rows (cannot be reapproved): " + ", ".join(retired))
    if not artifact.get("complete", False):
        lines.append("COVERAGE NEEDS REVIEW: extraction is not a complete-coverage guarantee.")
    return "\n".join(lines)


def format_row(snapshot: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    req = row["requirement"]
    assessment = row["assessment"]
    lines = [f"Row {row['row_id']} — {_text(row.get('approval', 'pending'))}", f"Requirement: {_text(req.get('literal_clause', req.get('text', '')))}", f"Interpretation: {_text(req.get('interpretation', req.get('normalized_interpretation', '')))}", f"Mandatory={req.get('mandatory', False)} conditional={req.get('conditional', False)} administrative={req.get('administrative', False)}", f"Applicability: {_text(req.get('applicability', 'unknown'))}; uncertainty: {_text(req.get('uncertainty', req.get('uncertain_split', '')))}", f"Parents: {_text(req.get('parent_ids', req.get('parent_id', [])))}; children: {_text(req.get('child_ids', []))}", f"Evidence: {_text(assessment.get('status', 'needs_review'))}; method: {_text(assessment.get('method', assessment.get('provenance', {})))}", f"Answer template: {_text(row.get('answer_text', ''))}", f"Unvalidated note (excluded from commitments): {_text(row.get('unvalidated_note', ''))}", f"Validation: {_text(json.dumps(row.get('validation', {}), ensure_ascii=False, sort_keys=True))}", f"Assessment generation: {snapshot['assessment_generation']}", f"Row revision: {row['row_revision']}", f"Decision event: {row.get('decision_event_id') or 'none'}", f"Invalidation reason: {_text(row.get('invalidation_reason', 'none'))}", "Source anchors:"]
    lines.insert(7, "Evidence reason: " + _text(assessment.get("reason", "unknown")))
    lines.insert(8, f"Assessment complete: {assessment.get('complete', False)}; origin: {_text(assessment.get('origin', 'unknown'))}; condition: {_text(req.get('condition', 'none'))}")
    lines.insert(9, f"Retired: {row.get('retired', False)}; revision ancestry: {_text(row.get('revision_ancestry', []))}")
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


def review_terminal(store: Any, run_id: str, *, row_id: str | None = None, queue: str = "all", source_context: Callable[[Mapping[str, Any], Mapping[str, Any]], str] | None = None, prepare_review: Callable[[str], Mapping[str, Any]] | None = None, revise_requirement: Callable[..., Mapping[str, Any]] | None = None, after_decision: Callable[[], Any] | None = None, read: Callable[[str], str] = input, write: Callable[[str], Any] = print) -> None:
    """Refresh generations before each decision; never infer approval from an edit."""
    while True:
        snapshot = store.load_run(run_id)
        rows = [r for r in snapshot["rows"] if (row_id is None or r["row_id"] == row_id) and (queue == "all" or r.get("approval", "pending") == queue)]
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
        action = read("Enter/S source · A approve positive · G accept gap · E edit note · R reject · D defer · N not applicable · I interpretation · C applicability · X split · M merge · Q leave: ").strip().lower()
        if action == "q":
            return
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
        event = store.decide(run_id, selected["row_id"], actions[action], expected_assessment_generation=snapshot["assessment_generation"], expected_row_revision=selected["row_revision"], request_id=request_id, reason=reason, answer_text=answer_text, human_validated=human_validated)
        write("Decision committed: " + _text(json.dumps(event, ensure_ascii=False, sort_keys=True)))
        if after_decision:
            write("Graph reconcile: " + _text(json.dumps(after_decision(), ensure_ascii=False, sort_keys=True)))
