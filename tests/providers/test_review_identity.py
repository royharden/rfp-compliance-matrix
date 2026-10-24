"""SOURCE ONLY, UNRUN. No inference, actual collector or real admission fixtures."""
from dataclasses import FrozenInstanceError, replace

import pytest

from rfp_matrix import provenance as p
from rfp_matrix.contracts import digest, M3Error
from rfp_matrix.providers import (ReviewPolicyIdentity, review_policy_identity,
    is_review_policy_identity, policy_identity_projection, ProviderConfig, REVIEW_POLICY_PURPOSE)


def current_descriptor(collection, method="F1", *, sdk=True):
    if sdk:
        component = collection["identity"]["components"]["judge"]
        component["dependencies"]["typesafe-sdk"] = {
            "status": "available", "version": "0.7.5", "reason": None}
        body = {key: value for key, value in component.items() if key != "fingerprint"}
        component["fingerprint"] = digest("m3.source-component.v1", body)
    return p.semantic_policy_fingerprint(method)


def read_only_config(policy, descriptor, method="F1"):
    config = policy(method, semantic_fingerprint=descriptor["fingerprint"],
        sdk_version=descriptor["identity"]["runtime"]["python_version"] if method == "F2" else "0.7.5")
    return replace(config, enabled=False, budget_usd="0", admission_reference="", approved_config_sha256="")


@pytest.mark.parametrize("method", ["F1", "B0b", "F2"])
def test_read_only_handle_accepts_matching_disabled_identity_without_io(collection, policy, monkeypatch, method):
    """what_bug_this_catches: reviewing existing evidence requires live transport or admission."""
    descriptor = current_descriptor(collection, method, sdk=method != "F2")
    config = read_only_config(policy, descriptor, method)
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only identity factory must be pure")
    monkeypatch.setattr(p, "_collect", forbidden)
    monkeypatch.setattr(p.importlib.metadata, "version", forbidden)
    monkeypatch.setattr(p.os.environ, "get", forbidden)
    result = review_policy_identity(config, semantic_provenance=descriptor)
    assert result.status == "available" and result.reason is None
    assert result.config is config and result.config.enabled is False
    assert result.config.admission_reason() == "disabled_zero_budget"
    assert result.read_only_policy is True
    assert result.purpose == REVIEW_POLICY_PURPOSE == "review-policy-identity-only-v1"
    assert is_review_policy_identity(result)
    assert result.execution_verified is result.live_authorization_verified is False
    assert not hasattr(result, "judge") and not hasattr(result, "dispatch")
    assert not hasattr(result, "transport") and not hasattr(result, "ledger")


def test_projection_is_closed_read_only_and_descriptor_is_defensive(collection, policy):
    """what_bug_this_catches: caller mutation or config authority fields leak into review identity."""
    descriptor = current_descriptor(collection)
    result = review_policy_identity(read_only_config(policy, descriptor), semantic_provenance=descriptor)
    assert set(result.identity) == {"method", "model", "sdk_name", "sdk_version", "question_policy",
        "source_policy", "document_id", "source_sha256", "corpus_sha256", "semantic_fingerprint"}
    with pytest.raises(TypeError):
        result.identity["method"] = "F2"
    with pytest.raises(FrozenInstanceError):
        result.status = "available"
    descriptor["status"] = "unavailable"
    returned = result.semantic_provenance
    returned["identity"]["method"] = "F2"
    assert result.semantic_provenance["status"] == "available"
    assert result.semantic_provenance["identity"]["method"] == "F1"
    with pytest.raises(TypeError):
        ReviewPolicyIdentity()


@pytest.mark.parametrize("change", [{"enabled": True}, {"budget_usd": "0.1"},
    {"admission_reference": "synthetic-not-an-approval"}, {"approved_config_sha256": "f" * 64}])
def test_live_authority_fields_cannot_escalate_read_only_handle(collection, policy, change):
    """what_bug_this_catches: read-only path accepts a live-enabling configuration or consumes approval."""
    descriptor = current_descriptor(collection)
    config = replace(read_only_config(policy, descriptor), **change)
    result = review_policy_identity(config, semantic_provenance=descriptor)
    assert result.status == "unavailable" and result.reason == "review_policy_live_authority_forbidden"
    assert config.enabled == change.get("enabled", False)  # supplied policy is never rewritten/enabled


@pytest.mark.parametrize("change,reason", [({"semantic_fingerprint": ""}, "review_policy_semantic_binding_missing"),
    ({"semantic_fingerprint": "0" * 64}, "review_policy_semantic_mismatch"),
    ({"document_id": ""}, "review_policy_source_corpus_binding_missing"),
    ({"source_sha256": ""}, "review_policy_source_corpus_binding_missing"),
    ({"corpus_sha256": ""}, "review_policy_source_corpus_binding_missing"),
    ({"question_policy": "unknown"}, "review_policy_fixed_policy_mismatch"),
    ({"source_policy": "unknown"}, "review_policy_fixed_policy_mismatch")])
def test_missing_legacy_and_fixed_policy_mismatch_fail_closed(collection, policy, change, reason):
    """what_bug_this_catches: missing bindings or caller-defined policies become trusted current identity."""
    descriptor = current_descriptor(collection)
    result = review_policy_identity(replace(read_only_config(policy, descriptor), **change), semantic_provenance=descriptor)
    assert result.status == "unavailable" and result.reason == reason


def test_wrong_method_and_sdk_metadata_not_current(collection, policy):
    """what_bug_this_catches: F1 descriptors authorize F2 policy or missing SDK is guessed installed."""
    descriptor = current_descriptor(collection, sdk=False)
    config = read_only_config(policy, descriptor)
    assert review_policy_identity(config, semantic_provenance=descriptor).reason == "review_policy_sdk_metadata_missing_or_mismatch"
    other = read_only_config(policy, descriptor, "F2")
    assert review_policy_identity(other, semantic_provenance=descriptor).reason == "review_policy_method_mismatch"


def test_f2_python_metadata_mismatch_and_no_probability_claim(collection, policy):
    """what_bug_this_catches: wrong stdlib version is admitted or F2 labels acquire probabilities."""
    descriptor = current_descriptor(collection, "F2", sdk=False)
    config = read_only_config(policy, descriptor, "F2")
    result = review_policy_identity(replace(config, sdk_version="0.0.0"), semantic_provenance=descriptor)
    assert result.reason == "review_policy_sdk_metadata_missing_or_mismatch"
    good = review_policy_identity(config, semantic_provenance=descriptor)
    assert good.status == "available"
    assert not hasattr(good, "answers") and not hasattr(good, "raw_classifications")


@pytest.mark.parametrize("descriptor", [None, {}, {"status": "current", "fingerprint": "a" * 64}])
def test_legacy_captured_or_missing_descriptor_is_not_current(collection, policy, descriptor):
    """what_bug_this_catches: a recorded hash/status string substitutes for actual current descriptor."""
    actual = current_descriptor(collection)
    config = read_only_config(policy, actual)
    result = review_policy_identity(config, semantic_provenance=descriptor)
    assert result.reason == "review_policy_descriptor_missing_or_invalid"


def test_restart_descriptor_retains_restart_guard(collection, policy):
    """what_bug_this_catches: full valid integrity record bypasses process-restart currentness."""
    first = current_descriptor(collection)
    config = read_only_config(policy, first)
    component = collection["identity"]["components"]["validate"]
    component["files"][0]["sha256"] = "c" * 64
    body = {key: value for key, value in component.items() if key != "fingerprint"}
    component["fingerprint"] = digest("m3.source-component.v1", body)
    drift = p.semantic_policy_fingerprint("F1")
    result = review_policy_identity(config, semantic_provenance=drift)
    assert result.status == "restart_required" and result.reason == "review_policy_restart_required"


def test_missing_source_descriptor_is_unavailable(collection, policy, monkeypatch):
    """what_bug_this_catches: unavailable source record is interpreted as an SDK-only review context."""
    first = current_descriptor(collection)
    config = read_only_config(policy, first)
    component = collection["identity"]["components"]["parse"]
    component["files"][0].update(status="unavailable", sha256=None, reason="source_unavailable")
    component.update(status="unavailable", fingerprint=None)
    # Simulate a clean process first seeing missing source, not a drifted process.
    monkeypatch.setattr(p, "_PINS", {})
    monkeypatch.setattr(p, "_RESTART", set())
    missing = p.semantic_policy_fingerprint("F1")
    assert missing["status"] == "unavailable" and missing["fingerprint"] is None
    result = review_policy_identity(config, semantic_provenance=missing)
    assert result.status == "unavailable" and result.reason == "review_policy_source_unavailable"


def test_unknown_secret_fields_do_not_return_raw_config(collection, policy):
    """what_bug_this_catches: arbitrary key-bearing input becomes a review config or leaks in diagnostics."""
    descriptor = current_descriptor(collection)
    config = read_only_config(policy, descriptor)
    result = review_policy_identity({**config.identity(), "api_key": "synthetic-forbidden-field"}, semantic_provenance=descriptor)
    assert result.status == "unavailable" and result.config is None and dict(result.identity) == {}
    assert result.reason == "review_policy_invalid_config"


def test_exact_type_check_never_duck_types_or_runs_properties():
    """what_bug_this_catches: marker/config lookalikes become trusted review handles or predictors."""
    class Untrusted:
        @property
        def purpose(self):
            pytest.fail("Exact-type recognition must not read arbitrary properties")
        @property
        def config(self):
            pytest.fail("Do not coerce config-bearing objects to a JudgmentPort")
    assert not is_review_policy_identity(Untrusted())
    assert not is_review_policy_identity({"purpose": REVIEW_POLICY_PURPOSE, "config": {}})


def test_projection_shared_by_actual_execution_and_disabled_review_without_relabel(collection, policy, monkeypatch):
    """what_bug_this_catches: full admission hash/class makes later review impossible or relabels execution."""
    descriptor = current_descriptor(collection)
    execution = policy(semantic_fingerprint=descriptor["fingerprint"])
    review = read_only_config(policy, descriptor)
    def forbidden(*args, **kwargs):
        pytest.fail("Projection is identity only, never admission or current collection")
    monkeypatch.setattr(ProviderConfig, "admission_reason", forbidden)
    monkeypatch.setattr(p, "_collect", forbidden)
    monkeypatch.setattr(p.importlib.metadata, "version", forbidden)
    monkeypatch.setattr(p.os.environ, "get", forbidden)
    before = execution.identity()
    actual = policy_identity_projection(execution)
    assert actual == policy_identity_projection(review)
    handle = review_policy_identity(review, semantic_provenance=descriptor)
    assert actual == dict(handle.identity)
    assert execution.identity() == before and execution.enabled is True
    assert execution.config_sha256 != review.config_sha256
    assert not {"implementation", "config_sha256", "enabled", "budget_usd", "admission_reference"} & set(actual)


@pytest.mark.parametrize("field", ["source_sha256", "corpus_sha256", "document_id", "semantic_fingerprint", "sdk_version"])
def test_shared_projection_missing_binding_cannot_become_receipt(collection, policy, field):
    """what_bug_this_catches: missing old semantic identity is fabricated as a new complete proof."""
    descriptor = current_descriptor(collection, "F2", sdk=False)
    config = read_only_config(policy, descriptor, "F2")
    with pytest.raises(M3Error, match="Exact fixed policy"):
        policy_identity_projection(replace(config, **{field: ""}))


@pytest.mark.parametrize("change", [{"model": "jev-1.14.0"}, {"source_sha256": "c" * 64},
    {"corpus_sha256": "d" * 64}, {"document_id": "other-run"}, {"semantic_fingerprint": "e" * 64}])
def test_shared_projection_retains_exact_model_run_and_source_changes(collection, policy, change):
    """what_bug_this_catches: removing admission noise also removes genuine semantic/source identity."""
    descriptor = current_descriptor(collection)
    config = read_only_config(policy, descriptor)
    assert policy_identity_projection(config) != policy_identity_projection(replace(config, **change))
