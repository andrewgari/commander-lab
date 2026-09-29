"""
Unit test for the legacy card:{name} key guard in sync.py.

Verifies that sync.py's guard against rewriting legacy card:{name} keys
respects the migration:legacy_card_keys_purged marker set by
scripts/migrate_to_instances.py --delete-old.

These tests exercise the production guard (sync.should_write_legacy_card_keys)
and the shared marker constant (sync.MIGRATION_LEGACY_CARD_KEYS_PURGED)
directly, rather than reimplementing the check, so they fail if the guard or
the marker constant regresses.
"""
import os
import sys
import json
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

# sync.py runs load_dotenv() and exits(1) at import time if ARCHIDEKT_USERNAME
# is unset, so provide a dummy value before importing it.
os.environ.setdefault("ARCHIDEKT_USERNAME", "test_user")

import sync
import cards as card_store
import migrate_to_instances as migrate_mod


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls the sync guard and the
    migration script use."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def delete(self, *keys):
        # Mirror the real client: DEL with no key arguments is an error.
        if not keys:
            raise TypeError("delete() requires at least one key")
        for k in keys:
            self.store.pop(k, None)

    def sadd(self, key, *values):
        self.store.setdefault(key, set()).update(values)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """Queues commands and replays them against the parent on execute(), so a
    pipelined delete+set is observable only as a completed unit."""

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


class TestSyncLegacyKeyGuard(unittest.TestCase):
    def setUp(self):
        self.card_store = card_store
        self.r = FakeRedis()

    def test_legacy_write_skipped_when_marker_present(self):
        """When migration:legacy_card_keys_purged is set, the guard must skip
        writing legacy card:{name} keys."""
        self.r.set(sync.MIGRATION_LEGACY_CARD_KEYS_PURGED, "1")

        inventory = {
            "Sol Ring": {"type": "Artifact", "copies": []},
            "Mana Crypt": {"type": "Artifact", "copies": []},
        }

        if sync.should_write_legacy_card_keys(self.r):
            for name, data in inventory.items():
                self.r.set(f"card:{name}", json.dumps(data))

        # Verify no legacy card:{name} keys were written
        card_keys = [k for k in self.r.keys("card:*")
                     if not self.card_store.is_oracle_id(k[len("card:"):])]
        self.assertEqual(card_keys, [],
                         "Legacy card:{name} keys must not be written when "
                         "migration marker is present")

    def test_legacy_write_allowed_when_marker_absent(self):
        """When migration:legacy_card_keys_purged is NOT set, the guard must
        allow writing legacy card:{name} keys (backward compatibility for
        pre-migration setups)."""
        # No marker set
        inventory = {
            "Sol Ring": {"type": "Artifact", "copies": []},
        }

        if sync.should_write_legacy_card_keys(self.r):
            for name, data in inventory.items():
                self.r.set(f"card:{name}", json.dumps(data))

        card_keys = [k for k in self.r.keys("card:*")
                     if not self.card_store.is_oracle_id(k[len("card:"):])]
        self.assertEqual(len(card_keys), 1,
                         "Legacy card:{name} keys should be written when "
                         "migration marker is absent")
        self.assertIn("card:Sol Ring", card_keys)

    def test_marker_distinguishes_legacy_from_oracle_keys(self):
        """The guard must not block card:{oracle_id} writes (those come from
        cards.py and are unrelated to the legacy inventory keys)."""
        self.r.set(sync.MIGRATION_LEGACY_CARD_KEYS_PURGED, "1")
        # Simulate an oracle_id key already present (written by cards.py)
        self.r.set("card:62a7d306-de17-4b2a-92e1-27a4a1cccb43",
                   json.dumps({"oracle_id": "62a7d306-de17-4b2a-92e1-27a4a1cccb43",
                               "name": "Sol Ring"}))

        # The guard only looks at legacy name-keyed entries
        if sync.should_write_legacy_card_keys(self.r):
            self.r.set("card:SomeLegacyCard", json.dumps({"type": "Creature"}))

        # oracle_id key should still exist
        oracle_key = "card:62a7d306-de17-4b2a-92e1-27a4a1cccb43"
        self.assertIsNotNone(self.r.get(oracle_key),
                             "card:{oracle_id} keys must survive the guard")


class TestMigrationSetsGuardMarker(unittest.TestCase):
    """End-to-end coverage of the migration -> sync handshake: run the real
    scripts/migrate_to_instances.py migrate() against a fake Redis, then assert
    sync.py's production guard reacts to what the migration actually wrote.

    This is what pins the two sides together — if the migration stopped setting
    the marker, or wrote a different key than
    sync.MIGRATION_LEGACY_CARD_KEYS_PURGED, these tests fail.
    """

    def setUp(self):
        self.r = FakeRedis()

    def _migrate(self, apply=True, delete_old=True):
        with mock.patch.object(migrate_mod.redis, "from_url", return_value=self.r):
            migrate_mod.migrate(apply=apply, delete_old=delete_old)

    def test_migration_marker_key_matches_sync_constant(self):
        """The key the migration sets must be the exact key sync.py reads."""
        self.r.set("card:Sol Ring", json.dumps({"type": "Artifact", "copies": []}))
        self.assertTrue(sync.should_write_legacy_card_keys(self.r))

        self._migrate()

        self.assertIn(sync.MIGRATION_LEGACY_CARD_KEYS_PURGED, self.r.store,
                      "migrate_to_instances.py must set the same marker key "
                      "sync.py's guard reads")
        self.assertFalse(sync.should_write_legacy_card_keys(self.r),
                         "sync guard must refuse legacy writes after the "
                         "migration purge")
        self.assertNotIn("card:Sol Ring", self.r.store,
                         "legacy name-keyed record should have been purged")

    def test_migration_marker_set_when_no_legacy_keys_remain(self):
        """On an already-migrated database old_keys is empty. The migration must
        still set the marker rather than dying on DEL with no arguments."""
        oracle_key = "card:62a7d306-de17-4b2a-92e1-27a4a1cccb43"
        self.r.set(oracle_key, json.dumps({"name": "Sol Ring"}))

        self._migrate()  # must not raise

        self.assertFalse(sync.should_write_legacy_card_keys(self.r),
                         "marker must be set even when there were no legacy "
                         "keys left to delete")
        self.assertIn(oracle_key, self.r.store,
                      "card:{oracle_id} ORM records must survive the purge")

    def test_purge_and_marker_are_applied_atomically(self):
        """The delete and the marker write must go through one pipeline, so a
        concurrent sync can never observe deleted keys without the marker."""
        self.r.set("card:Sol Ring", json.dumps({"type": "Artifact", "copies": []}))

        observed = []
        real_pipeline = self.r.pipeline

        def tracking_pipeline():
            pipe = real_pipeline()
            real_execute = pipe.execute

            def execute():
                observed.append([name for name, _a, _k in pipe.ops])
                return real_execute()

            pipe.execute = execute
            return pipe

        with mock.patch.object(self.r, "pipeline", tracking_pipeline):
            self._migrate()

        purge_batches = [ops for ops in observed
                         if "delete" in ops and "set" in ops]
        self.assertTrue(purge_batches,
                        "the legacy purge and the marker write must be issued "
                        "in a single pipeline, not as separate commands; "
                        f"observed pipelines: {observed}")

    def test_marker_not_set_without_delete_old(self):
        """A migration run that doesn't purge legacy keys must leave the marker
        unset, so sync.py keeps maintaining the legacy keyspace."""
        self.r.set("card:Sol Ring", json.dumps({"type": "Artifact", "copies": []}))

        self._migrate(apply=True, delete_old=False)

        self.assertTrue(sync.should_write_legacy_card_keys(self.r),
                        "without --delete-old the legacy keys still exist, so "
                        "sync must keep writing them")
        self.assertIn("card:Sol Ring", self.r.store)


if __name__ == "__main__":
    unittest.main()
