"""OL-4: sim_run oracle tool (SimRunner) and skip_review in sim_service.run_job."""
import copy
import json
import unittest
from unittest.mock import patch

from src.server.domain.analytics import sim_service as svc
from src.server.domain.analytics import sim_store as ss
from src.server.domain.llm.port import LLMUnavailable
from src.server.domain.oracle.registry import ToolError
from src.server.domain.oracle.tools import FACTORIES, SimRunner, ToolContext, build_registry
from tests.test_sim_service_and_api import FakeSim, put_decks, user_deck
from tests.test_sim_store_and_diff import FakeRedis, make_deck, make_report, make_review


class Boom:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("LLM factory must not be called")


class Base(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        put_decks(self.r, self.deck, user_deck(1), user_deck(2), user_deck(3))
        svc.save_pod(self.r, ["user_901"], FakeSim())
        self.addCleanup(svc._INFLIGHT.clear)
        p = patch.object(svc.review_metrics, "review_deck", return_value=make_review())
        p.start()
        self.addCleanup(p.stop)

    def runner(self, sim, **kw):
        return SimRunner(self.r, self.deck, client=sim, sleep=lambda s: None, **kw)


class TestNewRun(Base):
    def test_new_run_skips_review_and_returns_comparison(self):
        sim = FakeSim()
        out = self.runner(sim).run(20)
        self.assertEqual(out["source"], "new_run")
        self.assertTrue(out["job_id"])
        self.assertIn("fingerprint", out["comparison"])
        self.assertNotIn("games", out["comparison"].get("pod_breakdown") or {})
        self.assertEqual(len(sim.payloads), 1)
        fp = svc.review_metrics.fingerprint(self.deck)
        self.assertIsNone(svc.sim_focus.saved_choice(self.r, svc._rid(self.deck), fp))  # user's chips untouched
        self.assertIsNone(ss.load_latest(self.r, self.deck))  # tool run saves nothing

    def test_run_job_skip_review_never_calls_factory(self):
        boom = Boom()
        sim = FakeSim()
        job = svc.begin_run(self.r, self.deck, {"games": 20}, sim)
        out = svc.run_job(self.r, job, self.deck, sim, boom, poll_interval=0, sleep=lambda s: None, skip_review=True)
        self.assertEqual((out["status"], boom.calls), ("done", 0))
        self.assertEqual(out["review_source"], "skipped")

    def test_default_still_reviews(self):
        job = svc.begin_run(self.r, self.deck, {"games": 20}, FakeSim())
        calls = []

        def factory(task):
            calls.append(1)
            raise LLMUnavailable("down")

        out = svc.run_job(self.r, job, self.deck, FakeSim(), factory, poll_interval=0, sleep=lambda s: None)
        self.assertEqual((out["review_source"], len(calls)), ("fallback", 1))


class TestNoOverwrite(Base):
    def _presave(self):
        ss.record_job_started(self.r, self.deck, {"job_id": "user", "status": "running"})
        ss.save_completed(self.r, self.deck, {"job_id": "user", "status": "done", "report": make_report()})
        doc = copy.deepcopy(ss.load_latest(self.r, self.deck, persist_stale=False))
        return doc, None

    def _snapshot(self):
        return copy.deepcopy(ss.load_latest(self.r, self.deck, persist_stale=False))

    def test_new_run_keeps_saved_report_and_pointer(self):
        before = self._presave()[0]
        ptr = self.r.kv.get(f"deck:sim:job:{svc._rid(self.deck)}")
        self.assertIsNotNone(ptr)  # a real user pointer exists, so preservation is exercised
        out = self.runner(FakeSim()).run(20)
        self.assertEqual(out["source"], "new_run")
        after = self._snapshot()
        self.assertEqual(after["meta"]["job_id"], "user")
        self.assertEqual(after["report"], before["report"])
        self.assertEqual(after, before)
        self.assertEqual(self.r.kv.get(f"deck:sim:job:{svc._rid(self.deck)}"), ptr)

    def test_failed_run_keeps_saved_report(self):
        before = self._presave()[0]
        sim = FakeSim(states=["failed"])
        with self.assertRaises(ToolError):
            self.runner(sim).run(20)
        self.assertEqual(self._snapshot(), before)

    def test_job_record_has_no_report(self):
        out = self.runner(FakeSim()).run(20)
        raw = self.r.kv.get(svc.run_key(out["job_id"]))
        self.assertIsNotNone(raw)
        self.assertNotIn("report", json.loads(raw))

    def test_metrics_passed_and_saved_review_never_read(self):
        sentinel = {"sentinel": True}
        with patch.object(svc, "_static_review", side_effect=AssertionError("no")), \
                patch("src.server.domain.analytics.review_doc.load_latest", side_effect=AssertionError("no")), \
                patch("src.server.domain.analytics.sim_diff.build_sim_comparison", return_value={}) as b:
            self.runner(FakeSim()).run(20, metrics=sentinel)
        self.assertIs(b.call_args[0][2], sentinel)


class TestSaved(Base):
    def _save(self, games, pod=True):
        rep = make_report()
        rep["summary"] = {"games": games}
        if pod:
            rep["params"] = {"opponent_mode": "standard", "pod": {"saved_at": svc.get_pod(self.r)["updated_at"]}}
        ss.save_completed(self.r, self.deck, {"job_id": "old", "status": "done", "report": rep})

    def test_saved_fresh_enough_is_reused(self):
        self._save(20)
        sim = FakeSim()
        out = self.runner(sim).run(20)
        self.assertEqual((out["source"], out["games"]), ("saved", 20))
        self.assertTrue(out["saved_at"])
        self.assertEqual(sim.payloads, [])

    def test_saved_non_standard_or_other_pod_runs_new(self):
        self._save(20, pod=False)
        self.assertEqual(self.runner(FakeSim()).run(20)["source"], "new_run")

    def test_saved_with_too_few_games_runs_new(self):
        self._save(5)
        self.assertEqual(self.runner(FakeSim()).run(20)["source"], "new_run")

    def test_stale_saved_runs_new(self):
        self._save(20)
        changed = copy.deepcopy(self.deck)
        changed["cards"][0]["name"] = "Something Else"
        put_decks(self.r, changed, user_deck(1), user_deck(2), user_deck(3))
        out = SimRunner(self.r, changed, client=FakeSim(), sleep=lambda s: None).run(20)
        self.assertEqual(out["source"], "new_run")


class TestErrors(Base):
    def kind(self, runner, games=20):
        with self.assertRaises(ToolError) as cm:
            runner.run(games)
        return cm.exception.kind

    def test_busy_run_in_flight(self):
        svc._INFLIGHT[svc._rid(self.deck)] = "other"
        self.assertEqual(self.kind(self.runner(FakeSim())), "sim_busy")

    def test_daemon_busy_409(self):
        self.assertEqual(self.kind(self.runner(FakeSim(busy=True))), "sim_busy")

    def test_no_pod_is_not_configured(self):
        svc.clear_pod(self.r)
        self.assertEqual(self.kind(self.runner(FakeSim())), "sim_not_configured")

    def test_daemon_down(self):
        self.assertEqual(self.kind(self.runner(FakeSim(down=True))), "sim_unavailable")

    def test_timeout(self):
        sim = FakeSim(states=["running"])
        self.assertEqual(self.kind(self.runner(sim, max_wait_s=-1)), "sim_timeout")
        self.assertEqual(svc._INFLIGHT, {})

    def test_failed_run(self):
        self.assertEqual(self.kind(self.runner(FakeSim(states=["failed"]))), "sim_failed")


class TestRegistry(Base):
    def test_tool_spec_and_dispatch(self):
        self.assertIn("sim_run", FACTORIES)
        t = FACTORIES["sim_run"]()
        self.assertEqual(t.max_calls, 1)
        self.assertEqual(t.parameters["properties"]["games"]["enum"], [5, 20])
        ctx = ToolContext(deck=self.deck, sim=self.runner(FakeSim()))
        self.assertIsNotNone(build_registry(["sim_run"], ctx))
        self.assertEqual(ctx.sim.run(20)["source"], "new_run")

    def test_no_sim_in_context(self):
        with self.assertRaises(ToolError) as cm:
            FACTORIES["sim_run"]().handler({"games": 5}, ToolContext(deck=self.deck))
        self.assertEqual(cm.exception.kind, "unavailable")


if __name__ == "__main__":
    unittest.main()
