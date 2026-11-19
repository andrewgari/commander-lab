"""
Unit tests for deck_sync.py: dual-source fetch + merge + sync, using the
FakeRedis stand-in pattern from tests/test_registry.py / tests/test_deck_links.py.
Provider network calls are stubbed by monkeypatching providers.PROVIDERS.

Run: python tests/test_deck_sync.py
"""
import os
import sys
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.deck_links as deck_links
import src.server.domain.decks.deck_sync as deck_sync
import src.server.domain.decks.registry as registry
import src.server.domain.inventory.instances as instance_store
from src.server.domain.decks.deck_sync import DeckSyncError, ProviderFailure
from src.server.infra.providers import PROVIDERS, ProviderError
from tests.test_physical_resync_diff import FakeRedis as PhysicalFakeRedis


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def _seed_linked_deck(r, status="testing"):
    deck = {
        "id": 42,
        "name": "Atraxa Superfriends",
        "registry_id": "archidekt:42",
        "source": "archidekt",
        "source_id": "42",
        "status": status,
        "cards": [{"name": "Sol Ring", "quantity": 1}],
        "links": {
            "archidekt": {"identifier": "42", "deck_id": "42"},
            "moxfield": {"identifier": "abc123", "deck_id": "abc123"},
        },
    }
    r.set("decks", json.dumps([deck]))
    return deck


class FakeArchidektProvider:
    def __init__(self, deck=None, error=None):
        self._deck = deck
        self._error = error

    def fetch_deck(self, identifier):
        if self._error:
            raise self._error
        return self._deck

    def parse_identifier(self, identifier):
        return identifier


class FakeMoxfieldProvider:
    def __init__(self, deck=None, error=None):
        self._deck = deck
        self._error = error

    def fetch_deck(self, identifier):
        if self._error:
            raise self._error
        return self._deck

    def parse_identifier(self, identifier):
        return identifier


ARCHIDEKT_DECK = {
    "source": "archidekt",
    "source_id": "42",
    "name": "Atraxa Superfriends",
    "color": "WUBG",
    "commanders": ["Atraxa, Praetors' Voice"],
    "commander_uids": ["uid-atraxa"],
    "folder": "Imported",
    "description": "",
    "cards": [
        {"name": "Sol Ring", "quantity": 1},
        {"name": "Arcane Signet", "quantity": 1},
        {"name": "Cultivate", "quantity": 1},
    ],
    "url": "https://archidekt.com/decks/42",
}

MOXFIELD_DECK = {
    "source": "moxfield",
    "source_id": "abc123",
    "name": "Atraxa Superfriends",
    "color": "WUBG",
    "commanders": ["Atraxa, Praetors' Voice"],
    "commander_uids": ["uid-atraxa"],
    "folder": "Imported",
    "description": "",
    "cards": [
        {"name": "Sol Ring", "quantity": 1},
        {"name": "Arcane Signet", "quantity": 2},  # conflicting quantity
        {"name": "Rhystic Study", "quantity": 1},  # moxfield-only card
    ],
    "url": "https://moxfield.com/decks/abc123",
}


class TestMergeNormalizedDecks(unittest.TestCase):
    def test_union_of_cards_max_quantity_wins(self):
        cards, commanders, uids, color, conflicts, _slots, _flags = deck_sync.merge_normalized_decks(
            {"archidekt": ARCHIDEKT_DECK, "moxfield": MOXFIELD_DECK}
        )
        by_name = {c["name"]: c["quantity"] for c in cards}
        self.assertEqual(by_name["Sol Ring"], 1)
        self.assertEqual(by_name["Arcane Signet"], 2)  # max(1, 2)
        self.assertIn("Cultivate", by_name)  # archidekt-only
        self.assertIn("Rhystic Study", by_name)  # moxfield-only
        self.assertEqual(len(cards), 4)

    def test_conflicts_flagged_for_mismatched_qty_and_single_source_cards(self):
        _, _, _, _, conflicts, _slots, _flags = deck_sync.merge_normalized_decks(
            {"archidekt": ARCHIDEKT_DECK, "moxfield": MOXFIELD_DECK}
        )
        names = {c.card_name for c in conflicts}
        self.assertIn("Arcane Signet", names)  # quantity mismatch
        self.assertIn("Cultivate", names)  # archidekt-only
        self.assertIn("Rhystic Study", names)  # moxfield-only
        self.assertNotIn("Sol Ring", names)  # agrees everywhere

    def test_commanders_unioned_without_duplicates(self):
        other = dict(MOXFIELD_DECK)
        other["commanders"] = ["Atraxa, Praetors' Voice"]
        cards, commanders, uids, color, conflicts, _slots, _flags = deck_sync.merge_normalized_decks(
            {"archidekt": ARCHIDEKT_DECK, "moxfield": other}
        )
        self.assertEqual(commanders, ["Atraxa, Praetors' Voice"])

    def test_commander_uids_stay_aligned_with_names(self):
        a = dict(ARCHIDEKT_DECK, commanders=["Doctor"], commander_uids=["a"])
        m = dict(MOXFIELD_DECK, commanders=["Doctor", "Rose"], commander_uids=["b", "r"])
        _, commanders, uids, _, _, _slots, _flags = deck_sync.merge_normalized_decks({"archidekt": a, "moxfield": m})
        self.assertEqual(commanders, ["Doctor", "Rose"])
        self.assertEqual(uids, ["a", "r"])  # one printing per name, first source wins
        # A commander with no pairable printing: no uids, views use name images.
        m2 = dict(MOXFIELD_DECK, commanders=["Doctor", "Rose"], commander_uids=["b"])
        _, _, uids, _, _, _slots, _flags = deck_sync.merge_normalized_decks({"archidekt": a, "moxfield": m2})
        self.assertEqual(uids, [])

    def test_single_source_has_no_conflicts(self):
        _, _, _, _, conflicts, _slots, _flags = deck_sync.merge_normalized_decks({"archidekt": ARCHIDEKT_DECK})
        self.assertEqual(conflicts, [])


class TestSyncDeck(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed_linked_deck(self.r)

    def test_dual_source_sync_merges_and_persists(self):
        with patch.dict(
            PROVIDERS,
            {
                "archidekt": FakeArchidektProvider(deck=ARCHIDEKT_DECK),
                "moxfield": FakeMoxfieldProvider(deck=MOXFIELD_DECK),
            },
        ):
            report = deck_sync.sync_deck(self.r, "archidekt:42")

        self.assertEqual(report.merged_card_count, 4)
        self.assertEqual({s.provider for s in report.sources}, {"archidekt", "moxfield"})
        self.assertTrue(all(s.status == "ok" for s in report.sources))
        self.assertEqual(report.warnings, [])

        deck = registry.find_deck(self.r, "archidekt:42")
        names = {c["name"] for c in deck["cards"]}
        self.assertEqual(names, {"Sol Ring", "Arcane Signet", "Cultivate", "Rhystic Study"})

    def test_one_source_failure_aborts_update_and_preview(self):
        for failed in ("archidekt", "moxfield"):
            for dry_run in (False, True):
                with self.subTest(failed=failed, dry_run=dry_run):
                    error = ProviderError(f"{failed} timed out")
                    providers = {
                        "archidekt": FakeArchidektProvider(deck=ARCHIDEKT_DECK),
                        "moxfield": FakeMoxfieldProvider(deck=MOXFIELD_DECK),
                    }
                    providers[failed]._error = error
                    before = deepcopy(self.r.store)
                    with patch.dict(PROVIDERS, providers):
                        with self.assertRaises(ProviderFailure) as ctx:
                            deck_sync.update_deck(self.r, "archidekt:42", dry_run=dry_run)
                    self.assertIn(failed, str(ctx.exception))
                    self.assertIn("timed out", str(ctx.exception))
                    self.assertIs(ctx.exception.__cause__, error)
                    self.assertEqual(self.r.store, before)

    def test_physical_deck_partial_failure_does_not_mutate_any_state(self):
        r = PhysicalFakeRedis()
        deck = _seed_linked_deck(r, status="physical")
        deck.update({
            "cards": [{"name": "Rhystic Study", "quantity": 1}],
            "commanders": ["Original Commander"],
            "commander_uids": ["original-uid"],
            "color": "U",
            "slots": {"maybeboard": [{"name": "Original Maybe", "quantity": 1}]},
            "parse_flags": ["original flag"],
            "parent_folder_id": 588380,
            "last_updated_at": "old",
            "sync_state": {"archidekt": {"last_updated_at": "old"}},
        })
        r.set("decks", json.dumps([deck]))
        r.set("deck_status:42", "physical")
        instance = instance_store.create_instance(
            r, card_name="Rhystic Study", ownership_status="in_deck",
            deck_id="archidekt:42", deck_name=deck["name"],
        )
        before_store, before_sets = deepcopy(r.store), deepcopy(r.sets)
        arch = dict(ARCHIDEKT_DECK, parent_folder_id=123, theorycrafted=True)
        with patch.dict(PROVIDERS, {
            "archidekt": FakeArchidektProvider(deck=arch),
            "moxfield": FakeMoxfieldProvider(error=ProviderError("rate limited")),
        }), patch.object(registry, "update_deck_cards", wraps=registry.update_deck_cards) as update, \
                patch.object(registry, "set_folder_data", wraps=registry.set_folder_data) as folder, \
                patch.object(deck_sync, "_stamp", wraps=deck_sync._stamp) as stamp:
            with self.assertRaises(ProviderFailure):
                deck_sync.sync_deck(r, "archidekt:42")
        update.assert_not_called()
        folder.assert_not_called()
        stamp.assert_not_called()
        self.assertEqual(r.store, before_store)
        self.assertEqual(r.sets, before_sets)
        self.assertNotIn("pending_removal", instance_store.get_instance(r, instance["id"]))

    def test_all_sources_failing_raises_and_leaves_deck_untouched(self):
        before = deepcopy(self.r.store)
        with patch.dict(
            PROVIDERS,
            {
                "archidekt": FakeArchidektProvider(error=ProviderError("archidekt down")),
                "moxfield": FakeMoxfieldProvider(error=ProviderError("moxfield down")),
            },
        ):
            with self.assertRaises(ProviderFailure) as ctx:
                deck_sync.sync_deck(self.r, "archidekt:42")

        self.assertIn("all linked sources failed", str(ctx.exception))
        self.assertIn("archidekt: archidekt down", str(ctx.exception))
        self.assertIn("moxfield: moxfield down", str(ctx.exception))
        self.assertEqual(self.r.store, before)

    def test_physical_deck_success_still_flags_removed_inventory(self):
        r = PhysicalFakeRedis()
        deck = _seed_linked_deck(r, status="physical")
        deck["cards"] = [{"name": "Local Only", "quantity": 1}]
        r.set("decks", json.dumps([deck]))
        instance = instance_store.create_instance(
            r, card_name="Local Only", ownership_status="in_deck",
            deck_id="archidekt:42", deck_name=deck["name"],
        )
        with patch.dict(PROVIDERS, {
            "archidekt": FakeArchidektProvider(deck=ARCHIDEKT_DECK),
            "moxfield": FakeMoxfieldProvider(deck=MOXFIELD_DECK),
        }):
            deck_sync.sync_deck(r, "archidekt:42")
        updated = instance_store.get_instance(r, instance["id"])
        self.assertEqual(updated["pending_removal"]["reason"], "resync_removed")
        self.assertEqual(updated["ownership_status"], "in_deck")

    def test_single_source_only_still_works_no_regression(self):
        self.r.store.clear()
        deck = {
            "id": 99,
            "name": "Mono Source Deck",
            "registry_id": "archidekt:99",
            "source": "archidekt",
            "source_id": "99",
            "status": "testing",
            "cards": [],
            "links": {"archidekt": {"identifier": "99", "deck_id": "99"}},
        }
        self.r.set("decks", json.dumps([deck]))

        with patch.dict(PROVIDERS, {"archidekt": FakeArchidektProvider(deck=ARCHIDEKT_DECK)}):
            report = deck_sync.sync_deck(self.r, "archidekt:99")

        self.assertEqual(len(report.sources), 1)
        self.assertEqual(report.conflicts, [])  # nothing to disagree with
        updated = registry.find_deck(self.r, "archidekt:99")
        self.assertEqual(len(updated["cards"]), 3)

    def test_no_syncable_links_raises(self):
        self.r.store.clear()
        deck = {
            "id": 7,
            "name": "Unlinked Deck",
            "registry_id": "archidekt:7",
            "source": "archidekt",
            "source_id": "7",
            "status": "testing",
            "cards": [],
        }
        self.r.set("decks", json.dumps([deck]))

        with self.assertRaises(DeckSyncError):
            deck_sync.sync_deck(self.r, "archidekt:7")

    def test_unknown_deck_raises(self):
        with self.assertRaises(DeckSyncError):
            deck_sync.sync_deck(self.r, "archidekt:does-not-exist")


if __name__ == "__main__":
    unittest.main()
