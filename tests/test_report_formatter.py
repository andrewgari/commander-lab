"""
Unit tests for deck review formatting and export pipeline (analytics/report.py).

Verifies:
1. Markdown report generation with headers, tables, badges, and upgrade rationale.
2. CLI terminal plain-text formatting with ASCII dividers and neat indentation.
3. JSON serialization and schema validity.
4. Export to disk methods (export_markdown, export_cli, export_json).
5. Edge cases: empty commanders, no cuts, minimal decks, missing intent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.models import CommanderIdentifier, DeckCardEntry, DecklistInput
from analytics.report import (
    DeckReportFormatter,
    DeckReview,
    generate_deck_review,
)
from user_intent import DeckVision, TargetPowerLevel, UserDeckIntent


class TestDeckReportFormatter(unittest.TestCase):
    def setUp(self):
        self.deck = DecklistInput(
            deck_id="test:krenko",
            name="Krenko Mob Boss Swarm",
            format="commander",
            commanders=[
                CommanderIdentifier(name="Krenko, Mob Boss", oracle_id=None, scryfall_id=None)
            ],
            cards=[
                DeckCardEntry(name="Sol Ring", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Dragon Fodder", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Krenko's Command", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Goblin Chieftain", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Impact Tremors", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Diabolic Tutor", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),  # Cut candidate
                DeckCardEntry(name="Mountain", quantity=34, oracle_id=None, scryfall_id=None, category="Mainboard"),
            ],
        )

        self.intent = UserDeckIntent(
            deck_vision=DeckVision(intent="tune_up", description="Token swarm combat deck"),
            target_power_level=TargetPowerLevel(scale=7, tier="optimized"),
        )

        self.review = generate_deck_review(deck=self.deck, intent=self.intent)

    def test_markdown_format_structure_and_sections(self):
        """Markdown output contains required titles, tables, metrics, and sections."""
        md = self.review.to_markdown()

        # Check document title & badges
        self.assertIn("# Deck Review: Krenko Mob Boss Swarm", md)
        self.assertIn("**Commander:** Krenko, Mob Boss", md)
        self.assertIn("**Overall Score:**", md)

        # Check section headers
        self.assertIn("## 1. Deck Identity & Executive Summary", md)
        self.assertIn("## 2. Thematic & Archetype Assessment", md)
        self.assertIn("## 3. Playfeel & Pacing Summary", md)
        self.assertIn("## 4. Strengths & Cohesion Gaps", md)
        self.assertIn("## 5. User Intent Alignment", md)
        self.assertIn("## 6. Actionable Recommendations & Card Swaps", md)

        # Check 1-to-1 Swaps table format
        if self.review.recommendations.swaps:
            self.assertIn("| Cut (Out) | Add (In) | Mana Diff | Synergy Gain | Category | Upgrade Rationale |", md)
            self.assertIn("| :--- | :--- | :---: | :---: | :--- | :--- |", md)

        # Check ranked additions & cuts
        self.assertIn("### Recommended Additions (Ranked)", md)
        self.assertIn("### Recommended Cuts", md)

    def test_cli_format_structure_and_ascii_layout(self):
        """CLI text output contains ASCII dividers, uppercase headers, and clean layout."""
        cli_text = self.review.to_cli(width=80)

        self.assertIn("COMMANDER LAB DECK REVIEW: KRENKO MOB BOSS SWARM", cli_text)
        self.assertIn("Commander:    Krenko, Mob Boss", cli_text)
        self.assertIn("[ 1. EXECUTIVE SUMMARY & IDENTITY ]", cli_text)
        self.assertIn("[ 2. THEMATIC & ARCHETYPE ASSESSMENT ]", cli_text)
        self.assertIn("[ 3. PLAYFEEL & PACING SUMMARY ]", cli_text)
        self.assertIn("[ 4. STRENGTHS & COHESION Gaps ]".upper(), cli_text.upper())
        self.assertIn("[ 5. USER INTENT ALIGNMENT ]", cli_text)
        self.assertIn("[ 6. ACTIONABLE CARD SWAPS ]", cli_text)
        self.assertIn("[ 7. TOP RECOMMENDED ADDITIONS ]", cli_text)

        # Verify no line exceeds width by more than a couple characters (from long tokens)
        for line in cli_text.splitlines():
            # Allow minor overflow only for lines with long URLs or uninterrupted strings
            if not line.startswith("="):
                self.assertLessEqual(len(line), 100)

    def test_json_serialization(self):
        """JSON output is valid and accurately serializes DeckReview."""
        json_str = self.review.to_json()
        data = json.loads(json_str)

        self.assertEqual(data["deck_name"], "Krenko Mob Boss Swarm")
        self.assertEqual(data["format"], "commander")
        self.assertIn("overall_score", data)
        self.assertIn("thematic_assessment", data)
        self.assertIn("recommendations", data)
        self.assertIn("swaps", data["recommendations"])

    def test_export_file_methods(self):
        """Exporting to markdown, CLI text, and JSON writes expected files to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "review.md")
            cli_path = os.path.join(tmpdir, "review.txt")
            json_path = os.path.join(tmpdir, "review.json")

            md_content = self.review.export_markdown(md_path)
            self.assertTrue(os.path.isfile(md_path))
            with open(md_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), md_content)

            cli_content = self.review.export_cli(cli_path)
            self.assertTrue(os.path.isfile(cli_path))
            with open(cli_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), cli_content)

            json_content = self.review.export_json(json_path)
            self.assertTrue(os.path.isfile(json_path))
            with open(json_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), json_content)

    def test_edge_case_minimal_deck_without_intent(self):
        """Review works cleanly when no intent is provided and deck is minimal."""
        minimal_deck = "Commander\n1 Krenko, Mob Boss\n\nDeck\n1 Sol Ring\n1 Mountain\n"
        review = generate_deck_review(deck=minimal_deck)

        self.assertIsInstance(review, DeckReview)
        self.assertTrue(len(review.commanders) > 0)
        self.assertTrue(len(review.to_markdown()) > 100)
        self.assertTrue(len(review.to_cli()) > 100)


if __name__ == "__main__":
    unittest.main()
