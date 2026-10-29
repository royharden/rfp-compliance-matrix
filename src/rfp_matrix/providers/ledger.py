"""Durable worst-case accounting source; construction never opens this DB.

Unknown actual usage retains its reservation. This accounting port does not admit
a network transport or authorize budget. All live dispatch remains disabled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from rfp_matrix.contracts import M3Error, canonical_json, digest
import json


def _amount(value: str | Decimal) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise M3Error("invalid_price", "Price/budget must be a finite Decimal") from exc
    if not result.is_finite() or result < 0:
        raise M3Error("invalid_price", "Price/budget must be nonnegative and finite")
    return result


class CallLedger:
    """A zero-budget default cannot reserve even a nominally free live request."""

    def __init__(self, path: str | Path, *, budget_usd: str = "0",
                 input_per_million_usd: str | None = None,
                 output_per_million_usd: str | None = None,
                 price_policy: str | None = None, origin: str = "live") -> None:
        self.path = Path(path)
        self.budget = _amount(budget_usd)
        self.input_price = _amount(input_per_million_usd) if input_per_million_usd is not None else None
        self.output_price = _amount(output_per_million_usd) if output_per_million_usd is not None else None
        self.price_policy = price_policy
        if origin not in {"live", "mock"}:
            raise M3Error("invalid_origin", "Accounting must be live or explicitly simulated")
        self.origin = origin

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise M3Error("invalid_ledger", "Ledger cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS calls (operation_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, request_spec TEXT NOT NULL, reservation TEXT NOT NULL, actual_cost TEXT, status TEXT NOT NULL, usage_input INTEGER, usage_output INTEGER, created_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS dispatch_requests (request_hash TEXT PRIMARY KEY, specification TEXT NOT NULL, document_id TEXT NOT NULL, receipt TEXT, created_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS dispatch_attempts (operation_key TEXT PRIMARY KEY REFERENCES calls(operation_key), request_hash TEXT NOT NULL, attempt INTEGER NOT NULL, max_input INTEGER NOT NULL, max_output INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0)")
        connection.execute("CREATE TABLE IF NOT EXISTS dispatch_policy (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, operation_key: str, request_hash: str, *, max_input_tokens: int,
                max_output_tokens: int, attempt: int = 0) -> dict[str, Any]:
        if self.budget <= 0:
            raise M3Error("budget_disabled", "Live budget is zero")
        if self.input_price is None or self.output_price is None or not self.price_policy:
            raise M3Error("price_unknown", "A pinned complete price policy is required")
        if type(max_input_tokens) is not int or type(max_output_tokens) is not int or type(attempt) is not int or not 0 < max_input_tokens <= 8000 or not 0 <= max_output_tokens <= 8000 or attempt not in {0, 1}:
            raise M3Error("request_limit", "At most 8k input and one visible retry are permitted")
        specification = digest("m3.call-reservation.v1", {"request_hash": request_hash,
            "input": max_input_tokens, "output": max_output_tokens, "attempt": attempt,
            "price_policy": self.price_policy})
        reserved = (self.input_price * max_input_tokens + self.output_price * max_output_tokens) / Decimal(1_000_000)
        with self._transaction() as db:
            previous = db.execute("SELECT * FROM calls WHERE operation_key=?", (operation_key,)).fetchone()
            if previous:
                if previous["request_spec"] != specification:
                    raise M3Error("idempotency_conflict", "Call key reused with a different request")
                return dict(previous)
            committed = sum((_amount(row["actual_cost"] if row["actual_cost"] is not None else row["reservation"]) for row in db.execute("SELECT actual_cost,reservation FROM calls")), Decimal(0))
            if committed + reserved > self.budget:
                raise M3Error("budget_exhausted", "Worst-case reservation would exceed budget")
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?, ?,NULL,NULL,?)", (operation_key, request_hash, specification, str(reserved), None, "reserved", datetime.now(timezone.utc).isoformat()))
            return {"operation_key": operation_key, "request_hash": request_hash, "reservation": str(reserved), "status": "reserved"}

    def settle(self, operation_key: str, *, input_tokens: int | None,
               output_tokens: int | None) -> dict[str, Any]:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE operation_key=?", (operation_key,)).fetchone()
            if row is None:
                raise M3Error("unknown_call", "Cannot settle an unreserved call")
            if input_tokens is None or output_tokens is None:
                db.execute("UPDATE calls SET status='usage_unknown' WHERE operation_key=? AND actual_cost IS NULL", (operation_key,))
                return {"operation_key": operation_key, "actual_cost_usd": row["actual_cost"], "reservation_retained": row["actual_cost"] is None}
            if type(input_tokens) is not int or type(output_tokens) is not int or input_tokens < 0 or output_tokens < 0 or self.input_price is None or self.output_price is None:
                raise M3Error("invalid_usage", "Actual usage must be nonnegative and priced")
            cost = (self.input_price * input_tokens + self.output_price * output_tokens) / Decimal(1_000_000)
            if row["actual_cost"] is not None and (row["usage_input"] != input_tokens or row["usage_output"] != output_tokens):
                raise M3Error("settlement_conflict", "Actual usage cannot be silently changed")
            db.execute("UPDATE calls SET actual_cost=?,status='settled',usage_input=?,usage_output=? WHERE operation_key=?", (str(cost), input_tokens, output_tokens, operation_key))
            return {"operation_key": operation_key, "actual_cost_usd": str(cost), "reservation_retained": False,
                    "reservation_exceeded": cost > _amount(row["reservation"])}

    def prepare_dispatch(self, request_hash: str, *, specification: str, document_id: str,
                         document_budget_usd: str, max_input_tokens: int,
                         max_output_tokens: int) -> dict[str, Any]:
        """Atomically reserve BOTH possible attempts before any dispatch.

        Retry reservations are never released while an attempt may still be sent.
        One shared ledger path is the application-wide budget/concurrency authority.
        """
        if self.budget <= 0 or self.input_price is None or self.output_price is None or not self.price_policy:
            raise M3Error("budget_disabled", "A positive budget and complete pinned price policy are required")
        if (not document_id or type(max_input_tokens) is not int or not 0 < max_input_tokens <= 8000
                or type(max_output_tokens) is not int or not 0 < max_output_tokens <= 8000):
            raise M3Error("request_limit", "Invalid scope or token bounds")
        per_attempt = (self.input_price * max_input_tokens + self.output_price * max_output_tokens) / Decimal(1_000_000)
        total = per_attempt * 2
        with self._transaction() as db:
            policy = db.execute("SELECT value FROM dispatch_policy WHERE name='price' ").fetchone()
            price_identity = digest("m3.ledger-price.v1", {"input": str(self.input_price),
                "output": str(self.output_price), "policy": self.price_policy, "budget": str(self.budget), "origin": self.origin})
            if policy and policy["value"] != price_identity:
                raise M3Error("ledger_policy_mismatch", "Ledger pricing/budget cannot silently change")
            db.execute("INSERT OR IGNORE INTO dispatch_policy VALUES ('price',?)", (price_identity,))
            if db.execute("SELECT 1 FROM dispatch_policy WHERE name='halted'").fetchone():
                raise M3Error("accounting_halted", "Resolve the recorded accounting overrun before further dispatch")
            previous = db.execute("SELECT * FROM dispatch_requests WHERE request_hash=?", (request_hash,)).fetchone()
            if previous:
                if previous["specification"] != specification or previous["document_id"] != document_id:
                    raise M3Error("idempotency_conflict", "Request identity reused with different scope/policy")
                return {"created": False, "receipt": json.loads(previous["receipt"]) if previous["receipt"] else None}
            committed = sum((_amount(r["actual_cost"] if r["actual_cost"] is not None else r["reservation"])
                             for r in db.execute("SELECT actual_cost,reservation FROM calls")), Decimal(0))
            scoped = sum((_amount(r["actual_cost"] if r["actual_cost"] is not None else r["reservation"])
                          for r in db.execute("SELECT c.actual_cost,c.reservation FROM calls c LEFT JOIN dispatch_attempts a ON a.operation_key=c.operation_key LEFT JOIN dispatch_requests r ON r.request_hash=a.request_hash WHERE r.document_id=? OR a.operation_key IS NULL", (document_id,))), Decimal(0))
            if committed + total > self.budget or scoped + total > _amount(document_budget_usd):
                raise M3Error("budget_exhausted", "Two-attempt worst-case reservation exceeds document/batch budget")
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO dispatch_requests VALUES (?,?,?,?,?)", (request_hash, specification, document_id, None, now))
            for attempt in (0, 1):
                key = f"{request_hash}:{attempt}"
                db.execute("INSERT INTO calls VALUES (?,?,?,?,?, ?,NULL,NULL,?)", (key, request_hash,
                    specification, str(per_attempt), None, "reserved", now))
                db.execute("INSERT INTO dispatch_attempts VALUES (?,?,?,?,?,0,0)", (key, request_hash, attempt, max_input_tokens, max_output_tokens))
            return {"created": True, "reservation_usd": str(total), "receipt": None}

    def budget_snapshot(self, document_id: str) -> dict[str, Any]:
        """Explicit later runtime snapshot, never invoked by the pure estimator.

        Opens/initializes the accounting schema through the normal ledger path.
        Unknown charges count at retained reservation. This is an exact snapshot,
        not a whole-run reservation; later shared-ledger traffic can change it.
        """
        if not isinstance(document_id, str) or not document_id or len(document_id) > 160:
            raise M3Error("invalid_budget_scope", "A bounded document ID is required")
        with self._transaction() as db:
            all_rows = [dict(row) for row in db.execute("SELECT operation_key,reservation,actual_cost,status FROM calls ORDER BY operation_key")]
            scoped = [dict(row) for row in db.execute("SELECT c.operation_key,c.reservation,c.actual_cost,c.status FROM calls c LEFT JOIN dispatch_attempts a ON a.operation_key=c.operation_key LEFT JOIN dispatch_requests r ON r.request_hash=a.request_hash WHERE r.document_id=? OR a.operation_key IS NULL ORDER BY c.operation_key", (document_id,))]
        def upper(rows):
            return sum((_amount(row["actual_cost"] if row["actual_cost"] is not None else row["reservation"]) for row in rows), Decimal(0))
        return {"schema_version": 1, "document_id": document_id, "origin": self.origin,
            "batch_committed_usd": str(upper(all_rows)), "document_committed_usd": str(upper(scoped)),
            "ledger_snapshot_sha256": digest("m3.run-budget-snapshot.v1", {"origin": self.origin,
                "document_id": document_id, "price_policy": self.price_policy, "batch": all_rows, "document": scoped})}

    def start_attempt(self, request_hash: str, attempt: int) -> bool:
        if type(attempt) is not int or attempt not in (0, 1):
            raise M3Error("request_limit", "At most one retry is permitted")
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM dispatch_policy WHERE name='halted'").fetchone():
                raise M3Error("accounting_halted", "Accounting requires reconciliation")
            row = db.execute("SELECT * FROM dispatch_attempts WHERE operation_key=?", (f"{request_hash}:{attempt}",)).fetchone()
            parent = db.execute("SELECT receipt FROM dispatch_requests WHERE request_hash=?", (request_hash,)).fetchone()
            if row is None or row["started"] or parent is None or parent["receipt"] is not None:
                raise M3Error("idempotency_conflict", "Attempt already dispatched or request already finalized")
            if db.execute("SELECT COUNT(*) FROM dispatch_attempts WHERE active=1").fetchone()[0] >= 2:
                return False
            db.execute("UPDATE dispatch_attempts SET active=1,started=1 WHERE operation_key=?", (row["operation_key"],))
            db.execute("UPDATE calls SET status='dispatching' WHERE operation_key=?", (row["operation_key"],))
            return True

    def complete_attempt(self, request_hash: str, attempt: int, usage: dict[str, Any] | None,
                         *, definitely_not_dispatched: bool = False) -> dict[str, Any]:
        key = f"{request_hash}:{attempt}"
        try:
            result = self.settle(key, input_tokens=0 if definitely_not_dispatched else (usage or {}).get("input_tokens"),
                                 output_tokens=0 if definitely_not_dispatched else (usage or {}).get("output_tokens"))
            if result.get("reservation_exceeded"):
                self.halt("reported_cost_exceeded_reservation")
            with self._transaction() as db:
                bounds = db.execute("SELECT max_input,max_output FROM dispatch_attempts WHERE operation_key=?", (key,)).fetchone()
            if bounds is None:
                raise M3Error("unknown_call", "Attempt bounds are missing")
            result["token_bound_exceeded"] = any(type((usage or {}).get(field)) is int and (usage or {})[field] > bounds[column]
                for field, column in [("input_tokens", "max_input"), ("output_tokens", "max_output")])
            if result["token_bound_exceeded"]:
                self.halt("reported_tokens_exceeded_approved_bound")
            if self.origin == "mock":
                result.update(simulated_cost_usd=result["actual_cost_usd"], actual_cost_usd=None,
                              accounting_semantics="simulation")
            return result
        except Exception:
            self.halt("settlement_failed_usage_unresolved")
            raise
        finally:
            # Timeout/cancellation returns do not execute this; only the worker
            # exiting the actual transport attempt can release its durable slot.
            with self._transaction() as db:
                db.execute("UPDATE dispatch_attempts SET active=0 WHERE operation_key=?", (key,))

    def halt(self, reason: str) -> None:
        with self._transaction() as db:
            db.execute("INSERT OR REPLACE INTO dispatch_policy VALUES ('halted',?)", (reason,))

    def finish_dispatch(self, request_hash: str, receipt: dict[str, Any]) -> dict[str, Any]:
        with self._transaction() as db:
            current = db.execute("SELECT receipt FROM dispatch_requests WHERE request_hash=?", (request_hash,)).fetchone()
            if current is None:
                raise M3Error("unknown_call", "Cannot finalize an unreserved request")
            if current["receipt"] is not None:
                return json.loads(current["receipt"])
            # Unsent retries have known zero incremental charge. In-flight calls
            # retain reservation and slots until the real worker exits.
            db.execute("UPDATE calls SET actual_cost='0',usage_input=0,usage_output=0,status='not_dispatched' WHERE operation_key IN (SELECT operation_key FROM dispatch_attempts WHERE request_hash=? AND started=0)", (request_hash,))
            accounting = db.execute("SELECT c.operation_key,c.reservation,c.actual_cost,c.status FROM calls c JOIN dispatch_attempts a ON a.operation_key=c.operation_key WHERE a.request_hash=? ORDER BY a.attempt", (request_hash,)).fetchall()
            complete = all(r["actual_cost"] is not None for r in accounting)
            result = dict(receipt)
            result.update({"attempt_accounting": [dict(r) for r in accounting],
                "actual_cost_usd": str(sum((_amount(r["actual_cost"]) for r in accounting), Decimal(0))) if complete else None,
                "cost_status": "known" if complete else "unknown_reservation_retained"})
            if self.origin == "mock":
                result.update(simulated_cost_usd=result["actual_cost_usd"], actual_cost_usd=None,
                              accounting_semantics="simulation", cost_status="simulated_" + result["cost_status"])
                for record in result["attempt_accounting"]:
                    record["simulated_cost"] = record.pop("actual_cost")
            db.execute("UPDATE dispatch_requests SET receipt=? WHERE request_hash=?", (canonical_json(result), request_hash))
            return result
