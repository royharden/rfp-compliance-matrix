"""Synthetic audit/promotion regression SOURCE. All definitions UNRUN.

No lawful public-PDF corpus, human gold, semantic recall, authenticated CAS or
runtime evidence is produced by these authored development mappings.
"""
from copy import deepcopy

import pytest

from rfp_matrix.contracts import M3Error, digest
from rfp_matrix.evidence import (
    build_matrix, inventory_audit, inventory_report, make_corpus,
    promote_inventory_candidate, reassess_rows,
)
from rfp_matrix.source import parse_text_pages
from rfp_matrix.web.presentation import inventory_audit_view


def fixture(text=None, *, method="B0a"):
    text = text or "General background for discussion.\n\nThe vendor must provide encrypted backup."
    artifact = parse_text_pages([text], source_id="authored-inventory-audit", terms="Project-authored development text only")
    corpus = make_corpus([{"record_id": "fictional", "text": "We provide encrypted backup.",
                           "terms": "Project-authored fictional vendor", "fictional": True}])
    matrix = build_matrix(artifact, corpus, method=method)
    return artifact, corpus, matrix


def promote(artifact, corpus, matrix, candidate_id, *, current_rows=None, **overrides):
    return promote_inventory_candidate(artifact, corpus, matrix["inventory_report"], candidate_id=candidate_id,
        assessment_generation=matrix["rows"][0]["assessment_generation"], policy={"schema_version": 1},
        current_rows=matrix["rows"] if current_rows is None else current_rows, **overrides)


def rejected_id(audit):
    return next(entry["candidate_id"] for entry in audit["entries"] if entry["machine_disposition"] == "rejected")


def test_every_candidate_and_structural_segment_accounted_without_quality_claim():
    """what_bug_this_catches: excluded context vanishes or attribution is marketed as semantic recall."""
    artifact, _, matrix = fixture()
    original = deepcopy(matrix["inventory_report"])
    audit = inventory_audit(artifact, original)
    assert audit["counts"]["candidates"] == 2
    assert audit["counts"]["retained"] == audit["counts"]["rejected"] == 1
    assert all(item["candidate_ids"] for item in audit["segments"])
    assert audit["semantic_recall"] == "not_assessed" and audit["all_requirements_found"] is None
    assert matrix["inventory_report"] == original


def test_missing_disposition_is_unattributed_and_duplicate_disposition_rejected():
    """what_bug_this_catches: missing/duplicated machine decisions silently become accounted exclusions."""
    artifact, _, matrix = fixture()
    missing = deepcopy(matrix["inventory_report"])
    missing["rejected_candidates"] = []
    audit = inventory_audit(artifact, missing)
    assert audit["counts"]["unattributed"] == 1 and audit["attribution_complete"] is False
    duplicate = deepcopy(matrix["inventory_report"])
    duplicate["rejected_candidates"].append(deepcopy(duplicate["rejected_candidates"][0]))
    with pytest.raises(M3Error) as error:
        inventory_audit(artifact, duplicate)
    assert error.value.code == "inventory_disposition_conflict"


def test_unavailable_provider_distinct_from_intentional_context_exclusion():
    """what_bug_this_catches: missing model/budget makes excluded background look semantically verified."""
    artifact, _, matrix = fixture(method="F1")
    audit = inventory_audit(artifact, matrix["inventory_report"])
    assert audit["machine_report_complete"] is False and audit["counts"]["provider_unavailable"] == 2
    assert all(entry["disposition"] == "uncertain" for entry in audit["entries"])
    assert all(entry["provider_status"]["unavailable"] is True for entry in audit["entries"])
    assert audit["semantic_recall"] == "not_assessed"


def test_sparse_table_input_remains_unanchored_gap_and_not_a_promotion_choice():
    """what_bug_this_catches: parser/table uncertainty creates fabricated source pointers or complete inventory."""
    artifact, _, _ = fixture("Tiny")
    artifact["pages"][0]["tables"] = [[["Unknown cell"]]]
    artifact["pages"][0]["issues"].append("table_reading_order_requires_inspection")
    artifact.pop("artifact_sha")
    artifact["complete"] = False
    artifact["artifact_sha"] = digest("m3.document-artifact.v1", artifact)
    audit = inventory_audit(artifact, inventory_report(artifact))
    assert audit["coverage_complete"] is False and audit["attribution_complete"] is False
    assert audit["structural_gaps"][0]["anchor"] is None and audit["structural_gaps"][0]["promotable"] is False


def test_repeated_unicode_promotion_keeps_second_exact_original_pointer():
    """what_bug_this_catches: first-match or normalized offsets promote the wrong repeated sharp-s clause."""
    text = "Straße background for discussion.\n\nStraße background for discussion.\n\nThe vendor must provide encrypted backup."
    artifact, corpus, matrix = fixture(text)
    audit = inventory_audit(artifact, matrix["inventory_report"])
    candidates = [entry for entry in audit["entries"] if entry["machine_disposition"] == "rejected"]
    second = candidates[1]
    row = promote(artifact, corpus, matrix, second["candidate_id"], interpretation="Human interpretation awaiting assessment",
                  mandatory=False, conditional=True, administrative=False)
    pointer = row["requirement"]["anchors"][0]
    assert pointer["start"] == second["anchor"]["start"] and pointer["start"] != candidates[0]["anchor"]["start"]
    assert pointer["quote"] == "Straße background for discussion."
    assert row["requirement"]["mandatory"] is False and row["requirement"]["conditional"] is True
    assert row["requirement"]["administrative"] is False and row["requirement"]["applicability"] == "unknown"


def test_stale_report_unknown_candidate_retained_or_duplicate_atom_fail_closed():
    """what_bug_this_catches: old/unknown candidates or repeated promotions insert duplicate requirement atoms."""
    artifact, corpus, matrix = fixture()
    audit = inventory_audit(artifact, matrix["inventory_report"])
    candidate_id = rejected_id(audit)
    other, _, _ = fixture("Changed source background.\n\nThe vendor must provide encrypted backup.")
    with pytest.raises(M3Error) as error:
        inventory_audit(other, matrix["inventory_report"])
    assert error.value.code == "stale_inventory_report"
    with pytest.raises(M3Error):
        promote(artifact, corpus, matrix, "unknown-candidate")
    retained = next(item["candidate_id"] for item in audit["entries"] if item["requirement_ids"])
    with pytest.raises(M3Error):
        promote(artifact, corpus, matrix, retained)
    first = promote(artifact, corpus, matrix, candidate_id)
    first["retired"] = True
    with pytest.raises(M3Error) as error:
        promote(artifact, corpus, matrix, candidate_id, current_rows=[*matrix["rows"], first])
    assert error.value.code == "duplicate_inventory_promotion"


def test_promotion_is_pending_no_model_dispatch_and_human_tags_survive_reassessment(monkeypatch):
    """what_bug_this_catches: correction calls a model, approves itself or enters blind evaluation after corpus update."""
    artifact, corpus, matrix = fixture()
    candidate_id = rejected_id(inventory_audit(artifact, matrix["inventory_report"]))
    original_report = deepcopy(matrix["inventory_report"])

    def forbidden_assessment(*args, **kwargs):
        raise AssertionError("Promotion must not dispatch evidence assessment")

    with monkeypatch.context() as guard:
        guard.setattr("rfp_matrix.evidence.engine.assess_requirement", forbidden_assessment)
        # Promotion never reaches assess_requirement; guard the actual judgment dispatch seam too.
        guard.setattr("rfp_matrix.evidence.engine.call_judgment", forbidden_assessment)
        guard.setattr("rfp_matrix.evidence.consumer.call_judgment", forbidden_assessment)
        row = promote(artifact, corpus, matrix, candidate_id)
    assert row["approval"] == "pending" and row["decision_event_id"] is None
    assert row["assessment"]["status"] == "needs_review" and row["assessment"]["complete"] is False
    assert row["assessment"]["support"] == [] and row["assessment"]["usage"] is None
    assert row["human_origin"]["blind_method_prediction"] is False and original_report == matrix["inventory_report"]
    new_corpus = make_corpus([{"record_id": "updated", "text": "Fictional updated evidence.", "terms": "Authored fictional", "fictional": True}])
    refreshed = reassess_rows(artifact, new_corpus, [row])
    assert refreshed[0]["human_origin"] == row["human_origin"] and refreshed[0]["approval"] == "pending"


def test_inventory_view_keeps_machine_audit_separate_from_human_pending_overlay():
    """what_bug_this_catches: human correction overwrites original rejection or enables actions without versions."""
    artifact, corpus, matrix = fixture()
    audit = inventory_audit(artifact, matrix["inventory_report"])
    candidate_id = rejected_id(audit)
    row = promote(artifact, corpus, matrix, candidate_id)
    snapshot = {"run_id": "fixture", "artifact": artifact, "rows": [*matrix["rows"], row],
        "assessment_generation": row["assessment_generation"], "matrix_generation": "a" * 64}
    original = deepcopy(audit)
    view = inventory_audit_view({"audit": audit, "snapshot": snapshot})
    entry = next(item for item in view["entries"] if item["candidate_id"] == candidate_id)
    assert entry["disposition"] == "rejected" and entry["already_promoted"] is True and entry["eligible"] is False
    assert audit == original and view["all_requirements_found"] is None
    assert inventory_audit_view(audit)["controls"]["enabled"] is False


def test_audit_and_promotion_caps_fail_visibly_without_partial_rows():
    """what_bug_this_catches: structural/candidate caps silently truncate audit or exceed the current atom limit."""
    artifact, corpus, matrix = fixture()
    candidate_id = rejected_id(inventory_audit(artifact, matrix["inventory_report"]))
    with pytest.raises(M3Error) as error:
        promote(artifact, corpus, matrix, candidate_id, current_rows=[matrix["rows"][0]] * 2000)
    assert error.value.code == "requirement_limit"
    oversized = parse_text_pages(["\n\n".join(["Context background."] * 2001)], source_id="oversized-authored", terms="Authored development only")
    report = {"schema_version": 1, "artifact_sha": oversized["artifact_sha"], "policy_hash": "b" * 64,
              "method": "B0a", "requirements": [], "rejected_candidates": [], "judgments": [], "complete": False}
    with pytest.raises(M3Error) as error:
        inventory_audit(oversized, report)
    assert error.value.code == "requirement_limit"
