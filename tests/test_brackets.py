"""BR-1: Commander Brackets reference table + Game Changers list
(analytics/brackets.py, docs/BRACKETS_SPEC.md).

Scryfall is never hit: the fetch tests drive the real ScryfallClient through
an httpx.MockTransport, and Redis is an in-memory stub.

Run: python -m unittest tests.test_brackets
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import brackets as br  # noqa: E402
from src.server.infra.providers.scryfall import ScryfallClient  # noqa: E402
from src.server.infra.http import RetryConfig, SharedHttpClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class FakeRedis:
    """The two calls brackets.py makes, plus TTL bookkeeping."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl


class BrokenRedis:
    def get(self, key):
        raise ConnectionError("redis down")

    def setex(self, key, ttl, value):
        raise ConnectionError("redis down")


def _card(name, **extra):
    d = {"object": "card", "id": f"id-{name}", "oracle_id": f"o-{name}", "name": name, "game_changer": True}
    d.update(extra)
    return d


def _client(handler):
    http = SharedHttpClient(
        base_url="https://api.scryfall.com",
        transport=httpx.MockTransport(handler),
        enable_cache=False,
        retry_config=RetryConfig(max_retries=0),
    )
    return ScryfallClient(http_client=http)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


class BracketTableTests(unittest.TestCase):
    def test_five_brackets_in_order_with_official_names(self):
        self.assertEqual([b.number for b in br.BRACKETS], [1, 2, 3, 4, 5])
        self.assertEqual([b.name for b in br.BRACKETS],
                         ["Exhibition", "Core", "Upgraded", "Optimized", "cEDH"])

    def test_game_changer_limits(self):
        # Official: 1-2 none, 3 up to three, 4-5 unlimited (None).
        self.assertEqual([b.max_game_changers for b in br.BRACKETS], [0, 0, 3, None, None])

    def test_mass_land_denial(self):
        self.assertEqual([b.mass_land_denial for b in br.BRACKETS],
                         [br.NOT_ALLOWED, br.NOT_ALLOWED, br.NOT_ALLOWED, br.ALLOWED, br.ALLOWED])

    def test_extra_turns(self):
        self.assertEqual([b.extra_turns for b in br.BRACKETS],
                         [br.NOT_ALLOWED, br.NO_CHAINS, br.NO_CHAINS, br.ALLOWED, br.ALLOWED])

    def test_two_card_combos(self):
        self.assertEqual([b.two_card_combos for b in br.BRACKETS],
                         [br.NOT_ALLOWED, br.NOT_ALLOWED, br.LATE_GAME_ONLY, br.ALLOWED, br.ALLOWED])

    def test_turn_expectations_from_oct_2025_update(self):
        self.assertEqual([b.min_turns for b in br.BRACKETS], [9, 8, 6, 4, None])

    def test_every_bracket_has_intent_tutor_guidance_and_sources(self):
        for b in br.BRACKETS:
            self.assertTrue(b.intent.strip(), b.name)
            self.assertTrue(b.expectations, b.name)
            self.assertTrue(b.tutors.strip(), b.name)
            self.assertTrue(b.sources, b.name)
            for url in b.sources:
                self.assertTrue(url.startswith("https://"), url)

    def test_constraint_values_come_from_a_closed_vocabulary(self):
        for b in br.BRACKETS:
            for value in (b.mass_land_denial, b.extra_turns, b.two_card_combos):
                self.assertIn(value, br.CONSTRAINT_VALUES)

    def test_table_is_immutable_data(self):
        with self.assertRaises(Exception):
            br.BRACKETS[0].max_game_changers = 5  # frozen dataclass
        self.assertIsInstance(br.BRACKETS, tuple)

    def test_constraints_are_split_hard_vs_advisory(self):
        kinds = {c.key: c.kind for c in br.CONSTRAINTS}
        self.assertEqual(kinds["game_changers"], br.HARD)
        self.assertEqual(kinds["mass_land_denial"], br.HARD)
        self.assertEqual(kinds["extra_turns"], br.HARD)
        self.assertEqual(kinds["two_card_combos"], br.HARD)
        self.assertEqual(kinds["tutors"], br.ADVISORY)
        self.assertEqual(kinds["turn_expectation"], br.ADVISORY)
        self.assertEqual(kinds["intent"], br.ADVISORY)
        # every bracket field a constraint names exists on Bracket
        for c in br.CONSTRAINTS:
            self.assertTrue(hasattr(br.BRACKETS[0], c.field), c.field)


class BracketHelperTests(unittest.TestCase):
    def test_get_bracket_by_number_name_and_string(self):
        self.assertEqual(br.get_bracket(3).name, "Upgraded")
        self.assertEqual(br.get_bracket("3").name, "Upgraded")
        self.assertEqual(br.get_bracket("optimized").number, 4)
        self.assertEqual(br.get_bracket("CEDH").number, 5)
        self.assertEqual(br.get_bracket("Bracket 2").name, "Core")
        self.assertIsNone(br.get_bracket(0))
        self.assertIsNone(br.get_bracket(6))
        self.assertIsNone(br.get_bracket("nope"))
        self.assertIsNone(br.get_bracket(None))
        self.assertIsNone(br.get_bracket(True))

    def test_game_changers_allowed(self):
        self.assertTrue(br.game_changers_allowed(1, 0))
        self.assertFalse(br.game_changers_allowed(2, 1))
        self.assertTrue(br.game_changers_allowed(3, 3))
        self.assertFalse(br.game_changers_allowed(3, 4))
        self.assertTrue(br.game_changers_allowed(4, 40))
        self.assertTrue(br.game_changers_allowed(5, 99))
        with self.assertRaises(ValueError):
            br.game_changers_allowed(9, 0)
        with self.assertRaises(ValueError):
            br.game_changers_allowed(3, -1)

    def test_min_bracket_for_game_changer_count(self):
        self.assertEqual(br.min_bracket_for_game_changers(0), 1)
        self.assertEqual(br.min_bracket_for_game_changers(1), 3)
        self.assertEqual(br.min_bracket_for_game_changers(3), 3)
        self.assertEqual(br.min_bracket_for_game_changers(4), 4)
        with self.assertRaises(ValueError):
            br.min_bracket_for_game_changers(-1)

    def test_table_payload_is_json_serialisable(self):
        payload = br.brackets_payload()
        json.dumps(payload)
        self.assertEqual(len(payload["brackets"]), 5)
        self.assertEqual(payload["brackets"][2]["max_game_changers"], 3)
        self.assertIn("constraints", payload)
        self.assertEqual(payload["as_of"], br.RULES_AS_OF)


class NameMatchingTests(unittest.TestCase):
    def test_find_game_changers_matches_front_face_and_case(self):
        gcs = ["Tergrid, God of Fright // Tergrid's Lantern", "Rhystic Study", "Mana Vault"]
        deck = ["Sol Ring", "rhystic study", "Tergrid, God of Fright", "Mana Vault", "Mana Vault"]
        self.assertEqual(br.find_game_changers(deck, gcs), ["Rhystic Study", "Tergrid, God of Fright // Tergrid's Lantern", "Mana Vault"])

    def test_is_game_changer(self):
        gcs = ["Tergrid, God of Fright // Tergrid's Lantern"]
        self.assertFalse(br.is_game_changer("Tergrid's Lantern", gcs))
        self.assertTrue(br.is_game_changer("TERGRID, GOD OF FRIGHT", gcs))
        self.assertFalse(br.is_game_changer("", gcs))


# ---------------------------------------------------------------------------
# Fallback snapshot
# ---------------------------------------------------------------------------


class FallbackSnapshotTests(unittest.TestCase):
    def test_snapshot_file_is_checked_in_and_well_formed(self):
        path = ROOT / "src" / "server" / "domain" / "analytics" / "data" / "game_changers.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["query"], "is:gamechanger")
        self.assertTrue(data["as_of"])
        names = data["cards"]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names, sorted(names))
        # Known members of the official list as of the Feb 9 2026 update
        for n in ("Rhystic Study", "Farewell", "Biorhythm", "Thassa's Oracle", "Mana Vault"):
            self.assertIn(n, names)
        # Cut on Oct 21 2025: must not be in the snapshot
        for n in ("Food Chain", "Deflecting Swat", "Urza, Lord High Artificer", "Expropriate"):
            self.assertNotIn(n, names)

    def test_load_fallback(self):
        gc = br.load_fallback()
        self.assertEqual(gc.source, br.SOURCE_FALLBACK)
        self.assertGreater(gc.count, 40)
        self.assertEqual(gc.count, len(gc.cards))
        self.assertTrue(gc.fetched_at)


# ---------------------------------------------------------------------------
# Scryfall fetch (mocked) + Redis cache + fallback
# ---------------------------------------------------------------------------


class ScryfallFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_is_gamechanger_and_follows_pages(self):
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.url.path, "/cards/search")
            self.assertEqual(request.url.params["q"], "is:gamechanger")
            self.assertEqual(request.url.params["unique"], "cards")
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"object": "list", "total_cards": 3, "has_more": False,
                                                 "data": [_card("Ad Nauseam")]})
            return httpx.Response(200, json={"object": "list", "total_cards": 3, "has_more": True,
                                             "next_page": "https://api.scryfall.com/cards/search?page=2",
                                             "data": [_card("Rhystic Study"), _card("Mana Vault")]})

        client = _client(handler)
        names = await br.fetch_game_changers(client)
        self.assertEqual(names, ["Ad Nauseam", "Mana Vault", "Rhystic Study"])
        self.assertEqual(len(seen), 2)

    async def test_fetch_raises_on_empty_result(self):
        client = _client(lambda req: httpx.Response(404, json={"object": "error", "status": 404}))
        with self.assertRaises(br.GameChangersUnavailable):
            await br.fetch_game_changers(client)

    async def test_fetch_raises_on_server_error(self):
        client = _client(lambda req: httpx.Response(500, json={"object": "error"}))
        with self.assertRaises(br.GameChangersUnavailable):
            await br.fetch_game_changers(client)

    async def test_get_game_changers_fetches_then_caches_with_ttl(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"object": "list", "total_cards": 2, "has_more": False,
                                             "data": [_card("Rhystic Study"), _card("Farewell")]})

        r = FakeRedis()
        client = _client(handler)
        gc = await br.get_game_changers(r, client=client)
        self.assertEqual(gc.source, br.SOURCE_SCRYFALL)
        self.assertEqual(gc.cards, ["Farewell", "Rhystic Study"])
        self.assertEqual(gc.count, 2)
        self.assertEqual(r.ttls[br.CACHE_KEY], br.CACHE_TTL_S)
        self.assertGreaterEqual(br.CACHE_TTL_S, 3600)

        again = await br.get_game_changers(r, client=client)
        self.assertEqual(again.source, br.SOURCE_CACHE)
        self.assertEqual(again.cards, ["Farewell", "Rhystic Study"])
        self.assertEqual(again.fetched_at, gc.fetched_at)
        self.assertEqual(len(calls), 1)

    async def test_refresh_bypasses_cache(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"object": "list", "total_cards": 1, "has_more": False,
                                             "data": [_card("Farewell")]})

        r = FakeRedis()
        r.setex(br.CACHE_KEY, 10, json.dumps({"cards": ["Old Card"], "fetched_at": "x"}))
        gc = await br.get_game_changers(r, client=_client(handler), refresh=True)
        self.assertEqual(gc.cards, ["Farewell"])
        self.assertEqual(len(calls), 1)

    async def test_scryfall_down_falls_back_to_snapshot_and_does_not_cache_it(self):
        r = FakeRedis()
        client = _client(lambda req: httpx.Response(503, json={"object": "error"}))
        gc = await br.get_game_changers(r, client=client)
        self.assertEqual(gc.source, br.SOURCE_FALLBACK)
        self.assertEqual(gc.cards, br.load_fallback().cards)
        self.assertNotIn(br.CACHE_KEY, r.store)

    async def test_transport_error_falls_back(self):
        def handler(request):
            raise httpx.ConnectError("no network")

        gc = await br.get_game_changers(FakeRedis(), client=_client(handler))
        self.assertEqual(gc.source, br.SOURCE_FALLBACK)

    async def test_redis_down_still_fetches(self):
        client = _client(lambda req: httpx.Response(200, json={"object": "list", "total_cards": 1,
                                                               "has_more": False, "data": [_card("Farewell")]}))
        gc = await br.get_game_changers(BrokenRedis(), client=client)
        self.assertEqual(gc.source, br.SOURCE_SCRYFALL)

    async def test_corrupt_cache_is_ignored(self):
        r = FakeRedis()
        r.setex(br.CACHE_KEY, 10, "{not json")
        client = _client(lambda req: httpx.Response(200, json={"object": "list", "total_cards": 1,
                                                               "has_more": False, "data": [_card("Farewell")]}))
        gc = await br.get_game_changers(r, client=client)
        self.assertEqual(gc.source, br.SOURCE_SCRYFALL)

    async def test_no_redis_no_client_owned_client_is_closed(self):
        closed = []

        class Owned:
            async def search_all_cards(self, *a, **kw):
                for n in ("Farewell",):
                    yield type("C", (), {"name": n})()

            async def aclose(self):
                closed.append(True)

        with patch.object(br, "ScryfallClient", Owned):
            gc = await br.get_game_changers(None)
        self.assertEqual(gc.cards, ["Farewell"])
        self.assertEqual(closed, [True])


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------


class BracketsRouteTests(unittest.TestCase):
    def test_api_brackets(self):
        from fastapi.testclient import TestClient

        import src.server.app.main as app_module

        fake = br.GameChangerList(cards=["Farewell", "Rhystic Study"], source=br.SOURCE_CACHE,
                                  fetched_at="2026-10-15T00:00:00+00:00")

        async def fake_get(r, client=None, refresh=False):
            return fake

        # Stub app.r so the version middleware sees a cached version and does
        # not start a GitHub release sync that writes to the shared CI Redis
        # (that breaks tests/test_version.py later in the run).
        stub_r = FakeRedis()
        stub_r.store["github_latest_version"] = "test"
        with patch.object(br, "get_game_changers", fake_get), patch.object(app_module, "r", stub_r):
            resp = TestClient(app_module.app).get("/api/brackets")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual([b["name"] for b in body["brackets"]],
                         ["Exhibition", "Core", "Upgraded", "Optimized", "cEDH"])
        self.assertEqual(body["game_changers"]["count"], 2)
        self.assertEqual(body["game_changers"]["source"], "cache")
        self.assertEqual(body["game_changers"]["cards"], ["Farewell", "Rhystic Study"])


class SpecDocTests(unittest.TestCase):
    def test_spec_doc_exists_and_cites_official_sources(self):
        text = (ROOT / "docs" / "BRACKETS_SPEC.md").read_text()
        for b in br.BRACKETS:
            self.assertIn(b.name, text)
        for url in br.SOURCES.values():
            self.assertIn(url, text)
        for card in ("BR-1", "BR-8"):
            self.assertIn(card, text)
        self.assertIn("advisory", text.lower())
        self.assertIn("deterministic", text.lower())


if __name__ == "__main__":
    unittest.main()
