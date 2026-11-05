"""Tests for src.server.domain.decks.decklist_export — the pure decklist
text-export seam. Syntax expectations come from docs/EXPORT_FORMAT_SYNTAX.md.
"""
import unittest

from src.server.domain.decks.decklist_export import (
    ExportFormatError,
    export_decklist_text,
)


def sample_deck():
    return {
        "name": "Atraxa Superfriends",
        "status": "physical",  # export must never branch on deck type
        "cards": [
            {"name": "Atraxa, Praetors' Voice", "quantity": 1,
             "categories": ["Commander"], "set": "2X2", "collector_number": "190"},
            {"name": "Sol Ring", "quantity": 1, "set": "CMM", "collector_number": "400"},
            {"name": "Arcane Signet", "quantity": 1},  # no printing data
            {"name": "Rest in Peace", "quantity": 1, "categories": ["Sideboard"],
             "set": "RVR", "collector_number": "24"},
            {"name": "Ponder", "quantity": 2, "categories": ["Maybeboard"]},
        ],
    }


class TestPlain(unittest.TestCase):
    def test_basic_commander_first_boards_excluded(self):
        out = export_decklist_text(sample_deck(), "plain")
        self.assertEqual(out.splitlines(), [
            "1 Atraxa, Praetors' Voice",
            "1 Sol Ring",
            "1 Arcane Signet",
        ])

    def test_printing_upper_set_and_cn(self):
        out = export_decklist_text(sample_deck(), "plain", include_printing=True)
        lines = out.splitlines()
        self.assertEqual(lines[0], "1 Atraxa, Praetors' Voice (2X2) 190")
        self.assertEqual(lines[1], "1 Sol Ring (CMM) 400")
        # No printing data -> name-only fallback, no trailing junk
        self.assertEqual(lines[2], "1 Arcane Signet")

    def test_boards_included_with_headers(self):
        out = export_decklist_text(sample_deck(), "plain",
                                   include_sideboard=True, include_maybeboard=True)
        self.assertEqual(out.splitlines(), [
            "1 Atraxa, Praetors' Voice",
            "1 Sol Ring",
            "1 Arcane Signet",
            "",
            "Sideboard",
            "1 Rest in Peace",
            "",
            "Maybeboard",
            "2 Ponder",
        ])

    def test_sideboard_only(self):
        out = export_decklist_text(sample_deck(), "plain", include_sideboard=True)
        self.assertIn("Sideboard", out)
        self.assertNotIn("Ponder", out)


class TestMoxfield(unittest.TestCase):
    def test_commander_suffix(self):
        out = export_decklist_text(sample_deck(), "moxfield")
        self.assertEqual(out.splitlines()[0], "1 Atraxa, Praetors' Voice *CMDR*")

    def test_printing_then_cmdr_suffix(self):
        out = export_decklist_text(sample_deck(), "moxfield", include_printing=True)
        lines = out.splitlines()
        self.assertEqual(lines[0], "1 Atraxa, Praetors' Voice (2X2) 190 *CMDR*")
        self.assertEqual(lines[1], "1 Sol Ring (CMM) 400")

    def test_sideboard_prefix(self):
        out = export_decklist_text(sample_deck(), "moxfield",
                                   include_sideboard=True, include_maybeboard=True,
                                   include_printing=True)
        lines = out.splitlines()
        self.assertIn("SB: 1 Rest in Peace (RVR) 24", lines)
        self.assertIn("MB: 2 Ponder", lines)


class TestArchidekt(unittest.TestCase):
    def test_x_qty_and_commander_tag(self):
        out = export_decklist_text(sample_deck(), "archidekt")
        lines = out.splitlines()
        self.assertEqual(lines[0], "1x Atraxa, Praetors' Voice [Commander{top}]")
        self.assertEqual(lines[1], "1x Sol Ring")

    def test_lowercase_set(self):
        out = export_decklist_text(sample_deck(), "archidekt", include_printing=True)
        lines = out.splitlines()
        self.assertEqual(lines[0], "1x Atraxa, Praetors' Voice (2x2) 190 [Commander{top}]")
        self.assertEqual(lines[1], "1x Sol Ring (cmm) 400")

    def test_board_tags(self):
        out = export_decklist_text(sample_deck(), "archidekt",
                                   include_sideboard=True, include_maybeboard=True,
                                   include_printing=True)
        lines = out.splitlines()
        self.assertIn("1x Rest in Peace (rvr) 24 [Sideboard{noDeck}{noPrice}]", lines)
        self.assertIn("2x Ponder [Maybeboard{noDeck}{noPrice}]", lines)


class TestForge(unittest.TestCase):
    def test_sections(self):
        out = export_decklist_text(sample_deck(), "forge")
        self.assertEqual(out.splitlines(), [
            "[metadata]",
            "Name=Atraxa Superfriends",
            "[commander]",
            "1 Atraxa, Praetors' Voice",
            "[main]",
            "1 Sol Ring",
            "1 Arcane Signet",
        ])

    def test_printing_pipe_set_no_cn(self):
        out = export_decklist_text(sample_deck(), "forge", include_printing=True)
        self.assertIn("1 Atraxa, Praetors' Voice|2X2", out)
        self.assertIn("1 Sol Ring|CMM", out)
        # No printing data -> no trailing pipe
        self.assertIn("1 Arcane Signet\n", out + "\n")
        self.assertNotIn("Arcane Signet|", out)

    def test_boards_fold_into_sideboard_section(self):
        out = export_decklist_text(sample_deck(), "forge",
                                   include_sideboard=True, include_maybeboard=True)
        self.assertEqual(out.splitlines()[-3:], ["[sideboard]", "1 Rest in Peace", "2 Ponder"])

    def test_no_sideboard_section_when_excluded_or_empty(self):
        out = export_decklist_text(sample_deck(), "forge", include_sideboard=True)
        self.assertIn("[sideboard]", out)
        out = export_decklist_text(sample_deck(), "forge")
        self.assertNotIn("[sideboard]", out)


class TestTcgplayer(unittest.TestCase):
    def test_flat_bracket_printing(self):
        out = export_decklist_text(sample_deck(), "tcgplayer", include_printing=True)
        lines = out.splitlines()
        self.assertEqual(lines[0], "1 Atraxa, Praetors' Voice [2X2] 190")
        self.assertEqual(lines[1], "1 Sol Ring [CMM] 400")
        self.assertEqual(lines[2], "1 Arcane Signet")

    def test_boards_appended_flat(self):
        out = export_decklist_text(sample_deck(), "tcgplayer",
                                   include_sideboard=True, include_maybeboard=True)
        self.assertEqual(out.splitlines()[-2:], ["1 Rest in Peace", "2 Ponder"])


class TestRobustness(unittest.TestCase):
    def test_empty_deck(self):
        for fmt in ("plain", "archidekt", "moxfield", "tcgplayer"):
            self.assertEqual(export_decklist_text({"cards": []}, fmt), "")
            self.assertEqual(export_decklist_text({}, fmt), "")
        forge = export_decklist_text({}, "forge")
        self.assertEqual(forge.splitlines(), ["[metadata]", "Name=Deck", "[commander]", "[main]"])

    def test_none_deck(self):
        self.assertEqual(export_decklist_text(None, "plain"), "")

    def test_commander_only_deck(self):
        deck = {"cards": [{"name": "Kenrith, the Returned King", "board": "commander"}]}
        self.assertEqual(export_decklist_text(deck, "plain"), "1 Kenrith, the Returned King")

    def test_commander_always_included_even_with_toggles_off(self):
        out = export_decklist_text(sample_deck(), "moxfield")
        self.assertTrue(out.splitlines()[0].startswith("1 Atraxa"))

    def test_board_key_and_categories_both_work(self):
        deck = {"cards": [
            {"name": "Cmdr", "board": "commander"},
            {"name": "SB Card", "board": "sideboard"},
            {"name": "MB Card", "categories": ["Maybeboard"]},
        ]}
        out = export_decklist_text(deck, "plain", include_sideboard=True, include_maybeboard=True)
        self.assertEqual(out.splitlines(), ["1 Cmdr", "", "Sideboard", "1 SB Card", "", "Maybeboard", "1 MB Card"])

    def test_set_without_collector_number(self):
        deck = {"cards": [{"name": "Sol Ring", "set": "CMM"}]}
        self.assertEqual(export_decklist_text(deck, "plain", include_printing=True), "1 Sol Ring (CMM)")
        self.assertEqual(export_decklist_text(deck, "tcgplayer", include_printing=True), "1 Sol Ring [CMM]")

    def test_bad_quantity_falls_back_not_throws(self):
        deck = {"cards": [{"name": "Sol Ring", "quantity": "lots"}, {"name": "", "quantity": 1}, {"name": "Zero", "quantity": 0}]}
        self.assertEqual(export_decklist_text(deck, "plain"), "1 Sol Ring")

    def test_unknown_format_raises(self):
        with self.assertRaises(ExportFormatError):
            export_decklist_text(sample_deck(), "star-city")

    def test_format_case_insensitive(self):
        self.assertEqual(export_decklist_text(sample_deck(), "PLAIN"),
                         export_decklist_text(sample_deck(), "plain"))

    def test_no_branching_on_deck_type(self):
        deck = sample_deck()
        physical = export_decklist_text(dict(deck, status="physical"), "plain", include_printing=True)
        digital = export_decklist_text(dict(deck, status="digital"), "plain", include_printing=True)
        self.assertEqual(physical, digital)


if __name__ == "__main__":
    unittest.main()
