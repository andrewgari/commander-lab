"""BR-3: deterministic bracket checks on a decklist (analytics/bracket_check.py).

Fixture decklists (tests/fixtures/bracket_check_decks.json) carry the real
Commander Spellbook find-my-combos `included` bucket for each list, so the
combo rule runs on real Spellbook data. No network, no Redis.

Run: python -m unittest tests.test_bracket_check
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import bracket_check as bc  # noqa: E402
from analytics import brackets as br  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bracket_check_decks.json")


def load(key):
    with open(FIXTURE, encoding="utf-8") as f:
        d = json.load(f)["decks"][key]
    return {"name": d["name"], "commanders": d["commanders"], "cards": d["cards"]}, d["spellbook_in_deck"]


def check(key, **kw):
    deck, combos = load(key)
    return bc.check_deck_brackets(deck, combos=combos, **kw)


GC = br.load_fallback().cards


class PreconLikeDeck(unittest.TestCase):
    """Lathril elves, one Time Warp, no Game Changers, no combos: Bracket 2."""

    def setUp(self):
        self.f = check("precon_b2", game_changers=GC)

    def test_minimum_bracket_is_2(self):
        self.assertEqual(self.f.minimum_bracket, 2)
        self.assertEqual(self.f.minimum_bracket_name, "Core")

    def test_no_game_changers(self):
        self.assertEqual(self.f.game_changers.count, 0)
        self.assertEqual(self.f.game_changers.cards, [])
        self.assertEqual(self.f.game_changers.floor, 1)

    def test_the_extra_turn_card_is_the_only_floor(self):
        self.assertEqual([h.name for h in self.f.extra_turns.cards], ["Time Warp"])
        self.assertEqual(self.f.extra_turns.loops, [])
        self.assertEqual([(fl.rule, fl.floor, fl.cards) for fl in self.f.floors],
                         [("extra_turns", 2, ["Time Warp"])])

    def test_no_land_denial_or_combos(self):
        self.assertEqual(self.f.mass_land_denial.count, 0)
        self.assertTrue(self.f.two_card_combos.checked)
        self.assertEqual(self.f.two_card_combos.count, 0)


class GameChangerHeavyDeck(unittest.TestCase):
    """Tergrid + 7 Game Changers + Armageddon + Liliana of the Veil."""

    EXPECTED_GC = ["Tergrid, God of Fright // Tergrid's Lantern", "Rhystic Study", "Demonic Tutor",
                   "Vampiric Tutor", "Necropotence", "Smothering Tithe", "Cyclonic Rift", "The One Ring"]

    def setUp(self):
        self.f = check("gc_heavy", game_changers=GC)

    def test_game_changers_counted_with_the_commander(self):
        # Matches Spellbook's estimate-bracket gameChanger flags for this list.
        self.assertEqual(self.f.game_changers.cards, self.EXPECTED_GC)
        self.assertEqual(self.f.game_changers.count, 8)
        self.assertEqual(self.f.game_changers.floor, 4)

    def test_mass_land_denial_cards(self):
        self.assertEqual([h.name for h in self.f.mass_land_denial.cards], ["Armageddon", "Liliana of the Veil"])
        self.assertEqual(self.f.mass_land_denial.floor, 4)

    def test_minimum_bracket_4_with_both_floors_named(self):
        self.assertEqual(self.f.minimum_bracket, 4)
        rules = {fl.rule: fl for fl in self.f.floors}
        self.assertEqual(set(rules), {"game_changers", "mass_land_denial"})
        self.assertEqual(rules["game_changers"].cards, self.EXPECTED_GC)

    def test_tutors_are_advisory_and_listed(self):
        self.assertEqual(self.f.tutors.kind, br.ADVISORY)
        self.assertEqual({h.name for h in self.f.tutors.cards}, {"Demonic Tutor", "Vampiric Tutor"})
        self.assertNotIn("tutors", {fl.rule for fl in self.f.floors})

    def test_three_game_changers_is_bracket_3(self):
        deck, _ = load("gc_heavy")
        keep = {"Rhystic Study", "Demonic Tutor", "Smothering Tithe", "Sol Ring", "Island"}
        deck = {"commanders": [], "cards": [c for c in deck["cards"] if c["name"] in keep]}
        f = bc.check_deck_brackets(deck, game_changers=GC, combos=[])
        self.assertEqual(f.game_changers.count, 3)
        self.assertEqual(f.minimum_bracket, 3)
        deck["cards"].append({"name": "Necropotence", "quantity": 1})
        self.assertEqual(bc.check_deck_brackets(deck, game_changers=GC, combos=[]).minimum_bracket, 4)


class ComboDeck(unittest.TestCase):
    """Kinnan: Thoracle + Consultation, Kinnan + Basalt Monolith, Sage of Hours + Ezuri (infinite turns)."""

    def setUp(self):
        self.f = check("combo", game_changers=GC)

    def test_two_card_combos_with_their_bracket_tags(self):
        got = {tuple(sorted(c.cards)): (c.bracket_tag, c.bracket_tag_name, c.early) for c in self.f.two_card_combos.combos}
        self.assertEqual(got, {
            ("Demonic Consultation", "Thassa's Oracle"): ("R", "Ruthless", True),
            ("Basalt Monolith", "Kinnan, Bonder Prodigy"): ("R", "Ruthless", True),
            ("Ezuri, Claw of Progress", "Sage of Hours"): ("R", "Ruthless", True),
        })
        self.assertEqual(self.f.two_card_combos.floor, 4)
        for c in self.f.two_card_combos.combos:
            self.assertTrue((c.url or "").startswith("https://commanderspellbook.com/combo/"))

    def test_extra_turn_loop_found_from_spellbook(self):
        self.assertEqual({h.name for h in self.f.extra_turns.cards}, {"Time Warp", "Sage of Hours"})
        self.assertEqual([sorted(c.cards) for c in self.f.extra_turns.loops], [["Ezuri, Claw of Progress", "Sage of Hours"]])
        self.assertEqual(self.f.extra_turns.floor, 4)

    def test_kinnan_is_no_longer_a_game_changer(self):
        # Removed 2025-10-21; Thassa's Oracle is still on the list.
        self.assertEqual(self.f.game_changers.cards, ["Thassa's Oracle"])
        self.assertEqual(self.f.game_changers.floor, 3)

    def test_minimum_bracket_4_and_never_5(self):
        self.assertEqual(self.f.minimum_bracket, 4)
        self.assertEqual(self.f.floors[0].floor, 4)

    def test_floor_cards_name_the_combo_pieces(self):
        rules = {fl.rule: fl for fl in self.f.floors}
        self.assertIn("Thassa's Oracle", rules["two_card_combos"].cards)
        self.assertIn("Basalt Monolith", rules["two_card_combos"].cards)
        self.assertEqual(rules["extra_turns"].cards, ["Ezuri, Claw of Progress", "Sage of Hours"])


class LateGameComboDeck(unittest.TestCase):
    def test_spicy_two_card_combo_is_bracket_3(self):
        f = check("spicy_only", game_changers=GC)
        self.assertEqual([(sorted(c.cards), c.bracket_tag, c.early) for c in f.two_card_combos.combos],
                         [(["Kaalia of the Vast", "Master of Cruelties"], "S", False)])
        self.assertEqual(f.two_card_combos.floor, 3)
        self.assertEqual(f.minimum_bracket, 3)
        self.assertEqual(f.floors[0].cards, ["Kaalia of the Vast", "Master of Cruelties"])


class Rules(unittest.TestCase):
    def test_empty_deck_is_bracket_1_with_a_note(self):
        f = bc.check_deck_brackets([], game_changers=GC, combos=[])
        self.assertEqual(f.minimum_bracket, 1)
        self.assertEqual(f.floors, [])
        self.assertTrue(any("Exhibition" in n for n in f.notes))

    def test_combos_none_means_not_checked(self):
        deck, _ = load("combo")
        f = bc.check_deck_brackets(deck, game_changers=GC, combos=None)
        self.assertFalse(f.two_card_combos.checked)
        self.assertTrue(any("not checked" in n for n in f.notes))
        self.assertEqual(f.extra_turns.floor, 2)  # no loop data -> just the cards

    def test_accepts_deck_combos_output_shape(self):
        deck, combos = load("spicy_only")
        f = bc.check_deck_brackets(deck, game_changers=GC, combos={"in_deck": combos, "one_away": []})
        self.assertEqual(f.two_card_combos.count, 1)

    def test_three_card_combos_and_template_requirements_dont_count(self):
        three = {"id": "x", "card_names": ["A", "B", "C"], "cards": [{"name": n, "quantity": 1} for n in "ABC"],
                 "requires": [], "bracket_tag": "R", "produces": []}
        templ = {"id": "y", "card_names": ["A", "B"], "cards": [{"name": n, "quantity": 1} for n in "AB"],
                 "requires": [{"template": "Sac outlet"}], "bracket_tag": "R", "produces": []}
        f = bc.check_deck_brackets(["A", "B", "C"], game_changers=[], combos=[three, templ])
        self.assertEqual(f.two_card_combos.count, 0)
        self.assertEqual(f.minimum_bracket, 1)

    def test_banned_tag_counts_as_early(self):
        v = {"id": "z", "card_names": ["A", "B"], "requires": [], "bracket_tag": "B", "produces": []}
        f = bc.check_deck_brackets(["A", "B"], game_changers=[], combos=[v])
        self.assertEqual(f.two_card_combos.floor, 4)

    def test_double_faced_and_case_insensitive_names(self):
        f = bc.check_deck_brackets(["tergrid, god of fright", "ARMAGEDDON"], game_changers=GC, combos=[])
        self.assertEqual(f.game_changers.count, 1)
        self.assertEqual(f.mass_land_denial.count, 1)

    def test_commander_only_listed_in_commanders_still_counts(self):
        deck = {"commanders": ["Tergrid, God of Fright"], "cards": [{"name": "Island", "quantity": 1}]}
        f = bc.check_deck_brackets(deck, game_changers=GC, combos=[])
        self.assertEqual(f.game_changers.cards, ["Tergrid, God of Fright"])
        self.assertEqual(f.minimum_bracket, 3)

    def test_duplicates_and_commander_counted_once(self):
        deck = {"commanders": ["Demonic Tutor"], "cards": [{"name": "Demonic Tutor", "quantity": 1},
                                                           {"name": "Demonic Tutor", "quantity": 1}]}
        self.assertEqual(bc.check_deck_brackets(deck, game_changers=GC, combos=[]).game_changers.count, 1)

    def test_oracle_text_fallback_for_cards_missing_from_the_lists(self):
        oracle = {"New Turn Spell": {"oracle_text": "Take an extra turn after this one."},
                  "New Wipe": {"oracle_text": "Destroy all lands. Draw a card."}}
        f = bc.check_deck_brackets(["New Turn Spell", "New Wipe"], game_changers=[], combos=[], oracle=oracle,
                                   snapshot={"mass_land_denial": [], "extra_turns": [], "tutors": []})
        self.assertEqual([(h.name, h.source) for h in f.extra_turns.cards], [("New Turn Spell", "oracle_text")])
        self.assertEqual([(h.name, h.source) for h in f.mass_land_denial.cards], [("New Wipe", "oracle_text")])
        self.assertEqual(f.minimum_bracket, 4)

    def test_wotc_examples_pinned_even_if_snapshot_drops_them(self):
        f = bc.check_deck_brackets(["Winter Orb", "Blood Moon"], game_changers=[], combos=[],
                                   snapshot={"mass_land_denial": [], "extra_turns": [], "tutors": []})
        self.assertEqual(f.mass_land_denial.count, 2)

    def test_extra_turn_card_plus_other_infinite_is_not_a_loop(self):
        # Gonti's Aether Heart + Decoction Module + Whirler Virtuoso: infinite tokens, but the Heart exiles itself.
        v = {"id": "g", "card_names": ["Gonti's Aether Heart", "Whirler Virtuoso"], "requires": [],
             "bracket_tag": "S", "produces": ["Infinite colorless thopter tokens"]}
        f = bc.check_deck_brackets(["Gonti's Aether Heart", "Whirler Virtuoso"], game_changers=[], combos=[v])
        self.assertEqual(f.extra_turns.loops, [])
        self.assertEqual(f.extra_turns.floor, 2)

    def test_infinite_turns_with_unverified_template_is_a_note_not_a_floor(self):
        v = {"id": "t", "card_names": ["Sage of Hours"], "requires": [{"template": "Proliferate engine"}],
             "bracket_tag": "R", "produces": ["Infinite turns"]}
        f = bc.check_deck_brackets(["Sage of Hours"], game_changers=[], combos=[v])
        self.assertEqual(f.extra_turns.loops, [])
        self.assertEqual(f.extra_turns.floor, 2)
        self.assertEqual(f.minimum_bracket, 2)
        self.assertTrue(any("Proliferate engine" in n for n in f.notes))

    def test_live_helper_uses_find_my_combos_not_ownership_scan(self):
        import asyncio
        from unittest.mock import patch
        from analytics.interactions import combos as cm
        deck, combos = load("spicy_only")

        class R:
            def get(self, k):
                return None

            def mget(self, ks):
                return [None] * len(ks)

            def keys(self, pattern):
                raise AssertionError("KEYS must not be called")

        async def gc(_r):
            return br.GameChangerList(cards=GC, source="cache", fetched_at="x")

        with patch.object(br, "get_game_changers", gc), \
                patch.object(cm.SpellbookClient, "find_my_combos",
                             lambda self, c, m, refresh=False: {"included": combos, "fetched_at": "t"}):
            f = asyncio.run(bc.check_deck_brackets_live(R(), deck))
        self.assertEqual(f.two_card_combos.count, 1)
        self.assertEqual(f.data["game_changers_source"], "cache")

    def test_many_extra_turns_without_a_loop_note_but_floor_2(self):
        f = bc.check_deck_brackets(["Time Warp", "Temporal Manipulation", "Time Stretch"], game_changers=[], combos=[])
        self.assertEqual(f.extra_turns.floor, 2)
        self.assertTrue(any("check it by hand" in n for n in f.notes))

    def test_deterministic_and_input_untouched(self):
        deck, combos = load("combo")
        before = copy.deepcopy((deck, combos))
        a = bc.check_deck_brackets(deck, game_changers=GC, combos=combos).model_dump()
        b = bc.check_deck_brackets(deck, game_changers=GC, combos=combos).model_dump()
        self.assertEqual(a, b)
        self.assertEqual((deck, combos), before)
        json.dumps(a)  # JSON-serializable

    def test_default_game_changers_is_the_br1_snapshot(self):
        f = bc.check_deck_brackets(["Rhystic Study"], combos=[])
        self.assertEqual(f.game_changers.count, 1)
        self.assertEqual(f.data["game_changers_source"], "snapshot")

    def test_minimum_never_exceeds_4(self):
        for key in ("precon_b2", "gc_heavy", "combo", "spicy_only"):
            self.assertLessEqual(check(key, game_changers=GC).minimum_bracket, 4)


class Snapshot(unittest.TestCase):
    def test_snapshot_has_the_three_lists_and_wotc_examples(self):
        snap = bc._load_snapshot()
        for k in bc.SCRYFALL_QUERIES:
            self.assertGreater(len(snap[k]), 20, k)
        lists = bc.card_lists()
        for n in ("Armageddon", "Ruination", "Sunder", "Winter Orb", "Blood Moon"):
            self.assertIn(n.lower(), lists["mass_land_denial"])
        for n in ("Time Warp", "Nexus of Fate"):
            self.assertIn(n.lower(), lists["extra_turns"])
        for n in ("Demonic Tutor", "Vampiric Tutor"):
            self.assertIn(n.lower(), lists["tutors"])


if __name__ == "__main__":
    unittest.main()
