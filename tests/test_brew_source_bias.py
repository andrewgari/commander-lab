"""Bounded source bias: owned/imported cards get a small additive nudge."""
import json
import unittest
from dataclasses import replace

from tests.test_brew import FakeRedis

from src.server.domain.draft import brew_builder, brew_scoring as bs
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig
from src.server.domain.draft.deck_validator import DraftDeck
from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX
from src.server.domain.analytics.providers.edhrec import commander_slug

CMD = "Atraxa, Praetors' Voice"


def ctx(sources=(), edhrec=None, **kw):
    names, oids = bs.build_source_index(sources)
    return bs.ScoreContext(commander=CMD, themes=(), edhrec=edhrec or {}, skeleton={},
                           source_names=names, source_oracle_ids=oids, **kw)


class TestLookup(unittest.TestCase):
    def test_matches_normalized_name(self):
        c = ctx(["Nazgûl"])
        self.assertTrue(bs.in_sources({"name": "NAZGUL"}, c))
        self.assertFalse(bs.in_sources({"name": "Other"}, c))

    def test_double_faced_any_face_matches(self):
        c = ctx(["Delver of Secrets // Insectile Aberration"])
        self.assertTrue(bs.in_sources({"name": "Delver of Secrets"}, c))
        self.assertTrue(bs.in_sources({"name": "Insectile Aberration"}, c))
        c2 = ctx(["Delver of Secrets"])
        self.assertTrue(bs.in_sources({"name": "Delver of Secrets // Insectile Aberration"}, c2))

    def test_oracle_id_matches_other_printing_name(self):
        c = ctx([{"name": "Sol Ring", "oracle_id": "abc"}])
        self.assertTrue(bs.in_sources({"name": "Sol Ring (Commander Legends)", "oracle_id": "abc"}, c))
        self.assertFalse(bs.in_sources({"name": "Zzz", "oracle_id": "other"}, c))


class TestBias(unittest.TestCase):
    def _pool(self, n=10):
        return [{"name": f"Card {i:02d}"} for i in range(n)]

    def test_equal_scores_owned_wins(self):
        pool = self._pool()
        c = ctx(["Card 07"])
        self.assertEqual(bs.select_cards(pool, c, 1, BrewScoringConfig())[0]["name"], "Card 07")
        self.assertEqual(bs.select_cards(pool, ctx(), 1, BrewScoringConfig())[0]["name"], "Card 00")

    def test_breakdown_exposes_bonus(self):
        r = bs.score_card({"name": "Card 07"}, ctx(["Card 07"]), BrewScoringConfig())
        self.assertGreater(r.components["source_bias"], 0)
        self.assertEqual(bs.score_card({"name": "Card 01"}, ctx(["Card 07"])).components["source_bias"], 0)

    def test_zero_weight_disables(self):
        cfg = BrewScoringConfig(source_bias=0.0)
        r = bs.score_card({"name": "Card 07"}, ctx(["Card 07"]), cfg)
        self.assertEqual(r.components["source_bias"], 0)
        self.assertEqual(bs.select_cards(self._pool(), ctx(["Card 07"]), 1, cfg)[0]["name"], "Card 00")

    def test_clearly_better_card_not_displaced(self):
        # Spread of EDHREC scores; the owned card is weaker than the top by well over one gap.
        edhrec = {f"card {i:02d}": {"inclusion": 0.05 * i, "synergy": 0.0} for i in range(10)}
        pool = self._pool()
        for weight in (0.25, 5.0):  # even an absurd weight is clamped by source_bias_max
            cfg = BrewScoringConfig(source_bias=weight)
            top = bs.select_cards(pool, ctx(["Card 03"], edhrec), 1, cfg)[0]["name"]
            self.assertEqual(top, "Card 09")

    def test_bonus_nudges_adjacent_scores_only(self):
        edhrec = {f"card {i:02d}": {"inclusion": 0.05 * i, "synergy": 0.0} for i in range(10)}
        cfg = BrewScoringConfig()
        # owning the runner-up is still smaller than one gap -> top card holds
        self.assertEqual(bs.select_cards(self._pool(), ctx(["Card 08"], edhrec), 1, cfg)[0]["name"], "Card 09")

    def test_bonus_bounded_by_median_gap(self):
        edhrec = {f"card {i:02d}": {"inclusion": 0.05 * i, "synergy": 0.0} for i in range(10)}
        cfg = BrewScoringConfig(source_bias=99.0)
        c = ctx(["Card 00"], edhrec)
        unit = 0.05 / cfg.inclusion_full * cfg.edhrec_inclusion
        got = bs.score_card({"name": "Card 00"}, replace(c, source_unit=unit), cfg).components["source_bias"]
        self.assertLessEqual(got, cfg.source_bias_max * unit + 1e-9)
        self.assertLess(got, unit)

    def test_overlap_with_sources_increases(self):
        owned = [f"Card {i:02d}" for i in range(40, 60)]
        pool = [{"name": f"Card {i:02d}"} for i in range(100)]
        # mild score spread so ties are rare but gaps are small
        edhrec = {f"card {i:02d}": {"inclusion": 0.0, "synergy": 0.0} for i in range(100)}
        on = bs.select_cards(pool, ctx(owned, edhrec), 20, BrewScoringConfig())
        off = bs.select_cards(pool, ctx(owned, edhrec), 20, BrewScoringConfig(source_bias=0.0))
        ov = lambda ch: len({c["name"] for c in ch} & set(owned))
        self.assertGreater(ov(on), ov(off))


class TestBuilder(unittest.TestCase):
    def _session(self, cards):
        return {"commanders": [CMD], "build_mode": "owned_first", "themes": [], "preferences": [{}],
                "donor_decks": [{"cards": [{"name": n} for n in cards], "commanders": []}]}

    def test_builder_output_validates_and_includes_donor_cards(self):
        names = [f"Card {i:02d}" for i in range(40)]
        draft = brew_builder.StubBrewBuilder().build(self._session(names), FakeRedis())[0]
        DraftDeck.from_builder_draft(draft)
        self.assertTrue(any(c["name"] in names for c in draft["cards"]))

    def test_inventory_ownership_biases_tier3_tie(self):
        # Sol Ring/Arcane Signet/Command Tower are tier-3 fallbacks; a donor card named
        # like one of them is a source, so the tie among the three resolves to it.
        s = self._session(["Command Tower"])
        d = brew_builder.StubBrewBuilder().build(s, FakeRedis())[0]
        self.assertIn("Command Tower", [c["name"] for c in d["cards"]])


if __name__ == "__main__":
    unittest.main()
