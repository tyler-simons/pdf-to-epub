import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.public_access import AccessDenied, PublicAccess


class PublicAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "access.sqlite3"
        self.access = PublicAccess(self.path, "test-secret", requests_per_hour=3)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identify_normalizes_ip_and_hides_address(self):
        a = self.access.identify("192.0.2.7")
        self.assertEqual(a, self.access.identify("192.0.2.7"))
        self.assertNotEqual(a, self.access.identify("192.0.2.8"))
        self.assertEqual(self.access.identify("2001:db8:abcd:12::1"),
                         self.access.identify("2001:db8:abcd:12:ffff::9"))
        self.assertEqual(self.access.identify("not an ip"), self.access.identify(None))
        self.assertNotEqual(a, PublicAccess(self.path, "different-secret").identify("192.0.2.7"))
        self.access.admit_attempt(a)
        with sqlite3.connect(self.path) as db:
            values = [str(r[0]) for r in db.execute("SELECT owner_hash FROM public_access_attempts")]
            self.assertFalse(any("192.0.2.7" in value for value in values))

    def test_rolling_hour_limit_includes_failed_upload_attempts(self):
        owner = self.access.identify("203.0.113.5")
        self.access.admit_attempt(owner)  # caller records before parsing upload
        self.access.admit_attempt(owner)
        self.access.admit_attempt(owner)
        with self.assertRaises(AccessDenied):
            self.access.admit_attempt(owner)
        now = self.access._now()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE public_access_attempts SET created_at=?", (now - 3601,))
        self.access.admit_attempt(owner)

    def test_concurrent_requests_cannot_exceed_limit(self):
        owner = self.access.identify("203.0.113.10")
        def attempt(_):
            try:
                self.access.admit_attempt(owner)
                return True
            except AccessDenied:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            admitted = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(admitted), 3)

    def test_siteverify_checks_success_hostname_and_action(self):
        payloads = []
        def good(payload):
            payloads.append(payload)
            return {"success": True, "hostname": "example.test", "action": "convert"}
        self.assertTrue(self.access.verify_challenge("token", "cf-secret", "example.test", good))
        self.assertEqual(payloads, [{"secret": "cf-secret", "response": "token"}])
        self.assertFalse(self.access.verify_challenge("token", "secret", "example.test",
            lambda p: {"success": True, "hostname": "wrong.test", "action": "convert"}))
        self.assertFalse(self.access.verify_challenge("token", "secret", "example.test",
            lambda p: {"success": True, "hostname": "example.test", "action": "login"}))
        self.assertFalse(self.access.verify_challenge("token", "secret", "example.test",
            lambda p: {"success": False, "hostname": "example.test", "action": "convert"}))

    def test_bad_tokens_and_transport_errors_fail_closed(self):
        calls = []
        self.assertFalse(self.access.verify_challenge("", "secret", "example.test", lambda p: calls.append(p)))
        self.assertFalse(self.access.verify_challenge("x" * 2049, "secret", "example.test", lambda p: calls.append(p)))
        self.assertEqual(calls, [])
        self.assertFalse(self.access.verify_challenge("token", "secret", "example.test",
                                                       lambda p: (_ for _ in ()).throw(TimeoutError())))

    def test_local_challenge_consumption_rejects_replay_and_does_not_store_token(self):
        self.assertTrue(self.access.consume_challenge("single-use-token"))
        self.assertFalse(self.access.consume_challenge("single-use-token"))
        with sqlite3.connect(self.path) as db:
            value = db.execute("SELECT token_hash FROM public_access_challenges").fetchone()[0]
            self.assertNotIn("single-use-token", value)


if __name__ == "__main__":
    unittest.main()
