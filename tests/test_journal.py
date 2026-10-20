"""
AN-5: Deck Journal + review check-in (UX_SPEC.md §3.3, ANALYTICS_SPEC Layer 2).

- The journal is the user_intent.py schema plus the AN-5 fields: pet_cards[],
  novelties, budget_constraints.hard_cap / soft_cap (max_card_price is kept
  as an alias of hard_cap).
- Stored in Redis at journal:deck:{registry_id}. Fill once, edit anytime
  (PUT merges whole sections). insights.pet_cards() reads the same key.
- Every review opens with a check-in: deltas since the last review (+/- cards,
  power, price, on-plan) and one "still true?" row per field with computed
  evidence. Answers save per row; "changed" rows can be undone (the previous
  section is restored). Finishing pushes the review onto
  journal:reviews:{registry_id} (newest first); the next check-in diffs
  against that snapshot.

No network: the metrics context (power / prices / on-plan / pet notice) is
passed in or stubbed.

Run: python -m pytest tests/test_journal.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import insights
from analytics import journal
from analytics import prices
from tests.test_interactions import FakeRedis
from user_intent import load_schema, parse_user_intent, resolve_intent_defaults, validate_intent

DECK = {"id": 417707, "name": "Holy Hellfire", "registry_id": "archidekt:417707", "commanders": ["Kaalia of the Vast"],
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Platinum Angel", "quantity": 1},
                  {"name": "Necropotence", "quantity": 1}, {"name": "Sol Ring", "quantity": 1},
                  {"name": "Plains", "quantity": 5}]}

FULL = {
    "deck_vision": {"intent": "tune_up", "description": "Kaalia cheats a fatty every combat.", "target_turn_win": 8},
    "target_power_level": {"scale": 7},
    "budget_constraints": {"no_budget": False, "total_budget": 500, "hard_cap": 40, "soft_cap": 15},
    "preferred_win_conditions": {"primary": "combat", "secondary": ["commander_damage"],
                                 "disliked_or_excluded": ["combo", "lockout"]},
    "pet_cards": ["Platinum Angel", "Lord of the Void"],
    "novelties": "Win with a Dragon Tempest swarm once.",
    "freeform_notes": "",
}


def ctx(**kw):
    base = {"power": 6.5, "price": 468.0, "on_plan": 71,
            "prices": {"Necropotence": 31.0, "Sol Ring": 2.0, "Platinum Angel": 1.5, "Plains": 0.1},
            "pillars": [{"name": "Payoffs worth cheating", "state": "pinned"},
                        {"name": "Swing twice", "state": "draft"}],
            "proposal": {"new": 1}, "pet_notice": {"name": "Platinum Angel", "edges": 0, "weak_score": 0.82}}
    base.update(kw)
    return base


class TestSchemaFields(unittest.TestCase):
    def test_schema_declares_the_new_fields(self):
        props = load_schema()["properties"]
        self.assertEqual(props["pet_cards"]["type"], "array")
        self.assertEqual(props["pet_cards"]["items"]["type"], "string")
        self.assertEqual(props["novelties"]["type"], "string")
        bc = props["budget_constraints"]["properties"]
        for k in ("hard_cap", "soft_cap"):
            with self.subTest(k=k):
                self.assertEqual(bc[k]["type"], ["number", "null"])
                self.assertEqual(bc[k]["minimum"], 0)
        self.assertIn("alias", bc["max_card_price"]["description"].lower())

    def test_defaults_resolve_without_leaking_into_metadata(self):
        res = resolve_intent_defaults({})
        self.assertEqual(res["pet_cards"], [])
        self.assertEqual(res["novelties"], "")
        self.assertIsNone(res["budget_constraints"]["hard_cap"])
        self.assertIsNone(res["budget_constraints"]["soft_cap"])
        self.assertEqual(res["metadata"], {})

    def test_max_card_price_is_an_alias_of_hard_cap(self):
        res = resolve_intent_defaults({"budget_constraints": {"max_card_price": 25}})
        self.assertEqual(res["budget_constraints"]["hard_cap"], 25)
        res = resolve_intent_defaults({"budget_constraints": {"hard_cap": 40, "max_card_price": 10}})
        self.assertEqual(res["budget_constraints"]["hard_cap"], 40)
        self.assertEqual(res["budget_constraints"]["max_card_price"], 40)

    def test_dataclass_carries_the_new_fields(self):
        it = parse_user_intent(FULL)
        self.assertEqual(it.pet_cards, ["Platinum Angel", "Lord of the Void"])
        self.assertEqual(it.novelties, "Win with a Dragon Tempest swarm once.")
        self.assertEqual(it.budget_constraints.hard_cap, 40)
        self.assertEqual(it.budget_constraints.soft_cap, 15)
        self.assertEqual(it.budget_constraints.max_card_price, 40)

    def test_validation_of_caps_and_pets(self):
        ok, errs = validate_intent({"budget_constraints": {"hard_cap": 10, "soft_cap": 20}})
        self.assertFalse(ok)
        self.assertTrue(any("soft_cap (20.0) cannot exceed hard_cap (10.0)" in e for e in errs), errs)
        ok, errs = validate_intent({"budget_constraints": {"soft_cap": -1}})
        self.assertFalse(ok)
        self.assertTrue(any("soft_cap cannot be negative" in e for e in errs), errs)
        ok, errs = validate_intent({"budget_constraints": {"no_budget": True, "hard_cap": 5}})
        self.assertFalse(ok)
        ok, errs = validate_intent({"pet_cards": "Platinum Angel"})
        self.assertFalse(ok)
        self.assertTrue(any("pet_cards must be a list of card names" in e for e in errs), errs)
        ok, errs = validate_intent({"novelties": 3})
        self.assertFalse(ok)
        self.assertTrue(validate_intent(FULL)[0], validate_intent(FULL)[1])


class TestJournalStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_empty_until_saved(self):
        self.assertIsNone(journal.load_journal(self.r, DECK))
        view = journal.journal_view(self.r, DECK)
        self.assertFalse(view["exists"])
        self.assertEqual(view["journal"]["target_power_level"], {"scale": 7, "tier": "optimized"})
        self.assertEqual(view["journal"]["pet_cards"], [])

    def test_save_load_and_insights_reads_pets(self):
        rec = journal.save_journal(self.r, DECK, FULL, snapshot=journal.snapshot(DECK, ctx()))
        self.assertIn("journal:deck:archidekt:417707", self.r.store)
        self.assertEqual(rec["target_power_level"]["tier"], "optimized")  # derived, never picked
        self.assertEqual(insights.pet_cards(self.r, DECK), ["Platinum Angel", "Lord of the Void"])
        loaded = journal.load_journal(self.r, DECK)
        self.assertEqual(loaded["novelties"], FULL["novelties"])
        self.assertEqual(loaded["_journal"]["baseline"]["power"], 6.5)
        self.assertTrue(loaded["_journal"]["created_at"])

    def test_tier_is_always_derived_from_scale(self):
        rec = journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 9, "tier": "casual"}})
        self.assertEqual(rec["target_power_level"], {"scale": 9, "tier": "competitive"})

    def test_put_merges_whole_sections(self):
        journal.save_journal(self.r, DECK, FULL)
        rec = journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 8}})
        self.assertEqual(rec["target_power_level"]["scale"], 8)
        self.assertEqual(rec["deck_vision"]["description"], FULL["deck_vision"]["description"])
        self.assertEqual(rec["pet_cards"], FULL["pet_cards"])

    def test_baseline_is_kept_on_later_saves(self):
        journal.save_journal(self.r, DECK, FULL, snapshot={"at": "2026-09-14T00:00:00+00:00", "power": 6.0})
        journal.save_journal(self.r, DECK, {"novelties": "x"}, snapshot={"at": "later", "power": 9})
        self.assertEqual(journal.load_journal(self.r, DECK)["_journal"]["baseline"]["power"], 6.0)

    def test_invalid_payload_raises_with_all_errors(self):
        with self.assertRaises(journal.JournalError) as cm:
            journal.save_journal(self.r, DECK, {"budget_constraints": {"hard_cap": 5, "soft_cap": 9},
                                                "target_power_level": {"scale": 11}})
        self.assertEqual(len(cm.exception.errors), 2)
        self.assertIsNone(journal.load_journal(self.r, DECK))

    def test_pets_are_trimmed_and_deduped(self):
        rec = journal.save_journal(self.r, DECK, {"pet_cards": [" Platinum Angel ", "Platinum Angel", ""]})
        self.assertEqual(rec["pet_cards"], ["Platinum Angel"])

    def test_no_budget_clears_the_numbers(self):
        rec = journal.save_journal(self.r, DECK, {"budget_constraints": {"no_budget": True}})
        self.assertTrue(rec["budget_constraints"]["no_budget"])
        self.assertIsNone(rec["budget_constraints"]["hard_cap"])


class TestCaps(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_global_defaults_then_per_deck_override(self):
        self.assertEqual(journal.get_defaults(self.r), journal.DEFAULT_CAPS)
        caps = journal.effective_caps(self.r, resolve_intent_defaults({}))
        self.assertEqual((caps["hard_cap"], caps["hard_source"]), (journal.DEFAULT_CAPS["hard_cap"], "global"))
        rec = journal.save_journal(self.r, DECK, {"budget_constraints": {"hard_cap": 40}})
        caps = journal.effective_caps(self.r, rec)
        self.assertEqual((caps["hard_cap"], caps["hard_source"]), (40, "deck"))
        self.assertEqual(caps["soft_source"], "global")

    def test_defaults_are_editable_and_validated(self):
        journal.set_defaults(self.r, {"hard_cap": 50, "soft_cap": 20})
        self.assertEqual(journal.get_defaults(self.r), {"hard_cap": 50.0, "soft_cap": 20.0})
        with self.assertRaises(journal.JournalError):
            journal.set_defaults(self.r, {"hard_cap": 5, "soft_cap": 20})

    def test_no_budget_ignores_caps(self):
        caps = journal.effective_caps(self.r, resolve_intent_defaults({"budget_constraints": {"no_budget": True}}))
        self.assertIsNone(caps["hard_cap"])
        self.assertIsNone(caps["soft_cap"])


class TestDeltasAndRows(unittest.TestCase):
    def test_snapshot_and_deltas(self):
        prev = journal.snapshot({"cards": [{"name": "A", "quantity": 1}, {"name": "B", "quantity": 1},
                                           {"name": "Plains", "quantity": 6}]},
                                {"power": 6.5, "price": 412.0, "on_plan": 64})
        cur = journal.snapshot({"cards": [{"name": "A", "quantity": 1}, {"name": "C", "quantity": 1},
                                          {"name": "Plains", "quantity": 5}]},
                               {"power": 7.2, "price": 468.0, "on_plan": 71})
        d = journal.deltas(prev, cur)
        self.assertEqual(d["added"], ["C"])
        self.assertEqual(d["removed"], ["B", "Plains"])
        self.assertEqual(d["power"], [6.5, 7.2])
        self.assertEqual(d["price"], [412.0, 468.0])
        self.assertEqual(d["on_plan"], [64, 71])

    def test_deltas_without_a_previous_snapshot(self):
        d = journal.deltas(None, journal.snapshot(DECK, ctx()))
        self.assertEqual(d["added"], [])
        self.assertEqual(d["power"], [None, 6.5])

    def _rows(self, payload=FULL, c=None, answers=None, r=None):
        r = r or FakeRedis()
        rec = resolve_intent_defaults(payload)
        return {row["field"]: row for row in journal.build_rows(rec, c or ctx(), answers or {},
                                                                 journal.effective_caps(r, rec), DECK)}

    def test_rows_in_spec_order(self):
        rows = journal.build_rows(resolve_intent_defaults(FULL), ctx(), {}, journal.effective_caps(FakeRedis(),
                                  resolve_intent_defaults(FULL)), DECK)
        self.assertEqual([r["field"] for r in rows], journal.FIELDS)
        self.assertEqual([r["label"] for r in rows], ["Vision", "Win cons", "Target power", "Target bracket", "Budget",
                                                      "Pet cards",
                                                      "Novelties", "Pillars", "Card changes"])

    def test_values(self):
        rows = self._rows()
        self.assertEqual(rows["vision"]["value"], "Tune-up. Kaalia cheats a fatty every combat. Win by turn 8.")
        self.assertEqual(rows["win_cons"]["value"], "Combat (primary) · Commander damage · excluded: Combo, Lockout")
        self.assertEqual(rows["power"]["value"], "7 · optimized")
        self.assertEqual(rows["budget"]["value"], "$500 total · per-card hard $40 / soft $15")
        self.assertEqual(rows["pets"]["value_list"], ["Platinum Angel", "Lord of the Void"])
        self.assertEqual(rows["pillars"]["value"], "Payoffs worth cheating · Swing twice")

    def test_budget_evidence_never_prices_cards_already_in_the_deck(self):
        # Necropotence ($31) is in DECK and over the $15 soft cap; the deck is $468 of a $500 budget.
        # None of that is evidence: price only matters for cards that would have to be bought.
        self.assertIsNone(self._rows()["budget"]["evidence"])
        ev = self._rows(c=ctx(prices={"Necropotence": 5500.0}, price=5600.0))["budget"]["evidence"]
        self.assertIsNone(ev)
        self.assertIsNone(self._rows(payload={"budget_constraints": {"no_budget": True}})["budget"]["evidence"])

    def test_budget_evidence_shows_accepted_buy_spend_only(self):
        ev = self._rows(c=ctx(accepted_buy_spend=42.0))["budget"]["evidence"]
        self.assertEqual(ev, "Accepted buys: $42 of $500.")
        self.assertNotIn("% of budget", ev)

    def test_pet_evidence(self):
        ev = self._rows()["pets"]["evidence"]
        self.assertIn("Lord of the Void is no longer in the deck.", ev)
        self.assertIn("Platinum Angel has 0 edges and is your weakest pet. Still a pet?", ev)
        self.assertIsNone(self._rows(payload={"pet_cards": []}, c=ctx(pet_notice=None))["pets"]["evidence"])

    def test_power_and_pillar_and_vision_evidence(self):
        rows = self._rows(payload={"target_power_level": {"scale": 9}})
        self.assertEqual(rows["power"]["evidence"], "Estimate 6.5 vs target 9.")
        self.assertIsNone(self._rows()["power"]["evidence"])  # 6.5 vs 7 is within 1.5
        self.assertEqual(self._rows()["pillars"]["evidence"], "Re-sync proposed 1 new pillar. 1 pillar is still a draft.")
        self.assertEqual(self._rows()["pillars"]["link"], "/deck/Holy%20Hellfire/identity")
        self.assertIn("Empty", self._rows(payload={})["vision"]["evidence"])

    def test_card_changes_row_is_context(self):
        rows = self._rows(c=ctx(deltas={"added": ["Isshin"], "removed": ["Reliquary Tower"]}))
        self.assertEqual(rows["cards"]["value"], "+ Isshin · − Reliquary Tower")
        self.assertTrue(rows["cards"]["context"])
        self.assertEqual(self._rows()["cards"]["value"], "No card changes.")


class TestReviewLifecycle(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        journal.save_journal(self.r, DECK, FULL, snapshot={"at": "2026-09-14T00:00:00+00:00", "power": 6.0,
                                                            "price": 412.0, "on_plan": 64,
                                                            "cards": {"Kaalia of the Vast": 1, "Reliquary Tower": 1}})

    def test_needs_a_journal(self):
        with self.assertRaises(journal.JournalError):
            journal.start_review(FakeRedis(), DECK, ctx())

    def test_first_review_diffs_against_the_journal_baseline(self):
        view = journal.start_review(self.r, DECK, ctx(), now="2026-10-12T00:00:00+00:00")
        self.assertEqual(view["since"]["at"], "2026-09-14T00:00:00+00:00")
        self.assertEqual(view["since"]["days"], 28)
        self.assertEqual(view["deltas"]["removed"], ["Reliquary Tower"])
        self.assertIn("Necropotence", view["deltas"]["added"])
        self.assertEqual(view["deltas"]["power"], [6.0, 6.5])
        self.assertEqual(view["progress"], {"total": 9, "answered": 0, "changed": 0})
        self.assertIn("journal:review:open:archidekt:417707", self.r.store)

    def test_start_resumes_the_open_review(self):
        a = journal.start_review(self.r, DECK, ctx())
        journal.answer_row(self.r, DECK, "vision", "true")
        b = journal.start_review(self.r, DECK, ctx(power=7.0))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["progress"]["answered"], 1)
        self.assertEqual(b["deltas"]["power"], [6.0, 7.0])  # context refreshed, answers kept

    def test_still_true_and_unknown_field(self):
        journal.start_review(self.r, DECK, ctx())
        view = journal.answer_row(self.r, DECK, "budget", "true")
        row = next(r for r in view["rows"] if r["field"] == "budget")
        self.assertEqual(row["answer"], "true")
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "nope", "true")
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "budget", "maybe")

    def test_answer_without_open_review(self):
        with self.assertRaises(journal.NoOpenReview):
            journal.answer_row(self.r, DECK, "vision", "true")

    def test_change_saves_the_journal_and_undo_restores_it(self):
        journal.start_review(self.r, DECK, ctx())
        view = journal.answer_row(self.r, DECK, "power", "changed", {"target_power_level": {"scale": 8}})
        row = next(r for r in view["rows"] if r["field"] == "power")
        self.assertEqual(row["answer"], "changed")
        self.assertEqual(row["value"], "8 · optimized")
        self.assertEqual(row["was"], "was 7 · optimized")
        self.assertEqual(view["progress"], {"total": 9, "answered": 1, "changed": 1})
        self.assertEqual(journal.load_journal(self.r, DECK)["target_power_level"]["scale"], 8)
        view = journal.answer_row(self.r, DECK, "power", None)
        self.assertEqual(journal.load_journal(self.r, DECK)["target_power_level"]["scale"], 7)
        row = next(r for r in view["rows"] if r["field"] == "power")
        self.assertIsNone(row["answer"])

    def test_change_rejects_other_sections_and_bad_values(self):
        journal.start_review(self.r, DECK, ctx())
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "power", "changed", {"novelties": "sneaky"})
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "power", "changed", {"target_power_level": {"scale": 99}})
        self.assertEqual(journal.load_journal(self.r, DECK)["novelties"], FULL["novelties"])

    def test_finish_records_history_and_next_review_diffs_against_it(self):
        journal.start_review(self.r, DECK, ctx(), now="2026-10-12T00:00:00+00:00")
        journal.answer_row(self.r, DECK, "vision", "true")
        rec = journal.finish_review(self.r, DECK, now="2026-10-12T00:10:00+00:00")
        self.assertFalse(rec["skipped"])
        self.assertEqual(rec["answers"]["vision"]["answer"], "true")
        self.assertEqual(rec["snapshot"]["power"], 6.5)
        self.assertEqual(rec["deltas"]["power"], [6.0, 6.5])
        self.assertNotIn("journal:review:open:archidekt:417707", self.r.store)
        hist = journal.list_reviews(self.r, DECK)
        self.assertEqual([h["id"] for h in hist], [rec["id"]])
        nxt = journal.start_review(self.r, DECK, ctx(power=7.2), now="2026-10-20T00:00:00+00:00")
        self.assertEqual(nxt["since"]["at"], "2026-10-12T00:10:00+00:00")
        self.assertEqual(nxt["since"]["days"], 7)
        self.assertEqual(nxt["deltas"]["power"], [6.5, 7.2])
        self.assertEqual(nxt["deltas"]["added"], [])

    def test_skip_is_recorded(self):
        journal.start_review(self.r, DECK, ctx())
        self.assertTrue(journal.finish_review(self.r, DECK, skipped=True)["skipped"])

    def test_finish_without_open_review(self):
        with self.assertRaises(journal.NoOpenReview):
            journal.finish_review(self.r, DECK)


class TestTargetBracket(unittest.TestCase):
    """BR-2: optional target_bracket (1-5 or null), separate from the 1-10 power level."""

    def setUp(self):
        self.r = FakeRedis()

    def test_schema_declares_target_bracket(self):
        tb = load_schema()["properties"]["target_bracket"]
        self.assertEqual(tb["type"], ["integer", "null"])
        self.assertEqual((tb["minimum"], tb["maximum"]), (1, 5))
        self.assertIsNone(tb["default"])
        self.assertIn("target_bracket", journal.SECTION_KEYS)

    def test_valid_values_and_null(self):
        for v in (1, 2, 3, 4, 5, None):
            with self.subTest(v=v):
                ok, errs = validate_intent({"target_bracket": v})
                self.assertTrue(ok, errs)
                rec = journal.save_journal(self.r, DECK, {"target_bracket": v})
                self.assertEqual(rec["target_bracket"], v)
                self.assertEqual(parse_user_intent({"target_bracket": v}).target_bracket, v)

    def test_invalid_values_rejected(self):
        for v in (0, 6, -1, "x", "3", 3.0, 2.5, True, [3], {"n": 3}):
            with self.subTest(v=v):
                ok, errs = validate_intent({"target_bracket": v})
                self.assertFalse(ok)
                self.assertTrue(any("target_bracket" in e for e in errs), errs)
                with self.assertRaises(journal.JournalError):
                    journal.save_journal(self.r, DECK, {"target_bracket": v})
        self.assertIsNone(journal.load_journal(self.r, DECK))

    def test_default_is_unset_and_never_derived_from_power(self):
        self.assertIsNone(resolve_intent_defaults({})["target_bracket"])
        res = resolve_intent_defaults({"target_power_level": {"scale": 10}})
        self.assertIsNone(res["target_bracket"])
        self.assertNotIn("target_bracket", res["metadata"])
        rec = journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 2}, "target_bracket": 4})
        self.assertEqual((rec["target_power_level"]["scale"], rec["target_bracket"]), (2, 4))

    def test_old_journal_without_the_field_loads(self):
        old = resolve_intent_defaults(FULL)
        old.pop("target_bracket", None)
        old["_journal"] = {"created_at": "2026-09-14T00:00:00+00:00", "updated_at": "2026-09-14T00:00:00+00:00"}
        self.r.set(journal.KEY_PREFIX + "archidekt:417707", json.dumps(old))
        self.assertIsNone(journal.public(journal.load_journal(self.r, DECK))["target_bracket"])
        self.assertIsNone(journal.journal_view(self.r, DECK)["journal"]["target_bracket"])
        rec = journal.save_journal(self.r, DECK, {"novelties": "still loads"})
        self.assertIsNone(rec["target_bracket"])
        self.assertEqual(rec["pet_cards"], FULL["pet_cards"])
        rec = journal.save_journal(self.r, DECK, {"target_bracket": 3})
        self.assertEqual(rec["target_bracket"], 3)
        self.assertEqual(rec["deck_vision"], old["deck_vision"])

    def test_put_merges_and_null_clears(self):
        journal.save_journal(self.r, DECK, dict(FULL, target_bracket=2))
        rec = journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 8}})
        self.assertEqual(rec["target_bracket"], 2)
        rec = journal.save_journal(self.r, DECK, {"target_bracket": None})
        self.assertIsNone(rec["target_bracket"])
        self.assertEqual(rec["target_power_level"]["scale"], 8)

    def _rows(self, payload):
        rec = resolve_intent_defaults(payload)
        rows = journal.build_rows(rec, ctx(), {}, journal.effective_caps(self.r, rec), DECK)
        return {row["field"]: row for row in rows}, rows

    def test_appears_in_checkin_rows(self):
        by, rows = self._rows(dict(FULL, target_bracket=3))
        self.assertEqual([r["field"] for r in rows][2:4], ["power", "bracket"])
        self.assertEqual(by["bracket"]["label"], "Target bracket")
        self.assertEqual(by["bracket"]["value"], "Bracket 3 · Upgraded")
        self.assertEqual(by["bracket"]["sections"], ["target_bracket"])
        self.assertTrue(by["bracket"]["editable"])
        self.assertIsNone(by["bracket"]["evidence"])  # no evaluation in BR-2
        self.assertEqual(by["power"]["value"], "7 · optimized")  # power row untouched
        by, _ = self._rows(FULL)
        self.assertEqual(by["bracket"]["value"], "Not set (no bracket checks).")

    def test_checkin_change_and_undo(self):
        journal.save_journal(self.r, DECK, dict(FULL, target_bracket=2))
        journal.start_review(self.r, DECK, ctx())
        view = journal.answer_row(self.r, DECK, "bracket", "changed", {"target_bracket": 4})
        row = next(r for r in view["rows"] if r["field"] == "bracket")
        self.assertEqual((row["answer"], row["value"], row["was"]),
                         ("changed", "Bracket 4 · Optimized", "was Bracket 2 · Core"))
        self.assertEqual(journal.load_journal(self.r, DECK)["target_bracket"], 4)
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "bracket", "changed", {"target_power_level": {"scale": 3}})
        with self.assertRaises(journal.JournalError):
            journal.answer_row(self.r, DECK, "bracket", "changed", {"target_bracket": 6})
        journal.answer_row(self.r, DECK, "bracket", None)
        self.assertEqual(journal.load_journal(self.r, DECK)["target_bracket"], 2)

    def test_undo_restores_unset(self):
        journal.save_journal(self.r, DECK, FULL)
        journal.start_review(self.r, DECK, ctx())
        journal.answer_row(self.r, DECK, "bracket", "changed", {"target_bracket": 1})
        journal.answer_row(self.r, DECK, "bracket", None)
        self.assertIsNone(journal.load_journal(self.r, DECK)["target_bracket"])


class FakeFetch:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def __call__(self, names):
        self.calls.append(list(names))
        return {n: self.data.get(n) for n in names}


class TestPrices(unittest.TestCase):
    def test_fetches_missing_then_caches(self):
        r = FakeRedis()
        f = FakeFetch({"Sol Ring": 2.5, "Necropotence": 31.0})
        out = prices.card_prices(r, ["Sol Ring", "Necropotence", "Unknown Card"], fetch=f, now=1000)
        self.assertEqual(out, {"Sol Ring": 2.5, "Necropotence": 31.0, "Unknown Card": None})
        out = prices.card_prices(r, ["Sol Ring", "Necropotence", "Unknown Card"], fetch=f, now=2000)
        self.assertEqual(len(f.calls), 1)
        self.assertEqual(out["Sol Ring"], 2.5)
        prices.card_prices(r, ["Sol Ring"], fetch=f, now=1000 + prices.MAX_AGE + 1)
        self.assertEqual(f.calls[-1], ["Sol Ring"])

    def test_fetch_failure_falls_back_to_cache(self):
        r = FakeRedis()
        prices.card_prices(r, ["Sol Ring"], fetch=FakeFetch({"Sol Ring": 2.5}), now=0)

        def boom(names):
            raise RuntimeError("scryfall down")
        out = prices.card_prices(r, ["Sol Ring", "Necropotence"], fetch=boom, now=prices.MAX_AGE * 2)
        self.assertEqual(out, {"Sol Ring": 2.5, "Necropotence": None})

    def test_deck_total_counts_quantity(self):
        tot = prices.deck_total(DECK, {"Sol Ring": 2.0, "Plains": 0.1, "Necropotence": None})
        self.assertEqual(tot["price"], 2.5)
        self.assertEqual(tot["unpriced"], 3)  # Kaalia, Platinum Angel, Necropotence

    def test_scryfall_usd_picks_nonfoil_then_foil(self):
        self.assertEqual(prices.usd_of({"prices": {"usd": "1.25", "usd_foil": "3"}}), 1.25)
        self.assertEqual(prices.usd_of({"prices": {"usd": None, "usd_foil": "3"}}), 3.0)
        self.assertIsNone(prices.usd_of({"prices": {}}))

    def test_unpriced_default_printing_falls_back_to_cheapest_priced_print(self):
        class Resp:
            def __init__(self, data, code=200):
                self.data, self.status_code = data, code

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        class Session:
            def __init__(self):
                self.searches = []

            def post(self, url, **kw):
                return Resp({"data": [{"name": "Sol Ring", "prices": {"usd": "2.00"}},
                                      {"name": "Blood Crypt", "prices": {"usd": None}}]})

            def get(self, url, params=None, **kw):
                self.searches.append(params["q"])
                return Resp({"data": [{"name": "Blood Crypt", "prices": {"usd": None, "usd_foil": None}},
                                      {"name": "Blood Crypt", "prices": {"usd": "17.50"}}]})

        s = Session()
        with patch("analytics.prices.time.sleep"):
            out = prices.scryfall_fetch(["Sol Ring", "Blood Crypt"], session=s)
        self.assertEqual(out, {"Sol Ring": 2.0, "Blood Crypt": 17.5})
        self.assertEqual(s.searches, ['!"Blood Crypt" (usd>0 or usdfoil>0)'])


class TestJournalRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import app
        import app as app_mod
        self.app_mod = app_mod
        self.client = TestClient(app)
        self.fake = FakeRedis()
        self.patches = [patch.object(app_mod, "r", self.fake),
                        patch("app.registry.find_deck", return_value=DECK),
                        patch("app.registry.list_decks", return_value=[DECK]),
                        patch("app.journal_mod.collect_context", side_effect=lambda r, deck, rec=None: ctx())]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_unknown_deck_404(self):
        with patch("app.registry.find_deck", return_value=None), patch("app.registry.list_decks", return_value=[]):
            self.assertEqual(self.client.get("/api/decks/nope/journal").status_code, 404)
            self.assertEqual(self.client.post("/api/decks/nope/checkin").status_code, 404)

    def test_get_put_journal(self):
        body = self.client.get("/api/decks/archidekt:417707/journal").json()
        self.assertFalse(body["exists"])
        self.assertEqual(body["deck_cards"], ["Kaalia of the Vast", "Necropotence", "Plains", "Platinum Angel",
                                              "Sol Ring"])
        self.assertEqual(body["metrics"]["power"], 6.5)
        res = self.client.put("/api/decks/archidekt:417707/journal", json=FULL)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["journal"]["pet_cards"], FULL["pet_cards"])
        body = self.client.get("/api/decks/Holy Hellfire/journal").json()
        self.assertTrue(body["exists"])
        self.assertEqual(body["caps"]["hard_source"], "deck")
        self.assertNotIn("_journal", body["journal"])

    def test_put_invalid_is_400_with_errors(self):
        res = self.client.put("/api/decks/archidekt:417707/journal",
                              json={"budget_constraints": {"hard_cap": 1, "soft_cap": 2}})
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json()["errors"])
        self.assertEqual(self.client.put("/api/decks/archidekt:417707/journal", content="[1]",
                                         headers={"Content-Type": "application/json"}).status_code, 400)

    def test_defaults_routes(self):
        self.assertEqual(self.client.get("/api/journal/defaults").json()["defaults"], journal.DEFAULT_CAPS)
        self.assertEqual(self.client.put("/api/journal/defaults", json={"hard_cap": 50, "soft_cap": 60}).status_code, 400)
        self.assertEqual(self.client.put("/api/journal/defaults", json={"hard_cap": 50, "soft_cap": 20}).status_code, 200)

    def test_target_bracket_round_trips_through_the_api(self):
        self.assertIsNone(self.client.get("/api/decks/archidekt:417707/journal").json()["journal"]["target_bracket"])
        res = self.client.put("/api/decks/archidekt:417707/journal", json=dict(FULL, target_bracket=3))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["journal"]["target_bracket"], 3)
        body = self.client.get("/api/decks/Holy Hellfire/journal").json()
        self.assertEqual(body["journal"]["target_bracket"], 3)
        self.assertEqual(body["journal"]["target_power_level"]["scale"], 7)
        for bad in (0, 6, "x"):
            with self.subTest(bad=bad):
                res = self.client.put("/api/decks/archidekt:417707/journal", json={"target_bracket": bad})
                self.assertEqual(res.status_code, 400)
                self.assertTrue(any("target_bracket" in e for e in res.json()["errors"]))
        self.assertEqual(self.client.get("/api/decks/archidekt:417707/journal").json()["journal"]["target_bracket"], 3)
        res = self.client.put("/api/decks/archidekt:417707/journal", json={"target_bracket": None})
        self.assertIsNone(res.json()["journal"]["target_bracket"])
        # The journal form's PUT (no target_bracket key) keeps a stored bracket: PUT merges sections.
        self.client.put("/api/decks/archidekt:417707/journal", json={"target_bracket": 2})
        form_save = {k: v for k, v in FULL.items()}
        res = self.client.put("/api/decks/archidekt:417707/journal", json=form_save)
        self.assertEqual(res.json()["journal"]["target_bracket"], 2)
        view = self.client.post("/api/decks/archidekt:417707/checkin").json()["checkin"]
        self.assertIn("bracket", [r["field"] for r in view["rows"]])
        res = self.client.patch("/api/decks/archidekt:417707/checkin/rows/bracket",
                                json={"answer": "changed", "journal": {"target_bracket": 5}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get("/api/decks/archidekt:417707/journal").json()["journal"]["target_bracket"], 5)

    def test_checkin_flow(self):
        self.assertEqual(self.client.post("/api/decks/archidekt:417707/checkin").status_code, 409)  # no journal yet
        self.client.put("/api/decks/archidekt:417707/journal", json=FULL)
        self.assertIsNone(self.client.get("/api/decks/archidekt:417707/checkin").json()["checkin"])
        view = self.client.post("/api/decks/archidekt:417707/checkin").json()["checkin"]
        self.assertEqual(len(view["rows"]), 9)
        res = self.client.patch("/api/decks/archidekt:417707/checkin/rows/power",
                                json={"answer": "changed", "journal": {"target_power_level": {"scale": 8}}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["checkin"]["progress"]["changed"], 1)
        self.assertEqual(self.client.patch("/api/decks/archidekt:417707/checkin/rows/nope",
                                           json={"answer": "true"}).status_code, 400)
        self.assertIsNotNone(self.client.get("/api/decks/archidekt:417707/checkin").json()["checkin"])
        res = self.client.post("/api/decks/archidekt:417707/checkin/finish", json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["next"], "/deck/Holy%20Hellfire/tune")
        self.assertEqual(self.client.post("/api/decks/archidekt:417707/checkin/finish", json={}).status_code, 409)
        self.assertEqual(self.client.patch("/api/decks/archidekt:417707/checkin/rows/vision",
                                           json={"answer": "true"}).status_code, 409)
        hist = self.client.get("/api/decks/archidekt:417707/reviews").json()["reviews"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["answers"]["power"]["answer"], "changed")


if __name__ == "__main__":
    unittest.main()
