import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.budget import Budget, BudgetExceeded


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "budget.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_compute_reservations_across_connections(self):
        budgets = [Budget(self.path, daily_jobs=20, daily_processing_seconds=100,
                          per_invite_daily_jobs=20) for _ in range(12)]
        def reserve(i):
            try:
                return budgets[i].reserve_job(f"owner{i}", 10)
            except BudgetExceeded:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            ids = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(x is not None for x in ids), 10)
        self.assertEqual(Budget(self.path, daily_processing_seconds=100).snapshot()[
            "daily_processing_seconds_pending"], 100)

    def test_per_owner_and_daily_job_limits_include_settled_jobs(self):
        b = Budget(self.path, daily_jobs=2, per_invite_daily_jobs=1)
        first = b.reserve_job("hash-a", 2)
        b.finish_job(first, 1)
        with self.assertRaises(BudgetExceeded):
            b.reserve_job("hash-a", 0)
        b.reserve_job("hash-b", 0)
        with self.assertRaises(BudgetExceeded):
            b.reserve_job("hash-c", 0)

    def test_compute_validation_and_idempotency(self):
        b = Budget(self.path, daily_processing_seconds=10)
        rid = b.reserve_job("h", 5)
        with self.assertRaises(ValueError):
            b.finish_job(rid, -1)
        with self.assertRaises(BudgetExceeded):
            b.finish_job(rid, 6)
        b.finish_job(rid, 3)
        b.finish_job(rid, 3)
        with self.assertRaises(ValueError):
            b.finish_job(rid, 2)

    def test_ai_lifetime_cap_pending_settlement_and_restart(self):
        b = Budget(self.path, total_ai_microusd=100)
        rid = b.reserve_ai("job", 70)
        Budget(self.path, total_ai_microusd=1000)
        with self.assertRaises(BudgetExceeded):
            b.reserve_ai("job2", 31)
        with self.assertRaises(ValueError):
            b.settle_ai(rid, -1)
        with self.assertRaises(BudgetExceeded):
            b.settle_ai(rid, 71)
        b.settle_ai(rid, 40)
        b.settle_ai(rid, 40)
        other = b.reserve_ai("job2", 60)
        with self.assertRaises(ValueError):
            b.settle_ai(rid, 39)
        self.assertEqual(b.snapshot()["ai_spend_microusd"], 40)
        self.assertEqual(b.snapshot()["ai_pending_microusd"], 60)
        self.assertEqual(b.snapshot()["ai_remaining_microusd"], 0)
        self.assertIsNotNone(other)

    def test_ai_reservation_race_never_oversubscribes(self):
        budgets = [Budget(self.path, total_ai_microusd=100) for _ in range(10)]
        def reserve(i):
            try:
                return budgets[i].reserve_ai(str(i), 20)
            except BudgetExceeded:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(reserve, range(10)))
        self.assertEqual(sum(x is not None for x in ids), 5)
        self.assertEqual(budgets[0].snapshot()["ai_pending_microusd"], 100)

    def test_other_schema_tables_are_untouched(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE app_data (id INTEGER PRIMARY KEY)")
        Budget(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='app_data'").fetchone())


if __name__ == "__main__":
    unittest.main()
