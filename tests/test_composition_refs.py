"""Ref catalog for the composition Ref combobox: validator parity + scope isolation."""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("tests", 1)[0].rstrip("/"))

from fastapi.testclient import TestClient

from src.server.domain.analytics import composition_models as cm
from src.server.domain.analytics import composition_refs as cr
from src.server.app.main import app
from tests.test_composition_presets_page import FakeRedis


def _expected(scope):
    exp = {f"core:{i}" for i in cm.CORE_IDS} | {f"group:{i}" for i in cm.GROUP_IDS}
    exp |= {f"type:{i}" for i in cm.TYPE_IDS} | {f"facet:{i}" for i in cm.FACET_IDS}
    exp |= {f"theme:{t}" for t in cm._themes()}
    return exp


class TestRefCatalog(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.r.set("categories:global", json.dumps([{"id": "g-abc", "name": "Pingers"}]))
        self.r.set("categories:deck:archidekt:777", json.dumps([{"id": "d-xyz", "name": "Mine"}]))
        self.r.set("decks", json.dumps([{"id": 777, "registry_id": "archidekt:777", "name": "T", "cards": []}]))

    def test_both_directions(self):
        for scope in ("preset", "deck"):
            refs = {e["ref"] for e in cr.enumerate_refs(scope, self.r, "archidekt:777")}
            for ref in refs:
                self.assertIsNone(cm.check_ref(ref, scope), ref)
            self.assertTrue(_expected(scope) <= refs)
            self.assertEqual(refs - _expected(scope), {"cat:g-abc"} | ({"cat:d-xyz"} if scope == "deck" else set()))

    def test_endpoint_scope_isolation_and_errors(self):
        with patch("src.server.app.main.r", self.r):
            c = TestClient(app)
            pre = {e["ref"] for e in c.get("/api/composition/refs?scope=preset").json()["refs"]}
            self.assertIn("cat:g-abc", pre)
            self.assertNotIn("cat:d-xyz", pre)
            dk = {e["ref"] for e in c.get("/api/composition/refs?scope=deck&deck_id=777").json()["refs"]}
            self.assertIn("cat:d-xyz", dk)
            self.assertEqual(c.get("/api/composition/refs?scope=x").status_code, 400)
            self.assertEqual(c.get("/api/composition/refs?scope=deck").status_code, 400)
            self.assertEqual(c.get("/api/composition/refs?scope=deck&deck_id=nope").status_code, 404)

    def test_aliases_and_validation_unchanged(self):
        by = {e["ref"]: e for e in cr.enumerate_refs("preset")}
        self.assertEqual(by["core:card_advantage"]["label"], "Card Draw")
        self.assertIn("wrath", by["core:board_wipe"]["aliases"])
        self.assertIn("draw", by["core:card_advantage"]["aliases"])
        for bad in ("ramp", "card_draw", "core:card_draw"):
            self.assertEqual(cm.check_ref(bad)["code"], "E3")


if __name__ == "__main__":
    unittest.main()
