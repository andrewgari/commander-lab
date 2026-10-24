"""RV-1 genre registry: CRUD + version counter, autofill validation (mocked oracle), genre_match."""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app as app_module
from analytics import genres as G
from analytics.oracle_client import OracleUnavailable


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return int(self.store[k])


ROLE = {"tag": "produces:sacrifice", "min_count": 2, "weight": 1.5}


class TestStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_starts_empty_no_seed_call(self):
        """No built-in seed data: an unread registry is simply absent, and reading it
        never materializes anything -- it stays an empty list with version 0."""
        self.assertEqual(G.list_genres(self.r), [])
        self.assertEqual(G.get_version(self.r), 0)
        self.assertIsNone(self.r.get(G.GENRES_KEY))
        G.list_genres(self.r)
        self.assertEqual(G.get_version(self.r), 0)  # reading never bumps or seeds

    def test_crud_and_version_bump(self):
        v0 = G.get_version(self.r)
        g, v1 = G.create_genre(self.r, {"name": "Sac Package", "description": "d", "required_roles": [ROLE]})
        self.assertEqual((g["id"], v1), ("sac-package", v0 + 1))
        self.assertEqual(G.get_genre(self.r, "sac-package")["required_roles"], [ROLE])
        g2, v2 = G.update_genre(self.r, "sac-package", {"description": "new"})
        self.assertEqual((g2["description"], g2["required_roles"], v2), ("new", [ROLE], v1 + 1))
        self.assertFalse(g2["draft"])
        v3 = G.delete_genre(self.r, "sac-package")
        self.assertEqual(v3, v2 + 1)
        with self.assertRaises(G.GenreNotFound):
            G.get_genre(self.r, "sac-package")

    def test_failed_write_does_not_bump(self):
        G.create_genre(self.r, {"name": "Storm"})
        v = G.get_version(self.r)
        for body in ({"name": ""}, {"name": "X", "required_roles": [{"tag": "produces:nope"}]}, {"name": "Storm"}):
            with self.assertRaises(G.GenreError):
                G.create_genre(self.r, body)
        with self.assertRaises(G.GenreNotFound):
            G.delete_genre(self.r, "missing")
        self.assertEqual(G.get_version(self.r), v)

    def test_slug_collision_and_validation(self):
        G.create_genre(self.r, {"name": "My Thing"})
        g, _ = G.create_genre(self.r, {"name": "my-thing"})  # different name, same slug
        self.assertEqual(g["id"], "my-thing-2")
        for roles in ([{"tag": "produces:tokens", "min_count": 0}], [{"tag": "produces:tokens", "weight": 0}],
                      [{"tag": "produces:tokens"}, {"tag": "Produces: Tokens"}], "x"):
            with self.assertRaises(G.GenreError):
                G.clean_roles(roles)
        self.assertEqual(G.normalize_tag("Consumes: Life gain"), "consumes:life_gain")

    def test_deleting_everything_does_not_reseed(self):
        G.create_genre(self.r, {"name": "Temp", "required_roles": [ROLE]})
        for g in G.list_genres(self.r):
            G.delete_genre(self.r, g["id"])
        self.assertEqual(G.list_genres(self.r), [])


class TestApi(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        p = patch.object(app_module, "r", self.r)
        p.start()
        self.addCleanup(p.stop)
        self.c = TestClient(app_module.app)

    def test_page_and_nav(self):
        res = self.c.get("/genres")
        self.assertEqual(res.status_code, 200)
        self.assertIn('id="gnForm"', res.text)
        self.assertIn('href="/genres"', res.text.split("</nav>", 1)[0])

    def test_crud_roundtrip(self):
        res = self.c.get("/api/genres").json()
        v = res["version"]
        self.assertEqual(len(res["genres"]), 0)
        self.assertIn("produces:sacrifice", [x["tag"] for x in res["vocabulary"]])
        res = self.c.post("/api/genres", json={"name": "Mine", "required_roles": [ROLE]})
        self.assertEqual((res.status_code, res.json()["version"]), (201, v + 1))
        self.assertEqual(self.c.get("/api/genres/mine").json()["genre"]["name"], "Mine")
        res = self.c.put("/api/genres/mine", json={"description": "x"})
        self.assertEqual((res.status_code, res.json()["version"]), (200, v + 2))
        res = self.c.delete("/api/genres/mine")
        self.assertEqual((res.status_code, res.json()["version"]), (200, v + 3))
        self.assertEqual(self.c.get("/api/genres/mine").status_code, 404)
        self.assertEqual(self.c.put("/api/genres/mine", json={"name": "z"}).status_code, 404)
        self.assertEqual(self.c.delete("/api/genres/mine").status_code, 404)
        self.assertEqual(self.c.get("/api/genres").json()["version"], v + 3)

    def test_bad_input_is_400(self):
        self.assertEqual(self.c.post("/api/genres", json={"name": "A", "required_roles": [{"tag": "bogus"}]}).status_code, 400)
        self.assertEqual(self.c.post("/api/genres", content=b"not json").status_code, 400)

    def test_deck_genres_endpoint(self):
        G.create_genre(self.r, {"id": "landfall", "name": "Landfall",
                                 "required_roles": [{"tag": "consumes:lands", "min_count": 1, "weight": 1}]})
        deck = {"name": "D", "registry_id": "archidekt:1", "cards": []}
        graph = {"counts": {"untagged": 1}, "nodes": [{"name": "A", "produces": ["lands"], "consumes": ["lands"]}]}
        with patch.object(app_module, "_deck_or_404", return_value=(deck, None)), \
                patch.object(app_module.interactions_service, "get_deck_graph", return_value=graph):
            body = self.c.get("/api/decks/archidekt:1/genres").json()
        self.assertEqual(body["matches"][0]["genre"]["id"], "landfall")
        self.assertEqual(body["untagged"], 1)


def _reply(obj):
    return "sure\n```json\n" + json.dumps(obj) + "\n```\n"


GOOD = {"known": True, "description": "Sac things.",
        "required_roles": [{"tag": "produces:sacrifice", "min_count": 3, "weight": 2},
                           {"tag": "consumes:death", "min_count": 4, "weight": 2}]}


class FakeClient:
    timeout = 1.0

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc = reply, exc
        self.prompts = []

    def ask(self, prompt, system=None, **kw):
        self.prompts.append(prompt)
        if self.exc:
            raise self.exc
        return self.reply


class TestAutofill(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        p = patch.object(app_module, "r", self.r)
        p.start()
        self.addCleanup(p.stop)
        self.c = TestClient(app_module.app)

    def _post(self, client, theme="Aristocrats", preflight=None):
        with patch.object(app_module, "_oracle_preflight", return_value=preflight), \
                patch.object(app_module, "_oracle_client", return_value=client):
            return self.c.post("/api/genres/autofill", json={"theme": theme})

    def test_valid_draft_is_returned_not_saved(self):
        client = FakeClient(_reply(GOOD))
        v = G.get_version(self.r)
        res = self._post(client)
        d = res.json()["draft"]
        self.assertEqual((res.status_code, d["source"], d["flagged"]), (200, "autofill", []))
        self.assertEqual(d["required_roles"][0], {"tag": "produces:sacrifice", "min_count": 3, "weight": 2.0})
        self.assertIn("produces:sacrifice", client.prompts[0])  # vocabulary is in the prompt
        self.assertEqual(G.get_version(self.r), v)  # nothing saved
        self.assertEqual(len(G.list_genres(self.r)), 0)

    def test_unknown_tags_are_flagged_and_dropped(self):
        reply = dict(GOOD, required_roles=GOOD["required_roles"] + [
            {"tag": "produces:planeswalkers", "min_count": 2, "weight": 1},
            {"tag": "consumes:death", "min_count": 1, "weight": 1},
            {"tag": "produces:tokens", "min_count": 999, "weight": 1},
            "garbage"])
        d = self._post(FakeClient(_reply(reply))).json()["draft"]
        self.assertEqual([x["tag"] for x in d["required_roles"]], ["produces:sacrifice", "consumes:death"])
        self.assertEqual(len(d["flagged"]), 4)
        self.assertEqual(d["flagged"][0], {"tag": "produces:planeswalkers", "reason": "unknown tag"})

    def test_all_unknown_or_unknown_theme_is_422(self):
        bad = {"known": True, "description": "x", "required_roles": [{"tag": "made:up"}]}
        for reply in (_reply(bad), _reply({"known": False}), "no json at all"):
            res = self._post(FakeClient(reply))
            self.assertEqual((res.status_code, res.json()["error_kind"]), (422, "autofill_invalid"))

    def test_offline_message_and_manual_entry_still_works(self):
        off = app_module._oracle_offline("not configured")
        res = self._post(FakeClient(), preflight=off)
        self.assertEqual((res.status_code, res.json()["error"]), (503, "oracle offline, try again later"))
        res = self._post(FakeClient(exc=OracleUnavailable("timed out")))
        self.assertEqual((res.status_code, res.json()["error_kind"]), (503, "oracle_offline"))
        res = self.c.post("/api/genres", json={"name": "Manual", "required_roles": [ROLE]})
        self.assertEqual(res.status_code, 201)

    def test_theme_required(self):
        self.assertEqual(self._post(FakeClient(), theme="  ").status_code, 400)


def _deck(extra=()):
    return {"cards": [
        {"name": "Viscera Seer", "produces": ["sacrifice"], "consumes": []},
        {"name": "Blood Artist", "produces": [], "consumes": ["death"]},
        {"name": "Zulaport Cutthroat", "produces": [], "consumes": ["death"]},
        {"name": "Bitterblossom", "produces": ["tokens"], "consumes": []},
        {"name": "Plains", "produces": [], "consumes": [], "quantity": 20},
        *extra]}


class TestGenreMatch(unittest.TestCase):
    GENRES = [
        {"id": "aristocrats", "name": "Aristocrats", "required_roles": [
            {"tag": "produces:sacrifice", "min_count": 2, "weight": 2},
            {"tag": "consumes:death", "min_count": 2, "weight": 1},
            {"tag": "produces:tokens", "min_count": 4, "weight": 1}]},
        {"id": "landfall", "name": "Landfall", "required_roles": [{"tag": "consumes:lands", "min_count": 3, "weight": 1}]},
        {"id": "empty", "name": "Empty", "required_roles": []},
    ]

    def test_coverage_hit_missing_and_rank(self):
        out = G.genre_match(_deck(), self.GENRES)
        self.assertEqual([m["genre"]["id"] for m in out], ["aristocrats", "empty", "landfall"])
        a = out[0]
        # 2*(1/2) + 1*1 + 1*(1/4) = 2.25 / 4
        self.assertEqual(a["coverage"], 0.5625)
        self.assertEqual([x["tag"] for x in a["roles_hit"]], ["consumes:death"])
        self.assertEqual([x["tag"] for x in a["roles_missing"]], ["produces:sacrifice", "produces:tokens"])
        self.assertEqual(out[1]["coverage"], 0.0)

    def test_quantity_counts_copies(self):
        d = {"cards": [{"name": "Rat", "produces": ["tokens"], "quantity": 4}]}
        m = G.genre_match(d, [self.GENRES[0]])[0]
        self.assertEqual(m["roles_hit"][0], {"tag": "produces:tokens", "have": 4, "min_count": 4, "weight": 1.0})

    def test_deterministic_and_order_independent(self):
        deck, genres = _deck(), copy.deepcopy(self.GENRES)
        a = G.genre_match(deck, genres)
        b = G.genre_match(copy.deepcopy(deck), copy.deepcopy(genres))
        self.assertEqual(json.dumps(a), json.dumps(b))
        shuffled = {"cards": list(reversed(deck["cards"]))}
        self.assertEqual(json.dumps(a), json.dumps(G.genre_match(shuffled, list(reversed(genres)))))
        self.assertEqual(deck, _deck())  # inputs untouched

    def test_fixture_aristocrats_ranks_first_for_aristocrat_deck(self):
        """Local fixture standing in for the old SEED_GENRES vocabulary check."""
        fixture_genres = [
            {"id": "aristocrats", "name": "Aristocrats", "required_roles": [
                {"tag": "produces:sacrifice", "min_count": 3, "weight": 2},
                {"tag": "consumes:death", "min_count": 4, "weight": 2},
                {"tag": "produces:tokens", "min_count": 4, "weight": 1},
                {"tag": "produces:life_loss", "min_count": 2, "weight": 1}]},
            {"id": "landfall", "name": "Landfall", "required_roles": [
                {"tag": "consumes:lands", "min_count": 3, "weight": 1}]},
        ]
        cards = [{"name": f"S{i}", "produces": ["sacrifice"], "consumes": ["death"]} for i in range(5)]
        cards += [{"name": f"T{i}", "produces": ["tokens", "life_loss"], "consumes": ["death"]} for i in range(5)]
        out = G.genre_match({"cards": cards}, fixture_genres)
        self.assertEqual(out[0]["genre"]["id"], "aristocrats")
        self.assertEqual(out[0]["coverage"], 1.0)

    def test_deck_from_graph(self):
        g = {"nodes": [{"name": "A", "produces": ["tokens"], "consumes": []}]}
        self.assertEqual(G.deck_from_graph(g)["cards"][0]["produces"], ["tokens"])
        self.assertEqual(G.genre_match({}, self.GENRES)[0]["coverage"], 0.0)

    def test_deck_from_graph_uses_real_quantities(self):
        g = {"nodes": [{"name": "A", "produces": ["tokens"], "consumes": []}]}
        self.assertEqual(G.deck_from_graph(g, {"A": 7})["cards"][0]["quantity"], 7)

    def test_overflow_min_count_is_a_genre_error(self):
        for bad in (1e309, float("inf")):
            with self.assertRaises(G.GenreError):
                G.clean_roles([{"tag": "produces:tokens", "min_count": bad, "weight": 1}])
        with self.assertRaises(G.GenreError):
            G.clean_roles([{"tag": "produces:tokens", "min_count": 1, "weight": 1e999}])

    def test_concurrent_creates_keep_every_write(self):
        import threading
        r = FakeRedis()
        G.ensure_seeded(r)  # no-op; kept to prove it never injects data under concurrency
        errs = []
        def mk(i):
            try: G.create_genre(r, {"name": f"Concurrent {i}", "required_roles": []})
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=mk, args=(i,)) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len([g for g in G.list_genres(r) if g["name"].startswith("Concurrent")]), 20)


if __name__ == "__main__":
    unittest.main()
