"""Tests for registry.decommission_deck and POST /api/decks/{id}/decommission.

Run: python tests/test_decommission.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.inventory.instances as instances
import src.server.domain.decks.registry as registry
from fastapi.testclient import TestClient


class FakePipe:
    def __init__(self, r):
        self.r, self.ops, self.executed = r, [], False

    def __getattr__(self, name):
        def queue(*a):
            self.ops.append((name, a))
            return self
        return queue

    def execute(self):
        self.r.pipe_executions += 1
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


class FakeRedis:
    def __init__(self):
        self.store, self.sets, self.pipe_executions = {}, {}, 0

    def get(self, k): return self.store.get(k)
    def set(self, k, v): self.store[k] = v
    def sadd(self, k, v): self.sets.setdefault(k, set()).add(v)
    def srem(self, k, v): self.sets.get(k, set()).discard(v)
    def smembers(self, k): return set(self.sets.get(k, set()))
    def sinter(self, *ks):
        out = None
        for k in ks:
            out = self.smembers(k) if out is None else out & self.smembers(k)
        return out or set()
    def keys(self, pat):
        pre = pat.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]
    def delete(self, k): self.store.pop(k, None)
    def pipeline(self): return FakePipe(self)


def make_deck(status="physical", **kw):
    d = {"id": 42, "name": "Atraxa", "registry_id": "archidekt:42", "source": "archidekt",
         "source_id": "42", "status": status, "tags": ["removed", "keep"],
         "cards": [{"name": "Sol Ring", "quantity": 1}]}
    d.update(kw)
    return d


class TestDecommission(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([make_deck(), make_deck(status="digital", id=7,
                  registry_id="archidekt:7", source_id="7", name="Digi")]))
        self.insts = [
            instances.create_instance(self.r, c, ownership_status="in_deck",
                                      deck_id="archidekt:42", deck_name="Atraxa")
            for c in ("Sol Ring", "Arcane Signet")
        ]
        self.other = instances.create_instance(self.r, "Island", ownership_status="in_collection")
        self.r.pipe_executions = 0  # count only the decommission's own transactions

    def deck(self):
        return registry.find_deck(self.r, "archidekt:42")

    def test_happy_path(self):
        res = registry.decommission_deck(self.r, "archidekt:42")
        self.assertFalse(res["already_decommissioned"])
        self.assertEqual(res["unassigned_count"], 2)
        self.assertTrue(res["tag_removed"])
        self.assertEqual(self.deck()["status"], "retired")
        self.assertEqual(self.r.get("deck_status:42"), "retired")

    def test_single_transaction(self):
        registry.decommission_deck(self.r, "archidekt:42")
        self.assertEqual(self.r.pipe_executions, 1)

    def test_non_physical_rejected_without_writes(self):
        with self.assertRaises(registry.DecommissionError):
            registry.decommission_deck(self.r, "archidekt:7")
        self.assertEqual(registry.find_deck(self.r, "archidekt:7")["status"], "digital")
        self.assertEqual(self.r.pipe_executions, 0)

    def test_unknown_deck(self):
        with self.assertRaises(ValueError):
            registry.decommission_deck(self.r, "nope")

    def test_cards_stay_owned(self):
        registry.decommission_deck(self.r, "archidekt:42")
        for i in self.insts:
            rec = instances.get_instance(self.r, i["id"])
            self.assertEqual(rec["ownership_status"], "in_collection")
            self.assertIsNone(rec["deck_id"])
        self.assertEqual(instances.list_instances(self.r, deck_id="archidekt:42"), [])
        self.assertEqual(len(instances.list_instances(self.r, ownership_status="in_collection")), 3)
        self.assertEqual(instances.get_instance(self.r, self.other["id"])["ownership_status"], "in_collection")

    def test_retired_category(self):
        registry.decommission_deck(self.r, "archidekt:42")
        self.assertEqual(self.deck()["status"], "retired")

    def test_tag_removed_others_kept(self):
        registry.decommission_deck(self.r, "archidekt:42")
        self.assertEqual(self.deck()["tags"], ["keep"])

    def test_missing_tag_ok(self):
        self.r.set("decks", json.dumps([make_deck(tags=[])]))
        res = registry.decommission_deck(self.r, "archidekt:42")
        self.assertFalse(res["tag_removed"])

    def test_repeat_is_idempotent(self):
        registry.decommission_deck(self.r, "archidekt:42")
        again = registry.decommission_deck(self.r, "archidekt:42")
        self.assertTrue(again["already_decommissioned"])
        self.assertEqual(again["unassigned_count"], 0)
        self.assertEqual(self.r.pipe_executions, 1)
        self.assertEqual(self.deck()["status"], "retired")

    def test_retired_never_physical_still_rejected(self):
        self.r.set("decks", json.dumps([make_deck(status="retired")]))
        with self.assertRaises(registry.DecommissionError):
            registry.decommission_deck(self.r, "archidekt:42")


class TestDecommissionEndpoint(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as app_module
        self.app_module = app_module
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([make_deck(), make_deck(status="testing", id=7,
                  registry_id="archidekt:7", source_id="7")]))
        instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck",
                                  deck_id="archidekt:42", deck_name="Atraxa")
        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    def test_ok_then_repeat(self):
        res = self.client.post("/api/decks/archidekt:42/decommission")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "retired")
        self.assertEqual(body["unassigned_count"], 1)
        self.assertEqual(body["tags"], ["keep"])
        again = self.client.post("/api/decks/archidekt:42/decommission").json()
        self.assertTrue(again["already_decommissioned"])

    def test_non_physical_400(self):
        res = self.client.post("/api/decks/archidekt:7/decommission")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["success"])

    def test_404(self):
        self.assertEqual(self.client.post("/api/decks/zzz/decommission").status_code, 404)

    def test_custom_tag(self):
        body = self.client.post("/api/decks/archidekt:42/decommission", json={"tag": "keep"}).json()
        self.assertEqual(body["tags"], ["removed"])


if __name__ == "__main__":
    unittest.main()
