"""
End-to-end integration tests for Commander Lab deck review pipeline:
1. Multi-archetype reviews (Atraxa counters, Wilhelt aristocrats, Stella spellslinger).
2. Facade parity (deck_review vs analytics.report vs analytics).
3. CLI executions (deck_review.py and cli.py review).
4. FastAPI REST API endpoints (/api/review, /api/decks/review, /api/decks/{deck_id}/review).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from analytics import generate_deck_review as analytics_review
import analytics
from app import app
import cli
import deck_review
from deck_review import DeckReview, review_deck
from user_intent import parse_user_intent

SAMPLE_ATRAXA_DECKLIST = """Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Arcane Signet
1 Hardened Scales
1 Evolution Sage
1 The Ozolith
1 Conclave Mentor
1 Diabolic Tutor
1 Cancel
1 Murder
1 Plains
1 Island
1 Swamp
1 Forest
"""

SAMPLE_WILHELT_DECKLIST = """Commander
1 Wilhelt, the Rotcleaver

Deck
1 Sol Ring
1 Arcane Signet
1 Blood Artist
1 Zulaport Cutthroat
1 Carrion Feeder
1 Viscera Seer
1 Diabolic Tutor
1 Cancel
1 Island
1 Swamp
"""

SAMPLE_STELLA_DECKLIST = """Commander
1 Stella Lee, Wild Card

Deck
1 Sol Ring
1 Arcane Signet
1 Guttersnipe
1 Archmage Emeritus
1 Third Path Iconoclast
1 Manalith
1 Island
1 Mountain
"""


class TestDeckReviewIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_atraxa_counters_review_end_to_end(self):
        """Atraxa counters deck review generates thematic swaps and aligned markdown report."""
        intent_payload = {
            "deck_vision": {"intent": "tune_up", "description": "Counters and proliferate tuning"},
            "target_power_level": {"scale": 8, "tier": "optimized"},
            "archetype_preferences": {"primary_archetype": "midrange", "custom_themes": ["counters", "proliferate"]},
            "preferred_win_conditions": {"primary": "combat"},
        }
        intent = parse_user_intent(intent_payload)

        review = review_deck(deck=SAMPLE_ATRAXA_DECKLIST, intent=intent)

        self.assertIsInstance(review, DeckReview)
        self.assertIn("Atraxa", review.deck_name)
        self.assertEqual(review.commanders, ["Atraxa, Praetors' Voice"])
        self.assertGreaterEqual(review.overall_score, 0.0)

        # Thematic assessment must reflect mechanics
        self.assertGreater(review.thematic_assessment.score, 0.0)

        # Recommendations must include swaps
        self.assertGreater(len(review.recommendations.swaps), 0)
        swap_categories = [s.category for s in review.recommendations.swaps]
        self.assertTrue(any("Synergy" in c or "Recent" in c or "Curve" in c for c in swap_categories))

        # Markdown and CLI formatting must be non-empty and well-formed
        md = review.to_markdown()
        self.assertIn(f"# Deck Review: {review.deck_name}", md)
        self.assertIn("## 6. Actionable Recommendations & Card Swaps", md)

        cli_text = review.to_cli()
        self.assertIn("COMMANDER LAB DECK REVIEW", cli_text)

    def test_wilhelt_aristocrats_review_end_to_end(self):
        """Wilhelt aristocrats deck review properly identifies aristocrats mechanics and cuts."""
        intent_payload = {
            "deck_vision": {"intent": "overhaul", "description": "Sacrifice drain loops"},
            "target_power_level": {"scale": 7, "tier": "optimized"},
            "archetype_preferences": {"primary_archetype": "aristocrats"},
            "preferred_win_conditions": {"primary": "attrition"},
        }
        intent = parse_user_intent(intent_payload)

        review = review_deck(deck=SAMPLE_WILHELT_DECKLIST, intent=intent)

        self.assertEqual(review.commanders, ["Wilhelt, the Rotcleaver"])
        self.assertIn("aristocrats", review.thematic_assessment.primary_archetype.lower())

        # Recommended additions should feature aristocrats / death trigger payoffs
        rec_names = [a.card_name for a in review.recommendations.additions]
        self.assertTrue(
            any(n in rec_names for n in ["Marionette Apprentice", "Rottenmouth Viper", "Vein Ripper", "Bloodletter of Aclazotz", "Drivnod, Carnage Dominus"]),
            f"Expected aristocrats recommendations, got: {rec_names}",
        )

    def test_stella_spellslinger_review_end_to_end(self):
        """Stella Lee spellslinger deck review suggests spell copying and noncreature payoffs."""
        intent_payload = {
            "deck_vision": {"intent": "tune_up"},
            "archetype_preferences": {"primary_archetype": "spellslinger"},
        }
        review = review_deck(deck=SAMPLE_STELLA_DECKLIST, intent=intent_payload)

        self.assertEqual(review.commanders, ["Stella Lee, Wild Card"])
        self.assertIn("spellslinger", review.thematic_assessment.primary_archetype.lower())

        # Manalith is an outclassed mana rock and should be a top cut candidate
        cut_names = [c.card_name for c in review.recommendations.cuts]
        self.assertIn("Manalith", cut_names)

    def test_facade_parity(self):
        """Verify deck_review.review_deck, analytics.report.generate_deck_review, and analytics.generate_deck_review are equivalent."""
        r1 = deck_review.review_deck(SAMPLE_ATRAXA_DECKLIST)
        r2 = analytics_review(SAMPLE_ATRAXA_DECKLIST)
        r3 = analytics.generate_deck_review(SAMPLE_ATRAXA_DECKLIST)

        self.assertEqual(r1.deck_name, r2.deck_name)
        self.assertEqual(r1.deck_name, r3.deck_name)
        self.assertEqual(r1.overall_score, r2.overall_score)
        self.assertEqual(r1.overall_score, r3.overall_score)
        self.assertEqual(len(r1.recommendations.swaps), len(r2.recommendations.swaps))

    def test_cli_execution_deck_review_main(self):
        """Test deck_review.py CLI entrypoint directly with temporary files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deck_file = os.path.join(tmpdir, "deck.txt")
            with open(deck_file, "w", encoding="utf-8") as f:
                f.write(SAMPLE_ATRAXA_DECKLIST)

            intent_file = os.path.join(tmpdir, "intent.json")
            with open(intent_file, "w", encoding="utf-8") as f:
                json.dump({"deck_vision": {"intent": "tune_up"}}, f)

            out_file = os.path.join(tmpdir, "review.md")

            argv = [
                "--deck", deck_file,
                "--intent", intent_file,
                "--format", "markdown",
                "--output", out_file,
            ]
            exit_code = deck_review.main(argv)
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.isfile(out_file))
            with open(out_file, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("# Deck Review:", content)

    def test_cli_execution_via_cli_py_review_subcommand(self):
        """Test invoking review subcommand via cli.py main."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deck_file = os.path.join(tmpdir, "deck.txt")
            with open(deck_file, "w", encoding="utf-8") as f:
                f.write(SAMPLE_WILHELT_DECKLIST)

            out_file = os.path.join(tmpdir, "review.txt")
            exit_code = cli.main([
                "review",
                "--deck", deck_file,
                "--format", "cli",
                "--output", out_file,
            ])
            self.assertEqual(exit_code, 0)
            self.assertTrue(os.path.isfile(out_file))
            with open(out_file, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("COMMANDER LAB DECK REVIEW", content)

    # -------------------------------------------------------------------------
    # REST API Endpoint Tests
    # -------------------------------------------------------------------------

    def test_api_post_review_endpoint_success(self):
        """POST /api/review returns 200 with structured review, markdown, and cli text."""
        payload = {
            "name": "API Atraxa Test",
            "format": "commander",
            "decklist": SAMPLE_ATRAXA_DECKLIST,
            "user_intent": {
                "deck_vision": {"intent": "tune_up"},
                "target_power_level": {"scale": 8, "tier": "optimized"},
            },
        }
        response = self.client.post("/api/review", json=payload)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("review", data)
        self.assertIn("markdown", data)
        self.assertIn("cli", data)

        review = data["review"]
        self.assertEqual(review["commanders"], ["Atraxa, Praetors' Voice"])
        self.assertIn("recommendations", review)
        self.assertGreater(len(review["recommendations"]["swaps"]), 0)

    def test_api_post_decks_review_alias_endpoint(self):
        """POST /api/decks/review alias returns 200."""
        payload = {
            "decklist": SAMPLE_WILHELT_DECKLIST,
        }
        response = self.client.post("/api/decks/review", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["review"]["commanders"], ["Wilhelt, the Rotcleaver"])

    def test_api_post_review_invalid_intent_returns_422(self):
        """POST /api/review returns 422 if user_intent is malformed."""
        payload = {
            "decklist": SAMPLE_ATRAXA_DECKLIST,
            "user_intent": {
                "target_power_level": {"tier": "invincible"}  # Invalid tier
            },
        }
        response = self.client.post("/api/review", json=payload)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("error", data)

    def test_api_get_deck_review_not_found_returns_404(self):
        """GET /api/decks/{deck_id}/review returns 404 for nonexistent deck."""
        response = self.client.get("/api/decks/nonexistent-deck-12345/review")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data.get("success"))


if __name__ == "__main__":
    unittest.main()
