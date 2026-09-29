"""
Abstract Base Provider interface for deck analytics.

Defines the contract for pluggable third-party analytics data sources
(such as Commander Salt, EDHRec, Moxfield, etc.).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Set

from .models import (
    CardRecommendations,
    DeckAnalyticsResult,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
)


class BaseAnalyticsProvider(ABC):
    """
    Abstract base class for all deck analytics providers.

    All third-party data source adapters must inherit from this class and
    implement its abstract methods to provide unified analytics data.
    """

    SUPPORTED_QUERIES: Set[str] = {
        "meta_scores",
        "recommendations",
        "synergy",
        "popularity",
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique identifier name for this provider (e.g. 'commandersalt', 'edhrec').
        """
        pass

    def supports_query(self, query_name: str) -> bool:
        """
        Check if this provider supports a specific analytics query.

        Args:
            query_name: Name of query ('meta_scores', 'recommendations', 'synergy', 'popularity').

        Returns:
            True if supported, False otherwise.
        """
        return query_name.lower().strip() in self.SUPPORTED_QUERIES

    @abstractmethod
    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        """
        Calculate or retrieve deck meta scores (salt score, power level).

        Args:
            deck: Validated input decklist.

        Returns:
            MetaScores containing salt and/or power evaluations.
        """
        pass

    @abstractmethod
    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        """
        Retrieve recommended card additions and suggested cuts for the deck.

        Args:
            deck: Validated input decklist.

        Returns:
            CardRecommendations containing recommended additions and cut candidates.
        """
        pass

    @abstractmethod
    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        """
        Calculate or retrieve synergy metrics between deck cards and commanders.

        Args:
            deck: Validated input decklist.

        Returns:
            DeckSynergy containing deck-level and per-card synergy metrics.
        """
        pass

    @abstractmethod
    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        """
        Retrieve meta inclusion statistics and popularity rankings.

        Args:
            deck: Validated input decklist.

        Returns:
            DeckPopularity containing rank and inclusion metrics.
        """
        pass

    def analyze_deck(self, deck: DecklistInput) -> DeckAnalyticsResult:
        """
        Execute comprehensive analytics covering all supported query dimensions.

        Aggregates outputs from get_meta_scores, get_recommendations,
        get_synergy, and get_popularity. Dimensions not supported by this
        provider will be left as None.

        Subclasses may override this method to optimize batch requests
        when underlying third-party APIs provide combined endpoints.

        Args:
            deck: Validated input decklist.

        Returns:
            Unified DeckAnalyticsResult.
        """
        meta_scores = self.get_meta_scores(deck) if self.supports_query("meta_scores") else None
        recommendations = (
            self.get_recommendations(deck) if self.supports_query("recommendations") else None
        )
        synergy = self.get_synergy(deck) if self.supports_query("synergy") else None
        popularity = self.get_popularity(deck) if self.supports_query("popularity") else None

        return DeckAnalyticsResult(
            provider_name=self.name,
            meta_scores=meta_scores,
            recommendations=recommendations,
            synergy=synergy,
            popularity=popularity,
        )
