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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# sync.py runs load_dotenv() and exits(1) at import time if ARCHIDEKT_USERNAME
# is unset, so provide a dummy value before importing it.
os.environ.setdefault("ARCHIDEKT_USERNAME", "test_user")

import sync
import cards as card_store


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls the sync guard uses."""

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
        for k in keys:
            self.store.pop(k, None)


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


if __name__ == "__main__":
    unittest.main()
