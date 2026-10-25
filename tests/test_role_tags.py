"""ARCH-3 role tag registry: CRUD, query normalization/cache, evaluation states,
legacy migration (score equality), seed templates, and library seeding."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import role_tags as RT
from analytics import archetypes as AR
from analytics import genres as G
from analytics.interactions.vocab import RESOURCES


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


def fake_source(mapping, default=()):
    """A Scryfall source stand-in: {substring: [names]} matched against the query."""
    def src(q):
        for key, names in mapping.items():
            if key in q:
                return {"names": list(names), "total": len(names), "error": None, "oversized": False}
        return {"names": list(default), "total": len(default), "error": None, "oversized": False}
    return src


class TestNormalization(unittest.TestCase):
    def test_normalize_collapses_whitespace_and_semicolon(self):
        self.assertEqual(RT.normalize_query("  t:bird   or\n o:bird; "), "t:bird or o:bird")

    def test_hash_stable_and_order_sensitive(self):
        self.assertEqual(RT.query_hash("t:bird  or o:bird"), RT.query_hash("t:bird or o:bird"))
        self.assertNotEqual(RT.query_hash("t:bird or o:bird"), RT.query_hash("o:bird or t:bird"))

    def test_query_limits(self):
        with self.assertRaises(RT.RoleTagError):
            RT.normalize_query(5)
        r = FakeRedis()
        with self.assertRaises(RT.RoleTagError):
            RT.create_tag(r, {"label": "x", "query": "q" * 500, "facet": "FREE"})
        with self.assertRaises(RT.RoleTagError):
            RT.create_tag(r, {"label": "x", "query": " ", "facet": "FREE"})


class TestLegacyMigration(unittest.TestCase):
    def test_thirty_legacy_tags_seeded_once(self):
        r = FakeRedis()
        tags = RT.load_tags(r)
        self.assertEqual(len(tags), 30)
        self.assertEqual(len(RT.load_tags(r)), 30)  # idempotent
        by_id = {t["id"]: t for t in tags}
        self.assertEqual(by_id["legacy:produces:spells"]["query"], "t:instant or t:sorcery")
        self.assertEqual(by_id["legacy:produces:tokens"]["source"], "migrated")
        res = next(x for x in RESOURCES if x.key == "tokens")
        for otag in res.produce_otags:
            self.assertIn(f"otag:{otag}", by_id["legacy:produces:tokens"]["query"])

    def test_resolve_legacy_aliases(self):
        r = FakeRedis()
        idx = RT.tag_index(r)
        self.assertEqual(RT.resolve_role_tag("produces:tokens", idx), "legacy:produces:tokens")
        self.assertEqual(RT.resolve_role_tag("Consumes: Life gain", idx), "legacy:consumes:life_gain")
        self.assertEqual(RT.resolve_role_tag("legacy:produces:tokens", idx), "legacy:produces:tokens")
        with self.assertRaises(RT.RoleTagError):
            RT.resolve_role_tag("produces:nope", idx)
        with self.assertRaises(RT.RoleTagError):
            RT.resolve_role_tag("o:bird", idx)  # looks like a query, not a tag

    def test_migration_score_equality_gate(self):
        """Spec §3.2: a legacy role scores identically to the old produces/consumes counting,
        given the otag membership the old LLM tagger used."""
        r = FakeRedis()
        # Deck whose old-style graph tags imply: 3 copies produce sacrifice, 4 consume death.
        deck = {"cards": [{"name": "Outlet", "quantity": 3, "produces": ["sacrifice"], "consumes": []},
                          {"name": "Payoff", "quantity": 4, "produces": [], "consumes": ["death"]}]}
        roles = [{"tag": "produces:sacrifice", "min_count": 3, "weight": 2},
                 {"tag": "consumes:death", "min_count": 4, "weight": 2}]
        # Old algorithm (pre-ARCH-3): count copies carrying the tag.
        counts = {}
        for card in deck["cards"]:
            for side in ("produces", "consumes"):
                for key in set(card.get(side) or ()):
                    counts[f"{side}:{key}"] = counts.get(f"{side}:{key}", 0) + card["quantity"]
        old = {role["tag"]: min(counts.get(role["tag"], 0) / role["min_count"], 1.0) for role in roles}
        # New: the legacy tag's otag query matches exactly the cards the tagger would have tagged.
        src = fake_source({"sacrifice-outlet": ["Outlet"], "death-trigger": ["Payoff"]})
        arch = {"id": "a", "name": "A", "required_roles": roles}
        new_row = AR.archetype_match(deck, [arch], r, src)[0]
        self.assertEqual(new_row["coverage"], 1.0)
        for role, cov in zip(new_row["roles_hit"], [1.0, 1.0]):
            self.assertEqual(cov, 1.0)
        self.assertEqual(old["produces:sacrifice"], 1.0)
        self.assertEqual(old["consumes:death"], 1.0)


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_ok_short_empty_blocked_states(self):
        src = fake_source({"t:bird": ["Aerie", "Siren"]})
        tag = RT.create_tag(self.r, {"label": "Birds", "query": "t:bird", "facet": "IS"})
        deck = [{"name": "Aerie", "quantity": 3}, {"name": "Siren", "quantity": 1}, {"name": "Swamp", "quantity": 30}]
        out = RT.evaluate_tag(self.r, tag, deck, src)
        self.assertEqual((out["state"], out["have"], out["matched"]), ("ok", 4, 2))
        self.assertEqual([c["name"] for c in out["cards"]], ["Aerie", "Siren"])
        out = RT.evaluate_tag(self.r, tag, [{"name": "Swamp", "quantity": 30}], src)
        self.assertEqual((out["state"], out["have"]), ("empty", 0))
        bad = RT.create_tag(self.r, {"label": "Broken", "query": "o:/unclosed", "facet": "FREE"})
        err_src = lambda q: {"names": None, "total": 0, "error": "Scryfall rejected the query: bad syntax", "oversized": False}
        out = RT.evaluate_tag(self.r, bad, deck, err_src)
        self.assertEqual(out["state"], "blocked")
        self.assertIn("bad syntax", out["error"])

    def test_oversized_is_blocked_not_scored(self):
        big = lambda q: {"names": None, "total": 8185, "error": None, "oversized": True}
        tag = RT.create_tag(self.r, {"label": "Historic", "query": "t:artifact or t:legendary or t:saga", "facet": "FREE"})
        out = RT.evaluate_tag(self.r, tag, [{"name": "Sol Ring", "quantity": 1}], big)
        self.assertEqual(out["state"], "blocked")
        self.assertIn("cap", out["error"])

    def test_404_is_empty_not_error(self):
        src = lambda q: {"names": [], "total": 0, "error": None, "oversized": False}
        tag = RT.create_tag(self.r, {"label": "Wipe", "query": "o:/destroy all [^.]*vampires/", "facet": "FREE"})
        out = RT.evaluate_tag(self.r, tag, [{"name": "A", "quantity": 1}], src)
        self.assertEqual((out["state"], out["have"], out["error"]), ("empty", 0, None))

    def test_index_caches_second_evaluation(self):
        calls = []
        def counting(q):
            calls.append(q)
            return {"names": ["A"], "total": 1, "error": None, "oversized": False}
        RT.query_index(self.r, "t:bird", counting)
        RT.query_index(self.r, "t:bird", counting)
        self.assertEqual(len(calls), 1)

    def test_archetype_match_returns_card_names_and_zero_for_empty(self):
        src = fake_source({"t:bird": ["Aerie"]})
        arch = {"id": "birds", "name": "Birds", "required_roles": [
            {"tag": "free:birds", "min_count": 2, "weight": 1.0},
            {"tag": "free:wipe", "min_count": 1, "weight": 1.0}]}
        RT.create_tag(self.r, {"id": "free:birds", "label": "Birds", "query": "t:bird", "facet": "IS"})
        RT.create_tag(self.r, {"id": "free:wipe", "label": "Wipe", "query": "o:/destroy all [^.]*vampires/", "facet": "FREE"})
        deck = {"cards": [{"name": "Aerie", "quantity": 1}]}
        row = AR.archetype_match(deck, [arch], self.r, src)[0]
        self.assertEqual(row["coverage"], 0.25)  # (1/2 + 0/1) / 2
        hit = row["roles_hit"] + row["roles_missing"]
        wipe = next(r0 for r0 in hit if r0["tag"] == "free:wipe")
        self.assertEqual((wipe["state"], wipe["have"]), ("empty", 0))
        birds = next(r0 for r0 in hit if r0["tag"] == "free:birds")
        self.assertEqual([c["name"] for c in birds["cards"]], ["Aerie"])

    def test_blocked_role_scores_zero_without_error(self):
        src = lambda q: {"names": None, "total": 0, "error": "Scryfall unreachable", "oversized": False}
        RT.create_tag(self.r, {"id": "free:x", "label": "X", "query": "t:bird", "facet": "IS"})
        arch = {"id": "a", "name": "A", "required_roles": [{"tag": "free:x", "min_count": 1, "weight": 1}]}
        row = AR.archetype_match({"cards": [{"name": "Aerie", "quantity": 1}]}, [arch], self.r, src)[0]
        self.assertEqual(row["coverage"], 0.0)
        self.assertEqual(row["roles_missing"][0]["state"], "blocked")


class TestDeckScope(unittest.TestCase):
    def test_maybeboard_and_considering_excluded_commander_kept(self):
        deck = {"cards": [
            {"name": "Main", "quantity": 1, "category": "Mainboard"},
            {"name": "Cmdr", "quantity": 1, "category": "Commander"},
            {"name": "Maybe", "quantity": 1, "category": "Maybeboard"},
            {"name": "Considering", "quantity": 1, "category": "Considering"},
        ]}
        names = {c["name"] for c in RT.deck_cards_from_deck(deck)}
        self.assertEqual(names, {"Main", "Cmdr"})


class TestCrudAndSeeding(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_create_update_delete_and_limits(self):
        t = RT.create_target(self.r, {"label": "Birds", "kind": "subtype", "signal": "bird"})
        self.assertEqual(t["id"], "bird")
        tag = RT.create_tag(self.r, {"label": "Birds", "query": "t:bird", "facet": "IS", "target_id": "bird"})
        self.assertEqual(tag["id"], "bird:is")
        dup = RT.create_tag(self.r, {"label": "Birds 2", "query": "t:bird", "facet": "IS", "target_id": "bird"})
        self.assertEqual(dup["id"], "bird:is-2")  # same query is legal; ids dedupe
        RT.update_tag(self.r, "bird:is", {"query": "t:bird or o:bird"})
        self.assertEqual(RT.tag_index(self.r)["bird:is"]["query"], "t:bird or o:bird")
        RT.delete_tag(self.r, "bird:is-2")
        with self.assertRaises(RT.RoleTagError):
            RT.delete_tag(self.r, "bird:is", in_use_by=["archetype 'Birds'"])
        with self.assertRaises(RT.RoleTagError):
            RT.create_tag(self.r, {"label": "x", "query": "t:x", "facet": "NOPE"})
        with self.assertRaises(RT.RoleTagError):
            RT.create_tag(self.r, {"label": "x", "query": "t:x", "facet": "IS", "target_id": "nope"})

    def test_seed_templates(self):
        bird = {"id": "bird", "label": "Birds", "kind": "subtype", "signal": "bird"}
        self.assertEqual(RT.seed_query(bird, "IS"), "t:bird")
        self.assertEqual(RT.seed_query(bird, "TALKS"), "o:bird")
        self.assertEqual(RT.seed_query(bird, "MAKES"), "o:/creates? a [^.]*Bird creature token/")
        self.assertEqual(RT.seed_query(bird, "AMPLIFIES"), "")
        self.assertEqual(RT.seed_query({"kind": "keyword", "signal": "flying"}, "IS"), "keyword:flying")
        starters = dict(RT.starter_queries({"kind": "enemy", "signal": "vampire"}))
        self.assertIn("o:/destroy target [^.]*vampire/", starters.values())

    def test_library_seeding_idempotent_and_non_destructive(self):
        lib = {"targets": [{"id": "bird", "label": "Birds", "kind": "subtype", "signal": "bird"}],
               "tags": [{"id": "bird:is", "label": "Birds", "query": "t:bird", "facet": "IS", "target_id": "bird"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(lib, fh)
            path = fh.name
        try:
            self.assertEqual(RT.seed_library(self.r, path), 2)
            # User edits the tag; a re-seed must not overwrite the edit.
            RT.update_tag(self.r, "bird:is", {"query": "t:bird or o:bird"})
            self.assertEqual(RT.seed_library(self.r, path), 0)
            self.assertEqual(RT.tag_index(self.r)["bird:is"]["query"], "t:bird or o:bird")
        finally:
            os.unlink(path)
        self.assertEqual(RT.seed_library(self.r, "/nonexistent/role_library.json"), 0)

    def test_genre_roles_share_the_registry(self):
        g, _ = G.create_genre(self.r, {"name": "Birds", "required_roles": [{"tag": "produces:tokens"}]})
        self.assertEqual(g["required_roles"][0]["tag"], "legacy:produces:tokens")
        with self.assertRaises(G.GenreError):
            G.create_genre(self.r, {"name": "Bad", "required_roles": [{"tag": "o:bird"}]})


if __name__ == "__main__":
    unittest.main()
