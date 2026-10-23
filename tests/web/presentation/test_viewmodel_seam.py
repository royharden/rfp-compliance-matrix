"""Critical pure-viewmodel regression SOURCE only; UNRUN, no renderer/HTTP/DB.

Hostile strings remain plain strings here; Jinja autoescape is D7's separate
template responsibility and is never claimed verified by these definitions.
"""
from copy import deepcopy

import pytest

from rfp_matrix.web.presentation import build_view, coverage_view, matrix_view, row_view


def domain_snapshot():
    pointer = {"source_id": "rfp", "artifact_sha": "a" * 64, "source_sha": "b" * 64,
        "page_index": 1, "start": 7, "end": 12, "quote": "<img>", "excerpt_sha": "c" * 64}
    return {"run_id": "local-run", "matrix_generation": "d" * 64, "assessment_generation": "e" * 64,
        "policy_status": {"status": "current", "allows_current_claim": True, "reasons": []},  # Authored fake application currentness.
        "corpus": {"complete": True}, "coverage": {"effective": {"artifact_sha": "a" * 64,
            "complete": True, "unresolved_pages": [], "artifact_issues": [], "pages": [
                {"page_index": 0, "resolved": True, "page_sha": "f" * 64},
                {"page_index": 1, "resolved": True, "page_sha": "1" * 64}]}},
        "rows": [{"row_id": "R2", "row_revision": "2" * 64, "assessment_generation": "e" * 64,
            "approval": "pending", "answer_text": "Restricted <script>text</script>", "unvalidated_note": "",
            "requirement": {"requirement_id": "R2", "literal_clause": "If selected, the bidder must submit certification.",
                "interpretation": "Explicit original-bound interpretation", "mandatory": True,
                "conditional": True, "administrative": True, "applicability": "applies", "uncertainty": [],
                "anchors": [pointer], "parent_id": "R0", "child_ids": ["R3"]},
            "assessment": {"status": "supported", "complete": True, "method": "F1", "origin": "mock",
                "support": [{"record_id": "Vendor", "sentence_id": "Vendor:0:8", "start": 0, "end": 8, "quote": "supports"}],
                "counterevidence": [], "reason": "Mock evidence only"},
            "validation": {"state": "validated", "method": "explicit-human-assessment"},
            "revision_ancestry": ["R0"], "dependency_revision": "3" * 64}]}


def test_closed_detail_fields_keep_all_columns_flags_offsets_and_plain_text():
    """what_bug_this_catches: source/counterquote columns or exact offsets get lost in HTML adaptation."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    row["assessment"]["counterevidence"] = [{"record_id": "Vendor", "start": 20, "end": 28, "quote": "conflict"}]
    detail = build_view("row", {"snapshot": snapshot, "row": row, "actor": "attacker", "csrf_token": "not-copied"})
    assert set(detail) >= {"row", "requirement", "evidence", "controls", "lineage", "notes", "context", "coverage", "changes", "warnings"}
    assert all(detail["requirement"][name] for name in ("mandatory", "conditional", "administrative"))
    pointer = detail["requirement"]["anchors"][0]
    assert (pointer["page_number"], pointer["start"], pointer["end"], pointer["quote"]) == (2, 7, 12, "<img>")
    assert detail["evidence"]["counterevidence"][0]["quote"] == "conflict"
    assert detail["row"]["answer_text"] == "Restricted <script>text</script>"
    assert "actor" not in detail["controls"] and "csrf_token" not in detail["controls"]


@pytest.mark.parametrize("kind", ["F2", "partial", "uncertain", "coverage", "note"])
def test_uncalibrated_partial_uncertain_or_unvalidated_data_never_positive(kind):
    """what_bug_this_catches: a stale approved-positive flag overrides unresolved domain data."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    row.update(approval="approved_positive", decision_event_id="decision")
    if kind == "F2":
        row["assessment"]["method"] = "F2"
    elif kind == "partial":
        row["assessment"]["status"] = "partial"
    elif kind == "uncertain":
        row["requirement"]["uncertainty"] = ["table_alignment_unknown"]
    elif kind == "coverage":
        snapshot["coverage"]["effective"]["pages"][0]["resolved"] = False
    else:
        row["unvalidated_note"] = "Unvalidated reviewer promise"
    detail = row_view(snapshot, row)
    assert detail["row"]["approved_answer"] is None and "approve_positive" not in detail["controls"]["actions"]


def test_snapshot_versions_not_guessed_and_context_generation_is_checked():
    """what_bug_this_catches: run ID/row-only payload fabricates CAS or mismatched context permits decisions."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    missing = build_view("row", {"run_id": "local-run", "row": row})
    assert missing["controls"]["actions"] == [] and missing["controls"]["expected_assessment_generation"] == ""
    detail = build_view("row", {"row": {"snapshot": snapshot, "row": row},
        "context": {"matrix_generation": "old-generation", "row_revision": row["row_revision"], "anchors": []}})
    assert detail["controls"]["actions"] == [] and any("different generation" in warning for warning in detail["warnings"])
    assert detail["controls"]["expected_assessment_generation"] == snapshot["assessment_generation"]


def test_retired_ancestry_and_decision_metadata_are_domain_bound():
    """what_bug_this_catches: retired descendants regain controls or actor/time comes from caller input."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    row.update(retired=True, decision_event_id="event-1")
    snapshot["decision_events"] = [{"event_id": "other", "actor": "unrelated"},
        {"event_id": "event-1", "actor": "resolved-local-reviewer", "created_at": "2026-10-23T00:00:00Z"}]
    detail = row_view(snapshot, row)
    assert detail["row"]["label"] == "retired" and detail["controls"]["actions"] == []
    assert detail["lineage"]["parent_id"] == "R0" and detail["lineage"]["revision_ancestry"] == ["R0"]
    assert detail["row"]["decision_actor"] == "resolved-local-reviewer"


def test_gap_acknowledgement_separate_from_positive_and_unvalidated_note():
    """what_bug_this_catches: gap history or reviewer prose populates a positive export field."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    row.update(approval="approved_gap", decision_event_id="gap-1", unvalidated_note="Not an approved promise")
    row["assessment"].update(status="cannot_answer_absent", support=[])
    detail = row_view(snapshot, row)
    assert detail["row"]["gap_acknowledged"] is True and detail["row"]["approved_answer"] is None
    assert detail["notes"]["excluded_from_approved_answer"] is True


def test_matrix_dispatch_is_deterministic_preserves_unresolved_counts_and_caps():
    """what_bug_this_catches: hidden rows/changed order or truncated snippets look like a complete approved subset."""
    snapshot = domain_snapshot()
    second = deepcopy(snapshot["rows"][0])
    second["row_id"] = "R1"
    second["answer_text"] = "x" * 700
    snapshot["rows"].append(second)
    original = deepcopy(snapshot)
    matrix = build_view("matrix", {"run": snapshot, "queue": "unresolved"})
    assert [item["row"]["row_id"] for item in matrix["rows"]] == ["R1", "R2"]
    assert matrix["counts"]["total"] == 2 and matrix["counts"]["unresolved"] == 2
    assert matrix["rows"][0]["text_truncated"] is True
    assert len(matrix["rows"][0]["row"]["answer_text"]) == 600 and snapshot == original
    assert len(matrix_view(snapshot, queue="invalid")["rows"]) == 2


def test_partial_coverage_and_bounded_stale_changes_stay_visible():
    """what_bug_this_catches: missing coverage or display truncation relaxes global stale semantics."""
    assert coverage_view({"effective": {"complete": True, "artifact_sha": "a" * 64}})["complete"] is False
    changes = build_view("changes", {"changes": {"context_sha": "b" * 64,
        "all_dependent_approvals_stale": True, "source": {"ancestry": "unknown_requires_explicit_rebase"},
        "entries": [{"kind": "pages_changed", "page_index": 0}] * 101}})
    assert changes["truncated"] is True and changes["display_omitted_entries"] == 1
    assert changes["all_dependent_approvals_stale"] is True and changes["semantic_equivalence"] == "not_assessed"


def test_runs_dispatch_is_closed_and_unknown_kind_is_visible():
    """what_bug_this_catches: route payload leaks paths/provider credentials or arbitrary callable dispatch."""
    view = build_view("runs", {"runs": {"runs": [{"run_id": "R", "row_count": 1, "path": "never-output"}], "total_runs": 1},
        "actor": "not-an-input", "provider_config": {"key": "fake-must-not-copy"}})
    assert len(view["runs"]) == 1 and "path" not in view["runs"][0]
    assert "provider_config" not in view and "actor" not in view
    assert build_view("arbitrary-callable", {})["kind"] == "error"


def test_separate_matrix_row_list_and_coverage_reads_must_share_generation():
    """what_bug_this_catches: concurrent domain commits yield apparently actionable mixed-version views."""
    snapshot = domain_snapshot()
    result = build_view("matrix", {"run": snapshot, "rows": {"matrix_generation": "old"}, "queue": "pending"})
    assert result["queue"] == "all" and result["counts"]["total"] == 1
    assert result["rows"][0]["controls"]["actions"] == [] and result["rows"][0]["row"]["label"] == "stale"
    coverage = {**snapshot["coverage"], "matrix_generation": "old"}
    detail = build_view("row", {"snapshot": snapshot, "row": snapshot["rows"][0], "coverage": coverage})
    assert detail["row"]["current"] is False and detail["controls"]["actions"] == []
    direct = row_view(snapshot, snapshot["rows"][0], context={"row_revision": "old", "anchors": []})
    assert direct["row"]["current"] is False and direct["controls"]["actions"] == []


def test_application_decision_cas_is_forwarded_without_changing_content_revision():
    """what_bug_this_catches: reject/defer leave content unchanged and an old decision CAS is accepted again."""
    snapshot = domain_snapshot()
    row = snapshot["rows"][0]
    content_revision = row["row_revision"]
    row["decision_revision"] = "opaque-application-cas-after-defer"
    detail = row_view(snapshot, row)
    assert detail["row"]["row_revision"] == content_revision
    assert detail["row"]["decision_revision"] == "opaque-application-cas-after-defer"
    assert detail["controls"]["expected_row_revision"] == row["decision_revision"]
    row["decision_revision"] = None  # Present-but-invalid is not a legacy row.
    assert row_view(snapshot, row)["controls"]["actions"] == []
