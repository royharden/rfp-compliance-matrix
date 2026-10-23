"""Unrun accounting source; all prices, documents and charges are simulated."""
from decimal import Decimal

import pytest

from rfp_matrix.contracts import M3Error
from rfp_matrix.providers import CallLedger


def ledger(path, *, budget="0.03", origin="mock"):
    return CallLedger(path, budget_usd=budget, input_per_million_usd="1000",
                      output_per_million_usd="0", price_policy="test-only", origin=origin)


def reserve(book, identity, *, document="doc"):
    return book.prepare_dispatch(identity, specification="test-only-spec:" + identity,
        document_id=document, document_budget_usd="1", max_input_tokens=10, max_output_tokens=1)


def test_two_attempt_reservation_is_atomic_and_unknown_charge_stays_reserved(tmp_path):
    """what_bug_this_catches: retry is unbudgeted or a timeout silently refunds the first charge."""
    book = ledger(tmp_path / "simulation.sqlite")
    first = reserve(book, "request-1")
    assert Decimal(first["reservation_usd"]) == Decimal("0.02")  # 2 * 10 * 1000 / 1,000,000
    with pytest.raises(M3Error, match="worst-case"):
        reserve(book, "request-2")
    with book._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM dispatch_requests").fetchone()[0] == 1
    assert book.start_attempt("request-1", 0)
    unknown = book.complete_attempt("request-1", 0, None)
    assert unknown["reservation_retained"]
    receipt = book.finish_dispatch("request-1", {"origin": "mock", "status": "unavailable"})
    assert receipt["actual_cost_usd"] is None and receipt["simulated_cost_usd"] is None
    assert receipt["attempt_accounting"][0]["simulated_cost"] is None
    assert receipt["attempt_accounting"][1]["simulated_cost"] == "0"
    # Later authoritative usage may settle the actual ledger row; the original
    # timeout receipt remains an immutable account of what was known at return.
    book.settle("request-1:0", input_tokens=2, output_tokens=0)
    repeated = reserve(book, "request-1")
    assert not repeated["created"] and repeated["receipt"] == receipt


def test_two_shared_ledger_slots_survive_caller_cancellation_until_transport_exit(tmp_path):
    """what_bug_this_catches: cancel/timeout frees a slot while its HTTP worker is still active."""
    path = tmp_path / "shared-simulation.sqlite"
    first, second = ledger(path, budget="1"), ledger(path, budget="1")
    for identity in ["a", "b", "c"]:
        reserve(first, identity)
    assert first.start_attempt("a", 0)
    assert second.start_attempt("b", 0)
    first.finish_dispatch("a", {"status": "unavailable", "origin": "mock", "reason": "cancelled"})
    assert not second.start_attempt("c", 0)
    first.complete_attempt("a", 0, None)  # Only actual worker exit releases the slot.
    assert second.start_attempt("c", 0)
    with pytest.raises(M3Error, match="already dispatched"):
        first.start_attempt("a", 0)


def test_document_budget_and_shared_price_origin_binding(tmp_path):
    """what_bug_this_catches: document aggregation or opening the same DB as live resets limits."""
    path = tmp_path / "scope.sqlite"
    book = ledger(path, budget="1")
    kwargs = dict(document_id="same-document", document_budget_usd="0.03",
                  max_input_tokens=10, max_output_tokens=1)
    book.prepare_dispatch("r1", specification="s1", **kwargs)
    with pytest.raises(M3Error, match="document/batch"):
        book.prepare_dispatch("r2", specification="s2", **kwargs)
    other = ledger(path, budget="1", origin="live")
    with pytest.raises(M3Error, match="cannot silently change"):
        reserve(other, "different-origin")


def test_usage_overrun_halts_future_dispatch_even_when_output_is_free(tmp_path):
    """what_bug_this_catches: free output tokens evade the approved token-cap halt."""
    book = ledger(tmp_path / "overrun.sqlite", budget="1")
    reserve(book, "r1")
    book.start_attempt("r1", 0)
    result = book.complete_attempt("r1", 0, {"input_tokens": 1, "output_tokens": 2})
    assert result["token_bound_exceeded"] and not result["reservation_exceeded"]
    with pytest.raises(M3Error, match="overrun"):
        reserve(book, "r2")


def test_idempotency_conflict_does_not_change_existing_reservation(tmp_path):
    """what_bug_this_catches: reuse of a request key can overwrite source/policy accounting."""
    book = ledger(tmp_path / "identity.sqlite", budget="1")
    reserve(book, "r1")
    with pytest.raises(M3Error, match="different scope/policy"):
        book.prepare_dispatch("r1", specification="changed", document_id="doc",
            document_budget_usd="1", max_input_tokens=10, max_output_tokens=1)
    assert reserve(book, "r1") == {"created": False, "receipt": None}
