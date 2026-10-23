"""Bounded source for future live execution; no workers exist until judge()."""
from __future__ import annotations

from decimal import Decimal
import queue
import random
import threading
import time
from typing import Any, Mapping

from rfp_matrix.contracts import M3Error, canonical_json, digest
from .live import outcome, validate_questions


class BoundedDispatcher:
    """One durable ledger per application is required, including across processes.

    A daemon transport worker may outlive a caller timeout. Its database slot and
    ambiguous reservation remain held until it really exits; it is never replaced
    just because the caller stopped waiting. Crashed-worker slots fail closed and
    need explicit later operator reconciliation, not a timed lease takeover.
    """

    def __init__(self, transport: Any, *, ledger: Any) -> None:
        self.transport, self.config, self.ledger = transport, transport.config, ledger

    def judge(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> dict[str, Any]:
        return self.dispatch(state, questions)

    def _ledger_matches(self) -> bool:
        policy = self.config
        return (self.ledger.budget == Decimal(policy.budget_usd)
                and self.ledger.origin == self.transport.origin
                and self.ledger.input_price == Decimal(policy.input_per_million_usd)
                and self.ledger.output_price == Decimal(policy.output_per_million_usd)
                and self.ledger.price_policy == policy.price_policy)

    def dispatch(self, state: Mapping[str, Any], questions: Mapping[str, Any], *,
                 cancel: threading.Event | None = None) -> dict[str, Any]:
        policy = self.config
        deadline = time.monotonic() + policy.deadline_seconds
        request_hash: str | None = None
        claimed = False
        try:
            reason = policy.admission_reason()
            if reason:
                return outcome(policy, reason)
            if not self._ledger_matches():
                return outcome(policy, "ledger_policy_mismatch")
            validate_questions(questions)
            # Freeze caller-owned data before identity, bounds and transport use.
            # JSON round-trip also rejects non-JSON objects before any reservation.
            import json
            frozen = json.loads(canonical_json({"state": dict(state), "questions": dict(questions)}))
            state, questions = frozen["state"], frozen["questions"]
            body = self.transport.request_body(state, questions)
            byte_bound = len(canonical_json(body).encode("utf-8")) + 1024
            if byte_bound > policy.max_input_tokens:
                return outcome(policy, "request_input_bound_exceeded", input_byte_bound=byte_bound)
            identity = {"origin": self.transport.origin, "state": state, "questions": questions,
                        "config": policy.identity()}
            request_hash = digest("m3.live-dispatch.v1", identity)
            specification = digest("m3.dispatch-reservation.v1", {"identity": request_hash,
                "attempts": 2, "max_input": policy.max_input_tokens,
                "max_output": policy.max_output_tokens, "config": policy.config_sha256})
            if cancel is not None and cancel.is_set():
                return outcome(policy, "cancelled_before_reservation", request_hash=request_hash)
            reserved = self.ledger.prepare_dispatch(request_hash, specification=specification,
                document_id=policy.document_id, document_budget_usd=policy.document_budget_usd,
                max_input_tokens=policy.max_input_tokens, max_output_tokens=policy.max_output_tokens)
            if not reserved["created"]:
                receipt = reserved["receipt"]
                if receipt is None:
                    return outcome(policy, "request_pending_or_unreconciled", request_hash=request_hash)
                replay = dict(receipt)
                replay.update(origin="replay", original_origin=receipt.get("origin"),
                              incremental_cost_usd="0", cache_identity=request_hash)
                return replay
            claimed = True
            attempts: list[dict[str, Any]] = []
            for attempt in (0, 1):
                # Bounded capacity wait; ledger is shared with other dispatchers.
                while True:
                    if cancel is not None and cancel.is_set():
                        return self._finish(request_hash, outcome(policy, "cancelled", charge_ambiguous=True), attempts)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._finish(request_hash, outcome(policy, "deadline_exceeded", charge_ambiguous=True), attempts)
                    if self.ledger.start_attempt(request_hash, attempt):
                        break
                    if cancel is not None:
                        cancel.wait(min(0.05, remaining))
                    else:
                        time.sleep(min(0.05, remaining))
                responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
                worker = threading.Thread(target=self._worker,
                    args=(responses, request_hash, attempt, state, questions, deadline, cancel), daemon=True,
                    name=f"m3-provider-{request_hash[:8]}-{attempt}")
                try:
                    worker.start()
                except Exception:
                    self.ledger.complete_attempt(request_hash, attempt, None, definitely_not_dispatched=True)
                    return self._finish(request_hash, outcome(policy, "worker_start_failed"), attempts)
                while True:
                    if cancel is not None and cancel.is_set():
                        return self._finish(request_hash, outcome(policy, "cancelled", dispatched=True, charge_ambiguous=True), attempts)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._finish(request_hash, outcome(policy, "deadline_exceeded", dispatched=True, charge_ambiguous=True), attempts)
                    try:
                        result = responses.get(timeout=min(0.05, remaining))
                        break
                    except queue.Empty:
                        continue
                attempts.append({"attempt": attempt, "status": result.get("status"),
                                 "code": result.get("code"), "usage": result.get("usage"),
                                 "accounting": result.get("accounting")})
                if time.monotonic() >= deadline:
                    return self._finish(request_hash, outcome(policy, "deadline_exceeded", dispatched=True, charge_ambiguous=True), attempts)
                if not result.get("retryable") or attempt == 1:
                    return self._finish(request_hash, result, attempts)
                delay = random.uniform(0.05, 0.25)
                if deadline - time.monotonic() <= delay:
                    return self._finish(request_hash, outcome(policy, "deadline_exceeded", charge_ambiguous=True), attempts)
                if cancel is not None:
                    cancel.wait(delay)
                else:
                    time.sleep(delay)
            raise M3Error("dispatch_state", "Unreachable attempt state")
        except Exception as exc:
            code = exc.code if isinstance(exc, M3Error) else "dispatch_unavailable"
            failed = outcome(policy, code, request_hash=request_hash, charge_ambiguous=claimed)
            if claimed and request_hash is not None:
                try:
                    return self._finish(request_hash, failed, [])
                except Exception:
                    # No receipt is safer than replacing unresolved durable state.
                    failed["reason"] = "accounting_unavailable_reservations_retained"
            return failed

    def _worker(self, responses: queue.Queue[dict[str, Any]], request_hash: str, attempt: int,
                state: Mapping[str, Any], questions: Mapping[str, Any], deadline: float,
                cancel: threading.Event | None) -> None:
        try:
            remaining = deadline - time.monotonic()
            result = (self.transport.attempt(state, questions, timeout=remaining) if remaining > 0 and not (cancel is not None and cancel.is_set())
                      else outcome(self.config, "deadline_before_transport"))
        except Exception:
            result = outcome(self.config, "transport_unavailable", dispatched=True,
                             retryable=False, charge_ambiguous=True)
        try:
            result["accounting"] = self.ledger.complete_attempt(request_hash, attempt, result.get("usage"),
                definitely_not_dispatched=not result.get("dispatched", False))
            if result["accounting"].get("reservation_exceeded") or result["accounting"].get("token_bound_exceeded"):
                result = outcome(self.config, "reported_usage_exceeded_approved_bound", usage=result.get("usage"),
                                 dispatched=True, accounting=result["accounting"], charge_ambiguous=False)
        except Exception:
            result = outcome(self.config, "accounting_unavailable", dispatched=True, charge_ambiguous=True)
        responses.put(result)

    def _finish(self, request_hash: str, value: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
        result = dict(value)
        result.update(request_hash=request_hash, attempts=attempts,
                      question_policy=self.config.question_policy, source_policy=self.config.source_policy)
        if self.transport.origin == "mock":
            result.update(origin="mock", model=None, usage_semantics="simulated")
        return self.ledger.finish_dispatch(request_hash, result)
