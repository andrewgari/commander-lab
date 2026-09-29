"""Standardized exception hierarchy for HTTP client operations.

All third-party MTG API integrations (Archidekt, Scryfall, Moxfield, ...)
should raise (or have wrapped for them) one of these exception types so
callers get a consistent error surface regardless of which API produced
the failure.
"""
from __future__ import annotations

from typing import Any, Optional


class HttpClientError(Exception):
    """Base class for all HTTP client errors."""

    def __init__(self, message: str, *, url: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.url = url

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.url:
            return f"{self.message} (url={self.url})"
        return self.message


class TimeoutError(HttpClientError):  # noqa: A001 - intentional shadow, matches spec
    """Raised when a request times out (connect or read)."""


class RateLimitError(HttpClientError):
    """Raised when a request is rejected due to rate limiting (HTTP 429).

    Carries ``retry_after`` (seconds) parsed from the ``Retry-After``
    header when the server provided one.
    """

    def __init__(
        self,
        message: str,
        *,
        url: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.retry_after = retry_after


class ApiError(HttpClientError):
    """Raised for any other non-2xx response that exhausted retries
    (or was not retryable in the first place).
    """

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

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = f"{self.message} (status={self.status_code})"
        if self.url:
            base += f" (url={self.url})"
        return base
