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
]
