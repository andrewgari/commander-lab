"""Shared HTTP utilities (rate limiting, caching, retry, client)."""

from .cache import CachedResponse, TTLCache, make_cache_key
from .client import HttpClientConfig, PROFILES, SharedHttpClient, create_http_client
from .exceptions import ApiError, HttpClientError, RateLimitError, TimeoutError
from .rate_limiter import RateLimitConfig, RateLimiter
from .retry import RetryConfig, retry_request

__all__ = [
    "ApiError",
    "CachedResponse",
    "HttpClientConfig",
    "HttpClientError",
    "PROFILES",
    "RateLimitConfig",
    "RateLimiter",
    "RateLimitError",
    "RetryConfig",
    "SharedHttpClient",
    "TTLCache",
    "TimeoutError",
    "create_http_client",
    "make_cache_key",
    "retry_request",
]
