#!/usr/bin/env python3
"""
Unit tests for tags.py — the deck-level & per-deck-card tag storage layer.

Usage:
    python tests/test_tags_storage.py

Run with a live Redis (defaults to redis://localhost:6379/0).
Tests are isolated per function and clean up after themselves.
"""
import json
import unittest
import redis

import src.server.domain.tags.deck_tags as tag_store

REDIS_URL = "redis://localhost:6379/0"
DECK_ID = "archidekt:42"
CARD_NAME = "Sol Ring"


def _fresh_redis():
    """Return a connected Redis client with the test key namespace cleared."""
    r = redis.from_url(REDIS_URL, decode_responses=True)
    # Clean any leftover test keys
    pattern = "deck_tags*"
    for key in r.keys(pattern):
        r.delete(key)
    return r


class TestDeckTags(unittest.TestCase):
    """Deck-level tag CRUD."""

    def setUp(self):
        self.r = _fresh_redis()

    def tearDown(self):
        for key in self.r.keys("deck_tags*"):
            self.r.delete(key)
        for key in self.r.keys("deck_card_tags*"):
            self.r.delete(key)

    def test_add_deck_tag(self):
        result = tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        self.assertEqual(result["tag"], "Ramp")
        self.assertEqual(result["source"], "user")
        self.assertIn("created_at", result)
        self.assertIn("version", result)
        self.assertEqual(result["version"], 1)

    def test_get_deck_tags_empty(self):
        tags = tag_store.get_deck_tags(self.r, DECK_ID)
        self.assertEqual(tags, [])

    def test_get_deck_tags_returns_added(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        tags = tag_store.get_deck_tags(self.r, DECK_ID)
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]["tag"], "Ramp")

    def test_deck_tags_returned_newest_first(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        tag_store.add_deck_tag(self.r, DECK_ID, "Draw", source="user")
        tags = tag_store.get_deck_tags(self.r, DECK_ID)
        self.assertEqual(tags[0]["tag"], "Draw")  # version 2
        self.assertEqual(tags[1]["tag"], "Ramp")   # version 1

    def test_add_duplicate_same_source_no_dup(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        r1 = tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        self.assertEqual(r1["version"], 1)  # same entry, no-op
        tags = tag_store.get_deck_tags(self.r, DECK_ID)
        self.assertEqual(len(tags), 1)

    def test_add_same_tag_different_source(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="auto")
        r2 = tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        self.assertEqual(r2["version"], 2)
        tags = tag_store.get_deck_tags(self.r, DECK_ID)
        self.assertEqual(len(tags), 2)

    def test_remove_deck_tag(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        removed = tag_store.remove_deck_tag(self.r, DECK_ID, "Ramp")
        self.assertTrue(removed)
        self.assertEqual(tag_store.get_deck_tags(self.r, DECK_ID), [])

    def test_remove_nonexistent_tag(self):
        removed = tag_store.remove_deck_tag(self.r, DECK_ID, "Ramp")
        self.assertFalse(removed)


class TestDeckCardTags(unittest.TestCase):
    """Per-deck per-card tag CRUD."""

    def setUp(self):
        self.r = _fresh_redis()

    def tearDown(self):
        for key in self.r.keys("deck_tags*"):
            self.r.delete(key)
        for key in self.r.keys("deck_card_tags*"):
            self.r.delete(key)

    def test_add_card_tag(self):
        result = tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        self.assertEqual(result["tag"], "Ramp")
        self.assertEqual(result["source"], "user")
        self.assertEqual(result["version"], 1)

    def test_get_card_tags_for_deck(self):
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        tags = tag_store.get_card_tags_for_deck(self.r, DECK_ID, CARD_NAME)
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]["tag"], "Ramp")

    def test_get_deck_card_tags_full(self):
        tag_store.add_card_tag_to_deck(self.r, DECK_ID, "Sol Ring", "Ramp", source="user")
        tag_store.add_card_tag_to_deck(self.r, DECK_ID, "Arcane Signet", "Ramp", source="user")
        data = tag_store.get_deck_card_tags(self.r, DECK_ID)
        self.assertIn("Sol Ring", data)
        self.assertIn("Arcane Signet", data)

    def test_get_card_tags_empty_when_none(self):
        tags = tag_store.get_card_tags_for_deck(self.r, DECK_ID, CARD_NAME)
        self.assertEqual(tags, [])

    def test_add_duplicate_same_source_no_dup(self):
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        r1 = tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        self.assertEqual(r1["version"], 1)
        tags = tag_store.get_card_tags_for_deck(self.r, DECK_ID, CARD_NAME)
        self.assertEqual(len(tags), 1)

    def test_remove_card_tag(self):
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        removed = tag_store.remove_card_tag_from_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp"
        )
        self.assertTrue(removed)
        self.assertEqual(
            tag_store.get_card_tags_for_deck(self.r, DECK_ID, CARD_NAME), []
        )

    def test_remove_card_tag_nonexistent(self):
        removed = tag_store.remove_card_tag_from_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp"
        )
        self.assertFalse(removed)

    def test_same_card_different_tags(self):
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Fast Mana", source="user"
        )
        tags = tag_store.get_card_tags_for_deck(self.r, DECK_ID, CARD_NAME)
        self.assertEqual(len(tags), 2)


class TestClearDeckTags(unittest.TestCase):
    def setUp(self):
        self.r = _fresh_redis()

    def tearDown(self):
        for key in self.r.keys("deck_tags*"):
            self.r.delete(key)
        for key in self.r.keys("deck_card_tags*"):
            self.r.delete(key)

    def test_clear_removes_all(self):
        tag_store.add_deck_tag(self.r, DECK_ID, "Ramp", source="user")
        tag_store.add_card_tag_to_deck(
            self.r, DECK_ID, CARD_NAME, "Ramp", source="user"
        )
        tag_store.clear_deck_tags(self.r, DECK_ID)
        self.assertIsNone(self.r.get(tag_store._deck_tags_key(DECK_ID)))
        self.assertIsNone(
            self.r.get(tag_store._deck_card_tags_key(DECK_ID))
        )


class TestVersionIsolation(unittest.TestCase):
    """Verify each deck gets its own version counter."""

    def setUp(self):
        self.r = _fresh_redis()

    def tearDown(self):
        for key in self.r.keys("deck_tags*"):
            self.r.delete(key)
        for key in self.r.keys("deck_card_tags*"):
            self.r.delete(key)

    def test_different_decks_independent(self):
        tag_store.add_deck_tag(self.r, "archidekt:1", "Ramp", source="user")
        tag_store.add_deck_tag(self.r, "archidekt:2", "Ramp", source="user")
        tags_1 = tag_store.get_deck_tags(self.r, "archidekt:1")
        tags_2 = tag_store.get_deck_tags(self.r, "archidekt:2")
        self.assertEqual(len(tags_1), 1)
        self.assertEqual(len(tags_2), 1)
        self.assertEqual(tags_1[0]["version"], 1)
        self.assertEqual(tags_2[0]["version"], 1)


if __name__ == "__main__":
    unittest.main()
