"""UNRUN synthetic integrity contracts. No actual source/custody/model attestation.

Descriptors (conftest eval_kit) are authored in memory from closed native schemas. Their
fake versions/file hashes never mean installed dependencies or execution evidence.
"""
import json

import pytest

from rfp_matrix.contracts import digest
from rfp_matrix.eval.build_provenance import (
    NativeBuildObserver, collect_build_provenance, compare_build_provenance,
    validate_build_provenance,
)
from rfp_matrix.eval.runner import fingerprint, freeze_comparison, run_comparison
from rfp_matrix.provenance import verify_fingerprint_descriptor


def context(kit, method, *, marker="synthetic", restart=False):
    raw = kit.descriptor(method, marker=marker, restart=restart)
    value = {"schema_version": 1, "profile": "m3-semantic-context-v1", "method": method,
        "parser_profile": None, "status": "restart_required" if restart else "current",
        "fingerprint": raw["fingerprint"], "descriptor": raw, "reasons": raw["reasons"],
        "execution_verified": False}
    return {**value, "context_sha": digest("m3.semantic-context.v1", value)}


def build(kit, *, changed_method=None, render_marker="synthetic", restart_method=None):
    return collect_build_provenance(
        semantic_collector=lambda method, **kwargs: context(kit, method,
            marker="changed" if method == changed_method else "synthetic", restart=method == restart_method),
        render_collector=lambda: kit.descriptor(marker=render_marker),
        descriptor_verifier=verify_fingerprint_descriptor)


def frozen_with_build(public, original, value):
    manifest = json.loads(original.canonical_manifest)
    return freeze_comparison((public,), split=manifest["split"], pins=manifest["pins"],
        method_configs=manifest["methods"], dataset_counts=manifest["dataset_counts"],
        build_provenance=value)


class Observer:
    def __init__(self, *values):
        self.values = list(values)
    def observe(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_full_context_and_missing_sdk_preserved_without_execution_claim(eval_kit):
    """what_bug_this_catches: descriptor digest alone upgrades absent SDK to verified."""
    value = build(eval_kit)
    assert validate_build_provenance(value) == value
    member = value["semantic"]["F1"]
    assert member["semantic_context"]["descriptor"] == member["descriptor"]
    assert member["dependencies"]["typesafe-sdk"] == {"status": "unavailable", "version": None}
    assert member["descriptor"]["descriptor_sha256"] != member["fingerprint"]
    assert value["execution_verified"] is False and value["authenticity"] == "not_attested"


def test_observer_constructor_never_collects(monkeypatch):
    """what_bug_this_catches: creating a frozen observer reads source/dependencies."""
    import rfp_matrix.eval.build_provenance as module
    calls = []
    monkeypatch.setattr(module, "collect_build_provenance", lambda **kwargs: calls.append(kwargs))
    observer = NativeBuildObserver()
    assert calls == []
    observer.observe()
    assert calls == [{"parser_profiles": {}}]


@pytest.mark.parametrize("mutation", ["outer", "context", "summary", "private"])
def test_rehashed_wrapper_cannot_hide_invalid_native_metadata(eval_kit, mutation):
    """what_bug_this_catches: public wrapper hash launders invalid/extra native metadata."""
    value = build(eval_kit)
    member = value["semantic"]["F1"]
    if mutation == "outer":
        member["descriptor"]["descriptor_sha256"] = "0" * 64
        member["descriptor_sha"] = fingerprint(member["descriptor"])
    elif mutation == "context":
        member["semantic_context"]["parser_profile"] = {"parser": "changed"}
    elif mutation == "summary":
        member["dependencies"]["typesafe-sdk"] = {"status": "available", "version": "fabricated"}
    else:
        member["gold_labels"] = ["PRIVATE_SENTINEL"]
    value["build_sha"] = fingerprint({k: v for k, v in value.items() if k != "build_sha"})
    with pytest.raises(ValueError):
        validate_build_provenance(value)


def test_stable_identity_is_separate_from_restart_record_integrity(eval_kit):
    """what_bug_this_catches: restart status is hashed into semantic identity or ignored."""
    current, restart = eval_kit.descriptor("B0a"), eval_kit.descriptor("B0a", restart=True)
    assert current["fingerprint"] == restart["fingerprint"]
    assert current["descriptor_sha256"] != restart["descriptor_sha256"]
    status = compare_build_provenance(build(eval_kit), build(eval_kit, restart_method="B0a"), active_methods=("B0a",))
    assert status["semantic"]["B0a"] == "restart_required" and not status["allows_sealed_run"]


def test_render_changes_separate_from_semantics_and_inactive_arm(eval_kit):
    """what_bug_this_catches: UI drift overwrites semantic status or inactive SDK blocks baseline."""
    old = build(eval_kit)
    changed = compare_build_provenance(old, build(eval_kit, render_marker="CSS-change"), active_methods=("B0a",))
    assert changed["semantic"] == {"B0a": "current_integrity_only"}
    assert changed["render"] == "mismatch" and not changed["allows_sealed_run"]
    inactive = compare_build_provenance(old, build(eval_kit, changed_method="F1"), active_methods=("B0a",))
    assert inactive["allows_sealed_run"] is True


def test_changed_pin_denied_before_external_consumption_and_prediction(eval_kit):
    """what_bug_this_catches: changed code reaches method/custodian using old freeze."""
    public, original = eval_kit.setup_comparison()
    frozen = frozen_with_build(public, original, build(eval_kit))
    custodian, port = eval_kit.CustodianFake(frozen), eval_kit.MethodFake()
    with pytest.raises(ValueError, match="provenance changed"):
        run_comparison(frozen, (public,), ports={"B0a": port}, custodian=custodian,
            seal=b"external-test-seal", build_observer=Observer(build(eval_kit, changed_method="B0a")))
    assert not custodian.spent and port.seen == []


def test_post_prediction_drift_keeps_seal_spent_and_never_scores(eval_kit):
    """what_bug_this_catches: mid-run drift releases scores or returns consumed seal to pool."""
    public, original = eval_kit.setup_comparison()
    old = build(eval_kit)
    frozen = frozen_with_build(public, original, old)
    custodian, port = eval_kit.CustodianFake(frozen), eval_kit.MethodFake()
    with pytest.raises(ValueError, match="provenance changed"):
        run_comparison(frozen, (public,), ports={"B0a": port}, custodian=custodian,
            seal=b"external-test-seal", build_observer=Observer(old, old, build(eval_kit, changed_method="B0a")))
    assert custodian.spent and len(port.seen) == 1 and custodian.scored == []


def test_uncollected_build_is_never_a_current_integrity_claim(eval_kit):
    """what_bug_this_catches: absent descriptor is interpreted as current verified code."""
    missing = validate_build_provenance(None)
    status = compare_build_provenance(missing, None, active_methods=("B0a",))
    assert status["semantic"] == {"B0a": "legacy_unverified"}
    assert status["all_active_integrity_current"] is False
    current_missing = compare_build_provenance(build(eval_kit), None, active_methods=("B0a",))
    assert current_missing["allows_sealed_run"] is False


def test_native_descriptor_without_outer_integrity_is_not_collected_proof(eval_kit):
    """what_bug_this_catches: pre-final descriptor shape silently gains current status."""
    raw = context(eval_kit, "B0a")
    del raw["descriptor"]["descriptor_sha256"]
    value = collect_build_provenance(semantic_collector=lambda *args, **kwargs: raw,
        render_collector=lambda: eval_kit.descriptor(), descriptor_verifier=verify_fingerprint_descriptor)
    assert all(member["status"] == "unavailable" for member in value["semantic"].values())


def test_frozen_native_context_requires_actual_prediction_trace_binding(eval_kit):
    """what_bug_this_catches: a current observer masks predictions with no semantic trace."""
    public, original = eval_kit.setup_comparison()
    value = build(eval_kit)
    frozen = frozen_with_build(public, original, value)
    custodian = eval_kit.CustodianFake(frozen)
    receipt = run_comparison(frozen, (public,), ports={"B0a": eval_kit.MethodFake()}, custodian=custodian,
        seal=b"external-test-seal", build_observer=Observer(value))
    assert receipt["methods"]["B0a"]["status"] == "failed_unmeasured"
    assert receipt["methods"]["B0a"]["metrics"] is None and custodian.scored == []
    assert receipt["receipt_sha"] == fingerprint({k: v for k, v in receipt.items() if k != "receipt_sha"})
    assert receipt["runtime_verified"] is False and receipt["public_quality_claim"] is False
