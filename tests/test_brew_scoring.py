"""Brew card scoring: components, config overrides, hooks, skeleton ranges."""
import json
import unittest

from tests.test_brew import FakeRedis

from src.server.domain.draft import brew_scoring as bs
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig, SkeletonRange


def ctx(**kw):
    base = dict(
        commander="Atraxa",
        themes=("proliferate",),
        deck_counts={},
        skeleton={"ramp": SkeletonRange(8, 12), "draw": SkeletonRange(6, 10)},
        edhrec={},
    )
    base.update(kw)
    return bs.ScoreContext(**base)


def card(name="X", **kw):
    return {"name": name, **kw}


class TestComponents(unittest.TestCase):
    def test_commander_synergy_counts_theme_matches(self):
        c = ctx()
        hit = bs.score_card(card(oracle_text="Proliferate each time"), c)
        miss = bs.score_card(card(oracle_text="Draw a card"), c)
        self.assertGreater(hit.components["commander_synergy"], 0)
        self.assertEqual(miss.components["commander_synergy"], 0)

    def test_edhrec_inclusion_and_synergy_scaled(self):
        e = {"sol ring": {"inclusion": 0.6, "synergy": 0.5}, "meh card": {"inclusion": 0.0, "synergy": -0.25}}
        c = ctx(edhrec=e)
        hi = bs.score_card(card("Sol Ring"), c)
        lo = bs.score_card(card("Meh Card"), c)
        none = bs.score_card(card("Unknown"), c)
        cfg = BrewScoringConfig()
        self.assertAlmostEqual(hi.components["edhrec_inclusion"], cfg.edhrec_inclusion)
        self.assertAlmostEqual(hi.components["edhrec_synergy"], cfg.edhrec_synergy)
        self.assertEqual(lo.components["edhrec_inclusion"], 0)
        self.assertEqual(lo.components["edhrec_synergy"], 0)
        self.assertEqual(none.components["edhrec_inclusion"], 0)

    def test_category_need_decays_toward_top_of_range(self):
        scores = []
        for n in (0, 8, 10, 11):
            c = ctx(deck_counts={"ramp": n})
            scores.append(bs.score_card(card(tags=["ramp"]), c).components["category_need"])
        self.assertEqual(scores[0], scores[1])  # below the floor: full need
        self.assertGreater(scores[1], scores[2])
        self.assertGreater(scores[2], scores[3])
        self.assertGreater(scores[3], 0)

    def test_category_need_penalizes_going_over_range(self):
        at_top = bs.score_card(card(tags=["ramp"]), ctx(deck_counts={"ramp": 12})).components["category_need"]
        over = bs.score_card(card(tags=["ramp"]), ctx(deck_counts={"ramp": 14})).components["category_need"]
        self.assertLess(at_top, 0)
        self.assertLess(over, at_top)

    def test_uncategorised_card_has_zero_need(self):
        self.assertEqual(bs.score_card(card(), ctx()).components["category_need"], 0)

    def test_total_is_sum_of_components(self):
        r = bs.score_card(card("Sol Ring", tags=["ramp"], oracle_text="proliferate"),
                          ctx(edhrec={"sol ring": {"inclusion": 0.3, "synergy": 0.1}}))
        self.assertAlmostEqual(r.total, sum(r.components.values()))


class TestConfig(unittest.TestCase):
    def test_override_changes_score(self):
        c = ctx(edhrec={"a": {"inclusion": 0.6, "synergy": 0.0}})
        base = bs.score_card(card("A"), c)
        boosted = bs.score_card(card("A"), c, BrewScoringConfig(edhrec_inclusion=base.components["edhrec_inclusion"] * 3))
        self.assertAlmostEqual(boosted.components["edhrec_inclusion"], base.components["edhrec_inclusion"] * 3)
        self.assertGreater(boosted.total, base.total)

    def test_zero_weight_disables_component(self):
        c = ctx()
        r = bs.score_card(card(oracle_text="proliferate"), c, BrewScoringConfig(commander_synergy=0.0))
        self.assertEqual(r.components["commander_synergy"], 0)

    def test_from_env_json_overrides_and_ignores_junk(self):
        cfg = BrewScoringConfig.from_env({"BREW_SCORING_WEIGHTS": json.dumps({"category_need": 9, "bogus": 1})})
        self.assertEqual(cfg.category_need, 9)
        self.assertEqual(BrewScoringConfig.from_env({"BREW_SCORING_WEIGHTS": "not json"}), BrewScoringConfig())

    def test_placeholder_blocks_exist_and_source_bias_bounded(self):
        cfg = BrewScoringConfig()
        self.assertGreater(cfg.source_bias, 0.0)
        self.assertLess(cfg.source_bias, 1.0)
        self.assertEqual(cfg.power_level, {})

    def test_no_numeric_literal_weights_in_scoring_module(self):
        import inspect
        src = inspect.getsource(bs)
        for bad in ("0.4", "0.25", "0.15", "0.6", "0.5"):
            self.assertNotIn(bad, src)


class TestHooks(unittest.TestCase):
    def tearDown(self):
        bs.clear_terms()

    def test_registered_term_adds_to_total_and_breakdown(self):
        base = bs.score_card(card("A"), ctx())
        bs.register_term("source_bias", lambda c, x, cfg: 0.07)
        r = bs.score_card(card("A"), ctx())
        self.assertAlmostEqual(r.components["source_bias"], 0.07)
        self.assertAlmostEqual(r.total, base.total + 0.07)

    def test_extra_terms_argument_without_registry(self):
        r = bs.score_card(card("A"), ctx(), extra_terms={"power": lambda c, x, cfg: -1.0})
        self.assertEqual(r.components["power"], -1.0)

    def test_bad_term_is_isolated(self):
        def boom(c, x, cfg):
            raise RuntimeError("x")
        r = bs.score_card(card("A"), ctx(), extra_terms={"boom": boom})
        self.assertEqual(r.components["boom"], 0.0)


class TestSelection(unittest.TestCase):
    def _pool(self):
        pool = []
        for i in range(30):
            pool.append(card(f"Ramp {i}", tags=["ramp"]))
        for i in range(30):
            pool.append(card(f"Draw {i}", tags=["draw"]))
        for i in range(60):
            pool.append(card(f"Other {i}"))
        return pool

    def test_category_counts_land_within_skeleton_ranges(self):
        skel = {"ramp": SkeletonRange(8, 12), "draw": SkeletonRange(6, 10)}
        edh = {}
        for i in range(30):  # make ramp/draw very popular so only the range stops them
            edh[f"ramp {i}"] = {"inclusion": 0.9, "synergy": 0.5}
            edh[f"draw {i}"] = {"inclusion": 0.9, "synergy": 0.5}
        c = ctx(skeleton=skel, edhrec=edh)
        picked = bs.select_cards(self._pool(), c, 63)
        self.assertEqual(len(picked), 63)
        counts = bs.count_categories(picked)
        self.assertTrue(8 <= counts["ramp"] <= 12, counts)
        self.assertTrue(6 <= counts["draw"] <= 10, counts)

    def test_floor_is_filled_even_when_unpopular(self):
        skel = {"ramp": SkeletonRange(8, 12)}
        edh = {f"other {i}": {"inclusion": 0.8, "synergy": 0.4} for i in range(60)}
        picked = bs.select_cards(self._pool(), ctx(skeleton=skel, edhrec=edh), 40)
        self.assertGreaterEqual(bs.count_categories(picked)["ramp"], 8)

    def test_selection_is_deterministic_and_unique(self):
        a = bs.select_cards(self._pool(), ctx(), 30)
        b = bs.select_cards(self._pool(), ctx(), 30)
        self.assertEqual([x["name"] for x in a], [x["name"] for x in b])
        self.assertEqual(len({x["name"] for x in a}), 30)


if __name__ == "__main__":
    unittest.main()


class TestBuilderWiring(unittest.TestCase):
    def _session(self, cards):
        return {"commanders": ["Atraxa, Praetors' Voice"], "build_mode": "owned_first", "themes": [],
                "preferences": [{}], "donor_decks": [{"cards": [{"name": n} for n in cards], "commanders": []}]}

    def test_builder_orders_donor_cards_by_edhrec_score_and_validates(self):
        from src.server.domain.draft import brew_builder
        from src.server.domain.draft.deck_validator import DraftDeck
        from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX
        from src.server.domain.analytics.providers.edhrec import commander_slug

        cmd = "Atraxa, Praetors' Voice"
        names = [f"Card {i:02d}" for i in range(40)]
        r = FakeRedis()
        r.set(CACHE_PREFIX + commander_slug([cmd]), json.dumps(
            {"cards": {"card 39": {"inclusion": 0.9, "synergy": 0.5}}}))
        draft = brew_builder.StubBrewBuilder().build(self._session(names), r)[0]
        mains = [c["name"] for c in draft["cards"] if c["name"] != cmd]
        self.assertEqual(mains[0], "Card 39")  # outranks alphabetical order
        DraftDeck.from_builder_draft(draft)  # draft format still loads for the final validator

    def test_config_override_changes_builder_order(self):
        from src.server.domain.draft import brew_builder
        from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX
        from src.server.domain.analytics.providers.edhrec import commander_slug
        import os

        cmd = "Atraxa, Praetors' Voice"
        r = FakeRedis()
        r.set(CACHE_PREFIX + commander_slug([cmd]), json.dumps(
            {"cards": {"card 39": {"inclusion": 0.9, "synergy": 0.5}}}))
        os.environ["BREW_SCORING_WEIGHTS"] = json.dumps({"edhrec_inclusion": 0, "edhrec_synergy": 0})
        try:
            draft = brew_builder.StubBrewBuilder().build(self._session([f"Card {i:02d}" for i in range(40)]), r)[0]
        finally:
            del os.environ["BREW_SCORING_WEIGHTS"]
        mains = [c["name"] for c in draft["cards"] if c["name"] != cmd]
        self.assertEqual(mains[0], "Card 00")


class TestReviewFixes(unittest.TestCase):
    def test_overlapping_floors_prefer_dual_card(self):
        skel = {"ramp": SkeletonRange(1, 5), "draw": SkeletonRange(1, 5)}
        pool = [card("A ramp", tags=["ramp"]), card("B dual", tags=["ramp", "draw"])]
        picked = bs.select_cards(pool, ctx(skeleton=skel), 1)
        self.assertEqual(picked[0]["name"], "B dual")

    def test_card_touching_any_capped_category_is_avoided(self):
        skel = {"ramp": SkeletonRange(0, 1), "draw": SkeletonRange(0, 5)}
        pool = [card("A dual", tags=["ramp", "draw"]), card("B draw", tags=["draw"])]
        c = ctx(skeleton=skel, deck_counts={"ramp": 1},
                edhrec={"a dual": {"inclusion": 0.9, "synergy": 0.5}})
        self.assertEqual(bs.select_cards(pool, c, 1)[0]["name"], "B draw")

    def test_invalid_normalisation_overrides_are_dropped(self):
        for bad in ({"inclusion_full": 0}, {"synergy_floor": 0.5, "synergy_ceil": 0.5}, {"synergy_saturation": 0}):
            cfg = BrewScoringConfig.from_env({"BREW_SCORING_WEIGHTS": json.dumps(bad)})
            r = bs.score_card(card("A", oracle_text="proliferate"),
                              ctx(edhrec={"a": {"inclusion": 0.5, "synergy": 0.1}}), cfg)
            self.assertIsInstance(r.total, float)

    def test_builder_reads_canonical_record_and_lab_tags(self):
        from src.server.domain.draft import brew_builder
        r = FakeRedis()
        r.set("lab_tags", json.dumps({"Rampy": ["ramp"]}))
        rec = brew_builder._card_record(r, "Rampy", json.loads(r.get("lab_tags")))
        self.assertEqual(rec["tags"], ["ramp"])
        from src.server.domain.inventory import cards as card_store
        orig = card_store.get_card_by_name
        card_store.get_card_by_name = lambda rr, n: {"oracle_text": "proliferate", "type_line": "Artifact"}
        try:
            rec = brew_builder._card_record(r, "Rampy")
        finally:
            card_store.get_card_by_name = orig
        self.assertEqual(rec["oracle_text"], "proliferate")
