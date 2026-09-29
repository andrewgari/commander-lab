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

# Alternatively, direct (non-decorator) registration of an unregistered class:
# register_provider(CommanderSaltProvider, name="commandersalt")

# Retrieval:
salt_provider = get_provider("commandersalt")

# Listing:
all_providers = list_providers()  # ['commandersalt', 'edhrec', ...]
```

---

## 4. Commander Salt Provider Adapter (`analytics.providers.commandersalt`)

`CommanderSaltProvider` (registered as `"commandersalt"`) is a concrete
`BaseAnalyticsProvider` implementation backed by Commander Salt's unofficial,
unauthenticated `api.commandersalt.com/decks?id=...` endpoint (the same data
commandersalt.com's own frontend consumes — Commander Salt has no official
public API or documented auth scheme).

```python
from analytics import get_provider
from analytics.providers import CommanderSaltProvider  # registers "commandersalt" on import

provider = get_provider("commandersalt")
result = provider.analyze_deck(deck)  # DeckAnalyticsResult (meta_scores only)
```

### Data sourced

A single deck lookup supplies everything this adapter needs, so
`get_meta_scores()` issues exactly one HTTP request per deck (the raw
payload is cached per resolved source URL for the provider instance's
lifetime):

- **Salt score** — `SaltScore.score` (Commander Salt's deck-wide
  `saltRating`), `SaltScore.salt_sum` (sum of all per-card salt values), and
  `SaltScore.high_salt_cards` (top contributing cards, ranked descending).
- **Power level estimate** — `PowerScore.score` (0-10 scale, from
  `powerLevelRating`), `PowerScore.tier` (Commander Salt's own bracket
  label, e.g. `"cEDH"`), and `PowerScore.breakdown` (per-category subscores:
  stax, ramp, combos, interaction, etc.).
- **Combo detection** — surfaced under
  `MetaScores.provider_metrics["combos"]`: combo count, number of
  independent effective win lines, redundancy classification, a
  human-readable summary, and a list of individual combos (participating
  cards, outcome categories, score, and a Commander Spellbook link).
- **Other provider metrics** — `bracket_rating`, `synergy_rating`,
  `threat_rating`, and `archetype_label` are passed through under
  `MetaScores.provider_metrics` when present.

Commander Salt does not publish card recommendation, synergy, or popularity
data in a form directly comparable to the other providers, so
`get_recommendations()`, `get_synergy()`, and `get_popularity()` raise
`UnsupportedAnalyticsQueryError`; `analyze_deck()` skips those dimensions
automatically via `SUPPORTED_QUERIES = {"meta_scores"}`.

### Deck identifier resolution

Commander Salt's lookup endpoint keys off the deck's *source* URL (matching
the "Commander Salt" link construction in `templates/deck.html` /
`templates/decks.html`), not an internal Commander Lab id.
`analytics.providers.commandersalt.resolve_source_url()` translates
`DecklistInput.deck_id` into that form and accepts:

- a full `http(s)://` deck URL, used as-is;
- a `"<provider>:<id>"` reference (e.g. `"archidekt:6862011"`), expanded to
  `https://archidekt.com/decks/6862011` (`"moxfield:<id>"` similarly); or
- a raw 32-character Commander Salt internal deck id, used as-is.

### Lazy ingestion

Commander Salt scores a deck the first time it is looked up. A deck that has
never been viewed on commandersalt.com returns HTTP 200 with
`status.exists=False` / `status.invalid=True` and empty scoring, rather than
an HTTP error. The adapter raises `CommanderSaltNotIngestedError` for this
case (distinct from `CommanderSaltNotFoundError`, a real 404) so callers can
decide whether to retry once Commander Salt has finished importing the deck.

### Error handling & rate limiting

`CommanderSaltClient` wraps outbound requests with:
- **Rate limiting** — a configurable minimum interval between requests
  (`min_request_interval`, default 0.6s) to avoid hammering an unofficial
  third-party endpoint.
- **Retry with backoff** — timeouts, connection errors, HTTP 429, and HTTP
  5xx responses are retried with exponential backoff (`max_retries`,
  default 3 attempts).
- **Typed exceptions** (`analytics.providers.commandersalt`):
  - `CommanderSaltNotFoundError` — no record at all for the deck identifier
    (HTTP 404 response from Commander Salt; not retried).
  - `CommanderSaltNotIngestedError` — a 200 response for a deck Commander
    Salt has not yet ingested/scored.
  - `CommanderSaltResponseError` — a 200 response that isn't valid JSON, or
    doesn't match the expected deck schema.
  - `CommanderSaltRequestError` — network/timeout/rate-limit/server errors
    that persisted after exhausting all retries, or a malformed/unsupported
    `deck_id`.

All four inherit `analytics.exceptions.AnalyticsProviderError`, so callers
that already handle the base analytics exception hierarchy catch Commander
Salt failures without a Commander-Salt-specific import. No authentication or
session is required: the endpoint is a public, unauthenticated GET.

---

## 5. EDHRec Provider Adapter (`analytics.providers.edhrec`)

`EDHRecProvider` is a concrete `BaseAnalyticsProvider` implementation backed
by EDHRec's unofficial `json.edhrec.com` JSON endpoints (the same data
EDHRec's own frontend consumes — EDHRec has no official public API).

```python
from analytics import get_provider
from analytics.providers import EDHRecProvider  # registers "edhrec" on import

provider = get_provider("edhrec")
result = provider.analyze_deck(deck)  # DeckAnalyticsResult
```

### Data sourced

A single EDHRec commander page (`GET
https://json.edhrec.com/pages/commanders/{slug}.json`) supplies everything
this adapter needs, so `analyze_deck()` issues exactly one HTTP request per
deck (the raw page is cached per commander slug for the provider instance's
lifetime):

- **Meta scores** — EDHRec's community salt score (`card.salt`) and
  meta rank/deck-count (`provider_metrics.edhrec_rank` /
  `edhrec_num_decks`). EDHRec does not publish a power-level score, so
  `MetaScores.power` is left `None`.
- **Recommendations** — cards from the `newcards`, `highsynergycards`,
  `topcards`, and `gamechangers` cardlists, deduplicated (highest synergy
  wins) and with cards already present in the input deck excluded.
- **Synergy** — per-card synergy ratings from the `highsynergycards`,
  `topcards`, and `gamechangers` cardlists; `overall_synergy` is the mean of
  the top synergy cards surfaced.
- **Popularity** — the commander's overall rank/deck count plus per-card
  inclusion counts and percentages computed from `num_decks /
  potential_decks` across all cardlists.

### Slug resolution

`analytics.providers.edhrec.slugify_card_name()` and `commander_slug()`
implement the same lowercase/strip-punctuation/hyphenate scheme as the
client-side `getEdhrecSlug()` helper in `templates/deck.html` /
`templates/decks.html`, so the adapter and the "open on EDHREC" UI links
resolve to the same URL. Partner/background commanders are combined with a
hyphen (matching EDHRec's own multi-commander page slugs); split cards use
only the front face.

### Error handling & rate limiting

`EDHRecClient` wraps outbound requests with:
- **Rate limiting** — a configurable minimum interval between requests
  (`min_request_interval`, default 0.6s) to avoid hammering an unofficial
  third-party endpoint.
- **Retry with backoff** — timeouts, connection errors, HTTP 429, and HTTP
  5xx responses are retried with exponential backoff (`max_retries`,
  default 3 attempts).
- **Typed exceptions** (`analytics.providers.edhrec`):
  - `EDHRecNotFoundError` — EDHRec has no page for the requested commander(s)
    (HTTP 404; not retried).
  - `EDHRecResponseError` — a 200 response that isn't valid JSON, or doesn't
    match the expected `container.json_dict` shape.
  - `EDHRecRequestError` — network/timeout/rate-limit/server errors that
    persisted after exhausting all retries.

All three inherit `analytics.exceptions.AnalyticsProviderError`, so callers
that already handle the base analytics exception hierarchy catch EDHRec
failures without an EDHRec-specific import.
