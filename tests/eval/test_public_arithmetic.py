"""Public development cases with independently computed expectations."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from rfp_matrix.eval.scoring import score_packet


@pytest.fixture
def public_packet():
    return json.loads((Path(__file__).resolve().parents[2] / "eval/hand-oracle.v1.json").read_text(encoding="utf-8"))


def test_bundled_arithmetic_counts_and_denominators(public_packet):
    """what_bug_this_catches: plausible aggregate values conceal wrong scoring units."""
    result = score_packet(public_packet)
    arm = result["methods"]["B0a"]
    assert arm["inventory"]["precision"]["numerator"] == 2
    assert arm["inventory"]["precision"]["denominator"] == 3
    assert arm["inventory"]["mandatory_recall"]["value"] == 1
    assert arm["retrieval"]["recall_at_10"] == pytest.approx(0.75)
    assert arm["retrieval"]["mrr_at_10"] == pytest.approx(0.75)
    assert arm["citations"]["semantic_precision"]["value"] == 0.5
    assert arm["cannot_answer"]["combined"]["precision"]["value"] == 0.5
    assert arm["cannot_answer"]["combined"]["coverage"]["value"] == 0.5
    assert result["public_quality_claim"] is False
    assert all(result["methods"][m]["status"] == "not_yet_measured" for m in ("B0b", "F1", "F2"))


def test_no_matches_reports_every_mandatory_miss(public_packet):
    """what_bug_this_catches: unmatched mandatory obligations disappear in a mean."""
    public_packet["methods"]["B0a"]["inventory"]["equivalence_edges"] = []
    arm = score_packet(public_packet)["methods"]["B0a"]
    assert arm["inventory"]["recall"]["value"] == 0
    assert [m["id"] for m in arm["inventory"]["mandatory_misses"]] == ["g1", "g2"]


def test_empty_labels_and_empty_evidence_remain_na(public_packet):
    """what_bug_this_catches: an empty reference population scores as success."""
    arm = public_packet["methods"]["B0a"]
    arm["inventory"] = {"equivalence_origin": "hand_oracle", "gold_atoms": [], "predicted_atoms": [], "equivalence_edges": []}
    arm["citation_cases"] = []
    arm["abstention_cases"] = []
    arm["retrieval_cases"] = [{"id": "empty", "relevant_ids": [], "retrieved_ids": []}]
    scored = score_packet(public_packet)["methods"]["B0a"]
    assert scored["inventory"]["recall"]["value"] is None
    assert scored["inventory"]["precision"]["value"] is None
    assert scored["citations"]["semantic_precision"]["value"] is None
    assert scored["retrieval"]["recall_at_10"] is None
    assert scored["retrieval"]["no_evidence_cases_excluded"] == 1


def test_wrong_abstention_reason_is_not_correct(public_packet):
    """what_bug_this_catches: absent and conflicting predictions are conflated."""
    public_packet["methods"]["B0a"]["abstention_cases"] = [{
        "id": "wrong-reason", "gold_status": "cannot_answer_conflicting",
        "predicted_status": "cannot_answer_absent"}]
    scored = score_packet(public_packet)["methods"]["B0a"]["cannot_answer"]
    assert scored["combined"]["precision"]["value"] == 0
    assert scored["combined"]["coverage"]["value"] == 0
    assert scored["cannot_answer_absent"]["precision"]["value"] == 0
    assert scored["cannot_answer_conflicting"]["coverage"]["value"] == 0


def test_retrieval_at_ten_excludes_eleventh_hit(public_packet):
    """what_bug_this_catches: unbounded retrieval inflates recall@10 and MRR@10."""
    public_packet["methods"]["B0a"]["retrieval_cases"] = [{
        "id": "late", "relevant_ids": ["gold"],
        "retrieved_ids": [f"irrelevant-{i}" for i in range(10)] + ["gold"]}]
    scored = score_packet(public_packet)["methods"]["B0a"]["retrieval"]
    assert scored["recall_at_10"] == 0
    assert scored["mrr_at_10"] == 0


@pytest.mark.parametrize("mutation", ["missing_mandatory", "unknown_edge", "duplicate_id", "no_adjudication"])
def test_invalid_labels_fail_explicitly(public_packet, mutation):
    """what_bug_this_catches: invalid/missing labels become reported model evidence."""
    inventory = public_packet["methods"]["B0a"]["inventory"]
    if mutation == "missing_mandatory":
        inventory["gold_atoms"][0].pop("mandatory")
    elif mutation == "unknown_edge":
        inventory["equivalence_edges"].append(["nonexistent", "g1"])
    elif mutation == "duplicate_id":
        inventory["gold_atoms"].append(deepcopy(inventory["gold_atoms"][0]))
    else:
        inventory["equivalence_origin"] = "span_overlap"
    with pytest.raises(ValueError):
        score_packet(public_packet)


def test_split_boundary_rejects_confirmatory_input_before_scoring(public_packet):
    """what_bug_this_catches: developer runner silently evaluates hidden splits."""
    public_packet["split"] = "heldout"
    with pytest.raises(ValueError, match="split=dev"):
        score_packet(public_packet)
