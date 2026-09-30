"""
Deck review and recommendation module facade for Commander Lab.

Re-exports core classes, models, and helper functions from:
- `analytics.recommendations` (card additions, candidate cuts, 1-to-1 swaps)
- `analytics.report` (DeckReview, DeckReportFormatter, review pipeline)

Also provides CLI entrypoint for evaluating decklists and exporting reviews
in Markdown, CLI plain text, or JSON format.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from analytics.recommendations import (
    CURATED_CARD_CATALOG,
    RECENT_SETS,
    CardSwapSuggestion,
    RecommendationGenerator,
    RecommendationSet,
    default_recommendation_generator,
)
from analytics.report import (
    DeckReportFormatter,
    DeckReview,
    DeckReviewPipeline,
    default_review_pipeline,
    generate_deck_review,
)
from user_intent import UserDeckIntent, parse_user_intent

# Convenient alias for generate_deck_review
review_deck = generate_deck_review

__all__ = [
    "CardSwapSuggestion",
    "RecommendationSet",
    "RecommendationGenerator",
    "default_recommendation_generator",
    "CURATED_CARD_CATALOG",
    "RECENT_SETS",
    "DeckReview",
    "DeckReportFormatter",
    "DeckReviewPipeline",
    "default_review_pipeline",
    "generate_deck_review",
    "review_deck",
]


# =============================================================================
# CLI Interface
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for deck review."""
    parser = argparse.ArgumentParser(
        prog="deck_review",
        description="Generate an actionable Commander deck review with recommendations and swaps.",
    )
    parser.add_argument(
        "--deck",
        "-d",
        required=True,
        help="Path to decklist file or raw decklist string.",
    )
    parser.add_argument(
        "--intent",
        "-i",
        default=None,
        help="Path to user intent JSON file or raw JSON string.",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["markdown", "cli", "json"],
        default="cli",
        help="Output report format (default: cli).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional file path to write the formatted report to.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    deck_input = args.deck
    deck_path = Path(deck_input)
    if deck_path.is_file():
        deck_content = deck_path.read_text(encoding="utf-8")
    else:
        deck_content = deck_input

    intent_obj = None
    if args.intent:
        intent_path = Path(args.intent)
        if intent_path.is_file():
            intent_raw = json.loads(intent_path.read_text(encoding="utf-8"))
        else:
            intent_raw = json.loads(args.intent)
        intent_obj = parse_user_intent(intent_raw)

    review = review_deck(deck=deck_content, intent=intent_obj)

    if args.format == "markdown":
        output = review.to_markdown()
    elif args.format == "json":
        output = review.to_json()
    else:
        output = review.to_cli()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Deck review written to: {out_path}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
