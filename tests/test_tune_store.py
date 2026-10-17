"""Saved Tune recommendations: archive-on-recompute (analytics/tune_store.py + routes)."""
import importlib.util
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_mod
from analytics import tune_store as ts
from analytics import tuner
from app import app

DECK = {"id": 417707, "registry_id": "archidekt:417707", "name": "Holy Hellfire"}


class FakeRedis:
    """Strings + lists, with a buffered pipeline so MULTI/EXEC atomicity is visible."""

    def __init__(self):
        self.store, self.lists = {}, {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)
            self.lists.pop(k, None)

    def keys(self, pattern):
        pre = pattern.rstrip("*")
        return [k for k in list(self.store) + list(self.lists) if k.startswith(pre)]

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def lrange(self, k, a, b):
        items = self.lists.get(k, [])
        return items[a:] if b == -1 else items[a:b + 1]

    def pipeline(self):
        return _Pipe(self)


class _Pipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def set(self, *a, **k):
        self.ops.append(("set", a, k))
        return self

    def lpush(self, *a):
        self.ops.append(("lpush", a, {}))
        return self

    def execute(self):
        for name, a, k in self.ops:
            getattr(self.r, name)(*a, **k)
        self.ops = []


class Gen:
    """Stub for tuner.compute; payload changes per call."""

    def __init__(self):
        self.calls = 0

    def __call__(self, r, deck, mode, in_deck, buy):
        self.calls += 1
        return {"n": self.calls, "mode": mode, "swaps": []}


def actives(r, deck_id="archidekt:417707"):
    out = []
    for rid in r.lrange(ts.INDEX_PREFIX + deck_id, 0, -1):
        rec = ts._load(r, rid)
        if rec["status"] == ts.ACTIVE:
            out.append(rec)
    return out


class TestStore(unittest.TestCase):
    def setUp(self):
        self.r, self.gen = FakeRedis(), Gen()

    def test_first_compute_creates_active_record(self):
        new, old = ts.recompute(self.r, DECK, self.gen)
        self.assertIsNone(old)
        self.assertEqual((new["status"], new["archived_at"], new["deck_id"]), ("active", None, "archidekt:417707"))
        self.assertEqual(new["payload"]["n"], 1)
        self.assertEqual(ts.get_active(self.r, "archidekt:417707")["id"], new["id"])

    def test_recompute_archives_old_and_creates_new_active(self):
        first, _ = ts.recompute(self.r, DECK, self.gen)
        second, old = ts.recompute(self.r, DECK, self.gen, mode="best", in_deck=False, buy=False)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(old["id"], first["id"])
        self.assertEqual(old["status"], "archived")
        self.assertTrue(old["archived_at"])
        stored_old = ts._load(self.r, first["id"])  # persisted, not deleted, payload intact
        self.assertEqual((stored_old["status"], stored_old["payload"]["n"]), ("archived", 1))
        self.assertEqual(second["params"], {"mode": "best", "in_deck": False, "buy": False})
        self.assertEqual(ts.get_active(self.r, "archidekt:417707")["id"], second["id"])

    def test_only_one_active_per_deck(self):
        for _ in range(4):
            ts.recompute(self.r, DECK, self.gen)
        self.assertEqual(len(actives(self.r)), 1)
        self.assertEqual(len(self.r.lrange(ts.INDEX_PREFIX + "archidekt:417707", 0, -1)), 4)
        other = dict(DECK, id=2, registry_id="archidekt:2")
        ts.recompute(self.r, other, self.gen)
        self.assertEqual(len(actives(self.r)), 1)  # other deck independent
        self.assertEqual(len(actives(self.r, "archidekt:2")), 1)

    def test_failed_generation_keeps_previous_active(self):
        first, _ = ts.recompute(self.r, DECK, self.gen)

        def boom(*a):
            raise tuner.TunerError("nope")

        with self.assertRaises(tuner.TunerError):
            ts.recompute(self.r, DECK, boom)
        self.assertEqual(ts.get_active(self.r, "archidekt:417707")["id"], first["id"])

    def test_history_lists_archived_only_newest_first(self):
        a, _ = ts.recompute(self.r, DECK, self.gen)
        b, _ = ts.recompute(self.r, DECK, self.gen)
        c, _ = ts.recompute(self.r, DECK, self.gen)
        h = ts.list_history(self.r, "archidekt:417707")
        self.assertEqual([x["id"] for x in h], [b["id"], a["id"]])
        self.assertNotIn("payload", h[0])
        self.assertIn("payload", ts.list_history(self.r, "archidekt:417707", include_payload=True)[0])

    def test_lock_busy_raises(self):
        self.r.set(ts.LOCK_PREFIX + "archidekt:417707", "x")
        with patch.object(ts, "LOCK_WAIT_S", 0.0):
            with self.assertRaises(ts.TuneStoreError):
                ts.recompute(self.r, DECK, self.gen)


class TestMigration(unittest.TestCase):
    def load(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
                            "migrate_tune_recommendations.py")
        spec = importlib.util.spec_from_file_location("mig_tune", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_empty_redis_runs_cleanly_and_stamps_version(self):
        r = FakeRedis()
        m = self.load()
        self.assertEqual(m.migrate(r, apply=False)["decks"], 0)
        self.assertIsNone(r.get(ts.SCHEMA_KEY))
        m.migrate(r, apply=True)
        self.assertEqual(r.get(ts.SCHEMA_KEY), "1")

    def test_repairs_duplicate_active_and_is_idempotent(self):
        r, gen = FakeRedis(), Gen()
        a, _ = ts.recompute(r, DECK, gen)
        b, _ = ts.recompute(r, DECK, gen)
        # corrupt: re-activate the older record
        import json
        a["status"], a["archived_at"] = "active", None
        r.set(ts.REC_PREFIX + a["id"], json.dumps(a))
        self.assertEqual(len(actives(r)), 2)
        m = self.load()
        st = m.migrate(r, apply=True)
        self.assertEqual(st["demoted"], 1)
        act = actives(r)
        self.assertEqual([x["id"] for x in act], [b["id"]])  # newest wins
        self.assertEqual(r.get(ts.ACTIVE_PREFIX + "archidekt:417707"), b["id"])
        self.assertEqual(m.migrate(r, apply=True)["demoted"], 0)
        self.assertEqual(len(r.lrange(ts.INDEX_PREFIX + "archidekt:417707", 0, -1)), 2)  # nothing deleted


class TestApi(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.client = TestClient(app)
        self.n = 0
        outer = self

        def fake_compute(r, deck, mode="flex", in_deck=True, buy=True, **kw):
            outer.n += 1
            return {"n": outer.n, "mode": mode, "swaps": []}

        self.patches = [patch.object(app_mod, "r", self.r),
                        patch("app.registry.find_deck",
                              side_effect=lambda r, d: DECK if d in ("archidekt:417707", "417707") else None),
                        patch("app.registry.list_decks", return_value=[DECK]),
                        patch.object(tuner, "compute", fake_compute)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    URL = "/api/decks/archidekt:417707/tune"

    def test_saved_404_when_none(self):
        res = self.client.get(self.URL + "/saved")
        self.assertEqual(res.status_code, 404)
        self.assertIsNone(res.json()["recommendation"])

    def test_unknown_deck_404(self):
        self.assertEqual(self.client.get("/api/decks/archidekt:999/tune/saved").status_code, 404)
        self.assertEqual(self.client.post("/api/decks/archidekt:999/tune/recompute").status_code, 404)

    def test_recompute_then_get_returns_active(self):
        res = self.client.post(self.URL + "/recompute", json={"mode": "best", "in_deck": False})
        self.assertEqual(res.status_code, 200)
        j = res.json()
        self.assertIsNone(j["archived"])
        self.assertEqual(j["recommendation"]["status"], "active")
        got = self.client.get(self.URL + "/saved").json()["recommendation"]
        self.assertEqual(got["id"], j["recommendation"]["id"])
        self.assertEqual(got["payload"]["mode"], "best")

    def test_recompute_archives_previous_and_history(self):
        first = self.client.post(self.URL + "/recompute").json()["recommendation"]  # no body ok
        j = self.client.post(self.URL + "/recompute").json()
        self.assertEqual(j["archived"]["id"], first["id"])
        self.assertEqual(j["archived"]["status"], "archived")
        self.assertNotIn("payload", j["archived"])
        self.assertEqual(self.client.get(self.URL + "/saved").json()["recommendation"]["id"], j["recommendation"]["id"])
        h = self.client.get(self.URL + "/history").json()["history"]
        self.assertEqual([x["id"] for x in h], [first["id"]])

    def test_plain_get_tune_does_not_save(self):
        self.client.get(self.URL)
        self.assertEqual(self.client.get(self.URL + "/saved").status_code, 404)

    def test_bad_mode_400_and_nothing_saved(self):
        with patch.object(tuner, "compute", side_effect=tuner.TunerError("mode must be one of")):
            res = self.client.post(self.URL + "/recompute", json={"mode": "zzz"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get(self.URL + "/saved").status_code, 404)

    def test_bad_in_deck_400(self):
        self.assertEqual(self.client.post(self.URL + "/recompute", json={"in_deck": "yes"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
