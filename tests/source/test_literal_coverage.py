"""Development-only authored text; SOURCE ONLY, UNRUN in Dirty Mode.

These examples confer no public-PDF evaluation or measured parser-quality claim.
"""
from copy import deepcopy

import pytest

from rfp_matrix.contracts import M3Error, NORMALIZATION_POLICY, digest
from rfp_matrix.source import (
    anchor_from_normalized, artifact_notices, effective_coverage, make_anchor, make_coverage_decision,
    normalize_with_alignment, parse_text_pages, resolve_anchor, resolve_context, same_effective_coverage,
    structural_segments,
)


def artifact(*pages):
    return parse_text_pages(pages, source_id="authored-development", terms="Project-authored fictional text; development only")


def rehash(value):
    value = deepcopy(value)
    value.pop("artifact_sha", None)
    value["complete"] = all(page["coverage"] == "ok" and not page["issues"] for page in value["pages"])
    value["artifact_sha"] = digest("m3.document-artifact.v1", value)
    return value


def inspection(value, *, page=0, action="accept_blank", request="inspect-1", previous=None, inspected=True):
    return make_coverage_decision(value, page_index=page, decision=action,
        reason="Reviewer inspected the original authored page", actor="application-resolved-reviewer",
        request_id=request, expected_revision=previous["revision"] if previous else None,
        previous=previous, inspected_original=inspected)


def test_synthetic_origin_is_a_notice_not_a_blocking_artifact_issue():
    """what_bug_this_catches: the synthetic provenance label blocks coverage/approval on every authored fixture."""
    value = artifact("The vendor shall log events.")
    assert value["issues"] == [] and value["complete"] is True
    assert value["source"]["media_type"] == "application/x-m3-synthetic-pages" and value["parser"] == "synthetic-text-pages"
    summary = effective_coverage(value)
    assert summary["complete"] is True and summary["artifact_issues"] == []
    assert summary["artifact_notices"] == artifact_notices(value) and len(summary["artifact_notices"]) == 1
    assert artifact_notices({"source": {"media_type": "application/pdf"}, "parser": "pdfplumber-native"}) == []
    legacy = {key: item for key, item in summary.items() if key != "artifact_notices"}
    assert same_effective_coverage(summary, legacy) and same_effective_coverage(summary, summary)
    assert not same_effective_coverage(summary, {**legacy, "complete": False})
    assert not same_effective_coverage(summary, {**summary, "artifact_notices": []})


def test_repeated_quote_keeps_second_original_coordinates():
    """what_bug_this_catches: first-substring resolution silently cites a different occurrence."""
    value = artifact("The vendor shall log events. The vendor shall log events.")
    text = value["pages"][0]["text"]
    start = text.rindex("The vendor")
    pointer = make_anchor(value, 0, start, len(text))
    resolved = resolve_anchor(value, pointer)
    assert resolved["start"] == start and resolved["quote"] == text[start:]


def test_unicode_equivalence_and_whole_expansion_roundtrip():
    """what_bug_this_catches: decomposition/casefold changes original quote coordinates."""
    value = artifact("Straße Cafe\u0301 and Café must be preserved as original text.")
    page = value["pages"][0]
    pointer = anchor_from_normalized(value, 0, 0, len("strasse"))
    assert pointer["quote"] == "Straße"
    policy = value["normalization_policy"]
    assert normalize_with_alignment("Cafe\u0301", policy=policy)[0] == normalize_with_alignment("Café", policy=policy)[0]
    assert len(page["alignment"]) == len(page["normalized_text"])


def test_partial_unicode_expansion_cannot_be_precise_anchor():
    """what_bug_this_catches: one generated s incorrectly becomes an original sharp-s quote."""
    value = artifact("ß")
    with pytest.raises(M3Error, match="Unicode expansion"):
        anchor_from_normalized(value, 0, 0, 1)


def test_cross_page_context_does_not_expand_evidence():
    """what_bug_this_catches: neighboring page context is silently promoted into cited evidence."""
    value = artifact("The vendor shall support", "encrypted backup for all customer records.")
    pointers = [make_anchor(value, index, 0, len(page["text"])) for index, page in enumerate(value["pages"])]
    result = resolve_context(value, pointers, radius=8)
    assert result["cross_page"] is True
    assert all(item["context_is_evidence"] is False for item in result["anchors"])
    assert [item["quote"] for item in result["anchors"]] == [page["text"] for page in value["pages"]]


def test_legacy_alignment_and_parser_change_keep_stale_anchor_boundary():
    """what_bug_this_catches: policy upgrades reinterpret historical offsets or accept stale parsers."""
    value = artifact("The vendor must retain Straße audit records.")
    value["normalization_policy"] = NORMALIZATION_POLICY
    for page in value["pages"]:
        page["normalized_text"], page["alignment"] = normalize_with_alignment(page["text"])
    value = rehash(value)
    pointer = make_anchor(value, 0, 0, len(value["pages"][0]["text"]))
    assert resolve_anchor(value, pointer)["quote"] == pointer["quote"]
    changed = deepcopy(value)
    changed["parser_version"] = "new-extraction-version"
    changed = rehash(changed)
    with pytest.raises(M3Error) as error:
        resolve_anchor(changed, pointer)
    assert error.value.code == "stale_anchor"


def test_blank_acknowledgement_is_separate_and_requires_inspection():
    """what_bug_this_catches: empty pages become complete from length or mutate immutable artifacts."""
    value = artifact("")
    original = deepcopy(value)
    with pytest.raises(M3Error):
        inspection(value, inspected=False)
    event = inspection(value)
    assert effective_coverage(value, [event])["complete"] is True
    assert value == original and value["complete"] is False


def test_image_only_page_cannot_be_acknowledged_as_blank():
    """what_bug_this_catches: human blank overrides certify scans without OCR/native extraction."""
    value = artifact("")
    value["pages"][0].update(coverage="unsupported", issues=["image_content_requires_inspection", "no_native_text"])
    value = rehash(value)
    with pytest.raises(M3Error) as error:
        inspection(value)
    assert error.value.code == "coverage_not_clearable"


def test_sparse_table_cells_keep_unanchored_structure_until_explicit_inspection():
    """what_bug_this_catches: repeated table cells get guessed offsets or sparse text auto-clears."""
    value = artifact("Yes Yes")
    value["pages"][0]["tables"] = [[["Yes", "Yes"]]]
    value["pages"][0]["issues"].append("table_reading_order_requires_inspection")
    value = rehash(value)
    view = structural_segments(value)
    assert view["complete"] is False
    assert len(view["table_cells"]) == 2 and all(cell["anchor"] is None for cell in view["table_cells"])
    assert effective_coverage(value)["unresolved_pages"] == [0]
    event = inspection(value, action="accept_text")
    assert effective_coverage(value, [event])["complete"] is True
    assert structural_segments(value)["complete"] is False


def test_coverage_cas_history_request_identity_and_wrong_artifact_fail_closed():
    """what_bug_this_catches: reordered/forked or rebound inspection events clear unresolved pages."""
    value = artifact("")
    first = inspection(value)
    second = inspection(value, action="flag_unresolved", previous=first, request="inspect-2")
    assert effective_coverage(value, [first, first])["complete"] is True
    assert effective_coverage(value, [first, second])["complete"] is False
    with pytest.raises(M3Error):
        effective_coverage(value, [second, first])
    with pytest.raises(M3Error):
        effective_coverage(artifact("different"), [first])
    changed_request = inspection(value, action="flag_unresolved", request="inspect-1")
    with pytest.raises(M3Error) as error:
        effective_coverage(value, [first, changed_request])
    assert error.value.code == "coverage_idempotency_conflict"
