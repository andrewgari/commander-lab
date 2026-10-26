"""
Deck analytics package for Commander Lab.

Provides unified domain models, an abstract provider interface, and a pluggable
registry for third-party deck analytics data sources (e.g., Commander Salt, EDHRec).
"""
from .exceptions import (
    AnalyticsError,
    AnalyticsProviderError,
    ProviderNotFoundError,
    ProviderRegistrationError,
    UnsupportedAnalyticsQueryError,
)
from .models import (
    CardCutRecommendation,
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PopularityMetric,
    PowerScore,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
)
from .provider import BaseAnalyticsProvider
from .registry import (
    AnalyticsProviderRegistry,
    clear_registry,
    default_registry,
    get_provider,
    list_providers,
    register_provider,
    unregister_provider,
)
from .aggregator import (
    AggregatedAnalyticsReport,
    AnalyticsSummary,
    ConsolidatedMetrics,
    DeckAnalyticsAggregator,
    ProviderAnalyticsStatus,
    aggregate_deck_analytics,
    coerce_to_decklist_input,
    default_aggregator,
)
from .engine import (
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
from .recommendations import (
    CURATED_CARD_CATALOG,
    RECENT_SETS,
    CardSwapSuggestion,
    RecommendationGenerator,
    RecommendationSet,
    default_recommendation_generator,
)
from .report import (
    DeckReportFormatter,
    DeckReview,
    DeckReviewPipeline,
    default_review_pipeline,
    generate_deck_review,
)

__all__ = [
    # Input models
    "CommanderIdentifier",
    "DeckCardEntry",
    "DecklistInput",
    # Output models
    "SaltCardDetail",
    "SaltScore",
    "PowerScore",
    "MetaScores",
    "CardRecommendation",
    "CardCutRecommendation",
    "CardRecommendations",
    "SynergyMetric",
    "DeckSynergy",
    "PopularityMetric",
    "DeckPopularity",
    "DeckAnalyticsResult",
    # Aggregator models & service
    "AggregatedAnalyticsReport",
    "AnalyticsSummary",
    "ConsolidatedMetrics",
    "DeckAnalyticsAggregator",
    "ProviderAnalyticsStatus",
    "aggregate_deck_analytics",
    "coerce_to_decklist_input",
    "default_aggregator",
    # Provider interface
    "BaseAnalyticsProvider",
    # Registry & helpers
    "AnalyticsProviderRegistry",
    "default_registry",
    "register_provider",
    "get_provider",
    "list_providers",
    "unregister_provider",
    "clear_registry",
    # Exceptions
    "AnalyticsError",
    "AnalyticsProviderError",
    "ProviderNotFoundError",
    "ProviderRegistrationError",
    "UnsupportedAnalyticsQueryError",
    # Analysis Engine
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
    # Recommendations & Card Swaps
    "CardSwapSuggestion",
    "RecommendationSet",
    "RecommendationGenerator",
    "default_recommendation_generator",
    "CURATED_CARD_CATALOG",
    "RECENT_SETS",
    # Deck Review & Report Formatter
    "DeckReview",
    "DeckReportFormatter",
    "DeckReviewPipeline",
    "default_review_pipeline",
    "generate_deck_review",
]
