"""
Tests for scripts/remove_nonphysical_wishlist_instances.py -- the cleanup
script for wishlist instances wrongly created for non-physical decks by the
now-reversed PR #18 behavior (see docs/PHYSICAL_RESYNC_ADJUDICATION.md
section 2).

Run: pytest tests/test_remove_nonphysical_wishlist_instances.py -q
"""
import importlib.util
import os
import sys
import unittest

import fakeredis

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import registry
import instances as instance_store

_SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "remove_nonphysical_wishlist_instances.py")
_spec = importlib.util.spec_from_file_location("remove_nonphysical_wishlist_instances", _SCRIPT_PATH)
cleanup_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cleanup_script)


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
        self.r = fakeredis.FakeRedis(decode_responses=True)

    def _seed_wishlist_instance(self, registry_id: str, card_name: str = "Sol Ring"):
        """Simulate a leftover instance created by the reversed PR #18 logic
        (bypasses the now-gated upsert_deck path so tests don't depend on
        the fix under test to set up their fixtures)."""
        return instance_store.create_instance(
            self.r, card_name=card_name, ownership_status="not_owned", considered_for_deck=registry_id
        )

    def test_dry_run_identifies_candidates_without_deleting(self):
        digital_deck = registry.upsert_deck(self.r, _normalized_deck("1"), default_status="digital")
        inst = self._seed_wishlist_instance(registry.registry_id_of(digital_deck))

        candidates = cleanup_script.find_candidates(self.r)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], inst["id"])
        # Dry run: nothing removed.
        self.assertIsNotNone(instance_store.get_instance(self.r, inst["id"]))

    def test_physical_deck_wishlist_instances_are_not_candidates(self):
        physical_deck = registry.upsert_deck(self.r, _normalized_deck("2"), default_status="physical")
        # upsert_deck already created real wishlist instances for the physical deck.
        candidates = cleanup_script.find_candidates(self.r)
        self.assertEqual(candidates, [])

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
        physical_deck = registry.upsert_deck(self.r, _normalized_deck("5"), default_status="physical")

        bad_wishlist = self._seed_wishlist_instance(registry.registry_id_of(digital_deck))
        good_wishlist_ids = {inst["id"] for inst in instance_store.list_instances(self.r, deck_id=None)
                              if inst["considered_for_deck"] == registry.registry_id_of(physical_deck)}
        owned = instance_store.create_instance(
            self.r, card_name="Arcane Signet", ownership_status="in_collection",
            considered_for_deck=registry.registry_id_of(digital_deck),
        )

        cleanup_script.cleanup(apply=False)
        # Dry run: nothing deleted.
        self.assertIsNotNone(instance_store.get_instance(self.r, bad_wishlist["id"]))

        self._run_apply()

        self.assertIsNone(instance_store.get_instance(self.r, bad_wishlist["id"]))
        for good_id in good_wishlist_ids:
            self.assertIsNotNone(instance_store.get_instance(self.r, good_id))
        self.assertIsNotNone(instance_store.get_instance(self.r, owned["id"]))

    def _run_apply(self):
        candidates = cleanup_script.find_candidates(self.r)
        for record in candidates:
            instance_store.delete_instance(self.r, record["id"])


if __name__ == "__main__":
    unittest.main()
