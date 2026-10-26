"""Tests for the named deck slot model (deck_slots) and its wiring through
provider normalization, the registry (persistence + migration), the intake
pipeline, and dual-source sync merging.

Run: python -m unittest tests.test_deck_slots
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.deck_slots as deck_slots
from src.server.domain.decks.deck_slots import DeckSlot


class TestDeckSlotModel(unittest.TestCase):
    def test_slot_constants(self):
        self.assertEqual(
            set(DeckSlot.VALID),
            {
                "commander", "mainboard", "maybeboard", "considering",
                "planar_deck", "scheme_deck", "attraction_deck",
            },
        )

    def test_normalize_slot_aliases(self):
        self.assertEqual(deck_slots.normalize_slot("Planar Deck"), "planar_deck")
        self.assertEqual(deck_slots.normalize_slot("Planechase"), "planar_deck")
        self.assertEqual(deck_slots.normalize_slot("Schemes"), "scheme_deck")
        self.assertEqual(deck_slots.normalize_slot("Archenemy"), "scheme_deck")
        self.assertEqual(deck_slots.normalize_slot("Attractions"), "attraction_deck")
        self.assertEqual(deck_slots.normalize_slot("Considering"), "considering")
        self.assertIsNone(deck_slots.normalize_slot("Ramp"))

    def test_sideboard_normalizes_to_maybeboard(self):
        for spelling in ("Sideboard", "sideboard", "side", "SB"):
            self.assertEqual(deck_slots.normalize_slot(spelling), "maybeboard")
            self.assertTrue(deck_slots.is_sideboard_spelling(spelling))
        self.assertFalse(deck_slots.is_sideboard_spelling("Maybeboard"))

    def test_slot_for_types(self):
        self.assertEqual(deck_slots.slot_for_types(["Plane"]), "planar_deck")
        self.assertEqual(deck_slots.slot_for_types(["Phenomenon"]), "planar_deck")
        self.assertEqual(deck_slots.slot_for_types(["Scheme"]), "scheme_deck")
        self.assertEqual(deck_slots.slot_for_types(["Artifact", "Attraction"]), "attraction_deck")
        self.assertIsNone(deck_slots.slot_for_types(["Creature"]))

    def test_classify_card_slot_precedence(self):
        # Structural categories beat type data
        slot, remapped = deck_slots.classify_card_slot(["Sideboard"], ["Plane"])
        self.assertEqual((slot, remapped), ("maybeboard", True))
        slot, remapped = deck_slots.classify_card_slot(["Maybeboard"], ["Creature"])
        self.assertEqual((slot, remapped), ("maybeboard", False))
        slot, _ = deck_slots.classify_card_slot(["Considering"], ["Creature"])
        self.assertEqual(slot, "considering")
        # Type class beats variant category labels
        slot, _ = deck_slots.classify_card_slot(["Planar Deck"], ["Scheme"])
        self.assertEqual(slot, "scheme_deck")
        slot, _ = deck_slots.classify_card_slot(["Planar Deck"], ["Creature"])
        self.assertEqual(slot, "planar_deck")
        slot, remapped = deck_slots.classify_card_slot(["Ramp"], ["Creature"])
        self.assertEqual((slot, remapped), ("mainboard", False))

    def test_migrate_serialized_deck_folds_sideboard_into_maybeboard(self):
        legacy = {
            "name": "Old Deck",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
            "sideboard": [{"name": "Flusterstorm", "quantity": 1}],
            "slots": {"maybeboard": [{"name": "Force of Will", "quantity": 1}]},
        }
        out = deck_slots.migrate_serialized_deck(legacy)
        self.assertNotIn("sideboard", out)
        names = {c["name"] for c in out["slots"]["maybeboard"]}
        self.assertEqual(names, {"Force of Will", "Flusterstorm"})
        # All canonical slots present; no data loss
        self.assertEqual(set(out["slots"]), set(DeckSlot.VALID))
        # Idempotent
        again = deck_slots.migrate_serialized_deck(out)
        self.assertEqual(len(again["slots"]["maybeboard"]), 2)


class TestProviderSlotNormalization(unittest.TestCase):
    def _archidekt_payload(self, cards):
        return {"name": "Test", "cards": cards, "parentFolderId": None}

    def _archidekt_card(self, name, categories, types, qty=1):
        return {
            "categories": categories,
            "quantity": qty,
            "card": {"oracleCard": {"name": name, "types": types, "colorIdentity": []}},
        }

    @patch("src.server.infra.providers.archidekt.requests.get")
    def test_archidekt_planar_and_sideboard_slots(self, mock_get):
        from src.server.infra.providers import archidekt

        payload = self._archidekt_payload([
            self._archidekt_card("Missy", ["Commander"], ["Creature"]),
            self._archidekt_card("Sol Ring", [], ["Artifact"]),
            self._archidekt_card("The Abyssal Plane", [], ["Plane"]),
            self._archidekt_card("Stairs to Infinity", ["Planar Deck"], ["Plane"]),
            self._archidekt_card("Flusterstorm", ["Sideboard"], ["Instant"]),
            self._archidekt_card("Force of Will", ["Maybeboard"], ["Instant"]),
            self._archidekt_card("Rhystic Study", ["Considering"], ["Enchantment"]),
        ])
        mock_get.return_value = MagicMock(status_code=200, json=lambda: payload,
                                          raise_for_status=lambda: None)

        deck = archidekt.fetch_deck("1234")
        main_names = {c["name"] for c in deck["cards"]}
        self.assertEqual(main_names, {"Missy", "Sol Ring"})
        planar = {c["name"] for c in deck["slots"]["planar_deck"]}
        self.assertEqual(planar, {"The Abyssal Plane", "Stairs to Infinity"})
        maybe = deck["slots"]["maybeboard"]
        self.assertEqual({c["name"] for c in maybe}, {"Flusterstorm", "Force of Will"})
        remapped = [c for c in maybe if c.get("remapped_from") == "sideboard"]
        self.assertEqual([c["name"] for c in remapped], ["Flusterstorm"])
        self.assertEqual([c["name"] for c in deck["slots"]["considering"]], ["Rhystic Study"])
        self.assertIn(deck_slots.SIDEBOARD_MAPPED_FLAG, deck["parse_flags"])

    @patch("src.server.infra.providers.archidekt.requests.get")
    def test_archidekt_attraction_deck(self, mock_get):
        from src.server.infra.providers import archidekt

        payload = self._archidekt_payload([
            self._archidekt_card("The Most Dangerous Gamer", ["Commander"], ["Creature"]),
            self._archidekt_card("Balloon Stand", [], ["Artifact", "Attraction"], qty=2),
        ])
        mock_get.return_value = MagicMock(status_code=200, json=lambda: payload,
                                          raise_for_status=lambda: None)
        deck = archidekt.fetch_deck("5678")
        self.assertEqual([c["name"] for c in deck["slots"]["attraction_deck"]], ["Balloon Stand"])
        self.assertEqual(deck["slots"]["attraction_deck"][0]["quantity"], 2)
        self.assertNotIn("Balloon Stand", {c["name"] for c in deck["cards"]})

    @patch("src.server.infra.providers.moxfield.requests.get")
    def test_moxfield_boards_to_slots(self, mock_get):
        from src.server.infra.providers import moxfield

        payload = {
            "publicId": "abc",
            "name": "Mox Deck",
            "colorIdentity": ["U"],
            "boards": {
                "commanders": {"cards": {"1": {"card": {"name": "Najeela", "scryfall_id": "u1"}, "quantity": 1}}},
                "mainboard": {"cards": {"2": {"card": {"name": "Sol Ring"}, "quantity": 1}}},
                "sideboard": {"cards": {"3": {"card": {"name": "Flusterstorm"}, "quantity": 1}}},
                "maybeboard": {"cards": {"4": {"card": {"name": "Force of Will"}, "quantity": 1}}},
                "attractions": {"cards": {"5": {"card": {"name": "Balloon Stand"}, "quantity": 1}}},
                "planes": {"cards": {"6": {"card": {"name": "The Abyssal Plane"}, "quantity": 1}}},
                "schemes": {"cards": {"7": {"card": {"name": "Plots That Span Centuries"}, "quantity": 1}}},
            },
        }
        mock_get.return_value = MagicMock(status_code=200, json=lambda: payload,
                                          raise_for_status=lambda: None)
        deck = moxfield.fetch_deck("abc")
        self.assertEqual({c["name"] for c in deck["cards"]}, {"Najeela", "Sol Ring"})
        self.assertEqual({c["name"] for c in deck["slots"]["maybeboard"]},
                         {"Flusterstorm", "Force of Will"})
        self.assertEqual([c["name"] for c in deck["slots"]["attraction_deck"]], ["Balloon Stand"])
        self.assertEqual([c["name"] for c in deck["slots"]["planar_deck"]], ["The Abyssal Plane"])
        self.assertEqual([c["name"] for c in deck["slots"]["scheme_deck"]],
                         ["Plots That Span Centuries"])
        self.assertIn(deck_slots.SIDEBOARD_MAPPED_FLAG, deck["parse_flags"])


class _FakeRedis(dict):
    def get(self, key):
        return super().get(key)

    def set(self, key, value):
        super().__setitem__(key, value)


class TestRegistrySlotPersistence(unittest.TestCase):
    def test_upsert_persists_slots(self):
        import json as _json
        import src.server.domain.decks.registry as registry

        r = _FakeRedis()
        normalized = {
            "source": "archidekt", "source_id": "99", "name": "Missy",
            "color": "UB", "commanders": ["Missy"], "commander_uids": [],
            "cards": [{"name": "Sol Ring", "quantity": 1}],
            "slots": deck_slots.normalize_slots({
                "planar_deck": [{"name": "The Abyssal Plane", "quantity": 1}],
            }),
            "parse_flags": [],
        }
        with patch("src.server.domain.decks.registry.apply_folder_rule", lambda *a, **k: None), \
             patch("src.server.domain.decks.registry.r_get", create=True):
            deck = registry.upsert_deck(r, normalized)
        self.assertEqual(
            deck["slots"]["planar_deck"], [{"name": "The Abyssal Plane", "quantity": 1}]
        )
        # Persisted JSON carries the slots
        stored = _json.loads(r.get(registry.DECKS_KEY))
        self.assertEqual(stored[0]["slots"]["planar_deck"][0]["name"], "The Abyssal Plane")

    def test_load_migrates_legacy_sideboard(self):
        import json as _json
        import src.server.domain.decks.registry as registry

        r = _FakeRedis()
        r.set(registry.DECKS_KEY, _json.dumps([{
            "id": 1, "name": "Legacy",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
            "sideboard": [{"name": "Flusterstorm", "quantity": 1}],
        }]))
        decks = registry._load_decks(r)
        deck = decks[0]
        self.assertNotIn("sideboard", deck)
        self.assertEqual(
            deck["slots"]["maybeboard"], [{"name": "Flusterstorm", "quantity": 1}]
        )
        self.assertEqual(set(deck["slots"]), set(DeckSlot.VALID))

    def test_update_deck_cards_writes_slots(self):
        import json as _json
        import src.server.domain.decks.registry as registry

        r = _FakeRedis()
        r.set(registry.DECKS_KEY, _json.dumps([{
            "id": 7, "source": "archidekt", "source_id": "7",
            "registry_id": "archidekt:7", "name": "Gamer",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }]))
        registry.update_deck_cards(
            r, "archidekt:7", cards=[{"name": "Sol Ring", "quantity": 1}],
            slots={"attraction_deck": [{"name": "Balloon Stand", "quantity": 1}]},
        )
        deck = registry.find_deck(r, "archidekt:7")
        self.assertEqual(
            deck["slots"]["attraction_deck"], [{"name": "Balloon Stand", "quantity": 1}]
        )


class TestSyncMergeSlots(unittest.TestCase):
    def test_merge_normalized_decks_merges_slots(self):
        from src.server.domain.decks.deck_sync import merge_normalized_decks

        fetched = {
            "archidekt": {
                "cards": [{"name": "Sol Ring", "quantity": 1}],
                "commanders": ["Missy"], "commander_uids": [], "color": "UB",
                "slots": deck_slots.normalize_slots({
                    "planar_deck": [{"name": "The Abyssal Plane", "quantity": 1}],
                }),
                "parse_flags": [],
            },
            "moxfield": {
                "cards": [{"name": "Sol Ring", "quantity": 1}],
                "commanders": ["Missy"], "commander_uids": [], "color": "UB",
                "slots": deck_slots.normalize_slots({
                    "planar_deck": [{"name": "Stairs to Infinity", "quantity": 1}],
                    "maybeboard": [{"name": "Flusterstorm", "quantity": 1,
                                    "remapped_from": "sideboard"}],
                }),
                "parse_flags": [deck_slots.SIDEBOARD_MAPPED_FLAG],
            },
        }
        cards, commanders, uids, color, conflicts, slots, flags = merge_normalized_decks(fetched)
        planar = {c["name"] for c in slots["planar_deck"]}
        self.assertEqual(planar, {"The Abyssal Plane", "Stairs to Infinity"})
        self.assertEqual(
            slots["maybeboard"],
            [{"name": "Flusterstorm", "quantity": 1, "remapped_from": "sideboard"}],
        )
        self.assertIn(deck_slots.SIDEBOARD_MAPPED_FLAG, flags)


class TestIntakeSlots(unittest.TestCase):
    def test_intake_parse_sideboard_flag_and_slots(self):
        from src.server.domain.intake.deck_intake import process_deck_intake

        decklist = (
            "Commander\n1 Missy\n\nDeck\n1 Sol Ring\n\n"
            "Sideboard\n1 Flusterstorm\n\n"
            "Planar Deck\n1 The Abyssal Plane\n"
        )
        result = process_deck_intake(decklist=decklist)
        self.assertIn(deck_slots.SIDEBOARD_MAPPED_FLAG, result.parse_flags)
        self.assertTrue(any("Sideboard" in w for w in result.warnings))
        self.assertEqual([c.name for c in result.maybeboard], ["Flusterstorm"])
        self.assertEqual([c.name for c in result.planar_deck], ["The Abyssal Plane"])
        d = result.to_dict()
        self.assertEqual(d["slots"]["planar_deck"][0]["name"], "The Abyssal Plane")
        self.assertNotIn("sideboard", d)
        # Serialized payload round-trips through the migration helper
        migrated = deck_slots.migrate_serialized_deck(d)
        self.assertEqual(migrated["slots"]["planar_deck"][0]["name"], "The Abyssal Plane")

    def test_intake_structured_sideboard_section_remapped(self):
        from src.server.domain.intake.deck_intake import process_deck_intake

        cards = [
            {"name": "Missy", "section": "commander"},
            {"name": "Sol Ring", "section": "mainboard"},
            {"name": "Flusterstorm", "section": "sideboard"},
            {"name": "Balloon Stand", "section": "attraction_deck"},
        ]
        result = process_deck_intake(decklist=cards)
        self.assertIn(deck_slots.SIDEBOARD_MAPPED_FLAG, result.parse_flags)
        self.assertEqual([c.name for c in result.maybeboard], ["Flusterstorm"])
        self.assertEqual([c.name for c in result.attraction_deck], ["Balloon Stand"])
        self.assertEqual(result.summary.attraction_deck_count, 1)
        self.assertEqual(result.summary.maybeboard_count, 1)


if __name__ == "__main__":
    unittest.main()
