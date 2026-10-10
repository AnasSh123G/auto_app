"""Request budget for rate-limited APIs: per run, per day and per month, kept in the database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime


class RequestBudget:
    """Counts requests in ``api_usage`` so limits hold across separate runs.

    ``take()`` reserves one request and returns ``True``, or returns ``False`` and sets
    ``reason`` when any cap is reached. Attempts count whether or not they succeed.
    """

    def __init__(self, conn: sqlite3.Connection, source: str, *, per_run: int, per_day: int, per_month: int,
                 today: date | None = None, reserve: int = 0) -> None:
        self._conn, self._source = conn, source
        # ``reserve`` requests stay unused by this caller: another step (the own-ad search) needs them later.
        self._per_run, self._per_day, self._per_month = per_run - reserve, per_day - reserve, per_month
        self._day = (today or datetime.now(UTC).date()).isoformat()
        self.used_this_run = 0
        self.reason: str | None = None

    def _sum(self, like: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(requests), 0) FROM api_usage WHERE source = ? AND day LIKE ?",
            (self._source, like),
        ).fetchone()
        return int(row[0])

    def take(self) -> bool:
        if self.reason:
            return False
        limits = (
            (self.used_this_run, self._per_run, "per-run"),
            (self._sum(self._day), self._per_day, "daily"),
            (self._sum(self._day[:7] + "-%"), self._per_month, "monthly"),
        )
        for used, cap, label in limits:
            if used >= cap:
                self.reason = f"{label} request limit reached ({cap}); the rest follows on the next run"
                return False
        with self._conn:
            self._conn.execute(
                "INSERT INTO api_usage (source, day, requests) VALUES (?, ?, 1) "
                "ON CONFLICT(source, day) DO UPDATE SET requests = requests + 1",
                (self._source, self._day),
            )
        self.used_this_run += 1
        return True
