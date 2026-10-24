"""UNRUN nominal-role/receipt regressions; synthetic identity is not execution proof."""
from copy import deepcopy
import json

import pytest

from rfp_matrix.contracts import digest
from rfp_matrix.eval.pipeline import PipelineMethodPort, RecordingPort
from rfp_matrix.eval.runner import _is_review_identity, freeze_comparison, run_comparison
from rfp_matrix.providers import ReviewPolicyIdentity, review_policy_identity
from test_build_provenance_source import descriptor
from test_custody_source import CustodianFake, MethodFake, setup_comparison


def identity_handle(status="available"):
    """Authored metadata only. No installed SDK, source collection or model call."""
    if status == "unavailable":
        return review_policy_identity({}, semantic_provenance=None)
    raw = deepcopy(descriptor("F1", restart=status == "restart_required"))
    judge = raw["identity"]["components"]["judge"]
    judge["dependencies"]["typesafe-sdk"] = {"status": "available", "version": "0.7.5", "reason": None}
    judge["fingerprint"] = digest("m3.source-component.v1", {k: v for k, v in judge.items() if k != "fingerprint"})
    raw["fingerprint"] = digest("m3.fingerprint-descriptor.v1", {"profile": raw["profile"], "identity": raw["identity"]})
    raw["descriptor_sha256"] = digest("m3.fingerprint-descriptor-record.v1", {k: v for k, v in raw.items() if k != "descriptor_sha256"})
    config = {"method": "F1", "document_id": "public-doc", "source_sha256": "a" * 64,
        "corpus_sha256": "b" * 64, "semantic_fingerprint": raw["fingerprint"], "enabled": False, "budget_usd": "0"}
    return review_policy_identity(config, semantic_provenance=raw)


@pytest.mark.parametrize("status", ["available", "unavailable", "restart_required"])
def test_review_handle_rejected_before_external_seal_consumption(status):
    """what_bug_this_catches: policy-only metadata consumes a seal as a prediction arm."""
    public, frozen = setup_comparison()
    handle, custodian = identity_handle(status), CustodianFake(frozen)
    assert handle.status == status
    with pytest.raises(ValueError, match="read-only policy identity"):
        run_comparison(frozen, (public,), ports={"F1": handle}, custodian=custodian, seal=b"external-test-seal")
    assert custodian.spent is False and custodian.scored == []


@pytest.mark.parametrize("status", ["available", "unavailable", "restart_required"])
def test_review_handle_rejected_by_judgment_recording_and_factory_constructor(status):
    """what_bug_this_catches: disabled identity is adapted into an evidence request delegate."""
    handle = identity_handle(status)
    with pytest.raises(ValueError, match="read-only policy identity"):
        RecordingPort(handle)
    with pytest.raises(ValueError, match="Read-only review identity"):
        PipelineMethodPort("F1", (), factory=handle)


def test_nominal_guard_never_reads_arbitrary_purpose_marker_or_config():
    """what_bug_this_catches: duck properties are executed/trusted while inspecting role."""
    class MarkerTrap:
        @property
        def read_only_policy(self):
            raise AssertionError("arbitrary marker executed")
        @property
        def purpose(self):
            raise AssertionError("arbitrary purpose executed")
        @property
        def config(self):
            raise AssertionError("arbitrary config executed")
    assert _is_review_identity(MarkerTrap()) is False
    assert _is_review_identity(identity_handle()) is True


def test_nominal_role_rejected_even_if_successful_methods_are_attached(monkeypatch):
    """what_bug_this_catches: interface shape defeats explicit read-only role restriction."""
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("identity was executed")
    monkeypatch.setattr(ReviewPolicyIdentity, "judge", forbidden, raising=False)
    monkeypatch.setattr(ReviewPolicyIdentity, "predict", forbidden, raising=False)
    handle = identity_handle()
    public, frozen = setup_comparison()
    custodian = CustodianFake(frozen)
    with pytest.raises(ValueError):
        RecordingPort(handle)
    with pytest.raises(ValueError):
        run_comparison(frozen, (public,), ports={"F1": handle}, custodian=custodian, seal=b"external-test-seal")
    assert not called and not custodian.spent


def test_review_projection_cannot_be_added_as_frozen_method_provenance():
    """what_bug_this_catches: prospective review identity is silently accepted as execution proof."""
    public, original = setup_comparison()
    manifest = json.loads(original.canonical_manifest)
    methods = deepcopy(manifest["methods"])
    methods["F1"]["review_policy_identity"] = dict(identity_handle().identity)
    with pytest.raises(ValueError):
        freeze_comparison((public,), split=manifest["split"], pins=manifest["pins"],
            method_configs=methods, dataset_counts=manifest["dataset_counts"])


def test_review_identity_payload_cannot_populate_prediction_metrics():
    """what_bug_this_catches: a policy receipt is smuggled into a measured prediction wire."""
    public, frozen = setup_comparison()
    handle, custodian = identity_handle(), CustodianFake(frozen)
    class WrongReceipt(MethodFake):
        def predict(self, **kwargs):
            result = super().predict(**kwargs)
            result["review_policy_identity"] = dict(handle.identity)
            result["review_policy_identity"]["PRIVATE_SENTINEL"] = "never-public"
            return result
    receipt = run_comparison(frozen, (public,), ports={"F1": WrongReceipt()}, custodian=custodian,
        seal=b"external-test-seal")
    assert receipt["methods"]["F1"]["status"] == "failed_unmeasured"
    assert receipt["methods"]["F1"]["metrics"] is None and custodian.scored == []
    assert "PRIVATE_SENTINEL" not in json.dumps(receipt) and "never-public" not in json.dumps(receipt)
    assert receipt["public_quality_claim"] is False and receipt["runtime_verified"] is False
