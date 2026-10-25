"""Candidate limits reject oversized public inputs before expensive side effects."""

from copy import deepcopy

import pytest

from rfp_matrix.contracts import M3Error
from rfp_matrix.evidence import engine, inventory_audit
from rfp_matrix.source import make_anchor, parse_text_pages, resolve_anchor


def context_artifact(count):
    return parse_text_pages(["\n\n".join(["Authored context background."] * count)],
                            source_id="authored-preflight", terms="Project-authored public development regression")


@pytest.mark.parametrize("entrypoint", ["registry", "baseline", "typed", "audit"])
def test_overcap_rejects_before_anchor_or_provider_construction(monkeypatch, entrypoint):
    """what_bug_this_catches: quadratic artifact validation/anchor creation or provider access before the 2000-candidate cap."""
    artifact = context_artifact(2001)

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized inventory must reject before constructing anchors or providers")

    monkeypatch.setattr(engine, "make_anchor", forbidden)
    monkeypatch.setattr(engine, "judgment_port", forbidden)
    monkeypatch.setattr(engine, "call_judgment", forbidden)
    with pytest.raises(M3Error) as error:
        if entrypoint == "registry":
            engine.registered_source_candidates(artifact)
        elif entrypoint in {"baseline", "typed"}:
            engine.inventory_report(artifact, method="F1" if entrypoint == "typed" else "B0a")
        else:
            report = {"schema_version": 1, "artifact_sha": artifact["artifact_sha"], "policy_hash": "b" * 64,
                      "method": "B0a", "requirements": [], "rejected_candidates": [], "judgments": [], "complete": False}
            inventory_audit(artifact, report)
    assert error.value.code == "requirement_limit"


def test_exact_cap_remains_accepted_without_silent_truncation():
    """what_bug_this_catches: an off-by-one fix rejecting exactly 2000 candidates or silently dropping the last source span."""
    artifact = context_artifact(2000)
    frozen = engine.registered_source_candidates(artifact)
    assert len(frozen["candidates"]) == 2000
    assert frozen["candidates"][-1]["anchor"]["end"] == len(artifact["pages"][0]["text"])
    report = engine.inventory_report(artifact)
    assert report["requirements"] == [] and len(report["rejected_candidates"]) == 2000


def test_corrupt_artifact_and_public_quote_still_fail_closed():
    """what_bug_this_catches: a trusted internal anchor optimization bypassing validation at public source/quote boundaries."""
    artifact = context_artifact(1)
    corrupted = deepcopy(artifact)
    corrupted["pages"][0]["text"] = "Different unregistered text."
    with pytest.raises(M3Error):
        engine.registered_source_candidates(corrupted)
    anchor = make_anchor(artifact, 0, 0, len(artifact["pages"][0]["text"]))
    forged = {**anchor, "quote": "Forged quotation absent from the source."}
    with pytest.raises(M3Error):
        resolve_anchor(artifact, forged)
