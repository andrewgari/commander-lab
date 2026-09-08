"""
Unit tests for scripts/migrate_card_meta_to_oracle_id.py.

Uses the same in-memory FakeRedis stub pattern as tests/test_cards.py, and
mocks the Scryfall network call (resolve_oracle_id) so tests never hit the
real API.

Run: python tests/test_migrate_card_meta_to_oracle_id.py
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import cards as card_store
import migrate_card_meta_to_oracle_id as migrate_mod


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls the migration script
    and cards.py make."""

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
LOTUS_ORACLE_ID = "bd8fa327-dd41-4737-8f19-2cf5eb1f74d2"


def seed_meta(r, name, **meta):
    r.set(f"card_meta:{name}", json.dumps(meta))


class TestMigrateCardMetaToOracleId(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def _run(self, apply, resolver):
        with mock.patch.object(migrate_mod, "resolve_oracle_id", side_effect=resolver):
            migrate_mod.migrate(apply=apply, r=self.r)

    def test_dry_run_does_not_write_card_records(self):
        seed_meta(self.r, "Sol Ring", type="Artifact", cmc=1)

        self._run(apply=False, resolver=lambda name: SOL_RING_ORACLE_ID)

        self.assertIsNone(card_store.get_card(self.r, SOL_RING_ORACLE_ID))
        self.assertIsNone(card_store.get_card_by_name(self.r, "Sol Ring"))
        # legacy key untouched
        self.assertIn("card_meta:Sol Ring", self.r.store)

    def test_dry_run_is_idempotent(self):
        """Running the dry run multiple times must never mutate state, and
        must report the same counts each time."""
        seed_meta(self.r, "Sol Ring", type="Artifact", cmc=1)
        seed_meta(self.r, "Black Lotus", type="Artifact", cmc=0)

        resolver = lambda name: {
            "Sol Ring": SOL_RING_ORACLE_ID,
            "Black Lotus": LOTUS_ORACLE_ID,
        }[name]

        snapshot_before = dict(self.r.store)
        self._run(apply=False, resolver=resolver)
        snapshot_after_first = dict(self.r.store)
        self._run(apply=False, resolver=resolver)
        snapshot_after_second = dict(self.r.store)

        self.assertEqual(snapshot_before, snapshot_after_first)
        self.assertEqual(snapshot_after_first, snapshot_after_second)
        self.assertIsNone(card_store.get_card(self.r, SOL_RING_ORACLE_ID))
        self.assertIsNone(card_store.get_card(self.r, LOTUS_ORACLE_ID))

    def test_apply_persists_card_and_index(self):
        seed_meta(
            self.r, "Sol Ring", type="Artifact", color="C", cmc=1,
            oracle_text="Add {C}{C}.",
        )

        self._run(apply=True, resolver=lambda name: SOL_RING_ORACLE_ID)

        record = card_store.get_card(self.r, SOL_RING_ORACLE_ID)
        self.assertIsNotNone(record)
        self.assertEqual(record["name"], "Sol Ring")
        self.assertEqual(record["oracle_id"], SOL_RING_ORACLE_ID)
        self.assertEqual(record["cmc"], 1)

        by_name = card_store.get_card_by_name(self.r, "Sol Ring")
        self.assertEqual(by_name, record)

        # legacy key left untouched by --apply too
        self.assertEqual(
            json.loads(self.r.get("card_meta:Sol Ring"))["type"], "Artifact"
        )

    def test_apply_skips_already_indexed_names(self):
        seed_meta(self.r, "Sol Ring", type="Artifact", cmc=1)
        card_store.upsert_card(self.r, {
            "oracle_id": SOL_RING_ORACLE_ID, "name": "Sol Ring", "cmc": 99,
        })

        resolver = mock.Mock(side_effect=AssertionError(
            "resolve_oracle_id should not be called for already-indexed names"
        ))
        self._run(apply=True, resolver=resolver)

        # untouched by the migration since it was already indexed
        record = card_store.get_card(self.r, SOL_RING_ORACLE_ID)
        self.assertEqual(record["cmc"], 99)

    def test_unresolved_names_are_skipped_without_writes(self):
        seed_meta(self.r, "Totally Fake Card", type="Unknown")

        self._run(apply=True, resolver=lambda name: None)

        self.assertIsNone(card_store.get_card_by_name(self.r, "Totally Fake Card"))
        # legacy key still present, untouched
        self.assertIn("card_meta:Totally Fake Card", self.r.store)

    def test_never_deletes_or_mutates_card_meta_keys(self):
        seed_meta(self.r, "Sol Ring", type="Artifact", cmc=1)
        original = self.r.get("card_meta:Sol Ring")

        self._run(apply=True, resolver=lambda name: SOL_RING_ORACLE_ID)

        self.assertEqual(self.r.get("card_meta:Sol Ring"), original)


if __name__ == "__main__":
    unittest.main()
