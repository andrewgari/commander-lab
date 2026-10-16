"""
Unit tests for deck_links.py: per-deck provider link CRUD.

Run: python tests/test_deck_links.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deck_links
from deck_links import DeckLinkError


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls registry.py makes."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def _seed_deck(r, **overrides):
    deck = {
        "id": 42,
        "name": "Atraxa Superfriends",
        "registry_id": "archidekt:42",
        "source": "archidekt",
        "source_id": "42",
        "status": "testing",
        "cards": [{"name": "Sol Ring", "quantity": 1}],
    }
    deck.update(overrides)
    r.set("decks", json.dumps([deck]))
    return deck


class TestSetLink(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed_deck(self.r)

    def test_adds_moxfield_link_alongside_source_provider(self):
        deck = deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/abc123")
        self.assertIn("moxfield", deck["links"])
        self.assertEqual(deck["links"]["moxfield"]["deck_id"], "abc123")

    def test_adds_archidekt_link_to_a_moxfield_native_deck(self):
        self.r.store.clear()
        _seed_deck(
            self.r,
            id="moxfield:abc123",
            registry_id="moxfield:abc123",
            source="moxfield",
            source_id="abc123",
        )
        deck = deck_links.set_link(self.r, "moxfield:abc123", "archidekt", "https://archidekt.com/decks/6862011/foo")
        self.assertEqual(deck["links"]["archidekt"]["deck_id"], "6862011")

    def test_edits_existing_link_in_place_not_duplicated(self):
        deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/old111")
        deck = deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/new222")
        self.assertEqual(deck["links"]["moxfield"]["deck_id"], "new222")
        self.assertEqual(len(deck["links"]), 1)

    def test_commandersalt_link_has_no_parser_stored_verbatim(self):
        deck = deck_links.set_link(self.r, "archidekt:42", "commandersalt", "https://commandersalt.com/details/deck/xyz")
        self.assertEqual(deck["links"]["commandersalt"]["identifier"], "https://commandersalt.com/details/deck/xyz")
        # commandersalt has no parse_identifier, so deck_id falls back to the
        # verbatim (stripped) identifier rather than a normalized provider id.
        self.assertEqual(deck["links"]["commandersalt"]["deck_id"], "https://commandersalt.com/details/deck/xyz")

    def test_empty_identifier_clears_link(self):
        deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/abc123")
        deck = deck_links.set_link(self.r, "archidekt:42", "moxfield", "")
        self.assertNotIn("moxfield", deck.get("links", {}))

    def test_unknown_provider_raises(self):
        with self.assertRaises(DeckLinkError):
            deck_links.set_link(self.r, "archidekt:42", "edhrec", "https://edhrec.com/foo")

    def test_unparsable_identifier_raises(self):
        with self.assertRaises(DeckLinkError):
            deck_links.set_link(self.r, "archidekt:42", "archidekt", "not-a-deck")

    def test_unknown_deck_raises(self):
        with self.assertRaises(DeckLinkError):
            deck_links.set_link(self.r, "archidekt:999", "moxfield", "https://moxfield.com/decks/abc123")

    def test_persists_across_calls(self):
        deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/abc123")
        decks = json.loads(self.r.get("decks"))
        self.assertIn("moxfield", decks[0]["links"])


class TestRemoveLink(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed_deck(self.r)
        deck_links.set_link(self.r, "archidekt:42", "moxfield", "https://moxfield.com/decks/abc123")

    def test_removes_link(self):
        deck = deck_links.remove_link(self.r, "archidekt:42", "moxfield")
        self.assertNotIn("moxfield", deck.get("links", {}))

    def test_remove_nonexistent_link_is_a_noop(self):
        deck = deck_links.remove_link(self.r, "archidekt:42", "commandersalt")
        self.assertNotIn("commandersalt", deck.get("links", {}))
        self.assertIn("moxfield", deck.get("links", {}))


if __name__ == "__main__":
    unittest.main()
