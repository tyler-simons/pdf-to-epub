"""Durable SQLite admission and spend limits for EPUB jobs and AI usage.

Amounts are integer micro-USD. This module makes no provider calls; callers
must reserve before work and settle after it completes.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(Exception):
    """Raised when a requested reservation would cross a configured limit."""


class Budget:
    def __init__(self, db_path: Path, total_ai_microusd: int = 10_000_000,
                 daily_jobs: int = 20, daily_processing_seconds: int = 3600,
                 per_invite_daily_jobs: int = 3):
        for name, value in (("total_ai_microusd", total_ai_microusd),
                            ("daily_jobs", daily_jobs),
                            ("daily_processing_seconds", daily_processing_seconds),
                            ("per_invite_daily_jobs", per_invite_daily_jobs)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        self.db_path = str(db_path)
        self.total_ai_microusd = total_ai_microusd
        self.daily_jobs = daily_jobs
        self.daily_processing_seconds = daily_processing_seconds
        self.per_invite_daily_jobs = per_invite_daily_jobs
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS budget_config (
                    key TEXT PRIMARY KEY, value INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budget_jobs (
                    id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, day TEXT NOT NULL,
                    reserved_seconds INTEGER NOT NULL, actual_seconds INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS budget_jobs_day ON budget_jobs(day);
                CREATE TABLE IF NOT EXISTS budget_ai (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    reserved_microusd INTEGER NOT NULL, actual_microusd INTEGER,
                    created_at TEXT NOT NULL
                );
            """)
            # The lifetime cap can only stay the same or decrease across
            # process/config changes, so old reservations can never be bypassed.
            row = db.execute("SELECT value FROM budget_config WHERE key='ai_cap'").fetchone()
            cap = total_ai_microusd if row is None else min(row[0], total_ai_microusd)
            db.execute("INSERT INTO budget_config(key,value) VALUES('ai_cap',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cap,))

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def reserve_job(self, owner_hash: str, max_seconds: int) -> str:
        """Atomically admit a job and reserve its worst-case processing time."""
        if not isinstance(owner_hash, str) or not owner_hash:
            raise ValueError("owner_hash must be a nonempty string")
        if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or max_seconds < 0:
            raise ValueError("max_seconds must be a nonnegative integer")
        now = self._now()
        day = now.date().isoformat()
        reservation_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM budget_jobs WHERE day=?", (day,)).fetchone()[0]
            owner_count = db.execute("SELECT COUNT(*) FROM budget_jobs WHERE day=? AND owner_hash=?",
                                     (day, owner_hash)).fetchone()[0]
            if count >= self.daily_jobs:
                raise BudgetExceeded("daily job limit reached")
            if owner_count >= self.per_invite_daily_jobs:
                raise BudgetExceeded("Daily conversion limit for this connection reached")
            used = db.execute("SELECT COALESCE(SUM(COALESCE(actual_seconds,reserved_seconds)),0) "
                              "FROM budget_jobs WHERE day=?", (day,)).fetchone()[0]
            if used + max_seconds > self.daily_processing_seconds:
                raise BudgetExceeded("daily processing time limit reached")
            db.execute("INSERT INTO budget_jobs VALUES(?,?,?,?,?,?)",
                       (reservation_id, owner_hash, day, max_seconds, None, now.isoformat()))
            return reservation_id

    def finish_job(self, reservation_id: str, actual_seconds: int) -> None:
        """Settle a compute reservation; repeats are safe when values match."""
        if not isinstance(actual_seconds, int) or isinstance(actual_seconds, bool) or actual_seconds < 0:
            raise ValueError("actual_seconds must be a nonnegative integer")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM budget_jobs WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError("unknown job reservation")
            if row["actual_seconds"] is not None:
                if row["actual_seconds"] != actual_seconds:
                    raise ValueError("job reservation already settled differently")
                return
            if actual_seconds > row["reserved_seconds"]:
                raise BudgetExceeded("actual processing time exceeded reservation")
            db.execute("UPDATE budget_jobs SET actual_seconds=? WHERE id=?", (actual_seconds, reservation_id))

    def reserve_ai(self, job_id: str, maximum_microusd: int) -> str:
        """Reserve lifetime AI spend in integer micro-USD before any provider call."""
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a nonempty string")
        if not isinstance(maximum_microusd, int) or isinstance(maximum_microusd, bool) or maximum_microusd < 0:
            raise ValueError("maximum_microusd must be a nonnegative integer")
        reservation_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cap = db.execute("SELECT value FROM budget_config WHERE key='ai_cap'").fetchone()[0]
            used = db.execute("SELECT COALESCE(SUM(COALESCE(actual_microusd,reserved_microusd)),0) FROM budget_ai").fetchone()[0]
            if used + maximum_microusd > cap:
                raise BudgetExceeded("lifetime AI budget exhausted")
            db.execute("INSERT INTO budget_ai VALUES(?,?,?,?,?)",
                       (reservation_id, job_id, maximum_microusd, None, self._now().isoformat()))
        return reservation_id

    def settle_ai(self, reservation_id: str, actual_microusd: int) -> None:
        """Settle AI spend once; reject negative or over-reservation charges."""
        if not isinstance(actual_microusd, int) or isinstance(actual_microusd, bool) or actual_microusd < 0:
            raise ValueError("actual_microusd must be a nonnegative integer")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM budget_ai WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError("unknown AI reservation")
            if row["actual_microusd"] is not None:
                if row["actual_microusd"] != actual_microusd:
                    raise ValueError("AI reservation already settled differently")
                return
            if actual_microusd > row["reserved_microusd"]:
                raise BudgetExceeded("AI charge exceeded reservation")
            db.execute("UPDATE budget_ai SET actual_microusd=? WHERE id=?", (actual_microusd, reservation_id))

    def snapshot(self) -> dict:
        """Return aggregate spend and remaining limits without owner/job secrets."""
        today = self._now().date().isoformat()
        with self._connect() as db:
            cap = db.execute("SELECT value FROM budget_config WHERE key='ai_cap'").fetchone()[0]
            ai = db.execute("SELECT COALESCE(SUM(COALESCE(actual_microusd,reserved_microusd)),0), "
                            "COALESCE(SUM(CASE WHEN actual_microusd IS NULL THEN reserved_microusd ELSE 0 END),0) "
                            "FROM budget_ai").fetchone()
            jobs = db.execute("SELECT COUNT(*), COALESCE(SUM(COALESCE(actual_seconds,reserved_seconds)),0), "
                              "COALESCE(SUM(CASE WHEN actual_seconds IS NULL THEN reserved_seconds ELSE 0 END),0) "
                              "FROM budget_jobs WHERE day=?", (today,)).fetchone()
        return {"ai_cap_microusd": cap, "ai_spend_microusd": ai[0] - ai[1],
                "ai_pending_microusd": ai[1], "ai_remaining_microusd": max(0, cap - ai[0]),
                "daily_jobs": jobs[0], "daily_processing_seconds_used": jobs[1] - jobs[2],
                "daily_processing_seconds_pending": jobs[2],
                "daily_processing_seconds_remaining": max(0, self.daily_processing_seconds - jobs[1])}
