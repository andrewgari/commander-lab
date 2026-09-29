"""
Commander Salt analytics provider adapter.

Integrates with Commander Salt's unofficial `api.commandersalt.com` JSON
endpoint (the same data commandersalt.com's own frontend consumes) to source
deck salt scores, high-salt card breakdowns, detected combo lines, and power
level estimates, mapping them onto the unified `analytics.models` domain
schema.

Commander Salt has no official public API and no documented authentication
scheme -- the deck-lookup endpoint used here (`GET /decks?id=...`) is a plain
unauthenticated GET that requires no session, cookies, or API key (confirmed
live, 2026-09). Commander Salt lazily ingests decks the first time they are
looked up: a deck that has never been viewed on commandersalt.com returns a
200 response with `status.exists=False` / `status.invalid=True` and empty
scoring, rather than an HTTP error. This adapter surfaces that state as a
typed `CommanderSaltNotIngestedError` distinct from a real "not found" so
callers can decide whether to retry after ingestion completes.

See docs/DECK_ANALYTICS.md and the `mtg-apis` skill for background on the
Commander Salt integration and its data model quirks.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from analytics.exceptions import AnalyticsProviderError, UnsupportedAnalyticsQueryError
from analytics.models import (
    CardRecommendations,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PowerScore,
    SaltCardDetail,
    SaltScore,
)
from analytics.provider import BaseAnalyticsProvider
from analytics.registry import register_provider

__all__ = [
    "CommanderSaltClient",
    "CommanderSaltProvider",
    "CommanderSaltError",
    "CommanderSaltNotFoundError",
    "CommanderSaltNotIngestedError",
    "CommanderSaltRequestError",
    "CommanderSaltResponseError",
    "resolve_source_url",
]


# =============================================================================
# Exceptions
# =============================================================================

class CommanderSaltError(AnalyticsProviderError):
    """Base exception for all Commander Salt adapter errors."""


class CommanderSaltNotFoundError(CommanderSaltError):
    """Raised when Commander Salt has no record at all for the requested
    deck identifier (HTTP 404, or an unrecognized/malformed identifier)."""


class CommanderSaltNotIngestedError(CommanderSaltError):
    """Raised when Commander Salt returns a 200 response for a deck it has
    not yet ingested/scored (`status.exists=False` / `status.invalid=True`).
    Commander Salt ingests decks lazily on first lookup; retrying after a
    delay may succeed once ingestion completes."""


class CommanderSaltRequestError(CommanderSaltError):
    """Raised when the Commander Salt request fails after exhausting
    retries (network errors, timeouts, persistent 5xx/429 responses)."""


class CommanderSaltResponseError(CommanderSaltError):
    """Raised when Commander Salt returns a 200 response that is not valid
    JSON, or whose shape does not match the expected deck schema."""


# =============================================================================
# Deck identifier resolution
#
# `DecklistInput.deck_id` uses the "<provider>:<id>" convention documented in
# analytics/models.py (e.g. "archidekt:6862011"). Commander Salt's deck
# lookup keys off the *source* deck URL (matching the "Commander Salt" link
# construction in templates/deck.html and templates/decks.html), so this
# module translates known provider prefixes into that URL. A bare
# http(s):// deck_id, or a raw Commander Salt internal deck id, is passed
# through unchanged.
# =============================================================================

_SOURCE_URL_BUILDERS: Dict[str, Callable[[str], str]] = {
    "archidekt": lambda ref: f"https://archidekt.com/decks/{ref}",
    "moxfield": lambda ref: f"https://moxfield.com/decks/{ref}",
}

_COMMANDERSALT_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def resolve_source_url(deck_id: Optional[str]) -> str:
    """Resolve a `DecklistInput.deck_id` value into the identifier Commander
    Salt's `/decks` endpoint expects.

    Accepts:
      - a full ``http(s)://`` deck URL (Archidekt/Moxfield/etc.), used as-is
      - a ``"<provider>:<id>"`` reference (e.g. ``"archidekt:6862011"``),
        expanded into that provider's deck URL
      - a raw 32-character Commander Salt internal deck id, used as-is

    Raises:
        CommanderSaltRequestError: if `deck_id` is missing or its provider
            prefix is not one Commander Salt is known to ingest.
    """
    if not deck_id or not deck_id.strip():
        raise CommanderSaltRequestError(
            "cannot query Commander Salt: deck has no deck_id "
            "(expected a source URL or '<provider>:<id>' reference)"
        )
    cleaned = deck_id.strip()

    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned

    if _COMMANDERSALT_ID_RE.match(cleaned):
        return cleaned

    if ":" in cleaned:
        provider, _, ref = cleaned.partition(":")
        builder = _SOURCE_URL_BUILDERS.get(provider.strip().lower())
        ref = ref.strip()
        if builder is not None and ref:
            return builder(ref)
        raise CommanderSaltRequestError(
            f"cannot query Commander Salt: unsupported deck_id source '{provider}' "
            f"(supported: {', '.join(sorted(_SOURCE_URL_BUILDERS))})"
        )

    raise CommanderSaltRequestError(
        f"cannot query Commander Salt: unrecognized deck_id format '{deck_id}'"
    )


def _clamp(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    if value is None:
        return None
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return None


# =============================================================================
# HTTP client: rate limiting, retries, typed errors
# =============================================================================

class CommanderSaltClient:
    """Thin, defensive HTTP client for Commander Salt's unofficial deck
    lookup endpoint.

    - Enforces a minimum interval between outbound requests (rate limiting)
      so a burst of provider calls doesn't hammer an unofficial third-party
      endpoint.
    - Retries transient failures (timeouts, connection errors, 429, 5xx)
      with exponential backoff, up to `max_retries` attempts.
    - Raises `CommanderSaltNotFoundError` for 404s,
      `CommanderSaltNotIngestedError` for decks Commander Salt has not yet
      scored, `CommanderSaltResponseError` for malformed/unexpected JSON,
      and `CommanderSaltRequestError` for anything else that exhausts
      retries.
    - No authentication/session is required: the endpoint is a public,
      unauthenticated GET.
    """

    BASE_URL = "https://api.commandersalt.com"
    USER_AGENT = "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        min_request_interval: float = 0.6,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session or requests.Session()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._min_request_interval = max(0.0, min_request_interval)
        self._max_retries = max(1, max_retries)
        self._backoff_factor = backoff_factor
        self._sleep = sleep_fn
        self._now = time_fn
        self._last_request_at: Optional[float] = None

    def _throttle(self) -> None:
        """Block until at least `min_request_interval` has elapsed since the
        previous outbound request."""
        if self._last_request_at is not None:
            elapsed = self._now() - self._last_request_at
            wait = self._min_request_interval - elapsed
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._now()

    def get_deck(self, source_url: str) -> Dict[str, Any]:
        """Fetch the raw deck payload for a resolved Commander Salt deck
        identifier (see `resolve_source_url`)."""
        headers = {"User-Agent": self.USER_AGENT, "Accept": "application/json"}
        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                response = self._session.get(
                    f"{self._base_url}/decks",
                    params={"id": source_url},
                    timeout=self._timeout,
                    headers=headers,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = exc
            except requests.exceptions.RequestException as exc:
                # Non-transient client-side request construction errors: don't retry.
                raise CommanderSaltRequestError(
                    f"Commander Salt request failed for {source_url}: {exc}"
                ) from exc
            else:
                if response.status_code == 404:
                    raise CommanderSaltNotFoundError(
                        f"Commander Salt has no record for: {source_url}"
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    last_error = CommanderSaltRequestError(
                        f"Commander Salt returned HTTP {response.status_code} for {source_url}"
                    )
                elif response.status_code != 200:
                    raise CommanderSaltRequestError(
                        f"Commander Salt returned unexpected HTTP {response.status_code} "
                        f"for {source_url}"
                    )
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise CommanderSaltResponseError(
                            f"Commander Salt returned invalid JSON for {source_url}"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise CommanderSaltResponseError(
                            f"Commander Salt returned unexpected response shape for {source_url}"
                        )
                    status = payload.get("status")
                    if isinstance(status, dict) and (
                        status.get("exists") is False or status.get("invalid") is True
                    ):
                        raise CommanderSaltNotIngestedError(
                            f"Commander Salt has not yet ingested/scored: {source_url} "
                            "(try again after Commander Salt has imported this deck)"
                        )
                    return payload

            if attempt < self._max_retries:
                self._sleep(self._backoff_factor * (2 ** (attempt - 1)))

        raise CommanderSaltRequestError(
            f"Commander Salt request failed for {source_url} after "
            f"{self._max_retries} attempts"
        ) from last_error


# =============================================================================
# Response parsing helpers
# =============================================================================

def _extract_high_salt_cards(
    cards: Optional[Dict[str, Any]], max_cards: int
) -> List[SaltCardDetail]:
    scored: List[Tuple[str, float]] = []
    for card in cards.values() if isinstance(cards, dict) else []:
        if not isinstance(card, dict):
            continue
        card_name = card.get("name")
        raw_salt = card.get("salt")
        if not card_name or raw_salt is None:
            continue
        try:
            salt_value = float(raw_salt)
        except (TypeError, ValueError):
            continue
        if salt_value <= 0:
            continue
        scored.append((card_name, salt_value))

    scored.sort(key=lambda item: item[1], reverse=True)
    details: List[SaltCardDetail] = []
    for rank, (card_name, salt_value) in enumerate(scored[:max_cards], start=1):
        details.append(
            SaltCardDetail(card_name=card_name, salt_score=salt_value, rank=rank, oracle_id=None)
        )
    return details


def _sum_card_salt(cards: Optional[Dict[str, Any]]) -> Optional[float]:
    total = 0.0
    found = False
    for card in cards.values() if isinstance(cards, dict) else []:
        if not isinstance(card, dict):
            continue
        raw_salt = card.get("salt")
        if raw_salt is None:
            continue
        try:
            total += float(raw_salt)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _power_breakdown(power_level_scoring: Any) -> Dict[str, Any]:
    breakdown: Dict[str, Any] = {}
    if not isinstance(power_level_scoring, dict):
        return breakdown
    for category, entry in power_level_scoring.items():
        if isinstance(entry, dict) and "score" in entry:
            breakdown[category] = entry["score"]
    return breakdown


def _power_description(manuel: Any) -> Optional[str]:
    if not isinstance(manuel, dict):
        return None
    responses = manuel.get("listOfResponses")
    if isinstance(responses, list) and responses:
        first = responses[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _combo_summary(combos: Any, max_combos: int) -> Dict[str, Any]:
    if not isinstance(combos, dict):
        return {"count": 0, "score": 0, "wincon_count": None, "combos": []}

    combo_list = combos.get("list")
    profile = combos.get("profile") if isinstance(combos.get("profile"), dict) else {}

    entries: List[Dict[str, Any]] = []
    if isinstance(combo_list, list):
        for combo in combo_list[:max_combos]:
            if not isinstance(combo, dict):
                continue
            entries.append(
                {
                    "id": combo.get("id"),
                    "cards": combo.get("cards") or [],
                    "type": combo.get("type"),
                    "categories": combo.get("categories") or [],
                    "score": combo.get("score"),
                    "spellbook_uri": combo.get("spellbookUri"),
                }
            )

    headline = profile.get("headline")
    return {
        "count": profile.get("count", len(combo_list) if isinstance(combo_list, list) else 0),
        "score": combos.get("score", 0),
        "wincon_count": profile.get("winconCount"),
        "effective_lines": profile.get("effectiveLines"),
        "redundancy": profile.get("redundancy"),
        "summary": headline.get("summary") if isinstance(headline, dict) else None,
        "combos": entries,
    }


# =============================================================================
# Provider
# =============================================================================

@register_provider(name="commandersalt")
class CommanderSaltProvider(BaseAnalyticsProvider):
    """`BaseAnalyticsProvider` adapter backed by Commander Salt's
    unofficial deck-lookup JSON endpoint.

    A single Commander Salt deck lookup contains everything this adapter
    needs (salt score, high-salt card breakdown, combo detection, and power
    level estimates), so `_fetch_raw` caches the raw payload per resolved
    source URL for the lifetime of the provider instance.

    Commander Salt does not publish card recommendation, synergy, or
    popularity data in a form comparable to the other providers, so those
    three query dimensions are declared unsupported: `analyze_deck()` skips
    them automatically (per `SUPPORTED_QUERIES`), and calling their getters
    directly raises `UnsupportedAnalyticsQueryError`.
    """

    SUPPORTED_QUERIES = {"meta_scores"}

    def __init__(
        self,
        client: Optional[CommanderSaltClient] = None,
        max_high_salt_cards: int = 15,
        max_combos: int = 15,
    ) -> None:
        self._client = client or CommanderSaltClient()
        self._max_high_salt_cards = max_high_salt_cards
        self._max_combos = max_combos
        self._cache: Dict[str, Dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "commandersalt"

    def _fetch_raw(self, deck: DecklistInput) -> Dict[str, Any]:
        source_url = resolve_source_url(deck.deck_id)

        cached = self._cache.get(source_url)
        if cached is not None:
            return cached

        raw = self._client.get_deck(source_url)
        self._cache[source_url] = raw
        return raw

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        raw = self._fetch_raw(deck)
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        cards = raw.get("cards") if isinstance(raw.get("cards"), dict) else {}

        salt_rating = raw.get("saltRating")
        salt: Optional[SaltScore] = None
        if salt_rating is not None:
            try:
                salt = SaltScore(
                    score=max(0.0, float(salt_rating)),
                    salt_sum=_sum_card_salt(cards),
                    high_salt_cards=_extract_high_salt_cards(cards, self._max_high_salt_cards),
                    description=(
                        "Commander Salt deck-wide salt rating, aggregated from tax "
                        "effects, meta staples, lockouts, and infinite combo pieces."
                    ),
                )
            except (TypeError, ValueError):
                salt = None

        power_level_rating = raw.get("powerLevelRating")
        power: Optional[PowerScore] = None
        if power_level_rating is not None:
            try:
                power_level_details = (
                    details.get("powerLevel") if isinstance(details, dict) else {}
                )
                power_level_scoring = (
                    power_level_details.get("scoring")
                    if isinstance(power_level_details, dict)
                    else {}
                )
                manuel = raw.get("manuel")
                power = PowerScore(
                    score=_clamp(power_level_rating, 0.0, 10.0) or 0.0,
                    tier=manuel.get("category") if isinstance(manuel, dict) else None,
                    description=_power_description(manuel),
                    breakdown=_power_breakdown(power_level_scoring),
                )
            except (TypeError, ValueError):
                power = None

        combos_detail = details.get("combos") if isinstance(details, dict) else None
        provider_metrics: Dict[str, Any] = {
            "combos": _combo_summary(combos_detail, self._max_combos),
        }
        if raw.get("bracketRating") is not None:
            provider_metrics["bracket_rating"] = raw.get("bracketRating")
        if raw.get("synergyRating") is not None:
            provider_metrics["synergy_rating"] = raw.get("synergyRating")
        if raw.get("threatRating") is not None:
            provider_metrics["threat_rating"] = raw.get("threatRating")
        if raw.get("archetypeLabel") is not None:
            provider_metrics["archetype_label"] = raw.get("archetypeLabel")

        return MetaScores(
            salt=salt,
            power=power,
            meta_rank=None,
            provider_metrics=provider_metrics,
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        raise UnsupportedAnalyticsQueryError(
            "Commander Salt does not provide card recommendation data"
        )

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        raise UnsupportedAnalyticsQueryError(
            "Commander Salt does not provide per-card synergy data comparable "
            "to other providers; see MetaScores.provider_metrics for its "
            "deck-level synergy rating"
        )

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        raise UnsupportedAnalyticsQueryError(
            "Commander Salt does not provide card/commander popularity data"
        )
