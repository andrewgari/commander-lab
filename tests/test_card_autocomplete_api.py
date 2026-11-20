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
        from src.server.domain.inventory import card_search
        self.card_search = card_search
        card_search._reset_cooldown()
        self.addCleanup(card_search._reset_cooldown)
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
        self.assertIn("api.scryfall.com/cards/autocomplete", get.call_args_list[0][0][0])

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

    def test_default_scope_enriches_cards_missing_from_local_meta(self):
        """QA regression: names Scryfall returns that are NOT in local
        card_meta must still carry the full contract (uid/image/type_line/...),
        shaped like the commander scope."""
        full = {
            "id": "atraxa-uid", "name": "Atraxa, Praetors' Voice",
            "type_line": "Legendary Creature \u2014 Phyrexian Angel Horror",
            "colors": ["W", "U", "B", "G"], "color_identity": ["W", "U", "B", "G"],
            "keywords": ["Flying", "Vigilance", "Deathtouch", "Lifelink"],
            "mana_cost": "{G}{W}{U}{B}",
            "image_uris": {"art_crop": "https://img/atraxa.jpg"},
        }

        def fake_get(url, **kwargs):
            if url.endswith("/cards/autocomplete"):
                return Mock(status_code=200, json=lambda: {"data": ["Atraxa, Praetors' Voice"]})
            self.assertTrue(url.endswith("/cards/search"))
            return Mock(status_code=200, json=lambda: {"data": [full]})

        with patch.object(self.app_module.httpx, "get", side_effect=fake_get) as get:
            payload = self.client.get("/api/cards/autocomplete?q=atraxa").json()
        row = payload["results"][0]
        self.assertEqual(row["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(row["uid"], "atraxa-uid")
        self.assertEqual(row["image"], "https://img/atraxa.jpg")
        self.assertEqual(row["type_line"], full["type_line"])
        self.assertEqual(row["colors"], ["W", "U", "B", "G"])
        self.assertEqual(row["color_identity"], ["W", "U", "B", "G"])
        self.assertEqual(row["keywords"], full["keywords"])
        self.assertEqual(row["mana_cost"], "{G}{W}{U}{B}")
        self.assertEqual(get.call_count, 2)  # one names call + ONE batched lookup
        # The enriched result is cached.
        cached = json.loads(self.r.get("cards:autocomplete:cards:atraxa"))
        self.assertEqual(cached["results"][0]["uid"], "atraxa-uid")

    def test_enrichment_failure_falls_back_to_local_and_is_not_cached(self):
        self.r.set("card_meta:Sol Ring", json.dumps({"type": "Artifact"}))

        def fake_get(url, **kwargs):
            if url.endswith("/cards/autocomplete"):
                return Mock(status_code=200, json=lambda: {"data": ["Sol Ring"]})
            raise RuntimeError("scryfall search down")

        with patch.object(self.app_module.httpx, "get", side_effect=fake_get):
            payload = self.client.get("/api/cards/autocomplete?q=sol").json()
        self.assertEqual(payload["results"][0]["type_line"], "Artifact")
        self.assertIsNone(self.r.get("cards:autocomplete:cards:sol"))
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["source"], "local")

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
        self.assertEqual(get.call_count, 2)  # names + one batched enrichment lookup
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
        self.assertEqual(get.call_count, 2)  # names + enrichment lookup
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
        self.assertEqual(get.call_count, 2)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Refetched"])
        # 2-minute-old EMPTY envelope: expired (60s TTL), refetch.
        self.r.set("cards:autocomplete:cards:emptyold",
                   json.dumps({"cached_at": now - 120, "results": []}))
        with patch.object(self.app_module.httpx, "get",
                          return_value=self._scryfall_names(["Recovered"])) as get:
            res = self.client.get("/api/cards/autocomplete?q=emptyold")
        self.assertEqual(get.call_count, 2)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Recovered"])
        # Fresh empty envelope: still served (60s TTL).
        self.r.set("cards:autocomplete:cards:emptyfresh",
                   json.dumps({"cached_at": now - 10, "results": []}))
        with patch.object(self.app_module.httpx, "get") as get:
            res = self.client.get("/api/cards/autocomplete?q=emptyfresh")
        self.assertEqual(get.call_count, 0)
        self.assertEqual(res.json()["results"], [])

    # -- upstream rate limiting / outage (429, 5xx) --------------------------
    def _seed_spider_commanders(self):
        for n in ("Spider-Man, Web-Slinger", "Spider-Gwen, Ghost-Spider"):
            self.r.set(f"card_meta:{n}", json.dumps({"type": "Legendary Creature — Spider Hero"}))
        self.r.set("card_meta:Spider Spawning", json.dumps({"type": "Sorcery"}))

    def _status(self, code, retry_after=None):
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
        return Mock(status_code=code, headers=headers, json=lambda: {})

    def test_429_commanders_falls_back_to_local_degraded_and_uncached(self):
        self._seed_spider_commanders()
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, 60)):
            res = self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
        payload = res.json()
        self.assertIs(payload["degraded"], True)
        self.assertEqual(payload["source"], "local")
        self.assertEqual([r["name"] for r in payload["results"]],
                         ["Spider-Gwen, Ghost-Spider", "Spider-Man, Web-Slinger"])
        self.assertIsNone(self.r.get("cards:autocomplete:commanders:spid"))

    # -- offline commander pool (P0: local fallback returned 0 rows) ---------
    COMMANDER_POOL = [
        "Atraxa, Praetors' Voice", "Atraxa, Grand Unifier", "Krenko, Mob Boss",
        "Muldrotha, the Gravetide", "Kenrith, the Returned King",
        "The Gitrog Monster", "Spider-Man, Web-Slinger", "Spider-Gwen, Ghost-Spider",
        "Spiderling Swarm Lord",
    ] + [f"Spider Fixture {i}" for i in range(12)]

    def _seed_pool(self):
        # Nothing in card_meta, no eligible set: only the durable pool exists.
        self.r.set("cards:commander_pool:names", json.dumps(self.COMMANDER_POOL))

    def _offline(self, q, status=429):
        with patch.object(self.app_module.httpx, "get", return_value=self._status(status, 60)):
            res = self.client.get(f"/api/brew/commanders?q={q}")
        return res.json()

    def test_offline_exact_names_return_matches_from_local_pool(self):
        self._seed_pool()
        for q, expect in (("atraxa", "Atraxa, Praetors' Voice"), ("krenko", "Krenko, Mob Boss"),
                          ("muldrotha", "Muldrotha, the Gravetide")):
            self.card_search._reset_cooldown()
            payload = self._offline(q)
            self.assertEqual(payload["source"], "local", q)
            self.assertIs(payload["degraded"], True, q)
            self.assertIn(expect, [x["name"] for x in payload["results"]], q)
            self.assertIsNone(self.r.get(f"cards:autocomplete:commanders:{q}"))

    def test_offline_typos_find_the_right_family(self):
        self._seed_pool()
        for q, expect in (("spidr", "Spider-Man, Web-Slinger"), ("atrxa", "Atraxa, Grand Unifier"),
                          ("gitrg", "The Gitrog Monster"), ("kenith", "Kenrith, the Returned King")):
            self.card_search._reset_cooldown()
            names = [x["name"] for x in self._offline(q)["results"]]
            self.assertIn(expect, names, (q, names))
        self.card_search._reset_cooldown()
        self.assertEqual(self._offline("zzqxvwk")["results"], [])

    def test_offline_returns_every_match_not_just_eight(self):
        self._seed_pool()
        payload = self._offline("spider")
        self.assertGreater(len(payload["results"]), 8)
        self.assertEqual(len(payload["results"]), 15)

    def test_offline_pool_rows_are_selectable_contract_rows(self):
        self._seed_pool()
        row = self._offline("krenko")["results"][0]
        self.assertEqual(row["name"], "Krenko, Mob Boss")
        for key in ("uid", "oracle_id", "image", "type_line", "color_identity",
                    "colors", "keywords", "mana_cost"):
            self.assertIn(key, row)

    def test_healthy_search_seeds_the_pool_without_blocking_the_request(self):
        self.assertEqual(self.card_search._read_pool(self.r), [])
        self.card_search._pool_refreshing = False
        page = {"data": [_card("Atraxa, Praetors' Voice"), _card("Krenko, Mob Boss")], "has_more": False}
        ok = Mock(status_code=200, headers={}, json=lambda: page)
        with patch.object(self.app_module.httpx, "get", return_value=ok):
            res = self.client.get("/api/brew/commanders?q=atraxa")
            self.assertEqual(res.json()["source"], "scryfall")
            # TestClient loop is closed after the request; run the refresh directly.
            self.card_search._refresh_pool(self.r)
        self.assertEqual(self.card_search._read_pool(self.r),
                         ["Atraxa, Praetors' Voice", "Krenko, Mob Boss"])

    def test_failed_pool_refresh_writes_nothing_and_backs_off(self):
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, 60)):
            self.card_search._refresh_pool(self.r)
        self.assertEqual(self.card_search._read_pool(self.r), [])
        self.assertIsNotNone(self.r.get("cards:commander_pool:attempted_at"))
        self.assertIsNone(self.r.get("cards:commander_pool:refreshed_at"))

    def test_failed_refresh_of_stale_pool_retries_after_backoff_not_a_week(self):
        import time as _t
        self.r.set(self.card_search.POOL_KEY, json.dumps(["Old Commander"]))
        stale = str(_t.time() - self.card_search.POOL_REFRESH_AFTER_SECONDS - 10)
        self.r.set(self.card_search.POOL_REFRESHED_KEY, stale)
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, 60)):
            self.card_search._refresh_pool(self.r)
        # failure must not advance freshness
        self.assertEqual(self.r.get(self.card_search.POOL_REFRESHED_KEY), stale)
        # ...and a retry after the backoff window runs again
        self.r.set(self.card_search.POOL_ATTEMPT_KEY, str(_t.time() - 301))
        self.card_search._reset_cooldown()
        page = {"data": [_card("New Commander")], "has_more": False}
        ok = Mock(status_code=200, headers={}, json=lambda: page)
        with patch.object(self.app_module.httpx, "get", return_value=ok):
            self.card_search._refresh_pool(self.r)
        self.assertEqual(self.card_search._read_pool(self.r), ["New Commander"])

    def test_healthy_commander_search_returns_more_than_40(self):
        cards = [_card(f"Bulk Fixture {i:03d}") for i in range(60)]
        ok = Mock(status_code=200, headers={}, json=lambda: {"data": cards, "has_more": False})
        with patch.object(self.app_module.httpx, "get", return_value=ok):
            res = self.client.get("/api/brew/commanders?q=bulk&limit=100")
        self.assertEqual(len(res.json()["results"]), 60)

    def test_commanders_limit_above_20_is_honoured(self):
        cards = [_card(f"Spider Fixture {i:02d}") for i in range(35)]
        ok = Mock(status_code=200, headers={}, json=lambda: {"data": cards, "has_more": False})
        with patch.object(self.app_module.httpx, "get", return_value=ok):
            res = self.client.get("/api/brew/commanders?q=spider")
            res2 = self.client.get("/api/cards/autocomplete?q=spider&commanders_only=true&limit=100")
        self.assertEqual(len(res.json()["results"]), 35)
        self.assertEqual(len(res2.json()["results"]), 35)

    def test_5xx_commanders_falls_back_to_local_degraded_and_uncached(self):
        self._seed_spider_commanders()
        for code in (500, 503):
            self.card_search._reset_cooldown()
            with patch.object(self.app_module.httpx, "get", return_value=self._status(code)):
                res = self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
            self.assertIs(res.json()["degraded"], True)
            self.assertEqual(len(res.json()["results"]), 2)
        self.assertIsNone(self.r.get("cards:autocomplete:commanders:spid"))

    def test_429_default_scope_falls_back_to_local_degraded(self):
        self._seed_spider_commanders()
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429)):
            res = self.client.get("/api/cards/autocomplete?q=spid")
        self.assertIs(res.json()["degraded"], True)
        self.assertGreaterEqual(len(res.json()["results"]), 2)
        self.assertIsNone(self.r.get("cards:autocomplete:cards:spid"))

    def test_retry_after_backs_off_without_hammering_scryfall(self):
        self._seed_spider_commanders()
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, 30)) as get:
            self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
            first = get.call_count
            res = self.client.get("/api/cards/autocomplete?q=spide&commanders_only=true")
        self.assertEqual(first, 1)
        self.assertEqual(get.call_count, 1)  # cooldown: no further upstream call
        self.assertIs(res.json()["degraded"], True)
        self.assertEqual(len(res.json()["results"]), 2)

    def test_backoff_is_bounded_and_recovers(self):
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, 99999)):
            self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
        remaining = self.card_search._cooldown_until - time.monotonic()
        self.assertLessEqual(remaining, self.card_search.COOLDOWN_MAX_SECONDS + 1)
        # Cooldown elapsed -> Scryfall is used again and the success is cached.
        self.card_search._cooldown_until = time.monotonic() - 1
        ok = Mock(status_code=200, headers={}, json=lambda: {
            "data": [_card("Spider-Man, Web-Slinger")], "has_more": False})
        with patch.object(self.app_module.httpx, "get", return_value=ok):
            res = self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
        self.assertIs(res.json()["degraded"], False)
        self.assertEqual([r["name"] for r in res.json()["results"]], ["Spider-Man, Web-Slinger"])
        self.assertIsNotNone(self.r.get("cards:autocomplete:commanders:spid"))

    def test_retry_after_http_date_is_honored(self):
        from email.utils import format_datetime
        from datetime import datetime, timedelta, timezone
        when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45), usegmt=True)
        with patch.object(self.app_module.httpx, "get", return_value=self._status(429, when)):
            self.client.get("/api/cards/autocomplete?q=spid&commanders_only=true")
        remaining = self.card_search._cooldown_until - time.monotonic()
        self.assertGreater(remaining, 30)
        self.assertLessEqual(remaining, 46)

    def test_catalog_429_is_degraded_cooldown_and_uncached(self):
        self._seed_spider_commanders()

        def fake_get(url, **kwargs):
            if url.endswith("/catalog/card-names"):
                return self._status(429, 30)
            return Mock(status_code=404, headers={}, json=lambda: {})
        with patch.object(self.app_module.httpx, "get", side_effect=fake_get):
            res = self.client.get("/api/cards/autocomplete?q=spidr&commanders_only=true")
        self.assertIs(res.json()["degraded"], True)
        self.assertIsNone(self.r.get("cards:autocomplete:commanders:spidr"))
        self.assertGreater(self.card_search._cooldown_until, time.monotonic())

    def test_genuine_empty_is_not_degraded(self):
        # Scryfall answers 404 for every probe: a real "no match", not an outage.
        notfound = Mock(status_code=404, headers={}, json=lambda: {})
        with patch.object(self.app_module.httpx, "get", return_value=notfound):
            res = self.client.get("/api/cards/autocomplete?q=zzzzqq&commanders_only=true")
        self.assertEqual(res.json()["results"], [])
        self.assertIs(res.json()["degraded"], False)

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

    # -- typo tolerance (default scope) -----------------------------------------
    CATALOG = ["Biscuit, Tavern Cook", "Spider-Man, Web Slinger", "Ancient Spider", "Sol Ring",
               "Atraxa, Praetors' Voice", "Atraxa, Grand Unifier", "Lightning Bolt", "Bolt Hound"]

    def _typo_get(self, names=None):
        """Scryfall double: autocomplete finds nothing for the misspelling, the
        card-names catalog holds the real names, search/enrichment is empty."""
        catalog = names if names is not None else self.CATALOG

        def fake_get(url, **kwargs):
            if url.endswith("/cards/autocomplete"):
                return Mock(status_code=200, json=lambda: {"data": []})
            if url.endswith("/catalog/card-names"):
                return Mock(status_code=200, json=lambda: {"data": catalog})
            return Mock(status_code=200, json=lambda: {"data": []})
        return fake_get

    def test_misspelling_recovers_close_names_when_scryfall_finds_none(self):
        for typo, want in (("buscuit", "Biscuit, Tavern Cook"), ("spidr", "Ancient Spider"),
                           ("atraxxa", "Atraxa, Grand Unifier"), ("sol rnig", "Sol Ring"),
                           ("lightnin bolt", "Lightning Bolt")):
            with patch.object(self.app_module.httpx, "get", side_effect=self._typo_get()):
                payload = self.client.get(f"/api/cards/autocomplete?q={typo}").json()
            names = [r["name"] for r in payload["results"]]
            self.assertIn(want, names, (typo, names))
            self.assertEqual(payload["degraded"], False)

    def test_misspelling_best_match_is_first(self):
        with patch.object(self.app_module.httpx, "get", side_effect=self._typo_get()):
            names = [r["name"] for r in self.client.get("/api/cards/autocomplete?q=buscuit").json()["results"]]
        self.assertEqual(names[0], "Biscuit, Tavern Cook")

    def test_gibberish_stays_empty_not_random_cards(self):
        with patch.object(self.app_module.httpx, "get", side_effect=self._typo_get()):
            payload = self.client.get("/api/cards/autocomplete?q=zzqxv").json()
        self.assertEqual(payload["results"], [])

    def test_catalog_is_fetched_once_then_cached_in_redis(self):
        fake = self._typo_get()
        with patch.object(self.app_module.httpx, "get", side_effect=fake) as get:
            self.client.get("/api/cards/autocomplete?q=buscuit")
            self.client.get("/api/cards/autocomplete?q=spidr")
        catalog_calls = [c for c in get.call_args_list if c[0][0].endswith("/catalog/card-names")]
        self.assertEqual(len(catalog_calls), 1)
        self.assertIsNotNone(self.r.get("cards:catalog:names"))

    def test_catalog_failure_degrades_quietly_to_no_matches(self):
        def fake_get(url, **kwargs):
            if url.endswith("/cards/autocomplete"):
                return Mock(status_code=200, json=lambda: {"data": []})
            raise RuntimeError("catalog down")
        with patch.object(self.app_module.httpx, "get", side_effect=fake_get):
            res = self.client.get("/api/cards/autocomplete?q=buscuit")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["results"], [])
        # A catalog outage must not poison the cache with an empty list.
        self.assertIsNone(self.r.get("cards:catalog:names"))

    def test_exact_prefix_hit_does_not_touch_the_catalog(self):
        with patch.object(self.app_module.httpx, "get", side_effect=self._typo_get()) as get:
            def fake_get(url, **kwargs):
                if url.endswith("/cards/autocomplete"):
                    return Mock(status_code=200, json=lambda: {"data": ["Sol Ring"]})
                return Mock(status_code=200, json=lambda: {"data": []})
            get.side_effect = fake_get
            self.client.get("/api/cards/autocomplete?q=sol")
        self.assertFalse([c for c in get.call_args_list if c[0][0].endswith("/catalog/card-names")])

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
