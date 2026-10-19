"""Comprehensive unit tests for presetgen-v1 (spec §9), beyond the backend smoke suite.

Where tests/test_presetgen_backend.py walks the happy path of each fixture family, this file
pins down the arithmetic exactly (every hand-worked value from presetgen.md §9), the edge cases
around it (partner libraries, clamps, ties in the trim order, one-op-per-ref merges, pillar
locks, FIT_BLOCKED) and the AI contract: mocked LLM chains for every generate/improve status
(ok / off / fallback with reason / skipped), candidate discipline (A9-A12) and the §9.6
invariants. All fixtures are mock packets; no Redis, no network, no real LLM.

Run: python -m pytest tests/test_presetgen_comprehensive.py
"""
import copy
import json
import math
import os
import sys
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import composition_ai as ca
from analytics import composition_presets as cp
from analytics import presetgen as pg
from tests.test_identity import JsonProvider
from tests.test_presetgen_backend import (by_ref, chain, deck_g1, deck_g2, deck_g3, deck_g4,
                                          pk, preset, slot)


def draft_from(base_result, mutate=None, name="AI Draft", lands=None):
    """A GENERATE-shaped payload built from a Baseline, optionally mutated."""
    p = copy.deepcopy(base_result["preset"])
    if mutate:
        mutate(p)
    return {"preset": {"name": name, "description": "d",
                       "structure": {"lands": lands or p["structure"]["lands"]}, "slots": p["slots"]},
            "lands_math": "Burgess 38; Karsten 39.8", "summary": "ok"}


# ---------------------------------------------------------------------------------------------
# §2 / §9.1 — reliability function (P1-P7)
# ---------------------------------------------------------------------------------------------

class ReliabilityExactTests(unittest.TestCase):
    """P1-P4 re-derived independently, not just read off the spec table."""

    def hypergeo_miss(self, k, n, library):
        """P(miss all k) = C(L-k,n)/C(L,n), computed as an exact product fraction."""
        num = den = 1
        for i in range(n):
            num *= (library - k - i)
            den *= (library - i)
        return float(Fraction(num, den))

    def test_p1_ramp_floor(self):
        self.assertAlmostEqual(self.hypergeo_miss(10, 9, 99), 0.367, places=3)
        self.assertEqual(round(pg.reliability(10, 9, 99), 3), 0.633)

    def test_p2_draw_floor(self):
        self.assertEqual(round(pg.reliability(7, 11, 99), 3), 0.574)

    def test_p3_wipe_floor(self):
        self.assertEqual(round(pg.reliability(2, 14, 99), 3), 0.264)

    def test_p4_partner_library_98(self):
        # spec P4: miss = prod_{i=0..8} (86-i)/(98-i) = 0.2922 -> p = 0.708
        miss = 1.0
        for i in range(9):
            miss *= (86 - i) / (98 - i)
        self.assertAlmostEqual(miss, 0.2922, places=4)
        self.assertEqual(round(pg.reliability(12, 9, 98), 3), 0.708)

    def test_p5_zero_copies_no_entry(self):
        self.assertEqual(pg.reliability(0, 9, 99), 0.0)
        p = preset(37, slot("core:ramp", target=0), slot("core:card_advantage", 10))
        entries = pg.reliability_entries(p, 99)
        self.assertNotIn("core:ramp", [e["ref"] for e in entries])
        self.assertEqual([e["ref"] for e in entries], ["core:card_advantage"])

    def test_p5_negative_and_zero_turns(self):
        self.assertEqual(pg.reliability(-3, 9, 99), 0.0)
        self.assertEqual(pg.reliability(10, 0, 99), 0.0)

    def test_p6_certainty_boundary(self):
        # k >= L - n + 1 guarantees a hit: at n=8, L=99 the boundary is k=92.
        self.assertEqual(pg.reliability(92, 8, 99), 1.0)
        # one short of certainty must NOT be clamped to 1.0 (catches an off-by-one in the guard)
        p91 = pg.reliability(91, 8, 99)
        self.assertLess(p91, 1.0)
        self.assertAlmostEqual(p91, 1 - Fraction(math.comb(8, 8), math.comb(99, 8)), places=12)
        # math.comb returns 0 for k > n, so the guard is redundant only where C(L-k, n) == 0;
        # exercise it with n < L - k so a widened guard changes the value:
        self.assertEqual(round(pg.reliability(40, 30, 99), 6), round(1 - Fraction(math.comb(59, 30), math.comb(99, 30)), 6))

    def test_p7_floors_agree_with_the_model(self):
        rows = [("core:ramp", 0.60, 10), ("core:ramp@big_commander", 0.70, 12),
                ("core:card_advantage", 0.55, 7), ("group:targeted_interaction", 0.70, 10),
                ("core:board_wipe", 0.25, 2), ("core:finisher", 0.25, 2)]
        for key, p_star, floor in rows:
            turn = pg.CONFIG["reliability"][key]["turn"]
            n = 7 + turn
            self.assertGreaterEqual(pg.reliability(floor, n, 99), p_star, key)
            self.assertLess(pg.reliability(floor - 1, n, 99), p_star, key)

    def test_p7_agrees_with_autotag_base_floors(self):
        self.assertEqual(ca.BASE_FLOORS["core:ramp"], pg.CONFIG["reliability"]["core:ramp"] and 10)
        self.assertEqual(ca.BASE_FLOORS["core:card_advantage"], 7)
        self.assertEqual(ca.BASE_FLOORS["group:targeted_interaction"], 10)
        self.assertEqual(ca.BASE_FLOORS["core:board_wipe"], 2)
        self.assertEqual(ca.BASE_FLOORS["core:finisher"], 2)
        self.assertEqual(ca.modified_floors(5, {}, False)["core:ramp"], 12)
        self.assertEqual(ca.modified_floors(4, {}, False)["core:ramp"], 10)

    def test_display_turns_table(self):
        expected = {"core:mana_fixing": 3, "core:removal": 4, "core:counterspell": 4, "group:interaction": 4,
                    "core:protection": 5, "core:recursion": 6, "core:tutor": 6}
        for ref, turn in expected.items():
            self.assertEqual(pg.reliability_turn(ref), turn, ref)
        # prefix fallbacks (§2 display table: theme/type/cat -> 4, facet -> 6)
        for ref, turn in (("theme:goblin", 4), ("type:creature", 4), ("cat:g-1", 4),
                          ("facet:answers_stack", 6), ("facet:whatever", 6)):
            self.assertEqual(pg.reliability_turn(ref), turn, ref)
        self.assertIsNone(pg.reliability_turn("core:not_a_ref"))

    def test_entry_shape_is_exact(self):
        p = preset(37, slot("core:ramp", target=10))
        self.assertEqual(pg.reliability_entries(p, 99),
                         [{"ref": "core:ramp", "turn": 2, "cards_seen": 9, "k": 10, "p": 0.633, "library": 99}])

    def test_min_slot_lower_bound_counts(self):
        p = preset(37, slot("core:protection", min=3))
        e = pg.reliability_entries(p, 99)[0]
        self.assertEqual((e["k"], e["turn"], e["p"]), (3, 5, round(pg.reliability(3, 12, 99), 3)))


# ---------------------------------------------------------------------------------------------
# §3 / §9.2 — land math (L1-L6)
# ---------------------------------------------------------------------------------------------

class LandMathFixtureTests(unittest.TestCase):
    CASES = [  # (ci, mv, avg, cheap, mdfc, burgess, karsten, window_lo, window_hi, lands)
        (1, 6, 3.4, 8, 0.0, 38, 39.822, 36.0, 41.822, 39),   # L1
        (1, 4, 2.9, 9, 0.0, 36, 37.977, 34.0, 39.977, 37),   # L2
        (4, 4, 3.3, 10, 0.0, 39, 38.949, 36.949, 41.0, 39),  # L3
        (5, 5, 3.6, 6, 0.0, 41, 41.008, 39.0, 43.008, 41),   # L4
        (1, 6, 3.4, 8, 1.14, 38, 39.822, 36.0, 41.822, 38),  # L5 (3 non-mythic MDFCs)
        (1, 1, 2.0, 15, 0.0, 33, 33.48, 31.0, 35.48, 33),    # L6
    ]

    def test_all_six_rows(self):
        for ci, mv, avg, cheap, mdfc, b, k, lo, hi, lands in self.CASES:
            r = pg.land_math(ci, mv, avg, cheap, mdfc)
            tag = f"({ci},{mv},{avg},{cheap},{mdfc})"
            self.assertEqual(r["burgess"], b, tag)
            self.assertAlmostEqual(r["karsten"], k, places=3, msg=tag)
            self.assertAlmostEqual(r["window"][0], lo, places=3, msg=tag)
            self.assertAlmostEqual(r["window"][1], hi, places=3, msg=tag)
            self.assertEqual(r["lands"], lands, tag)

    def test_mean_before_mdfc(self):
        r = pg.land_math(1, 6, 3.4, 8)
        self.assertAlmostEqual(r["mean"], 38.911, places=3)
        self.assertAlmostEqual(r["raw"], 38.911, places=3)
        r5 = pg.land_math(1, 6, 3.4, 8, 1.14)
        self.assertAlmostEqual(r5["raw"], 37.771, places=3)

    def test_lands_math_string_formats(self):
        self.assertEqual(pg.land_math(1, 6, 3.4, 8)["text"],
                         "Burgess 31+1+6 = 38; Karsten 31.42+3.13·3.4−0.28·8 = 39.8; mean 38.9 → 39")
        t = pg.land_math(1, 6, 3.4, 8, 1.14)["text"]
        self.assertIn(" − 1.14 MDFC", t)
        self.assertTrue(t.endswith("→ 38"))

    def test_clamp_to_window_edges(self):
        # huge MDFC subtraction clamps up to ceil(lo); negative clamp goes down to floor(hi)
        r = pg.land_math(1, 1, 2.0, 15, 30.0)
        self.assertEqual(r["lands"], 31)                      # ceil(31) after raw 33.24 - 30
        r2 = pg.land_math(1, 1, 2.0, 0, 0.0)                  # karsten 31.42, burgess 33 -> hi 35
        self.assertLessEqual(r2["lands"], math.floor(r2["window"][1]))
        self.assertGreaterEqual(r2["lands"], math.ceil(r2["window"][0]))

    def test_round_half_up_not_bankers(self):
        self.assertEqual(pg.round_half_up(38.5), 39)          # Python round() would give 38
        self.assertEqual(pg.round_half_up(39.5), 40)
        self.assertEqual(pg.round_half_up(38.4999), 38)

    def test_mdfc_equivalents_rules(self):
        deck = {"commanders": ["Cmdr"], "cards": [
            {"name": "SpellFrontMythic", "quantity": 1},      # counted at 0.74
            {"name": "SpellFrontRare", "quantity": 2},        # counted at 0.38 each
            {"name": "LandFrontMDFC", "quantity": 1},         # land front: not counted
            {"name": "SplitCreature", "quantity": 1},         # creature // creature: not counted
            {"name": "CmdrMdfc", "quantity": 1},              # commander: excluded
            {"name": "PlainLand", "quantity": 3},             # no "//": not counted
        ]}
        texts = {
            "SpellFrontMythic": {"type_line": "Instant // Land", "rarity": "mythic"},
            "SpellFrontRare": {"type_line": "Creature // Land", "rarity": "rare"},
            "LandFrontMDFC": {"type_line": "Land // Creature", "rarity": "uncommon"},
            "SplitCreature": {"type_line": "Creature // Creature", "rarity": "rare"},
            "CmdrMdfc": {"type_line": "Sorcery // Land", "rarity": "mythic"},
            "PlainLand": {"type_line": "Basic Land — Forest"},
        }
        deck["commanders"].append("CmdrMdfc")
        self.assertEqual(pg.mdfc_equivalents(deck, texts), round(0.74 + 2 * 0.38, 2))

    def test_mdfc_weight_config_respected(self):
        orig = dict(pg.CONFIG["mdfc_land_weight"])
        try:
            pg.CONFIG["mdfc_land_weight"] = {"nonmythic": 0.5, "mythic": 1.0}
            deck = {"commanders": [], "cards": [{"name": "X", "quantity": 2}]}
            texts = {"X": {"type_line": "Sorcery // Land", "rarity": "rare"}}
            self.assertEqual(pg.mdfc_equivalents(deck, texts), 1.0)
        finally:
            pg.CONFIG["mdfc_land_weight"] = orig


# ---------------------------------------------------------------------------------------------
# §4 / §9.3 — Baseline generation (G1-G4), pinned to the hand-worked values
# ---------------------------------------------------------------------------------------------

class BaselineFixtureTests(unittest.TestCase):

    def test_g1_full_contract(self):
        b = pg.baseline(deck_g1())
        p = b["preset"]
        self.assertEqual(b["selected"]["preset_id"], "big_mana")
        self.assertEqual(b["selected"]["reason"], {"step": 2, "signal": "journal primary: ramp"})
        self.assertEqual(p["structure"], {"deck_size": 100, "commanders": 1, "lands": 39})
        s = by_ref(p)
        self.assertEqual(sorted(s), ["core:board_wipe", "core:card_advantage", "core:ramp",
                                     "group:targeted_interaction"])
        self.assertEqual({r: x["target"] for r, x in s.items()},
                         {"core:ramp": 14, "core:card_advantage": 10, "group:targeted_interaction": 10,
                          "core:board_wipe": 3})
        self.assertEqual(b["plan_room"], 23)
        self.assertEqual(b["notes"], [])
        self.assertEqual(b["reasons"], {})                     # M1 no-change: ramp 14 already >= 12
        self.assertEqual(b["lands_math"],
                         "Burgess 31+1+6 = 38; Karsten 31.42+3.13·3.4−0.28·8 = 39.8; mean 38.9 → 39")
        self.assertEqual([(e["ref"], e["k"], e["p"]) for e in b["reliability"]],
                         [("core:ramp", 14, 0.762), ("core:card_advantage", 10, 0.71),
                          ("group:targeted_interaction", 10, 0.71), ("core:board_wipe", 3, 0.37)])
        self.assertTrue(all(x["why"] == "Kept from Big Mana." for x in s.values()))
        self.assertEqual(p["based_on"], "big_mana")
        self.assertEqual(p["source"], "baseline:archidekt:1")
        self.assertEqual(p["confidence"], "heuristic")
        self.assertIsNone(p["auto_select"])
        self.assertTrue(all(x["pillar_id"] is None for x in s.values()))

    def test_g2_full_contract(self):
        b = pg.baseline(deck_g2())
        p = b["preset"]
        s = by_ref(p)
        self.assertEqual(b["selected"]["preset_id"], "go_wide")
        self.assertEqual(p["structure"]["lands"], 37)
        self.assertEqual(sorted(s), ["core:board_wipe", "core:card_advantage", "core:finisher",
                                     "core:protection", "core:ramp", "facet:answers_artifact_enchantment",
                                     "group:targeted_interaction", "theme:kindred:goblin"])
        self.assertEqual(s["core:board_wipe"], {"ref": "core:board_wipe", "target": None, "min": None, "max": 1,
                                                "why": "Kept from Go Wide.", "pillar_id": None})
        self.assertEqual(s["theme:kindred:goblin"], {"ref": "theme:kindred:goblin", "target": None, "min": 8,
                                                     "max": None, "pillar_id": None,
                                                     "why": "Main theme Kindred: Goblin: 8 pieces give 62.5% to see one by turn 4."})
        self.assertEqual(s["facet:answers_artifact_enchantment"],
                         {"ref": "facet:answers_artifact_enchantment", "target": None, "min": 3, "max": None,
                          "pillar_id": None,
                          "why": "Only 1 answers to artifacts or enchantments; 3 is the minimum."})
        self.assertEqual(b["plan_room"], 26)                   # theme/facet slots stay out of F
        self.assertEqual(b["reasons"], {"theme:kindred:goblin": "ENGINE",
                                        "facet:answers_artifact_enchantment": "COVERAGE"})
        self.assertEqual({e["ref"]: e["p"] for e in b["reliability"]},
                         {"core:ramp": 0.633, "core:card_advantage": 0.71, "group:targeted_interaction": 0.71,
                          "core:protection": 0.324, "core:finisher": 0.393, "theme:kindred:goblin": 0.625,
                          "facet:answers_artifact_enchantment": 0.348})
        self.assertNotIn("core:board_wipe", {e["ref"] for e in b["reliability"]})  # max-only: no entry

    def test_g3_full_contract(self):
        b = pg.baseline(deck_g3())
        p = b["preset"]
        s = by_ref(p)
        self.assertEqual(b["selected"]["preset_id"], "tcz_2025")
        self.assertEqual(p["structure"]["lands"], 39)
        self.assertEqual(s["core:card_advantage"]["target"], 10)   # 12 - 2 (M2), floor 4 does not bind
        self.assertEqual(s["core:card_advantage"]["why"],
                         "The commander already does this job, so 2 fewer are needed.")
        self.assertEqual(b["reasons"], {"core:card_advantage": "CMD_JOB"})
        self.assertEqual(b["plan_room"], 22)
        self.assertEqual({e["ref"]: e["p"] for e in b["reliability"]},
                         {"core:ramp": 0.633, "core:card_advantage": 0.71,
                          "group:targeted_interaction": 0.778, "core:board_wipe": 0.61})

    def test_g4_trim_walk(self):
        b = pg.baseline(deck_g4())
        p = b["preset"]
        s = by_ref(p)
        self.assertEqual(b["selected"]["preset_id"], "tcz_2025")
        self.assertEqual(p["structure"]["lands"], 41)
        # before fit: 12 + 12 + 12 + 6 = 42, R = 16; walk: draw 11, wipe 5, draw 10, wipe 4
        self.assertEqual({r: x["target"] for r, x in s.items()},
                         {"core:ramp": 12, "core:card_advantage": 10, "group:targeted_interaction": 12,
                          "core:board_wipe": 4})
        self.assertEqual(b["plan_room"], 20)
        self.assertEqual(b["reasons"], {"core:ramp": "RAMP_MV", "core:card_advantage": "TRIM",
                                        "core:board_wipe": "TRIM"})
        self.assertEqual(s["core:ramp"]["why"],
                         "Commander costs 5; ramp floor is 12, giving 70.4% to see one by turn 2.")
        self.assertEqual(s["core:card_advantage"]["why"], "Trimmed to keep 20 cards for the deck's own plan.")
        self.assertEqual(s["core:board_wipe"]["why"], "Trimmed to keep 20 cards for the deck's own plan.")
        self.assertEqual(s["group:targeted_interaction"]["why"], "Kept from Command Zone 2025.")
        self.assertEqual({e["ref"]: e["p"] for e in b["reliability"]},
                         {"core:ramp": 0.704, "core:card_advantage": 0.71,
                          "group:targeted_interaction": 0.778, "core:board_wipe": 0.462})
        self.assertEqual(b["notes"], [])

    def test_g4_trim_order_ties(self):
        # The two tie steps must have picked wipes over removal/counterspell-style alternatives:
        # excess(draw)==excess(wipe) at step 2 and 4, TRIM_ORDER puts board_wipe first (§4.4).
        b = pg.baseline(deck_g4())
        s = by_ref(b["preset"])
        self.assertEqual(s["core:board_wipe"]["target"], 4)       # wiped twice, not three times
        self.assertEqual(s["core:card_advantage"]["target"], 10)  # trimmed twice

    def test_m1_adds_ramp_slot_when_base_has_none(self):
        d = pk(mv=6, ci=("G",), journal={"primary_archetype": "aggroish"},
               facets={"facet:answers_artifact_enchantment": 9})
        d["user_presets"] = [{"id": "u-noramp", "name": "NoRamp",
                              "structure": {"deck_size": 100, "commanders": 1, "lands": 38},
                              "slots": [slot("core:card_advantage", target=10)],
                              "auto_select": {"archetypes": ["aggroish"], "themes": [],
                                              "commander_mv_min": None, "priority": 10}}]
        d["journal"] = {"primary_archetype": "aggroish"}
        b = pg.baseline(d)
        s = by_ref(b["preset"])
        self.assertEqual(s["core:ramp"], {"ref": "core:ramp", "target": 12, "min": None, "max": None,
                                          "pillar_id": None,
                                          "why": "Commander costs 6; ramp floor is 12, giving 70.4% to see one by turn 2."})
        self.assertEqual(b["reasons"]["core:ramp"], "RAMP_MV")

    def test_m2_clamped_at_modified_floor(self):
        # MV 3 + commander does ramp: floor 10-3 = 7; base ramp 9 -> max(9-2, 7) = 7 (not 6).
        d = pk(mv=3, ci=("G",), cmd_tags=["ramp"], facets={"facet:answers_artifact_enchantment": 9})
        d["user_presets"] = [{"id": "u-nine", "name": "Nine",
                              "structure": {"deck_size": 100, "commanders": 1, "lands": 36},
                              "slots": [slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                                        slot("group:targeted_interaction", target=10)],
                              "auto_select": {"archetypes": ["aggroish"], "themes": [],
                                              "commander_mv_min": None, "priority": 10}}]
        d["journal"] = {"primary_archetype": "aggroish"}
        b = pg.baseline(d)
        self.assertEqual(by_ref(b["preset"])["core:ramp"]["target"], 7)
        self.assertEqual(b["reasons"]["core:ramp"], "CMD_JOB")

    def test_m2_group_via_removal_tag(self):
        d = pk(mv=3, ci=("G", "U"), cmd_tags=["removal"], journal={"primary_archetype": "control"},
               facets={"facet:answers_artifact_enchantment": 9})
        b = pg.baseline(d)
        self.assertEqual(by_ref(b["preset"])["group:targeted_interaction"]["target"], 13)  # 15 - 2, floor 8 binds? no: 13 > 8
        self.assertEqual(b["reasons"]["group:targeted_interaction"], "CMD_JOB")

    def test_m4_removes_facet_answers_stack(self):
        d = pk(mv=3, ci=("G", "W"), journal={"primary_archetype": "control"},
               facets={"facet:answers_artifact_enchantment": 9})
        refs = set(by_ref(pg.baseline(d)["preset"]))
        self.assertNotIn("facet:answers_stack", refs)
        self.assertNotIn("core:counterspell", refs)

    def test_coverage_fires_when_facet_counts_missing(self):
        # Decision from t_a0a866cc: a missing facet:* count is 0, so M6 adds the coverage slot.
        d = deck_g2()
        d.pop("facet_counts")
        b = pg.baseline(d)
        self.assertEqual(by_ref(b["preset"])["facet:answers_artifact_enchantment"]["why"],
                         "Only 0 answers to artifacts or enchantments; 3 is the minimum.")

    def test_coverage_skipped_at_three(self):
        d = deck_g2()
        d["facet_counts"] = {"facet:answers_artifact_enchantment": 3}
        self.assertNotIn("facet:answers_artifact_enchantment", by_ref(pg.baseline(d)["preset"]))

    def test_engine_skipped_when_theme_slot_exists(self):
        d = pk(journal={"primary_archetype": "voltron"}, tags=["voltron"],
               facets={"facet:answers_artifact_enchantment": 9})
        b = pg.baseline(d)
        self.assertEqual(b["selected"]["preset_id"], "voltron")
        self.assertEqual(b["reasons"].get("theme:voltron"), None)     # already in base: no ENGINE
        self.assertEqual(by_ref(b["preset"])["theme:voltron"]["min"], 10)

    def test_generic_deck_tags_are_not_themes(self):
        for tag in ("commander", "casual", "competitive"):
            d = pk(tags=[tag], facets={"facet:answers_artifact_enchantment": 9})
            self.assertNotIn(f"theme:{tag}", by_ref(pg.baseline(d)["preset"]))

    def test_floor_reason_variants(self):
        # B3 raise with MV-driven reason vs autotag-floor reason.
        d = pk(mv=5, ci=("G", "U"), journal={"primary_archetype": "control"}, tags=["tokens"],
               facets={"facet:answers_artifact_enchantment": 9})
        b = pg.baseline(d)
        wipe = by_ref(b["preset"])["core:board_wipe"]
        self.assertEqual((wipe["target"], wipe["min"], wipe["max"]), (None, None, 1))
        self.assertEqual(b["reasons"]["core:board_wipe"], "TOKENS_WIPE")

    def test_shared_floor_trim_between_removal_and_counterspell(self):
        # No group slot: removal+counterspell share the targeted floor 10; both show excess 16-10=6,
        # TRIM_ORDER takes counterspell first (later in the list but earlier than ramp... actually
        # board_wipe/card_advantage/targeted absent, so counterspell then removal alternately).
        d = pk(mv=3, ci=("G", "U"), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 9})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        d["user_presets"] = [{"id": "u-heavy", "name": "Heavy",
                              "structure": {"deck_size": 100, "commanders": 1, "lands": 36},
                              "slots": [slot("core:ramp", target=12), slot("core:removal", target=16),
                                        slot("core:counterspell", target=16)],
                              "auto_select": {"archetypes": ["aggroish"], "themes": [],
                                              "commander_mv_min": None, "priority": 10}}]
        d["journal"] = {"primary_archetype": "aggroish"}
        b = pg.baseline(d)
        s = by_ref(b["preset"])
        self.assertEqual(b["selected"]["preset_id"], "u-heavy")
        self.assertEqual(b["plan_room"], 20)
        # F starts 44 (S=63 with lands 37); four trims, all from the shared pool (counterspell first).
        self.assertEqual(s["core:removal"]["target"], 14)
        self.assertEqual(s["core:counterspell"]["target"], 16)
        self.assertEqual(b["reasons"], {"core:removal": "TRIM"})
        self.assertEqual(s["core:removal"]["why"], "Trimmed to keep 20 cards for the deck's own plan.")

    def test_name_truncated_to_40(self):
        d = deck_g1()
        d["deck_name"] = "X" * 80
        name = pg.baseline(d)["preset"]["name"]
        self.assertEqual(len(name), 40)
        self.assertTrue(name.startswith("Baseline for "))

    def test_partner_deck_baseline(self):
        b = pg.baseline(pk(commanders=2))
        p = b["preset"]
        self.assertEqual(p["structure"]["commanders"], 2)
        for e in b["reliability"]:
            self.assertEqual(e["library"], 98)
        # reliability uses L = 100 - commanders: check against the function directly
        e = b["reliability"][0]
        self.assertEqual(e["p"], round(pg.reliability(e["k"], e["cards_seen"], 98), 3))

    def test_baseline_never_fails_a7_by_construction(self):
        # Sweep CI sizes/MVs: the A7 clamp inside land_math means _check_baseline never raises.
        for ci_n in range(1, 6):
            for mv in (1, 3, 5, 7):
                ci = ("W", "U", "B", "R", "G")[:ci_n]
                d = pk(mv=mv, ci=ci, avg=float(mv), cheap=mv, facets={"facet:answers_artifact_enchantment": 9})
                b = pg.baseline(d)
                ctx = ca.Context(d)
                self.assertEqual(ca._lands_rule(b["preset"]["structure"]["lands"], ctx), [], f"ci={ci_n} mv={mv}")

    def test_baseline_deterministic_byte_identical(self):
        for d in (deck_g1(), deck_g2(), deck_g3(), deck_g4()):
            a = json.dumps(pg.baseline(d), sort_keys=True)
            b = json.dumps(pg.baseline(copy.deepcopy(d)), sort_keys=True)
            self.assertEqual(a, b)

    def test_baseline_invariants_section_9_6(self):
        for d in (deck_g1(), deck_g2(), deck_g3(), deck_g4()):
            b = pg.baseline(d)
            p = b["preset"]
            ctx = ca.Context(d)
            ctx.existing_names = set()
            issues = ca.validate_preset(p, ctx, ai=False, scope="deck")
            self.assertEqual([i for i in issues if i["level"] == "error" or i["code"] == "W3"], [])
            refs = set(by_ref(p))
            self.assertNotIn("core:tutor", refs)
            self.assertFalse(any(r.split(":", 1)[-1] in ca.FLAG_NAMES for r in refs))
            st = p["structure"]
            self.assertEqual(st["deck_size"], st["lands"] + (st["deck_size"] - st["commanders"] - st["lands"]) + st["commanders"])
            self.assertNotIn("Goblin Guide", json.dumps(b))       # invariant 3 (no card names)
            self.assertTrue(all(s["pillar_id"] is None for s in p["slots"]))


# ---------------------------------------------------------------------------------------------
# §5 / §9.4 — Improve candidates (X1-X6) and deck gaps
# ---------------------------------------------------------------------------------------------

class ImproveCandidateFixtureTests(unittest.TestCase):

    def test_x1_exact_candidate_records(self):
        d = deck_g2()
        cur = cp.get_builtin("tcz_2025")
        out = pg.improve_candidates(d, cur)
        self.assertEqual(out["candidates"], [
            {"id": "c1", "trig": "R3", "priority": "P1", "op": "set", "ref": "core:board_wipe",
             "from": {"target": 6}, "to": {"max": 1},
             "why": "Tokens deck: wipes hit your own board, so cap them at 1."},
            {"id": "c2", "trig": "R8", "priority": "P3", "op": "add", "ref": "facet:answers_artifact_enchantment",
             "from": None, "to": {"min": 3},
             "why": "Only 1 answers to artifacts or enchantments; 3 is the minimum."},
            {"id": "c3", "trig": "R9", "priority": "P4", "op": "add", "ref": "theme:kindred:goblin",
             "from": None, "to": {"min": 8},
             "why": "Main theme Kindred: Goblin: 8 pieces give 62.5% to see one by turn 4."},
        ])
        self.assertEqual(out["notes"], [])
        # The deck's 1 A/E answer is short of the c2-added min 3 band: information only (§5.3).
        self.assertEqual(out["deck_gaps"], [{
            "ref": "facet:answers_artifact_enchantment", "status": "short", "count": 1, "lower": 3,
            "need": 2, "turn": 6, "p_now": 0.131, "p_at_lower": 0.348}])
        self.assertEqual(out["lands_math"],
                         "Burgess 31+1+4 = 36; Karsten 31.42+3.13·2.9−0.28·9 = 38.0; mean 37.0 → 37")

    def test_x2_lands_and_ramp_floor(self):
        d = deck_g1()
        cur = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        out = pg.improve_candidates(d, cur)
        got = [(c["id"], c["trig"], c["priority"], c["op"], c["ref"], c["from"], c["to"])
               for c in out["candidates"]]
        self.assertEqual(got, [
            ("c1", "R1", "P0", "lands", "structure:lands", 34, 39),
            ("c2", "R2", "P0", "set", "core:ramp", {"target": 9}, {"target": 12}),
        ])
        self.assertEqual(out["candidates"][0]["why"],
                         "Burgess 31+1+6 = 38; Karsten 31.42+3.13·3.4−0.28·8 = 39.8; mean 38.9 → 39")
        self.assertEqual(out["candidates"][1]["why"], "Raised to the floor of 12 (commander MV 6).")

    def test_r2_raise_shapes(self):
        d = deck_g1()
        # min+target slot: both bounds raised to the floor
        cur = preset(39, slot("core:ramp", target=8, min=5), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        out = pg.improve_candidates(d, cur)
        r2 = next(c for c in out["candidates"] if c["trig"] == "R2")
        self.assertEqual((r2["from"], r2["to"]), ({"target": 8, "min": 5}, {"target": 12, "min": 12}))
        # max-only slot under the floor: max itself is raised
        cur2 = preset(39, slot("core:ramp", max=8), slot("core:card_advantage", target=10),
                      slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        out2 = pg.improve_candidates(d, cur2)
        r2b = next(c for c in out2["candidates"] if c["trig"] == "R2")
        self.assertEqual((r2b["from"], r2b["to"]), ({"max": 8}, {"max": 12}))

    def test_r4_why_and_r5_pairing(self):
        d = pk(mv=3, ci=("G",), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 3})
        cur = preset(36, slot("core:ramp", target=10), slot("core:counterspell", target=4),
                     slot("facet:answers_stack", min=3), slot("theme:voltron", min=10))
        out = pg.improve_candidates(d, cur)
        bytr = {c["ref"]: c for c in out["candidates"]}
        self.assertEqual(bytr["theme:voltron"]["why"], "Theme Voltron is not active in this deck.")
        self.assertEqual(bytr["core:counterspell"]["why"], "No blue in the colour identity.")
        self.assertEqual(bytr["facet:answers_stack"]["why"], "No blue in the colour identity.")
        # ids follow (priority, trigger order): R4 first, then both R5 removals. The tokens deck also
        # earns an R9 engine add (theme:tokens), which is not a removal.
        self.assertEqual([(c["id"], c["trig"], c["ref"]) for c in out["candidates"]],
                         [("c1", "R4", "theme:voltron"), ("c2", "R5", "core:counterspell"),
                          ("c3", "R5", "facet:answers_stack"), ("c4", "R9", "theme:tokens")])
        self.assertTrue(all(c["op"] == "remove" and c["to"] is None for c in out["candidates"][:3]))

    def test_x4_r6_minimal_trim_to_15(self):
        d = pk(mv=3, ci=("G",), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12), slot("core:card_advantage", target=15),
                     slot("group:targeted_interaction", target=14), slot("core:board_wipe", target=6))
        out = pg.improve_candidates(d, cur)
        r6 = [c for c in out["candidates"] if c["trig"] == "R6"]
        self.assertEqual([(c["ref"], c["from"], c["to"]) for c in r6],
                         [("core:card_advantage", {"target": 15}, {"target": 14})])
        self.assertEqual(r6[0]["why"], "Trimmed to keep 15 cards for the deck's own plan.")
        self.assertEqual(r6[0]["priority"], "P2")

    def test_r6_shared_floor_branch(self):
        # removal+counterspell without the group slot share floor 10: only one op appears,
        # because the shared excess drains evenly (counterspell first per TRIM_ORDER).
        d = pk(mv=3, ci=("G", "U"), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 9})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12), slot("core:removal", target=18),
                     slot("core:counterspell", target=17))
        self.assertEqual(ca.plan_room(cur), 14)
        out = pg.improve_candidates(d, cur)
        r6 = [c for c in out["candidates"] if c["trig"] == "R6"]
        self.assertEqual([(c["ref"], c["from"], c["to"]) for c in r6],
                         [("core:removal", {"target": 18}, {"target": 17})])
        self.assertEqual(r6[0]["why"], "Trimmed to keep 15 cards for the deck's own plan.")

    def test_x5_r7_plus_gap_survives(self):
        d = deck_g3()
        cur = cp.get_builtin("tcz_2025")
        d["evaluation"]["counts"] = {"core:card_advantage": 8}
        out = pg.improve_candidates(d, cur)
        self.assertEqual([(c["trig"], c["ref"], c["to"]) for c in out["candidates"]],
                         [("R7", "core:card_advantage", {"target": 10})])
        gap = out["deck_gaps"][0]
        self.assertEqual((gap["ref"], gap["status"], gap["count"], gap["lower"], gap["need"]),
                         ("core:card_advantage", "short", 8, 10, 1))
        self.assertEqual((gap["turn"], gap["p_now"], gap["p_at_lower"]), (4, 0.625, 0.71))

    def test_x6_pillar_lock_moves_wipes_instead(self):
        d = pk(mv=3, ci=("G",), pillars=["pl-2"], facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12), slot("core:card_advantage", target=15, pillar_id="pl-2"),
                     slot("group:targeted_interaction", target=14), slot("core:board_wipe", target=6))
        out = pg.improve_candidates(d, cur)
        r6 = [c for c in out["candidates"] if c["trig"] == "R6"]
        self.assertEqual([(c["ref"], c["from"], c["to"]) for c in r6],
                         [("core:board_wipe", {"target": 6}, {"target": 5})])

    def test_fit_blocked_note_when_pillars_lock_everything(self):
        d = pk(mv=3, ci=("G", "U"), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 9})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12, pillar_id="pl-1"),
                     slot("core:removal", target=18, pillar_id="pl-1"),
                     slot("core:counterspell", target=17, pillar_id="pl-1"))
        out = pg.improve_candidates(d, cur)
        self.assertEqual(out["notes"], [{"code": "FIT_BLOCKED", "data": {"R": 14}}])
        self.assertEqual(out["candidates"], [])

    def test_r10_only_when_inside_window_and_far(self):
        d = deck_g1()                                    # window [36, 41.822], baseline lands 39
        near = preset(38, slot("core:ramp", target=14), slot("core:card_advantage", target=10),
                      slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        self.assertEqual([c["trig"] for c in pg.improve_candidates(d, near)["candidates"]], [])
        far = copy.deepcopy(near)
        far["structure"]["lands"] = 36
        trigs = [c["trig"] for c in pg.improve_candidates(d, far)["candidates"]]
        self.assertEqual(trigs, ["R10"])                 # |36-39| = 3 >= 2, still inside the window

    def test_r1_beats_r10_and_r6_sees_r3_first(self):
        # Working-copy chaining (§5.2.1): tokens deck whose wipes get capped by R3 before the R6 trim
        # evaluates excess; lands outside the window fire R1 (P0) and never duplicate as R10.
        d = deck_g2()                                    # baseline lands 37, window [34, 39.977]
        cur = preset(30, slot("core:ramp", target=10), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=6))
        out = pg.improve_candidates(d, cur)
        trigs_refs = [(c["trig"], c["ref"]) for c in out["candidates"]]
        self.assertIn(("R1", "structure:lands"), trigs_refs)
        self.assertNotIn(("R10", "structure:lands"), trigs_refs)

    def test_one_op_per_ref_merge(self):
        d = pk(mv=3, ci=("G",), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12), slot("core:card_advantage", target=15),
                     slot("group:targeted_interaction", target=15), slot("core:board_wipe", target=6))
        out = pg.improve_candidates(d, cur)
        refs = [c["ref"] for c in out["candidates"]]
        self.assertEqual(len(refs), len(set(refs)))

    def test_deck_gap_low_status_and_need(self):
        # Spec §5.3 example: ramp target 12 (floor), deck has 9 -> need 3, 59.2% -> 70.4% by T2.
        d = deck_g1()
        cur = preset(39, slot("core:ramp", target=12), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        d["evaluation"]["counts"] = {"core:ramp": 9}
        out = pg.improve_candidates(d, cur)
        self.assertEqual(out["deck_gaps"], [{
            "ref": "core:ramp", "status": "low", "count": 9, "lower": 12, "need": 3,
            "turn": 2, "p_now": 0.592, "p_at_lower": 0.704}])

    def test_gap_short_vs_low_band(self):
        d = deck_g1()
        cur = preset(39, slot("core:ramp", target=10), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        d["evaluation"]["counts"] = {"core:ramp": 9}     # floor 12 > band_lo 9 -> low, need 3
        g = pg.improve_candidates(d, cur)["deck_gaps"][0]
        self.assertEqual((g["status"], g["need"]), ("low", 3))
        # 'short': count above the floor but below the band (target 12 - BAND 1 = 11): MV 4, no cmd job.
        d2 = pk(mv=4, ci=("W", "U", "B", "G"), avg=3.3, cheap=10, facets={"facet:answers_artifact_enchantment": 9})
        cur2 = cp.get_builtin("tcz_2025")
        d2["evaluation"]["counts"] = {"core:card_advantage": 10}    # 10 < band_lo 11, >= floor 7 -> short
        out2 = pg.improve_candidates(d2, cur2)
        self.assertEqual(out2["candidates"], [])                    # a deck gap is never an op (A12)
        g2 = out2["deck_gaps"][0]
        self.assertEqual((g2["ref"], g2["status"], g2["count"], g2["need"]), ("core:card_advantage", "short", 10, 1))

    def test_gaps_ignore_slots_without_deck_counts(self):
        d = deck_g1()
        cur = preset(39, slot("core:ramp", target=14), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        d["evaluation"]["counts"] = {}                    # nothing measured -> no gaps
        self.assertEqual(pg.improve_candidates(d, cur)["deck_gaps"], [])

    def test_improve_requires_current_preset(self):
        with self.assertRaises(pg.PresetGenError):
            pg.improve_candidates(deck_g1(), None)
        with self.assertRaises(pg.PresetGenError):
            pg.improve(deck_g1(), None, preset={})

    def test_candidates_sorted_by_priority_then_trigger_then_ref(self):
        d = deck_g2()
        cur = preset(30, slot("core:ramp", target=10), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=6),
                     slot("theme:voltron", min=10), slot("core:counterspell", target=4))
        out = pg.improve_candidates(d, cur)
        keys = [(["P0", "P1", "P2", "P3", "P4"].index(c["priority"]),
                 pg.TRIGGER_ORDER.index(c["trig"]), c["ref"]) for c in out["candidates"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([c["id"] for c in out["candidates"]],
                         [f"c{i}" for i in range(1, len(out["candidates"]) + 1)])

    def test_candidate_invariants_never_tutor_or_below_floor(self):
        for d, cur in ((deck_g1(), cp.get_builtin("standard_36")), (deck_g2(), cp.get_builtin("tcz_2025")),
                       (deck_g3(), cp.get_builtin("control")), (deck_g4(), cp.get_builtin("go_wide"))):
            dd = copy.deepcopy(d)
            dd["current_preset"] = copy.deepcopy(cur)
            out = pg.improve_candidates(dd)
            facts = pg.Facts(dd)
            for c in out["candidates"]:
                self.assertNotEqual(c["ref"], "core:tutor")
                self.assertFalse(str(c["ref"]).split(":", 1)[-1] in ca.FLAG_NAMES)
                if c["op"] == "set" and c["ref"].startswith(("core:", "group:")):
                    floor = facts.floors.get(c["ref"], 0)
                    lo = ca.lower(c["to"]) if c["to"] else None
                    if lo is not None:
                        self.assertGreaterEqual(lo, floor, c["ref"])
            self.assertEqual(json.dumps(out), json.dumps(pg.improve_candidates(copy.deepcopy(dd))))


# ---------------------------------------------------------------------------------------------
# §6-§7 — AI contract: mocked chains, generate/improve statuses, A9-A12 (V16-V23)
# ---------------------------------------------------------------------------------------------

class AiContractTests(unittest.TestCase):

    def improve_deck(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        d["current_preset"] = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        return d

    def test_v16_a9_rejects_and_falls_back_to_baseline(self):
        d = deck_g1()
        base = pg.baseline(d)
        res = pg.generate(d, chain(draft_from(base, lambda p: by_ref(p)["core:ramp"].update(target=20))))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertEqual(res["ai"]["reason"], "invalid_draft")
        self.assertIn("A9", res["ai"]["codes"])
        self.assertEqual(res["preset"], base["preset"])          # deterministic result preserved
        self.assertEqual(res["baseline"], base)

    def test_v17_a9_exempt_with_real_pillar(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        base = pg.baseline(d)
        res = pg.generate(d, chain(draft_from(base, lambda p: by_ref(p)["core:ramp"].update(
            target=20, pillar_id="pl-1"))))
        self.assertEqual(res["ai"]["status"], "ok", res["ai"])
        self.assertEqual(by_ref(res["preset"])["core:ramp"]["target"], 20)
        self.assertEqual(ca.plan_room(res["preset"]), 17)

    def test_v17_pillar_id_must_be_real_for_a9(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        base = pg.baseline(d)
        res = pg.generate(d, chain(draft_from(base, lambda p: by_ref(p)["core:ramp"].update(
            target=20, pillar_id="pl-9"))))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertIn("A9", res["ai"]["codes"])

    def test_a9_upper_bound_delta_and_new_slot(self):
        d = deck_g2()
        base = pg.baseline(d)
        # tokens baseline caps wipes at max 1; a draft with max 6 is more than slack away upward.
        draft = copy.deepcopy(base["preset"])
        by_ref(draft)["core:board_wipe"].update(target=None, min=None, max=6)
        self.assertEqual([(i["code"], i["ref"]) for i in pg.check_a9(draft, base["preset"], ca.Context(d))],
                         [("A9", "core:board_wipe")])
        # a brand-new core slot not in Baseline needs a pillar
        draft2 = copy.deepcopy(base["preset"])
        draft2["slots"].append(slot("core:recursion", target=5))
        hits = [i for i in pg.check_a9(draft2, base["preset"], ca.Context(d)) if i["code"] == "A9"]
        self.assertEqual([i["ref"] for i in hits], ["core:recursion"])
        self.assertIn("needs a pillar_id", hits[0]["message"])
        # ...and is exempt with a real pillar
        d3 = deck_g2()
        d3["journal"]["pillars"] = [{"id": "pl-1", "name": "Graveyard"}]
        draft3 = copy.deepcopy(base["preset"])
        draft3["slots"].append(slot("core:recursion", target=5, pillar_id="pl-1"))
        self.assertEqual(pg.check_a9(draft3, base["preset"], ca.Context(d3)), [])

    def test_a9_missing_slot_counts_as_zero(self):
        d = deck_g1()
        base = pg.baseline(d)
        draft = copy.deepcopy(base["preset"])
        draft["slots"] = [s for s in draft["slots"] if s["ref"] != "core:card_advantage"]
        codes = {(i["code"], i["ref"]) for i in pg.check_a9(draft, base["preset"], ca.Context(d))}
        self.assertIn(("A9", "core:card_advantage"), codes)

    def test_v18_a11_tutor_op_dropped_even_with_pillar(self):
        d = self.improve_deck()
        op = {"op": "add", "ref": "core:tutor", "to": {"min": 3}, "why": "Find the engine.", "pillar_id": "pl-1"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(res["ops"], [])
        self.assertIn("A11", res["dropped"][0]["codes"])

    def test_a11_lowering_tutor_allowed(self):
        d = self.improve_deck()
        cur = preset(39, slot("core:tutor", target=5), slot("core:ramp", target=14),
                     slot("core:card_advantage", target=10), slot("group:targeted_interaction", target=10),
                     slot("core:board_wipe", target=3))
        op = {"candidate_id": "c1", "op": "set", "ref": "core:tutor", "to": {"target": 3}, "why": "Fewer tutors."}
        cands = [{"id": "c1", "trig": "R6", "priority": "P2", "op": "set", "ref": "core:tutor",
                  "from": {"target": 5}, "to": {"target": 3}, "why": "x"}]
        res = pg.validate_ops(cur, [op], d, cands)
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_v19_candidate_mismatch_dropped(self):
        d = self.improve_deck()
        cands = pg.improve_candidates(d)["candidates"]
        c = next(c for c in cands if c["trig"] == "R2")
        op = {"candidate_id": c["id"], "op": "set", "ref": c["ref"], "to": {"target": 11}, "why": "Floor."}
        res = pg.validate_ops(d["current_preset"], [op], d, cands)
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])

    def test_v19_op_kind_mismatch_also_a10(self):
        d = self.improve_deck()
        cands = pg.improve_candidates(d)["candidates"]
        c = cands[0]
        op = {"candidate_id": c["id"], "op": "remove", "ref": c["ref"], "why": "Wrong kind."}
        res = pg.validate_ops(d["current_preset"], [op], d, cands)
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])

    def test_v19_partial_bounds_do_not_match_full_candidate_bounds(self):
        # R2 raises a {target: 8, min: 5} ramp slot to {target: 12, min: 12}; an op citing c2 with
        # only {target: 12} drops the min and must NOT count as matching the candidate.
        d = deck_g1()
        cur = preset(34, slot("core:ramp", target=8, min=5), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        cands = pg.improve_candidates(d, cur)["candidates"]
        r2 = next(c for c in cands if c["trig"] == "R2")
        self.assertEqual(r2["to"], {"target": 12, "min": 12})
        partial = {"candidate_id": r2["id"], "op": "set", "ref": "core:ramp", "to": {"target": 12},
                   "why": "Raised floor."}
        res = pg.validate_ops(cur, [partial], d, cands)
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])
        exact = {**partial, "to": {"target": 12, "min": 12}}
        res2 = pg.validate_ops(cur, [exact], d, cands)
        self.assertEqual(len(res2["ops"]), 1, res2["dropped"])
        # ...and an op carrying an EXTRA bound (max: 20) is equally not a match: `to` must equal
        # the candidate's bounds exactly (§6.3), not merely cover them.
        extra = {**partial, "to": {"target": 12, "min": 12, "max": 20}}
        res3 = pg.validate_ops(cur, [extra], d, cands)
        self.assertEqual(res3["ops"], [])
        self.assertIn("A10", res3["dropped"][0]["codes"])

    def test_candidate_match_may_only_reword_why(self):
        d = self.improve_deck()
        cands = pg.improve_candidates(d)["candidates"]
        ops = [{"candidate_id": c["id"], "op": c["op"], "ref": c["ref"], "to": c["to"],
                "why": "Reworded reason."} for c in cands]
        res = pg.validate_ops(d["current_preset"], ops, d, cands)
        self.assertEqual(len(res["ops"]), 2)
        self.assertTrue(all(o["why"] == "Reworded reason." for o in res["ops"]))
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2"])

    def test_x3_a10_a12_a5_drop(self):
        # Spec X3 expects [A10, A12]; the implementation additionally reports A5 (the note says
        # "it would also fail A5"), so assert the superset rather than pinning the extra code.
        d = self.improve_deck()
        d["evaluation"]["counts"] = {"group:targeted_interaction": 8}
        op = {"op": "set", "ref": "group:targeted_interaction", "from": {"target": 10}, "to": {"target": 8},
              "why": "Deck runs 8."}
        res = pg.validate_ops(d["current_preset"], [op], d, pg.improve_candidates(d)["candidates"])
        self.assertEqual(res["ops"], [])
        codes = set(res["dropped"][0]["codes"])
        self.assertTrue({"A10", "A12"} <= codes)
        self.assertIn("A5", codes)

    def test_a12_exempt_for_r7_candidate(self):
        d = deck_g3()
        cur = cp.get_builtin("tcz_2025")
        d["evaluation"]["counts"] = {"core:card_advantage": 8}
        cands = pg.improve_candidates(d, cur)["candidates"]
        op = {"candidate_id": "c1", "op": "set", "ref": "core:card_advantage", "to": {"target": 10},
              "why": "Commander draws."}
        res = pg.validate_ops(cur, [op], d, cands)
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_a12_exempt_for_r6_candidate(self):
        d = pk(mv=3, ci=("G",), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(38, slot("core:ramp", target=12), slot("core:card_advantage", target=15),
                     slot("group:targeted_interaction", target=14), slot("core:board_wipe", target=6))
        cands = pg.improve_candidates(d, cur)["candidates"]
        r6 = next(c for c in cands if c["trig"] == "R6")
        d["evaluation"]["counts"] = {"core:card_advantage": 10}     # deck short of even the new lower
        op = {"candidate_id": r6["id"], "op": "set", "ref": r6["ref"], "to": r6["to"], "why": "Room."}
        res = pg.validate_ops(cur, [op], d, cands)
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_a12_pillar_exemption(self):
        d = self.improve_deck()
        d["evaluation"]["counts"] = {"core:card_advantage": 5}
        op = {"op": "set", "ref": "core:card_advantage", "to": {"target": 8}, "why": "Engine pillar.",
              "pillar_id": "pl-1"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_a12_exempt_for_r3_cap_and_sensitivity(self):
        # R3 caps wipes {target: 2} -> {max: 1}, which lowers the slot's lower bound to 0 while the
        # deck runs only 1. Matched to the candidate it is exempt; off-candidate it must be rejected.
        d = pk(mv=3, ci=("G", "U"), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 9})
        cur = preset(38, slot("core:ramp", target=12), slot("core:card_advantage", target=7),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        cands = pg.improve_candidates(d, cur)["candidates"]
        r3 = next(c for c in cands if c["trig"] == "R3")
        d["evaluation"]["counts"] = {"core:board_wipe": 1}      # count < old_lo 2
        op = {"candidate_id": r3["id"], "op": "set", "ref": "core:board_wipe", "to": {"max": 1},
              "why": "Own board."}
        res = pg.validate_ops(cur, [op], d, cands)
        self.assertEqual(len(res["ops"]), 1, res["dropped"])
        res2 = pg.validate_ops(cur, [{k: v for k, v in op.items() if k != "candidate_id"}], d, cands)
        self.assertEqual(res2["ops"], [])
        self.assertIn("A12", res2["dropped"][0]["codes"])
        # exemption is keyed to the trigger set (spec §7): drop R3 and the same op is rejected
        orig = pg.A12_EXEMPT_TRIGGERS
        try:
            pg.A12_EXEMPT_TRIGGERS = ("R5", "R6", "R7")
            res3 = pg.validate_ops(cur, [op], d, cands)
            self.assertEqual(res3["ops"], [])
            self.assertIn("A12", res3["dropped"][0]["codes"])
        finally:
            pg.A12_EXEMPT_TRIGGERS = orig

    def test_a12_exemption_is_limited_to_the_spec_trigger_set(self):
        # §7 A12: a matched op is exempt only for R3/R5/R6/R7. A candidate from another trigger
        # (here a synthetic R1-tagged op) that lowers a slot while the deck is short must still be
        # rejected. card_advantage 10 -> 8 with the deck at 6 stays above floor 7, so A12 is the
        # only rule that binds — widening the exemption set to all triggers would accept it.
        d = deck_g1()
        cur = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        cands = pg.improve_candidates(d, cur)["candidates"]
        synth = [{"id": "s1", "trig": "R1", "priority": "P0", "op": "set", "ref": "core:card_advantage",
                  "from": {"target": 10}, "to": {"target": 8}, "why": "x"}]
        d["evaluation"]["counts"] = {"core:card_advantage": 6}
        op = {"candidate_id": "s1", "op": "set", "ref": "core:card_advantage", "to": {"target": 8},
              "why": "Lean shell."}
        res = pg.validate_ops(cur, [op], d, cands + synth)
        self.assertEqual(res["ops"], [])
        self.assertIn("A12", res["dropped"][0]["codes"])
        orig = pg.A12_EXEMPT_TRIGGERS
        try:
            pg.A12_EXEMPT_TRIGGERS = tuple(pg.PRIORITY)          # mutant guard: exemption widened
            res2 = pg.validate_ops(cur, [op], d, cands + synth)
            self.assertEqual([o["ref"] for o in res2["ops"]], ["core:card_advantage"])
        finally:
            pg.A12_EXEMPT_TRIGGERS = orig

    def test_v20_off_candidate_add_with_pillar_accepted(self):
        d = deck_g2()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Go wide"}]
        cur = pg.baseline(d)["preset"]
        d["evaluation"]["counts"] = {"core:ramp": 10}
        op = {"op": "add", "ref": "theme:tokens", "to": {"min": 10}, "why": "Tokens are the plan.",
              "pillar_id": "pl-1"}
        res = pg.validate_ops(cur, [op], d, [])
        self.assertEqual(len(res["ops"]), 1, res["dropped"])
        self.assertEqual(res["ops"][0]["ref"], "theme:tokens")

    def test_v21_first_six_kept_in_ai_order(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        cur = preset(39, slot("core:ramp", target=14), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=3))
        refs = ["type:creature", "type:artifact", "type:enchantment", "type:instant", "type:sorcery",
                "type:planeswalker", "type:battle", "facet:flexible"]
        ops = [{"op": "add", "ref": r, "to": {"min": 1}, "why": "Pillar needs it.", "pillar_id": "pl-1"}
               for r in refs]
        res = pg.validate_ops(cur, ops, d, [])
        self.assertEqual([o["ref"] for o in res["ops"]], refs[:6])
        self.assertEqual([x["codes"] for x in res["dropped"]], [["A4"], ["A4"]])
        self.assertEqual([x["op_index"] for x in res["dropped"]], [6, 7])

    def test_v22_generate_lands_outside_window_rejected(self):
        d = deck_g1()
        res = pg.generate(d, chain(draft_from(pg.baseline(d), lands=44)))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertIn("A7", res["ai"]["codes"])

    def test_v23_unknown_pillar_treated_as_none(self):
        d = self.improve_deck()
        op = {"op": "add", "ref": "type:creature", "to": {"min": 10}, "why": "Creatures.", "pillar_id": "pl-9"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])

    def test_set_op_to_is_complete_bounds(self):
        # Decision from t_a0a866cc: {target: 6} -> {max: 1} replaces the whole bounds dict.
        p = preset(38, slot("core:board_wipe", target=6))
        new, why_not = pg.apply_op(p, {"op": "set", "ref": "core:board_wipe", "to": {"max": 1}})
        self.assertIsNone(why_not)
        self.assertEqual((new["slots"][0]["target"], new["slots"][0]["min"], new["slots"][0]["max"]),
                         (None, None, 1))

    def test_non_object_op_dropped(self):
        d = self.improve_deck()
        res = pg.validate_ops(d["current_preset"], ["just a string"], d, [])
        self.assertEqual(res["ops"], [])
        self.assertEqual(res["dropped"][0]["reason"], "not an object")


class GenerateImproveStatusTests(unittest.TestCase):
    """Every ai.status branch, with the LLM client fully mocked."""

    def test_generate_ai_off_returns_baseline(self):
        d = deck_g1()
        res = pg.generate(d, None)
        self.assertEqual(res["ai"], {"status": "off", "reason": "no_llm"})
        self.assertEqual(res["preset"], pg.baseline(d)["preset"])
        self.assertEqual(res["mode"], "generate")
        self.assertFalse(res["saved"] or res["pins_deck"])

    def test_generate_success_fields(self):
        d = deck_g1()
        base = pg.baseline(d)
        slots = copy.deepcopy(base["preset"]["slots"])
        by_ref({"slots": slots})["core:ramp"]["target"] = 16
        res = pg.generate(d, chain(draft_from(base, lambda p: by_ref(p)["core:ramp"].update(target=16))))
        self.assertEqual(res["ai"]["status"], "ok")
        self.assertEqual(res["ai"]["model"], "gemini-3.8-flash")
        self.assertEqual(res["preset"]["source"], "ai:" + res["ai"]["model"])
        # reliability recomputed on the accepted draft, not copied from Baseline
        ramp = next(e for e in res["reliability"] if e["ref"] == "core:ramp")
        self.assertEqual((ramp["k"], ramp["p"]), (16, 0.81))

    def test_generate_provider_failure_falls_back(self):
        d = deck_g1()
        res = pg.generate(d, chain(fail=True))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertEqual(res["ai"]["reason"], "llm_failed")
        self.assertEqual(res["preset"], pg.baseline(d)["preset"])

    def test_generate_bad_response_falls_back(self):
        d = deck_g1()
        res = pg.generate(d, chain({"nothing": 1}))
        self.assertEqual((res["ai"]["status"], res["ai"]["reason"]), ("fallback", "invalid_draft"))

    def test_generate_empty_draft_slots_falls_back(self):
        d = deck_g1()
        res = pg.generate(d, chain({"preset": {"name": "E", "description": "d",
                                               "structure": {"lands": 39}, "slots": []},
                                    "lands_math": "x", "summary": "y"}))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertIn("E4", res["ai"]["codes"])

    def test_improve_ai_off_applies_top_six(self):
        d = deck_g2()
        cur = cp.get_builtin("tcz_2025")
        res = pg.improve(d, None, preset=cur)
        self.assertEqual(res["ai"], {"status": "off", "reason": "no_llm"})
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2", "c3"])
        s = by_ref(res["preset"])
        self.assertEqual((s["core:board_wipe"]["target"], s["core:board_wipe"]["max"]), (None, 1))
        self.assertEqual(s["theme:kindred:goblin"]["min"], 8)
        self.assertEqual(res["preset"]["version"], 2)
        self.assertTrue(res["summary"].startswith("Applied the top 3 of 3"))

    def test_improve_caps_at_six_candidates(self):
        d = pk(mv=3, ci=("G",), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 0})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(30, slot("core:ramp", target=10), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=6),
                     slot("theme:voltron", min=10), slot("core:counterspell", target=4),
                     slot("facet:answers_stack", min=3))
        found = pg.improve_candidates(d, cur)
        self.assertGreater(len(found["candidates"]), 6)
        res = pg.improve(d, None, preset=cur)
        self.assertEqual(len(res["ops"]), 6)
        self.assertEqual([o["candidate_id"] for o in res["ops"]],
                         [c["id"] for c in found["candidates"][:6]])

    def test_improve_no_candidates_skips_llm_entirely(self):
        d = deck_g1()
        cur = pg.baseline(d)["preset"]
        prov = JsonProvider([])                      # any answer would be a bug: queue empty
        res = pg.improve(d, __import__("analytics.interactions.llm", fromlist=["LLMChain"]).LLMChain([prov]),
                         preset=copy.deepcopy(cur))
        self.assertEqual(res["ai"], {"status": "skipped", "reason": "no_candidates"})
        self.assertEqual(res["ops"], [])
        self.assertEqual(res["summary"], "No preset issues found.")
        self.assertEqual(prov.prompts, [])

    def test_improve_success_rewords_and_marks_ok(self):
        d = deck_g2()
        cur = cp.get_builtin("tcz_2025")
        cands = pg.improve_candidates(d, cur)["candidates"]
        ops = [{"candidate_id": c["id"], "op": c["op"], "ref": c["ref"], "to": c["to"], "why": "Reworded."}
               for c in cands]
        res = pg.improve(d, chain({"ops": ops, "summary": "Three fixes."}), preset=cur)
        self.assertEqual(res["ai"]["status"], "ok")
        self.assertEqual(res["ai"]["dropped"], [])
        self.assertEqual(res["summary"], "Three fixes.")
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2", "c3"])
        self.assertEqual(by_ref(res["preset"])["core:board_wipe"]["why"], "Reworded.")

    def test_improve_llm_error_falls_back_to_top_six(self):
        d = deck_g1()
        d["current_preset"] = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        res = pg.improve(d, chain(fail=True))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertEqual(res["ai"]["reason"], "llm_failed")
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2"])

    def test_improve_bad_response_falls_back(self):
        d = deck_g1()
        cur = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        res = pg.improve(d, chain({"ops": "nope", "summary": "x"}), preset=cur)
        self.assertEqual((res["ai"]["status"], res["ai"]["reason"]), ("fallback", "bad_response"))
        self.assertEqual(len(res["ops"]), 2)

    def test_improve_all_ops_rejected_reports_dropped(self):
        d = deck_g1()
        cur = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        op = {"op": "set", "ref": "core:ramp", "to": {"target": 9}, "why": "Nothing."}
        res = pg.improve(d, chain({"ops": [op], "summary": "x"}), preset=cur)
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertEqual(res["ai"]["reason"], "all_ops_rejected")
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2"])
        self.assertEqual(res["ai"]["dropped"][0]["codes"], ["A10"])

    def test_improve_original_preset_untouched(self):
        d = deck_g2()
        cur = cp.get_builtin("tcz_2025")
        before = copy.deepcopy(cur)
        pg.improve(d, None, preset=cur)
        self.assertEqual(cur, before)

    def test_improve_partial_acceptance_lists_drops(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        cur = preset(34, slot("core:ramp", target=9), slot("core:card_advantage", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        cands = pg.improve_candidates(d, cur)["candidates"]
        good = {"candidate_id": cands[1]["id"], "op": "set", "ref": cands[1]["ref"], "to": cands[1]["to"],
                "why": "Floor."}
        bad = {"op": "add", "ref": "core:tutor", "to": {"min": 3}, "why": "Tutors!", "pillar_id": "pl-1"}
        res = pg.improve(d, chain({"ops": [bad, good], "summary": "One fix."}), preset=cur)
        self.assertEqual(res["ai"]["status"], "ok")
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c2"])
        self.assertEqual(res["ai"]["dropped"], [{"op_index": 0, "codes": ["A11"]}])


class PacketAndPromptTests(unittest.TestCase):

    def test_build_packet_enrichment(self):
        from tests.test_composition_ai import make_deck
        deck, texts, tags = make_deck()
        texts["MDFC1"] = {"type_line": "Instant // Land", "cmc": 2, "rarity": "mythic"}
        texts["MDFC2"] = {"type_line": "Creature // Land", "cmc": 3, "rarity": "rare"}
        deck["cards"] += [{"name": "MDFC1", "quantity": 1}, {"name": "MDFC2", "quantity": 2}]
        packet = pg.build_packet(deck, texts, tags, deck_tags=["counters"],
                                 facet_counts={"facet:answers_artifact_enchantment": 1})
        self.assertEqual(packet["mdfc_eq"], round(0.74 + 2 * 0.38, 2))
        self.assertEqual(packet["facet_counts"], {"facet:answers_artifact_enchantment": 1})
        self.assertEqual(packet["commander_tags"], [])
        self.assertIn("user_presets", packet)
        res = pg.generate(packet, None)
        self.assertEqual(res["ai"]["status"], "off")
        self.assertEqual(res["preset"]["source"], "baseline:archidekt:1")

    def test_prompt_packet_shape_and_privacy(self):
        d = deck_g1()
        d["deck_card_names"] = ["Goblin Piledriver"]
        base = pg.baseline(d)
        found = pg.improve_candidates(d, copy.deepcopy(base["preset"]))
        pp = pg.prompt_packet(d, "improve", base, found["candidates"], found["deck_gaps"], base["reliability"])
        blob = json.dumps(pp, default=str)
        self.assertNotIn("Goblin Piledriver", blob)
        for drop in ("deck_card_names", "existing_preset_names", "registry_id", "deck_name",
                     "user_presets", "commander_tags", "mdfc_eq", "facet_counts", "commander_does"):
            self.assertNotIn(drop, pp)
        self.assertEqual(pp["mode"], "improve")
        self.assertEqual(set(pp["baseline"]), {"structure", "slots", "lands_math"})
        self.assertEqual(pp["limits"], {"max_ops": 6, "plan_room_min": 15, "baseline_slack": 4,
                                        "why_max": 160, "lands_window": [36.0, 41.822]})
        self.assertEqual(pp["floors"], d["floors"])
        # generate mode carries empty candidates/gaps (§6.1)
        pg_pp = pg.prompt_packet(d, "generate", base, [], [], base["reliability"])
        self.assertEqual((pg_pp["candidates"], pg_pp["deck_gaps"]), ([], []))

    def test_system_prompt_verbatim(self):
        expected = (
            "You draft Commander deck-composition presets for Commander Lab. A preset is a shape, not a list:\n"
            "it sets counts for categories such as ramp, card draw, interaction, wipes and themes. It never names cards.\n"
            "\n"
            "The deck's spirit outranks raw power. The Journal thesis and pillars say what the deck is.\n"
            "Keep room for them: plan room (spells minus the function-slot minimums) must stay at least 15.\n"
            "\n"
            "You get a deterministic BASELINE and, when improving, a ranked list of CANDIDATES.\n"
            "- Start from the baseline. Move a baseline slot by more than 4 only when a Journal pillar or the\n"
            "  commander's own text demands it, and put that pillar's id in pillar_id.\n"
            "- When improving, prefer candidates and cite candidate_id. An op that isn't a candidate needs a pillar_id.\n"
            "- Never lower a target just because the deck has fewer cards than the target today. That is a deck gap.\n"
            "- Never set a slot below its floor unless a pillar or the commander does that job.\n"
            "- Never add or raise core:tutor. Never target flags (stax, land destruction, extra turns, Game Changers).\n"
            "- Keep lands inside limits.lands_window.\n"
            "- Each why is one sentence of at most 160 characters. Give the deck reason or the number.\n"
            '  Never name a card, and never use popularity, price, "staple", "best", "meta" or EDHREC as a reason.\n'
            "- Use only refs from the catalog. Return JSON only, in the schema given. No prose outside the JSON.")
        self.assertEqual(pg.SYSTEM_PROMPT, expected)

    def test_single_turn_json_only_call(self):
        prov = JsonProvider([draft_from(pg.baseline(deck_g1()))])
        pg.generate(deck_g1(), __import__("analytics.interactions.llm", fromlist=["LLMChain"]).LLMChain([prov]))
        self.assertEqual(len(prov.prompts), 1)
        system, prompt, schema = prov.prompts[0]
        self.assertEqual(system, pg.SYSTEM_PROMPT)
        self.assertTrue(prompt.startswith("PACKET:\n{"))
        self.assertIn("\n\nSCHEMA:\n", prompt)
        self.assertEqual(json.loads(prompt.split("\n\nSCHEMA:\n")[1]), pg.GENERATE_SCHEMA)
        self.assertEqual(schema, pg.GENERATE_SCHEMA)

    def test_schema_required_fields(self):
        self.assertEqual(pg.GENERATE_SCHEMA["required"], ["preset", "lands_math", "summary"])
        self.assertEqual(pg.GENERATE_SCHEMA["properties"]["preset"]["required"], ["name", "structure", "slots"])
        self.assertEqual(pg.IMPROVE_SCHEMA["required"], ["ops", "summary"])
        self.assertEqual(pg.IMPROVE_SCHEMA["properties"]["ops"]["items"]["required"], ["op", "why"])

    def test_on_call_receives_usage(self):
        calls = []
        prov = JsonProvider([draft_from(pg.baseline(deck_g1()))])
        pg.generate(deck_g1(), __import__("analytics.interactions.llm", fromlist=["LLMChain"]).LLMChain([prov]),
                    on_call=calls.append)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["ok"])
        self.assertEqual(calls[0]["purpose"], "presetgen_generate")

    def test_config_matches_spec_section_8(self):
        c = pg.CONFIG
        self.assertEqual(c["version"], "presetgen-v1")
        self.assertEqual((c["plan_room_target"], c["plan_room_min"], c["baseline_slack"]), (20, 15, 4))
        self.assertEqual((c["engine_min"], c["coverage_min"], c["cmd_job_target_delta"]), (8, 3, -2))
        self.assertEqual((c["big_commander_mv"], c["big_commander_ramp"]), (5, 12))
        self.assertEqual(c["mdfc_land_weight"], {"nonmythic": 0.38, "mythic": 0.74})
        self.assertEqual(c["trim_order"], ["core:board_wipe", "core:card_advantage", "group:targeted_interaction",
                                           "core:removal", "core:counterspell", "core:ramp"])
        self.assertEqual(c["reliability"]["core:ramp"], {"turn": 2, "p": 0.60})
        self.assertEqual(c["reliability"]["core:ramp@big_commander"], {"turn": 2, "p": 0.70})
        self.assertEqual(c["reliability"]["core:card_advantage"], {"turn": 4, "p": 0.55})
        self.assertEqual(c["reliability"]["group:targeted_interaction"], {"turn": 4, "p": 0.70})
        self.assertEqual(c["reliability"]["core:board_wipe"], {"turn": 7, "p": 0.25})
        self.assertEqual(c["reliability"]["core:finisher"], {"turn": 8, "p": 0.25})


class WhyTemplateTests(unittest.TestCase):
    """§4.3: templates verbatim and every generated why within A6 limits."""

    def test_templates_match_spec(self):
        t = pg.WHY_TEMPLATES
        self.assertEqual(t["BASE"], "Kept from {base_name}.")
        self.assertEqual(t["RAMP_MV"], "Commander costs {mv}; ramp floor is 12, giving {p}% to see one by turn 2.")
        self.assertEqual(t["CMD_JOB"], "The commander already does this job, so 2 fewer are needed.")
        self.assertEqual(t["TOKENS_WIPE"], "Tokens deck: wipes hit your own board, so cap them at 1.")
        self.assertEqual(t["NO_BLUE"], "No blue in the colour identity.")
        self.assertEqual(t["ENGINE"], "Main theme {theme_label}: 8 pieces give {p}% to see one by turn 4.")
        self.assertEqual(t["COVERAGE"], "Only {count} answers to artifacts or enchantments; 3 is the minimum.")
        self.assertEqual(t["FLOOR"], "Raised to the floor of {floor} ({floor_reason}).")
        self.assertEqual(t["TRIM"], "Trimmed to keep {R} cards for the deck's own plan.")
        self.assertEqual(t["THEME_OFF"], "Theme {theme_label} is not active in this deck.")
        self.assertEqual(t["LANDS"], "{lands_math}")

    def test_generated_whys_pass_a6_limits(self):
        for d in (deck_g1(), deck_g2(), deck_g3(), deck_g4()):
            for s in pg.baseline(d)["preset"]["slots"]:
                self.assertLessEqual(len(s["why"]), ca.WHY_MAX, s["ref"])
                self.assertTrue(s["why"].strip())

    def test_theme_label_capitalisation(self):
        # M5 fires only for the top-ranked theme tag; kindred:* gets the "Kindred: X" label.
        d = pk(tags=["kindred:goblin"], facets={"facet:answers_artifact_enchantment": 9})
        b = pg.baseline(d)
        self.assertEqual(b["reasons"]["theme:kindred:goblin"], "ENGINE")
        self.assertEqual(by_ref(b["preset"])["theme:kindred:goblin"]["why"],
                         "Main theme Kindred: Goblin: 8 pieces give 62.5% to see one by turn 4.")
        # a plain theme tag uses the title-cased label; pick a non-archetype tag so M5 actually fires
        d = pk(tags=["phthisic"], facets={"facet:answers_artifact_enchantment": 9})
        b = pg.baseline(d)
        self.assertEqual(b["reasons"]["theme:phthisic"], "ENGINE")
        self.assertEqual([w for w in (s["why"] for s in b["preset"]["slots"]) if w.startswith("Main theme")],
                         ["Main theme Phthisic: 8 pieces give 62.5% to see one by turn 4."])

    def test_floor_reason_variants(self):
        # MV-driven: G2 X2 case above; autotag-floor variant via a plain floored raise.
        d = pk(mv=3, ci=("G",), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 9})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        cur = preset(36, slot("core:card_advantage", target=5), slot("core:ramp", target=10),
                     slot("group:targeted_interaction", target=10), slot("core:board_wipe", target=2))
        out = pg.improve_candidates(d, cur)
        r2 = next(c for c in out["candidates"] if c["trig"] == "R2" and c["ref"] == "core:card_advantage")
        self.assertEqual(r2["why"], "Raised to the floor of 7 (autotag health floor).")


if __name__ == "__main__":
    unittest.main()
