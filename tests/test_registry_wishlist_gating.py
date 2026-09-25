"""
Tests for the physical-only wishlist-instance gating in registry.upsert_deck
(reversal of part of PR #18 -- see docs/PHYSICAL_RESYNC_ADJUDICATION.md
section 2): non-physical decks (digital/testing/retired) must have zero
inventory footprint on import or re-import (resync). Only physical decks
get instances, and only via instances.auto_bind_physical at the one-time
digital/testing->physical status transition.

Uses the in-repo in-memory FakeRedis (same shape as the one in
tests/test_instances.py and tests/test_deck_manage_integration.py) rather
than the external `fakeredis` package, so the suite runs under CI's
`pip install -r requirements.txt` with no test-only runtime dependency.

Run: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry
import instances as instance_store


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls registry.py and
    instances.py make, mirroring the FakeRedis in tests/test_instances.py so
    the whole suite uses one consistent in-repo fake instead of the external
    fakeredis package."""

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

    def scan_iter(self, match):
        # Snapshot the key list so callers may mutate the store while iterating,
        # matching real SCAN's tolerance for concurrent modification.
        return iter(self.keys(match))

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


def _normalized_deck(source_id: str, cards=None):
    """Build a minimal NORMALIZED_DECK_SHAPE payload. Tests set the desired
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
        self.r = FakeRedis()

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

    def test_physical_deck_via_status_transition_creates_bound_instances(self):
        # Instance creation is no longer triggered by upsert_deck directly
        # (even for a physical default_status) -- it happens exclusively
        # via instances.auto_bind_physical at the digital/testing->physical
        # status transition. Import as testing first, then flip physical,
        # matching how the real UI/API flow works (registry.set_status).
        deck = registry.upsert_deck(self.r, _normalized_deck("4"), default_status="testing")
        self.assertEqual(instance_store.list_instances(self.r), [])
        result = registry.set_status(self.r, registry.registry_id_of(deck), "physical")
        self.assertEqual(result["deck"]["status"], "physical")
        instances = instance_store.list_instances(self.r)
        self.assertEqual(len(instances), 2)
        for inst in instances:
            self.assertIn(inst["ownership_status"], ("in_deck", "not_owned"))

    def test_resync_of_nonphysical_deck_creates_zero_instances(self):
        # Import once, then re-import (resync) the same deck -- still digital.
        registry.upsert_deck(self.r, _normalized_deck("5"), default_status="digital")
        registry.upsert_deck(self.r, _normalized_deck("5", cards=[{"name": "Sol Ring", "quantity": 1}]))
        self.assertEqual(instance_store.list_instances(self.r), [])

    def test_resync_of_physical_deck_does_not_create_new_instances(self):
        # upsert_deck no longer calls ensure_wishlist_instances at all, for
        # any status. A physical deck's resync (re-import via upsert_deck)
        # must not create instances for remotely-added cards -- that's the
        # whole point of section 3's pending_removal/informational-additions
        # flow instead of automatic creation. Bind via the one-time status
        # transition, then resync and confirm no new instances appear.
        deck = registry.upsert_deck(self.r, _normalized_deck("6"), default_status="testing")
        registry.set_status(self.r, registry.registry_id_of(deck), "physical")
        before = len(instance_store.list_instances(self.r))
        self.assertGreater(before, 0)
        registry.upsert_deck(self.r, _normalized_deck("6"))
        self.assertEqual(len(instance_store.list_instances(self.r)), before)


if __name__ == "__main__":
    unittest.main()
