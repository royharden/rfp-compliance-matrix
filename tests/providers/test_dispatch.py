"""Unrun deterministic future transport/clock/ledger regressions."""
from dataclasses import replace
import threading

from rfp_matrix.providers import build_questions, create_provider


def test_one_retry_with_no_hidden_sdk_retries(policy, ledger_for, inline_clock, sdk_factory, noul_response):
    """what_bug_this_catches: SDK retries multiply the controller's two-attempt budget."""
    config = policy()
    questions = build_questions("inventory")
    client = sdk_factory([TimeoutError(), noul_response(questions)])
    result = create_provider(config, client=client, ledger=ledger_for(config)).judge({}, questions)
    assert result["status"] == "ok" and len(client.calls) == 2
    assert all(call["retry"] == {"max_retries": 0} for call in client.calls)
    assert len(result["attempts"]) == 2
    # The first timeout's charge is ambiguous, despite the successful retry.
    assert result["actual_cost_usd"] is None
    assert result["cost_status"] == "simulated_unknown_reservation_retained"
    assert result["attempt_accounting"][0]["simulated_cost"] is None
    assert inline_clock.now == 0.1


def test_deadline_withholds_late_result_and_does_not_retry(policy, ledger_for, inline_clock, sdk_factory, noul_response):
    """what_bug_this_catches: late results/retries escape the original request deadline."""
    config = policy(deadline_seconds=1)
    questions = build_questions("inventory")
    class LateClient:
        calls = 0

        def system_one(self, **request):
            self.calls += 1
            inline_clock.sleep(2)
            return noul_response(questions)
    client = LateClient()
    result = create_provider(config, client=client, ledger=ledger_for(config)).judge({}, questions)
    assert result["code"] == "deadline_exceeded" and result["status"] == "unavailable"
    assert client.calls == 1


def test_cancel_before_reservation_never_dispatches(policy, ledger_for, sdk_factory):
    """what_bug_this_catches: a canceled request starts network work or budget reservation."""
    config = policy()
    ledger = ledger_for(config)
    client = sdk_factory([])
    cancel = threading.Event()
    cancel.set()
    result = create_provider(config, client=client, ledger=ledger).dispatch({}, build_questions("inventory"), cancel=cancel)
    assert result["code"] == "cancelled_before_reservation" and not client.calls
    assert not ledger.path.exists()


def test_cache_identity_includes_origin_model_sdk_and_policies(policy, ledger_for, inline_clock, sdk_factory, noul_response):
    """what_bug_this_catches: repeats are charged twice or changed source/policy hits old cache."""
    config = policy()
    questions = build_questions("inventory")
    ledger = ledger_for(config)
    client = sdk_factory([noul_response(questions), noul_response(questions)])
    port = create_provider(config, client=client, ledger=ledger)
    first = port.judge({"clause": "synthetic"}, questions)
    second = port.judge({"clause": "synthetic"}, questions)
    assert len(client.calls) == 1
    assert second["origin"] == "replay" and second["original_origin"] == "mock"
    assert second["incremental_cost_usd"] == "0" and second["actual_cost_usd"] is None
    changed = replace(config, corpus_sha256="c" * 64, approved_config_sha256="")
    changed = replace(changed, approved_config_sha256=changed.config_sha256)
    third = create_provider(changed, client=client, ledger=ledger).judge({"clause": "synthetic"}, questions)
    assert len(client.calls) == 2 and third["request_hash"] != first["request_hash"]
    # Fake clients cannot accidentally charge/use an application's live ledger.
    denied = create_provider(config, client=client, ledger=ledger_for(config, name="live.sqlite", origin="live")).judge({}, questions)
    assert denied["code"] == "ledger_policy_mismatch"


def test_preflight_rejects_oversized_body_before_ledger_or_client(policy, ledger_for, sdk_factory):
    """what_bug_this_catches: source text exceeding the input bound reserves or sends first."""
    config = policy()
    ledger = ledger_for(config)
    client = sdk_factory([])
    result = create_provider(config, client=client, ledger=ledger).judge({"text": "x" * 9000}, build_questions("inventory"))
    assert result["code"] == "request_input_bound_exceeded"
    assert not client.calls and not ledger.path.exists()
