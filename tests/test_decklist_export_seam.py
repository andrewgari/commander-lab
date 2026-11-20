"""Seam-level edge cases for decklist_export.export_decklist_text.

Complements tests/test_decklist_export.py (per-format syntax) by pinning the
cross-format rules of the seam: commander first, board toggles, tolerant
input, printing fallback, no branching on deck type, unknown-format error.
"""
import unittest

from src.server.domain.decks.decklist_export import (
    FORMATS,
    ExportFormatError,
    export_decklist_text,
)


def deck(**extra):
    d = {
        "name": "Seam Deck",
        "cards": [
            {"name": "Sol Ring", "quantity": 1, "set": "cmm", "collector_number": "400"},
            {"name": "Forest", "quantity": 3},
            {"name": "Kaalia of the Vast", "quantity": 1, "categories": ["Commander"]},
            {"name": "Rest in Peace", "quantity": 1, "board": "sideboard"},
            {"name": "Ponder", "quantity": 2, "board": "maybeboard"},
        ],
    }
    d.update(extra)
    return d


class TestSeam(unittest.TestCase):
    def test_commander_always_first_in_every_format(self):
        for fmt in FORMATS:
            body = export_decklist_text(deck(), fmt)
            lines = [l for l in body.splitlines() if l and not l.startswith("[") and "=" not in l]
            self.assertIn("Kaalia of the Vast", lines[0], fmt)

    def test_boards_excluded_by_default_included_on_toggle(self):
        for fmt in FORMATS:
            off = export_decklist_text(deck(), fmt)
            self.assertNotIn("Rest in Peace", off, fmt)
            self.assertNotIn("Ponder", off, fmt)
            on = export_decklist_text(deck(), fmt, include_sideboard=True, include_maybeboard=True)
            self.assertIn("Rest in Peace", on, fmt)
            self.assertIn("Ponder", on, fmt)

    def test_missing_printing_falls_back_to_name_only(self):
        out = export_decklist_text(deck(), "plain", include_printing=True)
        self.assertIn("3 Forest", out.splitlines())
        self.assertIn("1 Sol Ring (CMM) 400", out.splitlines())

    def test_physical_and_digital_render_identically(self):
        a = export_decklist_text(deck(status="physical"), "moxfield")
        b = export_decklist_text(deck(status="virtual"), "moxfield")
        self.assertEqual(a, b)

    def test_tolerant_input_never_throws(self):
        junk = {"cards": [None, "x", {"name": ""}, {"name": "A", "quantity": "bad"},
                          {"name": "B", "quantity": 0}, {"name": "C", "quantity": -2}]}
        self.assertEqual(export_decklist_text(junk, "plain"), "1 A")
        self.assertEqual(export_decklist_text({}, "plain"), "")
        self.assertEqual(export_decklist_text(None, "tcgplayer"), "")

    def test_format_is_case_insensitive_and_unknown_raises(self):
        self.assertEqual(export_decklist_text(deck(), " PLAIN "), export_decklist_text(deck(), "plain"))
        for bad in ("csv", "", None):
            with self.assertRaises(ExportFormatError):
                export_decklist_text(deck(), bad)

    def test_forge_folds_maybeboard_into_sideboard(self):
        out = export_decklist_text(deck(), "forge", include_maybeboard=True)
        self.assertIn("[sideboard]\n2 Ponder", out)
        self.assertNotIn("[maybeboard]", out)


if __name__ == "__main__":
    unittest.main()
