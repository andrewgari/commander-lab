"""OL-5: LOOPS caps table, build_oracle, main._oracle/_oracle_tool_ctx (spec oracle-tool-loop §3)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.llm.port import LLMNotConfigured  # noqa: E402
from src.server.infra.llm.factory import build_oracle  # noqa: E402
from src.server.infra.llm.tasks import LOOPS, SONNET, TASKS, TaskSpec  # noqa: E402

KEY = {"OPENROUTER_API_KEY": "k"}

ROWS = {
    "deck_review": (10, 16, 0.60, 1.00, 400_000, 1500),
    "sim_review": (6, 8, 0.30, 0.50, 200_000, 300),
    "sim_diagnosis": (4, 4, 0.05, 0.10, 60_000, 120),
    "card_review": (6, 8, 0.20, 0.35, 150_000, 180),
    "review_writer": (6, 8, 0.40, 0.70, 250_000, 300),
    "review_pipeline": (6, 8, 0.50, 0.90, 300_000, 600),
    "reconstruct": (14, 30, 1.50, 2.50, 900_000, 1200),
    "brew_review": (6, 8, 0.20, 0.35, 150_000, 240),
    "brew_rules": (2, 2, 0.05, 0.10, 40_000, 120),
    "brew_panel": (2, 2, 0.10, 0.20, 60_000, 180),
}


def _t(c):
    return (c.max_turns, c.max_tool_calls, c.soft_cost_usd, c.hard_cost_usd, c.max_tokens_in, c.deadline_s)


class LoopsTable(unittest.TestCase):
    def test_exactly_the_eight_rows(self):
        self.assertEqual({k: _t(v) for k, v in LOOPS.items()}, ROWS)

    def test_brew_review_task(self):
        self.assertEqual(TASKS["brew_review"], TaskSpec(SONNET, 240, 6000))


class BuildOracle(unittest.TestCase):
    def test_default_caps(self):
        o = build_oracle("sim_review", env=KEY)
        self.assertEqual(_t(o.caps), ROWS["sim_review"])

    def test_every_loops_task_builds(self):
        for task, row in ROWS.items():
            self.assertEqual(_t(build_oracle(task, env=KEY).caps), row, task)

    def test_unknown_task(self):
        with self.assertRaises(ValueError):
            build_oracle("nope", env=KEY)

    def test_no_key(self):
        with self.assertRaises(LLMNotConfigured):
            build_oracle("sim_review", env={"OPENROUTER_API_KEY": ""})

    def test_overrides(self):
        env = {**KEY, "LLM_LOOP_MAX_TURNS_SIM_REVIEW": "3", "LLM_LOOP_HARD_USD_SIM_REVIEW": "0.25"}
        c = build_oracle("sim_review", env=env).caps
        self.assertEqual((c.max_turns, c.hard_cost_usd, c.soft_cost_usd), (3, 0.25, 0.25))

    def test_invalid_overrides_ignored(self):
        for bad in ("x", "0", "-2", "nan", "inf", ""):
            env = {**KEY, "LLM_LOOP_MAX_TURNS_SIM_REVIEW": bad, "LLM_LOOP_HARD_USD_SIM_REVIEW": bad}
            self.assertEqual(_t(build_oracle("sim_review", env=env).caps), ROWS["sim_review"], bad)


class ToolCtx(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as m
        self.m = m

    def test_all_fail_degrades_to_none(self):
        m = self.m
        with mock.patch.object(m.journal_mod, "load_journal", side_effect=RuntimeError), \
                mock.patch.object(m.brackets_mod, "get_game_changers", side_effect=RuntimeError), \
                mock.patch("src.server.domain.analytics.fact_sources.default_fetchers", side_effect=RuntimeError):
            ctx = m._oracle_tool_ctx(object(), {"name": "d"})
        self.assertIsNone(ctx.scryfall_batch)
        self.assertTrue(callable(ctx.compute_metrics))  # lazy: binds r, cannot fail at build
        self.assertIsNone(ctx.journal)
        self.assertIsNone(ctx.game_changers)
        self.assertEqual(ctx.deck, {"name": "d"})

    def test_absent_journal_stays_none(self):
        m = self.m
        with mock.patch.object(m.journal_mod, "load_journal", return_value=None), \
                mock.patch.object(m.journal_mod, "public", return_value={"goals": ["default"]}), \
                mock.patch.object(m.brackets_mod, "get_game_changers", side_effect=RuntimeError):
            ctx = m._oracle_tool_ctx(object(), {"name": "d"})
        self.assertIsNone(ctx.journal)

    def test_success_and_kwargs(self):
        m = self.m
        gc = mock.Mock(cards=["Sol Ring"])

        async def fake_gc(r):
            return gc

        with mock.patch.object(m.journal_mod, "load_journal", return_value={"a": 1}), \
                mock.patch.object(m.journal_mod, "public", return_value={"pub": 1}), \
                mock.patch.object(m.brackets_mod, "get_game_changers", fake_gc), \
                mock.patch("src.server.domain.analytics.fact_sources.default_fetchers",
                           return_value={"scryfall_batch": len}):
            ctx = m._oracle_tool_ctx(object(), {"name": "d"}, allow_hypothetical=True)
        self.assertEqual(ctx.journal, {"pub": 1})
        self.assertEqual(ctx.game_changers, ["Sol Ring"])
        self.assertIs(ctx.scryfall_batch, len)
        self.assertTrue(callable(ctx.compute_metrics))
        self.assertTrue(ctx.allow_hypothetical)

    def test_oracle_helper(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaises(LLMNotConfigured):
                self.m._oracle("sim_review")


if __name__ == "__main__":
    unittest.main()
