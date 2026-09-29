"""Commander Salt API integration client using SharedHttpClient.

Provides:
- `CommanderSaltClient`: Async client for querying Commander Salt's endpoints.
  - `get_card_salt`: Fetch salt rating and rank for a card.
  - `get_deck`: Fetch deck payload with card salt breakdowns.
  - `get_deck_cards_salt`: Fetch per-card salt ratings from a deck payload.
- Standardized exceptions: `CommanderSaltError`, `CommanderSaltNotFoundError`,
  `CommanderSaltRateLimitError`, `CommanderSaltRequestError`, `CommanderSaltResponseError`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Union

from src.integrations.models import CardSalt
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


class CommanderSaltError(HttpClientError):
    """Base exception for all Commander Salt client errors."""


class CommanderSaltNotFoundError(CommanderSaltError):
    """Raised when a deck or card is not found on Commander Salt (HTTP 404)."""

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


class CommanderSaltRateLimitError(CommanderSaltError):
    """Raised when Commander Salt rate limits the client and retries are exhausted (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        url: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.retry_after = retry_after


class CommanderSaltRequestError(CommanderSaltError):
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


class CommanderSaltResponseError(CommanderSaltError):
    """Raised when Commander Salt returns invalid or unexpected response shape."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_WHITESPACE_RE = re.compile(r"\s+")


def slugify_card_name(name: str) -> str:
    """Convert card name to normalized slug."""
    if not name:
        return ""
    front_face = name.split(" // ")[0]
    cleaned = _SLUG_STRIP_RE.sub("", front_face.lower())
    return _SLUG_WHITESPACE_RE.sub("-", cleaned.strip())


# ---------------------------------------------------------------------------
# CommanderSaltClient
# ---------------------------------------------------------------------------


class CommanderSaltClient:
    """Async API client for Commander Salt using SharedHttpClient."""

    def __init__(
        self,
        http_client: Optional[SharedHttpClient] = None,
        *,
        base_url: Optional[str] = None,
        **client_kwargs: Any,
    ) -> None:
        """Initialize CommanderSaltClient.

        Parameters
        ----------
        http_client : Optional[SharedHttpClient]
            Existing SharedHttpClient instance. If None, one is created from the
            'commandersalt' configuration profile.
        base_url : Optional[str]
            Optional base URL override (default: https://api.commandersalt.com).
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
            self._http_client = SharedHttpClient.from_config("commandersalt", **kwargs)
            self._owns_http_client = True

    @property
    def http_client(self) -> SharedHttpClient:
        """Return the underlying SharedHttpClient."""
        return self._http_client

    async def __aenter__(self) -> CommanderSaltClient:
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

    async def _request_json(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute GET request with standardized error mapping."""
        url = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        try:
            response = await self._http_client.get(url, params=params)
            if response.status_code == 404:
                raise CommanderSaltNotFoundError(
                    f"Commander Salt resource not found: {endpoint}",
                    status_code=404,
                    url=str(response.url),
                )
            if response.status_code == 429:
                raise CommanderSaltRateLimitError(
                    f"Commander Salt rate limit exceeded: {endpoint}",
                    url=str(response.url),
                )
            if response.status_code >= 400:
                raise CommanderSaltRequestError(
                    f"Commander Salt request failed with status {response.status_code}: {endpoint}",
                    status_code=response.status_code,
                    body=response.text,
                    url=str(response.url),
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise CommanderSaltResponseError(
                    f"Invalid JSON returned from Commander Salt for {endpoint}"
                ) from exc

            if not isinstance(data, dict):
                raise CommanderSaltResponseError(
                    f"Expected JSON object from Commander Salt for {endpoint}, got {type(data).__name__}"
                )
            return data

        except (
            CommanderSaltResponseError,
            CommanderSaltNotFoundError,
            CommanderSaltRateLimitError,
            CommanderSaltRequestError,
        ):
            raise
        except RateLimitError as exc:
            raise CommanderSaltRateLimitError(
                f"Commander Salt rate limit exceeded: {endpoint}",
                url=getattr(exc, "url", None),
                retry_after=getattr(exc, "retry_after", None),
            ) from exc
        except ApiError as exc:
            if exc.status_code == 404:
                raise CommanderSaltNotFoundError(
                    f"Commander Salt resource not found: {endpoint}",
                    status_code=404,
                    url=getattr(exc, "url", None),
                ) from exc
            if exc.status_code == 429:
                raise CommanderSaltRateLimitError(
                    f"Commander Salt rate limit exceeded: {endpoint}",
                    url=getattr(exc, "url", None),
                ) from exc
            raise CommanderSaltRequestError(
                f"Commander Salt API error ({exc.status_code}): {endpoint}",
                status_code=exc.status_code,
                body=exc.body,
                url=getattr(exc, "url", None),
            ) from exc
        except (HttpTimeoutError, HttpClientError) as exc:
            raise CommanderSaltRequestError(
                f"Commander Salt request error for {endpoint}: {exc}",
                status_code=getattr(exc, "status_code", 0) or 0,
                url=getattr(exc, "url", None),
            ) from exc

    async def get_deck(self, source_url: str) -> Dict[str, Any]:
        """Fetch raw deck analytics payload from Commander Salt."""
        if not source_url or not source_url.strip():
            raise ValueError("source_url cannot be empty")
        return await self._request_json("/decks", params={"id": source_url.strip()})

    async def get_card_salt(self, card_name: str) -> Optional[CardSalt]:
        """Fetch salt rating for an individual card from Commander Salt.

        Queries the card salt endpoint `/cards/{slug}` (or `/cards?name=...`),
        and returns a standardized `CardSalt` model.
        """
        if not card_name or not card_name.strip():
            return None

        clean_name = card_name.strip()
        slug = slugify_card_name(clean_name)

        try:
            # Query card-level endpoint
            payload = await self._request_json(f"/cards/{slug}")
        except CommanderSaltNotFoundError:
            # Try query parameter fallback
            try:
                payload = await self._request_json("/cards", params={"name": clean_name})
            except CommanderSaltNotFoundError:
                return None

        # Parse salt score from response payload
        raw_salt = (
            payload.get("salt")
            or payload.get("saltRating")
            or payload.get("score")
            or payload.get("salt_score")
        )
        if raw_salt is None:
            return None

        try:
            score = max(0.0, float(raw_salt))
        except (ValueError, TypeError):
            return None

        rank_val = payload.get("rank")
        rank: Optional[int] = None
        if rank_val is not None:
            try:
                rank = int(rank_val)
            except (ValueError, TypeError):
                rank = None

        salt_sum_val = payload.get("salt_sum")
        salt_sum: Optional[float] = None
        if salt_sum_val is not None:
            try:
                salt_sum = float(salt_sum_val)
            except (ValueError, TypeError):
                salt_sum = None

        return CardSalt(
            card_name=clean_name,
            score=score,
            rank=rank,
            salt_sum=salt_sum,
            description=payload.get("description") or "Commander Salt card salt rating",
            source="commandersalt",
            is_fallback=False,
            raw=payload,
        )

    async def get_deck_cards_salt(self, source_url: str) -> Dict[str, CardSalt]:
        """Extract per-card salt ratings from a Commander Salt deck payload."""
        deck_data = await self.get_deck(source_url)
        cards_dict = deck_data.get("cards") or {}
        if not isinstance(cards_dict, dict):
            return {}

        results: Dict[str, CardSalt] = {}
        for card_key, card_entry in cards_dict.items():
            if not isinstance(card_entry, dict):
                continue
            name = card_entry.get("name") or card_key.replace("_", " ").title()
            raw_salt = card_entry.get("salt")
            if raw_salt is not None:
                try:
                    score = max(0.0, float(raw_salt))
                    results[name.lower()] = CardSalt(
                        card_name=name,
                        score=score,
                        description="Commander Salt deck card salt score",
                        source="commandersalt",
                        is_fallback=False,
                        raw=card_entry,
                    )
                except (ValueError, TypeError):
                    continue

        return results
