"""
Tests for the physical-only wishlist-instance gating in registry.upsert_deck
(reversal of part of PR #18 -- see docs/PHYSICAL_RESYNC_ADJUDICATION.md
section 2): non-physical decks (digital/testing/retired) must have zero
inventory footprint on import or re-import (resync). Only physical decks
get automatic wishlist-instance creation via instance_store.ensure_wishlist_instances.

Uses fakeredis (same convention as tests/test_deck_manage_integration.py)
rather than the hand-rolled FakeRedis in tests/test_instances.py, since this
exercises registry.py + instances.py together end-to-end.

Run: pytest tests/test_registry_wishlist_gating.py -q
"""
import os
import sys
import unittest

import fakeredis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry
import instances as instance_store


def _normalized_deck(source_id: str, status_hint: str = None, cards=None):
    """Build a minimal NORMALIZED_DECK_SHAPE payload. `status_hint` is not
    a real provider field -- upsert_deck ignores it; tests set the desired
    status via default_status on first import (deck status is otherwise
    preserved across re-imports, exactly as production does)."""
    return {
        "source": "archidekt",
        "source_id": source_id,
        "name": f"Deck {source_id}",
        "color": "C",
        "commanders": [],
        "commander_uids": [],
        "folder": "Imported",
        "description": "",
        "cards": cards or [{"name": "Sol Ring", "quantity": 1}, {"name": "Arcane Signet", "quantity": 1}],
        "url": "",
    }


class WishlistGatingTestCase(unittest.TestCase):
    def setUp(self):
        self.r = fakeredis.FakeRedis(decode_responses=True)

    def test_digital_deck_import_creates_zero_instances(self):
        deck = registry.upsert_deck(self.r, _normalized_deck("1"), default_status="digital")
        self.assertEqual(deck["status"], "digital")
        self.assertEqual(instance_store.list_instances(self.r), [])

    def test_testing_deck_import_creates_zero_instances(self):
        deck = registry.upsert_deck(self.r, _normalized_deck("2"), default_status="testing")
        self.assertEqual(deck["status"], "testing")
        self.assertEqual(instance_store.list_instances(self.r), [])

    def test_retired_deck_import_creates_zero_instances(self):
        deck = registry.upsert_deck(self.r, _normalized_deck("3"), default_status="retired")
        self.assertEqual(deck["status"], "retired")
        self.assertEqual(instance_store.list_instances(self.r), [])

    def test_physical_deck_import_still_creates_wishlist_instances(self):
        deck = registry.upsert_deck(self.r, _normalized_deck("4"), default_status="physical")
        self.assertEqual(deck["status"], "physical")
        instances = instance_store.list_instances(self.r)
        self.assertEqual(len(instances), 2)
        for inst in instances:
            self.assertEqual(inst["ownership_status"], "not_owned")
            self.assertEqual(inst["considered_for_deck"], registry.registry_id_of(deck))

    def test_resync_of_nonphysical_deck_creates_zero_instances(self):
        # Import once, then re-import (resync) the same deck -- still digital.
        registry.upsert_deck(self.r, _normalized_deck("5"), default_status="digital")
        registry.upsert_deck(self.r, _normalized_deck("5", cards=[{"name": "Sol Ring", "quantity": 1}]))
        self.assertEqual(instance_store.list_instances(self.r), [])

    def test_resync_of_physical_deck_does_not_duplicate_instances(self):
        # Physical resync re-runs ensure_wishlist_instances, which is
        # idempotent -- existing instances aren't duplicated.
        registry.upsert_deck(self.r, _normalized_deck("6"), default_status="physical")
        registry.upsert_deck(self.r, _normalized_deck("6"))
        self.assertEqual(len(instance_store.list_instances(self.r)), 2)


if __name__ == "__main__":
    unittest.main()
