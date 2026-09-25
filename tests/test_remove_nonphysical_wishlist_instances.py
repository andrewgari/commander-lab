"""
Tests for scripts/remove_nonphysical_wishlist_instances.py -- the cleanup
script for wishlist instances wrongly created for non-physical decks by the
now-reversed PR #18 behavior (see docs/PHYSICAL_RESYNC_ADJUDICATION.md
section 2).

Uses the in-repo in-memory FakeRedis (same shape as the one in
tests/test_instances.py and tests/test_deck_manage_integration.py) rather
than the external `fakeredis` package, so the suite runs under CI's
`pip install -r requirements.txt` with no test-only runtime dependency.
No test here opens a real Redis connection.

Run: python -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import registry
import instances as instance_store

_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "remove_nonphysical_wishlist_instances.py")
_spec = importlib.util.spec_from_file_location("remove_nonphysical_wishlist_instances", _SCRIPT_PATH)
cleanup_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup_script)


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls registry.py,
    instances.py and the cleanup script make, mirroring the FakeRedis in
    tests/test_instances.py so the whole suite uses one consistent in-repo
    fake instead of the external fakeredis package."""

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
    return {
        "source": "archidekt",
        "source_id": source_id,
        "name": f"Deck {source_id}",
        "color": "C",
        "commanders": [],
        "commander_uids": [],
        "folder": "Imported",
        "description": "",
        "cards": cards or [{"name": "Sol Ring", "quantity": 1}],
        "url": "",
    }


class RemoveNonphysicalWishlistInstancesTestCase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def _seed_wishlist_instance(self, registry_id: str, card_name: str = "Sol Ring"):
        """Simulate a leftover instance created by the reversed PR #18 logic
        (bypasses the now-gated upsert_deck path so tests don't depend on
        the fix under test to set up their fixtures)."""
        return instance_store.create_instance(
            self.r, card_name=card_name, ownership_status="not_owned", considered_for_deck=registry_id
        )

    def _make_physical_deck(self, source_id: str, cards=None):
        """Create a deck and take it through the real digital->physical
        transition, which is the only path that now creates instances."""
        deck = registry.upsert_deck(self.r, _normalized_deck(source_id, cards=cards), default_status="testing")
        registry.set_status(self.r, registry.registry_id_of(deck), "physical")
        return deck

    def test_dry_run_identifies_candidates_without_deleting(self):
        digital_deck = registry.upsert_deck(self.r, _normalized_deck("1"), default_status="digital")
        inst = self._seed_wishlist_instance(registry.registry_id_of(digital_deck))

        candidates = cleanup_script.find_candidates(self.r)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], inst["id"])
        # find_candidates is read-only: nothing removed.
        self.assertIsNotNone(instance_store.get_instance(self.r, inst["id"]))

    def test_physical_deck_wishlist_instances_are_not_candidates(self):
        physical_deck = self._make_physical_deck("2")
        reg_id = registry.registry_id_of(physical_deck)
        # A not_owned placeholder legitimately considered_for a *physical*
        # deck must survive the cleanup.
        kept = self._seed_wishlist_instance(reg_id)

        candidates = cleanup_script.find_candidates(self.r)

        self.assertEqual(candidates, [])
        self.assertIsNotNone(instance_store.get_instance(self.r, kept["id"]))

    def test_owned_instances_are_never_candidates_regardless_of_deck_status(self):
        digital_deck = registry.upsert_deck(self.r, _normalized_deck("3"), default_status="digital")
        reg_id = registry.registry_id_of(digital_deck)
        owned = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection", considered_for_deck=reg_id
        )

        candidates = cleanup_script.find_candidates(self.r)

        self.assertEqual(candidates, [])
        self.assertIsNotNone(instance_store.get_instance(self.r, owned["id"]))

    def test_instance_for_missing_deck_is_a_candidate(self):
        inst = self._seed_wishlist_instance("archidekt:does-not-exist")
        candidates = cleanup_script.find_candidates(self.r)
        self.assertEqual([c["id"] for c in candidates], [inst["id"]])

    def test_apply_deletes_only_the_right_set(self):
        digital_deck = registry.upsert_deck(self.r, _normalized_deck("4"), default_status="digital")
        physical_deck = self._make_physical_deck("5")

        bad_wishlist = self._seed_wishlist_instance(registry.registry_id_of(digital_deck))
        good_wishlist = self._seed_wishlist_instance(registry.registry_id_of(physical_deck))
        owned = instance_store.create_instance(
            self.r, card_name="Arcane Signet", ownership_status="in_collection",
            considered_for_deck=registry.registry_id_of(digital_deck),
        )

        self._run_apply()

        self.assertIsNone(instance_store.get_instance(self.r, bad_wishlist["id"]))
        self.assertIsNotNone(instance_store.get_instance(self.r, good_wishlist["id"]))
        self.assertIsNotNone(instance_store.get_instance(self.r, owned["id"]))

    def _run_apply(self):
        candidates = cleanup_script.find_candidates(self.r)
        for record in candidates:
            instance_store.delete_instance(self.r, record["id"])


if __name__ == "__main__":
    unittest.main()
