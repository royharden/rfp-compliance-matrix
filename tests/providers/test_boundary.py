"""Unrun provider boundary regressions; no observed live output is used."""
import copy
from dataclasses import replace
import json

import pytest

from rfp_matrix.contracts import M3Error
from rfp_matrix.providers import CallLedger, ProviderConfig, build_questions, create_provider, validate_judgment
from rfp_matrix.providers.live import OpenAIClassificationProvider, TypeSafeProvider
import rfp_matrix.providers.live as live_module


def test_construction_and_zero_budget_perform_no_io(monkeypatch, tmp_path):
    """what_bug_this_catches: lazy construction accidentally imports SDK, reads keys or opens DB."""
    def forbidden(*args, **kwargs):
        raise AssertionError("Forbidden I/O on disabled construction")
    with monkeypatch.context() as scoped:
        scoped.setattr(live_module.importlib.metadata, "version", forbidden)
        scoped.setattr(live_module.OpenAIHTTPClient, "post", forbidden)
        scoped.setattr(CallLedger, "_connect", forbidden)
        scoped.setattr(live_module.os.environ, "get", forbidden)
        ledger = CallLedger(tmp_path / "must-not-exist.sqlite")
        port = create_provider(ProviderConfig(), ledger=ledger)
        result = port.judge({"clause": "Public synthetic clause"}, build_questions("inventory"))
        assert result["status"] == "unavailable"
    assert not (tmp_path / "must-not-exist.sqlite").exists()


def test_approval_hash_binds_exact_source_model_price_and_policy(policy):
    """what_bug_this_catches: a previously approved config silently admits changed inputs."""
    config = policy()
    assert config.admission_reason() is None
    for changes in [{"model": "jev-1.14.0"}, {"source_sha256": "c" * 64},
                    {"corpus_sha256": "d" * 64}, {"price_policy": "changed"},
                    {"question_policy": "changed"}, {"sdk_provenance": "changed"}]:
        assert replace(config, **changes).admission_reason() == "configuration_not_approved"
    with pytest.raises(M3Error, match="Unknown"):
        ProviderConfig.from_mapping({"api_key": None})
    with pytest.raises(M3Error, match="exact Jev"):
        replace(config, model="jev-latest").validate()


def test_jev_copies_full_distribution_and_registered_choices(policy, sdk_factory):
    """what_bug_this_catches: adapters discard alternatives or accept a generated span ID."""
    questions = build_questions("evidence", ["window-1"])
    probabilities = {"supports": 0.91, "partial": 0.03, "contradicts": 0.02, "irrelevant": 0.02, "unknown": 0.02}
    raw = {"model": "jev-1.13.0", "usage": {"input_tokens": 12, "output_tokens": 8}, "answers": {
        "relation": {"type": "choice", "choice": "supports", "confidence": 0.92, "probabilities": probabilities},
        "selection": {"type": "choice", "choice": "window-1", "confidence": 0.96,
                      "probabilities": {"window-1": 0.97, "none": 0.03}}}}
    client = sdk_factory([raw])
    result = TypeSafeProvider(policy(), client=client).attempt({}, questions, timeout=10)
    assert result["status"] == "ok"
    assert result["answers"]["relation"]["probabilities"] == probabilities
    assert result["origin"] == "mock" and result["model"] is None
    assert client.calls[0]["retry"] == {"max_retries": 0}
    tampered = copy.deepcopy(result)
    tampered["answers"]["selection"]["choice"] = "invented-window"
    with pytest.raises(M3Error, match="Unregistered"):
        validate_judgment(tampered, questions)


@pytest.mark.parametrize("probability", [True, float("nan"), -0.1, 1.1])
def test_invalid_noul_never_becomes_probability(probability):
    """what_bug_this_catches: bool/nonfinite/out-of-range values cross the closed probability wire."""
    with pytest.raises(M3Error):
        validate_judgment({"status": "ok", "origin": "mock", "answers": {
            "q": {"type": "noul", "noul": probability}}}, {"q": {"type": "noul"}})


def test_f2_keeps_raw_labels_and_never_fabricates_distributions(policy, http_factory):
    """what_bug_this_catches: generated labels get one-hot probabilities/confidence and look calibrated."""
    questions = {"present": {"type": "noul", "instructions": "Present?"},
        "selection": {"type": "choice", "instructions": "Choose registered ID", "criteria": {"w1": "passage", "none": "absent"}}}
    raw = {"id": "fake-response", "model": policy("F2").model,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps({"present": None, "selection": "w1"})}, "logprobs": None}]}
    client = http_factory(raw)
    result = OpenAIClassificationProvider(policy("F2"), http_client=client).attempt({}, questions, timeout=10)
    assert result["status"] == "needs_review" and result["answers"] == {}
    assert result["raw_classifications"] == {"present": None, "selection": "w1"}
    assert result["origin"] == "mock" and result["model"] is None
    assert "probabilities" not in result and "confidence" not in result
    assert result["usage"]["input_tokens"] == 10
    assert client.calls[0]["body"]["response_format"]["json_schema"]["strict"] is True
    raw["choices"][0]["message"]["content"] = json.dumps({"present": True, "selection": "unregistered"})
    assert OpenAIClassificationProvider(policy("F2"), http_client=client).attempt({}, questions, timeout=10)["code"] == "malformed_response"


def test_f2_refusal_preserves_reported_usage(policy, http_factory):
    """what_bug_this_catches: refusals become successful absence or lose billable usage."""
    config = policy("F2")
    raw = {"model": config.model, "usage": {"prompt_tokens": 7, "completion_tokens": 1},
           "choices": [{"finish_reason": "stop", "message": {"refusal": "fixture refusal"}}]}
    result = OpenAIClassificationProvider(config, http_client=http_factory(raw)).attempt({}, build_questions("inventory"), timeout=10)
    assert result["code"] == "refusal" and result["status"] == "unavailable"
    assert result["usage"]["input_tokens"] == 7
