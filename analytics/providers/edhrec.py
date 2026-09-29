"""
EDHRec analytics provider adapter.

Integrates with EDHRec's unofficial `json.edhrec.com` JSON endpoints (the
same data EDHRec's own frontend consumes) to source commander synergy
ratings, popular/staple card inclusions, salt scores, and archetype/theme
tags, and maps them onto the unified `analytics.models` domain schema.

EDHRec has no official public API — see docs/DECK_ANALYTICS.md and the
`mtg-apis` skill for background. This module treats the endpoint as an
external, occasionally-unreliable dependency: requests are throttled,
retried with backoff on transient failures, and any unexpected response
shape raises a typed `EDHRecError` rather than propagating a raw KeyError.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from analytics.exceptions import AnalyticsProviderError
from analytics.models import (
    CardRecommendation,
    CardRecommendations,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PopularityMetric,
    PowerScore,
    SaltScore,
    SynergyMetric,
)
from analytics.provider import BaseAnalyticsProvider
from analytics.registry import register_provider

__all__ = [
    "EDHRecClient",
    "EDHRecProvider",
    "EDHRecError",
    "EDHRecNotFoundError",
    "EDHRecRequestError",
    "EDHRecResponseError",
    "commander_slug",
    "slugify_card_name",
]


# =============================================================================
# Exceptions
# =============================================================================

class EDHRecError(AnalyticsProviderError):
    """Base exception for all EDHRec adapter errors."""


class EDHRecNotFoundError(EDHRecError):
    """Raised when EDHRec has no page for the requested commander(s) (HTTP 404)."""


class EDHRecRequestError(EDHRecError):
    """Raised when the EDHRec request fails after exhausting retries
    (network errors, timeouts, persistent 5xx/429 responses)."""


class EDHRecResponseError(EDHRecError):
    """Raised when EDHRec returns a 200 response that is not valid JSON, or
    whose shape does not match the expected `container.json_dict` schema."""


# =============================================================================
# Slug helpers
#
# Mirrors the client-side `getEdhrecSlug()` used in templates/deck.html and
# templates/decks.html: lowercase, strip everything but [a-z0-9\s-], collapse
# whitespace runs to a single hyphen. Kept in sync intentionally so the API
# adapter and the "open on EDHREC" links in the UI resolve the same slug.
# =============================================================================

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_WHITESPACE_RE = re.compile(r"\s+")


def slugify_card_name(name: str) -> str:
    """Convert a card/commander name into EDHRec's URL slug format.

    Split cards (e.g. "Fire // Ice") use only the front face, matching
    EDHRec's own slug convention.
    """
    if not name:
        return ""
    front_face = name.split(" // ")[0]
    cleaned = _SLUG_STRIP_RE.sub("", front_face.lower())
    return _SLUG_WHITESPACE_RE.sub("-", cleaned.strip())


def commander_slug(commander_names: Sequence[str]) -> str:
    """Build the combined EDHRec commander-page slug for one or more
    commanders (partners / backgrounds are joined with '-'), e.g.
    ["Tymna the Weaver", "Thrasios, Triton Hero"] ->
    "tymna-the-weaver-thrasios-triton-hero".
    """
    slugs = [slugify_card_name(n) for n in commander_names if n and n.strip()]
    return "-".join(s for s in slugs if s)


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

class EDHRecClient:
    """Thin, defensive HTTP client for EDHRec's unofficial JSON endpoints.

    - Enforces a minimum interval between outbound requests (rate limiting)
      so a burst of provider calls doesn't hammer an unofficial third-party
      endpoint.
    - Retries transient failures (timeouts, connection errors, 429, 5xx)
      with exponential backoff, up to `max_retries` attempts.
    - Raises `EDHRecNotFoundError` for 404s, `EDHRecResponseError` for
      malformed/unexpected JSON, and `EDHRecRequestError` for anything else
      that exhausts retries.
    """

    BASE_URL = "https://json.edhrec.com/pages"
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

    def _get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"User-Agent": self.USER_AGENT, "Accept": "application/json"}
        last_error: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                response = self._session.get(url, timeout=self._timeout, headers=headers)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = exc
            except requests.exceptions.RequestException as exc:
                # Non-transient client-side request construction errors: don't retry.
                raise EDHRecRequestError(f"EDHRec request failed for {path}: {exc}") from exc
            else:
                if response.status_code == 404:
                    raise EDHRecNotFoundError(f"EDHRec has no page for: {path}")

                if response.status_code == 429 or response.status_code >= 500:
                    last_error = EDHRecRequestError(
                        f"EDHRec returned HTTP {response.status_code} for {path}"
                    )
                elif response.status_code != 200:
                    raise EDHRecRequestError(
                        f"EDHRec returned unexpected HTTP {response.status_code} for {path}"
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise EDHRecResponseError(
                            f"EDHRec returned invalid JSON for {path}"
                        ) from exc

            if attempt < self._max_retries:
                self._sleep(self._backoff_factor * (2 ** (attempt - 1)))

        raise EDHRecRequestError(
            f"EDHRec request failed for {path} after {self._max_retries} attempts"
        ) from last_error

    def get_commander_page(self, commander_names: Sequence[str]) -> Dict[str, Any]:
        """Fetch the raw commander page JSON for one or more commander names
        (partners are combined into a single slug, matching EDHRec's URL
        scheme)."""
        slug = commander_slug(commander_names)
        if not slug:
            raise ValueError("commander_names must contain at least one non-empty name")
        return self._get_json(f"commanders/{slug}.json")


# =============================================================================
# Response parsing helpers
# =============================================================================

# `cardlists[].tag` values (from json.edhrec.com/pages/commanders/*.json)
# that represent recommended additions worth surfacing.
_RECOMMENDATION_TAGS = {"newcards", "highsynergycards", "topcards", "gamechangers"}

# Tags used to compute per-card and overall deck synergy.
_SYNERGY_TAGS = {"highsynergycards", "topcards", "gamechangers"}


def _extract_json_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        json_dict = raw["container"]["json_dict"]
    except (KeyError, TypeError) as exc:
        raise EDHRecResponseError(
            "unexpected EDHRec response shape: missing container.json_dict"
        ) from exc
    if not isinstance(json_dict, dict):
        raise EDHRecResponseError("unexpected EDHRec response shape: json_dict is not an object")
    return json_dict


def _extract_cardlists(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    cardlists = _extract_json_dict(raw).get("cardlists")
    if cardlists is None:
        return []
    if not isinstance(cardlists, list):
        raise EDHRecResponseError("unexpected EDHRec response shape: cardlists is not a list")
    return cardlists


def _extract_card_meta(raw: Dict[str, Any]) -> Dict[str, Any]:
    card = _extract_json_dict(raw).get("card")
    return card if isinstance(card, dict) else {}


def _inclusion_rate(num_decks: Any, potential_decks: Any) -> Optional[float]:
    try:
        num_decks = float(num_decks)
        potential_decks = float(potential_decks)
    except (TypeError, ValueError):
        return None
    if potential_decks <= 0:
        return None
    return _clamp(num_decks / potential_decks, 0.0, 1.0)


# =============================================================================
# Provider
# =============================================================================

@register_provider(name="edhrec")
class EDHRecProvider(BaseAnalyticsProvider):
    """`BaseAnalyticsProvider` adapter backed by EDHRec's unofficial
    commander-page JSON endpoint.

    A single EDHRec commander page contains everything this adapter needs
    (salt score, card recommendations, synergy ratings, and deck-popularity
    counts), so `_fetch_raw` caches the raw page per commander slug for the
    lifetime of the provider instance -- `analyze_deck()` issues one HTTP
    request per deck instead of four.
    """

    def __init__(
        self,
        client: Optional[EDHRecClient] = None,
        max_recommendations: int = 15,
        max_synergy_cards: int = 15,
        max_popularity_cards: int = 15,
    ) -> None:
        self._client = client or EDHRecClient()
        self._max_recommendations = max_recommendations
        self._max_synergy_cards = max_synergy_cards
        self._max_popularity_cards = max_popularity_cards
        self._cache: Dict[str, Dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "edhrec"

    def _fetch_raw(self, deck: DecklistInput) -> Dict[str, Any]:
        commander_names = [c.name for c in deck.commanders]
        slug = commander_slug(commander_names)
        if not slug:
            raise EDHRecRequestError("cannot query EDHRec: deck has no commanders")

        cached = self._cache.get(slug)
        if cached is not None:
            return cached

        raw = self._client.get_commander_page(commander_names)
        self._cache[slug] = raw
        return raw

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        raw = self._fetch_raw(deck)
        card_meta = _extract_card_meta(raw)

        salt_value = card_meta.get("salt")
        salt = None
        if salt_value is not None:
            try:
                salt = SaltScore(
                    score=max(0.0, float(salt_value)),
                    description="EDHRec community salt score for this commander.",
                )
            except (TypeError, ValueError):
                salt = None

        provider_metrics: Dict[str, Any] = {}
        rank = card_meta.get("rank")
        num_decks = card_meta.get("num_decks")
        if rank is not None:
            provider_metrics["edhrec_rank"] = rank
        if num_decks is not None:
            provider_metrics["edhrec_num_decks"] = num_decks

        power: Optional[PowerScore] = None

        return MetaScores(
            salt=salt,
            power=power,
            meta_rank=None,
            provider_metrics=provider_metrics,
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        raw = self._fetch_raw(deck)
        cardlists = _extract_cardlists(raw)
        deck_card_names = {name.lower() for name in deck.all_card_names(include_commanders=True)}

        best_by_name: Dict[str, CardRecommendation] = {}
        for cardlist in cardlists:
            tag = str(cardlist.get("tag") or "").lower()
            if tag not in _RECOMMENDATION_TAGS:
                continue
            header = cardlist.get("header") or tag
            for view in cardlist.get("cardviews") or []:
                name = view.get("name")
                if not name or name.lower() in deck_card_names:
                    continue
                synergy = _clamp(view.get("synergy"), -1.0, 1.0)
                inclusion_rate = _inclusion_rate(view.get("num_decks"), view.get("potential_decks"))
                candidate = CardRecommendation(
                    card_name=name,
                    score=synergy,
                    synergy=synergy,
                    inclusion_rate=inclusion_rate,
                    reason=f"EDHRec {header}",
                    categories=[header],
                )
                existing = best_by_name.get(name.lower())
                if existing is None or (candidate.synergy or -1.0) > (existing.synergy or -1.0):
                    best_by_name[name.lower()] = candidate

        items = sorted(
            best_by_name.values(),
            key=lambda rec: rec.synergy if rec.synergy is not None else -1.0,
            reverse=True,
        )[: self._max_recommendations]

        return CardRecommendations(items=items, cuts=[])

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        raw = self._fetch_raw(deck)
        cardlists = _extract_cardlists(raw)
        commander_name = deck.commanders[0].name if deck.commanders else None

        best_by_name: Dict[str, SynergyMetric] = {}
        for cardlist in cardlists:
            tag = str(cardlist.get("tag") or "").lower()
            if tag not in _SYNERGY_TAGS:
                continue
            header = cardlist.get("header") or tag
            for view in cardlist.get("cardviews") or []:
                name = view.get("name")
                synergy_score = _clamp(view.get("synergy"), -1.0, 1.0)
                if not name or synergy_score is None:
                    continue
                candidate = SynergyMetric(
                    card_name=name,
                    synergy_score=synergy_score,
                    commander_name=commander_name,
                    context=f"EDHRec synergy rating from {header}",
                )
                existing = best_by_name.get(name.lower())
                if existing is None or candidate.synergy_score > existing.synergy_score:
                    best_by_name[name.lower()] = candidate

        card_synergies = sorted(
            best_by_name.values(), key=lambda m: m.synergy_score, reverse=True
        )[: self._max_synergy_cards]

        overall_synergy = None
        if card_synergies:
            overall_synergy = _clamp(
                sum(m.synergy_score for m in card_synergies) / len(card_synergies), -1.0, 1.0
            )

        return DeckSynergy(overall_synergy=overall_synergy, card_synergies=card_synergies)

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        raw = self._fetch_raw(deck)
        card_meta = _extract_card_meta(raw)
        cardlists = _extract_cardlists(raw)

        rank = card_meta.get("rank")
        num_decks = card_meta.get("num_decks")

        seen: Dict[str, PopularityMetric] = {}
        for cardlist in cardlists:
            for view in cardlist.get("cardviews") or []:
                name = view.get("name")
                deck_count = view.get("num_decks")
                if not name or deck_count is None or name.lower() in seen:
                    continue
                try:
                    deck_count_int = int(deck_count)
                except (TypeError, ValueError):
                    continue
                percentage = _inclusion_rate(view.get("num_decks"), view.get("potential_decks"))
                if percentage is not None:
                    percentage *= 100.0
                seen[name.lower()] = PopularityMetric(
                    card_name=name,
                    deck_count=deck_count_int,
                    percentage=percentage,
                )

        card_popularity = sorted(
            seen.values(), key=lambda m: m.deck_count or 0, reverse=True
        )[: self._max_popularity_cards]

        return DeckPopularity(
            rank=rank,
            num_decks=num_decks,
            popularity_percentile=None,
            card_popularity=card_popularity,
        )
