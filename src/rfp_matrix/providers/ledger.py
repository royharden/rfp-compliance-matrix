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

from rfp_matrix.contracts import M3Error, digest


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
                 price_policy: str | None = None) -> None:
        self.path = Path(path)
        self.budget = _amount(budget_usd)
        self.input_price = _amount(input_per_million_usd) if input_per_million_usd is not None else None
        self.output_price = _amount(output_per_million_usd) if output_per_million_usd is not None else None
        self.price_policy = price_policy

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise M3Error("invalid_ledger", "Ledger cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS calls (operation_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, request_spec TEXT NOT NULL, reservation TEXT NOT NULL, actual_cost TEXT, status TEXT NOT NULL, usage_input INTEGER, usage_output INTEGER, created_at TEXT NOT NULL)")
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
