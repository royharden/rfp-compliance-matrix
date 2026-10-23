"""Authored synthetic amendment regression SOURCE, UNRUN in Dirty Mode.

Snapshot-shaped fixtures are deterministic development data, never human labels,
durable CAS evidence, public-PDF gold or live model quality measurements.
"""
from copy import deepcopy

import pytest

from rfp_matrix.contracts import M3Error, digest
from rfp_matrix.evidence import amendment_candidates, build_matrix, make_corpus, validate_correspondence_selection
from rfp_matrix.source import parse_text_pages
from rfp_matrix.web.presentation import amendments_view, build_view


def snapshot(*clauses, run_id="owned-amendment-run"):
    artifact = parse_text_pages(["\n\n".join(clauses)], source_id="authored-amendment-rfp", terms="Authored fictional development only")
    corpus = make_corpus([{"record_id": "fictional", "text": "We provide audit logs and encrypted backup.",
                           "terms": "Project-authored fictional vendor", "fictional": True}])
    rows = build_matrix(artifact, corpus)["rows"]
    generation = rows[0]["assessment_generation"] if rows else digest("fixture.empty-generation", artifact["artifact_sha"])
    return {"run_id": run_id, "artifact": artifact, "corpus": corpus, "rows": rows,
        "assessment_generation": generation,
        "matrix_generation": digest("fixture.snapshot-shaped-matrix", {"artifact": artifact["artifact_sha"], "rows": rows})}


def select(report, *, relation="one_to_one", old=None, new=None, **overrides):
    arguments = {"comparison_id": report["comparison_id"], "expected_old_identity": report["old_identity"],
        "expected_new_identity": report["new_identity"], "relation": relation,
        "old_row_revisions": old if old is not None else {report["old_rows"][0]["row_id"]: report["old_rows"][0]["row_revision"]},
        "new_row_revisions": new if new is not None else {report["new_rows"][0]["row_id"]: report["new_rows"][0]["row_revision"]}}
    arguments.update(overrides)
    return validate_correspondence_selection(report, **arguments)


def test_changed_dates_conditions_remain_distinct_source_bound_proposals():
    """what_bug_this_catches: similar text erases deadline/condition changes or becomes semantic identity."""
    old = snapshot("If selected, the bidder must submit certification by 2026-11-15.")
    new = snapshot("Unless exempt, the bidder must submit certification by 2026-12-01.")
    report = amendment_candidates(old, new)
    proposal = report["candidates"][0]
    assert proposal["literal_changed"] is True and "condition" in proposal["field_changes"]
    assert proposal["confirmed"] is False and proposal["score_semantics"] == "token_overlap_not_equivalence"
    assert "2026-11-15" in report["old_rows"][0]["literal_excerpt"] and "2026-12-01" in report["new_rows"][0]["literal_excerpt"]
    assert all(report["new_rows"][0][field] for field in ("mandatory", "conditional", "administrative"))
    assert report["old_rows"][0]["anchors"][0]["artifact_sha"] != report["new_rows"][0]["anchors"][0]["artifact_sha"]


def test_repeated_clauses_keep_coordinates_and_expose_split_merge_ambiguity():
    """what_bug_this_catches: duplicate requirements are paired to the first quote with hidden ambiguity."""
    clause = "The vendor must provide encrypted backup."
    report = amendment_candidates(snapshot(clause, clause), snapshot(clause, clause))
    assert len(report["candidates"]) == 4
    assert all(item["ambiguity"]["split_possible"] and item["ambiguity"]["merge_possible"] for item in report["candidates"])
    assert len({row["anchors"][0]["start"] for row in report["old_rows"]}) == 2
    assert len({row["row_id"] for row in report["old_rows"]}) == 2


@pytest.mark.parametrize("relation", ["split", "merge"])
def test_compound_split_merge_is_only_a_human_selection_preflight(relation):
    """what_bug_this_catches: one-to-many lexical proposals silently rewrite ancestry or approve children."""
    compound = snapshot("The vendor must provide audit logs and encrypted backup.")
    separate = snapshot("The vendor must provide audit logs.", "The vendor must provide encrypted backup.")
    old, new = (compound, separate) if relation == "split" else (separate, compound)
    report = amendment_candidates(old, new)
    selection = select(report, relation=relation,
        old={row["row_id"]: row["row_revision"] for row in report["old_rows"]},
        new={row["row_id"]: row["row_revision"] for row in report["new_rows"]})
    assert selection["human_confirmation_required"] is True and selection["approval_carry_allowed"] is False
    assert all(item["confirmed"] is False for item in report["candidates"])


def test_unmatched_mandatory_additions_and_removals_stay_in_inventory():
    """what_bug_this_catches: rows without lexical matches disappear or are treated as confirmed added/removed."""
    report = amendment_candidates(snapshot("The vendor shall encrypt backups."), snapshot("The bidder must submit notarized certification."))
    assert report["candidates"] == []
    assert len(report["unmapped_mandatory_old_ids"]) == len(report["unmapped_mandatory_new_ids"]) == 1
    assert report["counts"]["added"] == report["counts"]["removed"] == 1
    assert report["added_removed_semantics"] == "unmapped_lexical_proposals_not_confirmed_additions_or_removals"
    assert select(report, relation="added", old={})["approval_carry_allowed"] is False


def test_stale_artifact_generation_and_row_revisions_cannot_be_rebound():
    """what_bug_this_catches: old correspondence payload is replayed against a new artifact or edited row."""
    old = snapshot("The vendor must provide encrypted backup.")
    new = snapshot("The vendor must provide encrypted nightly backup.")
    report = amendment_candidates(old, new)
    stale_identity = {**report["new_identity"], "artifact_sha": "0" * 64}
    with pytest.raises(M3Error) as error:
        select(report, expected_new_identity=stale_identity)
    assert error.value.code == "stale_correspondence"
    with pytest.raises(M3Error):
        select(report, new={report["new_rows"][0]["row_id"]: "0" * 64})
    another = amendment_candidates(old, snapshot("The vendor must provide encrypted annual backup."))
    with pytest.raises(M3Error):
        select(another, comparison_id=report["comparison_id"], expected_new_identity=report["new_identity"])
    with pytest.raises(M3Error) as error:
        amendment_candidates(old, snapshot("The vendor must provide encrypted backup.", run_id="other-run"))
    assert error.value.code == "amendment_run_mismatch"


def test_candidate_cap_is_deterministic_and_keeps_unmapped_mandatory_rows():
    """what_bug_this_catches: candidate truncation hides mandatory obligations or produces unstable request pins."""
    clause = "The vendor must provide encrypted backup."
    old, new = snapshot(*([clause] * 5)), snapshot(*([clause] * 5))
    before = deepcopy((old, new))
    report = amendment_candidates(old, new, limit=1)
    assert report == amendment_candidates(old, new, limit=1) and (old, new) == before
    assert report["truncated"] is True and report["counts"]["candidate_pairs"] == 25
    assert report["omitted_candidates"] == 24
    assert len(report["old_rows"]) == len(report["new_rows"]) == 5
    assert len(report["unmapped_mandatory_old_ids"]) == len(report["unmapped_mandatory_new_ids"]) == 4


def test_correspondence_view_never_carries_approval_or_confirms_a_proposal():
    """what_bug_this_catches: exact-looking correspondence or human lineage is turned into reapproval."""
    old = snapshot("The vendor must provide encrypted backup.")
    new = snapshot("The vendor must provide encrypted nightly backup.")
    old["rows"][0].update(approval="approved_positive", decision_event_id="old-decision")
    new["rows"][0].update(approval="stale", decision_event_id=None)
    report = amendment_candidates(old, new)
    events = [{"event_id": "human-map-1", "comparison_id": report["comparison_id"], "relation": "one_to_one",
        "actor": "domain-resolved-reviewer", "reason": "Human lineage record, not approval", "old_row_ids": [report["old_rows"][0]["row_id"]],
        "new_row_ids": [report["new_rows"][0]["row_id"]]}]
    view = build_view("amendments", {"comparison": report, "human_events": events})
    assert view["controls"]["expected_correspondence_revision"] is None
    versioned = amendments_view({"comparison": report, "human_events": events, "correspondence_revision": "f" * 64})
    assert versioned["controls"]["expected_correspondence_revision"] == "f" * 64
    assert view["approval_carry_allowed"] is False and view["controls"]["confers_approval"] is False
    assert view["candidates"][0]["confirmed"] is False and "approved_answer" not in view["candidates"][0]
    assert view["human_events"][0]["label"] == "application_supplied_human_correspondence_record"
    assert new["rows"][0]["approval"] == "stale" and new["rows"][0]["decision_event_id"] is None
    assert "actor" not in view["controls"]


def test_retired_other_artifact_ancestry_stays_unresolved_not_retargeted():
    """what_bug_this_catches: retired historical pointers are rebound to current text or become active matches."""
    old = snapshot("The vendor must retain historical audit logs.")
    new = snapshot("The vendor must provide encrypted backup.")
    retired = deepcopy(old["rows"][0])
    retired.update(row_id="retired-original", retired=True)
    retired["requirement"]["requirement_id"] = "retired-original"
    new["rows"].append(retired)
    report = amendment_candidates(old, new)
    historical = next(row for row in report["new_rows"] if row["retired"])
    assert historical["unresolved_historical_anchors"] is True and historical["anchors"] == []
    assert all(item["new_row_id"] != "retired-original" for item in report["candidates"])
    with pytest.raises(M3Error):
        select(report, new={"retired-original": historical["row_revision"]})
    assert amendments_view(report)["approval_carry_allowed"] is False
