"""Trace persistence (D2 review_runs / review_run_steps) on in-memory SQLite; no Postgres, no network."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.analytics.review_pipeline import b_analysis, config, runner
from src.server.infra.db.orm_models import Base, ReviewRunStepRow
from src.server.infra.services import review_trace_service as svc
from tests import review_pipeline_helpers as h
from tests.test_review_pipeline_passes import CFG, make_ctx


def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def scripted_run():
    replies = {"A": [h.BRIEF_REPLY]}
    for ax in config.AXES:
        replies[f"B:{ax.id}"] = [h.analysis(strengths=[h.finding()], problems=[h.finding(
            claim="Mana Crypt is a Game Changer. It lifts the floor.", cards=("Mana Crypt",), keys=("bracket.minimum_bracket",))])]
    return runner.run_ab(make_ctx(), h.ScriptedClient(replies), CFG)


class TraceStoreTests(unittest.TestCase):
    def test_round_trip_answers_which_prompt_and_model_made_finding_x(self):
        res = scripted_run()
        s = session()
        run_id = svc.store_trace(s, res.trace, owner_id="u1")
        s.commit()
        fid = res.analysis.problems[0]["id"]
        prov = svc.provenance_of(s, run_id, fid)
        self.assertEqual(prov["pass"], "B")
        self.assertEqual(prov["model"], "fake:b")
        self.assertTrue(prov["prompt_version"])
        self.assertEqual(prov, res.trace.provenance_of(fid) | {"response_model": prov["response_model"]})

    def test_one_step_row_per_attempt_with_hash_tokens_cost_and_raw_output(self):
        res = scripted_run()
        s = session()
        run_id = svc.store_trace(s, res.trace)
        rows = s.scalars(select(ReviewRunStepRow).where(ReviewRunStepRow.run_id == run_id)).all()
        self.assertEqual(len(rows), len(res.trace.steps))
        r = rows[0]
        self.assertEqual((r.pass_, r.model, r.verdict), ("A", "fake:a", "ok"))
        self.assertEqual(len(r.input_hash), 64)
        self.assertEqual((r.tokens_in, r.tokens_out), (1000, 200))
        self.assertAlmostEqual(r.cost_usd, 0.01)
        self.assertIn("what_it_does", r.response)

    def test_run_row_carries_models_prompt_versions_inputs_and_cost(self):
        res = scripted_run()
        s = session()
        run_id = svc.store_trace(s, res.trace)
        t = svc.load_trace(s, run_id)
        self.assertEqual(t.status, "ok")
        self.assertEqual(t.models["A"], "fake:a")
        self.assertEqual(t.prompt_versions, res.trace.prompt_versions)
        self.assertEqual(t.inputs["fact_pack_hash"], res.trace.inputs["fact_pack_hash"])
        self.assertAlmostEqual(t.cost_usd, res.trace.cost_usd)

    def test_failed_run_is_stored_with_its_error(self):
        client = h.ScriptedClient({"A": ["not json", "still not json", "nope"]})
        with self.assertRaises(runner.ReviewRunFailed) as cm:
            runner.run_ab(make_ctx(), client, CFG)
        s = session()
        run_id = svc.store_trace(s, cm.exception.trace)
        t = svc.load_trace(s, run_id)
        self.assertEqual(t.status, "failed")
        self.assertTrue(t.error)
        self.assertTrue(all(st["verdict"] in ("retry", "failed") for st in t.steps))

    def test_drops_are_stored(self):
        replies = {"A": [h.BRIEF_REPLY]}
        for ax in config.AXES:
            replies[f"B:{ax.id}"] = [h.analysis(problems=[h.finding(claim="x", cards=("Cyclonic Rift",))])]
        res = runner.run_ab(make_ctx(), h.ScriptedClient(replies), CFG)
        s = session()
        t = svc.load_trace(s, svc.store_trace(s, res.trace))
        self.assertEqual(len(t.drops), len(config.AXES))
        self.assertEqual(t.drops[0]["reason"], "evidence_card_not_in_deck")

    def test_unknown_run_raises(self):
        with self.assertRaises(KeyError):
            svc.load_trace(session(), 999)

    def test_migration_is_chained_after_a009_and_idempotent_ddl(self):
        from src.server.infra.db.migrations.versions import a010_review_runs as m
        self.assertEqual(m.down_revision, "a009_player_profile")
        for ddl in m.ALL_DDLS:
            self.assertIn("IF NOT EXISTS", ddl)


if __name__ == "__main__":
    unittest.main()
