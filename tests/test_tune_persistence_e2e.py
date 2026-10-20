"""E2E: saved Tune recommendations persist and Recompute archives the previous one (scenarios 1-4).

Needs a running app and a Redis it reads from. Skipped unless both are configured:
    TUNE_E2E_BASE_URL=http://127.0.0.1:8000  TUNE_E2E_REDIS_URL=redis://localhost:6379/0 \
    TUNE_E2E_DECK=<deck name with no saved recommendation> python -m unittest tests.test_tune_persistence_e2e
The deck is generated on first visit (scenario 1), so use a deck that has never been tuned.
"""
import json
import os
import unittest
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = os.getenv("TUNE_E2E_BASE_URL", "")
REDIS_URL = os.getenv("TUNE_E2E_REDIS_URL", "")
DECK = os.getenv("TUNE_E2E_DECK", "")


def _api(path, method="GET", body=None):
    req = Request(BASE + quote(path, safe="/?=&:"), method=method,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"content-type": "application/json"})
    with urlopen(req) as resp:
        return json.loads(resp.read())


@unittest.skipUnless(BASE and REDIS_URL and DECK, "set TUNE_E2E_BASE_URL, TUNE_E2E_REDIS_URL, TUNE_E2E_DECK")
class TestTunePersistenceE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        import redis
        cls.r = redis.from_url(REDIS_URL, decode_responses=True)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()
        cls.page_url = BASE + "/deck/" + quote(DECK) + "/tune"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def _open(self):
        pg = self.browser.new_page()
        pg.goto(self.page_url)
        pg.wait_for_selector("#fixes")
        pg.wait_for_function("document.querySelector('#recompute') && !document.querySelector('#recompute').disabled")
        pg.wait_for_timeout(500)
        return pg

    def _active(self):
        return _api("/api/decks/" + DECK + "/tune/saved")["recommendation"]

    def _records(self):
        did = self._active()["deck_id"]
        ids = self.r.lrange("tunerec:index:" + did, 0, -1)
        return [json.loads(self.r.get("tunerec:rec:" + i)) for i in ids]

    def test_generate_persist_recompute_archive(self):
        # 1. First visit generates and saves; navigating away and back shows the same one.
        pg = self._open()
        first = self._active()
        self.assertEqual(first["status"], "active")
        fixes = pg.inner_text("#fixes")
        pg.goto(BASE + "/decks")
        pg.wait_for_timeout(500)
        pg = self._open()
        self.assertEqual(self._active()["id"], first["id"])
        self.assertEqual(pg.inner_text("#fixes"), fixes)

        # 2. Reload keeps it.
        pg.reload()
        pg.wait_for_selector("#fixes")
        self.assertEqual(self._active()["id"], first["id"])

        # 3. Recompute replaces the displayed result with a new record and timestamp.
        with pg.expect_response(lambda resp: "/tune/recompute" in resp.url) as resp_info:
            pg.click("#recompute")
        self.assertEqual(resp_info.value.status, 200)
        pg.wait_for_timeout(1500)
        new = self._active()
        self.assertNotEqual(new["id"], first["id"])
        self.assertGreaterEqual(new["created_at"], first["created_at"])
        self.assertIn("ARCHIVED", pg.inner_text("#tuneGenerated").upper())

        # 4. Exactly one active record; the previous one is archived, not deleted.
        recs = self._records()
        active = [x for x in recs if x["status"] == "active"]
        self.assertEqual([x["id"] for x in active], [new["id"]])
        old = next(x for x in recs if x["id"] == first["id"])
        self.assertEqual(old["status"], "archived")
        self.assertIsNotNone(old["archived_at"])
        self.assertIsNotNone(old.get("payload"))
        history = _api("/api/decks/" + DECK + "/tune/history")["history"]
        self.assertIn(first["id"], [h["id"] for h in history])
        self.assertNotIn(new["id"], [h["id"] for h in history])


if __name__ == "__main__":
    unittest.main()
