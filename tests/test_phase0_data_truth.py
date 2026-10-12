"""
Phase 0 data-truth tests (docs/DESIGN_REVIEW.md §4): /api/inventory reads the
instance registry, deck views see instances regardless of deck_id shape
(int id vs registry_id), the dedupe migration collapses a doubled import,
orphan decks are removed, and GET /api/decks/{id}/review works.

Run: python -m pytest tests/test_phase0_data_truth.py
"""
import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import instances as instance_store  # noqa: E402
import registry  # noqa: E402
from scripts import dedupe_instances, remove_orphan_decks  # noqa: E402
from tests.test_deck_manage_integration import FakeRedis as _BaseFake  # noqa: E402

DECK_ID = 417707


class _Pipe:
    def __init__(self, parent):
        self.parent, self.ops = parent, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        out = [getattr(self.parent, n)(*a, **k) for n, a, k in self.ops]
        self.ops = []
        return out


class FakeRedis(_BaseFake):
    def mget(self, keys):
        return [self.get(k) for k in keys]

    def pipeline(self):
        return _Pipe(self)


def seed(r):
    decks = [
        {
            "id": DECK_ID, "name": "Holy Hellfire", "color": "WBR",
            "commanders": ["Kaalia of the Vast"], "commander_uids": [],
            "folder": "Imported", "status": "physical", "source": "archidekt",
            "source_id": str(DECK_ID), "registry_id": f"archidekt:{DECK_ID}",
            "cards": [{"name": "Kaalia of the Vast", "quantity": 1},
                      {"name": "Sol Ring", "quantity": 1},
                      {"name": "Mountain", "quantity": 2}],
        },
        {"id": "1", "name": "test-deck", "commander": "Test"},
    ]
    r.set("decks", json.dumps(decks))
    r.set("card_meta:Sol Ring", json.dumps({"type": "Artifact", "color": "C", "cmc": 0}))
    r.set("card_meta:Kaalia of the Vast", json.dumps({"type": "Creature", "color": "WBR", "cmc": 0}))
    # oracle-keyed record carries the correct cmc; name-keyed one is stale
    r.set("idx:card_name_to_oracle:Kaalia of the Vast", "oracle-kaalia")
    r.set("card_meta:oracle-kaalia", json.dumps({"type": "Creature", "color": "WBR", "cmc": 4}))
    r.set("card_meta:Mountain", json.dumps({"type": "Land", "color": "C", "cmc": 0}))
    r.set("lab_tags", json.dumps({"Sol Ring": ["Ramp"]}))

    def bind(name, uid, created):
        inst = instance_store.create_instance(
            r, card_name=name, ownership_status="in_deck",
            deck_id=DECK_ID, deck_name="Holy Hellfire", archidekt_uid=uid,
        )
        inst["created_at"] = inst["updated_at"] = created
        r.set(f"instance:{inst['id']}", json.dumps(inst))
        return inst

    # Two identical import batches; Mountain legitimately x2 within a batch.
    for created in ("2026-09-08T00:00:00+00:00", "2026-09-25T00:00:00+00:00"):
        bind("Kaalia of the Vast", "u-kaalia", created)
        bind("Sol Ring", "u-sol", created)
        bind("Mountain", "u-mtn", created)
        bind("Mountain", "u-mtn", created)
    instance_store.create_instance(r, card_name="Opt", ownership_status="not_owned",
                                   considered_for_deck="Holy Hellfire", archidekt_uid="u-opt")


class Phase0Case(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        seed(self.r)
        self._orig = app_module.r
        app_module.r = self.r
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.r = self._orig


class TestInventoryEndpoint(Phase0Case):
    def test_global_inventory_has_rows_from_instances(self):
        rows = self.client.get("/api/inventory").json()["inventory"]
        self.assertEqual(len(rows), 8)  # owned copies only; Opt placeholder excluded
        self.assertTrue(all(r["status"] == "have" for r in rows))
        sol = next(r for r in rows if r["name"] == "Sol Ring")
        self.assertEqual(sol["deck"], "Holy Hellfire")
        self.assertEqual(sol["type"], "Artifact")
        self.assertEqual(sol["categories"], ["Ramp"])
        self.assertEqual(sol["uid"], "u-sol")

    def test_deck_filter_by_name_includes_considered_and_commander(self):
        rows = self.client.get("/api/inventory", params={"deck": "Holy Hellfire"}).json()["inventory"]
        self.assertEqual(len(rows), 9)
        kaalia = [r for r in rows if r["name"] == "Kaalia of the Vast"]
        self.assertTrue(all(r["is_commander"] for r in kaalia))
        self.assertEqual(kaalia[0]["cmc"], 4)  # oracle-keyed meta wins
        self.assertIn("possible", {r["status"] for r in rows})

    def test_query_filter(self):
        rows = self.client.get("/api/inventory", params={"query": "sol"}).json()["inventory"]
        self.assertEqual({r["name"] for r in rows}, {"Sol Ring"})


class TestDeckIdShapes(Phase0Case):
    def test_manage_sees_int_deck_id_instances(self):
        data = self.client.get(f"/api/decks/{DECK_ID}/manage").json()
        self.assertEqual(len(data["cards"]), 8)

    def test_manage_by_registry_id(self):
        data = self.client.get(f"/api/decks/archidekt:{DECK_ID}/manage").json()
        self.assertEqual(len(data["cards"]), 8)

    def test_resync_review_reports_nothing_missing(self):
        data = self.client.get(f"/api/decks/{DECK_ID}/resync-review").json()
        self.assertEqual(data["unbound_cards"], [])

    def test_remove_card_accepts_int_bound_instance(self):
        inst = next(i for i in instance_store.load_all_instances(self.r) if i["card_name"] == "Sol Ring")
        res = self.client.delete(f"/api/decks/archidekt:{DECK_ID}/cards/{inst['id']}")
        self.assertEqual(res.status_code, 200, res.text)


class TestDedupe(Phase0Case):
    def test_dedupe_halves_doubled_import_and_is_idempotent(self):
        rep = dedupe_instances.run(self.r, apply=False)
        self.assertEqual((rep["before"], rep["delete"]), (9, 4))
        dedupe_instances.run(self.r, apply=True)
        left = instance_store.load_all_instances(self.r)
        self.assertEqual(len(left), 5)
        self.assertEqual(sum(1 for i in left if i["card_name"] == "Mountain"), 2)
        self.assertTrue(all(i["created_at"].startswith("2026-09-08") for i in left if i["deck_id"]))
        # indexes cleaned
        self.assertEqual(len(self.r.smembers(f"idx:instances_by_deck:{DECK_ID}")), 4)
        self.assertEqual(dedupe_instances.run(self.r, apply=True)["delete"], 0)

    def test_edited_copy_survives(self):
        later = [i for i in instance_store.load_all_instances(self.r)
                 if i["card_name"] == "Sol Ring" and i["created_at"].startswith("2026-09-25")][0]
        instance_store.update_instance_fields(self.r, later["id"], notes="sleeved")
        dedupe_instances.run(self.r, apply=True)
        self.assertIsNotNone(instance_store.get_instance(self.r, later["id"]))


class TestOrphanDecks(Phase0Case):
    def test_removes_test_deck_only(self):
        rep = remove_orphan_decks.run(self.r, apply=True)
        self.assertEqual(rep["removed"], ["test-deck"])
        self.assertEqual([d["name"] for d in registry.list_decks(self.r)], ["Holy Hellfire"])


class TestCardTagsEndpoint(Phase0Case):
    def test_card_tags_without_legacy_card_key(self):
        res = self.client.get("/api/card/Sol Ring/tags")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["lab_tags"], ["Ramp"])

    def test_unknown_card_404(self):
        self.assertEqual(self.client.get("/api/card/Nope Card/tags").status_code, 404)

    def test_split_card_name_with_slashes(self):
        name = "Fire // Ice"
        self.r.set("lab_tags", json.dumps({name: ["Removal"]}))
        res = self.client.get("/api/card/Fire%20%2F%2F%20Ice/tags")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["card_name"], name)


class TestReviewEndpoint(Phase0Case):
    def test_get_review_by_name(self):
        res = self.client.get("/api/decks/Holy Hellfire/review")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["success"])
        self.assertIn("Holy Hellfire", res.json()["markdown"])

    def test_unknown_deck_404(self):
        self.assertEqual(self.client.get("/api/decks/nope/review").status_code, 404)


if __name__ == "__main__":
    unittest.main()
