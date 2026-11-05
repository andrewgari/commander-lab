"""Async retry wrapper for HTTP requests with exponential backoff + jitter.

The wrapper is HTTP-client-agnostic: it accepts an async callable that
performs a single request attempt and returns an object exposing
``status_code`` and (optionally) ``headers`` — this matches both
``httpx.Response`` and any lightweight stand-in used in tests.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Sequence, TypeVar

from .exceptions import ApiError, HttpClientError, RateLimitError
from .exceptions import TimeoutError as HttpTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass
class RetryConfig:
    """Configuration for :func:`retry_request`."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_status_codes: frozenset[int] = field(
        default_factory=lambda: DEFAULT_RETRYABLE_STATUS_CODES
    )
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.max_delay < 0:
            raise ValueError("max_delay must be >= 0")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")


class RetryExhaustedError(HttpClientError):
    """Raised when all retry attempts have been exhausted."""


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """Exponential backoff with full jitter, capped at max_delay.

    ``attempt`` is 1-indexed (the number of the attempt that just failed).
    """
    raw_delay = config.base_delay * (2 ** (attempt - 1))
    raw_delay = min(raw_delay, config.max_delay)
    if config.jitter:
        raw_delay = random.uniform(0, raw_delay)
    return raw_delay


def _parse_retry_after(headers: Optional[dict]) -> Optional[float]:
    """Parse the Retry-After header (seconds form only; HTTP-date not
    supported, matching common MTG API providers which use seconds).
    """
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def retry_request(
    request_fn: Callable[[], Awaitable[T]],
    *,
    config: Optional[RetryConfig] = None,
    url: Optional[str] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Execute ``request_fn`` with retry + exponential backoff + jitter.

    ``request_fn`` is called with no arguments and must return an object
    with a ``status_code`` attribute (and optionally ``headers`` / a
    ``.text``/``.json()`` body for error reporting). Raises
    :class:`~exceptions.ApiError` (or subclasses) if the final attempt is
    a non-retryable or exhausted failure. Network-level exceptions raised
    by ``request_fn`` itself (timeouts, connection errors) are treated as
    retryable and, on exhaustion, re-raised as
    :class:`~exceptions.TimeoutError` / :class:`~exceptions.HttpClientError`.
    """
    cfg = config or RetryConfig()
    attempt = 0
    last_exception: Optional[BaseException] = None

    while attempt <= cfg.max_retries:
        attempt += 1
        try:
            response = await request_fn()
        except asyncio.TimeoutError as exc:
            last_exception = exc
            if attempt > cfg.max_retries:
                logger.warning(
                    "Retry attempt %d/%d exhausted after timeout (url=%s)",
                    attempt,
                    cfg.max_retries + 1,
                    url,
                )
                raise HttpTimeoutError(
                    f"Request timed out after {attempt} attempts", url=url
                ) from exc
            delay = _compute_delay(attempt, cfg)
            logger.info(
                "Retry attempt %d/%d after timeout: sleeping %.2fs (url=%s)",
                attempt,
                cfg.max_retries + 1,
                delay,
                url,
            )
            await sleep(delay)
            continue
        except Exception as exc:  # network/connection errors, etc.
            last_exception = exc
            if attempt > cfg.max_retries:
                logger.warning(
                    "Retry attempt %d/%d exhausted after error: %s (url=%s)",
                    attempt,
                    cfg.max_retries + 1,
                    exc,
                    url,
                )
                raise HttpClientError(
                    f"Request failed after {attempt} attempts: {exc}", url=url
                ) from exc
            delay = _compute_delay(attempt, cfg)
            logger.info(
                "Retry attempt %d/%d after error %s: sleeping %.2fs (url=%s)",
                attempt,
                cfg.max_retries + 1,
                exc,
                delay,
                url,
            )
            await sleep(delay)
            continue

        status_code = getattr(response, "status_code", None)

        if status_code is None or status_code < 400:
            return response

        if status_code not in cfg.retryable_status_codes:
            logger.warning(
                "Non-retryable status %s received on attempt %d (url=%s)",
                status_code,
                attempt,
                url,
            )
            body = _extract_body(response)
            raise ApiError(
                f"Request failed with non-retryable status {status_code}",
                status_code=status_code,
                body=body,
                url=url,
            )

        headers = getattr(response, "headers", None)
        retry_after = _parse_retry_after(headers) if status_code == 429 else None

        if attempt > cfg.max_retries:
            logger.warning(
                "Retry attempt %d/%d exhausted, last status %s (url=%s)",
                attempt,
                cfg.max_retries + 1,
                status_code,
                url,
            )
            body = _extract_body(response)
            if status_code == 429:
                raise RateLimitError(
                    f"Rate limited after {attempt} attempts",
                    url=url,
                    retry_after=retry_after,
                )
            raise ApiError(
                f"Request failed with status {status_code} after {attempt} attempts",
                status_code=status_code,
                body=body,
                url=url,
            )

        delay = retry_after if retry_after is not None else _compute_delay(attempt, cfg)
        delay = min(delay, cfg.max_delay) if retry_after is None else delay
        logger.info(
            "Retry attempt %d/%d: status=%s delay=%.2fs (url=%s)",
            attempt,
            cfg.max_retries + 1,
            status_code,
            delay,
            url,
        )
        await sleep(delay)

    # Should not be reachable, but keep a safety net.
    if last_exception is not None:
        raise HttpClientError(str(last_exception), url=url) from last_exception
    raise HttpClientError("Retry loop exited unexpectedly", url=url)


def _extract_body(response) -> object:
    for attr in ("text", "content"):
        value = getattr(response, attr, None)
        if value is not None:
            return value
    try:
        return response.json()
    except Exception:
        return None
