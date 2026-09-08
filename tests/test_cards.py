"""
Unit tests for the card ORM (cards.py) using the same in-memory FakeRedis
stub pattern as tests/test_instances.py.

Run: python tests/test_cards.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cards as card_store


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls cards.py makes."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

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


SOL_RING_ORACLE_ID = "62a7d306-de17-4b2a-92e1-27a4a1cccb43"


class TestCardOrm(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_upsert_and_get_card(self):
        record = card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID,
            "name": "Sol Ring",
            "type": "Artifact",
            "cmc": 1,
        })
        fetched = card_store.get_card(self.r, SOL_RING_ORACLE_ID)
        self.assertEqual(fetched, record)
        self.assertEqual(fetched["name"], "Sol Ring")

    def test_get_card_unknown_returns_none(self):
        self.assertIsNone(card_store.get_card(self.r, "00000000-0000-0000-0000-000000000000"))

    def test_get_card_by_name_resolves_through_index(self):
        card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID,
            "name": "Sol Ring",
            "type": "Artifact",
        })
        fetched = card_store.get_card_by_name(self.r, "Sol Ring")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["oracle_id"], SOL_RING_ORACLE_ID)

    def test_get_card_by_name_unknown_returns_none(self):
        self.assertIsNone(card_store.get_card_by_name(self.r, "Nonexistent Card"))

    def test_upsert_requires_oracle_id_and_name(self):
        with self.assertRaises(ValueError):
            card_store.upsert_card(self.r, {"name": "Sol Ring"})
        with self.assertRaises(ValueError):
            card_store.upsert_card(self.r, {"oracle_id": SOL_RING_ORACLE_ID})

    def test_upsert_overwrites_existing_record(self):
        card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID, "name": "Sol Ring", "cmc": 1,
        })
        card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID, "name": "Sol Ring", "cmc": 2,
        })
        fetched = card_store.get_card(self.r, SOL_RING_ORACLE_ID)
        self.assertEqual(fetched["cmc"], 2)

    def test_is_oracle_id(self):
        self.assertTrue(card_store.is_oracle_id(SOL_RING_ORACLE_ID))
        self.assertTrue(card_store.is_oracle_id(SOL_RING_ORACLE_ID.upper()))
        self.assertFalse(card_store.is_oracle_id("Sol Ring"))
        self.assertFalse(card_store.is_oracle_id(""))
        self.assertFalse(card_store.is_oracle_id(None))

    def test_card_meta_keyspace_untouched(self):
        """cards.py must never read or write card_meta:* directly."""
        self.r.set("card_meta:Sol Ring", json.dumps({"type": "Artifact"}))
        card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID, "name": "Sol Ring", "cmc": 1,
        })
        # legacy key untouched
        self.assertEqual(
            json.loads(self.r.get("card_meta:Sol Ring")), {"type": "Artifact"}
        )


if __name__ == "__main__":
    unittest.main()
