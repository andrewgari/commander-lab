"""BR-4: bracket evaluator (analytics/bracket_eval.py).

evaluate_bracket(deck, journal, metadata) -> BracketEvaluation. Pure and
deterministic: BR-3 findings + the Deck Journal + metadata we already have
(Commander Salt, our power estimate, EDHREC, price) -> estimated bracket and
power level, conflicts against the journal targets, and an explanation.

Fixture decks are the BR-3 ones (real Commander Spellbook combo data).
No network, no Redis.

Run: python -m unittest tests.test_bracket_eval
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import bracket_eval as be  # noqa: E402
from analytics import brackets as br  # noqa: E402
from analytics import engine  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bracket_check_decks.json")
GC = br.load_fallback().cards


def load(key):
    with open(FIXTURE, encoding="utf-8") as f:
        d = json.load(f)["decks"][key]
    return {"name": d["name"], "commanders": d["commanders"], "cards": d["cards"]}, d["spellbook_in_deck"]


def journal(bracket=None, power=7, pets=(), vision="", novelties=""):
    return {"target_bracket": bracket, "target_power_level": {"scale": power},
            "pet_cards": list(pets), "deck_vision": {"description": vision}, "novelties": novelties}


def three_gc_deck():
    deck, _ = load("gc_heavy")
    keep = {"Rhystic Study", "Demonic Tutor", "Smothering Tithe", "Sol Ring", "Island"}
    return {"commanders": [], "cards": [c for c in deck["cards"] if c["name"] in keep]}


def evaluate(deck, j, combos=(), **meta):
    meta.setdefault("game_changers", GC)
    meta.setdefault("combos", list(combos))
    return be.evaluate_bracket(deck, j, meta)


def rules(ev, severity=None):
    return [c.rule for c in ev.conflicts if severity is None or c.severity == severity]


class Bracket1TooManyGameChangers(unittest.TestCase):
    """Target 1 + 3 Game Changers = a hard conflict naming the 3 cards."""

    def setUp(self):
        self.ev = evaluate(three_gc_deck(), journal(bracket=1, power=3), power_estimate=6.5)

    def test_hard_game_changer_conflict_names_the_cards(self):
        hard = [c for c in self.ev.conflicts if c.severity == "hard"]
        self.assertEqual([c.rule for c in hard], ["game_changers"])
        self.assertEqual(sorted(hard[0].cards), ["Demonic Tutor", "Rhystic Study", "Smothering Tithe"])
        self.assertIn("3 Game Changers", hard[0].message)
        self.assertIn("Bracket 1", hard[0].message)

    def test_estimated_bracket_3_and_not_aligned(self):
        self.assertEqual(self.ev.minimum_bracket, 3)
        self.assertEqual(self.ev.estimated_bracket, 3)
        self.assertEqual(self.ev.target_bracket, 1)
        self.assertFalse(self.ev.no_target)
        self.assertFalse(self.ev.aligned)

    def test_exact_mismatch_is_advisory_plays_above_target(self):
        mm = [c for c in self.ev.conflicts if c.rule == "bracket_mismatch"]
        self.assertEqual(len(mm), 1)
        self.assertEqual(mm[0].severity, "advisory")
        self.assertIn("plays above target", mm[0].message)


class Bracket3Clean(unittest.TestCase):
    """Kaalia + Master of Cruelties (Spicy, late game) at target 3: no conflicts."""

    def setUp(self):
        deck, combos = load("spicy_only")
        self.ev = evaluate(deck, journal(bracket=3, power=7), combos=combos, power_estimate=7.0)

    def test_estimated_3_aligned_no_conflicts(self):
        self.assertEqual(self.ev.estimated_bracket, 3)
        self.assertEqual(self.ev.estimated_bracket_name, "Upgraded")
        self.assertTrue(self.ev.aligned)
        self.assertEqual(self.ev.conflicts, [])

    def test_reason_names_the_combo(self):
        self.assertIn("Master of Cruelties", self.ev.estimated_bracket_reason)
        self.assertIn("Bracket 3", self.ev.estimated_bracket_reason)

    def test_power_shown_against_target(self):
        self.assertEqual(self.ev.estimated_power_level, 7.0)
        self.assertEqual(self.ev.target_power_level, 7)
        self.assertEqual(self.ev.power_delta, 0.0)
        self.assertTrue(self.ev.power_aligned)


class UnsetTarget(unittest.TestCase):
    def test_no_target_still_estimates_but_no_bracket_conflicts(self):
        ev = evaluate(three_gc_deck(), journal(bracket=None, power=6), power_estimate=6.5)
        self.assertTrue(ev.no_target)
        self.assertIsNone(ev.target_bracket)
        self.assertIsNone(ev.aligned)
        self.assertEqual(ev.estimated_bracket, 3)
        self.assertEqual(rules(ev), [])

    def test_no_journal_at_all(self):
        ev = evaluate(three_gc_deck(), None, power_estimate=6.5)
        self.assertTrue(ev.no_target)
        self.assertIsNone(ev.target_power_level)
        self.assertIsNone(ev.power_aligned)
        self.assertEqual(ev.conflicts, [])


class PetCardConflict(unittest.TestCase):
    """A pet card that itself breaks the target bracket: advisory, the user's call."""

    def setUp(self):
        self.ev = evaluate(three_gc_deck(), journal(bracket=2, power=5, pets=["Rhystic Study"]),
                           power_estimate=5.0)

    def test_rule_break_stays_hard(self):
        self.assertIn("game_changers", rules(self.ev, "hard"))

    def test_pet_card_advisory_is_worded_as_their_call(self):
        pets = [c for c in self.ev.conflicts if c.rule == "pet_card"]
        self.assertEqual(len(pets), 1)
        self.assertEqual(pets[0].severity, "advisory")
        self.assertEqual(pets[0].cards, ["Rhystic Study"])
        self.assertIn("pet card", pets[0].message)
        self.assertIn("your call", pets[0].message)
        self.assertIn("Game Changer", pets[0].message)

    def test_card_named_in_novelties_is_the_same_kind_of_advisory(self):
        ev = evaluate(three_gc_deck(), journal(bracket=2, power=5, novelties="Win off a big Smothering Tithe turn"),
                      power_estimate=5.0)
        hits = [c for c in ev.conflicts if c.rule == "journal_card"]
        self.assertEqual([(c.severity, c.cards) for c in hits], [("advisory", ["Smothering Tithe"])])
        self.assertIn("your call", hits[0].message)
        self.assertIn("novelties", hits[0].message)

    def test_pet_cards_that_break_nothing_are_silent(self):
        ev = evaluate(three_gc_deck(), journal(bracket=3, power=7, pets=["Sol Ring"]), power_estimate=7.0)
        self.assertNotIn("pet_card", rules(ev))


class PowerBandAdvisory(unittest.TestCase):
    """Target 2 + Commander Salt power well above Bracket 2's band = advisory."""

    def setUp(self):
        deck, combos = load("precon_b2")
        self.ev = evaluate(deck, journal(bracket=2, power=5), combos=combos,
                           commandersalt={"power": 7.8, "salt": 31.0, "bracket_rating": None},
                           power_estimate=6.0)

    def test_power_band_advisory(self):
        band = [c for c in self.ev.conflicts if c.rule == "power_band"]
        self.assertEqual(len(band), 1)
        self.assertEqual(band[0].severity, "advisory")
        self.assertIn("7.8", band[0].message)
        self.assertIn("Commander Salt", band[0].message)
        self.assertIn("Bracket 2", band[0].message)
        self.assertEqual(rules(self.ev, "hard"), [])

    def test_commander_salt_power_is_the_power_estimate_and_bumps_the_bracket(self):
        self.assertEqual(self.ev.power_source, "commandersalt")
        self.assertEqual(self.ev.estimated_power_level, 7.8)
        self.assertEqual(self.ev.minimum_bracket, 2)
        self.assertEqual(self.ev.estimated_bracket, 3)
        self.assertIn("plays above target", " ".join(c.message for c in self.ev.conflicts))

    def test_inside_the_band_is_quiet(self):
        deck, combos = load("precon_b2")
        ev = evaluate(deck, journal(bracket=2, power=5), combos=combos,
                      commandersalt={"power": 5.2}, power_estimate=6.0)
        self.assertEqual(ev.estimated_bracket, 2)
        self.assertNotIn("power_band", rules(ev))
        self.assertTrue(ev.aligned)


class PowerTargetThreshold(unittest.TestCase):
    """|estimated - target power| > engine.POWER_ALIGN_TOLERANCE (1.5) = advisory."""

    def deck(self):
        deck, combos = load("spicy_only")
        return deck, combos

    def test_reuses_the_engine_constant(self):
        self.assertIs(be.POWER_ALIGN_TOLERANCE, engine.POWER_ALIGN_TOLERANCE)
        self.assertEqual(engine.POWER_ALIGN_TOLERANCE, 1.5)

    def test_inside_threshold(self):
        deck, combos = self.deck()
        ev = evaluate(deck, journal(bracket=3, power=6), combos=combos, power_estimate=7.5)
        self.assertNotIn("power_target", rules(ev))
        self.assertTrue(ev.power_aligned)

    def test_outside_threshold(self):
        deck, combos = self.deck()
        ev = evaluate(deck, journal(bracket=3, power=6), combos=combos, power_estimate=7.6)
        hits = [c for c in ev.conflicts if c.rule == "power_target"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "advisory")
        self.assertIn("7.6", hits[0].message)
        self.assertIn("6", hits[0].message)
        self.assertFalse(ev.power_aligned)


class PlaysBelowTarget(unittest.TestCase):
    def test_lower_estimate_is_advisory_plays_below_target(self):
        deck, combos = load("precon_b2")
        ev = evaluate(deck, journal(bracket=4, power=8), combos=combos, power_estimate=5.0)
        self.assertEqual(ev.estimated_bracket, 2)
        mm = [c for c in ev.conflicts if c.rule == "bracket_mismatch"]
        self.assertEqual([c.severity for c in mm], ["advisory"])
        self.assertIn("plays below target", mm[0].message)
        self.assertEqual(rules(ev, "hard"), [])
        self.assertFalse(ev.aligned)

    def test_target_5_is_never_estimated(self):
        deck, combos = load("combo")
        ev = evaluate(deck, journal(bracket=5, power=10), combos=combos, power_estimate=9.5)
        self.assertEqual(ev.estimated_bracket, 4)
        mm = [c for c in ev.conflicts if c.rule == "bracket_mismatch"]
        self.assertIn("never estimate Bracket 5", mm[0].message)


class Exhibition(unittest.TestCase):
    def test_bracket_1_only_when_declared(self):
        deck = {"commanders": [], "cards": [{"name": "Llanowar Elves", "quantity": 1}]}
        self.assertEqual(evaluate(deck, journal(bracket=None), power_estimate=3.0).estimated_bracket, 2)
        ev = evaluate(deck, journal(bracket=1, power=3), power_estimate=3.0)
        self.assertEqual(ev.estimated_bracket, 1)
        self.assertTrue(ev.aligned)


class Explanation(unittest.TestCase):
    """Ordered signals behind both estimates + a plain-language summary."""

    def setUp(self):
        deck, combos = load("combo")
        self.ev = evaluate(deck, journal(bracket=3, power=7), combos=combos,
                           commandersalt={"power": 7.8, "salt": 42.5, "bracket_rating": 3.6},
                           power_estimate=6.5,
                           edhrec={"status": "ok", "overlap_pct": 41, "decks_sampled": 5200},
                           price=1234.5)
        self.sig = {s.signal: s for s in self.ev.explanation.signals}

    def test_signal_shape(self):
        for s in self.ev.explanation.signals:
            self.assertTrue(s.signal and s.effect and s.source)

    def test_hard_floors_come_first_highest_first(self):
        order = [s.signal for s in self.ev.explanation.signals]
        self.assertEqual(order[:3], ["extra_turns", "two_card_combos", "game_changers"])
        self.assertIn("Bracket 4", self.sig["two_card_combos"].effect)
        self.assertIn("Commander Spellbook", self.sig["two_card_combos"].source)
        self.assertIn("Commander Spellbook", self.sig["extra_turns"].source)
        self.assertIn("Bracket 3", self.sig["game_changers"].effect)
        self.assertEqual(self.sig["game_changers"].value, 1)
        self.assertEqual(self.sig["mass_land_denial"].value, 0)
        self.assertIn("no floor", self.sig["mass_land_denial"].effect)
        self.assertIn("Scryfall", self.sig["game_changers"].source)

    def test_power_signals(self):
        self.assertEqual(self.sig["commandersalt_power"].value, 7.8)
        self.assertIn("power estimate", self.sig["commandersalt_power"].effect)
        self.assertEqual(self.ev.estimated_power_level, 7.8)
        self.assertEqual(self.ev.power_source, "commandersalt")
        self.assertEqual(self.sig["commandersalt_bracket_rating"].value, 3.6)
        self.assertIn("our_power_estimate", self.sig)
        self.assertIn("not used", self.sig["our_power_estimate"].effect)

    def test_context_signals(self):
        self.assertEqual(self.sig["commandersalt_salt"].value, 42.5)
        self.assertEqual(self.sig["price"].value, 1234.5)
        self.assertEqual(self.sig["edhrec_overlap"].value, 41)
        self.assertIn("context", self.sig["price"].effect)

    def test_summary_paragraph(self):
        s = self.ev.explanation.summary
        self.assertIn("1 Game Changer", s)
        self.assertIn("Thassa's Oracle", s)
        self.assertIn("Bracket 4", s)
        self.assertIn("Commander Salt power 7.8", s)
        self.assertIn("power 7.8/10", s)
        self.assertIn("target 7", s)
        self.assertIn("$1,234.50", s)

    def test_hard_conflicts_against_target_3(self):
        self.assertEqual(sorted(rules(self.ev, "hard")), ["extra_turns", "two_card_combos"])


class FallbacksAndDeterminism(unittest.TestCase):
    def test_no_power_inputs_uses_the_bracket_band(self):
        deck, combos = load("spicy_only")
        ev = evaluate(deck, journal(bracket=3, power=7), combos=combos)
        self.assertEqual(ev.power_source, "bracket_band")
        self.assertEqual(ev.estimated_power_level, 7.0)
        sig = {x.signal: x for x in ev.explanation.signals}
        self.assertIn("no power data", sig["power_fallback"].effect)

    def test_our_estimate_is_clamped_into_the_estimated_bracket_band(self):
        # The engine heuristic starts every deck at 6.0; it never moves the bracket.
        deck, combos = load("precon_b2")
        ev = evaluate(deck, journal(bracket=2, power=5), combos=combos, power_estimate=6.5)
        self.assertEqual(ev.estimated_bracket, 2)
        self.assertEqual(ev.estimated_power_level, 6.0)
        self.assertEqual(ev.power_source, "our_estimate")
        sig = {x.signal: x for x in ev.explanation.signals}
        self.assertEqual(sig["our_power_estimate"].value, 6.5)
        self.assertIn("6.0", sig["our_power_estimate"].effect)
        self.assertIn("Bracket 2 band", sig["our_power_estimate"].effect)

    def test_commandersalt_meta_scores_object_accepted(self):
        from analytics.models import MetaScores, PowerScore, SaltScore
        ms = MetaScores(power=PowerScore(score=5.1, tier=None, description=None),
                        salt=SaltScore(score=12.0, salt_sum=None, description=None),
                        meta_rank=None, provider_metrics={"bracket_rating": 2.2})
        deck, combos = load("precon_b2")
        ev = evaluate(deck, journal(bracket=2, power=5), combos=combos, commandersalt=ms)
        self.assertEqual(ev.estimated_power_level, 5.1)
        self.assertEqual(ev.estimated_bracket, 2)

    def test_findings_can_be_passed_in(self):
        from analytics import bracket_check as bc
        deck, combos = load("combo")
        f = bc.check_deck_brackets(deck, game_changers=GC, combos=combos)
        ev = be.evaluate_bracket(deck, journal(bracket=4, power=8), {"findings": f, "power_estimate": 8.0})
        self.assertEqual(ev.minimum_bracket, 4)
        self.assertTrue(ev.aligned)

    def test_deterministic(self):
        deck, combos = load("combo")
        a = evaluate(deck, journal(bracket=3), combos=combos, power_estimate=6.5).model_dump()
        b = evaluate(deck, journal(bracket=3), combos=combos, power_estimate=6.5).model_dump()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
