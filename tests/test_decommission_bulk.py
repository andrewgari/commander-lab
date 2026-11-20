"""Tests for registry.decommission_decks and POST /api/decks/decommission.

Run: python tests/test_decommission_bulk.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import src.server.domain.inventory.instances as instances
import src.server.domain.decks.registry as registry
from fastapi.testclient import TestClient
from test_decommission import FakeRedis, make_deck


def mk(i, status="physical", **kw):
    return make_deck(status=status, id=i, registry_id=f"archidekt:{i}",
                     source_id=str(i), name=f"Deck{i}", **kw)


class TestBulk(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([mk(1), mk(2), mk(3, status="digital")]))
        for i in (1, 2):
            for c in ("Sol Ring", "Arcane Signet"):
                instances.create_instance(self.r, c, ownership_status="in_deck",
                                          deck_id=f"archidekt:{i}", deck_name=f"Deck{i}")
        self.r.pipe_executions = 0

    def status(self, i):
        return registry.find_deck(self.r, f"archidekt:{i}")["status"]

    def test_success_single_transaction(self):
        res = registry.decommission_decks(self.r, ["archidekt:1", "archidekt:2"])
        self.assertEqual([s["deck_id"] for s in res["succeeded"]], ["archidekt:1", "archidekt:2"])
        self.assertEqual(res["failed"], [])
        self.assertEqual(self.r.pipe_executions, 1)
        self.assertEqual((self.status(1), self.status(2)), ("retired", "retired"))
        self.assertEqual(res["succeeded"][0]["unassigned_count"], 2)
        self.assertEqual(instances.list_instances(self.r, ownership_status="in_deck"), [])

    def test_empty_rejected(self):
        for bad in ([], None, "archidekt:1", [""], [1]):
            with self.assertRaises(ValueError):
                registry.decommission_decks(self.r, bad)

    def test_too_many(self):
        with self.assertRaises(ValueError):
            registry.decommission_decks(self.r, [str(i) for i in range(101)])

    def test_duplicates_deduped_including_aliases(self):
        res = registry.decommission_decks(self.r, ["archidekt:1", "archidekt:1", "1"])
        self.assertEqual(len(res["succeeded"]), 1)
        self.assertEqual(res["succeeded"][0]["unassigned_count"], 2)
        self.assertEqual(res["failed"], [])

    def test_nonexistent_does_not_block_others(self):
        res = registry.decommission_decks(self.r, ["nope", "archidekt:1"])
        self.assertEqual(res["failed"][0]["code"], "not_found")
        self.assertEqual(res["failed"][0]["deck_id"], "nope")
        self.assertEqual(self.status(1), "retired")

    def test_non_physical_fails_without_touching_it(self):
        res = registry.decommission_decks(self.r, ["archidekt:3", "archidekt:2"])
        self.assertEqual(res["failed"][0]["code"], "not_physical")
        self.assertEqual(self.status(3), "digital")
        self.assertEqual(self.status(2), "retired")

    def test_already_decommissioned_is_noop_success(self):
        registry.decommission_deck(self.r, "archidekt:1")
        self.r.pipe_executions = 0
        res = registry.decommission_decks(self.r, ["archidekt:1"])
        self.assertTrue(res["succeeded"][0]["already_decommissioned"])
        self.assertEqual(res["succeeded"][0]["unassigned_count"], 0)
        self.assertEqual(res["failed"], [])
        self.assertEqual(self.r.pipe_executions, 0)

    def test_all_failed_writes_nothing(self):
        res = registry.decommission_decks(self.r, ["nope", "archidekt:3"])
        self.assertEqual(res["succeeded"], [])
        self.assertEqual(self.r.pipe_executions, 0)

    def test_matches_single_deck_result(self):
        r2 = FakeRedis()
        r2.set("decks", json.dumps([mk(1)]))
        single = registry.decommission_deck(r2, "archidekt:1")["deck"]
        registry.decommission_decks(self.r, ["archidekt:1"])
        bulk = registry.find_deck(self.r, "archidekt:1")
        for k in ("status", "tags"):
            self.assertEqual(single[k], bulk[k])


class TestBulkEndpoint(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as app_module
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([mk(1), mk(2), mk(3, status="digital")]))
        instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck",
                                  deck_id="archidekt:1", deck_name="Deck1")
        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    def test_mixed_batch(self):
        res = self.client.post("/api/decks/decommission", json={
            "deck_ids": ["archidekt:1", "archidekt:1", "archidekt:3", "zzz"]})
        self.assertEqual(res.status_code, 200)
        b = res.json()
        self.assertTrue(b["success"])
        self.assertEqual(b["requested"], 3)
        self.assertEqual([s["deck_id"] for s in b["succeeded"]], ["archidekt:1"])
        self.assertEqual({f["code"] for f in b["failed"]}, {"not_physical", "not_found"})

    def test_empty_400(self):
        for body in ({"deck_ids": []}, {}, {"deck_ids": "x"}):
            res = self.client.post("/api/decks/decommission", json=body)
            self.assertEqual(res.status_code, 400)
            self.assertFalse(res.json()["success"])

    def test_no_body_400(self):
        self.assertEqual(self.client.post("/api/decks/decommission").status_code, 400)

    def test_single_endpoint_unaffected(self):
        res = self.client.post("/api/decks/archidekt:2/decommission")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "retired")


if __name__ == "__main__":
    unittest.main()
