"""Unrun algebraic envelope regressions; hand-authored counts/prices, no live data."""
from dataclasses import replace
from decimal import Decimal
import copy

import pytest

from rfp_matrix.contracts import M3Error, digest
from rfp_matrix.providers import estimate_run_envelope, validate_future_live_envelope, create_provider, CallLedger
import rfp_matrix.providers.live as live


@pytest.fixture(name="policy")
def bound_policy(policy):
    def make(method="F1", **changes):
        return policy(method, **{"semantic_fingerprint": "7" * 64, **changes})
    return make


def counts():
    return {"schema_version": 1, "artifact_sha": "a" * 64, "corpus_generation": "b" * 64,
        "registry_sha256": "c" * 64, "assessment_generation": "d" * 64, "matrix_generation": "e" * 64,
        "inventory_candidates": 3, "requirement_rows": 2, "retrieval_top_k": 2,
        "max_windows_per_candidate": 4, "retrieved_evidence_candidates": 1}


def balance(config, *, batch="0", document="0", origin="live"):
    return {"schema_version": 1, "document_id": config.document_id, "origin": origin,
            "batch_committed_usd": batch, "document_committed_usd": document,
            "ledger_snapshot_sha256": "f" * 64}


def prospective_record(envelope):
    """Fixture claims only; never a real user/custodian/budget approval."""
    identity = envelope["identity"]
    return {"schema_version": 1, "envelope_sha256": envelope["envelope_sha256"],
        **{name: identity[name] for name in ["config_sha256", "source_sha256", "corpus_sha256",
                                           "method", "origin", "registry_counts_sha256"]},
        "approval_reference": "test-only prospective record; not live authority",
        "assumptions_acknowledged": True}


@pytest.mark.parametrize("method,requests,questions", [("B0b", 3, 15), ("F1", 9, 27), ("F2", 9, 27)])
def test_bundle_fanout_and_retry_algebra_are_requests_not_choices(policy, method, requests, questions):
    """what_bug_this_catches: windows/questions multiply calls, or rejected candidates undercount a rerun."""
    config = policy(method)
    result = estimate_run_envelope(config, counts(), budget_context=balance(config))
    bounds = result["bounds"]
    assert bounds["logical_requests"] == requests
    assert bounds["question_judgments_before_retry"] == questions
    assert bounds["transport_attempts_maximum"] == requests * 2
    assert bounds["input_tokens_maximum"] == requests * 2 * 8000
    assert bounds["output_tokens_maximum"] == requests * 2 * 1024
    assert bounds["concurrent_transport_calls_maximum"] == 2
    assert Decimal(bounds["serial_dispatch_deadline_sum_seconds"]) == requests * 60
    if method in {"F1", "F2"}:
        # 3 candidates can become 3 requirements despite the present 2 rows and
        # only 1 current retrieval hit. 3*2=6 evidence calls +3 inventory calls.
        assert bounds["evidence_requests"] == 6
        assert Decimal(result["cost"]["declared_live_cost_upper_usd"]) == Decimal("0.180864")
    assert result["dispatch_performed"] is False and result["runtime_verified"] is False


def test_baseline_no_calls_and_window_truncation_are_explicit(policy):
    """what_bug_this_catches: baseline needs a provider or a 300-window Choice looks complete."""
    config = policy()
    baseline = estimate_run_envelope(config, counts(), method="B0a", budget_context=balance(config))
    assert baseline["bounds"]["logical_requests"] == 0
    assert baseline["cost"]["incremental_cost_upper_usd"] == "0"
    assert baseline["identity"]["model"] is None
    large = {**counts(), "max_windows_per_candidate": 300}
    result = estimate_run_envelope(config, large, budget_context=balance(config))
    assert result["bounds"]["choice_window_options_maximum"] == 255
    assert result["bounds"]["logical_requests"] == 9
    assert "evidence_choice_windows_truncated" in result["conditions"]


def test_unknown_prices_and_replay_incremental_cost_are_never_conflated(policy):
    """what_bug_this_catches: missing price becomes zero or replay hides unknown original cost/coverage."""
    config = policy(input_per_million_usd=None)
    result = estimate_run_envelope(config, counts())
    assert result["cost"]["declared_live_cost_upper_usd"] is None
    assert result["cost"]["incremental_cost_upper_usd"] is None
    assert "price_unknown" in result["conditions"]
    replay = estimate_run_envelope(config, counts(), origin="replay")
    assert replay["cost"]["incremental_cost_upper_usd"] == "0"
    assert replay["cost"]["original_observed_cost_usd"] is None
    assert "replay_coverage_unknown" in replay["conditions"]
    simulated = estimate_run_envelope(config, counts(), origin="mock")
    assert simulated["cost"]["incremental_cost_upper_usd"] == "0"
    assert simulated["cost"]["measured_cost_usd"] is None


def test_remaining_balance_and_whole_run_insufficiency_not_partial_success(policy):
    """what_bug_this_catches: gross budget is mistaken for fresh balance or partial budget funds a whole run."""
    config = policy()
    unknown = estimate_run_envelope(config, counts())
    assert unknown["budget"]["declared_gross_budget_can_cover_run"] is True
    assert unknown["budget"]["whole_run_live_affordable"] is None
    assert "remaining_ledger_budget_unknown" in unknown["conditions"]
    partial = estimate_run_envelope(config, counts(), budget_context=balance(config, batch="9.9", document="0.9"))
    assert Decimal(partial["budget"]["available_full_run_budget_usd"]) == Decimal("0.1")
    assert partial["budget"]["whole_run_live_affordable"] is False
    assert "full_run_budget_insufficient" in partial["conditions"]
    assert partial["budget"]["partial_budget_is_incomplete_evaluation"] is True
    disabled = policy(enabled=False, budget_usd="0")
    assert "live_disabled_zero_budget" in estimate_run_envelope(disabled, counts())["conditions"]


def test_exact_content_digest_record_and_changed_registry_reject_tamper(policy):
    """what_bug_this_catches: lower cost/resealed pins or prior-run approvals enable future live transport."""
    config = policy()
    envelope = estimate_run_envelope(config, counts(), budget_context=balance(config))
    record = prospective_record(envelope)
    assert validate_future_live_envelope(config, envelope, record) is None
    changed = copy.deepcopy(envelope)
    changed["cost"]["declared_live_cost_upper_usd"] = "0"
    assert validate_future_live_envelope(config, changed, record) == "full_run_preflight_mismatch"
    body = {key: value for key, value in changed.items() if key not in {"envelope_sha256", "prospective_approval"}}
    changed["envelope_sha256"] = digest("m3.prospective-run-envelope.v1", body)
    assert validate_future_live_envelope(config, changed, prospective_record(changed)) == "full_run_preflight_mismatch"
    newer = estimate_run_envelope(config, {**counts(), "matrix_generation": "9" * 64}, budget_context=balance(config))
    assert validate_future_live_envelope(config, newer, record) == "full_run_prospective_approval_required"
    assert validate_future_live_envelope(config, envelope, {**record, "assumptions_acknowledged": False}) == "full_run_prospective_approval_required"


@pytest.mark.parametrize("method", ["F1", "F2"])
def test_live_factory_without_preflight_cannot_use_unused_fake_to_bypass_gate(policy, ledger_for, monkeypatch, method):
    """what_bug_this_catches: a fake for the wrong method skips a real transport's full-run guard."""
    config = policy(method)
    def forbidden(*args, **kwargs):
        raise AssertionError("No SDK/key/DB I/O in disabled construction")
    unused = {"client": object()} if method == "F2" else {"http_client": object()}
    with monkeypatch.context() as scoped:
        scoped.setattr(live.importlib.metadata, "version", forbidden)
        scoped.setattr(live.os.environ, "get", forbidden)
        scoped.setattr(CallLedger, "_connect", forbidden)
        port = create_provider(config, ledger=ledger_for(config, origin="live"), **unused)
        result = port.judge({}, {})
    assert result["reason"] == "full_run_preflight_required"


@pytest.mark.parametrize("change", [{"inventory_candidates": True}, {"requirement_rows": 2001},
    {"retrieval_top_k": 11}, {"artifact_sha": "9" * 64}, {"arbitrary_extra": 1}])
def test_registry_count_caps_types_and_source_binding_fail_closed(policy, change):
    """what_bug_this_catches: caller-supplied counters bypass limits or bind a different artifact."""
    with pytest.raises(M3Error):
        estimate_run_envelope(policy(), {**counts(), **change})


def test_estimator_is_pure_without_sdk_key_or_ledger_reads(policy, monkeypatch):
    """what_bug_this_catches: a preview begins provider admission, reads keys or opens accounting DB."""
    config = policy(enabled=False, budget_usd="0")
    def forbidden(*args, **kwargs):
        raise AssertionError("No side effects in pure envelope")
    with monkeypatch.context() as scoped:
        scoped.setattr(CallLedger, "_connect", forbidden)
        scoped.setattr(live.importlib.metadata, "version", forbidden)
        scoped.setattr(live.os.environ, "get", forbidden)
        result = estimate_run_envelope(config, counts())
    assert result["dispatch_performed"] is False
    assert "live_disabled_zero_budget" in result["conditions"]
