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
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deck_links
import deck_sync
import registry
from deck_sync import DeckSyncError
from providers import PROVIDERS, ProviderError


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
        cards, commanders, uids, color, conflicts = deck_sync.merge_normalized_decks(
            {"archidekt": ARCHIDEKT_DECK, "moxfield": MOXFIELD_DECK}
        )
        by_name = {c["name"]: c["quantity"] for c in cards}
        self.assertEqual(by_name["Sol Ring"], 1)
        self.assertEqual(by_name["Arcane Signet"], 2)  # max(1, 2)
        self.assertIn("Cultivate", by_name)  # archidekt-only
        self.assertIn("Rhystic Study", by_name)  # moxfield-only
        self.assertEqual(len(cards), 4)

    def test_conflicts_flagged_for_mismatched_qty_and_single_source_cards(self):
        _, _, _, _, conflicts = deck_sync.merge_normalized_decks(
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
        cards, commanders, uids, color, conflicts = deck_sync.merge_normalized_decks(
            {"archidekt": ARCHIDEKT_DECK, "moxfield": other}
        )
        self.assertEqual(commanders, ["Atraxa, Praetors' Voice"])

    def test_single_source_has_no_conflicts(self):
        _, _, _, _, conflicts = deck_sync.merge_normalized_decks({"archidekt": ARCHIDEKT_DECK})
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

    def test_one_source_unreachable_other_still_completes_with_warning(self):
        with patch.dict(
            PROVIDERS,
            {
                "archidekt": FakeArchidektProvider(deck=ARCHIDEKT_DECK),
                "moxfield": FakeMoxfieldProvider(error=ProviderError("moxfield timed out")),
            },
        ):
            report = deck_sync.sync_deck(self.r, "archidekt:42")

        statuses = {s.provider: s.status for s in report.sources}
        self.assertEqual(statuses["archidekt"], "ok")
        self.assertEqual(statuses["moxfield"], "error")
        self.assertTrue(any("moxfield" in w for w in report.warnings))

        deck = registry.find_deck(self.r, "archidekt:42")
        names = {c["name"] for c in deck["cards"]}
        self.assertEqual(names, {"Sol Ring", "Arcane Signet", "Cultivate"})

    def test_all_sources_failing_raises_and_leaves_deck_untouched(self):
        original_cards = json.loads(self.r.get("decks"))[0]["cards"]
        with patch.dict(
            PROVIDERS,
            {
                "archidekt": FakeArchidektProvider(error=ProviderError("archidekt down")),
                "moxfield": FakeMoxfieldProvider(error=ProviderError("moxfield down")),
            },
        ):
            with self.assertRaises(DeckSyncError):
                deck_sync.sync_deck(self.r, "archidekt:42")

        deck = registry.find_deck(self.r, "archidekt:42")
        self.assertEqual(deck["cards"], original_cards)

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
