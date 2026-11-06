"""AI-assisted composition preset generation/improvement (composition-v1 §8). LLM stubbed.

Run: python -m pytest tests/test_composition_ai.py
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import composition_ai as ca
from src.server.domain.analytics.interactions import llm as llm_mod
from tests.test_identity import JsonProvider


def chain(*answers, fail=False):
    return llm_mod.LLMChain([JsonProvider(answers, fail=fail)])


def make_deck():
    cards = [{"name": "Forest", "quantity": 34}, {"name": "Command Tower", "quantity": 1}]
    texts = {"Atraxa": {"type_line": "Legendary Creature — Angel", "cmc": 4, "color_identity": ["W", "U", "B", "G"],
                        "oracle_text": "Flying, vigilance, deathtouch, lifelink"},
             "Forest": {"type_line": "Basic Land — Forest", "cmc": 0},
             "Command Tower": {"type_line": "Land", "cmc": 0}}
    tags = {}
    for i in range(8):
        n = f"Rock{i}"; cards.append({"name": n, "quantity": 1})
        texts[n] = {"type_line": "Artifact", "cmc": 2}; tags[n] = [{"tag": "ramp"}]
    for i in range(5):
        n = f"Draw{i}"; cards.append({"name": n, "quantity": 1})
        texts[n] = {"type_line": "Sorcery", "cmc": 3}; tags[n] = [{"tag": "card_advantage"}]
    for i in range(6):
        n = f"Kill{i}"; cards.append({"name": n, "quantity": 1})
        texts[n] = {"type_line": "Instant", "cmc": 2}; tags[n] = [{"tag": "removal"}]
    for i in range(10):
        n = f"Guy{i}"; cards.append({"name": n, "quantity": 1})
        texts[n] = {"type_line": "Creature — Human", "cmc": 3}
    deck = {"name": "Atraxa Test", "commanders": ["Atraxa"], "cards": cards, "source": "archidekt", "source_id": "1"}
    return deck, texts, tags


def packet(**kw):
    deck, texts, tags = make_deck()
    return ca.build_packet(deck, texts, tags, deck_tags=["counters"],
                           ident={"pillars": [{"id": "p1", "name": "Proliferate", "state": "active"}]}, **kw)


GOOD_GEN = {
    "name": "Atraxa Midrange", "description": "Balanced shell.",
    "structure": {"lands": 36}, "lands_math": "Burgess 31+4+4=39; Karsten ~35.",
    "slots": [
        {"ref": "core:ramp", "target": 11, "min": 10, "why": "Four-mana commander wants early mana."},
        {"ref": "core:card_advantage", "target": 8, "min": 7, "why": "Keeps the engine fed."},
        {"ref": "group:targeted_interaction", "target": 10, "min": 10, "why": "Answers early threats."},
    ],
}


class TestPacketAndMirror(unittest.TestCase):
    def test_packet_counts_by_quantity_and_hides_nothing_needed(self):
        p = packet()
        c = p["evaluation"]["counts"]
        self.assertEqual(c["structure:lands"], 35)
        self.assertEqual(c["core:ramp"], 8)
        self.assertEqual(c["group:targeted_interaction"], 6)
        self.assertEqual(p["land_math"]["burgess"], 31 + 4 + 4)
        self.assertIn("Rock0", p["deck_card_names"])

    def test_no_commander_rejected(self):
        deck, texts, tags = make_deck()
        deck["commanders"] = []
        with self.assertRaises(ca.CompositionAIError):
            ca.build_packet(deck, texts, tags)

    def test_mirror_copies_counts(self):
        m = ca.mirror_preset(packet())
        self.assertEqual(m["structure"]["lands"], 35)
        slots = {s["ref"]: s for s in m["slots"]}
        self.assertEqual(slots["core:ramp"]["target"], 8)
        self.assertNotIn("core:board_wipe", slots)
        self.assertFalse(ca.error_issues(m["issues"]))

    def test_prompt_packet_strips_card_names(self):
        p = packet()
        self.assertNotIn("deck_card_names", ca._prompt_packet(p))


class TestGenerate(unittest.TestCase):
    def test_good_draft_accepted_and_not_saved(self):
        out = ca.generate_preset(packet(), chain(GOOD_GEN))
        self.assertTrue(out["ai"])
        self.assertTrue(out["draft"])
        self.assertFalse(out["saved"])
        self.assertFalse(out["pins_deck"])
        self.assertEqual(out["preset"]["structure"]["lands"], 36)
        self.assertEqual(len(out["preset"]["slots"]), 3)
        self.assertFalse(ca.error_issues(out["preset"]["issues"]))

    def test_no_llm_falls_back_to_mirror(self):
        out = ca.generate_preset(packet(), chain(fail=True))
        self.assertEqual(out["fallback"], "mirror")
        self.assertFalse(out["ai"])

    def test_no_llm_without_fallback_raises(self):
        with self.assertRaises(ca.CompositionAIUnavailable):
            ca.generate_preset(packet(), chain(fail=True), fallback=False)

    def test_none_chain_falls_back(self):
        self.assertEqual(ca.generate_preset(packet(), None)["fallback"], "mirror")

    def _bad(self, mutate, code):
        d = copy.deepcopy(GOOD_GEN)
        mutate(d)
        with self.assertRaises(ca.CompositionAIUnavailable) as cm:
            ca.generate_preset(packet(), chain(d))
        self.assertEqual(cm.exception.kind, "invalid_draft")
        self.assertIn(code, {i["code"] for i in cm.exception.issues})

    def test_lands_outside_formulas_rejected(self):  # A7
        self._bad(lambda d: d["structure"].update(lands=28), "A7")

    def test_card_name_in_why_rejected(self):  # A6
        self._bad(lambda d: d["slots"][0].update(why="Rock0 is great here."), "A6")

    def test_edhrec_in_why_rejected(self):  # A6
        self._bad(lambda d: d["slots"][0].update(why="EDHREC says so."), "A6")

    def test_below_floor_without_pillar_rejected(self):  # A5
        self._bad(lambda d: d["slots"][0].update(target=5, min=5), "A5")

    def test_below_floor_with_pillar_accepted(self):
        d = copy.deepcopy(GOOD_GEN)
        d["slots"][0].update(target=6, min=6, pillar_id="p1", why="Proliferate plan runs light on rocks.")
        out = ca.generate_preset(packet(), chain(d))
        self.assertFalse(ca.error_issues(out["preset"]["issues"]))

    def test_unknown_ref_rejected(self):
        self._bad(lambda d: d["slots"].append({"ref": "core:bogus", "target": 1, "why": "x"}), "E3")

    def test_plan_room_too_small_rejected(self):  # W3 -> error
        self._bad(lambda d: d["slots"].append({"ref": "core:protection", "target": 30, "min": 30, "why": "Lots."}), "W3")

    def test_bad_range_rejected(self):
        self._bad(lambda d: d["slots"][0].update(target=3, min=9), "E5")

    def test_empty_slots_is_bad_response(self):
        d = copy.deepcopy(GOOD_GEN); d["slots"] = []
        with self.assertRaises(ca.CompositionAIUnavailable) as cm:
            ca.generate_preset(packet(), chain(d))
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_ai_output_never_equals_v8_mirror_of_bad_deck(self):
        # V8: a validator-rejected draft must not be silently downgraded to a saved preset.
        d = copy.deepcopy(GOOD_GEN); d["structure"]["lands"] = 20
        with self.assertRaises(ca.CompositionAIUnavailable):
            ca.generate_preset(packet(), chain(d))


class TestImprove(unittest.TestCase):
    def setUp(self):
        self.p = packet()
        self.p["current_preset"] = ca.generate_preset(self.p, chain(GOOD_GEN))["preset"]

    def run_ops(self, ops):
        return ca.improve_preset(self.p, chain({"ops": ops, "summary": "Tune."}))

    def test_valid_set_applies_and_bumps_version(self):
        out = self.run_ops([{"op": "set", "ref": "core:ramp", "from": {"target": 11}, "to": {"target": 12},
                             "why": "Commander costs four."}])
        self.assertEqual(len(out["ops"]), 1)
        slot = next(s for s in out["preset"]["slots"] if s["ref"] == "core:ramp")
        self.assertEqual(slot["target"], 12)
        self.assertEqual(out["preset"]["version"], 2)
        self.assertFalse(out["saved"])
        self.assertFalse(out["pins_deck"])

    def test_add_and_lands_ops(self):
        out = self.run_ops([
            {"op": "add", "ref": "core:board_wipe", "to": {"min": 2, "target": 3}, "why": "Recover from behind."},
            {"op": "lands", "from": 36, "to": 37, "why": "Four colors need fixing."}])
        self.assertEqual(len(out["ops"]), 2)
        self.assertEqual(out["preset"]["structure"]["lands"], 37)

    def test_bad_ops_dropped_good_kept(self):
        out = self.run_ops([
            {"op": "lands", "from": 36, "to": 25, "why": "Go low."},
            {"op": "set", "ref": "core:ramp", "to": {"target": 12}, "why": "EDHREC staple count."},
            {"op": "remove", "ref": "core:card_advantage", "why": "Not needed."},
            {"op": "set", "ref": "core:ramp", "to": {"target": 11}, "why": "No change."},
            {"op": "set", "ref": "core:finisher", "to": {"target": 2}, "why": "Not in preset."},
            {"op": "set", "ref": "core:ramp", "to": {"target": 12}, "why": "Commander costs four."}])
        self.assertEqual(len(out["ops"]), 1)
        self.assertEqual(len(out["dropped"]), 5)
        reasons = " ".join(d["reason"] for d in out["dropped"])
        self.assertIn("no-op", reasons)
        self.assertIn("health floor", reasons)

    def test_removing_floored_slot_with_pillar_allowed(self):
        out = self.run_ops([{"op": "remove", "ref": "core:card_advantage", "pillar_id": "p1",
                             "why": "Proliferate plan is not a draw deck."}])
        self.assertEqual(len(out["ops"]), 1)

    def test_unknown_pillar_dropped(self):
        out = self.run_ops([{"op": "remove", "ref": "core:card_advantage", "pillar_id": "zz", "why": "No."}])
        self.assertEqual(out["ops"], [])

    def test_max_six_ops(self):
        ops = [{"op": "add", "ref": f"type:{t}", "to": {"min": 1}, "why": "Engine floor."}
               for t in ("creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker", "battle")]
        out = self.run_ops(ops)
        self.assertEqual(len(out["ops"]), 6)
        self.assertTrue(any("A4" in d["reason"] for d in out["dropped"]))

    def test_stale_from_dropped(self):
        out = self.run_ops([{"op": "lands", "from": 40, "to": 37, "why": "Fixing."}])
        self.assertEqual(out["ops"], [])
        self.assertIn("stale", out["dropped"][0]["reason"])

    def test_original_preset_untouched(self):
        before = copy.deepcopy(self.p["current_preset"])
        self.run_ops([{"op": "lands", "from": 36, "to": 37, "why": "Fixing."}])
        self.assertEqual(self.p["current_preset"], before)

    def test_requires_current_preset(self):
        p = packet()
        with self.assertRaises(ca.CompositionAIError):
            ca.improve_preset(p, chain({"ops": [], "summary": ""}))

    def test_no_llm_raises(self):
        with self.assertRaises(ca.CompositionAIUnavailable) as cm:
            ca.improve_preset(self.p, chain(fail=True))
        self.assertIn(cm.exception.kind, ("llm_failed", "no_llm"))

    def test_non_list_ops_is_bad_response(self):
        with self.assertRaises(ca.CompositionAIUnavailable) as cm:
            ca.improve_preset(self.p, chain({"ops": "nope", "summary": ""}))
        self.assertEqual(cm.exception.kind, "bad_response")


class TestFloors(unittest.TestCase):
    def test_high_mv_commander_raises_ramp_floor(self):
        self.assertEqual(ca.modified_floors(6, {}, False)["core:ramp"], 12)

    def test_commander_that_does_job_lowers_floor(self):
        self.assertEqual(ca.modified_floors(3, {"core:ramp": True}, False)["core:ramp"], 7)

    def test_plan_room(self):
        pr = {"structure": {"commanders": 1, "lands": 36},
              "slots": [{"ref": "core:ramp", "min": 10}, {"ref": "core:card_advantage", "min": 7}]}
        self.assertEqual(ca.plan_room(pr), 63 - 17)


if __name__ == "__main__":
    unittest.main()
