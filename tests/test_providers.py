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


class TestArchidektListDecks(unittest.TestCase):
    """list_decks(username) mocked at the requests layer (no network)."""

    def test_paginates_and_returns_ids(self):
        from unittest.mock import patch, MagicMock

        user_resp = MagicMock()
        user_resp.json.return_value = {"results": [{"id": 35025, "username": "CovaDax"}]}
        page1 = MagicMock()
        page1.json.return_value = {
            "results": [{"id": 1}, {"id": 2}],
            "next": "https://archidekt.com/api/decks/v3/?ownerId=35025&pageSize=100&page=2",
        }
        page2 = MagicMock()
        page2.json.return_value = {"results": [{"id": 3}], "next": None}

        with patch("providers.archidekt.requests.get", side_effect=[user_resp, page1, page2]) as mock_get:
            ids = archidekt.list_decks("CovaDax")
        self.assertEqual(ids, ["1", "2", "3"])
        self.assertEqual(mock_get.call_count, 3)

    def test_unknown_username_raises(self):
        from unittest.mock import patch, MagicMock

        user_resp = MagicMock()
        user_resp.json.return_value = {"results": []}
        with patch("providers.archidekt.requests.get", return_value=user_resp):
            with self.assertRaises(archidekt.ArchidektUserNotFound):
                archidekt.list_decks("zzz_nonexistent_user_xyz_12345")

    def test_empty_username_raises(self):
        with self.assertRaises(archidekt.ArchidektUserNotFound):
            archidekt.list_decks("   ")


class TestMoxfieldListDecks(unittest.TestCase):
    """list_decks(username) mocked at the requests layer (no network)."""

    def test_paginates_and_returns_public_ids(self):
        from unittest.mock import patch, MagicMock

        def make_page(total_pages, decks):
            resp = MagicMock()
            resp.json.return_value = {
                "totalPages": total_pages,
                "data": [
                    {"publicId": pid, "authors": [{"userName": "CovaDax"}]}
                    for pid in decks
                ],
            }
            return resp

        page1 = make_page(2, ["abc", "def"])
        page2 = make_page(2, ["ghi"])

        with patch("providers.moxfield.requests.get", side_effect=[page1, page2]) as mock_get:
            ids = moxfield.list_decks("CovaDax")
        self.assertEqual(ids, ["abc", "def", "ghi"])
        self.assertEqual(mock_get.call_count, 2)

    def test_unrelated_feed_raises_not_silent_empty(self):
        """A bad username still 200s but returns the unfiltered global feed;
        list_decks must detect the mismatch and raise, not return []."""
        from unittest.mock import patch, MagicMock

        resp = MagicMock()
        resp.json.return_value = {
            "totalPages": 2000,
            "data": [{"publicId": "unrelated1", "authors": [{"userName": "someone_else"}]}],
        }
        with patch("providers.moxfield.requests.get", return_value=resp):
            with self.assertRaises(moxfield.MoxfieldUserNotFound):
                moxfield.list_decks("zzz_nonexistent_user_xyz_12345")

    def test_empty_username_raises(self):
        with self.assertRaises(moxfield.MoxfieldUserNotFound):
            moxfield.list_decks("   ")


class TestProvidersDispatcher(unittest.TestCase):
    def test_list_decks_dispatches(self):
        from unittest.mock import patch

        with patch("providers.archidekt.list_decks", return_value=["1", "2"]) as mocked:
            from providers import list_decks

            self.assertEqual(list_decks("archidekt", "CovaDax"), ["1", "2"])
            mocked.assert_called_once_with("CovaDax")

    def test_list_decks_unknown_provider_raises(self):
        from providers import list_decks, ProviderError

        with self.assertRaises(ProviderError):
            list_decks("nonexistent", "someone")


if __name__ == "__main__":
    unittest.main()
