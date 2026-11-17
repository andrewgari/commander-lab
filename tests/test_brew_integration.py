"""End-to-end Brew integration: scoring + power-level targeting + source bias together.

Covers 3 commanders x 3 power levels x source bias on/off:
  * category counts stay inside the skeleton ranges (selection over a supplied pool),
  * power level shifts the choice of power signals (fast mana / tutors / GCs),
  * the source bias is bounded (its bonus never exceeds the configured fraction of the
    candidate gap, and it changes only a bounded number of picks),
  * every deck the full builder produces passes the final validator,
  * selection is deterministic for identical inputs.
No randomness, no network: FakeRedis + fixture catalog via tests.support.brew_harness.
"""
from __future__ import annotations

import unittest

from src.server.domain.draft import brew_power
from src.server.domain.draft import brew_scoring as bs
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig
from tests.support import deck_assertions as da
from tests.support.brew_matrix import matrix_catalog
from src.server.domain.draft.brew_builder import StubBrewBuilder
from tests.support.brew_harness import run_brew

COMMANDERS = ("Krenko, Mob Boss", "Meren of Clan Nel Toth", "Kozilek, the Great Distortion")  # all in matrix_catalog()
POWERS = ("low", "mid", "high")
CFG = BrewScoringConfig()
SKEL = CFG.skeleton()
LIMIT = 40


def _pool():
    """Category-tagged candidate pool with ample supply in every category and an equal
    share of power signals, so any shift in picks is caused by scoring, not by scarcity."""
    cards = []
    spec = [("ramp", 30), ("draw", 30), ("removal", 20), ("wipe", 8), ("other", 60)]
    i = 0
    for cat, n in spec:
        for j in range(n):
            i += 1
            signal = ["fast mana", "tutor", "combo", ""][j % 4] if cat != "other" else ("fast mana" if j % 5 == 0 else "")
            cards.append({
                "name": f"Pool {i:03d} {cat}", "category": cat,
                "tags": [t for t in (signal,) if t], "cmc": (j % 6) + (1 if cat != "other" else 2),
                "type_line": "Creature" if j % 3 else "Artifact",
                "oracle_text": "Search your library for a card." if signal == "tutor" else "",
                "oracle_id": f"oid-{i:03d}",
            })
    return cards


def _ctx(power, sources=frozenset()):
    return bs.ScoreContext(
        commander="X", themes=("spells",), deck_counts={}, skeleton=SKEL, edhrec={},
        source_names=frozenset(sources), target_power=power)


def _signal_count(picks):
    out = {"fast_mana": 0, "tutor": 0, "combo": 0}
    for c in picks:
        sig = brew_power.card_signals(c)
        for k in out:
            out[k] += bool(sig.get(k))
    return out


class BrewIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.catalog = matrix_catalog()
        cls.pool = _pool()
        cls.src_names = [c["name"] for c in cls.pool[::7]]  # a spread of owned cards

    # 1. Full builder + final validator across commander x power x bias ------------------
    def test_every_built_deck_passes_final_validator(self):
        for cmd in COMMANDERS:
            for power in POWERS:
                for sources in (True, False):
                    with self.subTest(cmd=cmd, power=power, sources=sources):
                        res = run_brew(cmd, power, sources=sources, catalog=self.catalog,
                                       builder=StubBrewBuilder())
                        da.assert_valid_brew_deck(res.deck, self.catalog)
                        self.assertEqual(res.deck.get("power_level"), power)

    # 2. Category counts within skeleton ranges ----------------------------------------
    def test_category_counts_within_skeleton_ranges(self):
        for power in (3.0, 6.0, 8.5):
            for sources in (False, True):
                srcs = self.src_names if sources else ()
                ctx = _ctx(power, bs.build_source_index(srcs)[0] if sources else frozenset())
                picks = bs.select_cards(self.pool, ctx, LIMIT, CFG)
                counts = bs.count_categories(picks, SKEL)
                for cat, rng in SKEL.items():
                    with self.subTest(power=power, sources=sources, cat=cat):
                        self.assertGreaterEqual(counts.get(cat, 0), rng.low)
                        self.assertLessEqual(counts.get(cat, 0), rng.high)

    # 3. Power shifts the signal mix ----------------------------------------------------
    def test_power_level_shifts_signal_choices(self):
        lo = _signal_count(bs.select_cards(self.pool, _ctx(3.0), LIMIT, CFG))
        hi = _signal_count(bs.select_cards(self.pool, _ctx(8.5), LIMIT, CFG))
        self.assertGreater(hi["fast_mana"] + hi["tutor"] + hi["combo"],
                           lo["fast_mana"] + lo["tutor"] + lo["combo"],
                           f"high power should pick more power signals (low={lo}, high={hi})")

    # 4. Source bias is bounded ---------------------------------------------------------
    def test_source_bias_bonus_is_bounded_by_gap(self):
        src = bs.build_source_index(self.src_names)[0]
        base = _ctx(6.0, src)
        ordered = sorted(bs.score_card(c, base, BrewScoringConfig(source_bias=0.0)).total for c in self.pool)
        gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b - a > 0]
        unit = sorted(gaps)[len(gaps) // 2]
        ctx = bs.ScoreContext(**{**base.__dict__, "source_unit": unit})
        for c in self.pool:
            bonus = bs.score_card(c, ctx, CFG).components["source_bias"]
            self.assertLessEqual(bonus, CFG.source_bias_max * unit + 1e-9)
            self.assertLess(bonus, unit, "bias must stay below one full candidate gap")

    def test_source_bias_changes_only_a_bounded_number_of_picks(self):
        for power in (3.0, 6.0, 8.5):
            src = bs.build_source_index(self.src_names)[0]
            on = bs.select_cards(self.pool, _ctx(power, src), LIMIT, CFG)
            off = bs.select_cards(self.pool, _ctx(power, src), LIMIT, BrewScoringConfig(source_bias=0.0))
            changed = len({c["name"] for c in on} ^ {c["name"] for c in off}) // 2
            with self.subTest(power=power):
                self.assertLessEqual(changed, LIMIT // 2, f"bias swapped {changed} of {LIMIT} picks")

    # 5. Determinism --------------------------------------------------------------------
    def test_selection_is_deterministic(self):
        src = bs.build_source_index(self.src_names)[0]
        a = [c["name"] for c in bs.select_cards(self.pool, _ctx(6.0, src), LIMIT, CFG)]
        b = [c["name"] for c in bs.select_cards(list(reversed(self.pool)), _ctx(6.0, src), LIMIT, CFG)]
        self.assertEqual(a, b)

    # 6. Real power estimate is not masked by the harness ---------------------------------
    def test_estimated_power_is_real_and_varies_by_commander_and_bias(self):
        est = {}
        for c in COMMANDERS:
            for p in POWERS:
                for src in (True, False):
                    res = run_brew(c, p, sources=src, catalog=self.catalog, builder=StubBrewBuilder())
                    # the harness must not inject Commander Salt unless asked
                    self.assertEqual(res.power_source, "bracket_band", (c, p, src))
                    self.assertNotIn(res.estimated_power, (3.0, 6.5, 8.5), (c, p, src))
                    est[(c, p, src)] = res.deck["estimated_power"]  # builder's own estimate
        for c in COMMANDERS:
            for src in (True, False):
                tiers = [est[(c, p, src)] for p in POWERS]
                self.assertLess(tiers[0], tiers[1], (c, tiers))
                self.assertLess(tiers[1], tiers[2], (c, tiers))
        for p in POWERS:  # estimates differ across commanders at the same tier
            self.assertGreater(len({est[(c, p, True)] for c in COMMANDERS}), 1, p)
        # source bias is bounded: it must not swing the estimate between tiers
        for c in COMMANDERS:
            for p in POWERS:
                self.assertAlmostEqual(est[(c, p, True)], est[(c, p, False)], delta=1.0, msg=(c, p))


if __name__ == "__main__":
    unittest.main()
