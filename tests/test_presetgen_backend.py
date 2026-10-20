"""presetgen-v1 backend: reliability, land math, Baseline, improve candidates, AI contract, A9-A12.

Fixtures P1-P7, L1-L6, G1-G4, X1-X6, V16-V23 come from presetgen.md §9. LLM stubbed.
Run: python -m pytest tests/test_presetgen_backend.py
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import composition_ai as ca
from analytics import composition_presets as cp
from analytics import presetgen as pg
from analytics.interactions import llm as llm_mod
from tests.test_composition_ai import make_deck
from tests.test_identity import JsonProvider


def chain(*answers, fail=False):
    return llm_mod.LLMChain([JsonProvider(answers, fail=fail)])


def pk(mv=4, ci=("G",), avg=3.0, cheap=8, tags=(), cmd_tags=(), journal=None, counts=None, facets=None,
       current=None, mdfc=0.0, commanders=1, pillars=(), name="Test Deck", user_presets=None):
    tags = list(tags)
    themes = [t for t in tags if t not in pg.GENERIC_TAGS]
    cdoes = {}
    for ref in ca.BASE_FLOORS:
        ids = {"removal", "counterspell"} if ref == "group:targeted_interaction" else {ref.split(":", 1)[1]}
        cdoes[ref] = bool(set(cmd_tags) & ids)
    return {
        "commander": {"names": ["Cmdr"], "mv": mv, "color_identity": list(ci), "oracle_text": [""]},
        "commanders_count": commanders,
        "journal": {"thesis": "", "pillars": [{"id": p, "name": p} for p in pillars], **(journal or {})},
        "autotag": {"deck_tags": tags, "health": []},
        "evaluation": {"counts": dict(counts or {}), "lands": (counts or {}).get("structure:lands", 0)},
        "avg_mv_nonland": avg, "cheap_ramp_draw": cheap,
        "land_math": {"burgess": 31 + len(ci) + mv, "karsten": round(31.42 + 3.13 * avg - 0.28 * cheap, 1)},
        "catalog": ca.build_catalog(themes),
        "current_preset": copy.deepcopy(current) if current else {},
        "floors": ca.modified_floors(mv, cdoes, "tokens" in tags), "commander_does": cdoes,
        "deck_name": name, "deck_card_names": [], "registry_id": "archidekt:1", "existing_preset_names": [],
        "commander_tags": list(cmd_tags), "mdfc_eq": mdfc, "facet_counts": dict(facets or {}),
        "user_presets": fixture_presets() if user_presets is None else user_presets,
    }


def slot(ref, target=None, min=None, max=None, why="because", pillar_id=None):
    return {"ref": ref, "target": target, "min": min, "max": max, "why": why, "pillar_id": pillar_id}


def preset(lands, *slots, commanders=1):
    return {"name": "User", "structure": {"deck_size": 100, "commanders": commanders, "lands": lands},
            "slots": list(slots), "version": 1}


def by_ref(p):
    return {s["ref"]: s for s in p["slots"]}


# Literal fixture presets replacing the removed built-ins (unrelated to
# testing "builtin-ness" -- these are just convenient fixed presets for
# presetgen algorithm tests).
_FIXTURE_PRESETS = {
    "tcz_2025": {
        "id": "tcz_2025", "name": "Command Zone 2025", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 38},
        "slots": [slot("core:ramp", 10), slot("core:card_advantage", 12),
                  slot("group:targeted_interaction", 12), slot("core:board_wipe", 6)],
    },
    "standard_36": {
        "id": "standard_36", "name": "Standard 36", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 36},
        "slots": [slot("core:ramp", 12), slot("core:card_advantage", 7),
                  slot("group:targeted_interaction", 10), slot("core:board_wipe", 2)],
    },
    "big_mana": {
        "id": "big_mana", "name": "Big Mana", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 38},
        "slots": [slot("core:ramp", 14), slot("core:card_advantage", 10),
                  slot("group:targeted_interaction", 10), slot("core:board_wipe", 3)],
        "auto_select": {"archetypes": ["ramp"], "themes": [], "commander_mv_min": 6, "priority": 0},
    },
    "go_wide": {
        "id": "go_wide", "name": "Go Wide", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 36},
        "slots": [slot("core:ramp", 10), slot("core:card_advantage", 10),
                  slot("group:targeted_interaction", 10), slot("core:board_wipe", max=1),
                  slot("core:protection", min=3), slot("core:finisher", min=3)],
        "auto_select": {"archetypes": ["tokens", "aristocrats", "tribal"],
                        "themes": ["tokens", "sacrifice", "kindred:*"],
                        "commander_mv_min": None, "priority": 0},
    },
    "control": {
        "id": "control", "name": "Control", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 38},
        "slots": [slot("core:ramp", 10), slot("core:card_advantage", 12),
                  slot("group:targeted_interaction", 15), slot("core:board_wipe", 5)],
        "auto_select": {"archetypes": ["control", "stax", "pillowfort"], "themes": [],
                        "commander_mv_min": None, "priority": 0},
    },
    "voltron": {
        "id": "voltron", "name": "Voltron", "version": 1,
        "structure": {"deck_size": 100, "commanders": 1, "lands": 37},
        "slots": [slot("core:ramp", 10), slot("core:card_advantage", 10),
                  slot("core:protection", 10), slot("group:targeted_interaction", 10),
                  slot("core:board_wipe", 2), slot("theme:voltron", min=10)],
        "auto_select": {"archetypes": ["voltron"], "themes": ["voltron"],
                        "commander_mv_min": None, "priority": 0},
    },
}


def get_fixture_preset(preset_id):
    """Stand-in for the removed composition_presets.get_builtin()."""
    return copy.deepcopy(_FIXTURE_PRESETS[preset_id])


def fixture_presets():
    """Stand-in for the removed composition_presets.builtin_presets()."""
    return [copy.deepcopy(p) for p in _FIXTURE_PRESETS.values()]


# fixture decks ------------------------------------------------------------------------------

def deck_g1():  # mono-G big mana, MV 6, lands inputs = L1
    return pk(mv=6, ci=("G",), avg=3.4, cheap=8, journal={"primary_archetype": "ramp"},
              facets={"facet:answers_artifact_enchantment": 4})


def deck_g2():  # mono-R goblins, MV 4, lands inputs = L2
    return pk(mv=4, ci=("R",), avg=2.9, cheap=9, tags=["kindred:goblin", "tokens"],
              facets={"facet:answers_artifact_enchantment": 1})


def deck_g3():  # four-colour good stuff, commander tagged card_advantage, lands inputs = L3
    return pk(mv=4, ci=("W", "U", "B", "G"), avg=3.3, cheap=10, cmd_tags=["card_advantage"],
              facets={"facet:answers_artifact_enchantment": 5})


def deck_g4():  # five-colour MV 5, lands inputs = L4
    return pk(mv=5, ci=("W", "U", "B", "R", "G"), avg=3.6, cheap=6,
              facets={"facet:answers_artifact_enchantment": 4})


class ReliabilityTests(unittest.TestCase):
    def test_p1_to_p6(self):
        self.assertEqual(round(pg.reliability(10, 9, 99), 3), 0.633)
        self.assertEqual(round(pg.reliability(7, 11, 99), 3), 0.574)
        self.assertEqual(round(pg.reliability(2, 14, 99), 3), 0.264)
        self.assertEqual(round(pg.reliability(12, 9, 98), 3), 0.708)
        self.assertEqual(pg.reliability(0, 9, 99), 0.0)
        self.assertEqual(pg.reliability(92, 8, 99), 1.0)

    def test_p7_floors_follow_from_the_model(self):
        floors = [("core:ramp", 10), ("core:ramp@big_commander", 12), ("core:card_advantage", 7),
                  ("group:targeted_interaction", 10), ("core:board_wipe", 2), ("core:finisher", 2)]
        for key, floor in floors:
            row = pg.CONFIG["reliability"][key]
            n = 7 + row["turn"]
            self.assertGreaterEqual(pg.reliability(floor, n, 99), row["p"], key)
            self.assertLess(pg.reliability(floor - 1, n, 99), row["p"], key)
        # and the autotag floors agree with composition_ai's base floors
        self.assertEqual(ca.BASE_FLOORS["core:ramp"], 10)
        self.assertEqual(ca.modified_floors(5, {}, False)["core:ramp"], 12)

    def test_entries_skip_zero_and_max_only(self):
        p = preset(37, slot("core:ramp", 10), slot("core:board_wipe", max=1), slot("core:tutor", 0))
        entries = pg.reliability_entries(p, 99)
        self.assertEqual([e["ref"] for e in entries], ["core:ramp"])
        self.assertEqual(entries[0], {"ref": "core:ramp", "turn": 2, "cards_seen": 9, "k": 10, "p": 0.633,
                                      "library": 99})


class LandMathTests(unittest.TestCase):
    def check(self, ci, mv, avg, cheap, mdfc, burgess, karsten, lands):
        r = pg.land_math(ci, mv, avg, cheap, mdfc)
        self.assertEqual(r["burgess"], burgess)
        self.assertAlmostEqual(r["karsten"], karsten, places=3)
        self.assertEqual(r["lands"], lands)
        return r

    def test_l1_to_l6(self):
        r = self.check(1, 6, 3.4, 8, 0, 38, 39.822, 39)
        self.assertEqual(r["window"][0], 36)
        self.assertAlmostEqual(r["window"][1], 41.822, places=3)
        self.check(1, 4, 2.9, 9, 0, 36, 37.977, 37)
        self.check(4, 4, 3.3, 10, 0, 39, 38.949, 39)
        self.check(5, 5, 3.6, 6, 0, 41, 41.008, 41)
        self.check(1, 6, 3.4, 8, 1.14, 38, 39.822, 38)
        self.check(1, 1, 2.0, 15, 0, 33, 33.48, 33)

    def test_text(self):
        t = pg.land_math(1, 6, 3.4, 8)["text"]
        self.assertEqual(t, "Burgess 31+1+6 = 38; Karsten 31.42+3.13·3.4−0.28·8 = 39.8; mean 38.9 → 39")
        self.assertIn(" − 1.14 MDFC", pg.land_math(1, 6, 3.4, 8, 1.14)["text"])

    def test_clamped_into_the_a7_window(self):
        r = pg.land_math(1, 1, 2.0, 15, 30.0)  # absurd MDFC adjustment still lands inside the window
        self.assertEqual(r["lands"], 31)
        self.assertGreaterEqual(r["lands"], r["window"][0])

    def test_mdfc_equivalents(self):
        deck = {"commanders": ["C"], "cards": [{"name": "A", "quantity": 1}, {"name": "B", "quantity": 2},
                                               {"name": "Land", "quantity": 1}]}
        texts = {"A": {"type_line": "Sorcery // Land", "rarity": "mythic"},
                 "B": {"type_line": "Creature // Land", "rarity": "rare"},
                 "Land": {"type_line": "Land"}}
        self.assertEqual(pg.mdfc_equivalents(deck, texts), round(0.74 + 2 * 0.38, 2))


class BaselineTests(unittest.TestCase):
    def test_g1_big_mana(self):
        b = pg.baseline(deck_g1())
        p = b["preset"]
        self.assertEqual(b["selected"]["preset_id"], "big_mana")
        self.assertEqual(p["structure"], {"deck_size": 100, "commanders": 1, "lands": 39})
        s = by_ref(p)
        self.assertEqual({r: x["target"] for r, x in s.items()},
                         {"core:ramp": 14, "core:card_advantage": 10, "group:targeted_interaction": 10,
                          "core:board_wipe": 3})
        self.assertEqual(ca.plan_room(p), 23)
        self.assertTrue(all(x["why"] == "Kept from Big Mana." for x in s.values()))
        self.assertEqual({e["ref"]: e["p"] for e in b["reliability"]},
                         {"core:ramp": 0.762, "core:card_advantage": 0.71, "group:targeted_interaction": 0.71,
                          "core:board_wipe": 0.37})
        self.assertEqual(p["source"], "baseline:archidekt:1")
        self.assertEqual(p["name"], "Baseline for Test Deck")
        self.assertIsNone(p["auto_select"])

    def test_g2_goblins(self):
        b = pg.baseline(deck_g2())
        p = b["preset"]
        s = by_ref(p)
        self.assertEqual(b["selected"]["preset_id"], "go_wide")
        self.assertEqual(p["structure"]["lands"], 37)
        self.assertEqual(s["theme:kindred:goblin"]["min"], 8)
        self.assertEqual(s["facet:answers_artifact_enchantment"]["min"], 3)
        self.assertEqual(s["core:board_wipe"], {**s["core:board_wipe"], "target": None, "min": None, "max": 1})
        self.assertEqual(ca.plan_room(p), 26)
        self.assertEqual(s["theme:kindred:goblin"]["why"],
                         "Main theme Kindred: Goblin: 8 pieces give 62.5% to see one by turn 4.")
        self.assertEqual(s["facet:answers_artifact_enchantment"]["why"],
                         "Only 1 answers to artifacts or enchantments; 3 is the minimum.")
        rel = {e["ref"]: e["p"] for e in b["reliability"]}
        self.assertEqual(rel["core:ramp"], 0.633)
        self.assertEqual(rel["core:protection"], 0.324)
        self.assertEqual(rel["core:finisher"], 0.393)
        self.assertEqual(rel["theme:kindred:goblin"], 0.625)
        self.assertEqual(rel["facet:answers_artifact_enchantment"], 0.348)
        self.assertNotIn("core:board_wipe", rel)

    def test_g3_commander_does_the_job(self):
        b = pg.baseline(deck_g3())
        s = by_ref(b["preset"])
        self.assertIsNone(b["selected"]["preset_id"])
        self.assertEqual(b["preset"]["structure"]["lands"], 39)
        self.assertEqual(s["core:card_advantage"]["target"], 10)
        self.assertEqual(s["core:card_advantage"]["why"], "The commander already does this job, so 2 fewer are needed.")
        self.assertEqual(ca.plan_room(b["preset"]), 22)
        rel = {e["ref"]: e["p"] for e in b["reliability"]}
        self.assertEqual((rel["core:ramp"], rel["group:targeted_interaction"], rel["core:board_wipe"]),
                         (0.633, 0.778, 0.61))

    def test_g4_trim(self):
        b = pg.baseline(deck_g4())
        s = by_ref(b["preset"])
        self.assertEqual(b["preset"]["structure"]["lands"], 41)
        self.assertEqual({r: x["target"] for r, x in s.items()},
                         {"core:ramp": 12, "core:card_advantage": 10, "group:targeted_interaction": 12,
                          "core:board_wipe": 4})
        self.assertEqual(ca.plan_room(b["preset"]), 20)
        self.assertEqual(b["reasons"], {"core:ramp": "RAMP_MV", "core:card_advantage": "TRIM",
                                        "core:board_wipe": "TRIM"})
        self.assertEqual(s["core:ramp"]["why"], "Commander costs 5; ramp floor is 12, giving 70.4% to see one by turn 2.")
        self.assertEqual(s["core:board_wipe"]["why"], "Trimmed to keep 20 cards for the deck's own plan.")
        self.assertEqual(s["group:targeted_interaction"]["why"], "Kept from Baseline.")

    def test_no_blue_drops_counterspell_slots(self):
        p = pk(mv=4, ci=("G",), journal={"primary_archetype": "control"})
        b = pg.baseline(p)
        self.assertEqual(b["selected"]["preset_id"], "control")
        self.assertNotIn("facet:answers_stack", by_ref(b["preset"]))
        blue = pg.baseline(pk(mv=4, ci=("U",), journal={"primary_archetype": "control"}))
        self.assertEqual(blue["selected"]["preset_id"], "control")

    def test_inactive_theme_slots_are_dropped(self):
        b = pg.baseline(pk(journal={"primary_archetype": "voltron"}))
        self.assertNotIn("theme:voltron", by_ref(b["preset"]))
        b = pg.baseline(pk(journal={"primary_archetype": "voltron"}, tags=["voltron"]))
        self.assertIn("theme:voltron", by_ref(b["preset"]))

    def test_floor_raise_on_low_base(self):
        # standard_36 wipes target 2 for a deck whose tokens are active -> capped at 1; ramp 12 stays
        b = pg.baseline(pk(mv=5, ci=("G",), journal={"primary_archetype": "ramp"}, tags=["tokens"],
                           facets={"facet:answers_artifact_enchantment": 9}))
        self.assertEqual(by_ref(b["preset"])["core:board_wipe"]["max"], 1)

    def test_never_tutor_mana_fixing_or_flags(self):
        for d in (deck_g1(), deck_g2(), deck_g3(), deck_g4()):
            refs = set(by_ref(pg.baseline(d)["preset"]))
            self.assertFalse({"core:tutor", "core:mana_fixing"} & refs)
            self.assertFalse(any(r.split(":")[1] in ca.FLAG_NAMES for r in refs))

    def test_partner_commanders(self):
        b = pg.baseline(pk(commanders=2))
        self.assertEqual(b["preset"]["structure"]["commanders"], 2)
        self.assertEqual(b["reliability"][0]["library"], 98)

    def test_deterministic_and_valid(self):
        for d in (deck_g1(), deck_g2(), deck_g3(), deck_g4()):
            a, b = pg.baseline(d), pg.baseline(copy.deepcopy(d))
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
            ctx = ca.Context(d)
            issues = ca.validate_preset(a["preset"], ctx, ai=False, scope="deck")
            self.assertEqual([i for i in issues if i["level"] == "error" or i["code"] == "W3"], [])
            self.assertEqual(ca._lands_rule(a["preset"]["structure"]["lands"], ctx), [])
            lands = a["preset"]["structure"]["lands"]
            self.assertEqual(lands + (100 - 1 - lands) + 1, 100)

    def test_baseline_names_no_cards(self):
        d = deck_g2()
        d["deck_card_names"] = ["Goblin Guide"]
        b = pg.baseline(d)
        self.assertNotIn("Goblin Guide", json.dumps(b))

    def test_auto_select_ignores_pin(self):
        d = deck_g1()
        d["current_preset"] = preset(34, slot("core:ramp", 9))
        self.assertEqual(pg.baseline(d)["selected"]["preset_id"], "big_mana")


class AutoSelectTests(unittest.TestCase):
    def test_steps(self):
        fx = fixture_presets()
        pick = lambda j, t, mv, **kw: cp.auto_select(j, t, mv, fx, **kw)
        self.assertEqual(pick({"primary_archetype": "ramp"}, [], 2)["reason"]["step"], 2)
        self.assertEqual(pick({"primary_archetype": "x", "secondary_archetypes": ["tokens"]}, [], 2)["preset_id"], "go_wide")
        self.assertEqual(pick({}, ["kindred:elf"], 2)["reason"], {"step": 4, "signal": "deck tag: kindred:elf"})
        self.assertEqual(pick({}, [], 7)["reason"], {"step": 5, "signal": "commander MV 7"})
        # no archetype/theme/MV signal matches at mv=3 -> no preset selected (no hardcoded default)
        self.assertIsNone(pick({}, [], 3)["preset_id"])
        self.assertEqual(pick({}, [], 3)["reason"], {"step": 6, "signal": "no preset selected"})
        # phantom midrange counts as unset
        self.assertEqual(pick({"primary_archetype": "midrange"}, [], 3)["reason"]["step"], 6)

    def test_pin_and_user_presets_first(self):
        fx = fixture_presets()
        user = {"id": "p-mine", "name": "Mine", "slots": [], "structure": {},
                "auto_select": {"archetypes": ["ramp"], "themes": [], "commander_mv_min": None, "priority": 10}}
        sel = cp.auto_select({"primary_archetype": "ramp"}, [], 3, [user] + fx)
        self.assertEqual((sel["preset_id"], sel["also_matched"]), ("p-mine", ["big_mana"]))
        self.assertEqual(cp.auto_select({}, [], 3, [user] + fx, pin="standard_36")["mode"], "pinned")
        self.assertEqual(cp.auto_select({}, [], 3, [user] + fx, pin="standard_36", skip_pin=True)["mode"], "auto")

    def test_builtin_plan_rooms(self):
        for p in fixture_presets():
            self.assertEqual([i for i in ca.validate_preset(dict(p, name=p["name"]), None) if i["level"] == "error"], [])
        self.assertEqual(ca.plan_room(get_fixture_preset("tcz_2025")), 21)
        self.assertEqual(ca.plan_room(get_fixture_preset("standard_36")), 32)


class ImproveCandidateTests(unittest.TestCase):
    def test_x1_goblins_on_tcz(self):
        d = deck_g2()
        d["current_preset"] = get_fixture_preset("tcz_2025")
        out = pg.improve_candidates(d)
        got = [(c["id"], c["trig"], c["priority"], c["op"], c["ref"], c["from"], c["to"]) for c in out["candidates"]]
        self.assertEqual(got, [
            ("c1", "R3", "P1", "set", "core:board_wipe", {"target": 6}, {"max": 1}),
            ("c2", "R8", "P3", "add", "facet:answers_artifact_enchantment", None, {"min": 3}),
            ("c3", "R9", "P4", "add", "theme:kindred:goblin", None, {"min": 8}),
        ])
        self.assertEqual(out["candidates"][0]["why"], "Tokens deck: wipes hit your own board, so cap them at 1.")

    def test_x2_lands_and_floor(self):
        d = deck_g1()
        d["current_preset"] = preset(34, slot("core:ramp", 9), slot("core:card_advantage", 10),
                                     slot("group:targeted_interaction", 10), slot("core:board_wipe", 2))
        out = pg.improve_candidates(d)
        got = [(c["id"], c["trig"], c["op"], c["ref"], c["from"], c["to"]) for c in out["candidates"]]
        self.assertEqual(got, [("c1", "R1", "lands", "structure:lands", 34, 39),
                               ("c2", "R2", "set", "core:ramp", {"target": 9}, {"target": 12})])
        self.assertEqual(out["candidates"][1]["why"], "Raised to the floor of 12 (commander MV 6).")

    def test_x4_r6_stops_at_15(self):
        d = pk(mv=3, ci=("G",), avg=3.0, cheap=8, facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        d["current_preset"] = preset(38, slot("core:ramp", 12), slot("core:card_advantage", 15),
                                     slot("group:targeted_interaction", 14), slot("core:board_wipe", 6))
        out = pg.improve_candidates(d)
        r6 = [c for c in out["candidates"] if c["trig"] == "R6"]
        self.assertEqual([(c["ref"], c["from"], c["to"]) for c in r6],
                         [("core:card_advantage", {"target": 15}, {"target": 14})])
        self.assertEqual(r6[0]["why"], "Trimmed to keep 15 cards for the deck's own plan.")

    def test_x5_r7_commander_does_it_and_gap_still_listed(self):
        d = deck_g3()
        d["current_preset"] = get_fixture_preset("tcz_2025")
        d["evaluation"]["counts"] = {"core:card_advantage": 8}
        out = pg.improve_candidates(d)
        self.assertEqual([(c["trig"], c["ref"], c["to"]) for c in out["candidates"]],
                         [("R7", "core:card_advantage", {"target": 10})])
        gap = out["deck_gaps"][0]
        self.assertEqual((gap["ref"], gap["status"], gap["count"], gap["lower"], gap["need"]),
                         ("core:card_advantage", "short", 8, 10, 1))

    def test_x6_pillar_lock(self):
        d = pk(mv=3, ci=("G",), pillars=["pl-2"], facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        d["current_preset"] = preset(38, slot("core:ramp", 12), slot("core:card_advantage", 15, pillar_id="pl-2"),
                                     slot("group:targeted_interaction", 14), slot("core:board_wipe", 6))
        out = pg.improve_candidates(d)
        r6 = [c for c in out["candidates"] if c["trig"] == "R6"]
        self.assertEqual([(c["ref"], c["from"], c["to"]) for c in r6], [("core:board_wipe", {"target": 6}, {"target": 5})])

    def test_r4_r5_removals(self):
        d = pk(mv=3, ci=("G",), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 3})
        d["current_preset"] = preset(36, slot("core:ramp", 10), slot("core:counterspell", 4),
                                     slot("facet:answers_stack", min=3), slot("theme:voltron", min=10))
        out = pg.improve_candidates(d)
        trigs = {c["ref"]: c["trig"] for c in out["candidates"]}
        self.assertEqual(trigs["core:counterspell"], "R5")
        self.assertEqual(trigs["facet:answers_stack"], "R5")
        self.assertEqual(trigs["theme:voltron"], "R4")
        self.assertEqual([c["id"] for c in out["candidates"]][:3], ["c1", "c2", "c3"])
        self.assertTrue(all(c["op"] == "remove" for c in out["candidates"] if c["trig"] in ("R4", "R5")))

    def test_r10_lands_far_from_baseline(self):
        d = deck_g1()  # window [36, 41.8]; baseline 39
        d["current_preset"] = preset(36, slot("core:ramp", 14), slot("core:card_advantage", 10),
                                     slot("group:targeted_interaction", 10), slot("core:board_wipe", 3))
        out = pg.improve_candidates(d)
        self.assertEqual([(c["trig"], c["priority"], c["from"], c["to"]) for c in out["candidates"]],
                         [("R10", "P4", 36, 39)])

    def test_one_op_per_ref(self):
        d = pk(mv=3, ci=("G",), tags=["tokens"], facets={"facet:answers_artifact_enchantment": 3})
        d["land_math"] = {"burgess": 35, "karsten": 36.8}
        # wipes: R3 caps to max 1, then R6 would also touch it; the result is a single op
        d["current_preset"] = preset(38, slot("core:ramp", 12), slot("core:card_advantage", 15),
                                     slot("group:targeted_interaction", 15), slot("core:board_wipe", 6))
        out = pg.improve_candidates(d)
        refs = [c["ref"] for c in out["candidates"]]
        self.assertEqual(len(refs), len(set(refs)))

    def test_clean_preset_has_no_candidates_but_gaps(self):
        d = deck_g1()
        d["current_preset"] = pg.baseline(d)["preset"]
        d["evaluation"]["counts"] = {"core:ramp": 9}
        out = pg.improve_candidates(d)
        self.assertEqual(out["candidates"], [])
        gap = out["deck_gaps"][0]
        self.assertEqual((gap["ref"], gap["status"], gap["need"], gap["turn"], gap["p_now"], gap["p_at_lower"]),
                         ("core:ramp", "low", 4, 2, 0.592, 0.762))

    def test_requires_current_preset(self):
        with self.assertRaises(pg.PresetGenError):
            pg.improve_candidates(deck_g1())

    def test_deterministic(self):
        d = deck_g2()
        d["current_preset"] = get_fixture_preset("tcz_2025")
        self.assertEqual(json.dumps(pg.improve_candidates(d)), json.dumps(pg.improve_candidates(copy.deepcopy(d))))


class ValidatorTests(unittest.TestCase):
    def gen_draft(self, d, base, **edit):
        p = copy.deepcopy(base["preset"])
        for ref, upd in edit.items():
            ref = ref.replace("__", ":")
            s = by_ref(p).get(ref)
            if s is None:
                p["slots"].append(slot(ref, **upd))
            else:
                s.update(upd)
        return {"preset": {"name": "AI draft", "description": "d", "structure": {"lands": p["structure"]["lands"]},
                           "slots": p["slots"]}, "lands_math": "Burgess 38; Karsten 39.8", "summary": "ok"}

    def test_v16_v17_a9(self):
        d = deck_g1()
        base = pg.baseline(d)
        draft = self.gen_draft(d, base, core__ramp={"target": 20})
        res = pg.generate(d, chain(draft))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertIn("A9", res["ai"]["codes"])
        self.assertEqual(res["preset"], base["preset"])
        d17 = deck_g1()
        d17["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        draft = self.gen_draft(d17, pg.baseline(d17), core__ramp={"target": 20, "pillar_id": "pl-1"})
        ok = pg.generate(d17, chain(draft))
        self.assertEqual(ok["ai"]["status"], "ok", ok["ai"])
        self.assertEqual(by_ref(ok["preset"])["core:ramp"]["target"], 20)
        self.assertEqual(ca.plan_room(ok["preset"]), 17)

    def test_v22_lands_window(self):
        d = deck_g1()
        draft = self.gen_draft(d, pg.baseline(d))
        draft["preset"]["structure"]["lands"] = 44
        res = pg.generate(d, chain(draft))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertIn("A7", res["ai"]["codes"])

    def test_generate_good_ai_draft(self):
        d = deck_g1()
        draft = self.gen_draft(d, pg.baseline(d), core__ramp={"target": 15})
        res = pg.generate(d, chain(draft))
        self.assertEqual(res["ai"]["status"], "ok")
        self.assertTrue(res["ai"]["model"])
        self.assertEqual(res["preset"]["source"], "ai:" + res["ai"]["model"])
        self.assertFalse(res["saved"] or res["pins_deck"])
        self.assertEqual(res["summary"], "ok")

    def test_a11_no_tutor_slot(self):
        d = deck_g1()
        draft = self.gen_draft(d, pg.baseline(d), core__tutor={"min": 3, "pillar_id": None})
        res = pg.generate(d, chain(draft))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertTrue({"A11"} <= set(res["ai"]["codes"]))

    def test_generate_fallbacks(self):
        d = deck_g1()
        off = pg.generate(d, None)
        self.assertEqual(off["ai"]["status"], "off")
        self.assertEqual(off["preset"], pg.baseline(d)["preset"])
        failed = pg.generate(d, chain(fail=True))
        self.assertEqual(failed["ai"]["status"], "fallback")
        self.assertEqual(failed["preset"], pg.baseline(d)["preset"])
        bad = pg.generate(d, chain({"nothing": 1}))
        self.assertEqual(bad["ai"]["status"], "fallback")
        self.assertEqual(bad["ai"]["reason"], "invalid_draft")

    def test_prompt_packet_has_no_card_names_and_system_prompt_is_verbatim(self):
        d = deck_g2()
        d["deck_card_names"] = ["Goblin Guide"]
        d["current_preset"] = get_fixture_preset("tcz_2025")
        cands = pg.improve_candidates(d)
        pp = pg.prompt_packet(d, "improve", pg.baseline(d), cands["candidates"], cands["deck_gaps"], [])
        self.assertNotIn("Goblin Guide", json.dumps(pp))
        self.assertEqual(set(pp) >= {"baseline", "candidates", "reliability", "deck_gaps", "floors", "limits", "mode"}, True)
        self.assertEqual(pp["limits"]["max_ops"], 6)
        self.assertIn("An op that isn't a candidate needs a pillar_id.", pg.SYSTEM_PROMPT)
        self.assertTrue(pg.SYSTEM_PROMPT.startswith("You draft Commander deck-composition presets"))

    def improve_deck(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        d["current_preset"] = preset(34, slot("core:ramp", 9), slot("core:card_advantage", 10),
                                     slot("group:targeted_interaction", 10), slot("core:board_wipe", 2))
        return d

    def test_x3_a10_a12_dropped(self):
        d = self.improve_deck()
        d["evaluation"]["counts"] = {"group:targeted_interaction": 8}
        op = {"op": "set", "ref": "group:targeted_interaction", "from": {"target": 10}, "to": {"target": 8},
              "why": "Deck runs 8."}
        res = pg.validate_ops(d["current_preset"], [op], d, pg.improve_candidates(d)["candidates"])
        self.assertEqual(res["ops"], [])
        self.assertTrue({"A10", "A12"} <= set(res["dropped"][0]["codes"]))

    def test_v18_tutor_op_dropped(self):
        d = self.improve_deck()
        op = {"op": "add", "ref": "core:tutor", "to": {"min": 3}, "why": "Find the engine.", "pillar_id": "pl-1"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(res["ops"], [])
        self.assertIn("A11", res["dropped"][0]["codes"])

    def test_v19_candidate_mismatch(self):
        d = self.improve_deck()
        cands = pg.improve_candidates(d)["candidates"]
        c = next(c for c in cands if c["trig"] == "R2")
        op = {"candidate_id": c["id"], "op": "set", "ref": c["ref"], "to": {"target": 11}, "why": "Floor."}
        res = pg.validate_ops(d["current_preset"], [op], d, cands)
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])

    def test_candidate_ops_accepted_and_reworded(self):
        d = self.improve_deck()
        cands = pg.improve_candidates(d)["candidates"]
        ops = [{"candidate_id": c["id"], "op": c["op"], "ref": c["ref"], "from": c["from"], "to": c["to"],
                "why": "Commander costs six, so ramp must come early."} for c in cands]
        res = pg.improve(d, chain({"ops": ops, "summary": "Two fixes."}))
        self.assertEqual(res["ai"]["status"], "ok")
        self.assertEqual(len(res["ops"]), 2)
        self.assertEqual(res["summary"], "Two fixes.")
        self.assertEqual(res["preset"]["structure"]["lands"], 39)
        self.assertEqual(by_ref(res["preset"])["core:ramp"]["target"], 12)
        self.assertEqual(res["ops"][0]["candidate_id"], "c1")

    def test_v20_pillar_justified_off_candidate(self):
        d = deck_g2()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Go wide"}]
        d["current_preset"] = pg.baseline(d)["preset"]
        d["evaluation"]["counts"] = {"core:ramp": 10}
        op = {"op": "add", "ref": "theme:tokens", "to": {"min": 10}, "why": "Tokens are the plan.", "pillar_id": "pl-1"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_v21_cap_six(self):
        d = deck_g1()
        d["journal"]["pillars"] = [{"id": "pl-1", "name": "Big mana"}]
        d["current_preset"] = preset(39, slot("core:ramp", 14), slot("core:card_advantage", 10),
                                     slot("group:targeted_interaction", 10), slot("core:board_wipe", 3))
        refs = ["type:creature", "type:artifact", "type:enchantment", "type:instant", "type:sorcery",
                "type:planeswalker", "type:battle", "facet:flexible"]
        ops = [{"op": "add", "ref": r, "to": {"min": 1}, "why": "Pillar needs it.", "pillar_id": "pl-1"} for r in refs]
        res = pg.validate_ops(d["current_preset"], ops, d, [])
        self.assertEqual([o["ref"] for o in res["ops"]], refs[:6])
        self.assertEqual([x["codes"] for x in res["dropped"]], [["A4"], ["A4"]])

    def test_v23_unknown_pillar_is_no_pillar(self):
        d = self.improve_deck()
        op = {"op": "add", "ref": "type:creature", "to": {"min": 10}, "why": "Creatures.", "pillar_id": "pl-9"}
        res = pg.validate_ops(d["current_preset"], [op], d, [])
        self.assertEqual(res["ops"], [])
        self.assertIn("A10", res["dropped"][0]["codes"])

    def test_a12_exempt_for_candidate_r7(self):
        d = deck_g3()
        d["current_preset"] = get_fixture_preset("tcz_2025")
        d["evaluation"]["counts"] = {"core:card_advantage": 8}
        cands = pg.improve_candidates(d)["candidates"]
        op = {"candidate_id": "c1", "op": "set", "ref": "core:card_advantage", "to": {"target": 10}, "why": "Commander draws."}
        res = pg.validate_ops(d["current_preset"], [op], d, cands)
        self.assertEqual(len(res["ops"]), 1, res["dropped"])

    def test_improve_ai_off_applies_top_six(self):
        d = deck_g2()
        d["current_preset"] = get_fixture_preset("tcz_2025")
        res = pg.improve(d, None)
        self.assertEqual(res["ai"]["status"], "off")
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2", "c3"])
        s = by_ref(res["preset"])
        self.assertEqual((s["core:board_wipe"]["max"], s["core:board_wipe"]["target"]), (1, None))
        self.assertEqual(s["theme:kindred:goblin"]["min"], 8)
        self.assertEqual(res["preset"]["version"], 2)
        self.assertEqual(res["deck_gaps"], res["deck_gaps"])

    def test_improve_no_candidates(self):
        d = deck_g1()
        d["current_preset"] = pg.baseline(d)["preset"]
        res = pg.improve(d, chain({"ops": [], "summary": "x"}))
        self.assertEqual((res["ops"], res["summary"], res["ai"]["status"]), ([], "No preset issues found.", "skipped"))

    def test_improve_falls_back_when_all_ops_rejected(self):
        d = self.improve_deck()
        op = {"op": "set", "ref": "core:ramp", "to": {"target": 9}, "why": "Nothing."}
        res = pg.improve(d, chain({"ops": [op], "summary": "x"}))
        self.assertEqual((res["ai"]["status"], res["ai"]["reason"]), ("fallback", "all_ops_rejected"))
        self.assertEqual([o["candidate_id"] for o in res["ops"]], ["c1", "c2"])

    def test_improve_llm_failure_falls_back(self):
        d = self.improve_deck()
        res = pg.improve(d, chain(fail=True))
        self.assertEqual(res["ai"]["status"], "fallback")
        self.assertEqual(len(res["ops"]), 2)


class PacketIntegrationTests(unittest.TestCase):
    def test_build_packet_and_baseline_end_to_end(self):
        deck, texts, tags = make_deck()
        packet = pg.build_packet(deck, texts, tags, deck_tags=["counters"])
        self.assertEqual(packet["commander_tags"], [])
        self.assertEqual(packet["mdfc_eq"], 0.0)
        res = pg.generate(packet, None)
        self.assertEqual(res["ai"]["status"], "off")
        self.assertEqual(res["preset"]["structure"]["commanders"], 1)
        self.assertEqual(res["preset"]["structure"]["lands"] + 99 - res["preset"]["structure"]["lands"], 99)
        self.assertEqual(res["preset"]["source"], "baseline:archidekt:1")
        imp = pg.improve(dict(packet, current_preset=res["preset"]), None)
        self.assertEqual(imp["mode"], "improve")


if __name__ == "__main__":
    unittest.main()
