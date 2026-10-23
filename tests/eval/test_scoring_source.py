"""Unrun regression source; hand-authored development arithmetic, never gold."""
from rfp_matrix.eval.scoring import family_bootstrap_recall, score_packet


def packet(predicted, gold, edges, **extra):
    return {"schema_version": 1, "split": "dev", "methods": {"B0a": {
        "origin": "hand_oracle", "inventory": {"equivalence_origin": "hand_oracle",
        "predicted_atoms": predicted, "gold_atoms": gold, "equivalence_edges": edges}, **extra}}}


def atom(key, *, mandatory=True, family="family-a"):
    return {"id": key, "mandatory": mandatory, "family_id": family,
            "document_id": "doc-a", "page": 1, "clause": key}


def test_augmenting_path_beats_greedy():
    """what_bug_this_catches: first-edge greedy matching loses a valid second atom."""
    result = score_packet(packet([{"id": "p1"}, {"id": "p2"}], [atom("g1"), atom("g2")],
                                [["p1", "g1"], ["p1", "g2"], ["p2", "g1"]]))
    inventory = result["methods"]["B0a"]["inventory"]
    assert inventory["recall"]["value"] == 1
    assert inventory["matches"] == [{"predicted_id": "p1", "gold_id": "g2"}, {"predicted_id": "p2", "gold_id": "g1"}]


def test_duplicate_or_parent_prediction_does_not_get_second_match():
    """what_bug_this_catches: duplicate/parent overlap inflates precision or recall."""
    result = score_packet(packet([{"id": "child"}, {"id": "parent-copy"}], [atom("g")],
                                [["child", "g"], ["parent-copy", "g"]]))
    assert result["methods"]["B0a"]["inventory"]["precision"]["value"] == 0.5
    assert result["methods"]["B0a"]["inventory"]["recall"]["denominator"] == 1


def test_all_abstain_is_na_citation_and_mandatory_miss_not_perfect():
    """what_bug_this_catches: empty citation denominator masquerades as 100% quality."""
    result = score_packet(packet([], [atom("missing")], [], abstention_cases=[
        {"id": "case", "gold_status": "supported", "predicted_status": "cannot_answer_absent"}]))
    method = result["methods"]["B0a"]
    assert method["citations"]["semantic_precision"]["value"] is None
    assert method["citations"]["semantic_precision"]["denominator"] == 0
    assert method["cannot_answer"]["answered_coverage"]["value"] == 0
    assert method["inventory"]["mandatory_misses"][0]["id"] == "missing"
    assert method["inventory"]["mandatory_recall"]["value"] == 0
    assert result["methods"]["F1"]["status"] == "not_yet_measured"
    assert result["public_quality_claim"] is False


def test_support_and_counterevidence_recall_have_separate_denominators():
    """what_bug_this_catches: support retrieval hides a missed contradictory record."""
    result = score_packet(packet([], [], [], retrieval_cases=[{
        "id": "r", "relevant_ids": ["support", "counter"], "retrieved_ids": ["support"],
        "support_ids": ["support"], "counterevidence_ids": ["counter"]}]))
    case = result["methods"]["B0a"]["retrieval"]["cases"][0]
    assert case["recall_at_10"] == 0.5
    assert case["support_recall_at_10"]["value"] == 1
    assert case["counterevidence_recall_at_10"]["value"] == 0
    assert case["missed_ids"] == ["counter"]


def test_family_intervals_seeded_and_insufficiency_explicit():
    """what_bug_this_catches: nondeterministic or invented two-family confidence bounds."""
    gold = [atom(f"g{i}", family=f"f{i}") for i in range(3)]
    first = family_bootstrap_recall(gold, {"g0"}, seed=9, iterations=100)
    second = family_bootstrap_recall(gold, {"g0"}, seed=9, iterations=100)
    assert first == second
    assert first["unstable_small_family_count"] is True
    insufficient = family_bootstrap_recall(gold[:2], {"g0"}, iterations=100)
    assert insufficient["status"] == "insufficient_families"
    assert insufficient["interval_95"] is None
