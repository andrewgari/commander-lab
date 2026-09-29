# Deck Analytics Domain Models and Provider Interface

This document specifies the unified domain models, abstract provider interface, and runtime registration system for third-party deck analytics in Commander Lab.

## Overview

Commander Lab integrates with third-party analytics sources (e.g. Commander Salt, EDHRec) to provide meta ratings, card recommendations, synergies, and popularity metrics.

To ensure extensibility and loose coupling:
1. **Unified Domain Models** (`analytics.models`) standardise inputs (decklists, commander cards) and outputs (meta scores, recommendations, synergy, popularity) using Pydantic v2.
2. **Abstract Provider Interface** (`analytics.provider.BaseAnalyticsProvider`) defines the query contract for all third-party adapters.
3. **Pluggable Provider Registry** (`analytics.registry`) enables runtime discovery and registration of analytics providers without core code changes.

---

## 1. Domain Models (`analytics.models`)

### Input Models

- **`CommanderIdentifier`**: Identifies a commander card with canonical name, optional Scryfall Oracle UUID, and optional printing UUID. Validates that card names are non-empty and UUIDs conform to RFC 4122 format.
- **`DeckCardEntry`**: Represents a card in a decklist with name, quantity (ge 1), category, and UUIDs.
- **`DecklistInput`**: Standard input payload containing commanders (min 1), cards (min 1), format (default: `'commander'`), and helper methods (`total_cards()`, `all_card_names()`).

### Output Models

- **Meta Scores**:
  - `SaltScore` & `SaltCardDetail`: Deck salt scores, high-salt card breakdowns, and salt sums.
  - `PowerScore`: 0.0-10.0 power level score, tier rating, and breakdown dict.
  - `MetaScores`: Aggregated salt, power, meta rank, and provider-specific metrics.
- **Card Recommendations**:
  - `CardRecommendation`: Suggested additions with synergy scores (-1.0 to 1.0), inclusion rates (0.0 to 1.0), and rationale.
  - `CardCutRecommendation`: Suggested cuts with negative synergy ratings and rationale.
  - `CardRecommendations`: Collection of additions and cuts with total counts.
- **Synergy Metrics**:
  - `SynergyMetric`: Per-card synergy rating (-1.0 to 1.0) evaluated against commanders.
  - `DeckSynergy`: Overall deck synergy score and per-card synergy breakdown.
- **Popularity Metrics**:
  - `PopularityMetric`: Card deck count, meta percentage (0.0 to 100.0), and rank.
  - `DeckPopularity`: Commander meta rank, total decks count, and percentile.
- **Unified Result**:
  - `DeckAnalyticsResult`: Complete analytics bundle containing `provider_name`, timestamp, `meta_scores`, `recommendations`, `synergy`, `popularity`, and arbitrary `raw_metadata`.

---

## 2. Abstract Base Provider Interface (`analytics.provider`)

All analytics provider clients must inherit from `BaseAnalyticsProvider` and implement:

```python
from analytics import BaseAnalyticsProvider, DecklistInput, MetaScores, CardRecommendations, DeckSynergy, DeckPopularity

class MyProvider(BaseAnalyticsProvider):
    @property
    def name(self) -> str:
        return "my_provider"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores: ...
    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations: ...
    def get_synergy(self, deck: DecklistInput) -> DeckSynergy: ...
    def get_popularity(self, deck: DecklistInput) -> DeckPopularity: ...
```

### Composite Analysis
The default `analyze_deck(deck: DecklistInput) -> DeckAnalyticsResult` queries all supported dimensions (`supports_query(...)`) and aggregates them into a `DeckAnalyticsResult`. Subclasses may override `analyze_deck` if an upstream API provides a single consolidated response.

---

## 3. Pluggable Registration Mechanism (`analytics.registry`)

Providers can be registered dynamically at runtime by class, instance, or decorator:

```python
from analytics import register_provider, get_provider, list_providers, BaseAnalyticsProvider

# Decorator registration:
@register_provider(name="commandersalt")
class CommanderSaltProvider(BaseAnalyticsProvider):
    ...

# Direct registration:
register_provider(CommanderSaltProvider, name="commandersalt")

# Retrieval:
salt_provider = get_provider("commandersalt")

# Listing:
all_providers = list_providers()  # ['commandersalt', ...]
```
