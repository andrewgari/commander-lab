"""Card-name resolution and the fact index (what an LLM may cite as evidence). Offline."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.server.domain.analytics.review_pipeline import facts, names

FIX = Path(__file__).parent / "fixtures" / "fact_pack"


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.r = names.CardResolver({
            "Andúril, Flame of the West": "id-1", "Brass's Tunnel-Grinder // Tecutlan, the Searing Rift": "id-2",
            "Sol Ring": "id-3", "Rashmi and Ragavan": "id-4", "Mr. Foxglove": None})

    def test_exact_and_case_insensitive(self):
        self.assertEqual(self.r.resolve("Sol Ring"), "Sol Ring")
        self.assertEqual(self.r.resolve("sol ring"), "Sol Ring")

    def test_accents_and_curly_apostrophes_normalised_to_canonical(self):
        self.assertEqual(self.r.resolve("Anduril, Flame of the West"), "Andúril, Flame of the West")
        self.assertEqual(self.r.resolve("Brass’s Tunnel-Grinder"), "Brass's Tunnel-Grinder // Tecutlan, the Searing Rift")

    def test_front_face_resolves_double_faced(self):
        self.assertEqual(self.r.resolve("Brass's Tunnel-Grinder"), "Brass's Tunnel-Grinder // Tecutlan, the Searing Rift")

    def test_unknown_returns_none_never_fuzzy(self):
        self.assertIsNone(self.r.resolve("Sol Rings"))
        self.assertIsNone(self.r.resolve("Cyclonic Rift"))
        self.assertIsNone(self.r.resolve(""))

    def test_oracle_id_lookup_and_missing(self):
        self.assertEqual(self.r.oracle_id("Sol Ring"), "id-3")
        self.assertIsNone(self.r.oracle_id("Mr. Foxglove"))


def mini_pack():
    return {
        "schema": "fact_pack/2",
        "deck": {"commanders": ["Lara"], "color_identity": ["U", "R"], "card_count": 4},
        "curve": {"average_mv": 2.5, "buckets": [{"mv": 1, "count": 3, "label": "1"}, {"mv": 2, "count": 1, "label": "2"}]},
        "roles": {"roles": [{"role": "ramp", "count": 2, "cards": ["Sol Ring", "Arcane Signet"], "target_min": 9, "target_max": 11}]},
        "salt": {"status": "unavailable", "reason": "no source"},
        "bracket": {"minimum_bracket": 3, "reasons": [{"rule": "game_changers", "floor": 3, "cards": ["The One Ring"]}]},
        "cards": [
            {"name": "Sol Ring", "cmc": 1.0, "type_line": "Artifact", "price": "1.50", "oracle_id": "id-3",
             "edhrec": {"num_decks": 5, "potential_decks": 10, "synergy": 0.1}, "salt": {"status": "unavailable", "reason": "x"},
             "oracle_text": "{T}: Add {C}{C}."},
            {"name": "Lara", "cmc": 3.0, "type_line": "Legendary Creature", "price": {"status": "unavailable", "reason": "no price"},
             "oracle_id": "id-9", "edhrec": {"status": "unavailable", "reason": "x"}, "salt": {"status": "unavailable", "reason": "x"},
             "oracle_text": "Haste"},
        ],
    }


class FactIndexTests(unittest.TestCase):
    def setUp(self):
        self.idx = facts.FactIndex.build(mini_pack(), deck_cards=[("Sol Ring", 1), ("Lara", 1)])

    def test_exists_for_nested_scalar_and_node_paths(self):
        for k in ("curve.average_mv", "deck.color_identity", "roles.ramp.count", "roles.ramp", "roles.ramp.cards",
                  "bracket.minimum_bracket", "bracket.reasons.game_changers.floor", "curve.buckets.2.count"):
            self.assertTrue(self.idx.exists(k), k)

    def test_unknown_key_rejected(self):
        for k in ("roles.sac_outlet.count", "curve.nope", "", "roles.ramp.count.extra"):
            self.assertFalse(self.idx.exists(k), k)

    def test_unavailable_nodes_are_not_citable_evidence(self):
        self.assertFalse(self.idx.exists("salt"))
        self.assertIn("salt", self.idx.unavailable)
        self.assertEqual(self.idx.unavailable["salt"], "no source")

    def test_per_card_keys_use_exact_deck_name(self):
        self.assertTrue(self.idx.exists("card.Sol Ring.price_usd"))
        self.assertTrue(self.idx.exists("card.Sol Ring.edhrec_inclusion"))
        self.assertFalse(self.idx.exists("card.Lara.price_usd"))           # price unavailable
        self.assertFalse(self.idx.exists("card.Cyclonic Rift.price_usd"))  # not in deck
        self.assertEqual(self.idx.get("card.Sol Ring.price_usd"), 1.5)

    def test_derived_counts_are_computed_by_code(self):
        self.assertEqual(self.idx.get("derived.type_counts.artifact"), 1)
        self.assertEqual(self.idx.get("derived.type_counts.creature"), 1)
        self.assertEqual(self.idx.get("derived.price.total_usd"), 1.5)
        self.assertEqual(self.idx.get("derived.price.unpriced_cards"), ["Lara"])

    def test_render_slice_only_shows_requested_prefixes_and_marks_unavailable(self):
        text = self.idx.render(["curve", "salt", "roles"])
        self.assertIn("curve.average_mv = 2.5", text)
        self.assertIn("salt = UNAVAILABLE (no source)", text)
        self.assertIn("roles.ramp.count = 2", text)
        self.assertNotIn("bracket.", text)
        # every rendered key is citable (or explicitly marked UNAVAILABLE)
        for line in text.splitlines():
            key, _, val = line.partition(" = ")
            self.assertTrue(self.idx.exists(key) or val.startswith("UNAVAILABLE"), line)

    def test_render_is_deterministic(self):
        self.assertEqual(self.idx.render(["curve", "roles"]), facts.FactIndex.build(mini_pack(), deck_cards=[("Sol Ring", 1), ("Lara", 1)]).render(["curve", "roles"]))


class RealPackTests(unittest.TestCase):
    """The recorded golden fact pack from card 05 indexes cleanly."""

    def test_golden_pack_indexes(self):
        pack = json.loads((FIX / "golden_fact_pack.snapshot.json").read_text())
        deck = json.loads((FIX / "golden_deck.json").read_text())
        cards = [(c["name"] if isinstance(c, dict) else c, 1) for c in deck["cards"]]
        idx = facts.FactIndex.build(pack, deck_cards=cards)
        self.assertTrue(idx.exists("curve.average_mv"))
        self.assertTrue(len(idx.keys) > 50)


if __name__ == "__main__":
    unittest.main()


def pack_with_combos():
    p = mini_pack()
    p["combos"] = {
        "complete": [{"id": "1-2", "card_names": ["Sol Ring", "Lara"], "produces": ["Infinite mana"], "bracket_tag": "S",
                      "popularity": 10, "identity": "R", "url": "http://x"}],
        "near_misses": [{"id": "3-4", "card_names": ["Sol Ring", "Cyclonic Rift"], "produces": ["Infinite storm"],
                         "bracket_tag": "E", "popularity": 5, "identity": "U", "missing": ["Cyclonic Rift"]}],
    }
    p["edhrec"] = {"commander": {"name": "Lara", "num_decks": 100, "rank": 7},
                   "present": [{"name": "Sol Ring", "inclusion": 0.5, "synergy": 0.1, "num_decks": 50}],
                   "high_synergy_absent": [{"name": "Cyclonic Rift", "inclusion": 0.4, "synergy": 0.5}],
                   "deviation_score": 0.4}
    return p


class PromptViewTests(unittest.TestCase):
    def setUp(self):
        self.idx = facts.FactIndex.build(pack_with_combos(), deck_cards=[("Sol Ring", 1), ("Lara", 1)])

    def test_non_deck_card_names_never_appear_in_any_rendered_fact(self):
        text = self.idx.render(["combos", "edhrec"])
        self.assertNotIn("Cyclonic Rift", text)
        self.assertEqual(self.idx.get("edhrec.high_synergy_absent_count"), 1)

    def test_near_miss_exposes_missing_count_and_present_cards_only(self):
        self.assertEqual(self.idx.get("combos.near_misses.3-4.missing_count"), 1)
        self.assertEqual(self.idx.get("combos.near_misses.3-4.present_cards"), ["Sol Ring"])
        self.assertTrue(self.idx.exists("combos.complete.1-2.produces"))

    def test_noise_fields_dropped(self):
        self.assertFalse(self.idx.exists("combos.complete.1-2.popularity"))
        self.assertFalse(self.idx.exists("edhrec.present.Sol Ring.num_decks"))
        self.assertTrue(self.idx.exists("edhrec.present.Sol Ring.inclusion"))

    def test_near_misses_capped(self):
        p = pack_with_combos()
        p["combos"]["near_misses"] = [{"id": f"n{i}", "card_names": ["Sol Ring"], "produces": ["x"], "bracket_tag": "E",
                                       "popularity": i, "missing": ["Z"]} for i in range(40)]
        idx = facts.FactIndex.build(p, deck_cards=[("Sol Ring", 1), ("Lara", 1)])
        ids = {k.split(".")[2] for k in idx.leaves if k.startswith("combos.near_misses.")}
        self.assertEqual(len(ids), facts.MAX_NEAR_MISSES)
        self.assertIn("n39", ids)   # most popular kept


class BudgetInventoryTests(unittest.TestCase):
    def build(self, **kw):
        p = mini_pack()
        p["cards"].append({"name": "Mana Crypt", "cmc": 0.0, "type_line": "Artifact", "price": "150.00", "oracle_id": "id-5",
                           "edhrec": {"status": "unavailable", "reason": "x"}, "salt": {"status": "unavailable", "reason": "x"},
                           "oracle_text": "x"})
        return facts.FactIndex.build(p, deck_cards=[("Sol Ring", 1), ("Lara", 1), ("Mana Crypt", 1)], **kw)

    def test_over_cap_lists_computed_by_code(self):
        idx = self.build(caps=facts.BudgetCaps(soft=5.0, hard=100.0))
        self.assertEqual(idx.get("derived.price.over_hard_cap"), ["Mana Crypt"])
        self.assertEqual(idx.get("derived.price.over_soft_cap"), ["Mana Crypt"])
        self.assertEqual(idx.get("derived.price.hard_cap_usd"), 100.0)

    def test_no_caps_is_explicit_unavailable_not_empty_list(self):
        idx = self.build()
        self.assertFalse(idx.exists("derived.price.over_hard_cap"))
        self.assertIn("derived.price.over_hard_cap", idx.unavailable)

    def test_inventory_unavailable_when_not_provided(self):
        idx = self.build()
        self.assertIn("derived.inventory", idx.unavailable)

    def test_inventory_counts_from_owned_map(self):
        owned = {"sol ring": {"copies": 2, "available": 1, "decks": ["Other"]}}
        idx = self.build(owned=owned)
        self.assertEqual(idx.get("derived.inventory.owned_cards"), ["Sol Ring"])
        self.assertEqual(idx.get("derived.inventory.owned_count"), 1)
        self.assertEqual(idx.get("derived.inventory.not_owned_count"), 2)
        self.assertEqual(idx.get("card.Sol Ring.owned_copies"), 2)
        self.assertEqual(idx.get("derived.inventory.not_owned_price_usd"), 150.0)   # Lara unpriced; Crypt unowned


class CardTableTests(unittest.TestCase):
    def test_table_has_one_row_per_deck_card_with_oracle_text_roles_and_commander_flag(self):
        p = mini_pack()
        t = facts.render_card_table(p, [("Lara", 1), ("Sol Ring", 1)], owned=None, commanders=["Lara"])
        lines = t.splitlines()
        self.assertEqual(len([l for l in lines if "|" in l]), 3)   # header + 2 rows
        row = next(l for l in lines if l.startswith("Sol Ring"))
        self.assertIn("ramp", row)
        self.assertIn("$1.50", row)
        self.assertIn("{T}: Add {C}{C}.", row)
        self.assertIn("COMMANDER", next(l for l in lines if l.startswith("Lara")))

    def test_unpriced_card_shows_unavailable_not_zero(self):
        t = facts.render_card_table(mini_pack(), [("Lara", 1)], owned=None, commanders=["Lara"])
        self.assertIn("price n/a", t)
        self.assertNotIn("$0", t)

    def test_long_oracle_text_truncated(self):
        p = mini_pack()
        p["cards"][0]["oracle_text"] = "word " * 400
        t = facts.render_card_table(p, [("Sol Ring", 1)], owned=None, commanders=[])
        self.assertLess(len(t), 1200)
        self.assertIn("…", t)

    def test_owned_column_when_inventory_given(self):
        t = facts.render_card_table(mini_pack(), [("Sol Ring", 1)], owned={"sol ring": {"copies": 1, "available": 1, "decks": []}}, commanders=[])
        self.assertIn("owned", t.lower())


class WildcardAndUnavailableTests(unittest.TestCase):
    def setUp(self):
        self.idx = facts.FactIndex.build(mini_pack(), deck_cards=[("Sol Ring", 1), ("Lara", 1)])

    def test_wildcard_prefix_matches_one_segment(self):
        text = self.idx.render(["roles.*.count"])
        self.assertIn("roles.ramp.count = 2", text)
        self.assertNotIn("roles.ramp.cards", text)

    def test_unavailable_reason_for_node_and_children(self):
        self.assertEqual(self.idx.unavailable_reason("salt"), "no source")
        self.assertEqual(self.idx.unavailable_reason("salt.deck_salt"), "no source")
        self.assertIsNone(self.idx.unavailable_reason("curve.average_mv"))
