"""Pure presenter regression SOURCE; all definitions UNRUN in Dirty Mode."""
from rfp_matrix.web.presentation import changes_view, matrix_view, row_view


def snapshot():
    return {"run_id": "demo", "matrix_generation": "a" * 64, "assessment_generation": "b" * 64,
        "policy_status": {"status": "current", "allows_current_claim": True, "reasons": []},  # Explicit fake application status.
        "corpus": {"complete": True}, "coverage": {"effective": {"artifact_sha": "c" * 64,
            "complete": True, "unresolved_pages": [], "artifact_issues": [],
            "pages": [{"page_index": 0, "page_sha": "d" * 64, "original_coverage": "ok", "resolved": True}]}},
        "rows": [{"row_id": "R1", "row_revision": "e" * 64, "assessment_generation": "b" * 64,
            "approval": "pending", "answer_text": "<script>document text only</script>",
            "requirement": {"literal_clause": "If chosen, must submit certification.", "mandatory": True,
                "conditional": True, "administrative": True, "applicability": "applies",
                "anchors": [{"page_index": 0, "start": 0, "end": 5, "quote": "exact"}]},
            "assessment": {"status": "supported", "complete": True, "support": [{"record_id": "V", "quote": "fictional"}],
                "counterevidence": [], "origin": "mock"},
            "validation": {"state": "pending", "method": "restricted_template"}}]}


def test_presenter_preserves_plain_untrusted_text_flags_and_versions():
    """what_bug_this_catches: document markup is trusted or independent obligation flags disappear."""
    value = snapshot()
    view = row_view(value, value["rows"][0])
    assert view["row"]["answer_text"] == "<script>document text only</script>"
    assert view["row"]["approved_answer"] is None
    assert all(view["requirement"][key] for key in ("mandatory", "conditional", "administrative"))
    assert view["requirement"]["anchors"][0]["page_number"] == 1
    assert view["controls"]["expected_row_revision"] == "e" * 64 and "actor" not in view["controls"]


def test_historical_and_retired_rows_cannot_show_current_commitment():
    """what_bug_this_catches: old approved status resurrects a current answer or mutation control."""
    value = snapshot()
    row = value["rows"][0]
    row.update(approval="approved_positive", decision_event_id="decision-1", retired=True)
    row["validation"]["state"] = "validated"
    view = row_view(value, row)
    assert view["row"]["label"] == "retired" and view["row"]["approved_answer"] is None
    assert view["controls"]["actions"] == []
    row["retired"] = False
    value["historical"] = True
    assert row_view(value, row)["row"]["label"] == "stale"


def test_gap_acknowledgement_never_becomes_positive_answer():
    """what_bug_this_catches: acknowledged absence/conflict is presented as vendor compliance."""
    value = snapshot()
    row = value["rows"][0]
    row.update(approval="approved_gap", decision_event_id="gap-1")
    row["assessment"].update(status="cannot_answer_absent", support=[])
    view = row_view(value, row)
    assert view["row"]["gap_acknowledged"] is True and view["row"]["approved_answer"] is None
    assert "approve_positive" not in view["controls"]["actions"]


def test_missing_and_malformed_partial_views_fail_closed():
    """what_bug_this_catches: missing coverage/versions or malformed status enable positive controls."""
    value = snapshot()
    value.pop("coverage")
    row = value["rows"][0]
    row["assessment"]["status"] = {"unexpected": "shape"}
    view = row_view(value, row)
    assert view["evidence"]["status"] == "needs_review" and not view["coverage"]["complete"]
    assert "approve_positive" not in view["controls"]["actions"]
    assert row_view({}, {})["controls"]["actions"] == []


def test_matrix_and_changes_keep_truncation_and_all_row_counts_visible():
    """what_bug_this_catches: display truncation hides unresolved counts or implies equivalent revisions."""
    value = snapshot()
    value["rows"][0]["answer_text"] = "x" * 800
    matrix = matrix_view(value)
    assert matrix["counts"]["total"] == 1 and matrix["rows"][0]["text_truncated"] is True
    assert len(matrix["rows"][0]["row"]["answer_text"]) == 600
    changes = changes_view({"context_sha": "f" * 64, "all_dependent_approvals_stale": True,
        "source": {"ancestry": "unknown_requires_explicit_rebase"}, "entries": [{"kind": "pages_changed", "page_index": 0}] * 101})
    assert changes["truncated"] is True and changes["all_dependent_approvals_stale"] is True
    assert changes["semantic_equivalence"] == "not_assessed"
