"""Durable Qveris admission ledger using the native SQLite transaction discipline.

A pending reservation is already a budget debit: process death or an ambiguous provider
failure cannot make the same credits available again. Only a definite pre-send transport
failure refunds it; a received cost settles it to the provider's actual amount.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
from pathlib import Path
import sqlite3
from uuid import uuid4


class CreditLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS credits (id TEXT PRIMARY KEY, session TEXT NOT NULL, amount REAL NOT NULL, state TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS credits_session ON credits(session)")

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, session: str, expected: float, budget: float) -> tuple[str | None, float]:
        if not all(math.isfinite(x) and x >= 0 for x in (expected, budget)):
            raise ValueError("Qveris budget and reservation must be finite nonnegative amounts")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = db.execute("SELECT COALESCE(SUM(amount), 0) FROM credits WHERE session=?", (session,)).fetchone()[0]
            if spent >= budget or spent + expected > budget:
                return None, spent
            token = uuid4().hex
            db.execute("INSERT INTO credits VALUES (?, ?, ?, 'pending')", (token, session, expected))
            return token, spent

    def settle(self, token: str, amount: float, state: str = "settled") -> float:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("Qveris cost must be finite and nonnegative")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT session FROM credits WHERE id=? AND state='pending'", (token,)).fetchone()
            if row is None:
                raise RuntimeError("Qveris credit reservation is missing or already settled")
            db.execute("UPDATE credits SET amount=?, state=? WHERE id=?", (amount, state, token))
            return db.execute("SELECT SUM(amount) FROM credits WHERE session=?", row).fetchone()[0]

    def spent(self, session: str) -> float:
        with self._connect() as db:
            return db.execute("SELECT COALESCE(SUM(amount), 0) FROM credits WHERE session=?", (session,)).fetchone()[0]

    def pending(self, session: str) -> float:
        with self._connect() as db:
            return db.execute("SELECT COALESCE(SUM(amount), 0) FROM credits WHERE session=? AND state='pending'", (session,)).fetchone()[0]
