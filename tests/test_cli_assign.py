"""
Unit tests for the non-interactive CLI assign flow (cli.py) using the same
in-memory FakeRedis pattern as tests/test_instances.py.

Run: python tests/test_cli_assign.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli
import instances as instance_store
import registry


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls cli.py/registry.py/
    instances.py make."""

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


def make_deck(r, registry_id="archidekt:1", source="archidekt", source_id="1", name="Deck A"):
    decks = registry._load_decks(r)
    decks.append({
        "id": 1,
        "name": name,
        "color": "",
        "commanders": [],
        "commander_uids": [],
        "folder": "Imported",
        "status": "testing",
        "description": "",
        "source": source,
        "source_id": source_id,
        "registry_id": registry_id,
        "cards": [],
        "url": "",
    })
    registry._save_decks(r, decks)


class TestAssignCard(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_successful_assign_by_card_name(self):
        make_deck(self.r)
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")

        code, result = cli.assign_card(self.r, card="Sol Ring", deck_ref="archidekt:1")

        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["card_name"], "Sol Ring")
        self.assertEqual(result["deck_id"], "archidekt:1")
        self.assertEqual(result["instance"]["ownership_status"], "in_deck")

    def test_successful_assign_by_instance_id(self):
        make_deck(self.r)
        record = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")

        code, result = cli.assign_card(self.r, instance_id=record["id"], deck_ref="archidekt:1")

        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["instance"]["id"], record["id"])

    def test_successful_assign_by_source_id(self):
        make_deck(self.r, registry_id="moxfield:abc123", source="moxfield", source_id="abc123", name="Deck B")
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")

        code, result = cli.assign_card(self.r, card="Sol Ring", deck_ref="abc123")

        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["deck_id"], "moxfield:abc123")

    def test_ambiguous_match_returns_exit_code_1(self):
        make_deck(self.r)
        instance_store.create_instance(self.r, card_name="Island", ownership_status="in_collection")
        instance_store.create_instance(self.r, card_name="Island", ownership_status="in_collection")

        code, result = cli.assign_card(self.r, card="Island", deck_ref="archidekt:1")

        self.assertEqual(code, 1)
        self.assertFalse(result["success"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_no_match_found_returns_exit_code_2(self):
        make_deck(self.r)

        code, result = cli.assign_card(self.r, card="Nonexistent Card", deck_ref="archidekt:1")

        self.assertEqual(code, 2)
        self.assertFalse(result["success"])
        self.assertIn("no in_collection instance found", result["error"])

    def test_instance_id_not_found_returns_exit_code_2(self):
        make_deck(self.r)

        code, result = cli.assign_card(self.r, instance_id="does-not-exist", deck_ref="archidekt:1")

        self.assertEqual(code, 2)
        self.assertFalse(result["success"])
        self.assertIn("instance not found", result["error"])

    def test_deck_not_found_returns_exit_code_2(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")

        code, result = cli.assign_card(self.r, card="Sol Ring", deck_ref="archidekt:999")

        self.assertEqual(code, 2)
        self.assertFalse(result["success"])
        self.assertIn("deck not found", result["error"])

    def test_missing_deck_ref_returns_exit_code_1(self):
        code, result = cli.assign_card(self.r, card="Sol Ring", deck_ref=None)

        self.assertEqual(code, 1)
        self.assertFalse(result["success"])
        self.assertIn("destination deck is required", result["error"])

    def test_missing_card_and_instance_id_returns_exit_code_1(self):
        make_deck(self.r)

        code, result = cli.assign_card(self.r, deck_ref="archidekt:1")

        self.assertEqual(code, 1)
        self.assertFalse(result["success"])
        self.assertIn("one of --card or --instance-id is required", result["error"])

    def test_illegal_transition_returns_exit_code_3(self):
        make_deck(self.r, registry_id="archidekt:1", name="Deck A")
        # Bind a physical decklist that locks any in_deck instance for this deck.
        record = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck",
            deck_id="archidekt:1", deck_name="Deck A",
        )
        registry.set_status(self.r, "archidekt:1", "physical")

        # Reassigning an instance already locked in a physical deck to a
        # different deck should raise InstanceError -> exit code 3.
        make_deck(self.r, registry_id="archidekt:2", source_id="2", name="Deck B")
        code, result = cli.assign_card(self.r, instance_id=record["id"], deck_ref="archidekt:2")

        self.assertEqual(code, 3)
        self.assertFalse(result["success"])


class TestResolveDeck(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_resolve_by_registry_id(self):
        make_deck(self.r, registry_id="archidekt:1")
        deck = cli._resolve_deck(self.r, "archidekt:1")
        self.assertIsNotNone(deck)
        self.assertEqual(deck["registry_id"], "archidekt:1")

    def test_resolve_by_source_id(self):
        make_deck(self.r, registry_id="moxfield:abc123", source="moxfield", source_id="abc123")
        deck = cli._resolve_deck(self.r, "abc123")
        self.assertIsNotNone(deck)
        self.assertEqual(deck["source_id"], "abc123")

    def test_resolve_missing_returns_none(self):
        deck = cli._resolve_deck(self.r, "nope")
        self.assertIsNone(deck)


if __name__ == "__main__":
    unittest.main()
