"""EDHRec API integration client using SharedHttpClient.

Provides:
- `EDHRecClient`: Async client for querying EDHRec's JSON endpoints.
  - `get_commander_page`: Fetch commander synergy and recommendations.
  - `get_card_page`: Fetch card overview and inclusion rates.
  - `get_card_synergy`: Calculate card synergy with an optional commander context.
  - `get_card_salt`: Fetch community salt score for a card.
- Helper functions: `slugify_card_name`, `commander_slug`.
- Standardized exceptions: `EDHRecError`, `EDHRecNotFoundError`,
  `EDHRecRateLimitError`, `EDHRecRequestError`, `EDHRecResponseError`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from src.integrations.models import CardSalt, CardSynergy
from src.shared.http import SharedHttpClient
from src.shared.http.exceptions import (
    ApiError,
    HttpClientError,
    RateLimitError,
    TimeoutError as HttpTimeoutError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EDHRecError(HttpClientError):
    """Base exception for all EDHRec client errors."""


class EDHRecNotFoundError(EDHRecError):
    """Raised when a commander or card is not found on EDHRec (HTTP 404)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 404,
        body: Any = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.status_code = status_code
        self.body = body


class EDHRecRateLimitError(EDHRecError):
    """Raised when EDHRec rate limits the client and retries are exhausted (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        url: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.retry_after = retry_after


class EDHRecRequestError(EDHRecError):
    """Raised when an API request fails with an unrecoverable non-2xx status code or network error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: Any = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.status_code = status_code
        self.body = body


class EDHRecResponseError(EDHRecError):
    """Raised when EDHRec returns invalid or unexpected response shape."""


# ---------------------------------------------------------------------------
# Slug Helpers
# ---------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_WHITESPACE_RE = re.compile(r"\s+")


def slugify_card_name(name: str) -> str:
    """Convert a card/commander name into EDHRec's URL slug format.

    Split cards (e.g. 'Fire // Ice') use only the front face, matching
    EDHRec's slug convention.
    """
    if not name:
        return ""
    front_face = name.split(" // ")[0]
    cleaned = _SLUG_STRIP_RE.sub("", front_face.lower())
    return _SLUG_WHITESPACE_RE.sub("-", cleaned.strip())


def commander_slug(commander_names: Union[str, Sequence[str]]) -> str:
    """Build the combined EDHRec commander-page slug for one or more commanders."""
    if isinstance(commander_names, str):
        names = [commander_names]
    else:
        names = list(commander_names)
    slugs = [slugify_card_name(n) for n in names if n and n.strip()]
    return "-".join(s for s in slugs if s)


def _clamp(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    if value is None:
        return None
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# EDHRecClient
# ---------------------------------------------------------------------------


class EDHRecClient:
    """Async API client for EDHRec JSON endpoints using SharedHttpClient."""

    def __init__(
        self,
        http_client: Optional[SharedHttpClient] = None,
        *,
        base_url: Optional[str] = None,
        **client_kwargs: Any,
    ) -> None:
        """Initialize EDHRecClient.

        Parameters
        ----------
        http_client : Optional[SharedHttpClient]
            Existing SharedHttpClient instance. If None, one is created from the
            'edhrec' configuration profile.
        base_url : Optional[str]
            Optional base URL override (default: https://json.edhrec.com).
        **client_kwargs : Any
            Additional keyword arguments passed to SharedHttpClient.from_config.
        """
        if http_client is not None:
            self._http_client = http_client
            self._owns_http_client = False
        else:
            kwargs = dict(client_kwargs)
            if base_url is not None:
                kwargs["base_url"] = base_url
            self._http_client = SharedHttpClient.from_config("edhrec", **kwargs)
            self._owns_http_client = True

    @property
    def http_client(self) -> SharedHttpClient:
        """Return the underlying SharedHttpClient."""
        return self._http_client

    async def __aenter__(self) -> EDHRecClient:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self.close()

    async def aclose(self) -> None:
        """Close the underlying client if owned."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def close(self) -> None:
        """Alias for aclose."""
        await self.aclose()

    async def _get_json(self, path: str) -> Dict[str, Any]:
        """Execute GET request against EDHRec path with standardized error mapping."""
        url = f"/pages/{path.lstrip('/')}"
        try:
            response = await self._http_client.get(url)
            if response.status_code == 404:
                raise EDHRecNotFoundError(
                    f"EDHRec resource not found: {path}",
                    status_code=404,
                    url=str(response.url),
                )
            if response.status_code == 429:
                raise EDHRecRateLimitError(
                    f"EDHRec rate limit exceeded: {path}",
                    url=str(response.url),
                )
            if response.status_code >= 400:
                raise EDHRecRequestError(
                    f"EDHRec request failed with status {response.status_code}: {path}",
                    status_code=response.status_code,
                    body=response.text,
                    url=str(response.url),
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise EDHRecResponseError(f"Invalid JSON returned from EDHRec for {path}") from exc

            if not isinstance(data, dict):
                raise EDHRecResponseError(
                    f"Expected JSON object from EDHRec for {path}, got {type(data).__name__}"
                )
            return data

        except (EDHRecResponseError, EDHRecNotFoundError, EDHRecRateLimitError, EDHRecRequestError):
            raise
        except RateLimitError as exc:
            raise EDHRecRateLimitError(
                f"EDHRec rate limit exceeded: {path}",
                url=getattr(exc, "url", None),
                retry_after=getattr(exc, "retry_after", None),
            ) from exc
        except ApiError as exc:
            if exc.status_code == 404:
                raise EDHRecNotFoundError(
                    f"EDHRec resource not found: {path}",
                    status_code=404,
                    url=getattr(exc, "url", None),
                ) from exc
            if exc.status_code == 429:
                raise EDHRecRateLimitError(
                    f"EDHRec rate limit exceeded: {path}",
                    url=getattr(exc, "url", None),
                ) from exc
            raise EDHRecRequestError(
                f"EDHRec API error ({exc.status_code}): {path}",
                status_code=exc.status_code,
                body=exc.body,
                url=getattr(exc, "url", None),
            ) from exc
        except (HttpTimeoutError, HttpClientError) as exc:
            raise EDHRecRequestError(
                f"EDHRec request error for {path}: {exc}",
                status_code=getattr(exc, "status_code", 0) or 0,
                url=getattr(exc, "url", None),
            ) from exc

    def _extract_json_dict(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the json_dict object from EDHRec container."""
        container = raw.get("container")
        if isinstance(container, dict):
            json_dict = container.get("json_dict")
            if isinstance(json_dict, dict):
                return json_dict
        if "cardlists" in raw or "card" in raw:
            return raw
        raise EDHRecResponseError("Missing expected container.json_dict in EDHRec response")

    async def get_commander_page(
        self, commander_names: Union[str, Sequence[str]]
    ) -> Dict[str, Any]:
        """Fetch full commander page JSON by commander name(s)."""
        slug = commander_slug(commander_names)
        if not slug:
            raise ValueError("commander_names must contain at least one non-empty name")
        return await self._get_json(f"commanders/{slug}.json")

    async def get_card_page(self, card_name: str) -> Dict[str, Any]:
        """Fetch card page JSON by card name."""
        slug = slugify_card_name(card_name)
        if not slug:
            raise ValueError("card_name cannot be empty")
        return await self._get_json(f"cards/{slug}.json")

    async def get_card_synergy(
        self,
        card_name: str,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
    ) -> Optional[CardSynergy]:
        """Retrieve synergy metrics for a card, optionally in the context of a commander."""
        if not card_name or not card_name.strip():
            return None

        clean_card = card_name.strip()

        if commander_name:
            try:
                raw = await self.get_commander_page(commander_name)
                json_dict = self._extract_json_dict(raw)
            except EDHRecNotFoundError:
                return None

            cmd_display = (
                commander_name
                if isinstance(commander_name, str)
                else " // ".join(str(c) for c in commander_name)
            )

            # Check if card is the commander itself
            card_obj = json_dict.get("card")
            if isinstance(card_obj, dict):
                cmd_card_name = card_obj.get("name") or ""
                if cmd_card_name.lower() == clean_card.lower():
                    rank = card_obj.get("rank")
                    num_decks = card_obj.get("num_decks")
                    return CardSynergy(
                        card_name=clean_card,
                        synergy_score=1.0,
                        inclusion_rate=1.0,
                        num_decks=num_decks,
                        potential_decks=num_decks,
                        commander_name=cmd_display,
                        rank=rank,
                        categories=["Commander"],
                        context=f"Commander card ({cmd_display})",
                        source="edhrec",
                        raw=card_obj,
                    )

            # Search in cardlists
            cardlists = json_dict.get("cardlists") or []
            target_slug = slugify_card_name(clean_card)
            for clist in cardlists:
                if not isinstance(clist, dict):
                    continue
                header = clist.get("header") or clist.get("tag") or "Recommendations"
                cardviews = clist.get("cardviews") or []
                for view in cardviews:
                    if not isinstance(view, dict):
                        continue
                    v_name = view.get("name") or ""
                    v_slug = view.get("sanitized") or slugify_card_name(v_name)
                    if v_name.lower() == clean_card.lower() or v_slug == target_slug:
                        raw_syn = view.get("synergy")
                        synergy_score = _clamp(raw_syn, -1.0, 1.0)
                        num_decks = view.get("num_decks")
                        potential = view.get("potential_decks")
                        inclusion_rate = None
                        if num_decks is not None and potential and float(potential) > 0:
                            inclusion_rate = _clamp(float(num_decks) / float(potential), 0.0, 1.0)

                        syn_fmt = (
                            f"{synergy_score:+.0%}" if synergy_score is not None else "neutral"
                        )
                        return CardSynergy(
                            card_name=clean_card,
                            synergy_score=synergy_score,
                            inclusion_rate=inclusion_rate,
                            num_decks=int(num_decks) if num_decks is not None else None,
                            potential_decks=int(potential) if potential is not None else None,
                            commander_name=cmd_display,
                            rank=None,
                            categories=[header],
                            context=f"EDHRec {header} ({syn_fmt} synergy with {cmd_display})",
                            source="edhrec",
                            raw=view,
                        )

            # Not found in specific commander cardlists
            return None

        # No commander context provided: fetch card page
        try:
            raw = await self.get_card_page(clean_card)
            json_dict = self._extract_json_dict(raw)
        except EDHRecNotFoundError:
            return None

        card_obj = json_dict.get("card") or {}
        rank = card_obj.get("rank")
        num_decks = card_obj.get("num_decks")
        potential = card_obj.get("potential_decks")
        inclusion_rate = None
        if num_decks is not None and potential and float(potential) > 0:
            inclusion_rate = _clamp(float(num_decks) / float(potential), 0.0, 1.0)

        return CardSynergy(
            card_name=clean_card,
            synergy_score=None,
            inclusion_rate=inclusion_rate,
            num_decks=int(num_decks) if num_decks is not None else None,
            potential_decks=int(potential) if potential is not None else None,
            commander_name=None,
            rank=rank,
            categories=["EDHRec Card"],
            context=f"EDHRec overall rank #{rank}" if rank else "EDHRec general inclusion",
            source="edhrec",
            raw=card_obj,
        )

    async def get_card_salt(
        self,
        card_name: str,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
    ) -> Optional[CardSalt]:
        """Extract EDHRec community salt score for a card."""
        if not card_name or not card_name.strip():
            return None

        clean_card = card_name.strip()

        # If commander is specified and the card is the commander, read from commander page
        if commander_name:
            try:
                raw = await self.get_commander_page(commander_name)
                json_dict = self._extract_json_dict(raw)
                card_obj = json_dict.get("card")
                if isinstance(card_obj, dict):
                    cmd_name = card_obj.get("name") or ""
                    if cmd_name.lower() == clean_card.lower() and "salt" in card_obj:
                        salt_val = card_obj.get("salt")
                        if salt_val is not None:
                            return CardSalt(
                                card_name=clean_card,
                                score=max(0.0, float(salt_val)),
                                description="EDHRec community salt score",
                                source="edhrec",
                                is_fallback=False,
                                raw=card_obj,
                            )
            except (EDHRecNotFoundError, TypeError, ValueError):
                pass

        # Query card page for salt score
        try:
            raw = await self.get_card_page(clean_card)
            json_dict = self._extract_json_dict(raw)
            card_obj = json_dict.get("card")
            if isinstance(card_obj, dict) and "salt" in card_obj:
                salt_val = card_obj.get("salt")
                if salt_val is not None:
                    return CardSalt(
                        card_name=clean_card,
                        score=max(0.0, float(salt_val)),
                        description="EDHRec community salt score",
                        source="edhrec",
                        is_fallback=False,
                        raw=card_obj,
                    )
        except (EDHRecNotFoundError, TypeError, ValueError):
            return None

        return None
