"""
Unit tests for providers/raw_paste.py -- the raw decklist text parser.

Run: python3 -m unittest tests.test_raw_paste_parser
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.raw_paste import parse_decklist


class TestParseDecklist(unittest.TestCase):
    def test_qty_x_lowercase(self):
        self.assertEqual(
            parse_decklist("1x Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_qty_x_uppercase(self):
        self.assertEqual(
            parse_decklist("1X Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_qty_no_separator(self):
        self.assertEqual(
            parse_decklist("1 Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_qty_greater_than_one(self):
        self.assertEqual(
            parse_decklist("2 Forest"),
            [{"name": "Forest", "quantity": 2}],
        )

    def test_bare_name_no_leading_number(self):
        self.assertEqual(
            parse_decklist("Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_trailing_bracket_suffix_stripped(self):
        self.assertEqual(
            parse_decklist("1 Sol Ring [Ramp]"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_trailing_bracket_suffix_stripped_bare_name(self):
        self.assertEqual(
            parse_decklist("Sol Ring [Ramp]"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_blank_lines_skipped(self):
        text = "1 Sol Ring\n\n\n1 Mana Crypt"
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Mana Crypt", "quantity": 1},
            ],
        )

    def test_hash_comment_lines_skipped(self):
        text = "# Ramp\n1 Sol Ring\n# Draw\n1 Rhystic Study"
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Rhystic Study", "quantity": 1},
            ],
        )

    def test_double_slash_comment_lines_skipped(self):
        text = "// Commander\n1 Najeela\n// Sideboard\n1 Sol Ring"
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Najeela", "quantity": 1},
                {"name": "Sol Ring", "quantity": 1},
            ],
        )

    def test_mixed_case_x_separator(self):
        text = "1x Sol Ring\n1X Mana Crypt"
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Mana Crypt", "quantity": 1},
            ],
        )

    def test_multiple_spaces_between_qty_and_name(self):
        self.assertEqual(
            parse_decklist("1    Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_multiple_spaces_with_x_separator(self):
        self.assertEqual(
            parse_decklist("1x     Sol Ring"),
            [{"name": "Sol Ring", "quantity": 1}],
        )

    def test_windows_line_endings(self):
        text = "1 Sol Ring\r\n1 Mana Crypt\r\n"
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Mana Crypt", "quantity": 1},
            ],
        )

    def test_empty_string_input(self):
        self.assertEqual(parse_decklist(""), [])

    def test_none_like_whitespace_only_input(self):
        self.assertEqual(parse_decklist("   \n\n   "), [])

    def test_full_decklist_mixed_formats(self):
        text = (
            "// Commander\n"
            "1 Najeela, the Blade-Blossom\n"
            "\n"
            "# Ramp\n"
            "1x Sol Ring\n"
            "1X Mana Crypt [Ramp]\n"
            "\n"
            "Rhystic Study\n"
            "2   Forest\n"
        )
        self.assertEqual(
            parse_decklist(text),
            [
                {"name": "Najeela, the Blade-Blossom", "quantity": 1},
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Mana Crypt", "quantity": 1},
                {"name": "Rhystic Study", "quantity": 1},
                {"name": "Forest", "quantity": 2},
            ],
        )


if __name__ == "__main__":
    unittest.main()
