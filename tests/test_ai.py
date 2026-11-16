import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.ai import cleanup_epub
from app.budget import Budget


class AICleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.epub = self.root / "book.epub"
        self.db = self.root / "budget.sqlite3"
        with zipfile.ZipFile(self.epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("OEBPS/chapter.xhtml",
                             '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                             '<p>Th1s is a clear paragraph with a sufflcientlylongtoken.</p>'
                             '<p>ordinary prose</p></body></html>')
        self.budget = Budget(self.db, total_ai_microusd=1_000_000)

    def tearDown(self):
        self.tmp.cleanup()

    def _response(self, patches, input_tokens=100, output_tokens=20):
        return {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                "output": [{"content": [{"type": "output_text",
                                            "text": json.dumps({"patches": patches})}]}]}

    def test_no_key_disables_provider_and_no_suspicious_text_skips(self):
        result = cleanup_epub(self.epub, self.budget, "job", "")
        self.assertFalse(result["enabled"])
        clean = self.root / "clean.epub"
        with zipfile.ZipFile(clean, "w") as archive:
            archive.writestr("text.xhtml", "<html><p>Nothing suspicious here.</p></html>")
        calls = []
        result = cleanup_epub(clean, self.budget, "job", "secret", transport=lambda p, k: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(result["calls"], 0)
        exhausted = Budget(self.root / "small.sqlite3", total_ai_microusd=100)
        result = cleanup_epub(self.epub, exhausted, "job", "key", transport=lambda p, k: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(result["calls"], 0)

    def test_reserves_before_transport_and_applies_exact_patch(self):
        def transport(payload, key):
            self.assertEqual(key, "test-key")
            snap = self.budget.snapshot()
            self.assertGreater(snap["ai_pending_microusd"], 0)
            self.assertIn("untrusted", payload["input"][0]["content"][0]["text"])
            return self._response([{"before": "Th1s", "after": "This"}])
        result = cleanup_epub(self.epub, self.budget, "job", "test-key", transport=transport)
        self.assertEqual(result["corrections"], 1)
        with zipfile.ZipFile(self.epub) as archive:
            text = archive.read("OEBPS/chapter.xhtml").decode()
            self.assertIn("This is", text)
            self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
        snapshot = self.budget.snapshot()
        self.assertEqual(snapshot["ai_pending_microusd"], 0)
        self.assertEqual(snapshot["ai_spend_microusd"], 140)

    def test_timeout_keeps_reservation_pending(self):
        def timeout(payload, key):
            raise TimeoutError()
        result = cleanup_epub(self.epub, self.budget, "job", "key", transport=timeout)
        self.assertIn("unknown", result["warnings"][0])
        self.assertGreater(self.budget.snapshot()["ai_pending_microusd"], 0)

    def test_rejects_ambiguous_patch_and_invalid_usage_keeps_reservation(self):
        result = cleanup_epub(self.epub, self.budget, "job", "key",
                              transport=lambda p, k: self._response([{"before": "ordinary", "after": "x"}]))
        self.assertEqual(result["corrections"], 0)
        with zipfile.ZipFile(self.epub) as archive:
            self.assertIn("ordinary prose", archive.read("OEBPS/chapter.xhtml").decode())
        self.assertEqual(self.budget.snapshot()["ai_spend_microusd"], 140)
        other_db = self.root / "other.sqlite3"
        other = Budget(other_db, total_ai_microusd=1_000_000)
        result = cleanup_epub(self.epub, other, "job", "key",
                              transport=lambda p, k: {"output": []})
        self.assertTrue(result["warnings"])
        self.assertGreater(other.snapshot()["ai_pending_microusd"], 0)

    def test_cost_cap_and_budget_exhaustion_stop_before_transport(self):
        calls = []
        result = cleanup_epub(self.epub, self.budget, "job", "key", max_cost_microusd=1,
                              transport=lambda p, k: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(result["calls"], 0)

    def test_eight_candidate_cap_preserves_all_later_zip_entries(self):
        with zipfile.ZipFile(self.epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            eight = "<html><body>" + "".join(f"<p>Th{i} text.</p>" for i in range(8)) + "</body></html>"
            archive.writestr("OEBPS/chapter.xhtml", eight)
            archive.writestr("OEBPS/image.bin", b"preserve-me")
            archive.writestr("OEBPS/later.xhtml", "<html><body><p>later entry</p></body></html>")
        result = cleanup_epub(self.epub, self.budget, "job", "key",
                              transport=lambda p, k: self._response([{"before": "Th0", "after": "The"}]))
        self.assertEqual(result["corrections"], 1)
        with zipfile.ZipFile(self.epub) as archive:
            self.assertEqual(archive.namelist(), ["mimetype", "OEBPS/chapter.xhtml", "OEBPS/image.bin", "OEBPS/later.xhtml"])
            self.assertEqual(archive.read("OEBPS/image.bin"), b"preserve-me")
            self.assertIn(b"later entry", archive.read("OEBPS/later.xhtml"))

    def test_xml_control_char_and_incomplete_response_are_not_applied(self):
        bad = cleanup_epub(self.epub, self.budget, "job", "key",
                           transport=lambda p, k: self._response([{"before": "Th1s", "after": "bad\x01"}]))
        self.assertEqual(bad["corrections"], 0)
        response = self._response([{"before": "Th1s", "after": "This"}])
        response["status"] = "incomplete"
        incomplete = cleanup_epub(self.epub, self.budget, "job2", "key",
                                  transport=lambda p, k: response)
        self.assertEqual(incomplete["corrections"], 0)
        with zipfile.ZipFile(self.epub) as archive:
            self.assertIn(b"Th1s", archive.read("OEBPS/chapter.xhtml"))
if __name__ == "__main__":
    unittest.main()
