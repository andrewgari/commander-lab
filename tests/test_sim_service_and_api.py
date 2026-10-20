"""SM-3: sim_service (opponent pool, validation, background run, Oracle review + fallback) and the /sim API."""
import copy
import json
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from analytics import sim_client as sc
from analytics import sim_service as svc
from analytics import sim_store as ss
from analytics.oracle_client import OracleUnavailable
from app import app
from tests.test_sim_store_and_diff import FakeRedis, make_deck, make_report, make_review

RID = "archidekt:417707"


def user_deck(i):
    d = make_deck()
    d.update(id=900 + i, registry_id=f"archidekt:{900 + i}", name=f"Opp Deck {i}", commanders=["Kaalia of the Vast"])
    return d


class FakeSim:
    """Stands in for sim_client.SimClient."""

    def __init__(self, report=None, states=None, busy=False, down=False, fail_error=None):
        self.report = report if report is not None else make_report()
        self.states = list(states or ["running", "done"])
        self.busy, self.down, self.fail_error = busy, down, fail_error
        self.payloads, self.polls = [], 0

    def list_opponents(self):
        if self.down:
            raise sc.SimUnavailable("down")
        return {"opponents": [{"id": "precon_a", "name": "Precon A"}, {"id": "precon_b", "name": "Precon B"},
                              {"id": "precon_c", "name": "Precon C"}, {"id": "Explorers of the Deep", "name": "Explorers of the Deep"}]}

    def start_job(self, payload):
        if self.down:
            raise sc.SimUnavailable("down")
        if self.busy:
            raise sc.SimBusy("busy", job_id="x")
        self.payloads.append(payload)
        return {"job_id": "daemon-1", "status": "queued"}

    def get_job(self, job_id):
        self.polls += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == "failed":
            return {"job_id": job_id, "status": "failed", "error": self.fail_error or "forge crashed"}
        if state == "done":
            return {"job_id": job_id, "status": "done", "games_completed": 20, "games_total": 20, "report": copy.deepcopy(self.report)}
        return {"job_id": job_id, "status": state, "games_completed": 5, "games_total": 20}


class FakeOracle:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.fail:
            raise OracleUnavailable("down")
        return self.reply(messages) if callable(self.reply) else self.reply


def good_reply(messages):
    payload = json.loads(messages[-1]["content"].split("```json\n")[1].split("\n```")[0])
    body = {"schema_version": "1", "fingerprint": payload["fingerprint"],
            "headline": "The deck stumbled on mana more than the paper read suggested.",
            "notes_response": "You asked about mana: starvation at T5 was real.",
            "tempo_and_mana_read": "Commander arrived late.", "card_performance_read": "Helm of the Host carried.",
            "actionable_tweaks": ["Add one land.", "Swap Strionic Resonator for cheap ramp."]}
    return "Here you go.\n```json\n" + json.dumps(body) + "\n```"


def put_decks(r, *decks):
    r.set("decks", json.dumps(list(decks)))


def run_sync(r, deck, params, sim, oracle):
    """begin_run + run_job inline (no thread, no sleeping)."""
    job = svc.begin_run(r, deck, params, sim)
    with patch.object(svc, "_static_review", return_value=make_review()):
        return svc.run_job(r, job, deck, sim, lambda: oracle, poll_interval=0, sleep=lambda s: None)


class Base(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        put_decks(self.r, self.deck, user_deck(1), user_deck(2), user_deck(3))
        svc.save_pod(self.r, ["user_901"], FakeSim())  # Standard needs a user-defined pod; there is no default
        self.addCleanup(lambda: svc._INFLIGHT.clear())


class TestValidation(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(svc.validate_params({}), {"opponent_mode": "standard", "games": 20, "opponents": [], "notes": "",
                                                              "focus": [], "focus_snapshot": []})

    def test_bad_values(self):
        for bad in ({"opponent_mode": "wild"}, {"games": 0}, {"games": 101}, {"games": "20"}, {"games": True},
                    {"notes": "x" * 1001}, {"notes": 5}, {"opponent_mode": "specific", "opponents": []},
                    {"opponent_mode": "specific", "opponents": ["a", "b", "c", "d", "e", "f"]}, {"opponents": "a"}):
            with self.assertRaises(svc.SimParamError, msg=str(bad)):
                svc.validate_params(bad)

    def test_ok_values(self):
        for g in (1, 20, 60, 100):
            self.assertEqual(svc.validate_params({"games": g})["games"], g)
        p = svc.validate_params({"opponent_mode": "specific", "opponents": ["a", "b", "c"], "notes": "  hi  "})
        self.assertEqual((p["opponents"], p["notes"]), (["a", "b", "c"], "hi"))
        self.assertEqual(svc.validate_params({"opponent_mode": "rapid1v1", "opponents": ["a"]})["opponents"], ["a"])
        self.assertEqual(len(svc.validate_params({"notes": "x" * 1000})["notes"]), 1000)
        
        # Standard overrides
        # Standard ignores client-supplied opponents: the saved pod is authoritative
        p_std = svc.validate_params({"opponent_mode": "standard", "opponents": ["a", "b", "c"]})
        self.assertEqual(p_std["opponents"], [])


class TestPool(Base):
    def test_pool_tags_sources_and_excludes_target(self):
        pool = svc.build_opponent_pool(self.r, FakeSim(), self.deck)
        by = {o["id"]: o["source"] for o in pool["opponents"]}
        self.assertEqual(by["precon_a"], "precon")
        self.assertEqual(by["user_901"], "user_deck")
        self.assertNotIn("user_417707", by)
        self.assertIsNone(pool["daemon_error"])

    def test_pool_degrades_when_daemon_down(self):
        pool = svc.build_opponent_pool(self.r, FakeSim(down=True), self.deck)
        self.assertEqual({o["source"] for o in pool["opponents"]}, {"user_deck"})
        self.assertIn("down", pool["daemon_error"])


class TestRunModes(Base):
    def test_standard_mode_empty_pod_blocks_run(self):
        sim = FakeSim()
        svc.clear_pod(self.r)
        with self.assertRaises(svc.SimParamError) as cm:
            svc.begin_run(self.r, self.deck, {"opponent_mode": "standard"}, sim)
        self.assertIn("No Standard pod", str(cm.exception))
        self.assertEqual(sim.payloads, [])
        self.assertEqual(svc._INFLIGHT, {})

    def test_standard_mode_uses_exactly_saved_pod_and_records_it(self):
        sim = FakeSim()
        svc.save_pod(self.r, ["user_902", "precon_b"], sim)
        job = run_sync(self.r, self.deck, {"opponent_mode": "standard", "opponents": ["user_901"]}, sim, FakeOracle(good_reply))
        self.assertEqual(job["status"], "done", job.get("error"))
        p = sim.payloads[0]
        self.assertEqual((p["opponent_mode"], p["games"], p["deck_id"]), ("standard", 20, 417707))
        self.assertEqual(p["opponents"], ["user_902", "precon_b"])
        self.assertEqual(set(p["opponent_decks"]), {"user_902"})
        self.assertEqual([d["id"] for d in job["params"]["pod"]["decks"]], ["user_902", "precon_b"])
        doc = ss.load_latest(self.r, self.deck)
        self.assertEqual([d["name"] for d in doc["report"]["params"]["pod"]["decks"]], ["Opp Deck 2", "Precon B"])

    def test_standard_mode_flags_deleted_deck_before_run(self):
        sim = FakeSim()
        svc.save_pod(self.r, ["user_901", "user_902"], sim)
        put_decks(self.r, self.deck, user_deck(1))  # deck 902 deleted
        pod = svc.resolve_pod(self.r, self.deck, sim)
        self.assertEqual(pod["status"], "invalid")
        self.assertEqual([d["id"] for d in pod["decks"]], ["user_901", "user_902"])
        with self.assertRaises(svc.SimParamError):
            svc.begin_run(self.r, self.deck, {"opponent_mode": "standard"}, sim)
        self.assertEqual(sim.payloads, [])

    def test_save_pod_validation(self):
        sim = FakeSim()
        for bad in ([], ["user_901"] * 2, ["nope"], "x", [f"user_{900 + i}" for i in range(1, 7)]):
            with self.assertRaises(svc.SimParamError, msg=str(bad)):
                svc.save_pod(self.r, bad, sim)
        self.assertEqual([d["id"] for d in svc.get_pod(self.r)["decks"]], ["user_901"])  # unchanged by bad saves
        svc.clear_pod(self.r)
        self.assertIsNone(svc.get_pod(self.r))
        self.assertEqual(svc.resolve_pod(self.r, self.deck, sim)["status"], "empty")

    def test_no_hardcoded_default_pod_in_source(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for f in ("analytics/sim_service.py", "static/js/deck_sim.js", "app.py"):
            txt = (root / f).read_text()
            for name in ("Cavalry Charge", "Endless Punishment"):
                self.assertNotIn(name, txt, f)

    def test_specific_mode_exports_user_decks(self):
        sim = FakeSim()
        params = {"opponent_mode": "specific", "opponents": ["precon_a", "user_901", "user_902"], "games": 60}
        job = run_sync(self.r, self.deck, params, sim, FakeOracle(good_reply))
        self.assertEqual(job["status"], "done", job.get("error"))
        p = sim.payloads[0]
        self.assertEqual(p["opponents"], ["precon_a", "user_901", "user_902"])
        self.assertEqual(set(p["opponent_decks"]), {"user_901", "user_902"})
        self.assertIn("Name=user_901", p["opponent_decks"]["user_901"])
        self.assertEqual(p["games"], 60)

    def test_specific_mode_rejects_unexportable_user_deck(self):
        bad = user_deck(4)
        bad["cards"] = bad["cards"][:10]
        put_decks(self.r, self.deck, user_deck(1), bad)
        with self.assertRaises(svc.SimParamError):
            svc.begin_run(self.r, self.deck, {"opponent_mode": "specific", "opponents": ["precon_a", "user_901", "user_904"]}, FakeSim())
        self.assertEqual(svc._INFLIGHT, {})
        
    def test_standard_mode_custom_opponents(self):
        sim = FakeSim()
        svc.save_pod(self.r, ["user_901", "Explorers of the Deep", "user_902"], sim)
        params = {"opponent_mode": "standard"}
        job = run_sync(self.r, self.deck, params, sim, FakeOracle(good_reply))
        self.assertEqual(job["status"], "done", job.get("error"))
        p = sim.payloads[0]
        self.assertEqual(p["opponent_mode"], "standard")
        self.assertEqual(p["opponents"], ["user_901", "Explorers of the Deep", "user_902"])
        self.assertEqual(set(p["opponent_decks"]), {"user_901", "user_902"})
        self.assertIn("Name=user_901", p["opponent_decks"]["user_901"])

    def test_random_mode_sends_pool_and_per_game_selection_is_reported(self):
        sim = FakeSim()
        job = run_sync(self.r, self.deck, {"opponent_mode": "random"}, sim, FakeOracle(good_reply))
        self.assertEqual(job["status"], "done")
        p = sim.payloads[0]
        self.assertEqual(p["opponent_mode"], "random")
        self.assertTrue({"precon_a", "user_901"} <= set(p["opponent_pool"]))
        self.assertEqual(set(p["opponent_decks"]), {"user_901", "user_902", "user_903"})
        doc = ss.load_latest(self.r, self.deck)
        pod = doc["report"]["comparison"]["pod_breakdown"]
        self.assertEqual(pod["opponent_mode"], "random")
        decks_seen = {o["deck"] for g in pod["games"] for o in g["opponents"]}
        self.assertEqual(len(decks_seen), 4)  # different opponents per game
        self.assertEqual(doc["report"]["params"]["opponent_mode"], "random")

    def test_notes_reach_daemon_and_oracle_and_saved_params(self):
        sim, oracle = FakeSim(), FakeOracle(good_reply)
        run_sync(self.r, self.deck, {"notes": "Testing whether I stumble on mana"}, sim, oracle)
        self.assertEqual(sim.payloads[0]["notes"], "Testing whether I stumble on mana")
        messages, kw = oracle.calls[0]
        self.assertIn("Testing whether I stumble on mana", messages[-1]["content"])
        self.assertEqual((kw["tools"], kw["tool_choice"]), ([], "none"))
        doc = ss.load_latest(self.r, self.deck)
        self.assertEqual(doc["report"]["params"]["notes"], "Testing whether I stumble on mana")


class TestReview(Base):
    def test_oracle_success(self):
        run_sync(self.r, self.deck, {}, FakeSim(), FakeOracle(good_reply))
        rv = ss.load_latest(self.r, self.deck)["report"]["oracle_review"]
        self.assertEqual(rv["source"], "oracle")
        self.assertTrue(rv["headline"].startswith("The deck stumbled"))
        self.assertEqual(len(rv["actionable_tweaks"]), 2)

    def test_oracle_offline_falls_back(self):
        job = run_sync(self.r, self.deck, {"notes": "n"}, FakeSim(), FakeOracle(fail=True))
        self.assertEqual((job["status"], job["review_source"]), ("done", "fallback"))
        rv = ss.load_latest(self.r, self.deck)["report"]["oracle_review"]
        self.assertEqual(rv["source"], "fallback")
        self.assertTrue(rv["headline"])
        self.assertIn("35%", rv["tempo_and_mana_read"])  # t5_mana_starvation_rate 0.35 from the run
        self.assertTrue(2 <= len(rv["actionable_tweaks"]) <= 4)
        self.assertIn("Strionic Resonator", " ".join(rv["actionable_tweaks"]))

    def test_fallback_never_cuts_pet_cards(self):
        review = make_review()
        run_sync(self.r, self.deck, {}, FakeSim(), FakeOracle(fail=True))
        rv = ss.load_latest(self.r, self.deck)["report"]
        for t in rv["oracle_review"]["actionable_tweaks"]:
            if "Vilis" in t:
                self.assertIn("Pet card", t)
        self.assertIsNotNone(review)

    def test_invalid_reply_falls_back(self):
        for reply in ("no json here", "```json\n{\"schema_version\": \"1\"}\n```"):
            self.setUp()
            job = run_sync(self.r, self.deck, {}, FakeSim(), FakeOracle(reply))
            self.assertEqual(job["review_source"], "fallback")
            self.assertIn("invalid", ss.load_latest(self.r, self.deck)["report"]["oracle_review"]["note"])

    def test_wrong_fingerprint_falls_back(self):
        def bad(messages):
            return good_reply(messages).replace(json.loads(good_reply(messages).split("```json\n")[1].split("\n```")[0])["fingerprint"], "nope")
        job = run_sync(self.r, self.deck, {}, FakeSim(), FakeOracle(bad))
        self.assertEqual(job["review_source"], "fallback")

    def test_invented_numbers_are_stripped(self):
        def reply(messages):
            data = json.loads(good_reply(messages).split("```json\n")[1].split("\n```")[0])
            data["tempo_and_mana_read"] = "Commander landed on turn 5. A made up 73.3141 percent figure appeared."
            data["actionable_tweaks"] = ["Add 17 lands.", "Add one land.", "Trim the top end."]
            return "```json\n" + json.dumps(data) + "\n```"
        run_sync(self.r, self.deck, {}, FakeSim(), FakeOracle(reply))
        rv = ss.load_latest(self.r, self.deck)["report"]["oracle_review"]
        self.assertEqual(rv["source"], "oracle")
        self.assertNotIn("73.3141", rv["tempo_and_mana_read"])
        self.assertIn("Commander landed", rv["tempo_and_mana_read"])
        self.assertEqual(rv["actionable_tweaks"], ["Add one land.", "Trim the top end."])


class TestPreservesPreviousReport(Base):
    def _seed(self):
        run_sync(self.r, self.deck, {"notes": "first"}, FakeSim(), FakeOracle(good_reply))
        return self.r.get(ss.sim_key(RID))

    def test_failed_daemon_job_keeps_previous(self):
        before = self._seed()
        job = run_sync(self.r, self.deck, {"notes": "second"}, FakeSim(states=["running", "failed"]), FakeOracle(good_reply))
        self.assertEqual(job["status"], "failed")
        self.assertIn("forge crashed", job["error"])
        self.assertEqual(self.r.get(ss.sim_key(RID)), before)
        self.assertEqual(svc._INFLIGHT, {})

    def test_daemon_unreachable_mid_run_keeps_previous(self):
        before = self._seed()

        class Dies(FakeSim):
            def get_job(self, job_id):
                raise sc.SimUnavailable("gone")
        job = run_sync(self.r, self.deck, {}, Dies(), FakeOracle(good_reply))
        self.assertEqual((job["status"], job["error_kind"]), ("failed", "sim_unavailable"))
        self.assertEqual(self.r.get(ss.sim_key(RID)), before)

    def test_done_without_report_keeps_previous(self):
        before = self._seed()
        sim = FakeSim()
        sim.get_job = lambda jid: {"status": "done"}
        job = run_sync(self.r, self.deck, {}, sim, FakeOracle(good_reply))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.r.get(ss.sim_key(RID)), before)

    def test_unchanged_while_job_in_progress_then_replaced_on_success(self):
        before = self._seed()
        seen = {}

        class Watch(FakeSim):
            def get_job(inner, job_id):
                seen.setdefault("saved", []).append(self.r.get(ss.sim_key(RID)))
                seen.setdefault("job", []).append(svc.active_job(self.r, self.deck)["status"])
                return super().get_job(job_id)
        job = run_sync(self.r, self.deck, {"notes": "second"}, Watch(states=["running", "running", "done"]), FakeOracle(good_reply))
        self.assertEqual(job["status"], "done")
        self.assertTrue(all(s == before for s in seen["saved"]))
        self.assertEqual(set(seen["job"]), {"running"})
        after = ss.load_latest(self.r, self.deck)
        self.assertEqual(after["report"]["params"]["notes"], "second")
        self.assertNotEqual(self.r.get(ss.sim_key(RID)), before)

    def test_second_run_blocked_while_first_active(self):
        job = svc.begin_run(self.r, self.deck, {}, FakeSim())
        with self.assertRaises(svc.SimRunBusy) as cm:
            svc.begin_run(self.r, self.deck, {}, FakeSim())
        self.assertEqual(cm.exception.job_id, job["job_id"])
        svc.release(RID, job["job_id"])

    def test_daemon_busy_leaves_nothing_reserved(self):
        with self.assertRaises(sc.SimBusy):
            svc.begin_run(self.r, self.deck, {}, FakeSim(busy=True))
        self.assertEqual(svc._INFLIGHT, {})


class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        self.sim = FakeSim()
        self.oracle = FakeOracle(good_reply)
        real_client = sc.SimClient
        for p in (patch("app.r", self.r), patch("app._find_deck_any", return_value=self.deck),
                  patch("app._oracle_client", return_value=self.oracle),
                  patch.object(sc, "SimClient", lambda *a, **k: self.sim),
                  patch.object(svc, "_static_review", return_value=make_review()),
                  patch.object(svc, "POLL_INTERVAL_S", 0.01)):
            p.start()
            self.addCleanup(p.stop)
        self.assertIsNot(real_client, sc.SimClient)

    def wait_job(self, job_id, want=("done", "failed")):
        for _ in range(200):
            body = self.client.get(f"/api/decks/{RID}/sim/jobs/{job_id}").json()
            if body["status"] in want:
                return body
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_get_empty(self):
        body = self.client.get(f"/api/decks/{RID}/sim").json()
        self.assertEqual((body["success"], body["exists"], body["sim"]), (True, False, None))
        self.assertEqual(body["meta"], {"exists": False, "stale": False, "stale_reason": None, "created_at": None})
        self.assertTrue({"precon", "user_deck"} <= {o["source"] for o in body["opponent_pool"]})

    def test_get_unknown_deck_404(self):
        with patch("app._find_deck_any", return_value=None):
            self.assertEqual(self.client.get("/api/decks/nope/sim").status_code, 404)
            self.assertEqual(self.client.post("/api/decks/nope/sim/run", json={}).status_code, 404)

    def test_run_poll_and_get(self):
        res = self.client.post(f"/api/decks/{RID}/sim/run", json={"notes": "mana test", "games": 20})
        self.assertEqual(res.status_code, 202)
        job_id = res.json()["job_id"]
        self.assertTrue(res.json()["success"])
        job = self.wait_job(job_id)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual((job["games_completed"], job["games_total"]), (20, 20))
        body = self.client.get(f"/api/decks/{RID}/sim").json()
        self.assertTrue(body["exists"])
        self.assertTrue(body["meta"]["created_at"])
        self.assertFalse(body["meta"]["stale"])
        self.assertEqual(body["sim"]["report"]["oracle_review"]["source"], "oracle")

    def test_stale_after_decklist_change(self):
        self.client.post(f"/api/decks/{RID}/sim/run", json={})
        for _ in range(200):
            if self.client.get(f"/api/decks/{RID}/sim").json()["exists"]:
                break
            time.sleep(0.02)
        changed = copy.deepcopy(self.deck)
        changed["cards"].append({"name": "Brand New Card", "quantity": 1})
        with patch("app._find_deck_any", return_value=changed):
            meta = self.client.get(f"/api/decks/{RID}/sim").json()["meta"]
        self.assertTrue(meta["stale"])
        self.assertTrue(meta["stale_reason"])

    def test_validation_422(self):
        for body in ({"games": 7000}, {"opponent_mode": "specific", "opponents": []}, {"notes": "x" * 1001}):
            self.assertEqual(self.client.post(f"/api/decks/{RID}/sim/run", json=body).status_code, 422, body)

    def test_409_when_already_running(self):
        self.sim.states = ["running"]  # never finishes
        first = self.client.post(f"/api/decks/{RID}/sim/run", json={})
        self.assertEqual(first.status_code, 202)
        second = self.client.post(f"/api/decks/{RID}/sim/run", json={})
        self.assertEqual(second.status_code, 409)
        job = self.client.get(f"/api/decks/{RID}/sim/jobs/{first.json()['job_id']}").json()
        self.assertIn(job["status"], ("queued", "running"))
        self.assertEqual(self.client.get(f"/api/decks/{RID}/sim").json()["running_job"]["job_id"], first.json()["job_id"])
        self.sim.states = ["done"]
        self.wait_job(first.json()["job_id"])

    def test_409_when_daemon_busy_and_503_when_down(self):
        self.sim.busy = True
        self.assertEqual(self.client.post(f"/api/decks/{RID}/sim/run", json={}).status_code, 409)
        self.sim.busy, self.sim.down = False, True
        self.assertEqual(self.client.post(f"/api/decks/{RID}/sim/run", json={}).status_code, 503)

    def test_failed_job_reports_error_and_keeps_saved(self):
        first = self.client.post(f"/api/decks/{RID}/sim/run", json={"notes": "first"}).json()["job_id"]
        self.wait_job(first)
        before = self.r.get(ss.sim_key(RID))
        self.sim.states = ["failed"]
        second = self.client.post(f"/api/decks/{RID}/sim/run", json={}).json()["job_id"]
        job = self.wait_job(second)
        self.assertEqual(job["status"], "failed")
        self.assertIn("forge crashed", job["error"])
        self.assertEqual(self.r.get(ss.sim_key(RID)), before)
        self.assertTrue(self.client.get(f"/api/decks/{RID}/sim").json()["exists"])

    def test_job_404(self):
        self.assertEqual(self.client.get(f"/api/decks/{RID}/sim/jobs/missing").status_code, 404)

    def test_oracle_offline_via_api_still_saves(self):
        self.oracle.fail = True
        job = self.wait_job(self.client.post(f"/api/decks/{RID}/sim/run", json={}).json()["job_id"])
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["review_source"], "fallback")


if __name__ == "__main__":
    unittest.main()
