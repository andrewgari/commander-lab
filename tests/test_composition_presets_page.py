"""t_64d0b570: UI layer for the Composition Presets page + per-deck picker.

Backend CRUD/selection endpoints already exist (PR #210, t_1c738f68) and are
covered by tests/test_composition_presets_api.py. This file only covers the
new page route and that deck_composition.html wires the picker markup.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("tests", 1)[0].rstrip("/"))

from fastapi.testclient import TestClient

from src.server.app.main import app


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, **kw):
        self.kv[key] = value
        return True

    def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    def delete(self, *keys):
        n = 0
        for k in keys:
            if self.kv.pop(k, None) is not None:
                n += 1
        return n

    def keys(self, pattern="*"):
        prefix = pattern.rstrip("*")
        return [k for k in self.kv if k.startswith(prefix)]


class TestCompositionPresetsPage(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.p = patch("src.server.app.main.r", self.r)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.client = TestClient(app)

    def test_page_200(self):
        resp = self.client.get("/composition/presets")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])

    def test_page_mentions_composition_presets(self):
        resp = self.client.get("/composition/presets")
        body = resp.text
        self.assertIn("Composition Presets", body)
        self.assertIn("cpForm", body)  # the create/edit form
        self.assertIn("cpCompareTable", body)  # the side-by-side comparison

    def test_nav_links_to_composition_presets(self):
        # The page shows up in the global nav from any page.
        resp = self.client.get("/composition/presets")
        self.assertIn('href="/composition/presets"', resp.text)


class TestDeckCompositionPickerMarkup(unittest.TestCase):
    """The deck composition section (folded into Stats, NAV_IA §3.2) carries the
    per-deck preset picker form; the old /composition route 301s to it."""

    def setUp(self):
        self.r = FakeRedis()
        deck = {
            "id": 777, "registry_id": "archidekt:777", "source": "archidekt",
            "source_id": 777, "name": "Test Deck", "status": "physical",
            "commanders": ["Atraxa, Grand Unifier"],
            "cards": [{"name": "Atraxa, Grand Unifier", "quantity": 1}],
        }
        import json
        self.r.set("decks", json.dumps([deck]))
        self.p = patch("src.server.app.main.r", self.r)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.client = TestClient(app)

    def test_picker_form_present(self):
        with patch("src.server.app.main._composition_page_context", return_value={"selection": None, "preset": None, "slots": []}):
            resp = self.client.get("/deck/Test Deck/composition")
        self.assertEqual(resp.status_code, 200)  # 301 -> /stats#composition
        body = resp.text
        self.assertIn("compPickerForm", body)
        self.assertIn("compModeAuto", body)
        self.assertIn("compModePinned", body)
        self.assertIn("compPresetSelect", body)
        self.assertIn("compOverrides", body)
        self.assertIn('href="/composition/presets"', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
