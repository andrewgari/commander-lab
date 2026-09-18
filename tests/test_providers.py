"""Unit tests for provider identifier parsing (no network calls).

Run: python tests/test_providers.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import moxfield, archidekt


class TestMoxfieldParseIdentifier(unittest.TestCase):
    def test_bare_public_id(self):
        self.assertEqual(moxfield.parse_identifier("FQ2llEVF2k6oMUoXVA16XA"), "FQ2llEVF2k6oMUoXVA16XA")

    def test_full_url(self):
        self.assertEqual(
            moxfield.parse_identifier("https://moxfield.com/decks/FQ2llEVF2k6oMUoXVA16XA"),
            "FQ2llEVF2k6oMUoXVA16XA",
        )

    def test_url_with_trailing_path(self):
        self.assertEqual(
            moxfield.parse_identifier("https://www.moxfield.com/decks/FQ2llEVF2k6oMUoXVA16XA/primer"),
            "FQ2llEVF2k6oMUoXVA16XA",
        )

    def test_rejects_slashes(self):
        with self.assertRaises(ValueError):
            moxfield.parse_identifier("not/a/valid/id")

    def test_rejects_query_string_injection(self):
        with self.assertRaises(ValueError):
            moxfield.parse_identifier("abc?evil=1&x=2")

    def test_rejects_whitespace(self):
        with self.assertRaises(ValueError):
            moxfield.parse_identifier("abc def")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            moxfield.parse_identifier("   ")


class TestArchidektParseIdentifier(unittest.TestCase):
    def test_bare_id(self):
        self.assertEqual(archidekt.parse_identifier("6862011"), "6862011")

    def test_full_url(self):
        self.assertEqual(archidekt.parse_identifier("https://archidekt.com/decks/6862011/kaalia"), "6862011")

    def test_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            archidekt.parse_identifier("not-a-deck")


class TestArchidektColorIdentity(unittest.TestCase):
    """Regression test: deck-level `colors` field is null on real Archidekt
    decks, so color must be computed as the union of each card's
    oracleCard.colorIdentity, not read from the broken deck-level field."""

    def _mock_response(self, payload):
        mock_res = MagicMock()
        mock_res.json.return_value = payload
        mock_res.raise_for_status.return_value = None
        return mock_res

    def test_color_computed_from_card_colorIdentity_not_deck_colors(self):
        payload = {
            "name": "Test Deck",
            "colors": None,  # broken/null on real decks -- must be ignored
            "description": "",
            "cards": [
                {
                    "categories": ["Commander"],
                    "quantity": 1,
                    "card": {
                        "uid": "cmd-uid",
                        "oracleCard": {
                            "name": "Test Commander",
                            "colorIdentity": ["Black", "Red"],
                        },
                    },
                },
                {
                    "categories": [],
                    "quantity": 1,
                    "card": {
                        "uid": "card-uid",
                        "oracleCard": {
                            "name": "Test Green Card",
                            "colorIdentity": ["Green"],
                        },
                    },
                },
            ],
        }
        with patch("providers.archidekt.requests.get", return_value=self._mock_response(payload)):
            deck = archidekt.fetch_deck("6862011")
        self.assertEqual(deck["color"], "BRG")  # WUBRG order, union of B/R/G

    def test_colorless_deck_falls_back_to_C(self):
        payload = {
            "name": "Colorless Deck",
            "colors": None,
            "description": "",
            "cards": [
                {
                    "categories": ["Commander"],
                    "quantity": 1,
                    "card": {
                        "uid": "cmd-uid",
                        "oracleCard": {"name": "Karn", "colorIdentity": []},
                    },
                },
            ],
        }
        with patch("providers.archidekt.requests.get", return_value=self._mock_response(payload)):
            deck = archidekt.fetch_deck("6862011")
        self.assertEqual(deck["color"], "C")


if __name__ == "__main__":
    unittest.main()
