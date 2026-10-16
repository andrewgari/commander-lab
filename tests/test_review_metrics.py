"""RV-2: deterministic review metrics. Pure engine tests on a hand-built aristocrats-ish deck."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import review_metrics as rm
from analytics.interactions import engines as engines_mod
from analytics.interactions import graph as graph_mod

CARDS = {
    "Cmdr": ("Legendary Creature — Human", "Whenever a creature you control dies, each opponent loses 1 life.", "{2}{B}"),
    "Sol Ring": ("Artifact", "{T}: Add {C}{C}.", "{1}"),
    "Arcane Signet": ("Artifact", "{T}: Add one mana of any color.", "{2}"),
    "Mind Stone": ("Artifact", "{T}: Add {C}.", "{2}"),
    "Viscera Seer": ("Creature — Vampire", "Sacrifice a creature: Scry 1.", "{B}"),
    "Blood Artist": ("Creature — Vampire", "Whenever Blood Artist or another creature dies, target player loses 1 life and you gain 1 life.", "{1}{B}"),
    "Zulaport Cutthroat": ("Creature — Human", "Whenever Zulaport Cutthroat or another creature you control dies, each opponent loses 1 life and you gain 1 life.", "{1}{B}"),
    "Bitterblossom": ("Enchantment", "At the beginning of your upkeep, you lose 1 life and create a 1/1 black Faerie Rogue creature token with flying.", "{1}{B}"),
    "Phyrexian Arena": ("Enchantment", "At the beginning of your upkeep, you draw a card and lose 1 life.", "{1}{B}{B}"),
    "Rhythm of the Wild": ("Enchantment", "Creature spells you control can't be countered. Creatures you control have riot.", "{1}{R}{G}"),
    "Divination": ("Sorcery", "Draw two cards.", "{2}{U}"),
    "Counterspell": ("Instant", "Counter target spell.", "{U}{U}"),
    "Pestilence": ("Enchantment", "At the beginning of the end step, if no creatures are on the battlefield, sacrifice Pestilence.", "{2}{B}{B}"),
    "Temple of Silence": ("Land", "Temple of Silence enters tapped. When it enters, scry 1.\n{T}: Add {W} or {B}.", ""),
    "Swamp": ("Basic Land — Swamp", "{T}: Add {B}.", ""),
    "Command Tower": ("Land", "{T}: Add one mana of any color in your commander's color identity.", ""),
    "Gray Merchant": ("Creature — Zombie", "When Gray Merchant of Asphodel enters, each opponent loses X life, where X is your devotion to black.", "{3}{B}{B}"),
    "Gnaw Pet": ("Creature — Rat", "Menace", "{3}"),
    "Laboratory Maniac": ("Creature — Human Wizard", "If you would draw a card while your library has no cards in it, you win the game instead.", "{2}{U}"),
    "Pet Rock": ("Artifact", "Pet Rock does nothing useful whenever the moon is full.", "{4}"),
    "Ghostly Pilferer": ("Creature — Spirit", "Flash\nFlying", "{1}{U}"),
}
TAGS = {  # produces / consumes
    "Cmdr": (["life_loss"], ["death"]),
    "Viscera Seer": (["sacrifice", "death"], ["tokens"]),
    "Blood Artist": (["life_loss"], ["death"]),
    "Zulaport Cutthroat": (["life_loss"], ["death"]),
    "Bitterblossom": (["tokens"], []),
    "Gray Merchant": (["life_loss"], []),
    "Gnaw Pet": ([], []),
    "Pet Rock": ([], []),
    "Rhythm of the Wild": ([], []),
}
NAMES = [n for n in CARDS if n != "Swamp"]
FILL = 4


def make_deck():
    cards = [{"name": n, "quantity": 1} for n in NAMES if n != "Cmdr"]
    cards.append({"name": "Swamp", "quantity": 30})
    return {"registry_id": "archidekt:1", "id": "1", "name": "Test Deck", "commanders": ["Cmdr"],
            "cards": cards}


def make_texts():
    return {n: {"type_line": t, "oracle_text": o, "mana_cost": m} for n, (t, o, m) in CARDS.items()}


def make_graph(deck):
    tags = {n: {"produces": p, "consumes": c, "source": "t"} for n, (p, c) in TAGS.items()}
    names = [c["name"] for c in deck["cards"]] + deck["commanders"]
    return graph_mod.build_graph(names, tags, meta={"deck": {"registry_id": deck["registry_id"]}}, commanders=deck["commanders"])


GENRES = [
    {"id": "aristocrats", "name": "Aristocrats", "required_roles": [
        {"tag": "produces:sacrifice", "min_count": 1, "weight": 1.0}, {"tag": "consumes:death", "min_count": 3, "weight": 1.0}]},
    {"id": "stax", "name": "Stax", "required_roles": [{"tag": "produces:lockdown", "min_count": 3, "weight": 1.0}]},
    {"id": "tokens", "name": "Tokens", "required_roles": [{"tag": "produces:tokens", "min_count": 1, "weight": 1.0},
                                                          {"tag": "consumes:tokens", "min_count": 1, "weight": 1.0}]},
]
COMBOS = {"in_deck": [{"id": "1-2", "card_names": ["Laboratory Maniac", "Gnaw Pet"], "produces": ["Win the game"], "popularity": 50}],
          "one_away": [{"id": "3-4", "card_names": ["Sol Ring", "Foo"], "missing": ["Foo"], "missing_owned": True,
                        "produces": ["Infinite mana"], "popularity": 9}]}


def make_inputs(**over):
    deck = make_deck()
    graph = make_graph(deck)
    inp = {"deck": deck, "texts": make_texts(), "graph": graph, "engines": engines_mod.detect_engines(graph),
           "combos": copy.deepcopy(COMBOS), "genres": copy.deepcopy(GENRES), "genre_version": 7, "journal": None, "pets": []}
    inp.update(over)
    return inp


class TestFingerprint(unittest.TestCase):
    def test_order_independent_and_sensitive(self):
        a = make_deck()
        b = make_deck()
        b["cards"].reverse()
        self.assertEqual(rm.fingerprint(a), rm.fingerprint(b))
        b["cards"][0]["quantity"] += 1
        self.assertNotEqual(rm.fingerprint(a), rm.fingerprint(b))
        c = make_deck()
        c["commanders"] = ["Other"]
        self.assertNotEqual(rm.fingerprint(a), rm.fingerprint(c))
        self.assertEqual(len(rm.fingerprint(a)), 64)


class TestCardFacts(unittest.TestCase):
    def f(self, name, ramp=False):
        return rm.card_facts(name, make_texts()[name], 1, ramp)

    def test_cmc(self):
        self.assertEqual(rm.cmc_of("{2}{B}"), 3)
        self.assertEqual(rm.cmc_of("{X}{R}{R}"), 2)
        self.assertEqual(rm.cmc_of("{W/U}{2/B}"), 3)
        self.assertEqual(rm.cmc_of("{1}{B/P}"), 2)
        self.assertEqual(rm.cmc_of(""), 0)
        self.assertEqual(rm.cmc_of("{1}{U} // {2}{R}"), 2)

    def test_tapland_is_durdle_but_untapped_isnt(self):
        self.assertIn("tapland", self.f("Temple of Silence")["durdle_reasons"])
        self.assertFalse(self.f("Swamp")["durdle"])
        self.assertFalse(self.f("Command Tower")["durdle"])

    def test_setup_only_and_active(self):
        self.assertIn("setup_only", self.f("Pet Rock")["durdle_reasons"])
        self.assertTrue(self.f("Divination")["active"])
        self.assertFalse(self.f("Divination")["durdle"])
        self.assertTrue(self.f("Sol Ring", ramp=True)["active"])
        self.assertFalse(self.f("Sol Ring", ramp=True)["durdle"])
        self.assertTrue(self.f("Viscera Seer")["active"])
        self.assertIn("creature_body", self.f("Viscera Seer")["active_reasons"])

    def test_pay_now_payoff_later(self):
        f = rm.card_facts("Rift", {"type_line": "Sorcery", "oracle_text": "Suspend 4—{R}\nTarget creature gets +2/+0.", "mana_cost": "{4}{R}"}, 1, False)
        self.assertIn("pay_now_payoff_later", f["durdle_reasons"])
        saga = rm.card_facts("Saga", {"type_line": "Enchantment — Saga", "oracle_text": "I — Draw a card.", "mana_cost": "{2}"}, 1, False)
        self.assertIn("pay_now_payoff_later", saga["durdle_reasons"])

    def test_instant_speed(self):
        self.assertTrue(self.f("Counterspell")["instant_speed"])
        self.assertTrue(self.f("Ghostly Pilferer")["instant_speed"])
        self.assertFalse(self.f("Divination")["instant_speed"])


class TestTempo(unittest.TestCase):
    def test_hypergeom(self):
        self.assertAlmostEqual(rm._hyper_cdf_ge(100, 37, 7, 0), 1.0)
        p = rm._hyper_cdf_ge(100, 37, 10, 3)
        self.assertTrue(0.5 < p < 0.95)
        self.assertEqual(rm._hyper_cdf_ge(100, 2, 7, 3), 0.0)

    def test_more_lands_less_unspent_pressure_monotone(self):
        out = rm.compute_review(make_inputs())["curve_tempo"]
        pt = out["mana"]["per_turn"]
        self.assertEqual([p["turn"] for p in pt], [2, 3, 4, 5, 6])
        for p in pt:
            self.assertGreaterEqual(p["unspent"], 0)
            self.assertAlmostEqual(p["mana"], p["spend"] + p["unspent"], places=2)
        self.assertEqual(out["mana"]["lands"], 32)  # 30 Swamps + Temple + Command Tower
        self.assertAlmostEqual(out["mana"]["unspent_total_t2_t6"], sum(p["unspent"] for p in pt), places=3)

    def test_commander_on_curve(self):
        c = rm.compute_review(make_inputs())["curve_tempo"]["commander_on_curve"]
        self.assertEqual(c["cmc"], 3)
        self.assertGreaterEqual(c["p_with_ramp"], c["p_lands_only"])
        self.assertIn("lands", c["why"])

    def test_shares(self):
        t = rm.compute_review(make_inputs())["curve_tempo"]
        self.assertTrue(0 < t["active_share"]["value"] < 1)
        self.assertEqual(t["durdle_tax"]["by_reason"].get("tapland"), 1)
        self.assertGreater(t["flex"]["instant_speed"], 0)
        self.assertEqual(t["flex"]["instant_speed"], 2)
        names = {d["name"] for d in t["durdle_cards"]}
        self.assertIn("Pet Rock", names)
        self.assertIn("Temple of Silence", names)


class TestGenre(unittest.TestCase):
    def test_primary_secondary_and_why(self):
        g = rm.compute_review(make_inputs())["genre"]
        self.assertEqual(g["registry_version"], 7)
        self.assertEqual(g["primary"]["id"], "aristocrats")
        self.assertLessEqual(len(g["secondary"]), 2)
        self.assertNotIn("stax", [s["id"] for s in g["secondary"]])  # coverage 0
        self.assertTrue(g["primary"]["roles_hit"])
        self.assertEqual(g["ranking"][0]["id"], "aristocrats")

    def test_no_genre_when_none_cover(self):
        g = rm.compute_review(make_inputs(genres=[GENRES[1]]))["genre"]
        self.assertIsNone(g["primary"])
        self.assertEqual(g["secondary"], [])


class TestComposition(unittest.TestCase):
    def test_rows(self):
        c = rm.compute_review(make_inputs())["composition"]
        rows = {r["key"]: r for r in c["rows"]}
        self.assertEqual(rows["lands"]["actual"], 32)
        self.assertEqual(rows["ramp"]["actual"], 3)
        self.assertEqual(rows["lands"]["target"], 38)
        self.assertEqual(rows["lands"]["status"], "short")
        self.assertEqual(c["total_cards"], 32 + 18)


class TestFocus(unittest.TestCase):
    def test_lines_and_journal_diff(self):
        j = {"preferred_win_conditions": {"primary": "combat", "secondary": ["lockout", "combo"], "custom_win_conditions": ["vibes"],
                                          "disliked_or_excluded": ["mill"]}}
        f = rm.compute_review(make_inputs(journal=j))["focus"]
        types = [l["type"] for l in f["win_lines"]]
        self.assertTrue(1 <= len(types) <= 3)
        self.assertIn("combo", types)  # the in-deck combo
        self.assertIn("alternate_wincon", types)  # Laboratory Maniac
        self.assertIn("lockout", f["claimed_unsupported"])
        self.assertIn("combat", f["claimed_unsupported"])
        self.assertNotIn("combo", f["claimed_unsupported"])
        self.assertNotIn("combo", f["supported_unclaimed"])
        self.assertIn("alternate_wincon", f["supported_unclaimed"])
        self.assertEqual(f["journal"]["custom"], ["vibes"])

    def test_no_journal(self):
        f = rm.compute_review(make_inputs())["focus"]
        self.assertFalse(f["journal"]["present"])
        self.assertEqual(f["claimed_unsupported"], [])

    def test_engine_line(self):
        f = rm.compute_review(make_inputs(combos={"in_deck": [], "one_away": []}))["focus"]
        self.assertTrue(all(l["why"] for l in f["win_lines"]))


class TestStrongAndStragglers(unittest.TestCase):
    def setUp(self):
        self.out = rm.compute_review(make_inputs())

    def test_strong_requires_line_or_engine_and_active(self):
        for c in self.out["strong_cards"]["cards"]:
            self.assertTrue(c["in_win_line"] or c["in_engine"])
            self.assertTrue(c["active"])
        self.assertNotIn("Pet Rock", [c["name"] for c in self.out["strong_cards"]["cards"]])

    def test_stragglers_exclude_win_line_and_include_dead_cards(self):
        names = [c["name"] for c in self.out["stragglers"]["cards"]]
        self.assertIn("Pet Rock", names)
        lines = {c for l in self.out["focus"]["win_lines"] for c in l["cards"]}
        self.assertFalse(set(names) & lines)
        for c in self.out["stragglers"]["cards"]:
            self.assertGreaterEqual(c["straggler_score"], rm.STRAGGLER_MIN)
            self.assertTrue(c["why"])

    def test_durdle_pushes_over_threshold(self):
        rock = next(c for c in self.out["stragglers"]["cards"] if c["name"] == "Pet Rock")
        self.assertTrue(rock["durdle"])
        self.assertAlmostEqual(rock["straggler_score"], rock["weak_norm"] + rm.DURDLE_BONUS, places=3)

    def test_pet_cards_never_stragglers(self):
        for pets in (["Pet Rock"], ["pet rock"], ["Pet Rock", "Gnaw Pet", "Phyrexian Arena", "Rhythm of the Wild"]):
            out = rm.compute_review(make_inputs(pets=pets))
            names = {c["name"] for c in out["stragglers"]["cards"]}
            for p in pets:
                self.assertNotIn(p.title() if p == "pet rock" else p, names)
            self.assertNotIn("Pet Rock", names)
            prot = {c["name"] for c in out["stragglers"]["protected_pets"]}
            self.assertIn("Pet Rock", prot)
            self.assertTrue(all(c["protected"] for c in out["stragglers"]["protected_pets"]))

    def test_lands_and_commander_never_scored(self):
        names = {c["name"] for c in self.out["stragglers"]["cards"]} | {c["name"] for c in self.out["strong_cards"]["cards"]}
        self.assertFalse(names & {"Swamp", "Command Tower", "Temple of Silence", "Cmdr"})


class TestCombosEngines(unittest.TestCase):
    def test_sections(self):
        c = rm.compute_review(make_inputs())["combos_engines"]
        self.assertTrue(c["combos_available"])
        self.assertEqual(c["combos_in_deck"][0]["id"], "1-2")
        self.assertEqual(c["combos_one_away"][0]["missing"], ["Foo"])
        self.assertEqual(c["combos_one_away_total"], 1)
        self.assertTrue(c["engines"])
        self.assertTrue(all(e["summary"] for e in c["engines"]))

    def test_spellbook_down(self):
        c = rm.compute_review(make_inputs(combos={"in_deck": [], "one_away": [], "error": "boom"}))["combos_engines"]
        self.assertFalse(c["combos_available"])
        self.assertEqual(c["combos_error"], "boom")


class TestDeterminism(unittest.TestCase):
    def test_twice_byte_identical(self):
        a = rm.dumps(rm.compute_review(make_inputs(pets=["Pet Rock"])))
        b = rm.dumps(rm.compute_review(make_inputs(pets=["Pet Rock"])))
        self.assertEqual(a, b)
        self.assertEqual(hashlib_sha(a), hashlib_sha(b))

    def test_input_order_does_not_matter(self):
        inp = make_inputs()
        shuffled = make_inputs()
        shuffled["deck"]["cards"].reverse()
        shuffled["genres"].reverse()
        shuffled["engines"].reverse()
        shuffled["combos"]["one_away"].reverse()
        shuffled["graph"]["nodes"].reverse()
        self.assertEqual(rm.dumps(rm.compute_review(inp)), rm.dumps(rm.compute_review(shuffled)))

    def test_no_volatile_fields(self):
        s = rm.dumps(rm.compute_review(make_inputs()))
        for bad in ("fetched_at", "built_at", "updated_at", "generated_at"):
            self.assertNotIn(bad, s)
        json.loads(s)

    def test_inputs_not_mutated(self):
        inp = make_inputs()
        before = json.dumps(inp, sort_keys=True, default=str)
        rm.compute_review(inp)
        self.assertEqual(before, json.dumps(inp, sort_keys=True, default=str))

    def test_changing_deck_changes_fingerprint(self):
        a = rm.compute_review(make_inputs())
        inp = make_inputs()
        inp["deck"]["cards"][0]["quantity"] = 2
        self.assertNotEqual(a["fingerprint"], rm.compute_review(inp)["fingerprint"])


def hashlib_sha(s):
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()



class TestEndpoint(unittest.TestCase):
    def test_review_metrics_endpoint(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import app as app_module

        inp = make_inputs()
        with patch.object(app_module, "_find_deck_any", lambda _id: inp["deck"]), \
                patch.object(rm, "build_inputs", lambda r, deck, combos_client=None: inp):
            c = TestClient(app_module.app)
            a = c.get("/api/decks/1/review-metrics")
            b = c.get("/api/decks/1/review-metrics")
        self.assertEqual(a.status_code, 200)
        self.assertEqual(a.text, b.text)
        self.assertEqual(a.json()["review_metrics"]["schema"], rm.SCHEMA)
        with patch.object(app_module, "_find_deck_any", lambda _id: None):
            self.assertEqual(TestClient(app_module.app).get("/api/decks/nope/review-metrics").status_code, 404)


if __name__ == "__main__":
    unittest.main()
