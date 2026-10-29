"""Authored fictional source regression definitions; UNRUN in Dirty Mode."""
import pytest

from rfp_matrix.contracts import M3Error
from rfp_matrix.evidence import (
    assess_requirement, build_matrix, describe_changes, interpret_judgment,
    inventory_report, make_corpus, reassess_rows, registered_evidence_windows,
    registered_source_candidates, resolve_evidence, retrieve_evidence, revise_requirements,
)
from rfp_matrix.providers import build_questions
from rfp_matrix.source import parse_text_pages


def source(text="The vendor shall provide encrypted backup."):
    return parse_text_pages([text], source_id="authored-rfp", terms="Authored synthetic development only")


def corpus(*texts):
    return make_corpus([{"record_id": f"V{index:03}", "text": text, "terms": "Authored fictional vendor", "fictional": True}
                        for index, text in enumerate(texts)])


def test_contradiction_outside_top_k_cannot_produce_complete_positive():
    """what_bug_this_catches: top-k truncation hides counterevidence while asserting full support."""
    artifact = source()
    pack = corpus(*(["We provide encrypted backup."] * 10), "We do not provide encrypted backup. " + "context " * 100)
    requirement = inventory_report(artifact)["requirements"][0]
    retrieved = retrieve_evidence(requirement, pack)
    assert retrieved["truncated_candidates"] == 1
    assert "V010" not in [item["record_id"] for item in retrieved["candidates"]]
    assessment = assess_requirement(requirement, pack)
    assert assessment["status"] == "needs_review" and assessment["complete"] is False


def test_missing_model_is_incomplete_instead_of_corpus_absence():
    """what_bug_this_catches: unavailable model/budget becomes confident negative vendor capability."""
    artifact = source()
    requirement = inventory_report(artifact, method="F1")["requirements"][0]
    assessment = assess_requirement(requirement, corpus(), method="F1")
    assert assessment["status"] == "needs_review" and assessment["origin"] == "unavailable"


def test_mandatory_conditional_admin_are_independent():
    """what_bug_this_catches: administrative/conditional classification erases mandatory status."""
    report = inventory_report(source("If selected, the bidder must submit certification before the deadline."))
    requirement = report["requirements"][0]
    assert all(requirement[key] is True for key in ("mandatory", "conditional", "administrative"))
    assert requirement["applicability"] == "unknown"


def test_reassessment_preserves_manual_lineage_and_retires_approvals():
    """what_bug_this_catches: corpus update regenerates original requirements and resurrects retired rows."""
    artifact, initial = source(), corpus("We provide encrypted backup.")
    rows = build_matrix(artifact, initial)["rows"]
    manual = revise_requirements(artifact, initial, rows, operation="interpretation",
        row_ids=[rows[0]["row_id"]], interpretations=["The vendor must provide encrypted nightly backup."], reason="Explicit manual interpretation")
    manual[0]["revision_ancestry"] = [rows[0]["row_revision"]]
    manual[0]["unvalidated_note"] = "Reviewer note stays separate"
    retired = {**rows[0], "row_id": "retired-row", "retired": True}
    refreshed = reassess_rows(artifact, corpus("We do not provide encrypted backup."), [manual[0], retired])
    assert len(refreshed) == 1 and refreshed[0]["requirement"] == manual[0]["requirement"]
    assert refreshed[0]["revision_ancestry"] == manual[0]["revision_ancestry"]
    assert refreshed[0]["approval"] == "pending" and refreshed[0]["decision_event_id"] is None
    assert refreshed[0]["unvalidated_note"] == "Reviewer note stays separate"


def test_f2_labels_are_registered_but_never_probabilities():
    """what_bug_this_catches: generated label becomes a fabricated 100% confidence Choice."""
    questions = build_questions("evidence", ["V:0:20"])
    outcome = {"status": "needs_review", "origin": "mock", "model": None, "answers": {},
        "raw_classifications": {"relation": "supports", "selection": "V:0:20"},
        "distribution_semantics": "generated-closed-labels-no-probabilities", "usage_semantics": "simulated"}
    interpreted = interpret_judgment(outcome, questions, method="F2")
    assert interpreted["status"] == "labels" and "answers" not in interpreted
    invalid = {**outcome, "raw_classifications": {"relation": "supports", "selection": "invented-id"}}
    with pytest.raises(M3Error):
        interpret_judgment(invalid, questions, method="F2")


def test_frozen_registries_include_rejected_context_and_registered_literals():
    """what_bug_this_catches: methods receive a provider-filtered registry or fabricated sentence IDs."""
    artifact = source("General background only.\n\nThe vendor shall provide encrypted backup.")
    registry = registered_source_candidates(artifact)
    assert len(registry["candidates"]) == 2 and registry["classification_applied"] is False
    assert any(not item["structural_signal"] for item in registry["candidates"])
    pack = corpus("We provide encrypted backup. We do not store keys.")
    windows = registered_evidence_windows(pack)
    assert any(item["broad_fallback"] for item in windows["windows"])
    for item in windows["windows"]:
        assert resolve_evidence(pack, item["pointer"])["quote"] == item["quote"]


def test_change_context_truncation_does_not_relax_whole_corpus_stale():
    """what_bug_this_catches: unchanged cited passages or truncated display prevent global invalidation."""
    artifact = source()
    old = corpus("We provide encrypted backup.", "Old unrelated passage.")
    new = corpus("We provide encrypted backup.", "Changed unrelated passage.", "Added evidence.")
    result = describe_changes(artifact, artifact, old, new, limit=1, quote_limit=4)
    assert result["all_dependent_approvals_stale"] is True and result["truncated"] is True
    assert result["corpus"]["record_ids"] == {"added": ["V002"], "removed": [], "changed": ["V001"]}
    assert result["source"]["ancestry"] == "same_immutable_artifact"
