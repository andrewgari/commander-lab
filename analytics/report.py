"""
Deck review synthesis and report formatting module for Commander Lab.

Transforms deck analysis (theme, curve, cohesion, playfeel, intent alignment)
and card recommendations (additions, cuts, swaps) into actionable, human-readable
reports in Markdown, CLI terminal text, and JSON formats.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field

from analytics.engine import (
    CohesionAnalysisBreakdown,
    CurveAnalysisBreakdown,
    DeckAnalysisEngine,
    DeckAnalysisReport,
    IntentAlignmentReport,
    PlayfeelAnalysisBreakdown,
    ThemeAnalysisBreakdown,
    default_analysis_engine,
)
from analytics.models import (
    CardRecommendations,
    DeckAnalyticsResult,
    DecklistInput,
)
from analytics.recommendations import (
    CardSwapSuggestion,
    RecommendationGenerator,
    RecommendationSet,
    default_recommendation_generator,
)
from user_intent import UserDeckIntent, parse_user_intent

logger = logging.getLogger(__name__)


# =============================================================================
# Deck Review Consolidated Domain Model
# =============================================================================

class DeckReview(BaseModel):
    """
    Consolidated deck review synthesizing evaluation scores, breakdowns,
    intent alignment, actionable upgrades, and direct card swap suggestions.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    deck_name: str = Field(..., description="Deck display name")
    format: str = Field("commander", description="Game format (default: 'commander')")
    commanders: List[str] = Field(default_factory=list, description="Commander card names")
    overall_score: float = Field(..., ge=0.0, le=100.0, description="Overall deck composite score")
    identity_summary: str = Field(..., description="Executive summary of deck identity and playstyle")
    thematic_assessment: ThemeAnalysisBreakdown = Field(..., description="Thematic analysis breakdown")
    playfeel_summary: PlayfeelAnalysisBreakdown = Field(..., description="Playfeel and salt breakdown")
    curve_summary: CurveAnalysisBreakdown = Field(..., description="Mana curve and speed breakdown")
    cohesion_summary: CohesionAnalysisBreakdown = Field(..., description="Cohesion and synergy breakdown")
    intent_alignment: IntentAlignmentReport = Field(..., description="User intent alignment report")
    strengths: List[str] = Field(default_factory=list, description="Identified deck strengths")
    cohesion_gaps: List[str] = Field(default_factory=list, description="Identified cohesion gaps and bottlenecks")
    recommendations: RecommendationSet = Field(..., description="Actionable recommendations and swaps")
    upgrades_summary: str = Field(..., description="Summary of suggested upgrades and impact")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Review execution metadata")

    def to_markdown(self) -> str:
        """Format review as clean, structured Markdown."""
        return DeckReportFormatter.format_markdown(self)

    def to_cli(self, width: int = 80) -> str:
        """Format review as clean ASCII text for CLI display."""
        return DeckReportFormatter.format_cli(self, width=width)

    def to_json(self, indent: int = 2) -> str:
        """Serialize review to JSON string."""
        return DeckReportFormatter.format_json(self, indent=indent)

    def export_markdown(self, filepath: Union[str, Path]) -> str:
        """Export review to a Markdown file on disk and return content."""
        content = self.to_markdown()
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return content

    def export_cli(self, filepath: Union[str, Path], width: int = 80) -> str:
        """Export review to a plain text file on disk and return content."""
        content = self.to_cli(width=width)
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return content

    def export_json(self, filepath: Union[str, Path], indent: int = 2) -> str:
        """Export review to a JSON file on disk and return content."""
        content = self.to_json(indent=indent)
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return content


# =============================================================================
# Report Formatter
# =============================================================================

class DeckReportFormatter:
    """
    Formats DeckReview instances into Markdown, CLI plain text, or JSON.
    """

    @classmethod
    def format_markdown(cls, review: DeckReview) -> str:
        """
        Produce a clean, publication-ready Markdown report.
        """
        lines: List[str] = []
        cmd_str = ", ".join(review.commanders) if review.commanders else "None specified"
        theme = review.thematic_assessment.primary_archetype.title()

        # Title & Badges
        lines.append(f"# Deck Review: {review.deck_name}")
        lines.append("")
        lines.append(
            f"**Commander:** {cmd_str} | **Format:** {review.format.title()} | "
            f"**Overall Score:** {review.overall_score:.1f}/100"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

        # 1. Executive Summary & Identity
        lines.append("## 1. Deck Identity & Executive Summary")
        lines.append("")
        lines.append(review.identity_summary)
        lines.append("")
        lines.append(
            f"- **Primary Archetype:** {theme} (Consistency: {review.thematic_assessment.score:.1f}/100 — {review.thematic_assessment.consistency_tier})"
        )
        if review.thematic_assessment.secondary_archetypes:
            secondaries = [
                f"{a.archetype.title()} ({a.score:.0f}%)"
                for a in review.thematic_assessment.secondary_archetypes[:3]
            ]
            lines.append(f"- **Secondary Archetypes:** {', '.join(secondaries)}")
        lines.append(
            f"- **Mana Curve Profile:** Avg CMC {review.curve_summary.average_cmc:.2f} | Speed Score {review.curve_summary.speed_score:.1f}/10 ({review.curve_summary.speed_tier})"
        )
        lines.append(
            f"- **Playfeel & Table Friction:** Salt Rating {review.playfeel_summary.salt_score:.1f}/100 ({review.playfeel_summary.salt_tier}) | Dynamic: {review.playfeel_summary.playfeel_label}"
        )
        lines.append("")

        # 2. Thematic & Archetype Assessment
        lines.append("## 2. Thematic & Archetype Assessment")
        lines.append("")
        lines.append(review.thematic_assessment.description)
        lines.append("")
        if review.thematic_assessment.thematic_tags_matched:
            top_tags = sorted(
                review.thematic_assessment.thematic_tags_matched.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:6]
            tag_badges = [f"`{k}` ({v} cards)" for k, v in top_tags]
            lines.append(f"**Core Mechanics Detected:** {', '.join(tag_badges)}")
            lines.append("")

        if review.thematic_assessment.off_theme_cards:
            off_cards = ", ".join(review.thematic_assessment.off_theme_cards[:6])
            lines.append(f"> ⚠️ **Thematic Dilution Notice:** The following cards show minimal synergy with the core gameplan: *{off_cards}*.")
            lines.append("")

        # 3. Playfeel & Pacing Summary
        lines.append("## 3. Playfeel & Pacing Summary")
        lines.append("")
        lines.append(review.playfeel_summary.playfeel_summary)
        lines.append("")
        lines.append(
            f"- **Speed Rating:** {review.curve_summary.speed_score:.1f}/10 ({review.curve_summary.speed_tier}) — "
            f"Estimated operational turn: Turn {review.curve_summary.estimated_operational_turn}"
        )
        lines.append(
            f"- **Ramp & Acceleration:** {review.curve_summary.total_ramp} ramp sources across "
            f"{review.curve_summary.total_spells + review.curve_summary.total_lands} total cards"
        )

        if review.playfeel_summary.high_salt_cards:
            salty_list = ", ".join(c.card_name for c in review.playfeel_summary.high_salt_cards[:5])
            lines.append(f"- **High-Salt Inclusions:** {salty_list}")

        lines.append("")

        # 4. Strengths & Cohesion Gaps
        lines.append("## 4. Strengths & Cohesion Gaps")
        lines.append("")
        lines.append("### Key Strengths")
        if review.strengths:
            for s in review.strengths:
                lines.append(f"- {s}")
        else:
            lines.append("- Solid foundational card distribution and commander synergy.")
        lines.append("")

        lines.append("### Cohesion Gaps & Bottlenecks")
        if review.cohesion_gaps:
            for g in review.cohesion_gaps:
                lines.append(f"- {g}")
        else:
            lines.append("- No critical cohesion gaps or severe curve bottlenecks detected.")
        lines.append("")

        # 5. User Intent Alignment
        lines.append("## 5. User Intent Alignment")
        lines.append("")
        status_icon = "✅" if review.intent_alignment.is_aligned else "⚠️"
        lines.append(
            f"**Alignment Score:** {review.intent_alignment.alignment_score:.1f}/100 {status_icon} "
            f"({'Aligned with goals' if review.intent_alignment.is_aligned else 'Intent conflicts detected'})"
        )
        lines.append("")
        if review.intent_alignment.details:
            for d in review.intent_alignment.details:
                lines.append(f"- {d}")
        if review.intent_alignment.conflicts:
            lines.append("")
            lines.append("### Identified Goal Conflicts")
            for c in review.intent_alignment.conflicts:
                lines.append(f"- ❗ {c}")
        lines.append("")

        # 6. Actionable Upgrades & Card Swaps
        lines.append("## 6. Actionable Recommendations & Card Swaps")
        lines.append("")
        lines.append(review.upgrades_summary)
        lines.append("")

        # 1-to-1 Swaps Table
        if review.recommendations.swaps:
            lines.append("### Suggested 1-to-1 Card Swaps")
            lines.append("")
            lines.append("| Cut (Out) | Add (In) | Mana Diff | Synergy Gain | Category | Upgrade Rationale |")
            lines.append("| :--- | :--- | :---: | :---: | :--- | :--- |")
            for s in review.recommendations.swaps:
                mana_diff_str = f"{s.net_cmc_change:+.1f}" if s.net_cmc_change is not None else "-"
                syn_gain_str = f"{s.synergy_gain:+.2f}" if s.synergy_gain is not None else "-"
                lines.append(
                    f"| **{s.card_out.card_name}** | **{s.card_in.card_name}** | "
                    f"`{mana_diff_str}` | `{syn_gain_str}` | {s.category} | {s.swap_rationale} |"
                )
            lines.append("")

        # Recommended Additions
        if review.recommendations.additions:
            lines.append("### Recommended Additions (Ranked)")
            lines.append("")
            for i, add in enumerate(review.recommendations.additions[:8], 1):
                badge_list = " ".join([f"`{c}`" for c in add.categories[:3]])
                score_str = f"Score: {add.score:.2f}" if add.score else ""
                syn_str = f"Synergy: {add.synergy:+.2f}" if add.synergy is not None else ""
                metrics = " | ".join(filter(None, [score_str, syn_str]))
                lines.append(f"{i}. **{add.card_name}** {badge_list}")
                if metrics:
                    lines.append(f"   *{metrics}*")
                lines.append(f"   {add.reason or 'Synergistic addition for deck gameplan.'}")
            lines.append("")

        # Recommended Cuts
        if review.recommendations.cuts:
            lines.append("### Recommended Cuts")
            lines.append("")
            for i, cut in enumerate(review.recommendations.cuts[:6], 1):
                lines.append(f"{i}. **{cut.card_name}**")
                lines.append(f"   {cut.reason or 'Low synergy or curve bottleneck candidate for replacement.'}")
            lines.append("")

        # Recent Sets & Unique Synergies Spotlight
        if review.recommendations.recent_set_cards or review.recommendations.unique_synergies:
            lines.append("### Recent Sets & Unique Synergies Spotlight")
            lines.append("")
            featured = review.recommendations.recent_set_cards[:4] or review.recommendations.unique_synergies[:4]
            for card in featured:
                set_badge = next((c for c in card.categories if c.startswith("[")), "Modern")
                lines.append(f"- **{card.card_name}** ({set_badge}): {card.reason}")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def format_cli(cls, review: DeckReview, width: int = 80) -> str:
        """
        Produce a clean, formatted ASCII text report for terminal output.
        """
        lines: List[str] = []
        div_heavy = "=" * width
        div_light = "-" * width

        cmd_str = ", ".join(review.commanders) if review.commanders else "None specified"
        theme = review.thematic_assessment.primary_archetype.upper()

        lines.append(div_heavy)
        title_str = f"COMMANDER LAB DECK REVIEW: {review.deck_name.upper()}"
        lines.append(title_str.center(width))
        lines.append(div_heavy)

        lines.append(f"Commander:    {cmd_str}")
        lines.append(f"Format:       {review.format.title()} | Overall Score: {review.overall_score:.1f}/100")
        lines.append(f"Archetype:    {theme} (Consistency: {review.thematic_assessment.score:.1f}/100 - {review.thematic_assessment.consistency_tier})")
        lines.append(f"Curve/Speed:  Avg CMC {review.curve_summary.average_cmc:.2f} | Speed Score {review.curve_summary.speed_score:.1f}/10 ({review.curve_summary.speed_tier})")
        lines.append(f"Playfeel:     Salt Rating {review.playfeel_summary.salt_score:.1f}/100 ({review.playfeel_summary.salt_tier}) | Dynamic: {review.playfeel_summary.playfeel_label}")
        lines.append(div_light)

        # Executive Summary
        lines.append("[ 1. EXECUTIVE SUMMARY & IDENTITY ]")
        lines.append(cls._wrap_text(review.identity_summary, width, indent="  "))
        lines.append("")

        # Thematic Assessment
        lines.append("[ 2. THEMATIC & ARCHETYPE ASSESSMENT ]")
        lines.append(cls._wrap_text(review.thematic_assessment.description, width, indent="  "))
        if review.thematic_assessment.thematic_tags_matched:
            top_tags = sorted(
                review.thematic_assessment.thematic_tags_matched.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]
            tag_str = ", ".join([f"{k} ({v})" for k, v in top_tags])
            lines.append(f"  Key Mechanics: {tag_str}")
        lines.append("")

        # Playfeel & Pacing
        lines.append("[ 3. PLAYFEEL & PACING SUMMARY ]")
        lines.append(cls._wrap_text(review.playfeel_summary.playfeel_summary, width, indent="  "))
        lines.append(f"  Operational Pace: Online by Turn {review.curve_summary.estimated_operational_turn} ({review.curve_summary.total_ramp} ramp cards)")
        if review.playfeel_summary.high_salt_cards:
            salty_list = ", ".join(c.card_name for c in review.playfeel_summary.high_salt_cards[:5])
            lines.append(f"  High-Salt Cards:  {salty_list}")
        lines.append("")

        # Strengths & Cohesion Gaps
        lines.append("[ 4. STRENGTHS & COHESION GAPS ]")
        lines.append("  Key Strengths:")
        for s in review.strengths[:4]:
            lines.append(f"    + {s}")
        lines.append("  Cohesion Gaps & Bottlenecks:")
        for g in review.cohesion_gaps[:4]:
            lines.append(f"    - {g}")
        lines.append("")

        # User Intent Alignment
        lines.append("[ 5. USER INTENT ALIGNMENT ]")
        align_str = "ALIGNED" if review.intent_alignment.is_aligned else "CONFLICTS DETECTED"
        lines.append(f"  Alignment: {review.intent_alignment.alignment_score:.1f}/100 [{align_str}]")
        for d in review.intent_alignment.details[:3]:
            lines.append(f"    * {d}")
        for c in review.intent_alignment.conflicts[:2]:
            lines.append(f"    ! CONFLICT: {c}")
        lines.append("")

        # Card Swaps
        lines.append("[ 6. ACTIONABLE CARD SWAPS ]")
        if review.recommendations.swaps:
            for i, s in enumerate(review.recommendations.swaps[:6], 1):
                mana_diff = f"{s.net_cmc_change:+.1f} CMC" if s.net_cmc_change is not None else ""
                lines.append(f"  {i}. CUT: {s.card_out.card_name} -> ADD: {s.card_in.card_name} ({s.category} | {mana_diff})")
                lines.append(cls._wrap_text(s.swap_rationale, width, indent="     "))
        else:
            lines.append("  No direct card swaps proposed.")
        lines.append("")

        # Recommended Additions
        lines.append("[ 7. TOP RECOMMENDED ADDITIONS ]")
        for i, add in enumerate(review.recommendations.additions[:6], 1):
            categories = ", ".join(add.categories[:2])
            lines.append(f"  {i}. {add.card_name} [{categories}] (Score: {add.score or 0.0:.2f})")
            lines.append(cls._wrap_text(add.reason or "Synergistic addition", width, indent="     "))
        lines.append("")

        # Recommended Cuts
        lines.append("[ 8. CANDIDATE CUTS ]")
        for i, cut in enumerate(review.recommendations.cuts[:5], 1):
            lines.append(f"  {i}. {cut.card_name}")
            lines.append(cls._wrap_text(cut.reason or "Cut candidate", width, indent="     "))

        lines.append(div_heavy)
        return "\n".join(lines)

    @classmethod
    def format_json(cls, review: DeckReview, indent: int = 2) -> str:
        """
        Serialize DeckReview to a JSON formatted string.
        """
        return json.dumps(review.model_dump(), indent=indent, default=str)

    @staticmethod
    def _wrap_text(text: str, width: int, indent: str = "") -> str:
        """Simple text wrapping utility for clean CLI display."""
        import textwrap
        return textwrap.fill(
            text,
            width=width,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )


# =============================================================================
# Deck Review Synthesis Pipeline
# =============================================================================

class DeckReviewPipeline:
    """
    Orchestrates end-to-end deck review:
    1. Runs DeckAnalysisEngine to evaluate mechanics, theme, curve, and playfeel.
    2. Runs RecommendationGenerator to curate additions, cuts, and 1-to-1 swaps.
    3. Synthesizes executive summary, strengths, and cohesion gaps into DeckReview.
    """

    def __init__(
        self,
        analysis_engine: Optional[DeckAnalysisEngine] = None,
        recommendation_generator: Optional[RecommendationGenerator] = None,
    ):
        self.analysis_engine = analysis_engine or default_analysis_engine
        self.recommendation_generator = (
            recommendation_generator or default_recommendation_generator
        )

    def review(
        self,
        deck: Any,
        intent: Optional[Union[UserDeckIntent, Dict[str, Any]]] = None,
        external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
        card_metadata: Optional[Dict[str, Any]] = None,
        external_recommendations: Optional[CardRecommendations] = None,
    ) -> DeckReview:
        """
        Synthesize a comprehensive DeckReview report from raw or structured deck inputs.
        """
        # Step 1: Normalize input deck and intent
        intake_obj, resolved_intent = self.analysis_engine._normalize_inputs(deck, intent)

        # Step 2: Run core analysis engine
        analysis: DeckAnalysisReport = self.analysis_engine.analyze(
            deck=intake_obj,
            intent=resolved_intent,
            external_metrics=external_metrics,
            card_metadata=card_metadata,
        )

        # Step 3: Generate recommendations & swaps
        recommendations: RecommendationSet = self.recommendation_generator.generate(
            deck=intake_obj,
            analysis=analysis,
            intent=resolved_intent,
            external_recommendations=external_recommendations,
        )

        # Step 3: Synthesize Executive Summaries, Strengths, and Cohesion Gaps
        identity_summary = self._synthesize_identity(analysis, resolved_intent)
        strengths = self._synthesize_strengths(analysis, resolved_intent)
        cohesion_gaps = self._synthesize_cohesion_gaps(analysis, resolved_intent)
        upgrades_summary = self._synthesize_upgrades_summary(
            recommendations, analysis, resolved_intent
        )

        return DeckReview(
            deck_name=analysis.deck_name,
            format=analysis.format,
            commanders=analysis.commanders,
            overall_score=analysis.overall_score,
            identity_summary=identity_summary,
            thematic_assessment=analysis.theme_analysis,
            playfeel_summary=analysis.playfeel_analysis,
            curve_summary=analysis.curve_analysis,
            cohesion_summary=analysis.cohesion_analysis,
            intent_alignment=analysis.intent_alignment,
            strengths=strengths,
            cohesion_gaps=cohesion_gaps,
            recommendations=recommendations,
            upgrades_summary=upgrades_summary,
            metadata={
                "engine_version": "1.0.0",
                "total_recommendations": len(recommendations.additions),
                "total_cuts": len(recommendations.cuts),
                "total_swaps": len(recommendations.swaps),
            },
        )

    # -------------------------------------------------------------------------
    # Synthesis Helpers
    # -------------------------------------------------------------------------

    def _synthesize_identity(
        self, analysis: DeckAnalysisReport, intent: UserDeckIntent
    ) -> str:
        cmd_str = ", ".join(analysis.commanders) if analysis.commanders else "Commander"
        archetype = analysis.theme_analysis.primary_archetype.title()
        power_tier = intent.target_power_level.tier.title()
        vision_intent = intent.deck_vision.intent

        return (
            f"'{analysis.deck_name}' is a {power_tier}-tier {archetype} deck helmed by {cmd_str}. "
            f"The deck exhibits a {analysis.theme_analysis.consistency_tier.lower()} commitment to its primary theme "
            f"({analysis.theme_analysis.score:.0f}/100), operating with an average mana value of {analysis.curve_analysis.average_cmc:.2f} "
            f"and a salt rating of {analysis.playfeel_analysis.salt_score:.1f}/100. "
            f"Configured for a '{vision_intent}' review focused on {intent.preferred_win_conditions.primary} win conditions."
        )

    def _synthesize_strengths(
        self, analysis: DeckAnalysisReport, intent: UserDeckIntent
    ) -> List[str]:
        strengths: List[str] = []

        if analysis.theme_analysis.score >= 75.0:
            strengths.append(
                f"Strong thematic commitment to {analysis.theme_analysis.primary_archetype} ({analysis.theme_analysis.score:.0f}/100)."
            )
        if analysis.curve_analysis.average_cmc <= 3.2:
            strengths.append(
                f"Lean and disciplined mana curve (average CMC {analysis.curve_analysis.average_cmc:.2f}) promoting rapid deployment."
            )
        if analysis.curve_analysis.total_ramp >= 10:
            strengths.append(
                f"Robust ramp package ({analysis.curve_analysis.total_ramp} acceleration pieces) ensuring consistent operational pacing."
            )
        if analysis.cohesion_analysis.score >= 70.0:
            strengths.append(
                f"High internal synergy rating ({analysis.cohesion_analysis.score:.0f}/100) with well-clustered engine payoffs."
            )
        if analysis.playfeel_analysis.salt_score <= 25.0:
            strengths.append(
                f"Low salt rating ({analysis.playfeel_analysis.salt_score:.1f}/100) fostering positive table dynamics."
            )
        if analysis.intent_alignment.alignment_score >= 80.0:
            strengths.append(
                f"Strongly aligned with stated user intent and playstyle parameters ({analysis.intent_alignment.alignment_score:.0f}/100)."
            )

        if not strengths:
            strengths.append("Functional singleton mana base and distinct commander-led strategy.")

        return strengths

    def _synthesize_cohesion_gaps(
        self, analysis: DeckAnalysisReport, intent: UserDeckIntent
    ) -> List[str]:
        gaps: List[str] = []

        # Pull low-synergy cards or engine balance issues
        if analysis.cohesion_analysis.low_synergy_cards:
            low_names = [c.get("card_name", str(c)) for c in analysis.cohesion_analysis.low_synergy_cards[:4]]
            gaps.append(f"Low-synergy slots identified: {', '.join(low_names)}.")

        engine_status = analysis.cohesion_analysis.engine_balance.get("status")
        if engine_status and engine_status not in ("Balanced", "Optimal"):
            gaps.append(f"Engine imbalance detected: {engine_status}.")

        if analysis.curve_analysis.average_cmc > 3.6:
            gaps.append(
                f"Elevated average curve (CMC {analysis.curve_analysis.average_cmc:.2f}); vulnerable to stalling against faster pods."
            )
        if analysis.curve_analysis.total_ramp < 8:
            gaps.append(
                f"Low ramp density ({analysis.curve_analysis.total_ramp} pieces); risk of missing operational velocity on Turn 3-4."
            )
        if analysis.theme_analysis.off_theme_cards:
            gaps.append(
                f"{len(analysis.theme_analysis.off_theme_cards)} off-theme cards dilute synergy execution."
            )
        if analysis.playfeel_analysis.playfeel_label in ("High-Power / Spiky", "Oppressive"):
            gaps.append(
                f"High table friction dynamic detected ({analysis.playfeel_analysis.playfeel_label}) due to oppressive cards."
            )
        if analysis.intent_alignment.conflicts:
            for conflict in analysis.intent_alignment.conflicts:
                gaps.append(f"Intent gap: {conflict}")

        if not gaps:
            gaps.append("Minor opportunities to substitute generic staples with archetype-specific synergy pieces.")

        return gaps

    def _synthesize_upgrades_summary(
        self,
        recommendations: RecommendationSet,
        analysis: DeckAnalysisReport,
        intent: UserDeckIntent,
    ) -> str:
        swap_count = len(recommendations.swaps)
        recent_count = len(recommendations.recent_set_cards)
        primary_theme = analysis.theme_analysis.primary_archetype

        return (
            f"The proposed upgrade package features {swap_count} direct 1-to-1 card swaps designed to resolve "
            f"detected curve bottlenecks, eliminate {len(analysis.theme_analysis.off_theme_cards)} off-theme slots, "
            f"and incorporate {recent_count} high-impact recent set additions (2022-2024) to amplify {primary_theme} synergy."
        )


# Global default review pipeline instance
default_review_pipeline = DeckReviewPipeline()


def generate_deck_review(
    deck: Any,
    intent: Optional[Union[UserDeckIntent, Dict[str, Any]]] = None,
    external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
    card_metadata: Optional[Dict[str, Any]] = None,
    external_recommendations: Optional[CardRecommendations] = None,
) -> DeckReview:
    """
    Public entrypoint to generate a full DeckReview report.
    """
    return default_review_pipeline.review(
        deck=deck,
        intent=intent,
        external_metrics=external_metrics,
        card_metadata=card_metadata,
        external_recommendations=external_recommendations,
    )
