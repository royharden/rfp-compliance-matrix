"""Development regression SOURCE ONLY. Not executed during Dirty Mode."""
from copy import deepcopy

import pytest

from rfp_matrix.evidence import build_matrix, make_corpus, reassess_rows, revise_requirements
from rfp_matrix.review import ReviewError, ReviewStore
from rfp_matrix.source import SourceRegistry, coverage_page_sha, effective_coverage, parse_text_pages


@pytest.fixture
def run(tmp_path):
    artifact = parse_text_pages([
        "The vendor must provide encrypted daily backups.\nThe vendor must provide audit log exports.", ""
    ], source_id="synthetic-review", terms="Self-authored fictional development fixture")
    corpus = make_corpus([{"record_id": "V1", "text": "The vendor must provide encrypted daily backups.",
                           "terms": "Self-authored fictional fixture", "fictional": True}])
    matrix = build_matrix(artifact, corpus)
    store = ReviewStore(tmp_path / "state")
    SourceRegistry(store.root / "sources").save(artifact)
    snapshot = store.create_run("R1", artifact, corpus, matrix["rows"], inventory_report=matrix["inventory_report"])
    assert len(snapshot["rows"]) == 2  # Fixture sanity, not a measured product score.
    return store, snapshot


def _defer(store, snapshot, row, request="D1", **changes):
    payload = dict(expected_assessment_generation=snapshot["assessment_generation"],
                   expected_row_revision=row.get("decision_revision", row["row_revision"]), request_id=request, reason="Inspect later")
    payload.update(changes)
    return store.decide("R1", row["row_id"], "defer", **payload)


def test_exact_row_cas_retry_and_changed_intent(run):
    """what_bug_this_catches: stale/changed retries overwriting an exact displayed decision."""
    store, before = run
    row = before["rows"][0]
    event = _defer(store, before, row)
    assert _defer(store, before, row) == event
    assert len(store.events("R1")) == 1
    with pytest.raises(ReviewError) as error:
        _defer(store, before, row, reason="A different intent")
    assert error.value.code == "idempotency_conflict"
    with pytest.raises(ReviewError) as error:
        _defer(store, before, row, request="D2", expected_row_revision="0" * 64)
    assert error.value.code == "stale_revision"
    assert store.events("R1") == [event]


def test_row_edit_does_not_approve_or_stale_unrelated_row_but_corpus_update_does(run):
    """what_bug_this_catches: reviewer prose becoming approved, or row-local edits invalidating the whole corpus."""
    store, before = run
    first, other = before["rows"]
    store.decide("R1", first["row_id"], "edit", expected_assessment_generation=before["assessment_generation"],
                 expected_row_revision=first.get("decision_revision", first["row_revision"]), request_id="edit1", answer_text="Unverified new prose")
    edited = store.load_run("R1")
    assert edited["rows"][0]["approval"] == "pending"
    assert edited["rows"][0]["validation"]["state"] == "unvalidated"
    assert edited["rows"][1]["row_revision"] == other["row_revision"]
    changed = make_corpus([{"record_id": "V2", "text": "We cannot provide encrypted daily backups.",
                            "terms": "Self-authored fictional fixture", "fictional": True}])
    rows = reassess_rows(edited["artifact"], changed, edited["rows"])
    updated = store.update_run("R1", edited["artifact"], changed, rows,
                               expected_matrix_generation=edited["matrix_generation"])
    assert all(row["approval"] == "stale" for row in updated["rows"])
    assert updated["assessment_generation"] != before["assessment_generation"]
    assert updated["change_report"]["corpus"]["all_dependent_approvals_stale"] is True
    history = store.change_history("R1")
    assert history["total_updates"] == 1
    assert history["updates"][0]["changes"]["context_sha"] == updated["change_record"]["context_sha"]


def test_split_merge_retirement_survives_corpus_update(run):
    """what_bug_this_catches: reassessment resurrecting retired parent/child obligations or losing manual lineage."""
    store, original = run
    parent = original["rows"][0]["row_id"]
    replacements = revise_requirements(original["artifact"], original["corpus"], original["rows"],
        operation="split", row_ids=[parent], interpretations=["Encrypted backup storage", "Daily backup schedule"], reason="Separate obligations")
    split = store.revise_rows("R1", [parent], replacements, expected_matrix_generation=original["matrix_generation"],
                              request_id="split1", reason="Separate obligations")
    children = [row["row_id"] for row in replacements]
    merged_rows = revise_requirements(split["artifact"], split["corpus"], split["rows"], operation="merge",
                                      row_ids=children, interpretations=["Encrypted daily backup service"], reason="One joint obligation")
    merged = store.revise_rows("R1", children, merged_rows, expected_matrix_generation=split["matrix_generation"],
                               request_id="merge1", reason="One joint obligation")
    changed = make_corpus([], complete=False)
    refreshed = store.update_run("R1", merged["artifact"], changed,
        reassess_rows(merged["artifact"], changed, merged["rows"]), expected_matrix_generation=merged["matrix_generation"])
    by_id = {row["row_id"]: row for row in refreshed["rows"]}
    assert all(by_id[identity]["retired"] for identity in [parent, *children])
    assert by_id[merged_rows[0]["row_id"]]["requirement"]["interpretation"] == "Encrypted daily backup service"
    with pytest.raises(ReviewError) as error:
        _defer(store, refreshed, by_id[parent], request="resurrect")
    assert error.value.code == "row_retired"


def test_coverage_pure_event_actor_envelope_cas_and_reassessment(run):
    """what_bug_this_catches: forged actors/envelopes in pure events, stale coverage writes, or auto-approved blank pages."""
    store, before = run
    frozen = deepcopy(before["artifact"])
    args = dict(expected_artifact_sha=frozen["artifact_sha"], expected_page_hash=coverage_page_sha(frozen, 1),
                expected_matrix_generation=before["matrix_generation"], expected_coverage_revision=None,
                request_id="blank1", reason="Original synthetic page inspected and genuinely empty", inspected_original=True)
    result = store.inspect_coverage("R1", 1, "accept_blank", **args)
    assert result["event"]["actor"] == store.actor
    assert "created_at" not in result["event"] and "event_id" not in result["event"]
    assert result["created_at"] and result["event_id"]
    assert effective_coverage(frozen, [result["event"]])["complete"] is True
    assert store.inspect_coverage("R1", 1, "accept_blank", **args)["event"] == result["event"]
    with pytest.raises(TypeError):
        store.inspect_coverage("R1", 1, "accept_blank", actor="forged", **args)
    stale = dict(args, request_id="blank2", expected_matrix_generation=result["matrix_generation"])
    with pytest.raises(ValueError) as error:
        store.inspect_coverage("R1", 1, "flag_unresolved", **stale)
    assert error.value.code == "coverage_conflict"
    after = store.reassess_coverage("R1", expected_matrix_generation=result["matrix_generation"],
                                    request_id="assess1", reason="Reassess inspected source")
    assert all(row["approval"] == "pending" for row in after["rows"] if not row.get("retired"))
    assert after["artifact"] == frozen and before["artifact"]["complete"] is False
    assert all("source_coverage_incomplete" not in row["requirement"]["uncertainty"] for row in after["rows"])
