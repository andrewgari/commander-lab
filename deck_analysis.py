"""
Deck analysis module facade for Commander Lab.

Re-exports core classes, models, and helper functions from `analytics.engine`
so callers can import directly from `deck_analysis` or `analytics.engine`.
"""

from analytics.engine import (
    ARCHETYPE_PATTERNS,
    KNOWN_RAMP_CARDS,
    KNOWN_STAPLE_SALT,
    SALT_CATEGORY_CARDS,
    ArchetypeMatch,
    CohesionAnalysisBreakdown,
    CurveAnalysisBreakdown,
    DeckAnalysisEngine,
    DeckAnalysisReport,
    IntentAlignmentReport,
    PlayfeelAnalysisBreakdown,
    SaltCategoryBreakdown,
    SynergyCluster,
    ThemeAnalysisBreakdown,
    analyze_deck,
    default_analysis_engine,
    parse_mana_cost,
)

__all__ = [
    "ArchetypeMatch",
    "ThemeAnalysisBreakdown",
    "CurveAnalysisBreakdown",
    "SynergyCluster",
    "CohesionAnalysisBreakdown",
    "SaltCategoryBreakdown",
    "PlayfeelAnalysisBreakdown",
    "IntentAlignmentReport",
    "DeckAnalysisReport",
    "DeckAnalysisEngine",
    "analyze_deck",
    "default_analysis_engine",
    "parse_mana_cost",
    "SALT_CATEGORY_CARDS",
    "KNOWN_STAPLE_SALT",
    "KNOWN_RAMP_CARDS",
    "ARCHETYPE_PATTERNS",
]
