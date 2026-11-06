"""PS-3: stored personality scores (analytics/personality_store.py) + API.

Each deck's PersonalityScores is stored at personality:deck:{registry_id}
with the input hash it was computed from. A pass over every deck skips
decks whose hash is unchanged, so it's cheap to run after every sync and
journal save; one deck failing records status=error and the pass moves on.

Run: python -m unittest tests.test_personality_store
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import journal  # noqa: E402
from src.server.domain.analytics import personality as ps  # noqa: E402
from src.server.domain.analytics import personality_store as store  # noqa: E402
from src.server.domain.analytics.interactions import combos as combos_mod  # noqa: E402
from src.server.domain.analytics.interactions import service  # noqa: E402
from tests.test_bracket_worker import FakeRedis, deck, put_decks  # noqa: E402
from tests.test_personality_profile import scores as make_scores  # noqa: E402


def phys(rid, name, cards=("Sol Ring", "Island")):
    d = deck(rid, name, cards)
    d["status"] = "physical"
    return d


class Scorer:
    """Stand-in for personality.score_deck: records calls, can fail per deck."""

    def __init__(self, fail_for=(), values=(51, 31, 39, 76, 79)):
        self.calls = []
        self.fail_for = set(fail_for)
        self.values = values

    def __call__(self, r, d):
        self.calls.append(d["registry_id"])
        if d["registry_id"] in self.fail_for:
            raise RuntimeError("boom")
        sc = make_scores(*self.values, journal=journal.load_journal(r, d) is not None)
        return ps.PersonalityScores.model_validate(sc)


class InputHash(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.d = phys("archidekt:1", "One")

    def h(self, d=None):
        return store.current_input_hash(self.r, d or self.d)

    def test_stable(self):
        self.assertEqual(self.h(), self.h())

    def test_decklist_changes_it(self):
        self.assertNotEqual(self.h(), self.h(phys("archidekt:1", "One", ("Sol Ring", "Forest"))))

    def test_journal_changes_it(self):
        before = self.h()
        journal.save_journal(self.r, self.d, {"novelties": "a weird engine"})
        self.assertNotEqual(before, self.h())

    def test_graph_rebuild_changes_it(self):
        before = self.h()
        self.r.set(service.DECK_GRAPH_PREFIX + "archidekt:1", json.dumps({"built_at": "2026-10-15T08:00:00"}))
        self.assertNotEqual(before, self.h())

    def test_spellbook_refresh_changes_it(self):
        before = self.h()
        key = combos_mod.decklist_key(self.d["commanders"], combos_mod._deck_names(self.d))
        self.r.set(key, json.dumps({"fetched_at": "2026-10-12T00:00:00", "included": []}))
        self.assertNotEqual(before, self.h())

    def test_version_changes_it(self):
        before = self.h()
        with patch.object(ps, "PERSONALITY_VERSION", 99):
            self.assertNotEqual(before, self.h())


class Recompute(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.a = phys("archidekt:1", "One")
        self.b = phys("archidekt:2", "Two")
        put_decks(self.r, [self.a, self.b])

    def test_first_pass_stores_every_deck(self):
        sc = Scorer()
        stats = store.recompute_all(self.r, scorer=sc)
        self.assertEqual((stats["computed"], stats["skipped"], stats["failed"]), (2, 0, 0))
        rec = store.load_record(self.r, "archidekt:1")
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["scores"]["melvin"]["score"], 79)
        self.assertEqual(rec["input_hash"], store.current_input_hash(self.r, self.a))
        self.assertTrue(rec["computed_at"])
        self.assertEqual(rec["version"], ps.PERSONALITY_VERSION)

    def test_hash_skip(self):
        store.recompute_all(self.r, scorer=Scorer())
        sc = Scorer()
        stats = store.recompute_all(self.r, scorer=sc)
        self.assertEqual(sc.calls, [])
        self.assertEqual((stats["computed"], stats["skipped"]), (0, 2))

    def test_only_changed_deck_recomputed(self):
        store.recompute_all(self.r, scorer=Scorer())
        journal.save_journal(self.r, self.b, {"novelties": "x"})
        sc = Scorer()
        store.recompute_all(self.r, scorer=sc)
        self.assertEqual(sc.calls, ["archidekt:2"])
        self.assertTrue(store.load_record(self.r, "archidekt:2")["scores"]["journal_used"])

    def test_decklist_change_after_sync(self):
        store.recompute_all(self.r, scorer=Scorer())
        put_decks(self.r, [self.a, phys("archidekt:2", "Two", ("Sol Ring", "Mountain"))])
        sc = Scorer()
        store.recompute_all(self.r, scorer=sc)
        self.assertEqual(sc.calls, ["archidekt:2"])

    def test_one_deck_failure_isolated(self):
        stats = store.recompute_all(self.r, scorer=Scorer(fail_for={"archidekt:1"}))
        self.assertEqual((stats["computed"], stats["failed"]), (1, 1))
        bad = store.load_record(self.r, "archidekt:1")
        self.assertEqual(bad["status"], "error")
        self.assertIn("boom", bad["error"])
        self.assertIsNone(bad["scores"])
        self.assertEqual(store.load_record(self.r, "archidekt:2")["status"], "ok")

    def test_failure_keeps_last_good_and_is_not_retried_until_change(self):
        store.recompute_all(self.r, scorer=Scorer())
        journal.save_journal(self.r, self.a, {"novelties": "x"})
        store.recompute_all(self.r, scorer=Scorer(fail_for={"archidekt:1"}))
        rec = store.load_record(self.r, "archidekt:1")
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["scores"]["melvin"]["score"], 79)  # last good kept
        sc = Scorer()
        store.recompute_all(self.r, scorer=sc)
        self.assertEqual(sc.calls, [])  # same failed input: not retried every pass
        store.request(self.r, "archidekt:1")
        store.recompute_all(self.r, scorer=sc)
        self.assertEqual(sc.calls, ["archidekt:1"])
        self.assertEqual(store.load_record(self.r, "archidekt:1")["status"], "ok")

    def test_unreadable_inputs_do_not_stop_the_pass(self):
        self.r.set(journal.KEY_PREFIX + "archidekt:1", "{not json")
        stats = store.recompute_all(self.r, scorer=Scorer())
        self.assertEqual(stats["computed"] + stats["failed"], 2)
        self.assertEqual(store.load_record(self.r, "archidekt:2")["status"], "ok")

    def test_store_write_failure_is_isolated(self):
        class Flaky(FakeRedis):
            def set(self, key, value, **kw):
                if key == store.KEY_PREFIX + "archidekt:1":
                    raise ConnectionError("write failed")
                return super().set(key, value, **kw)
        r = Flaky()
        put_decks(r, [self.a, self.b])
        stats = store.recompute_all(r, scorer=Scorer())
        self.assertEqual(store.load_record(r, "archidekt:2")["status"], "ok")
        self.assertEqual(stats["failed"], 1)

    def test_redis_down_reading_decks(self):
        class Down(FakeRedis):
            def get(self, key):
                raise ConnectionError("down")
        stats = store.recompute_all(Down(), scorer=Scorer())
        self.assertIn("error", stats)

    def test_heartbeat(self):
        store.recompute_all(self.r, scorer=Scorer())
        hb = json.loads(self.r.get(store.HEARTBEAT_KEY))
        self.assertEqual(hb["computed"], 2)

    def test_load_all(self):
        store.recompute_all(self.r, scorer=Scorer(fail_for={"archidekt:2"}))
        scored, failed, not_scored = store.load_all(self.r, [self.a, self.b, phys("archidekt:3", "Three")])
        self.assertEqual([e["id"] for e in scored], ["archidekt:1"])
        self.assertEqual([f["id"] for f in failed], ["archidekt:2"])
        self.assertEqual([d["registry_id"] for d in not_scored], ["archidekt:3"])


class WorkerHook(unittest.TestCase):
    """The bracket worker process also runs the personality pass each tick."""

    def test_worker_tick_runs_personality(self):
        from src.server.domain.analytics import bracket_worker as bw

        r = FakeRedis()
        put_decks(r, [phys("archidekt:1", "One")])
        sc = Scorer()

        async def evaluator(r_, d):
            return {"estimated_bracket": 3}
        with patch.object(store, "_default_scorer", sc):
            bw.run_tick(r, evaluator=evaluator)
        self.assertEqual(sc.calls, ["archidekt:1"])
        self.assertEqual(store.load_record(r, "archidekt:1")["status"], "ok")

    def test_personality_failure_does_not_break_bracket_tick(self):
        from src.server.domain.analytics import bracket_worker as bw

        r = FakeRedis()
        put_decks(r, [phys("archidekt:1", "One")])

        async def evaluator(r_, d):
            return {"estimated_bracket": 3}
        with patch.object(store, "recompute_all", side_effect=RuntimeError("kaput")):
            stats = bw.run_tick(r, evaluator=evaluator)
        self.assertEqual(stats["evaluated"], 1)
        self.assertEqual(bw.load_result(r, "archidekt:1")["status"], "ok")


class ControlDeckEndToEnd(unittest.TestCase):
    """Real PS-2 scorer output for the Doctor/Rose control deck through the store, API and profile."""

    def test_control_deck(self):
        from fastapi.testclient import TestClient

        import src.server.app.main as app_mod
        from tests.test_personality import load_real, score_real

        real = load_real("5620189")
        d = dict(real["deck"], status="physical")
        r = FakeRedis()
        put_decks(r, [d])
        store.recompute_all(r, scorer=lambda r_, deck_: score_real("5620189"))
        with patch.object(app_mod, "r", r):
            client = TestClient(app_mod.app)
            p = client.get("/api/decks/archidekt:5620189/personality").json()["personality"]
            prof = client.get("/api/profile/personality").json()["profile"]
        self.assertEqual([p[a]["score"] for a in ("timmy", "johnny", "spike", "vorthos", "melvin")],
                         [51, 31, 39, 76, 79])
        t1 = next(e for e in p["timmy"]["evidence"] if e["signal"] == "T1")
        self.assertEqual(t1["weight"], 30)
        self.assertIsNone(next(e for e in p["timmy"]["evidence"] if e["signal"] == "journal")["weight"])
        self.assertTrue(p["computed_at"])
        self.assertEqual(prof["average"], {"timmy": 51, "johnny": 31, "spike": 39, "vorthos": 76, "melvin": 79})
        self.assertEqual((prof["dominant"], prof["low_data"], prof["decks_scored"]), ("melvin", True, 1))
        self.assertEqual(prof["decks"][0]["groups"], ["universe"])  # WHO 41/55


class Routes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import src.server.app.main as app_mod
        from src.server.app.main import app

        self.client = TestClient(app)
        self.fake = FakeRedis()
        self.ctrl = phys("archidekt:5620189", "The Laws of Time are Mine!!")
        self.ctrl["commanders"] = ["The Tenth Doctor", "Rose Tyler"]
        self.ctrl["id"] = 5620189
        self.decks = [self.ctrl, phys("archidekt:2", "Two"), phys("archidekt:3", "Three")]
        self.decks.append(dict(deck("archidekt:4", "Draft"), status="testing"))
        put_decks(self.fake, self.decks)
        self.p = patch.object(app_mod, "r", self.fake)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_deck_not_scored_is_404(self):
        res = self.client.get("/api/decks/archidekt:5620189/personality")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "not_scored")

    def test_unknown_deck(self):
        res = self.client.get("/api/decks/nope/personality")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "deck_not_found")

    def test_deck_scores(self):
        store.recompute_all(self.fake, scorer=Scorer())
        for ref in ("archidekt:5620189", "5620189", "The Laws of Time are Mine!!"):
            res = self.client.get(f"/api/decks/{ref}/personality")
            self.assertEqual(res.status_code, 200, ref)
            body = res.json()
            self.assertEqual(body["status"], "ok")
            self.assertFalse(body["stale"])
            self.assertTrue(body["computed_at"])
            p = body["personality"]
            self.assertEqual([p[a]["score"] for a in ("timmy", "johnny", "spike", "vorthos", "melvin")],
                             [51, 31, 39, 76, 79])
            self.assertEqual((p["leans_toward"], p["runner_up"]), ("melvin", "vorthos"))
            self.assertEqual(p["computed_at"], body["computed_at"])

    def test_stale_after_journal_change(self):
        store.recompute_all(self.fake, scorer=Scorer())
        journal.save_journal(self.fake, self.ctrl, {"novelties": "x"})
        self.assertTrue(self.client.get("/api/decks/archidekt:5620189/personality").json()["stale"])

    def test_failed_without_scores_is_500_shape(self):
        store.recompute_all(self.fake, scorer=Scorer(fail_for={"archidekt:5620189"}))
        res = self.client.get("/api/decks/archidekt:5620189/personality")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json()["status"], "error")
        self.assertIn("boom", res.json()["error"])

    def test_post_queues_without_scoring_in_request(self):
        with patch.object(ps, "score_deck") as live:
            res = self.client.post("/api/decks/archidekt:5620189/personality")
        live.assert_not_called()
        self.assertEqual(res.status_code, 202)
        self.assertIn("archidekt:5620189", store.pending(self.fake))

    def test_profile_empty(self):
        body = self.client.get("/api/profile/personality").json()
        self.assertTrue(body["success"])
        p = body["profile"]
        self.assertEqual((p["decks_scored"], p["decks_total"], p["low_data"]), (0, 3, True))
        self.assertEqual(p["not_scored"], 3)
        self.assertEqual(p["headline_kind"], "none")

    def test_profile_scope_and_failed(self):
        store.recompute_all(self.fake, scorer=Scorer(fail_for={"archidekt:3"}))
        p = self.client.get("/api/profile/personality").json()["profile"]
        self.assertEqual(p["scope"], "physical")
        self.assertEqual((p["decks_scored"], p["decks_total"], p["not_scored"]), (2, 3, 0))
        self.assertTrue(p["low_data"])
        self.assertEqual([f["id"] for f in p["failed"]], ["archidekt:3"])
        self.assertEqual(p["scope_counts"], {"physical": 3, "all": 4})
        allp = self.client.get("/api/profile/personality?scope=all").json()["profile"]
        self.assertEqual((allp["decks_scored"], allp["decks_total"]), (3, 4))
        self.assertFalse(allp["low_data"])
        self.assertTrue(allp["trends"])

    def test_profile_bad_scope(self):
        self.assertEqual(self.client.get("/api/profile/personality?scope=nope").status_code, 400)

    def test_profile_redis_down_is_503(self):
        with patch("src.server.app.main.personality_store.load_all", side_effect=ConnectionError("down")):
            res = self.client.get("/api/profile/personality")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["status"], "unavailable")

    def test_deck_redis_down_is_503(self):
        with patch("src.server.app.main.personality_store.load_record", side_effect=ConnectionError("down")):
            res = self.client.get("/api/decks/archidekt:5620189/personality")
        self.assertEqual(res.status_code, 503)

    def test_journal_save_wakes_worker(self):
        from src.server.domain.analytics import bracket_worker as bw
        res = self.client.put("/api/decks/archidekt:5620189/journal", json={"novelties": "weird"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("archidekt:5620189", self.fake.lists.get(bw.WAKE_KEY, []))


if __name__ == "__main__":
    unittest.main()
