"""
Unit tests for registry.py's decklist helpers, using the same FakeRedis
stand-in pattern as tests/test_instances.py.

Run: python tests/test_registry.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls registry.py makes."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class TestAddCardToDecklist(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        deck = {
            "id": 42,
            "name": "Atraxa Superfriends",
            "registry_id": "archidekt:42",
            "source": "archidekt",
            "source_id": "42",
            "status": "physical",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        self.r.set("decks", json.dumps([deck]))

    def test_appends_new_card(self):
        updated = registry.add_card_to_decklist(self.r, "archidekt:42", "Arcane Signet")
        names = [c["name"] for c in updated["cards"]]
        self.assertIn("Arcane Signet", names)
        self.assertEqual(len(names), 2)

    def test_does_not_duplicate_existing_card(self):
        updated = registry.add_card_to_decklist(self.r, "archidekt:42", "Sol Ring")
        names = [c["name"] for c in updated["cards"]]
        self.assertEqual(names.count("Sol Ring"), 1)

    def test_persists_across_calls(self):
        registry.add_card_to_decklist(self.r, "archidekt:42", "Arcane Signet")
        decks = json.loads(self.r.get("decks"))
        names = [c["name"] for c in decks[0]["cards"]]
        self.assertIn("Arcane Signet", names)

    def test_unknown_deck_raises(self):
        with self.assertRaises(ValueError):
            registry.add_card_to_decklist(self.r, "archidekt:999", "Sol Ring")

    def test_accepts_lookup_by_numeric_id(self):
        updated = registry.add_card_to_decklist(self.r, "42", "Arcane Signet")
        names = [c["name"] for c in updated["cards"]]
        self.assertIn("Arcane Signet", names)


if __name__ == "__main__":
    unittest.main()
