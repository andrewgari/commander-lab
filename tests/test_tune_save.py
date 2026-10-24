"""Tune dual save (DT-2): 'for later' drafts change nothing; 'save to deck' swaps cards."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_mod
import instances as inst
import registry
from analytics import tune_save
from app import app

DECK = {"id": 417707, "registry_id": "archidekt:417707", "source": "archidekt", "name": "Holy Hellfire",
        "status": "testing", "links": {"moxfield": {"identifier": "abc", "deck_id": "abc"}},
        "cards": [{"name": "Cut Me", "quantity": 1}, {"name": "Stay", "quantity": 1}]}


class FakeRedis:
    """In-memory stand-in (strings, sets, lists): CI installs requirements.txt only,
    so no external fakeredis package."""

    def __init__(self):
        self.store, self.sets, self.lists = {}, {}, {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        self.store[k] = v
        return True

    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)

    def sadd(self, k, m):
        self.sets.setdefault(k, set()).add(m)

    def srem(self, k, m):
        self.sets.get(k, set()).discard(m)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sinter(self, *ks):
        sets = [self.sets.get(k, set()) for k in ks]
        out = set(sets[0]) if sets else set()
        for x in sets[1:]:
            out &= x
        return out

    def keys(self, pattern):
        pre = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]

    def scan_iter(self, match):
        return iter(self.keys(match))

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def lrange(self, k, a, b):
        items = self.lists.get(k, [])
        return items[a:] if b == -1 else items[a:b + 1]

    def pipeline(self):
        return _Pipe(self)


class _Pipe:
    def __init__(self, parent):
        self.parent, self.ops = parent, []

    def __getattr__(self, name):
        def call(*a, **k):
            self.ops.append((name, a, k))
            return self
        return call

    def execute(self):
        for name, a, k in self.ops:
            getattr(self.parent, name)(*a, **k)
        self.ops = []


class TuneSaveTests(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([DECK]))
        rid = DECK["registry_id"]
        self.cut = inst.create_instance(self.r, "Cut Me", "in_collection")
        inst.transition_status(self.r, self.cut["id"], "in_deck", deck_id=rid, deck_name=DECK["name"])
        self.spare = inst.create_instance(self.r, "Owned Add", "in_collection")
        p = [patch.object(app_mod, "r", self.r), patch.object(app_mod.interactions_service, "start_rebuild"),
             patch.object(app_mod, "_wake_bracket_worker")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.c = TestClient(app)
        self.url = "/api/decks/archidekt:417707/tune/"

    def snapshot(self):
        return ({k: self.r.get(k) for k in self.r.keys("instance:*")}, self.r.get("decks"))

    BODY = {"swaps": [{"cut": "Cut Me", "add": "Owned Add", "source": "owned"}],
            "adds": [{"name": "Forest", "qty": 2, "source": "basic"}]}

    def test_for_later_changes_nothing_and_is_loadable(self):
        before = self.snapshot()
        res = self.c.post(self.url + "drafts", json=dict(self.BODY, label="Plan B"))
        self.assertEqual(res.status_code, 200)
        did = res.json()["draft"]["id"]
        self.assertEqual(self.snapshot(), before)
        got = self.c.get(self.url + "drafts/" + did).json()["draft"]
        self.assertEqual(got["swaps"], self.BODY["swaps"])
        self.assertEqual([d["id"] for d in self.c.get(self.url + "drafts").json()["drafts"]], [did])
        self.assertEqual(self.c.delete(self.url + "drafts/" + did).json()["draft"]["status"], "discarded")
        self.assertEqual(self.c.get(self.url + "drafts").json()["drafts"], [])
        self.assertEqual(self.snapshot(), before)

    def test_empty_and_bad_bodies_rejected(self):
        self.assertEqual(self.c.post(self.url + "drafts", json={}).status_code, 400)
        self.assertEqual(self.c.post(self.url + "apply", json={"adds": [{"name": "X", "qty": 0}]}).status_code, 400)

    def test_save_to_deck_swaps_inventory_and_decklist(self):
        res = self.c.post(self.url + "apply", json=self.BODY)
        self.assertEqual(res.status_code, 200, res.text)
        out = res.json()
        self.assertEqual([f["name"] for f in out["applied"]["freed"]], ["Cut Me"])
        a = out["applied"]
        self.assertEqual(inst.get_instance(self.r, self.cut["id"])["ownership_status"], "in_collection")
        self.assertIsNone(inst.get_instance(self.r, self.cut["id"])["deck_id"])
        spare = inst.get_instance(self.r, self.spare["id"])
        self.assertEqual((spare["ownership_status"], spare["deck_id"]), ("in_deck", "archidekt:417707"))
        self.assertEqual(a["to_buy"], ["Forest"])           # no unused Forest existed
        self.assertEqual(len(a["added"]), 3)
        cards = {c["name"]: c["quantity"] for c in json.loads(self.r.get("decks"))[0]["cards"]}
        self.assertEqual(cards, {"Stay": 1, "Owned Add": 1, "Forest": 2})
        self.assertTrue(out["prompt_update_external"])
        self.assertEqual({p["provider"] for p in out["external"]}, {"archidekt", "moxfield"})

    def test_apply_saved_draft_once(self):
        did = self.c.post(self.url + "drafts", json=self.BODY).json()["draft"]["id"]
        self.assertEqual(self.c.post(self.url + "apply", json={"draft_id": did}).status_code, 200)
        self.assertEqual(self.c.get(self.url + "drafts/" + did).json()["draft"]["status"], "applied")
        self.assertEqual(self.c.post(self.url + "apply", json={"draft_id": did}).status_code, 409)
        self.assertEqual(self.c.post(self.url + "apply", json={"draft_id": "nope"}).status_code, 404)

    def test_normalize_pairs_cuts_and_adds(self):
        t = tune_save.normalize({"swaps": [{"cut": "A", "add": "B"}], "cuts": ["C"], "adds": [{"name": "D", "qty": 2}]})
        cuts, adds = tune_save.flatten(t)
        self.assertEqual(cuts, ["A", "C"])
        self.assertEqual([(a["name"], a["qty"]) for a in adds], [("B", 1), ("D", 2)])


if __name__ == "__main__":
    unittest.main()
