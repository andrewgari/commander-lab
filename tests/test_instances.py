"""
Unit tests for the instance registry (instances.py) using fakeredis-style
manual stubbing via the real redis client against a test DB, OR a lightweight
in-memory fake if redis isn't reachable.

Run: python tests/test_instances.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instances as instance_store
from instances import InstanceError


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls instances.py makes."""

    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sinter(self, *keys):
        sets = [self.sets.get(k, set()) for k in keys]
        if not sets:
            return set()
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return result

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, parent):
        self.parent = parent
        self.ops = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.parent, name)(*args, **kwargs)
        self.ops = []


class TestInstanceRegistry(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_create_instance_defaults(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring")
        self.assertEqual(record["ownership_status"], "not_owned")
        self.assertIsNone(record["deck_id"])
        self.assertEqual(len(record["history"]), 1)

    def test_create_instance_requires_deck_id_for_in_deck(self):
        with self.assertRaises(InstanceError):
            instance_store.create_instance(
                self.r, card_name="Sol Ring", ownership_status="in_deck"
            )

    def test_create_instance_rejects_deck_id_unless_in_deck(self):
        with self.assertRaises(InstanceError):
            instance_store.create_instance(
                self.r, card_name="Sol Ring", ownership_status="in_collection", deck_id=1
            )

    def test_unique_instances_for_duplicate_cards(self):
        a = instance_store.create_instance(self.r, card_name="Sol Ring")
        b = instance_store.create_instance(self.r, card_name="Sol Ring")
        self.assertNotEqual(a["id"], b["id"])
        rollup = instance_store.card_rollup(self.r, "Sol Ring")
        self.assertEqual(rollup["total_instances"], 2)

    def test_valid_transition_chain(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="not_owned")
        iid = record["id"]
        record = instance_store.transition_status(self.r, iid, "in_mail")
        self.assertEqual(record["ownership_status"], "in_mail")
        record = instance_store.transition_status(self.r, iid, "in_collection")
        self.assertEqual(record["ownership_status"], "in_collection")
        record = instance_store.transition_status(self.r, iid, "in_deck", deck_id=42, deck_name="Meren")
        self.assertEqual(record["ownership_status"], "in_deck")
        self.assertEqual(record["deck_id"], 42)
        self.assertEqual(len(record["history"]), 4)

    def test_illegal_transition_rejected(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="not_owned")
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, record["id"], "in_deck", deck_id=1)

    def test_transition_to_in_deck_requires_deck_id(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, record["id"], "in_deck")

    def test_leaving_in_deck_clears_deck_id(self):
        record = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=42, deck_name="Meren"
        )
        record = instance_store.transition_status(self.r, record["id"], "in_collection")
        self.assertIsNone(record["deck_id"])
        self.assertEqual(record["deck_name"], "")

    def test_deck_index_updated_on_transition(self):
        record = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=1, deck_name="Deck A"
        )
        iid = record["id"]
        deck_instances = instance_store.list_instances(self.r, deck_id=1)
        self.assertEqual(len(deck_instances), 1)
        self.assertEqual(deck_instances[0]["id"], iid)
        instance_store.transition_status(self.r, iid, "in_collection")
        remaining = instance_store.list_instances(self.r, deck_id=1)
        self.assertEqual(len(remaining), 0)

    def test_list_instances_filters(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=1, deck_name="Deck A")
        instance_store.create_instance(self.r, card_name="Arcane Signet", ownership_status="in_collection")

        self.assertEqual(len(instance_store.list_instances(self.r, card_name="Sol Ring")), 2)
        self.assertEqual(len(instance_store.list_instances(self.r, ownership_status="in_collection")), 2)
        self.assertEqual(len(instance_store.list_instances(self.r, deck_id=1)), 1)

    def test_update_instance_fields(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring")
        updated = instance_store.update_instance_fields(self.r, record["id"], condition="LP", notes="bent corner")
        self.assertEqual(updated["condition"], "LP")
        self.assertEqual(updated["notes"], "bent corner")

    def test_update_instance_fields_rejects_status_change(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring")
        with self.assertRaises(InstanceError):
            instance_store.update_instance_fields(self.r, record["id"], ownership_status="in_mail")

    def test_delete_instance(self):
        record = instance_store.create_instance(self.r, card_name="Sol Ring")
        self.assertTrue(instance_store.delete_instance(self.r, record["id"]))
        self.assertIsNone(instance_store.get_instance(self.r, record["id"]))
        self.assertFalse(instance_store.delete_instance(self.r, record["id"]))

    def test_card_rollup_by_deck(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=1, deck_name="Deck A")
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=2, deck_name="Deck B")
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="not_owned")

        rollup = instance_store.card_rollup(self.r, "Sol Ring")
        self.assertEqual(rollup["total_instances"], 3)
        self.assertEqual(rollup["owned"], 2)
        self.assertEqual(rollup["by_deck"], {"Deck A": 1, "Deck B": 1})

    def test_auto_bind_physical_reuses_and_creates(self):
        # One pre-existing in_collection copy should be reused; the rest created.
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        report = instance_store.auto_bind_physical(
            self.r,
            deck_registry_id="archidekt:1",
            deck_name="Deck A",
            decklist=[{"name": "Sol Ring", "quantity": 2}, {"name": "Arcane Signet", "quantity": 1}],
        )
        self.assertEqual(report["cards"]["Sol Ring"], {"bound": 2, "created": 1})
        self.assertEqual(report["cards"]["Arcane Signet"], {"bound": 1, "created": 1})
        self.assertEqual(report["shortfalls"], [])
        sol_ring_in_deck = instance_store.list_instances(self.r, card_name="Sol Ring", deck_id="archidekt:1")
        self.assertEqual(len(sol_ring_in_deck), 2)

    def test_physical_deck_lock_blocks_direct_transition(self):
        import registry

        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id="archidekt:1", deck_name="Deck A"
        )
        decks_payload = [{"id": "archidekt:1", "registry_id": "archidekt:1", "name": "Deck A", "status": "physical"}]
        self.r.set("decks", json.dumps(decks_payload))

        iid = instance_store.list_instances(self.r, card_name="Sol Ring")[0]["id"]
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, iid, "in_collection")

    def test_physical_deck_lock_released_when_deck_unlocked(self):
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id="archidekt:1", deck_name="Deck A"
        )
        decks_payload = [{"id": "archidekt:1", "registry_id": "archidekt:1", "name": "Deck A", "status": "testing"}]
        self.r.set("decks", json.dumps(decks_payload))

        iid = instance_store.list_instances(self.r, card_name="Sol Ring")[0]["id"]
        record = instance_store.transition_status(self.r, iid, "in_collection")
        self.assertEqual(record["ownership_status"], "in_collection")

    def test_physical_deck_lock_blocks_reassignment_to_another_deck(self):
        # Same ownership_status (in_deck) but a different deck_id — a transfer
        # away from a physical deck must be blocked just like a status change.
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id="archidekt:1", deck_name="Deck A"
        )
        decks_payload = [{"id": "archidekt:1", "registry_id": "archidekt:1", "name": "Deck A", "status": "physical"}]
        self.r.set("decks", json.dumps(decks_payload))

        iid = instance_store.list_instances(self.r, card_name="Sol Ring")[0]["id"]
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, iid, "in_deck", deck_id="archidekt:2", deck_name="Deck B")


if __name__ == "__main__":
    unittest.main()
