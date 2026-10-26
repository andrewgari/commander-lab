"""Tests for lab_tags.py (learned vs manual provenance on global Lab Tags)
and the /api/card/{name}/tags endpoints that expose it.

Run: python -m pytest tests/test_lab_tags_source.py
"""
import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.app.main as app_module  # noqa: E402
import src.server.domain.tags.lab_tags as lab_tag_store  # noqa: E402
from tests.test_deck_manage_integration import FakeRedis  # noqa: E402


class LabTagsSourceUnitTests(unittest.TestCase):
    """Pure lab_tags.py behavior, no HTTP."""

    def setUp(self):
        self.r = FakeRedis()

    def test_legacy_tag_defaults_to_learned(self):
        # No lab_tags_source entry at all -> every existing tag is "learned".
        out = lab_tag_store.with_source(self.r, "Sol Ring", ["Ramp"])
        self.assertEqual(out, [{"tag": "Ramp", "source": "learned"}])

    def test_set_source_round_trips(self):
        lab_tag_store.set_source(self.r, "Sol Ring", "Fast Mana", lab_tag_store.MANUAL)
        self.assertEqual(lab_tag_store.source_of(self.r, "Sol Ring", "Fast Mana"), "manual")
        out = lab_tag_store.with_source(self.r, "Sol Ring", ["Ramp", "Fast Mana"])
        self.assertEqual(out, [
            {"tag": "Ramp", "source": "learned"},
            {"tag": "Fast Mana", "source": "manual"},
        ])

    def test_invalid_source_coerces_to_manual(self):
        lab_tag_store.set_source(self.r, "Sol Ring", "Weird", "bogus")
        self.assertEqual(lab_tag_store.source_of(self.r, "Sol Ring", "Weird"), "manual")

    def test_clear_source_removes_entry_and_empty_card_bucket(self):
        lab_tag_store.set_source(self.r, "Sol Ring", "Fast Mana", lab_tag_store.MANUAL)
        lab_tag_store.clear_source(self.r, "Sol Ring", "Fast Mana")
        self.assertEqual(lab_tag_store.source_of(self.r, "Sol Ring", "Fast Mana"), "learned")
        self.assertEqual(lab_tag_store.get_source_map(self.r), {})

    def test_clear_source_missing_entry_is_a_noop(self):
        lab_tag_store.clear_source(self.r, "Sol Ring", "Nope")  # must not raise
        self.assertEqual(lab_tag_store.get_source_map(self.r), {})


class CardTagsEndpointSourceTests(unittest.TestCase):
    """Add/remove through the real /api/card/{name}/tags endpoints."""

    def setUp(self):
        self.r = FakeRedis()
        self.r.set("lab_tags", json.dumps({"Sol Ring": ["Ramp"]}))
        self._orig = app_module.r
        app_module.r = self.r
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.r = self._orig

    def test_get_shows_pre_existing_tag_as_learned(self):
        res = self.client.get("/api/card/Sol Ring/tags")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["lab_tags"], [{"tag": "Ramp", "source": "learned"}])

    def test_add_tag_is_manual(self):
        res = self.client.post("/api/card/Sol Ring/tags", json={"action": "add", "tag": "Fast Mana"})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertIn({"tag": "Fast Mana", "source": "manual"}, body["lab_tags"])

        # Persisted: a fresh GET shows the same source.
        res2 = self.client.get("/api/card/Sol Ring/tags")
        self.assertIn({"tag": "Fast Mana", "source": "manual"}, res2.json()["lab_tags"])

    def test_remove_tag_clears_its_source_marker(self):
        self.client.post("/api/card/Sol Ring/tags", json={"action": "add", "tag": "Fast Mana"})
        res = self.client.post("/api/card/Sol Ring/tags", json={"action": "remove", "tag": "Fast Mana"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("Fast Mana", [t["tag"] for t in res.json()["lab_tags"]])
        self.assertEqual(lab_tag_store.get_source_map(self.r), {})

    def test_remove_learned_tag_then_readd_is_manual(self):
        self.client.post("/api/card/Sol Ring/tags", json={"action": "remove", "tag": "Ramp"})
        self.client.post("/api/card/Sol Ring/tags", json={"action": "add", "tag": "Ramp"})
        res = self.client.get("/api/card/Sol Ring/tags")
        self.assertEqual(res.json()["lab_tags"], [{"tag": "Ramp", "source": "manual"}])


if __name__ == "__main__":
    unittest.main()
