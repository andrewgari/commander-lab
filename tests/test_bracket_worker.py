"""BR-5: separate bracket-evaluation process + stored results + API.

analytics/bracket_worker.py runs as its own process (compose service
`bracket-worker`, `python -m analytics.bracket_worker`). Each tick it hashes
every deck's decklist + journal, re-evaluates only decks whose input hash
changed (or that were explicitly requested), and stores the BR-4 result at
bracket:eval:{registry_id}. The web app only reads the stored result and
queues requests; it never evaluates in the request path.

Run: python -m unittest tests.test_bracket_worker
"""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import bracket_worker as bw  # noqa: E402
from analytics import journal  # noqa: E402
from tests.test_interactions import FakeRedis as _BaseFake  # noqa: E402


class FakeRedis(_BaseFake):
    """Adds the set + blocking-pop calls the worker uses."""

    def __init__(self):
        super().__init__()
        self.sets = {}

    def sadd(self, key, *members):
        s = self.sets.setdefault(key, set())
        before = len(s)
        s.update(members)
        return len(s) - before

    def srem(self, key, *members):
        s = self.sets.get(key, set())
        n = sum(1 for m in members if m in s)
        s.difference_update(members)
        return n

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sismember(self, key, member):
        return member in self.sets.get(key, set())

    def blpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if items:
            return key, items.pop(0)
        return None


def deck(rid="archidekt:1", name="Deck One", cards=("Sol Ring", "Island")):
    return {"registry_id": rid, "name": name, "commanders": ["Kaalia of the Vast"],
            "cards": [{"name": c, "quantity": 1} for c in cards]}


def put_decks(r, decks):
    r.set("decks", json.dumps(decks))


def fake_result(d, j):
    """Stand-in for BR-4's evaluate_bracket_live: just the fields BR-5 must store."""
    target = (j or {}).get("target_bracket")
    return {
        "estimated_bracket": 3, "estimated_bracket_name": "Upgraded", "estimated_bracket_reason": "r",
        "minimum_bracket": 1, "target_bracket": target, "no_target": target is None,
        "estimated_power_level": 6.5, "power_source": "our_estimate",
        "conflicts": [] if target in (None, 3) else [{"severity": "advisory", "rule": "bracket_mismatch",
                                                      "message": "m"}],
        "explanation": {"signals": [{"signal": "game_changers", "value": 3, "effect": "floor 3", "source": "s"}],
                        "summary": f"cards={len(d['cards'])} target={target}"},
        "findings": {},
    }


class Recorder:
    """Async evaluator that records calls and can be told to fail for a deck."""

    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    async def __call__(self, r, d):
        self.calls.append(d["registry_id"])
        if d["registry_id"] in self.fail_for:
            raise RuntimeError("boom")
        rec = journal.load_journal(r, d)
        return fake_result(d, journal.public(rec) if rec else None)


class InputHash(unittest.TestCase):
    def test_stable_and_order_independent(self):
        a = deck(cards=("Sol Ring", "Island"))
        b = deck(cards=("Island", "Sol Ring"))
        self.assertEqual(bw.input_hash(a, None), bw.input_hash(b, None))

    def test_changes_with_decklist(self):
        self.assertNotEqual(bw.input_hash(deck(), None), bw.input_hash(deck(cards=("Sol Ring", "Forest")), None))

    def test_changes_with_quantity(self):
        a, b = deck(), deck()
        b["cards"][1]["quantity"] = 2
        self.assertNotEqual(bw.input_hash(a, None), bw.input_hash(b, None))

    def test_changes_with_journal_but_not_its_bookkeeping(self):
        j1 = {"target_bracket": 2, "_journal": {"updated_at": "2026-01-01"}}
        j2 = {"target_bracket": 2, "_journal": {"updated_at": "2026-02-02"}}
        j3 = {"target_bracket": 3, "_journal": {"updated_at": "2026-01-01"}}
        self.assertEqual(bw.input_hash(deck(), j1), bw.input_hash(deck(), j2))
        self.assertNotEqual(bw.input_hash(deck(), j1), bw.input_hash(deck(), j3))
        self.assertNotEqual(bw.input_hash(deck(), None), bw.input_hash(deck(), j1))


class WorkerRuns(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.d1, self.d2 = deck(), deck("archidekt:2", "Deck Two", ("Forest",))
        put_decks(self.r, [self.d1, self.d2])
        self.ev = Recorder()

    def run_once(self, **kw):
        return bw.run_once(self.r, evaluator=self.ev, **kw)

    def test_first_run_evaluates_and_stores_everything_br5_needs(self):
        stats = self.run_once()
        self.assertEqual(sorted(self.ev.calls), ["archidekt:1", "archidekt:2"])
        self.assertEqual(stats["evaluated"], 2)
        rec = bw.load_result(self.r, "archidekt:1")
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["registry_id"], "archidekt:1")
        self.assertTrue(rec["evaluated_at"])
        self.assertEqual(rec["input_hash"], bw.input_hash(self.d1, None))
        res = rec["result"]
        self.assertEqual(res["estimated_bracket"], 3)
        self.assertEqual(res["estimated_power_level"], 6.5)
        self.assertEqual(res["explanation"]["signals"][0]["signal"], "game_changers")
        self.assertIn("summary", res["explanation"])
        self.assertIn("bracket:eval:archidekt:1", self.r.store)

    def test_skips_unchanged_input_hash(self):
        self.run_once()
        first = bw.load_result(self.r, "archidekt:1")["evaluated_at"]
        stats = self.run_once()
        self.assertEqual(len(self.ev.calls), 2)  # no new calls
        self.assertEqual(stats["evaluated"], 0)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(bw.load_result(self.r, "archidekt:1")["evaluated_at"], first)

    def test_reruns_on_decklist_change(self):
        self.run_once()
        changed = deck(cards=("Sol Ring", "Island", "Demonic Tutor"))
        put_decks(self.r, [changed, self.d2])
        self.run_once()
        self.assertEqual(self.ev.calls[2:], ["archidekt:1"])
        rec = bw.load_result(self.r, "archidekt:1")
        self.assertEqual(rec["input_hash"], bw.input_hash(changed, None))
        self.assertEqual(rec["result"]["explanation"]["summary"], "cards=3 target=None")

    def test_reruns_on_journal_change(self):
        self.run_once()
        journal.save_journal(self.r, self.d2, {"target_bracket": 2})
        self.run_once()
        self.assertEqual(self.ev.calls[2:], ["archidekt:2"])
        res = bw.load_result(self.r, "archidekt:2")["result"]
        self.assertEqual(res["target_bracket"], 2)
        self.assertEqual(res["conflicts"][0]["rule"], "bracket_mismatch")

    def test_requested_deck_is_reevaluated_even_if_unchanged(self):
        self.run_once()
        bw.request_evaluation(self.r, "archidekt:1")
        self.assertIn("archidekt:1", bw.pending(self.r))
        stats = self.run_once()
        self.assertEqual(self.ev.calls[2:], ["archidekt:1"])
        self.assertEqual(stats["evaluated"], 1)
        self.assertNotIn("archidekt:1", bw.pending(self.r))

    def test_request_wakes_the_worker(self):
        bw.request_evaluation(self.r, "archidekt:1")
        self.assertEqual(self.r.lists.get(bw.WAKE_KEY), ["archidekt:1"])

    def test_failure_is_isolated_per_deck(self):
        self.ev.fail_for = {"archidekt:1"}
        stats = self.run_once()  # must not raise
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["evaluated"], 1)
        self.assertEqual(bw.load_result(self.r, "archidekt:2")["status"], "ok")
        bad = bw.load_result(self.r, "archidekt:1")
        self.assertEqual(bad["status"], "error")
        self.assertIn("boom", bad["error"])
        self.assertIsNone(bad.get("result"))

    def test_failed_input_is_not_retried_every_tick_but_is_on_change(self):
        self.ev.fail_for = {"archidekt:1"}
        self.run_once()
        self.run_once()
        self.assertEqual(self.ev.calls.count("archidekt:1"), 1)
        self.ev.fail_for = set()
        journal.save_journal(self.r, self.d1, {"target_bracket": 3})
        self.run_once()
        self.assertEqual(self.ev.calls.count("archidekt:1"), 2)
        self.assertEqual(bw.load_result(self.r, "archidekt:1")["status"], "ok")

    def test_failure_keeps_the_last_good_result(self):
        self.run_once()
        good = bw.load_result(self.r, "archidekt:1")["result"]
        self.ev.fail_for = {"archidekt:1"}
        journal.save_journal(self.r, self.d1, {"target_bracket": 1})
        self.run_once()
        rec = bw.load_result(self.r, "archidekt:1")
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["result"], good)
        self.assertIn("boom", rec["error"])

    def test_corrupt_stored_record_is_treated_as_not_evaluated(self):
        self.r.set(bw.EVAL_PREFIX + "archidekt:1", "{not json")
        self.assertIsNone(bw.load_result(self.r, "archidekt:1"))
        self.run_once()
        self.assertEqual(bw.load_result(self.r, "archidekt:1")["status"], "ok")

    def test_unreadable_journal_does_not_stop_the_tick(self):
        self.r.set(journal.KEY_PREFIX + "archidekt:1", "{not json")
        stats = self.run_once()
        self.assertEqual(stats["evaluated"] + stats["failed"], 2)

    def test_heartbeat_written(self):
        self.run_once()
        hb = json.loads(self.r.get(bw.HEARTBEAT_KEY))
        self.assertTrue(hb["at"])
        self.assertEqual(hb["evaluated"], 2)

    def test_run_once_survives_redis_errors_reading_decks(self):
        class Down(FakeRedis):
            def get(self, key):
                raise ConnectionError("down")

        stats = bw.run_once(Down(), evaluator=self.ev)
        self.assertIn("error", stats)


class DefaultEvaluator(unittest.TestCase):
    def test_stores_the_br4_model_dump(self):
        from analytics import bracket_eval as be

        captured = {}

        async def live(r, d, commandersalt=True):
            captured["cs"] = commandersalt
            return be.evaluate_bracket(d, {"target_bracket": 1, "target_power_level": {"scale": 4}},
                                       {"game_changers": ["Demonic Tutor"], "combos": [], "power_estimate": 6.0})

        r = FakeRedis()
        d = deck(cards=("Demonic Tutor", "Island"))
        put_decks(r, [d])
        with patch("analytics.bracket_eval.evaluate_bracket_live", live), \
                patch.dict(os.environ, {"BRACKET_EVAL_COMMANDERSALT": "0"}):
            bw.run_once(r)
        self.assertFalse(captured["cs"])
        res = bw.load_result(r, "archidekt:1")["result"]
        for k in ("estimated_bracket", "estimated_power_level", "explanation", "conflicts", "findings"):
            self.assertIn(k, res)
        self.assertEqual(res["conflicts"][0]["severity"], "hard")
        self.assertTrue(res["explanation"]["summary"])


class Routes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import app as app_mod
        from app import app

        self.app_mod = app_mod
        self.client = TestClient(app)
        self.fake = FakeRedis()
        self.d = deck("archidekt:417707", "Holy Hellfire")
        put_decks(self.fake, [self.d])
        self.p = patch.object(app_mod, "r", self.fake)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_not_evaluated_yet(self):
        for path in ("/api/deck/archidekt:417707/bracket", "/api/decks/archidekt:417707/bracket"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 404, path)
            body = res.json()
            self.assertEqual(body["status"], "not_evaluated")
            self.assertFalse(body["pending"])
            self.assertEqual(body["registry_id"], "archidekt:417707")

    def test_unknown_deck(self):
        res = self.client.get("/api/deck/nope/bracket")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "deck_not_found")
        self.assertEqual(self.client.post("/api/deck/nope/bracket").status_code, 404)

    def test_stored_result(self):
        bw.run_once(self.fake, evaluator=Recorder())
        body = self.client.get("/api/deck/archidekt:417707/bracket").json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["stale"])
        self.assertTrue(body["evaluated_at"])
        self.assertEqual(body["input_hash"], bw.input_hash(self.d, None))
        self.assertEqual(body["evaluation"]["estimated_bracket"], 3)
        self.assertEqual(body["evaluation"]["estimated_power_level"], 6.5)
        self.assertIn("explanation", body["evaluation"])

    def test_by_deck_name_too(self):
        bw.run_once(self.fake, evaluator=Recorder())
        self.assertEqual(self.client.get("/api/deck/Holy Hellfire/bracket").status_code, 200)

    def test_stale_flag_after_journal_change(self):
        bw.run_once(self.fake, evaluator=Recorder())
        journal.save_journal(self.fake, self.d, {"target_bracket": 2})
        body = self.client.get("/api/deck/archidekt:417707/bracket").json()
        self.assertTrue(body["stale"])
        self.assertEqual(body["status"], "ok")

    def test_post_queues_and_does_not_evaluate_in_request(self):
        with patch("analytics.bracket_eval.evaluate_bracket_live") as live:
            res = self.client.post("/api/deck/archidekt:417707/bracket")
        live.assert_not_called()
        self.assertEqual(res.status_code, 202)
        self.assertTrue(res.json()["queued"])
        self.assertIn("archidekt:417707", bw.pending(self.fake))
        body = self.client.get("/api/deck/archidekt:417707/bracket").json()
        self.assertTrue(body["pending"])

    def test_failed_eval_without_result(self):
        bw.run_once(self.fake, evaluator=Recorder(fail_for={"archidekt:417707"}))
        res = self.client.get("/api/deck/archidekt:417707/bracket")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "error")
        self.assertIn("boom", res.json()["error"])

    def test_redis_down_is_503_not_500(self):
        class Down(FakeRedis):
            def get(self, key):
                if key.startswith(bw.EVAL_PREFIX):
                    raise ConnectionError("down")
                return super().get(key)

        down = Down()
        put_decks(down, [self.d])
        with patch.object(self.app_mod, "r", down):
            res = self.client.get("/api/deck/archidekt:417707/bracket")
        self.assertEqual(res.status_code, 503)

    def test_journal_save_wakes_the_worker(self):
        res = self.client.put("/api/decks/archidekt:417707/journal", json={"target_bracket": 2})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("archidekt:417707", self.fake.lists.get(bw.WAKE_KEY, []))

    def test_journal_save_survives_a_failing_wake(self):
        with patch("analytics.bracket_worker.wake", side_effect=RuntimeError("redis down")):
            res = self.client.put("/api/decks/archidekt:417707/journal", json={"target_bracket": 2})
        self.assertEqual(res.status_code, 200, res.text)

    def test_deck_sync_wakes_the_worker_and_survives_a_failing_wake(self):
        class Report:
            def to_dict(self):
                return {"ok": True}

        with patch("app.deck_sync.sync_deck", return_value=Report()):
            res = self.client.post("/api/decks/archidekt:417707/sync")
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("archidekt:417707", self.fake.lists.get(bw.WAKE_KEY, []))
            with patch("analytics.bracket_worker.wake", side_effect=RuntimeError("redis down")):
                res = self.client.post("/api/decks/archidekt:417707/sync")
            self.assertEqual(res.status_code, 200, res.text)

    def test_deck_page_ignores_a_broken_eval_record(self):
        self.fake.set(bw.EVAL_PREFIX + "archidekt:417707", "{not json")
        res = self.client.get("/api/deck/archidekt:417707/bracket")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "not_evaluated")


class Compose(unittest.TestCase):
    def test_prod_compose_runs_the_worker_as_its_own_service(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "docker-compose.prod.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("bracket-worker:", text)
        self.assertIn("container_name: commander-lab-bracket-worker", text)
        self.assertIn("python -m analytics.bracket_worker", text)


if __name__ == "__main__":
    unittest.main()
