"""
Card budget rules (Oracle spec, t_7f650b13): price only matters for a card you'd have to BUY.

    hard cap  buy with price > hard is never an option (== hard is allowed); UI shows a count only
    soft cap  buy priced in (soft, hard] loses 0.05 + 0.10*(price-soft)/(hard-soft) from its ranking score
    owned / in-deck cards are never priced, filtered, penalised or flagged
    effective soft = min(soft, hard); no_budget turns everything off

Run: python -m pytest tests/test_budget_rules.py
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import journal, tuner
from analytics.engine import default_analysis_engine
from analytics.models import CommanderIdentifier, DeckCardEntry, DecklistInput
from analytics.recommendations import CURATED_CARD_CATALOG, RecommendationGenerator
from tests.test_interactions import FakeRedis
from tests.test_tuner import CAPS, NO_CAPS, build, cand, swap
from user_intent import BudgetConstraints, UserDeckIntent

C3010 = {**CAPS, "hard_cap": 30.0, "soft_cap": 10.0}


class TestSoftPenalty(unittest.TestCase):
    def test_worked_examples(self):
        p = journal.soft_penalty
        self.assertEqual(p(10.0, 10, 30), 0.0)  # at the soft cap: no penalty
        self.assertAlmostEqual(p(12.0, 10, 30), 0.06)
        self.assertAlmostEqual(p(20.0, 10, 30), 0.10)
        self.assertAlmostEqual(p(30.0, 10, 30), 0.15)
        self.assertEqual(p(31.0, 10, 30), 0.0)  # over hard: filtered elsewhere, not penalised
        self.assertEqual(p(None, 10, 30), 0.0)
        self.assertEqual(p(20.0, 10, None), 0.10)


class TestCaps(unittest.TestCase):
    def test_effective_soft_is_min_of_soft_and_hard(self):
        r = FakeRedis()  # global defaults $30 hard / $10 soft
        caps = journal.effective_caps(r, {"budget_constraints": {"hard_cap": 5}})
        self.assertEqual((caps["hard_cap"], caps["soft_cap"]), (5.0, 5.0))

    def test_no_budget_turns_both_off(self):
        caps = journal.effective_caps(FakeRedis(), {"budget_constraints": {"no_budget": True}})
        self.assertEqual((caps["hard_cap"], caps["soft_cap"]), (None, None))


class TestJournalBudgetRow(unittest.TestCase):
    def test_500_dollar_owned_card_in_deck_raises_nothing(self):
        deck = {"cards": [{"name": "Black Lotus", "quantity": 1}]}
        from user_intent import resolve_intent_defaults
        rec = resolve_intent_defaults({"budget_constraints": {"total_budget": 100, "hard_cap": 30, "soft_cap": 10}})
        ctx = {"price": 500.0, "prices": {"Black Lotus": 500.0}}
        caps = journal.effective_caps(FakeRedis(), rec)
        ev = journal._evidence("budget", rec, ctx, caps, deck)
        self.assertIsNone(ev)


class TestTunePricing(unittest.TestCase):
    def test_cuts_and_owned_options_are_price_blind(self):
        def run(list_price):
            out = build([cand("Owned Ok", 4, 0.75, 0.35, owned=True, available=1, price=list_price)], caps=C3010)
            s = swap(out, "Weak Plan")
            return (s["own"]["name"], s["own"]["score"], s["verdict"], s["hidden_hard"], s["cut"]["score"],
                    [(x["cut"]["name"], x["cut"]["score"]) for x in out["swaps"]])
        self.assertEqual(run(1.0), run(500.0))
        self.assertEqual(run(500.0)[3], 0)  # a $500 owned card is never hidden by the hard cap

    def test_hard_cap_boundary_and_no_names_in_ui(self):
        at = swap(build([cand("At Cap", 4, 0.9, 0.45, price=30.0)], caps=C3010), "Weak Plan")
        self.assertEqual(at["buy"]["name"], "At Cap")
        over = swap(build([cand("Over Cap", 4, 0.9, 0.45, price=30.01)], caps=C3010), "Weak Plan")
        self.assertIsNone(over)  # nothing else to offer, so no swap row at all
        both = swap(build([cand("Owned Ok", 3, 0.6, 0.2, owned=True, available=1),
                           cand("Over Cap", 4, 0.95, 0.5, price=30.01)], caps=C3010), "Weak Plan")
        self.assertIsNone(both["buy"])
        self.assertEqual(both["hidden_hard"], 1)
        # The page renders the count only: the template must not read the hidden card names or prices.
        html = Path(__file__).resolve().parent.parent.joinpath("templates/deck_tune.html").read_text()
        fn = html.split("function hiddenNote(s)", 1)[1].split("function swapRow", 1)[0]
        self.assertIn("hidden_hard", fn)
        self.assertNotIn("hidden_hard_cards", fn)
        self.assertNotIn(".name", fn)

    def test_soft_penalty_lets_the_cheaper_slightly_weaker_buy_win(self):
        a = cand("Pricey A", 4, 0.5, 0.30, price=25.0)   # score 0.70, penalty 0.125 -> 0.575
        b = cand("Cheap B", 4, 0.5, 0.18, price=4.0)     # score 0.62, no penalty
        out = build([a, b], caps=C3010)
        s = swap(out, "Weak Plan")
        self.assertEqual(s["buy"]["name"], "Cheap B")
        self.assertEqual(s["buy"]["score"], 0.62)  # raw score is displayed
        # sanity: without the soft cap, A wins
        s2 = swap(build([a, b], caps={**C3010, "soft_cap": 30.0}), "Weak Plan")
        self.assertEqual(s2["buy"]["name"], "Pricey A")

    def test_soft_buy_without_owned_option_must_clear_min_upgrade_on_adjusted_score(self):
        # Weak Plan scores 0.08. Raw 0.20 clears MIN_UPGRADE (0.13); at $25 it loses 0.125 -> 0.075.
        raw = cand("Meh", 0, 0.3, 0.15, price=25.0)
        self.assertIsNone(swap(build([raw], caps=C3010), "Weak Plan"))
        cheap = cand("Meh", 0, 0.3, 0.15, price=5.0)
        self.assertEqual(swap(build([cheap], caps=C3010), "Weak Plan")["buy"]["name"], "Meh")

    def test_owned_elsewhere_with_no_fair_steal_is_only_a_priced_second_copy(self):
        def run(price, **kw):
            c = cand("Sleeved", 4, 0.9, 0.45, owned=True, available=0, decks=["Other Deck"], price=price)
            return swap(build([c], caps=C3010, **kw), "Weak Plan")
        got = run(5.0)
        self.assertEqual((got["own"], got["buy"]["name"], got["buy"]["source"]), (None, "Sleeved", "buy"))
        self.assertEqual(got["buy"]["price"], 5.0)
        self.assertIsNone(run(95.0))               # a 2nd copy over the hard cap is filtered like any buy
        self.assertIsNone(run(None))               # unpriced -> can't be a buy
        self.assertIsNone(run(5.0, buy=False))     # buys switched off

    def test_no_budget_means_no_filter_no_penalty_no_flag(self):
        out = build([cand("Pricey", 4, 0.9, 0.45, price=500.0)], caps=NO_CAPS)
        s = swap(out, "Weak Plan")
        self.assertEqual(s["buy"]["name"], "Pricey")
        self.assertEqual((s["buy"]["cap"], s["buy"]["penalty"], s["hidden_hard"], s["soft_note"]),
                         (None, 0.0, 0, None))
        self.assertEqual(s["buy"]["adj_score"], s["buy"]["score"])


def _deck(*names):
    return DecklistInput(
        deck_id="t:budget", name="Budget Test", format="commander",
        commanders=[CommanderIdentifier(name="Atraxa, Praetors' Voice", oracle_id=None, scryfall_id=None)],
        cards=[DeckCardEntry(name=n, quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard") for n in names])


class TestLegacyDeckReview(unittest.TestCase):
    def setUp(self):
        self.gen = RecommendationGenerator()
        self.deck = _deck("Sol Ring", "Plains")
        self.analysis = default_analysis_engine.analyze(self.deck)
        self.intent = UserDeckIntent(budget_constraints=BudgetConstraints(max_card_price=5.0))
        self.base = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent).additions]

    def test_no_hard_coded_catalog_price_is_read(self):
        # Catalog prices are stale placeholders. Without live pricing nothing is price-filtered.
        self.assertTrue(any(c.approx_price_usd > 5.0 for c in CURATED_CARD_CATALOG if c.name in self.base))
        expensive = next(c for c in CURATED_CARD_CATALOG if c.approx_price_usd > 5.0 and c.name in self.base)
        saved = expensive.approx_price_usd
        try:
            object.__setattr__(expensive, "approx_price_usd", 0.01)
            again = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent).additions]
        finally:
            object.__setattr__(expensive, "approx_price_usd", saved)
        self.assertEqual(self.base, again)

    def _pricing(self, prices, owned=(), caps=None):
        return {"prices": prices, "owned": set(owned),
                "caps": caps or {"hard_cap": 5.0, "soft_cap": 2.0, "no_budget": False}}

    def test_owned_card_above_the_cap_is_still_recommended_and_unpriced_buy_is_dropped(self):
        top = self.base[:3]
        self.assertEqual(len(top), 3)
        owned_name, priced_name, unpriced_name = top
        prices = {n: 1.0 for n in self.base}
        prices[owned_name] = 500.0           # owned, way over the hard cap: untouched
        prices[priced_name] = 6.0            # a buy over the hard cap: dropped
        prices.pop(unpriced_name, None)      # a buy with no price: dropped
        prices[unpriced_name] = None
        pricing = self._pricing(prices, owned=[owned_name])
        got = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent, pricing=pricing).additions]
        self.assertIn(owned_name, got)
        self.assertNotIn(priced_name, got)
        self.assertNotIn(unpriced_name, got)

    def test_soft_cap_lowers_a_buys_score_but_not_an_owned_cards(self):
        name = self.base[0]
        raw = next(a.score for a in self.gen.generate(self.deck, self.analysis, self.intent).additions
                   if a.card_name == name)
        prices = {n: 1.0 for n in self.base}
        prices[name] = 4.0  # soft 2, hard 5 -> penalty 0.05 + 0.10 * 2/3
        buy = {a.card_name: a for a in self.gen.generate(self.deck, self.analysis, self.intent,
                                                        pricing=self._pricing(prices)).additions}
        pen = journal.soft_penalty(4.0, 2.0, 5.0)
        if name in buy:
            self.assertLess(buy[name].score, raw)
        owned = {a.card_name: a for a in self.gen.generate(self.deck, self.analysis, self.intent,
                                                          pricing=self._pricing(prices, owned=[name])).additions}
        self.assertEqual(owned[name].score, raw)
        self.assertAlmostEqual(pen, 0.05 + 0.10 * 2 / 3)

    def test_external_recommendations_are_price_checked_too(self):
        from analytics.models import CardRecommendation, CardRecommendations
        ext = CardRecommendations(items=[CardRecommendation(card_name="Zzz Pricey Staple", score=0.9, reason="x")])
        pricing = self._pricing({"Zzz Pricey Staple": 50.0})
        got = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent,
                                                      external_recommendations=ext, pricing=pricing).additions]
        self.assertNotIn("Zzz Pricey Staple", got)
        pricing["owned"] = {"Zzz Pricey Staple"}
        got = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent,
                                                      external_recommendations=ext, pricing=pricing).additions]
        self.assertIn("Zzz Pricey Staple", got)

    def test_no_budget_disables_filter_and_penalty(self):
        pricing = self._pricing({n: 999.0 for n in self.base}, caps={"hard_cap": None, "soft_cap": None, "no_budget": True})
        got = [a.card_name for a in self.gen.generate(self.deck, self.analysis, self.intent, pricing=pricing).additions]
        self.assertEqual(got, self.base)


class TestPriceBlindSurfaces(unittest.TestCase):
    def test_engine_budget_aligned_never_reads_deck_prices(self):
        src = Path(__file__).resolve().parent.parent.joinpath("analytics/engine.py").read_text()
        self.assertNotRegex(src, r"budget_aligned\s*=\s*[^T\n]*price")


if __name__ == "__main__":
    unittest.main()
