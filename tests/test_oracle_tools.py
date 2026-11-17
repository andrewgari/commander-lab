"""OL-3: get_deck, card_lookup, deck_stats + ToolContext (spec oracle-tool-loop §2.4/§2.5/§9.4)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import review_metrics as rm  # noqa: E402
from src.server.domain.oracle.tools import ToolContext, build_registry  # noqa: E402
from tests.oracle_fakes import call  # noqa: E402
from tests.test_review_metrics import make_deck, make_inputs  # noqa: E402

CARDS = {
    "sol ring": {"name": "Sol Ring", "mana_cost": "{1}", "cmc": 1.0, "type_line": "Artifact",
                 "oracle_text": "{T}: Add {C}{C}.", "color_identity": [], "legalities": {"commander": "legal"}},
    "mana crypt": {"name": "Mana Crypt", "mana_cost": "{0}", "cmc": 0.0, "type_line": "Artifact",
                   "oracle_text": "x", "color_identity": [], "legalities": {"commander": "banned"}},
}


class Scry:
    def __init__(self):
        self.calls = []

    def __call__(self, names):
        self.calls.append(list(names))
        return {n.lower(): CARDS[n.lower()] for n in names if n.lower() in CARDS}


class Metrics:
    def __init__(self):
        self.n = 0

    def __call__(self, deck):
        self.n += 1
        return rm.compute_review(make_inputs(deck=dict(deck)))


def ctx(**kw):
    kw.setdefault("deck", make_deck())
    return ToolContext(**kw)


def run(reg, c, name, args, id="c1"):
    return reg.dispatch(call(name, args, id), c)


class ToolsBase(unittest.TestCase):
    def reg(self, c, names=("get_deck", "card_lookup", "deck_stats")):
        return build_registry(names, c)


class TestRegistry(ToolsBase):
    def test_schemas_closed_and_fresh_registry(self):
        c = ctx()
        r1, r2 = self.reg(c), self.reg(c)
        self.assertIsNot(r1, r2)
        for s in r1.specs():
            p = s["function"]["parameters"]
            self.assertIs(p["additionalProperties"], False, s["function"]["name"])
            if "cards" in p.get("properties", {}):
                self.assertIs(p["properties"]["cards"]["items"]["additionalProperties"], False)
        self.assertEqual(r1.names(), frozenset({"get_deck", "card_lookup", "deck_stats"}))
        with self.assertRaises(ValueError):
            build_registry(["nope"], c)

    def test_context_defaults(self):
        c = ToolContext(deck={})
        self.assertFalse(c.allow_hypothetical)
        self.assertIsNone(c.sim)
        self.assertEqual(c.memo, {})
        self.assertIsNot(c.memo, ToolContext(deck={}).memo)


class TestGetDeck(ToolsBase):
    def test_shape(self):
        deck = make_deck()
        c = ctx(deck=deck, journal={"goal": "x"})
        o = run(self.reg(c), c, "get_deck", {})
        self.assertTrue(o.ok)
        d = o.result["data"]
        self.assertEqual(d["name"], "Test Deck")
        self.assertEqual(d["registry_id"], "archidekt:1")
        self.assertEqual(d["commanders"], ["Cmdr"])
        self.assertEqual(d["fingerprint"], rm.fingerprint(deck))
        self.assertEqual(d["journal"], {"goal": "x"})
        self.assertEqual(d["total"], sum(x["qty"] for x in d["cards"]))
        self.assertIn({"name": "Swamp", "qty": 30}, d["cards"])

    def test_rejects_args(self):
        c = ctx()
        self.assertEqual(run(self.reg(c), c, "get_deck", {"x": 1}).error_kind, "bad_arguments")


class TestCardLookup(ToolsBase):
    def test_found_not_found_and_game_changer(self):
        s = Scry()
        c = ctx(scryfall_batch=s, game_changers=["Mana Crypt"])
        o = run(self.reg(c), c, "card_lookup", {"names": ["Sol Ring", "Mana Crypt", "Nope Card"]})
        rows = o.result["data"]["cards"]
        self.assertEqual([r["found"] for r in rows], [True, True, False])
        self.assertEqual(rows[0]["name"], "Sol Ring")
        self.assertIs(rows[0]["commander_legal"], True)
        self.assertIs(rows[0]["game_changer"], False)
        self.assertIs(rows[1]["commander_legal"], False)
        self.assertIs(rows[1]["game_changer"], True)
        self.assertEqual(rows[2], {"query": "Nope Card", "found": False, "reason": "not on Scryfall"})

    def test_dfc_mana_cost_from_faces(self):
        dfc = {"name": "Delver // Aberrant", "cmc": 1.0, "type_line": "Creature", "color_identity": ["U"],
               "card_faces": [{"mana_cost": "{U}", "oracle_text": "a"}, {"mana_cost": "", "oracle_text": "b"}]}
        c = ctx(scryfall_batch=lambda names: {"delver // aberrant": dfc})
        row = run(self.reg(c), c, "card_lookup", {"names": ["Delver // Aberrant"]}).result["data"]["cards"][0]
        self.assertEqual(row["mana_cost"], "{U}")
        self.assertEqual(row["oracle_text"], "a\n//\nb")

    def test_game_changer_null_when_unavailable(self):
        c = ctx(scryfall_batch=Scry(), game_changers=None)
        o = run(self.reg(c), c, "card_lookup", {"names": ["Sol Ring"]})
        self.assertIsNone(o.result["data"]["cards"][0]["game_changer"])

    def test_unconfigured(self):
        c = ctx()
        o = run(self.reg(c), c, "card_lookup", {"names": ["Sol Ring"]})
        self.assertFalse(o.ok)
        self.assertEqual(o.error_kind, "unavailable")

    def test_arg_limits(self):
        c = ctx(scryfall_batch=Scry())
        r = self.reg(c)
        self.assertEqual(run(r, c, "card_lookup", {"names": []}).error_kind, "bad_arguments")
        self.assertEqual(run(r, c, "card_lookup", {"names": [f"c{i}" for i in range(21)]}).error_kind, "bad_arguments")
        self.assertEqual(run(r, c, "card_lookup", {"names": ["a", "a"]}).error_kind, "bad_arguments")
        # three bad calls disabled this registry (spec §4); a fresh one accepts exactly 20
        self.assertTrue(run(self.reg(c), c, "card_lookup", {"names": [f"c{i}" for i in range(20)]}).ok)

    def test_sixty_names_per_run(self):
        s = Scry()
        c = ctx(scryfall_batch=s)
        r = self.reg(c)
        for i in range(3):
            self.assertTrue(run(r, c, "card_lookup", {"names": [f"c{i}_{j}" for j in range(20)]}).ok)
        o = run(r, c, "card_lookup", {"names": ["Sol Ring"]})
        self.assertEqual(o.error_kind, "call_limit")
        self.assertEqual(len(s.calls), 3)

    def test_max_calls_four(self):
        c = ctx(scryfall_batch=Scry())
        r = self.reg(c)
        for i in range(4):
            self.assertTrue(run(r, c, "card_lookup", {"names": [f"c{i}"]}).ok)
        self.assertEqual(run(r, c, "card_lookup", {"names": ["z"]}).error_kind, "call_limit")

    def test_cached_names_not_refetched(self):
        s = Scry()
        c = ctx(scryfall_batch=s)
        r = self.reg(c)
        run(r, c, "card_lookup", {"names": ["Sol Ring"]})
        run(r, c, "card_lookup", {"names": ["sol ring", "Mana Crypt"]})
        self.assertEqual(s.calls, [["Sol Ring"], ["Mana Crypt"]])


class TestDeckStats(ToolsBase):
    ALL = ["curve", "bracket", "power", "composition"]

    def test_sections_and_memoization(self):
        m = Metrics()
        c = ctx(compute_metrics=m, game_changers=[])
        r = self.reg(c)
        o = run(r, c, "deck_stats", {"sections": self.ALL})
        self.assertTrue(o.ok, o.result)
        d = o.result["data"]
        self.assertEqual(set(d), set(self.ALL))
        self.assertIn("commander_on_curve", d["curve"])
        self.assertIn("rows", d["composition"])
        self.assertIn("total_cards", d["composition"])
        for k in ("estimated_bracket", "minimum_bracket", "conflicts", "floors"):
            self.assertIn(k, d["bracket"])
        self.assertNotIn("game_changers", d["bracket"])
        self.assertIn("estimated_power_level", d["power"])
        self.assertNotIn("brew_estimate", d["power"])
        json.dumps(d)
        run(r, c, "deck_stats", {"sections": ["curve", "composition"]}, id="c2")
        self.assertEqual(m.n, 1)

    def test_prefers_ctx_metrics(self):
        m = Metrics()
        c = ctx(compute_metrics=m, metrics=rm.compute_review(make_inputs()))
        o = run(self.reg(c), c, "deck_stats", {"sections": ["curve"]})
        self.assertTrue(o.ok)
        self.assertEqual(m.n, 0)

    def test_game_changers_unavailable_flag(self):
        c = ctx(game_changers=None)
        d = run(self.reg(c), c, "deck_stats", {"sections": ["bracket"]}).result["data"]
        self.assertEqual(d["bracket"]["game_changers"], "unavailable")

    def test_metrics_unavailable_not_zeros(self):
        c = ctx()
        d = run(self.reg(c), c, "deck_stats", {"sections": ["curve"]}).result["data"]
        self.assertEqual(d["curve"]["status"], "unavailable")

    def test_cards_override_rejected_unless_hypothetical(self):
        c = ctx(compute_metrics=Metrics())
        o = run(self.reg(c), c, "deck_stats", {"sections": ["power"], "cards": [{"name": "Sol Ring"}]})
        self.assertFalse(o.ok)
        self.assertEqual(o.error_kind, "not_allowed")

    def test_cards_override_when_hypothetical(self):
        m = Metrics()
        c = ctx(compute_metrics=m, allow_hypothetical=True, game_changers=[])
        r = self.reg(c)
        base = run(r, c, "deck_stats", {"sections": ["power"]}).result["data"]
        self.assertIn("brew_estimate", base["power"])
        self.assertIn("power", base["power"]["brew_estimate"])
        self.assertIn("breakdown", base["power"]["brew_estimate"])
        o = run(r, c, "deck_stats", {"sections": ["curve", "power"],
                                     "cards": [{"name": "Sol Ring", "qty": 1}, {"name": "Swamp", "qty": 36}]}, id="c2")
        self.assertTrue(o.ok, o.result)
        self.assertEqual(m.n, 1)  # override deck computed once; base never needed metrics
        run(r, c, "deck_stats", {"sections": ["curve"],
                                 "cards": [{"name": "Swamp", "qty": 36}, {"name": "Sol Ring"}]}, id="c3")
        self.assertEqual(m.n, 1)  # same fingerprint -> memo hit

    def test_override_keeps_enrichment_and_commanders_counted(self):
        from unittest.mock import patch
        deck = make_deck()
        deck["cards"][0] = {**deck["cards"][0], "type_line": "Artifact", "cmc": 3}
        name = deck["cards"][0]["name"]
        seen = []
        import src.server.domain.draft.brew_power as bp
        real = bp.estimate_power_level
        c = ctx(deck=deck, compute_metrics=Metrics(), allow_hypothetical=True, game_changers=[])
        with patch.object(bp, "estimate_power_level", side_effect=lambda cards, **k: (seen.append(cards), real(cards, **k))[1]):
            run(self.reg(c), c, "deck_stats", {"sections": ["power"], "cards": [{"name": name, "qty": 2}]})
            run(self.reg(c), c, "deck_stats", {"sections": ["power"]}, id="c2")
        override_cards, base_cards = seen
        self.assertEqual(override_cards[0]["type_line"], "Artifact")
        self.assertEqual(override_cards[0]["quantity"], 2)
        self.assertIn("Cmdr", [x["name"] for x in override_cards])
        self.assertIn("Cmdr", [x["name"] for x in base_cards])

    def test_bad_args(self):
        c = ctx()
        r = self.reg(c)
        for a in ({}, {"sections": []}, {"sections": ["nope"]}, {"sections": ["curve", "curve"]},
                  {"sections": ["curve"], "extra": 1}):
            self.assertEqual(run(r, c, "deck_stats", a).error_kind, "bad_arguments", a)


if __name__ == "__main__":
    unittest.main()
