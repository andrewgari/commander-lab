"""Contract tests for GET /api/cards/autocomplete (shared card-name
autocomplete, spec: docs/architecture/shared-card-autocomplete.md).

Covers: response contract shape, q < 2 short-circuit, commanders_only
legality handling, the cache policy learned from the brew commander incident
(failure-derived results are never cached; legacy bare-list entries ignored;
non-empty 24h / empty 60s envelopes), and the degraded local fallback.

Run: python -m unittest tests.test_card_autocomplete_api
"""
import json
import os
import sys
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from tests.test_brew import FakeRedis


def _card(name, type_line="Legendary Creature — Spider Hero", legality="legal"):
    return {
        "id": name.lower().replace(" ", "-"), "name": name, "type_line": type_line,
        "legalities": {"commander": legality}, "colors": [], "color_identity": [],
        "keywords": [], "mana_cost": "", "image_uris": {},
    }


class TestCardAutocompleteAPI(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as app_module
        self.app_module = app_module
        self.r = FakeRedis()
        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    # -- contract shape -------------------------------------------------
    def test_short_query_returns_empty_results(self):
        res = self.client.get("/api/cards/autocomplete?q=a")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["results"], [])
        res = self.client.get("/api/cards/autocomplete?q=%20%20")
        self.assertEqual(res.json()["results"], [])

    def test_default_path_contract_shape(self):
        body = {"data": ["Atraxa, Praetors' Voice", "Atraxa, Grand Unifier"]}
        with patch.object(self.app_module.httpx, "get",
                          return_value=Mock(status_code=200, json=lambda: body)) as get:
            res = self.client.get("/api/cards/autocomplete?q=atraxa")
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertGreaterEqual(len(payload["results"]), 1)
        self.assertEqual(payload["source"], "scryfall")
        self.assertIs(payload["degraded"], False)
        for row in payload["results"]:
            for key in ("name", "uid", "image", "type_line", "color_identity",
                        "colors", "keywords", "mana_cost"):
                self.assertIn(key, row)
        # The default path asks Scryfall's autocomplete endpoint for names.
        url = get.call_args[0][0]
        self.assertIn("api.scryfall.com/cards/autocomplete", url)

    def test_local_meta_enriches_scryfall_names(self):
        self.r.set("card_meta:Atraxa, Praetors' Voice", json.dumps(
            {"type": "Legendary Creature — Phyrexian Angel", "color": "WUBG", "keywords": ["Flying"]}))
        body = {"data": ["Atraxa, Praetors' Voice"]}
        with patch.object(self.app_module.httpx, "get",
                          return_value=Mock(status_code=200, json=lambda: body)):
            row = self.client.get("/api/cards/autocomplete?q=atraxa").json()["results"][0]
        self.assertEqual(row["type_line"], "Legendary Creature — Phyrexian Angel")
        self.assertEqual(row["keywords"], ["Flying"])
        self.assertEqual(row["color_identity"], ["W", "U", "B", "G"])

    def test_limit_is_applied_and_bounded(self):
        body = {"data": [f"Card {i}" for i in range(20)]}
        with patch.object(self.app_module.httpx, "get",
                          return_value=Mock(status_code=200, json=lambda: body)):
            res = self.client.get("/api/cards/autocomplete?q=ca&limit=3")
            self.assertEqual(len(res.json()["results"]), 3)
            res2 = self.client.get("/api/cards/autocomplete?q=cb&limit=500")
            self.assertLessEqual(len(res2.json()["results"]), 20)

    # -- commanders_only -------------------------------------------------
    def test_commanders_only_uses_commander_legal_scryfall_query(self):
        fresh = _card("Atraxa, Praetors' Voice")
        with patch.object(self.app_module.httpx, "get", return_value=Mock(
            status_code=200, json=lambda: {"data": [fresh], "has_more": False}
        )) as get:
            res = self.client.get("/api/cards/autocomplete?q=atraxa&commanders_only=true")
        payload = res.json()
        self.assertEqual(payload["results"][0]["name"], "Atraxa, Praetors' Voice")
        self.assertIs(payload["degraded"], False)
        # Legality is enforced by the Scryfall query itself.
        sent_params = get.call_args[1].get("params") or {}
        self.assertIn("is:commander legal:commander", sent_params.get("q", ""))

    def test_commanders_only_short_query(self):
        res = self.client.get("/api/cards/autocomplete?q=a&commanders_only=true")
        self.assertEqual(res.json()["results"], [])

    # -- cache policy ------------------------------------------------------
    def _scryfall_names(self, names):
        return Mock(status_code=200, json=lambda: {"data": names})

    def test_failure_result_is_never_cached(self):
        # 1. Scryfall down -> degraded local answer (empty here), not cached.
        with patch.object(self.app_module.httpx, "get", side_effect=RuntimeError("down")):
            res = self.client.get("/api/cards/autocomplete?q=atraxa")
        self.assertIs(res.json()["degraded"], True)
        self.assertIsNone(self.r.get("cards:autocomplete:cards:atraxa"))
        # 2. Scryfall back -> the success is served fresh, not the stale empty.
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Atraxa, Praetors' Voice"])) as get:
            res = self.client.get("/api/cards/autocomplete?q=atraxa")
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Atraxa, Praetors' Voice"])
        self.assertEqual(get.call_count, 1)
        # 3. The success IS cached: a repeat query does not re-hit Scryfall.
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Should Not Appear"])) as get2:
            res = self.client.get("/api/cards/autocomplete?q=atraxa")
        self.assertEqual(get2.call_count, 0)
        self.assertEqual(res.json()["source"], "cache")
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Atraxa, Praetors' Voice"])

    def test_legacy_bare_list_cache_entry_is_ignored(self):
        self.r.set("cards:autocomplete:cards:foo", json.dumps(["Legacy Name"]))
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Fresh Name"])) as get:
            res = self.client.get("/api/cards/autocomplete?q=foo")
        self.assertEqual(get.call_count, 1)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Fresh Name"])

    def test_nonempty_envelope_serves_for_24h_empty_only_60s(self):
        now = time.time()
        # Fresh non-empty envelope: served from cache.
        self.r.set("cards:autocomplete:cards:cached",
                   json.dumps({"cached_at": now - 3600, "results": [{"name": "Cached Card"}]}))
        with patch.object(self.app_module.httpx, "get") as get:
            res = self.client.get("/api/cards/autocomplete?q=cached")
        self.assertEqual(get.call_count, 0)
        self.assertEqual(res.json()["source"], "cache")
        # 25h-old non-empty envelope: expired, refetch.
        self.r.set("cards:autocomplete:cards:old",
                   json.dumps({"cached_at": now - (25 * 3600), "results": [{"name": "Stale Card"}]}))
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Refetched"])) as get:
            res = self.client.get("/api/cards/autocomplete?q=old")
        self.assertEqual(get.call_count, 1)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Refetched"])
        # 2-minute-old EMPTY envelope: expired (60s TTL), refetch.
        self.r.set("cards:autocomplete:cards:emptyold",
                   json.dumps({"cached_at": now - 120, "results": []}))
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Recovered"])) as get:
            res = self.client.get("/api/cards/autocomplete?q=emptyold")
        self.assertEqual(get.call_count, 1)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Recovered"])
        # Fresh empty envelope: still served (60s TTL).
        self.r.set("cards:autocomplete:cards:emptyfresh",
                   json.dumps({"cached_at": now - 10, "results": []}))
        with patch.object(self.app_module.httpx, "get") as get:
            res = self.client.get("/api/cards/autocomplete?q=emptyfresh")
        self.assertEqual(get.call_count, 0)
        self.assertEqual(res.json()["results"], [])

    # -- degraded path -------------------------------------------------------
    def test_degraded_path_substring_matches_local_card_meta(self):
        self.r.set("card_meta:Atraxa, Praetors' Voice", json.dumps(
            {"type": "Legendary Creature — Phyrexian Angel", "color": "WUBG"}))
        self.r.set("card_meta:Atraxa, Grand Unifier", json.dumps(
            {"type": "Legendary Creature — Phyrexian Angel", "color": "WUBG"}))
        self.r.set("card_meta:Unrelated Card", json.dumps({"type": "Instant"}))
        with patch.object(self.app_module.httpx, "get", side_effect=RuntimeError("down")):
            res = self.client.get("/api/cards/autocomplete?q=atraxa")
        payload = res.json()
        self.assertIs(payload["degraded"], True)
        self.assertEqual(payload["source"], "local")
        names = [r["name"] for r in payload["results"]]
        self.assertIn("Atraxa, Praetors' Voice", names)
        self.assertIn("Atraxa, Grand Unifier", names)
        self.assertNotIn("Unrelated Card", names)

    def test_degraded_commanders_only_returns_legendary_only(self):
        self.r.set("card_meta:Atraxa, Praetors' Voice", json.dumps(
            {"type": "Legendary Creature — Phyrexian Angel"}))
        self.r.set("card_meta:Atraxa's Fall", json.dumps({"type": "Instant"}))
        with patch.object(self.app_module.httpx, "get", side_effect=RuntimeError("down")):
            res = self.client.get("/api/cards/autocomplete?q=atraxa&commanders_only=true")
        payload = res.json()
        self.assertIs(payload["degraded"], True)
        names = [r["name"] for r in payload["results"]]
        self.assertEqual(names, ["Atraxa, Praetors' Voice"])

    def test_degraded_path_does_not_cache(self):
        self.r.set("card_meta:Kenrith, the Returned King", json.dumps(
            {"type": "Legendary Creature — Human Noble"}))
        with patch.object(self.app_module.httpx, "get", side_effect=RuntimeError("down")):
            self.client.get("/api/cards/autocomplete?q=kenrith&commanders_only=true")
        self.assertIsNone(self.r.get("cards:autocomplete:commanders:kenrith"))

    def test_never_raises_on_scryfall_error(self):
        with patch.object(self.app_module.httpx, "get", side_effect=RuntimeError("down")):
            res = self.client.get("/api/cards/autocomplete?q=anything")
            self.assertEqual(res.status_code, 200)
            res = self.client.get("/api/cards/autocomplete?q=anything&commanders_only=true")
            self.assertEqual(res.status_code, 200)

    # -- brew alias stays put ------------------------------------------------
    def test_brew_commanders_alias_delegates_to_shared_search(self):
        fresh = _card("The Gitrog Monster")
        with patch.object(self.app_module.httpx, "get", side_effect=[
            Mock(status_code=200, json=lambda: {"data": [], "has_more": False}),
            Mock(status_code=200, json=lambda: fresh),
        ]):
            res = self.client.get("/api/brew/commanders?q=gitrog")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["results"][0]["name"], "The Gitrog Monster")


if __name__ == "__main__":
    unittest.main()
