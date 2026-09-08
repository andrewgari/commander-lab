"""Unit tests for provider identifier parsing (no network calls).

Run: python tests/test_providers.py
"""
import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
