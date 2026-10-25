"""
AN-6: Tuner (ANALYTICS_SPEC Layer 3, UX_SPEC §3.4).

Every number here is deterministic. The LLM only writes the "why" prose.

    score(card)  = mean of the parts that exist, each 0..1, 1 = strong:
                   edges  min(1, plan edges into the deck / p75)    (the weak-performer p75)
                   fit    best pillar fit                            (only when the deck has pillars)
                   edhrec min(1, EDHREC inclusion / 0.50)            (only when EDHREC answered)
    A cut's score uses the same formula, so "gap" and "upgrade" compare like with like.

Owned-first (D5): a buy is shown only when buy.score - best_owned.score >= GAP_WORTH (0.10).
Otherwise the row says "keep yours", with the gap and the price.
Soft cap: a buy priced in (soft, hard] is devalued by 0.05..0.15 (journal.soft_penalty) for
sorting, MIN_UPGRADE and GAP_WORTH; a soft buy that can't clear GAP_WORTH on its adjusted score
becomes "keep yours". Owned / in-deck cards are never priced. Hard cap: never an option, only counted
("1 better buy hidden"). Pets are never cut. Caps come from the Journal (deck override)
or the global default.

Run: python -m pytest tests/test_tuner.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import journal
from analytics import tuner
from analytics.interactions import llm as llm_mod
from tests.test_interactions import FakeRedis

DECK = {"id": 417707, "name": "Holy Hellfire", "registry_id": "archidekt:417707", "color": "WBR",
        "commanders": ["Kaalia of the Vast"],
        "cards": [{"name": n, "quantity": 1} for n in ("Kaalia of the Vast", "Weak Plan", "Weak Removal",
                                                       "Pet Card", "Okay Plan", "Strong Plan", "Sol Ring")]
        + [{"name": "Plains", "quantity": 30}]}

CAPS = {"no_budget": False, "hard_cap": 40.0, "soft_cap": 15.0, "hard_source": "deck", "soft_source": "deck",
        "defaults": {"hard_cap": 30.0, "soft_cap": 10.0}}
NO_CAPS = {"no_budget": True, "hard_cap": None, "soft_cap": None, "hard_source": "none", "soft_source": "none",
           "defaults": {"hard_cap": 30.0, "soft_cap": 10.0}}


def weak_row(name, score, edges, fit, inc, rows=("plan",), pet=False, bucket="generic"):
    return {"name": name, "weak_score": score, "edges": edges, "fit": fit, "edhrec_inclusion": inc,
            "rows": list(rows), "pet": pet, "fit_bucket": bucket, "pillar": None,
            "parts": {"edges": 0, "pillar": 0, "edhrec": 0}, "reasons": []}


WEAK = {
    "cards": [weak_row("Weak Plan", 0.80, 0, 0.20, 0.02), weak_row("Weak Removal", 0.70, 0, 0.30, 0.05,
                                                                   rows=("targeted_disruption",), bucket="glue"),
              weak_row("Okay Plan", 0.35, 2, 0.60, 0.30), weak_row("Pet Card", 0.90, 0, 0.10, 0.0, pet=True)],
    "pet_notice": {"name": "Pet Card", "weak_score": 0.90, "text": "♥ Pet Card · pet · weakest pet card · never cut"},
    "weights": {"p75_degree": 4.0},
    "inputs": {"pillars": True, "edhrec": True},
}

COMP = {"rows": [
    {"key": "lands", "label": "Lands", "actual": 30, "target": 38, "delta": -8, "status": "short"},
    {"key": "ramp", "label": "Ramp", "actual": 1, "target": 10, "delta": -9, "status": "short"},
    {"key": "card_advantage", "label": "Card advantage", "actual": 11, "target": 12, "delta": -1, "status": "ok"},
    {"key": "targeted_disruption", "label": "Targeted disruption", "actual": 1, "target": 12, "delta": -11,
     "status": "short"},
    {"key": "mass_disruption", "label": "Mass disruption", "actual": 6, "target": 6, "delta": 0, "status": "ok"},
    {"key": "plan", "label": "Plan", "actual": 4, "target": 22, "delta": -18, "status": "short"},
]}


def cand(name, edges, fit, inc, owned=False, available=0, decks=(), price=None, rows=("plan",), land=False,
         on_plan=True, pillar="Payoffs"):
    return {"name": name, "edges": edges, "edges_by": {}, "fit": fit, "inclusion": inc, "owned": owned,
            "available": available, "decks": list(decks), "price": price, "rows": list(rows), "land": land,
            "on_plan": on_plan, "pillar": pillar, "sources": ["owned" if owned else "edhrec"]}


def build(cands, caps=CAPS, **kw):
    kw.setdefault("p75", 4.0)
    kw.setdefault("has_pillars", True)
    kw.setdefault("has_edhrec", True)
    return tuner.build_tune(DECK, WEAK, COMP, cands, caps, **kw)


def swap(out, cut):
    return next((s for s in out["swaps"] if s["cut"]["name"] == cut), None)


def inp(edges, fit, inc, rows=("plan",), land=False, color="W", owned=True, available=1, played=None):
    """A card's score inputs in the DONOR deck (AN-8)."""
    return {"edges": edges, "fit": fit, "inclusion": inc, "rows": list(rows), "land": land, "color": color,
            "owned": owned, "available": available, "played": played}


def donor(name="Dawnbringer", colors="WRG", cards=None, cands=None, pets=(), p75=4.0):
    """A hand-built donor-deck context (what DonorBook builds from Redis)."""
    cards = dict(cards or {})
    return {"name": name, "colors": colors, "commanders": ["Dawnbringer Cleric"],
            "in_deck": set(cards) | {"Dawnbringer Cleric"}, "pets": set(pets), "p75": p75, "has_pillars": True,
            "has_edhrec": True, "played_neutral": None, "cards": cards, "cands": dict(cands or {})}


class TestScore(unittest.TestCase):
    def test_score_is_the_mean_of_the_parts_that_exist(self):
        # edges 2/4 = 0.5, fit 0.8, edhrec 0.25/0.5 = 0.5 -> 0.6
        self.assertAlmostEqual(tuner.score(2, 4.0, 0.8, 0.25, True, True), 0.6, places=4)
        # no pillars: only edges + edhrec
        self.assertAlmostEqual(tuner.score(2, 4.0, 0.8, 0.25, False, True), 0.5, places=4)
        # no EDHREC: edges + fit
        self.assertAlmostEqual(tuner.score(2, 4.0, 0.8, None, True, False), 0.65, places=4)
        # everything clamps to 0..1
        self.assertEqual(tuner.score(99, 4.0, 1.7, 0.9, True, True), 1.0)
        self.assertEqual(tuner.score(0, 4.0, None, None, True, True), 0.0)

    def test_constants_match_the_spec(self):
        self.assertEqual(tuner.GAP_WORTH, 0.10)
        self.assertFalse(hasattr(tuner, "GAP_SOFT_OVERRIDE"))


class TestOwnedFirst(unittest.TestCase):
    def test_owned_swap_when_no_buy_exists(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)])
        s = swap(out, "Weak Plan")
        self.assertEqual(s["own"]["name"], "Owned Bomb")
        self.assertEqual(s["own"]["source"], "free")
        self.assertIsNone(s["buy"])
        self.assertEqual(s["pick"], "own")
        self.assertEqual(s["verdict"], "owned")

    def test_buy_shown_and_picked_when_it_clearly_beats_owned(self):
        out = build([cand("Owned Meh", 2, 0.5, 0.1, owned=True, available=1),
                     cand("Buy Bomb", 4, 0.9, 0.5, price=6.40)])
        s = swap(out, "Weak Plan")
        self.assertEqual(s["own"]["name"], "Owned Meh")
        self.assertEqual(s["buy"]["name"], "Buy Bomb")
        self.assertGreaterEqual(s["gap"], tuner.GAP_WORTH)
        self.assertEqual(s["pick"], "buy")
        self.assertEqual(s["verdict"], "worth_buying")
        self.assertIsNone(s["buy"]["cap"])

    def test_keep_yours_when_the_gap_is_small(self):
        out = build([cand("Owned Good", 4, 0.8, 0.4, owned=True, available=1),
                     cand("Buy Slightly Better", 4, 0.85, 0.45, price=40.0)])
        s = swap(out, "Weak Plan")
        self.assertIsNone(s["buy"])                      # D5: not shown as an option
        self.assertEqual(s["pick"], "own")
        self.assertEqual(s["verdict"], "keep_yours")
        near = s["near_buy"]
        self.assertEqual(near["name"], "Buy Slightly Better")
        self.assertEqual(near["price"], 40.0)
        self.assertLess(near["gap"], tuner.GAP_WORTH)
        self.assertGreater(near["gap"], 0)

    def test_owned_elsewhere_is_labelled_and_can_be_turned_off(self):
        c = [cand("Sleeved Elsewhere", 4, 0.9, 0.4, owned=True, available=0, decks=["Dawnbringer"])]
        donors = {"Dawnbringer": donor(cards={"Sleeved Elsewhere": inp(2, 0.5, 0.2)},
                                       cands={"Box Card": inp(2, 0.6, 0.25)})}
        s = swap(build(c, donors=donors), "Weak Plan")
        self.assertEqual(s["own"]["source"], "deck")
        self.assertEqual(s["own"]["deck"], "Dawnbringer")
        self.assertIsNone(swap(build(c, in_deck=False, donors=donors), "Weak Plan"))
        # AN-8: with no donor data the trade can't be checked, so it isn't offered.
        self.assertIsNone(swap(build(c), "Weak Plan"))

    def test_buy_only_when_nothing_owned_fits(self):
        s = swap(build([cand("Buy Bomb", 4, 0.9, 0.5, price=3.0)]), "Weak Plan")
        self.assertIsNone(s["own"])
        self.assertEqual(s["pick"], "buy")
        self.assertEqual(s["verdict"], "buy")

    def test_free_mode_drops_buys(self):
        out = build([cand("Owned Meh", 2, 0.5, 0.1, owned=True, available=1),
                     cand("Buy Bomb", 4, 0.9, 0.5, price=6.40)], mode="free")
        s = swap(out, "Weak Plan")
        self.assertIsNone(s["buy"])
        self.assertIsNone(s["near_buy"])
        self.assertEqual(s["pick"], "own")

    def test_best_card_picks_the_higher_score(self):
        out = build([cand("Owned Good", 4, 0.8, 0.4, owned=True, available=1),
                     cand("Buy Slightly Better", 4, 0.85, 0.45, price=12.0)], mode="best")
        s = swap(out, "Weak Plan")
        self.assertEqual(s["own"]["name"], "Buy Slightly Better")   # one option, ownership ignored
        self.assertIsNone(s["buy"])
        self.assertEqual(s["pick"], "own")

    def test_options_must_beat_the_cut(self):
        # Okay Plan scores well already; nothing here upgrades it.
        out = build([cand("Owned Meh", 1, 0.3, 0.05, owned=True, available=1)])
        self.assertIsNone(swap(out, "Okay Plan"))

    def test_a_card_is_suggested_once(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1,
                          rows=("targeted_disruption",))])
        adds = [s["own"]["name"] for s in out["swaps"] if s["own"]]
        self.assertEqual(adds.count("Owned Bomb"), 1)


def _donors():
    return {"Dawnbringer": donor(cards={"In Deck Card": inp(2, 0.5, 0.2)}, cands={"Box Card": inp(2, 0.6, 0.25)}),
            "Holy Hellfire": donor(name="Holy Hellfire", cards={"In Deck Card": inp(2, 0.5, 0.2)},
                                   cands={"Box Card": inp(2, 0.6, 0.25)})}


def _names(out):
    return {s["cut"]["name"]: (s["own"] or {}).get("name") for s in out["swaps"]}


def _srcs(out):
    return sorted({o["source"] for s in out["swaps"] for o in (s["own"], s["buy"]) if o})


class TestOwnershipModes(unittest.TestCase):
    """Mode 1 free, mode 2 flex (in_deck x buy), mode 3 best."""

    POOL = [cand("Free Card", 3, 0.7, 0.3, owned=True, available=1, rows=("plan",)),
            cand("In Deck Card", 4, 0.95, 0.5, owned=True, available=0, decks=["Dawnbringer"], rows=("ramp",)),
            cand("Buy Card", 4, 0.99, 0.5, price=5.0, rows=("targeted_disruption",))]

    def run_mode(self, mode, **kw):
        return build(self.POOL, mode=mode, donors=_donors(), **kw)

    def sources(self, out):
        return {s["cut"]["name"]: [o["source"] for o in (s["own"], s["buy"]) if o] for s in out["swaps"]}

    def test_legacy_values_map_to_new_modes(self):
        self.assertEqual(tuner.resolve_mode("owned_only")[0], "free")
        self.assertEqual(tuner.resolve_mode("owned_first"), ("flex", True, True))
        self.assertEqual(tuner.resolve_mode("best")[0], "best")
        self.assertEqual(tuner.resolve_mode(None), ("flex", True, True))
        with self.assertRaises(tuner.TunerError):
            tuner.resolve_mode("nope")
        self.assertEqual(self.run_mode("owned_only")["mode"], "free")

    def test_mode1_only_cards_in_no_deck(self):
        out = self.run_mode("free", in_deck=True, buy=True)  # toggles are ignored in mode 1
        self.assertEqual(out["mode"], "free")
        self.assertEqual(_srcs(out), ["free"])
        self.assertNotIn("In Deck Card", json.dumps(out["swaps"]))
        self.assertNotIn("Buy Card", json.dumps(out["swaps"]))

    def test_mode2_toggle_combinations(self):
        cases = {  # (in_deck, buy) -> sources that may appear
            (False, False): {"free"},
            (True, False): {"free", "deck"},
            (False, True): {"free", "buy"},
            (True, True): {"free", "deck", "buy"},
        }
        for (in_deck, buy), allowed in cases.items():
            out = self.run_mode("flex", in_deck=in_deck, buy=buy)
            got = set(_srcs(out))
            self.assertIn("free", got, (in_deck, buy))               # free cards always included
            self.assertEqual(got, allowed, (in_deck, buy))
            body = json.dumps(out["swaps"])
            self.assertEqual("In Deck Card" in body, in_deck, (in_deck, buy))
            self.assertEqual("Buy Card" in body, buy, (in_deck, buy))
            self.assertEqual((out["in_deck"], out["buy"]), (in_deck, buy))

    def test_mode2_in_deck_option_names_its_deck(self):
        out = self.run_mode("flex", in_deck=True, buy=False)
        o = next(s["own"] for s in out["swaps"] if s["own"]["source"] == "deck")
        self.assertEqual((o["name"], o["deck"]), ("In Deck Card", "Dawnbringer"))

    def test_mode2_excludes_the_deck_being_tuned(self):
        # Only "owned" in the deck we're tuning (stale inventory): not a source.
        c = [cand("Stale", 4, 0.9, 0.4, owned=True, available=0, decks=["Holy Hellfire"])]
        self.assertEqual(build(c, mode="flex", donors=_donors())["swaps"], [])
        # Also in another deck: only that deck is named / considered.
        c = [cand("Both", 4, 0.9, 0.4, owned=True, available=0, decks=["Holy Hellfire", "Dawnbringer"])]
        c_donors = {"Dawnbringer": donor(cards={"Both": inp(2, 0.5, 0.2)}, cands={"Box Card": inp(2, 0.6, 0.25)})}
        o = swap(build(c, mode="flex", donors=c_donors), "Weak Plan")["own"]
        self.assertEqual(o["deck"], "Dawnbringer")

    def test_mode3_ignores_ownership_and_never_mentions_other_decks(self):
        out = self.run_mode("best")
        self.assertEqual(out["mode"], "best")
        names = {s["own"]["name"] for s in out["swaps"]}
        self.assertTrue({"In Deck Card", "Buy Card"} & names)       # strongest wins whatever the owner
        body = json.dumps({k: out[k] for k in ("swaps", "fixes", "counts")})
        for bad in ("Dawnbringer", "steal", "donor", "sleeved", '"deck"', "another deck"):
            self.assertNotIn(bad, body, bad)
        for s in out["swaps"]:
            self.assertIsNone(s["buy"])
            self.assertEqual(s["verdict"], "best")
            self.assertNotIn("steal_line", s["own"])
        for f in out["fixes"]:
            self.assertNotIn("elsewhere", f["advice"])
            self.assertNotIn("another deck", f["advice"])

    def test_mode3_still_charges_spend_for_unowned_picks(self):
        out = build([cand("Buy Card", 4, 0.99, 0.5, price=5.0)], mode="best")
        o = swap(out, "Weak Plan")["own"]
        self.assertEqual((o["source"], o["price"]), ("buy", 5.0))

    def test_unknown_mode_rejected(self):
        with self.assertRaises(tuner.TunerError):
            build(self.POOL, mode="owned_never")


class TestPriceCaps(unittest.TestCase):
    def test_soft_cap_buy_that_misses_gap_worth_after_penalty_is_keep_yours(self):
        # raw gap 0.117 >= GAP_WORTH, but the $18.50 penalty (0.064) drops it to 0.052.
        out = build([cand("Owned Ok", 4, 0.75, 0.35, owned=True, available=1),
                     cand("Soft Buy", 4, 0.9, 0.45, price=18.50)])
        s = swap(out, "Weak Plan")
        self.assertIsNone(s["buy"])
        self.assertEqual(s["pick"], "own")
        self.assertEqual(s["verdict"], "keep_yours")
        self.assertEqual(s["near_buy"]["name"], "Soft Buy")
        self.assertEqual(s["near_buy"]["cap"], "soft")
        self.assertGreater(s["near_buy"]["gap"], tuner.GAP_WORTH)  # the raw gap is what's displayed

    def test_soft_cap_buy_is_picked_when_the_adjusted_gap_is_big(self):
        out = build([cand("Owned Weak", 1, 0.4, 0.05, owned=True, available=1),
                     cand("Soft Buy", 4, 0.95, 0.5, price=18.50)])
        s = swap(out, "Weak Plan")
        self.assertEqual(s["pick"], "buy")
        self.assertEqual(s["verdict"], "worth_buying")
        self.assertEqual(s["buy"]["cap"], "soft")
        self.assertAlmostEqual(s["buy"]["penalty"], 0.05 + 0.10 * 3.5 / 25, places=4)
        self.assertAlmostEqual(s["buy"]["adj_score"], s["buy"]["score"] - s["buy"]["penalty"], places=4)
        # The justification's numbers are computed, not written by the LLM.
        self.assertIn("$18.50", s["soft_note"])
        self.assertIn("Owned Weak", s["soft_note"])
        self.assertIn(f"{s['gap']:.2f}", s["soft_note"])
        self.assertIn("$15", s["soft_note"])
        self.assertIn(f"+{tuner.GAP_WORTH + s['buy']['penalty']:.2f}", s["soft_note"])

    def test_hard_cap_is_never_suggested_only_counted(self):
        out = build([cand("Owned Ok", 3, 0.6, 0.2, owned=True, available=1),
                     cand("Pricey Bomb", 4, 0.95, 0.5, price=95.0)])
        s = swap(out, "Weak Plan")
        self.assertIsNone(s["buy"])
        self.assertIsNone(s["near_buy"])
        self.assertEqual(s["hidden_hard"], 1)
        # never offered as a buy/own option, only listed under hidden_hard_cards
        self.assertNotIn("Pricey Bomb", json.dumps([{k: v for k, v in x.items() if k != "hidden_hard_cards"}
                                                    for x in out["swaps"]]))
        self.assertEqual([c["name"] for c in s["hidden_hard_cards"]], ["Pricey Bomb"])
        self.assertEqual(s["hidden_hard_cards"][0]["price"], 95.0)
        self.assertEqual(s["hidden_hard_cards"][0]["reason"], "over hard cap")
        self.assertEqual(out["caps"]["hard_cap"], 40.0)

    def test_hidden_list_is_capped_but_count_is_full(self):
        cs = [cand("Owned Ok", 3, 0.6, 0.2, owned=True, available=1)]
        n = tuner.HIDDEN_LIST_MAX + 3
        cs += [cand(f"Pricey {i:02d}", 4, 0.95, 0.5, price=95.0) for i in range(n)]
        s = swap(build(cs), "Weak Plan")
        self.assertEqual(s["hidden_hard"], n)
        self.assertEqual(len(s["hidden_hard_cards"]), tuner.HIDDEN_LIST_MAX)

    def test_no_hidden_cards_gives_empty_list(self):
        s = swap(build([cand("Owned Ok", 3, 0.6, 0.2, owned=True, available=1)]), "Weak Plan")
        self.assertEqual((s["hidden_hard"], s["hidden_hard_cards"]), (0, []))

    def test_unpriced_cards_are_never_buys(self):
        s = swap(build([cand("No Price", 4, 0.95, 0.5, price=None)]), "Weak Plan")
        self.assertIsNone(s)

    def test_no_budget_means_no_caps(self):
        out = build([cand("Owned Ok", 3, 0.6, 0.2, owned=True, available=1),
                     cand("Pricey Bomb", 4, 0.95, 0.5, price=95.0)], caps=NO_CAPS)
        s = swap(out, "Weak Plan")
        self.assertEqual(s["buy"]["name"], "Pricey Bomb")
        self.assertIsNone(s["buy"]["cap"])
        self.assertEqual(s["hidden_hard"], 0)

    def test_caps_come_from_the_journal_or_the_global_default(self):
        r = FakeRedis()
        self.assertEqual(tuner.caps_for(r, DECK)["hard_source"], "global")
        self.assertEqual(tuner.caps_for(r, DECK)["hard_cap"], journal.DEFAULT_CAPS["hard_cap"])
        journal.set_defaults(r, {"hard_cap": 25, "soft_cap": 5})
        self.assertEqual(tuner.caps_for(r, DECK)["hard_cap"], 25.0)
        journal.save_journal(r, DECK, {"budget_constraints": {"hard_cap": 40, "soft_cap": 15}})
        caps = tuner.caps_for(r, DECK)
        self.assertEqual((caps["hard_cap"], caps["soft_cap"], caps["hard_source"]), (40.0, 15.0, "deck"))


class TestStealRule(unittest.TestCase):
    """AN-8: a card sleeved in deck B is offered to deck A only with a
    replacement Y for B (a free owned card, A's cut card, or a basic) that
    leaves B's score >= before. Scores use B's own inputs:
    score = mean(edges / p75, fit, inclusion / 0.50), p75 = 4 here."""

    X = "Sleeved Bomb"

    def steal_cand(self, decks=("Dawnbringer",), **kw):
        return cand(self.X, 4, 0.9, 0.4, owned=True, available=0, decks=decks, **kw)

    def test_a_free_agent_replacement_is_shown(self):
        # B: X = (2/4 + 0.5 + 0.2/0.5)/3 = 0.4667; free Y = (0.5 + 0.6 + 0.5)/3 = 0.5333.
        d = donor(cards={self.X: inp(2, 0.5, 0.2)}, cands={"Box Card": inp(2, 0.6, 0.25),
                                                           "Worse Box Card": inp(0, 0.2, 0.0)})
        out = build([self.steal_cand()], donors={"Dawnbringer": d})
        own = swap(out, "Weak Plan")["own"]
        self.assertEqual((own["name"], own["source"], own["deck"]), (self.X, "deck", "Dawnbringer"))
        self.assertEqual(own["donor_deck"], "Dawnbringer")
        self.assertEqual(own["donor_replacement"], "Box Card")          # the best valid Y
        self.assertEqual(own["donor_replacement_source"], "free")
        self.assertAlmostEqual(own["donor_delta"], 0.0667, places=3)
        cut_score = swap(out, "Weak Plan")["cut"]["score"]
        self.assertAlmostEqual(own["receiver_delta"], round(own["score"] - cut_score, 4), places=4)
        self.assertGreater(own["receiver_delta"], 0)
        self.assertEqual(own["steal_line"], "Take Sleeved Bomb from Dawnbringer \u2192 Dawnbringer gets Box Card (+0.07)")
        self.assertEqual(out["counts"]["steals"], 1)

    def test_the_cut_card_is_a_replacement_when_b_is_no_worse(self):
        # Only Y is A's cut (Weak Plan, in deck A so not free): same score in B -> a fair 1-for-1, shown.
        d = donor(cards={self.X: inp(2, 0.5, 0.2)},
                  cands={"Weak Plan": inp(2, 0.5, 0.2, available=0), "Worse Box Card": inp(0, 0.2, 0.0)})
        own = swap(build([self.steal_cand()], donors={"Dawnbringer": d}), "Weak Plan")["own"]
        self.assertEqual(own["donor_replacement"], "Weak Plan")
        self.assertEqual(own["donor_replacement_source"], "cut")
        self.assertEqual(own["donor_delta"], 0.0)
        self.assertIn("(+0.00)", own["steal_line"])
        # A's cut is only a Y for the swap that cuts it: a card in A that isn't the cut is never a Y.
        self.assertIsNone(tuner.donor_trade(self.X, d, cut="Okay Plan"))

    def test_a_basic_land_replaces_a_land(self):
        # A is short on lands; B has the dual. B gets a basic in its colours (Plains), never an off-colour one.
        d = donor(cards={"Sleeved Dual": inp(0, None, 0.1, rows=("lands",), land=True)},
                  cands={"Island": inp(0, None, 0.5, land=True), "Plains": inp(0, None, 0.1, land=True),
                         "Box Spell": inp(4, 0.9, 0.5)})            # a spell never replaces a land
        dual = cand("Sleeved Dual", 0, None, 0.4, owned=True, available=0, decks=["Dawnbringer"], rows=("lands",),
                    land=True)
        out = build([dual], donors={"Dawnbringer": d})
        lands = next(f for f in out["fixes"] if f["key"] == "lands")
        self.assertEqual(lands["owned"], ["Sleeved Dual"])
        st = lands["steals"][0]
        self.assertEqual((st["donor_replacement"], st["donor_replacement_source"]), ("Plains", "basic"))
        self.assertEqual(st["steal_line"], "Take Sleeved Dual from Dawnbringer \u2192 Dawnbringer gets Plains (+0.00)")
        self.assertIn("Sleeved Dual (in Dawnbringer; it gets Plains, +0.00)", lands["advice"])

    def test_no_swap_when_every_replacement_makes_b_worse(self):
        d = donor(cards={self.X: inp(4, 0.8, 0.4)},
                  cands={"Box Card": inp(1, 0.4, 0.1), "Weak Plan": inp(0, 0.3, 0.0, available=0),
                         "Plains": inp(0, None, 0.0, land=True)})
        donors = {"Dawnbringer": d}
        self.assertIsNone(swap(build([self.steal_cand()], donors=donors), "Weak Plan"))
        # The next-best option takes its place: a free owned card, else a buy.
        out = build([self.steal_cand(), cand("Owned Meh", 3, 0.6, 0.2, owned=True, available=1)], donors=donors)
        own = swap(out, "Weak Plan")["own"]
        self.assertEqual((own["name"], own["source"]), ("Owned Meh", "free"))
        self.assertNotIn("donor_deck", own)
        self.assertEqual(out["counts"]["steals_dropped"], 1)
        s = swap(build([self.steal_cand(), cand("Buy Bomb", 4, 0.9, 0.5, price=3.0)], donors=donors), "Weak Plan")
        self.assertIsNone(s["own"])
        self.assertEqual(s["buy"]["name"], "Buy Bomb")
        # Nor is it listed as "sleeved elsewhere" in a shape fix.
        ramp = cand(self.X, 4, 0.9, 0.4, owned=True, available=0, decks=["Dawnbringer"], rows=("ramp",))
        fix = next(f for f in build([ramp], donors=donors)["fixes"] if f["key"] == "ramp")
        self.assertEqual((fix["owned"], fix["steals"], fix["action"]), ([], [], None))
        self.assertNotIn(self.X, fix["advice"])

    def test_replacement_must_be_legal_in_b(self):
        d = donor(colors="WRG", cards={self.X: inp(2, 0.5, 0.2), "Already Here": inp(1, 0.2, 0.0)},
                  cands={"Blue Card": inp(4, 0.9, 0.5, color="U"), "Already Here": inp(4, 0.9, 0.5),
                         "Swamp": inp(4, 0.9, 0.5, land=True)})
        self.assertIsNone(tuner.donor_trade(self.X, d))                  # off-identity, a duplicate, off-colour basic
        self.assertIsNone(swap(build([self.steal_cand()], donors={"Dawnbringer": d}), "Weak Plan"))
        d["cands"]["Green Card"] = inp(2, 0.6, 0.25, color="G")
        self.assertEqual(tuner.donor_trade(self.X, d)["donor_replacement"], "Green Card")
        # A cut card outside B's colours is rejected too.
        d2 = donor(colors="WRG", cards={self.X: inp(2, 0.5, 0.2)},
                   cands={"Weak Plan": inp(4, 0.9, 0.5, color="B", available=0)})
        self.assertIsNone(tuner.donor_trade(self.X, d2, cut="Weak Plan"))

    def test_a_pet_in_b_is_never_stolen(self):
        d = donor(cards={self.X: inp(0, 0.1, 0.0)}, cands={"Box Card": inp(4, 0.9, 0.5)}, pets=[self.X])
        self.assertIsNone(tuner.donor_trade(self.X, d))
        out = build([self.steal_cand()], donors={"Dawnbringer": d})
        self.assertIsNone(swap(out, "Weak Plan"))
        self.assertNotIn(self.X, json.dumps(out["swaps"]))
        # ... nor B's commander.
        d["pets"] = set()
        d["commanders"] = [self.X]
        self.assertIsNone(tuner.donor_trade(self.X, d))

    def test_y_keeps_xs_role_in_b(self):
        d = donor(cards={self.X: inp(2, 0.5, 0.2, rows=("targeted_disruption",))},
                  cands={"Box Plan": inp(4, 0.9, 0.5)})
        self.assertIsNone(tuner.donor_trade(self.X, d))
        d["cands"]["Box Removal"] = inp(2, 0.5, 0.2, rows=("targeted_disruption",))
        self.assertEqual(tuner.donor_trade(self.X, d)["donor_replacement"], "Box Removal")

    def test_best_donor_deck_wins_and_a_free_y_is_promised_once(self):
        worse = donor(name="Worse Deck", cards={self.X: inp(2, 0.5, 0.2)}, cands={"Box Card": inp(2, 0.5, 0.2)})
        better = donor(name="Dawnbringer", cards={self.X: inp(2, 0.5, 0.2)}, cands={"Box Card": inp(4, 0.6, 0.25)})
        own = swap(build([self.steal_cand(decks=("Worse Deck", "Dawnbringer"))],
                         donors={"Worse Deck": worse, "Dawnbringer": better}), "Weak Plan")["own"]
        self.assertEqual((own["donor_deck"], own["deck"]), ("Dawnbringer", "Dawnbringer"))
        # Two steals that both need the one free Box Card: only the first gets it.
        two = [self.steal_cand(), cand("Sleeved Removal", 4, 0.9, 0.4, owned=True, available=0, decks=["Dawnbringer"],
                                       rows=("targeted_disruption",))]
        d = donor(cards={self.X: inp(2, 0.5, 0.2), "Sleeved Removal": inp(2, 0.5, 0.2)},
                  cands={"Box Card": inp(2, 0.6, 0.25, rows=("targeted_disruption",))})
        out = build(two, donors={"Dawnbringer": d})
        got = [s["own"]["donor_replacement"] for s in out["swaps"] if s["own"]]
        self.assertEqual(got, ["Box Card"])
        # A free card given to B isn't also offered to A (ramp cut "Weak Removal" can't take it either).
        out = build([self.steal_cand(), cand("Box Card", 4, 0.9, 0.4, owned=True, available=1,
                                             rows=("targeted_disruption",))],
                    donors={"Dawnbringer": donor(cards={self.X: inp(2, 0.5, 0.2)},
                                                 cands={"Box Card": inp(2, 0.6, 0.25)})})
        adds = [s["own"]["name"] for s in out["swaps"] if s["own"]]
        ys = [s["own"].get("donor_replacement") for s in out["swaps"] if s["own"]]
        self.assertEqual(adds.count("Box Card") + ys.count("Box Card"), 1)

    def test_donor_book_scores_b_from_redis_read_only(self):
        """DonorBook builds B's context from Redis (fixtures in a FakeRedis), and only reads."""
        r = FakeRedis()
        b = {"id": 3503874, "registry_id": "archidekt:3503874", "name": "Dawnbringer", "color": "WRG",
             "commanders": ["Dawnbringer Cleric"],
             "cards": [{"name": n, "quantity": 1} for n in ("Dawnbringer Cleric", self.X, "Combat Celebrant")]
             + [{"name": "Plains", "quantity": 10}]}
        r.set("decks", json.dumps([DECK, b]))
        for n, color, t in ((self.X, "W", "Creature"), ("Combat Celebrant", "R", "Creature"),
                            ("Box Card", "G", "Creature"), ("Blue Card", "U", "Creature")):
            r.set(f"card_meta:{n}", json.dumps({"type": t, "color": color, "super_types": [], "oracle_text": ""}))
        for i, (n, status, deck_name) in enumerate(((self.X, "in_deck", "Dawnbringer"),
                                                    ("Box Card", "in_collection", ""),
                                                    ("Blue Card", "in_collection", ""))):
            r.set(f"instance:{i}", json.dumps({"card_name": n, "ownership_status": status, "deck_name": deck_name}))
        journal.save_journal(r, b, {"pet_cards": ["Combat Celebrant"]})
        before = dict(r.store)

        class NoEmbed:
            def available(self):
                return False

        book = tuner.DonorBook(r, DECK, embedder=NoEmbed())
        ctx = book.get("Dawnbringer")
        self.assertEqual(ctx["colors"], "WRG")
        self.assertEqual(ctx["pets"], {"Combat Celebrant"})
        self.assertIn(self.X, ctx["cards"])
        self.assertIn("Box Card", ctx["cands"])                         # free + in identity
        self.assertNotIn("Blue Card", ctx["cands"])                     # off identity
        self.assertIn("Mountain", ctx["cands"])                         # basics in B's colours
        self.assertIn("Plains", ctx["cands"])                           # basics are exempt from singleton
        self.assertNotIn("Island", ctx["cands"])
        self.assertNotIn("Wastes", ctx["cands"])                        # only for a colourless deck
        self.assertIsNone(book.get("Holy Hellfire"))                    # the receiver is never a donor
        self.assertIsNone(book.get("No Such Deck"))
        t = tuner.donor_trade(self.X, ctx)
        self.assertIsNotNone(t)
        self.assertGreaterEqual(t["donor_delta"], 0)
        self.assertEqual(r.store, before)                               # nothing written to Redis


class TestCutsAndPets(unittest.TestCase):
    def test_pets_are_never_cut(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1),
                     cand("Owned Bomb 2", 4, 0.9, 0.4, owned=True, available=1),
                     cand("Owned Bomb 3", 4, 0.9, 0.4, owned=True, available=1, rows=("targeted_disruption",))])
        self.assertNotIn("Pet Card", [s["cut"]["name"] for s in out["swaps"]])
        self.assertEqual(out["pet_notice"]["name"], "Pet Card")

    def test_pets_from_the_journal_win_even_if_weak_misses_the_flag(self):
        weak = json.loads(json.dumps(WEAK))
        weak["cards"][0]["pet"] = False
        out = tuner.build_tune(DECK, weak, COMP, [cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)], CAPS,
                               p75=4.0, has_pillars=True, has_edhrec=True, pets=["Weak Plan"])
        self.assertIsNone(swap(out, "Weak Plan"))

    def test_role_is_kept_removal_is_replaced_by_removal(self):
        out = build([cand("Owned Plan Bomb", 4, 0.95, 0.5, owned=True, available=1)])
        self.assertIsNone(swap(out, "Weak Removal"))
        out = build([cand("Owned Removal", 4, 0.9, 0.4, owned=True, available=1, rows=("targeted_disruption",))])
        self.assertEqual(swap(out, "Weak Removal")["own"]["name"], "Owned Removal")

    def test_lands_are_not_swap_options(self):
        out = build([cand("Owned Land", 4, 0.9, 0.4, owned=True, available=1, rows=("lands",), land=True)])
        self.assertEqual(out["swaps"], [])

    def test_swaps_are_ordered_by_weak_score(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1),
                     cand("Owned Removal", 4, 0.9, 0.4, owned=True, available=1, rows=("targeted_disruption",))])
        self.assertEqual([s["cut"]["name"] for s in out["swaps"]], ["Weak Plan", "Weak Removal"])

    def test_cut_carries_the_why_inputs(self):
        s = swap(build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)]), "Weak Plan")
        self.assertEqual(s["cut"]["why"], {"edges": 0, "fit": 0.2, "edhrec": 0.02})
        self.assertEqual(s["cut"]["weak_score"], 0.8)
        self.assertAlmostEqual(s["cut"]["score"], tuner.score(0, 4.0, 0.2, 0.02, True, True), places=4)

    def test_weak_table_marks_rows_with_a_swap(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)])
        by = {w["name"]: w for w in out["weak"]}
        self.assertTrue(by["Weak Plan"]["has_swap"])
        self.assertFalse(by["Pet Card"]["has_swap"])


class TestShapeFixes(unittest.TestCase):
    def test_rows_short_by_two_or_more_become_fixes(self):
        out = build([cand("Owned Rock", 1, 0.2, 0.3, owned=True, available=1, rows=("ramp",)),
                     cand("Owned Dual", 0, None, 0.4, owned=True, available=1, rows=("lands",), land=True)])
        keys = [f["key"] for f in out["fixes"]]
        self.assertEqual(keys, ["lands", "ramp", "targeted_disruption"])   # never plan, never ok rows
        ramp = next(f for f in out["fixes"] if f["key"] == "ramp")
        self.assertEqual(ramp["gap"], -9)
        self.assertEqual(ramp["owned"], ["Owned Rock"])
        self.assertIn("Owned Rock", ramp["advice"])
        self.assertEqual(ramp["action"], {"kind": "filter", "row": "ramp", "label": "See 1 owned"})

    def test_lands_fix_adds_basics_in_the_deck_colours(self):
        out = build([])
        lands = next(f for f in out["fixes"] if f["key"] == "lands")
        self.assertEqual(lands["action"]["kind"], "add_basics")
        self.assertEqual(lands["action"]["count"], 8)
        self.assertEqual(set(lands["action"]["basics"]), {"Plains", "Swamp", "Mountain"})
        self.assertEqual(len(lands["action"]["basics"]), 8)
        self.assertEqual(lands["action"]["label"], "Add 8 lands")

    def test_badge_counts_open_suggestions(self):
        out = build([cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)])
        self.assertEqual(out["counts"]["open"], len(out["fixes"]) + len(out["swaps"]) + 1)


class TestState(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_decisions_persist_and_reset_with_a_new_review(self):
        tuner.decide(self.r, DECK, "Weak Plan", "own", add="Owned Bomb")
        st = tuner.load_state(self.r, DECK)
        self.assertEqual(st["decisions"]["Weak Plan"]["decision"], "own")
        self.assertEqual(st["decisions"]["Weak Plan"]["add"], "Owned Bomb")
        tuner.decide(self.r, DECK, "Weak Plan", None)
        self.assertNotIn("Weak Plan", tuner.load_state(self.r, DECK)["decisions"])
        tuner.decide(self.r, DECK, "Weak Removal", "dismissed")
        # A finished review starts a new tuning pass: per-review decisions reset
        # (the DT-5 durable dismissal list lives separately and survives).
        journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 7}})
        journal.start_review(self.r, DECK, {"power": 6.5})
        journal.finish_review(self.r, DECK)
        self.assertEqual(tuner.load_state(self.r, DECK)["decisions"], {})

    def test_bad_decision_is_rejected(self):
        with self.assertRaises(tuner.TunerError):
            tuner.decide(self.r, DECK, "Weak Plan", "maybe")

    # DT-5: dismissed cuts are durable — they never re-flag, even across reviews.
    def test_dismissed_cut_survives_a_new_review(self):
        tuner.decide(self.r, DECK, "Weak Removal", "dismissed")
        self.assertIn("Weak Removal", tuner.dismissed_cuts(self.r, DECK))
        journal.save_journal(self.r, DECK, {"target_power_level": {"scale": 7}})
        journal.start_review(self.r, DECK, {"power": 6.5})
        journal.finish_review(self.r, DECK)
        # Per-review decisions reset; the durable dismissal does not.
        self.assertEqual(tuner.load_state(self.r, DECK)["decisions"], {})
        self.assertIn("Weak Removal", tuner.dismissed_cuts(self.r, DECK))

    def test_undismiss_lets_the_cut_flag_again(self):
        tuner.decide(self.r, DECK, "Weak Removal", "dismissed")
        tuner.decide(self.r, DECK, "Weak Removal", None)
        self.assertNotIn("Weak Removal", tuner.dismissed_cuts(self.r, DECK))
        self.assertNotIn("Weak Removal", tuner.load_state(self.r, DECK)["decisions"])

    def test_dismissed_cuts_drop_swaps_and_mark_weak_rows(self):
        cands = [cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1)]
        out = build(cands, dismissed=["Weak Plan"])
        self.assertIsNone(swap(out, "Weak Plan"))
        row = next(w for w in out["weak"] if w["name"] == "Weak Plan")
        self.assertTrue(row["dismissed"])
        self.assertEqual(out["dismissed"], ["Weak Plan"])
        # Other cuts are unaffected.
        self.assertFalse(next(w for w in out["weak"] if w["name"] == "Weak Removal")["dismissed"])

    def test_dismissed_rows_drop_out_accepted_rows_stay(self):
        cands = [cand("Owned Bomb", 4, 0.9, 0.4, owned=True, available=1),
                 cand("Owned Removal", 4, 0.9, 0.4, owned=True, available=1, rows=("targeted_disruption",))]
        state = {"decisions": {"Weak Plan": {"decision": "dismissed"}, "Weak Removal": {"decision": "own",
                                                                                     "add": "Owned Removal"}},
                 "adds": []}
        out = build(cands, state=state)
        self.assertIsNone(swap(out, "Weak Plan"))
        self.assertEqual(swap(out, "Weak Removal")["decision"], "own")
        open_swaps = sum(1 for s in out["swaps"] if not s["decision"])
        self.assertEqual(out["counts"]["open"], len(out["fixes"]) + open_swaps + 1)
        self.assertEqual(out["counts"]["open_swaps"], open_swaps)

    def test_adds_are_validated(self):
        tuner.set_adds(self.r, DECK, [{"name": "Plains", "qty": 2, "source": "basic"}])
        self.assertEqual(tuner.load_state(self.r, DECK)["adds"], [{"name": "Plains", "qty": 2, "source": "basic"}])
        with self.assertRaises(tuner.TunerError):
            tuner.set_adds(self.r, DECK, [{"name": "", "qty": 1}])

    def test_mark_pet_adds_to_the_journal(self):
        journal.save_journal(self.r, DECK, {"pet_cards": ["Pet Card"]})
        tuner.mark_pet(self.r, DECK, "Weak Plan")
        self.assertEqual(journal.load_journal(self.r, DECK)["pet_cards"], ["Pet Card", "Weak Plan"])
        with self.assertRaises(tuner.TunerError):
            tuner.mark_pet(self.r, DECK, "Not In Deck")


class ProseProvider(llm_mod.LLMTagProvider):
    def __init__(self, fail=False):
        self.model = "gemini-3.8-flash"
        self.fail = fail
        self.prompts = []

    @property
    def name(self):
        return "gemini"

    def is_configured(self):
        return True

    def tag_batch(self, cards):  # pragma: no cover
        raise llm_mod.LLMProviderError("n/a")

    def generate_json(self, system, prompt, schema=None):
        self.prompts.append(prompt)
        if self.fail:
            raise llm_mod.LLMProviderError("gemini down")
        items = json.loads(prompt.split("SWAPS:", 1)[1])
        return {"items": [{"id": i["id"], "why": f"{i['add']} attacks harder than {i['cut']}."} for i in items]}, \
            llm_mod.Usage(input_tokens=1200, output_tokens=90)


class TestProse(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.tune = build([cand("Owned Ok", 1, 0.4, 0.05, owned=True, available=1),
                           cand("Soft Buy", 4, 0.95, 0.5, price=18.50)])

    def test_llm_writes_only_prose_and_cost_is_logged(self):
        p = ProseProvider()
        out = tuner.generate_prose(self.r, DECK, self.tune, llm_mod.LLMChain([p]))
        s = swap(self.tune, "Weak Plan")
        self.assertEqual(out["prose"]["Weak Plan"]["why"], "Soft Buy attacks harder than Weak Plan.")
        # The numbers in the soft-cap line come from the tuner, the reason from the LLM.
        self.assertIn("$18.50", out["prose"]["Weak Plan"]["soft_note"])
        self.assertIn("Soft Buy attacks harder", out["prose"]["Weak Plan"]["soft_note"])
        self.assertEqual(out["llm"]["provider"], "gemini")
        self.assertGreater(out["llm"]["cost_usd"], 0)
        run = json.loads(self.r.lists["interactions:runs"][0])
        self.assertEqual(run["trigger"], "tuner")
        self.assertEqual(run["llm_call_count"], 1)
        self.assertGreater(run["llm_cost_usd"], 0)
        # Numbers never go to the LLM as something to restate.
        self.assertIn("never", tuner.PROSE_SYSTEM.lower())
        self.assertEqual(s["pick"], "buy")

    def test_prose_is_cached(self):
        p = ProseProvider()
        tuner.generate_prose(self.r, DECK, self.tune, llm_mod.LLMChain([p]))
        again = tuner.generate_prose(self.r, DECK, self.tune, llm_mod.LLMChain([p]))
        self.assertEqual(len(p.prompts), 1)
        self.assertEqual(again["llm"]["cached"], len(self.tune["swaps"]))
        self.assertTrue(tuner.attach_cached_prose(self.r, DECK, self.tune)["swaps"][0]["why"])

    def test_llm_failure_keeps_the_deterministic_rows(self):
        out = tuner.generate_prose(self.r, DECK, self.tune, llm_mod.LLMChain([ProseProvider(fail=True)]))
        self.assertEqual(out["prose"], {})
        self.assertEqual(out["llm"]["status"], "llm_failed")
        self.assertIn("$18.50", swap(self.tune, "Weak Plan")["soft_note"])


class TestCandidatePool(unittest.TestCase):
    """The I/O layer's pure helpers: colour identity, edges into the deck."""

    def test_colour_identity(self):
        self.assertTrue(tuner.in_identity("WR", "WBR"))
        self.assertTrue(tuner.in_identity("C", "WBR"))
        self.assertFalse(tuner.in_identity("G", "WBR"))
        self.assertTrue(tuner.in_identity("", "C"))
        self.assertFalse(tuner.in_identity("W", "C"))

    def test_edges_into_the_deck_ignore_mana_and_lands(self):
        deck_tags = {"Kaalia of the Vast": {"produces": ["attacks"], "consumes": []},
                     "Rock": {"produces": ["mana"], "consumes": []},
                     "Payoff": {"produces": [], "consumes": ["etb"]}}
        by = tuner.edges_into(deck_tags, {"produces": ["etb", "mana"], "consumes": ["attacks"]})
        self.assertEqual(by, {"Kaalia of the Vast": 1, "Payoff": 1})

    def test_basics_cycle_through_colours(self):
        self.assertEqual(tuner.basics_for("WBR", 4), ["Plains", "Swamp", "Mountain", "Plains"])
        self.assertEqual(tuner.basics_for("C", 2), ["Wastes", "Wastes"])


if __name__ == "__main__":
    unittest.main()
