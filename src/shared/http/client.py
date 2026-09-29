"""Reusable async HTTP client integrating rate limiting, caching, and retry.

Composes:
- `RateLimiter` (token-bucket rate limiting per host/endpoint)
- `TTLCache` (in-memory response cache with TTL expiration and LRU eviction)
- `retry_request` (exponential backoff with jitter and Retry-After support)
- `httpx.AsyncClient` (underlying async HTTP engine)

Provides standard HTTP verbs (`get`, `post`, `put`, `patch`, `delete`, `head`, `options`),
support for async context manager (`async with SharedHttpClient(...) as client:`),
and pre-configured MTG API profiles (`SharedHttpClient.from_config('scryfall')`).
"""

from __future__ import annotations

import asyncio
import copy
import json as _json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Union
from urllib.parse import urlsplit

import httpx

from .cache import CachedResponse, TTLCache, make_cache_key
from .exceptions import ApiError, HttpClientError, RateLimitError, TimeoutError
from .rate_limiter import RateLimitConfig, RateLimiter
from .retry import RetryConfig, retry_request

logger = logging.getLogger(__name__)


@dataclass
class HttpClientConfig:
    """Configuration profile for :class:`SharedHttpClient`.

    Attributes
    ----------
    base_url : Optional[str]
        Base URL for all outbound requests (e.g. ``"https://api.scryfall.com"``).
    headers : Dict[str, str]
        Default headers included on every outbound request.
    rate_limit : Optional[RateLimitConfig | Dict[str, Any] | RateLimiter]
        Rate limiting configuration or shared limiter.
    cache_ttl : int
        Default cache TTL in seconds for successful GET responses.
    cache_max_size : int
        Maximum number of cached responses before LRU eviction.
    enable_cache : bool
        Whether response caching is enabled.
    retry_config : Optional[RetryConfig | Dict[str, Any]]
        Retry configuration (backoff, retryable status codes, attempts).
    timeout : float
        Default request timeout in seconds.
    """

    base_url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    rate_limit: Optional[Union[RateLimitConfig, Dict[str, Any], RateLimiter]] = None
    cache_ttl: int = 300
    cache_max_size: int = 256
    enable_cache: bool = True
    retry_config: Optional[Union[RetryConfig, Dict[str, Any]]] = None
    timeout: float = 10.0

    @classmethod
    def from_mapping(cls, data: Union[HttpClientConfig, Dict[str, Any]]) -> HttpClientConfig:
        """Create an `HttpClientConfig` instance from a dictionary or existing config."""
        if isinstance(data, HttpClientConfig):
            return data
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict or HttpClientConfig, got {type(data).__name__}")

        payload = dict(data)
        if "rate_limit" in payload and isinstance(payload["rate_limit"], dict):
            # Keep as dict or convert to RateLimitConfig if requests_per_second present
            if "requests_per_second" in payload["rate_limit"]:
                payload["rate_limit"] = RateLimitConfig.from_mapping(payload["rate_limit"])
        if "retry_config" in payload and isinstance(payload["retry_config"], dict):
            payload["retry_config"] = RetryConfig(**payload["retry_config"])

        return cls(**payload)


# Predefined MTG API client profiles
DEFAULT_USER_AGENT = "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)"

PROFILES: Dict[str, HttpClientConfig] = {
    "scryfall": HttpClientConfig(
        base_url="https://api.scryfall.com",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=10.0, burst=10.0),
        cache_ttl=300,
        cache_max_size=1024,
        retry_config=RetryConfig(max_retries=3, base_delay=0.5, max_delay=10.0),
        timeout=10.0,
    ),
    "archidekt": HttpClientConfig(
        base_url="https://archidekt.com/api",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=5.0, burst=10.0),
        cache_ttl=120,
        cache_max_size=512,
        retry_config=RetryConfig(max_retries=3, base_delay=1.0, max_delay=15.0),
        timeout=15.0,
    ),
    "edhrec": HttpClientConfig(
        base_url="https://json.edhrec.com",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=2.0, burst=2.0),
        cache_ttl=600,
        cache_max_size=512,
        retry_config=RetryConfig(max_retries=3, base_delay=0.5, max_delay=10.0),
        timeout=10.0,
    ),
    "commandersalt": HttpClientConfig(
        base_url="https://api.commandersalt.com",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=2.0, burst=2.0),
        cache_ttl=300,
        cache_max_size=256,
        retry_config=RetryConfig(max_retries=3, base_delay=1.0, max_delay=15.0),
        timeout=15.0,
    ),
    "moxfield": HttpClientConfig(
        base_url="https://api.moxfield.com/v2",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=5.0, burst=5.0),
        cache_ttl=300,
        cache_max_size=512,
        retry_config=RetryConfig(max_retries=3, base_delay=0.5, max_delay=10.0),
        timeout=10.0,
    ),
    "default": HttpClientConfig(
        base_url=None,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        },
        rate_limit=RateLimitConfig(requests_per_second=10.0, burst=10.0),
        cache_ttl=300,
        cache_max_size=256,
        retry_config=RetryConfig(max_retries=3, base_delay=1.0, max_delay=30.0),
        timeout=10.0,
    ),
}


def _build_rate_limiter(
    rate_limit: Optional[Union[RateLimitConfig, Dict[str, Any], RateLimiter]],
    rate_limiter: Optional[RateLimiter],
    clock: Any,
) -> Optional[RateLimiter]:
    """Construct or resolve a RateLimiter instance based on input parameters."""
    if rate_limiter is not None:
        return rate_limiter
    if rate_limit is None:
        return None
    if isinstance(rate_limit, RateLimiter):
        return rate_limit
    if isinstance(rate_limit, RateLimitConfig):
        return RateLimiter(default=rate_limit, clock=clock)
    if isinstance(rate_limit, dict):
        if "requests_per_second" in rate_limit:
            return RateLimiter(default=RateLimitConfig.from_mapping(rate_limit), clock=clock)
        return RateLimiter(limits=rate_limit, clock=clock)
    raise TypeError(f"Unsupported rate_limit type: {type(rate_limit).__name__}")


class SharedHttpClient:
    """Async HTTP client composing rate limiting, response caching, and retries.

    Example
    -------
    >>> async with SharedHttpClient.from_config("scryfall") as client:
    ...     resp = await client.get("/cards/random")
    ...     data = resp.json()
    ...     print(data["name"])

    Features
    --------
    - **Caching**: Successful GET responses (200-299) are cached in an async
      `TTLCache`. Repeated calls return immediately without hitting the network
      or consuming rate limit slots.
    - **Rate Limiting**: Per-host token bucket via `RateLimiter` delays
      requests smoothly to stay within external API limits.
    - **Retries**: Automatic exponential backoff + jitter for transient
      errors (503, 502, 500, 429, timeouts) via `retry_request`.
    - **Standardized Exceptions**: Raises `ApiError`, `RateLimitError`,
      `TimeoutError`, or `HttpClientError` on non-recoverable failures.
    """

    def __init__(
        self,
        config: Optional[HttpClientConfig] = None,
        *,
        base_url: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit: Optional[Union[RateLimitConfig, Dict[str, Any], RateLimiter]] = None,
        rate_limiter: Optional[RateLimiter] = None,
        cache_ttl: Optional[int] = None,
        cache_max_size: Optional[int] = None,
        cache: Optional[TTLCache] = None,
        enable_cache: Optional[bool] = None,
        retry_config: Optional[Union[RetryConfig, Dict[str, Any]]] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client: Optional[httpx.AsyncClient] = None,
        clock: Any = time.monotonic,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        **httpx_kwargs: Any,
    ) -> None:
        cfg = config or HttpClientConfig()

        effective_base_url = base_url if base_url is not None else cfg.base_url

        merged_headers: Dict[str, str] = {}
        if cfg.headers:
            merged_headers.update(cfg.headers)
        if headers:
            merged_headers.update(headers)

        effective_timeout = timeout if timeout is not None else cfg.timeout

        # Rate Limiter
        limiter_input = rate_limit if rate_limit is not None else cfg.rate_limit
        self._rate_limiter = _build_rate_limiter(limiter_input, rate_limiter, clock)

        # Cache
        caching_enabled = enable_cache if enable_cache is not None else cfg.enable_cache
        if not caching_enabled:
            self._cache = None
        elif cache is not None:
            self._cache = cache
        else:
            ttl = cache_ttl if cache_ttl is not None else cfg.cache_ttl
            max_size = cache_max_size if cache_max_size is not None else cfg.cache_max_size
            self._cache = TTLCache(max_size=max_size, default_ttl=ttl)

        # Retry config
        r_cfg = retry_config if retry_config is not None else cfg.retry_config
        if r_cfg is None:
            self._retry_config = RetryConfig()
        elif isinstance(r_cfg, RetryConfig):
            self._retry_config = r_cfg
        elif isinstance(r_cfg, dict):
            self._retry_config = RetryConfig(**r_cfg)
        else:
            raise TypeError(f"Unsupported retry_config type: {type(r_cfg).__name__}")

        self._sleep = sleep or asyncio.sleep

        # HTTPX AsyncClient
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=effective_base_url or "",
                headers=merged_headers,
                timeout=effective_timeout,
                **httpx_kwargs,
            )
            self._owns_client = True

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying `httpx.AsyncClient`."""
        return self._client

    @property
    def cache(self) -> Optional[TTLCache]:
        """The response cache, or None if disabled."""
        return self._cache

    @property
    def rate_limiter(self) -> Optional[RateLimiter]:
        """The rate limiter, or None if unconstrained."""
        return self._rate_limiter

    @property
    def retry_config(self) -> RetryConfig:
        """The client's default retry configuration."""
        return self._retry_config

    @property
    def base_url(self) -> httpx.URL:
        """The configured base URL."""
        return self._client.base_url

    @property
    def is_closed(self) -> bool:
        """True if the underlying client is closed."""
        return self._client.is_closed

    @classmethod
    def register_profile(
        cls,
        name: str,
        config: Union[HttpClientConfig, Dict[str, Any]],
    ) -> None:
        """Register or update a named client configuration profile."""
        PROFILES[name] = HttpClientConfig.from_mapping(config)

    @classmethod
    def get_profile(cls, name: str) -> HttpClientConfig:
        """Retrieve a registered client configuration profile by name."""
        profile = PROFILES.get(name)
        if profile is None:
            available = ", ".join(repr(k) for k in PROFILES.keys())
            raise ValueError(f"Unknown HTTP client profile: {name!r}. Available: {available}")
        return copy.deepcopy(profile)

    @classmethod
    def from_config(
        cls,
        profile_or_config: Union[str, HttpClientConfig, Dict[str, Any]],
        **overrides: Any,
    ) -> SharedHttpClient:
        """Factory method to construct a `SharedHttpClient` from a profile name or config.

        Parameters
        ----------
        profile_or_config : str | HttpClientConfig | dict
            Profile name (e.g. ``'scryfall'``, ``'archidekt'``, ``'edhrec'``),
            an :class:`HttpClientConfig` object, or a configuration dictionary.
        **overrides : Any
            Keyword arguments overriding profile settings or passed to `SharedHttpClient`.
        """
        if isinstance(profile_or_config, str):
            base_cfg = cls.get_profile(profile_or_config)
        elif isinstance(profile_or_config, (HttpClientConfig, dict)):
            base_cfg = HttpClientConfig.from_mapping(profile_or_config)
        else:
            raise TypeError(
                f"profile_or_config must be str, HttpClientConfig, or dict, "
                f"got {type(profile_or_config).__name__}"
            )

        # Separate overrides into HttpClientConfig fields vs SharedHttpClient constructor args
        cfg_fields = {
            "base_url",
            "headers",
            "rate_limit",
            "cache_ttl",
            "cache_max_size",
            "enable_cache",
            "retry_config",
            "timeout",
        }

        cfg_dict = {
            "base_url": base_cfg.base_url,
            "headers": dict(base_cfg.headers),
            "rate_limit": base_cfg.rate_limit,
            "cache_ttl": base_cfg.cache_ttl,
            "cache_max_size": base_cfg.cache_max_size,
            "enable_cache": base_cfg.enable_cache,
            "retry_config": base_cfg.retry_config,
            "timeout": base_cfg.timeout,
        }

        client_kwargs: Dict[str, Any] = {}
        for key, value in overrides.items():
            if key in cfg_fields:
                if key == "headers" and isinstance(value, Mapping):
                    cfg_dict["headers"].update(value)
                else:
                    cfg_dict[key] = value
            else:
                client_kwargs[key] = value

        final_cfg = HttpClientConfig.from_mapping(cfg_dict)
        return cls(config=final_cfg, **client_kwargs)

    async def __aenter__(self) -> SharedHttpClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def aclose(self) -> None:
        """Close the underlying HTTP client session."""
        await self._client.aclose()

    async def close(self) -> None:
        """Alias for `aclose`."""
        await self.aclose()

    def _resolve_url_and_host(
        self,
        method: str,
        url: Union[str, httpx.URL],
        params: Optional[Mapping[str, Any]] = None,
    ) -> tuple[str, str]:
        """Resolve a full target URL and extract the target hostname."""
        dummy_request = self._client.build_request(method, url, params=params)
        target_url = str(dummy_request.url)
        split = urlsplit(target_url)
        host = split.netloc or split.path
        return target_url, host

    async def request(
        self,
        method: str,
        url: Union[str, httpx.URL],
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        content: Optional[Any] = None,
        data: Optional[Any] = None,
        files: Optional[Any] = None,
        json: Optional[Any] = None,
        cookies: Optional[Any] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        use_cache: bool = True,
        cache_ttl: Optional[int] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute an HTTP request with caching, rate limiting, and retry handling.

        Execution pipeline:
        1. Check cache (for GET requests) -> return response immediately if hit.
        2. Acquire a rate limiter token for the target host/key.
        3. Execute the request using retry logic with exponential backoff + jitter.
        4. Cache successful GET responses (200-299).
        5. Return the response or raise standardized exception.
        """
        normalized_method = method.upper()
        target_url, host = self._resolve_url_and_host(normalized_method, url, params=params)

        # 1. Check cache (GET only)
        cache_key = None
        if self._cache is not None and use_cache and normalized_method == "GET":
            cache_key = make_cache_key(normalized_method, target_url)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for %s %s", normalized_method, target_url)
                cached_body = cached.body
                if isinstance(cached_body, (bytes, bytearray)):
                    body_bytes = bytes(cached_body)
                elif isinstance(cached_body, str):
                    body_bytes = cached_body.encode("utf-8")
                elif cached_body is None:
                    body_bytes = b""
                else:
                    body_bytes = _json.dumps(cached_body).encode("utf-8")

                cached_response = httpx.Response(
                    status_code=cached.status_code,
                    headers=dict(cached.headers),
                    content=body_bytes,
                    request=httpx.Request(normalized_method, target_url),
                )
                setattr(cached_response, "from_cache", True)
                setattr(cached_response, "cached", True)
                setattr(cached_response, "body", cached.body)
                return cached_response

        # 2. Acquire rate limit slot
        if self._rate_limiter is not None:
            bucket_key = rate_limit_key if rate_limit_key is not None else host
            await self._rate_limiter.acquire(bucket_key)

        # 3. Execute request with retry logic
        request_kwargs: Dict[str, Any] = {
            "params": params,
            "headers": headers,
            "content": content,
            "data": data,
            "files": files,
            "json": json,
            "cookies": cookies,
        }
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        request_kwargs.update(kwargs)

        async def _do_request() -> httpx.Response:
            try:
                response = await self._client.request(
                    method=normalized_method,
                    url=url,
                    **request_kwargs,
                )
                # Ensure the full content is buffered in memory
                await response.aread()
                return response
            except httpx.TimeoutException as exc:
                # Map HTTPX timeout to asyncio.TimeoutError so retry_request recognizes it
                raise asyncio.TimeoutError(str(exc)) from exc

        effective_retry_cfg = retry_config or self._retry_config

        try:
            response = await retry_request(
                _do_request,
                config=effective_retry_cfg,
                url=target_url,
                sleep=self._sleep,
            )
        except (ApiError, RateLimitError) as exc:
            if not raise_for_status:
                status_code = getattr(exc, "status_code", 429 if isinstance(exc, RateLimitError) else 500)
                body = getattr(exc, "body", b"")
                content_bytes = (
                    body.encode("utf-8")
                    if isinstance(body, str)
                    else (body if isinstance(body, bytes) else b"")
                )
                err_resp = httpx.Response(
                    status_code=status_code,
                    content=content_bytes,
                    request=httpx.Request(normalized_method, target_url),
                )
                setattr(err_resp, "from_cache", False)
                setattr(err_resp, "cached", False)
                return err_resp
            raise

        setattr(response, "from_cache", False)
        setattr(response, "cached", False)
        setattr(response, "body", response.content)

        # 4. Cache successful GET responses
        if (
            self._cache is not None
            and use_cache
            and normalized_method == "GET"
            and 200 <= response.status_code < 300
        ):
            if cache_key is None:
                cache_key = make_cache_key(normalized_method, target_url)
            cached_entry = CachedResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=response.content,
            )
            await self._cache.set(cache_key, cached_entry, ttl=cache_ttl)

        return response

    async def get(
        self,
        url: Union[str, httpx.URL],
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        use_cache: bool = True,
        cache_ttl: Optional[int] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a GET request."""
        return await self.request(
            "GET",
            url,
            params=params,
            headers=headers,
            use_cache=use_cache,
            cache_ttl=cache_ttl,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def post(
        self,
        url: Union[str, httpx.URL],
        *,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a POST request."""
        return await self.request(
            "POST",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def put(
        self,
        url: Union[str, httpx.URL],
        *,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a PUT request."""
        return await self.request(
            "PUT",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def patch(
        self,
        url: Union[str, httpx.URL],
        *,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a PATCH request."""
        return await self.request(
            "PATCH",
            url,
            data=data,
            json=json,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def delete(
        self,
        url: Union[str, httpx.URL],
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a DELETE request."""
        return await self.request(
            "DELETE",
            url,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def head(
        self,
        url: Union[str, httpx.URL],
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a HEAD request."""
        return await self.request(
            "HEAD",
            url,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )

    async def options(
        self,
        url: Union[str, httpx.URL],
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        rate_limit_key: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an OPTIONS request."""
        return await self.request(
            "OPTIONS",
            url,
            params=params,
            headers=headers,
            rate_limit_key=rate_limit_key,
            retry_config=retry_config,
            raise_for_status=raise_for_status,
            **kwargs,
        )


def create_http_client(
    profile_or_config: Union[str, HttpClientConfig, Dict[str, Any]] = "default",
    **overrides: Any,
) -> SharedHttpClient:
    """Factory function to instantiate a configured `SharedHttpClient`.

    Parameters
    ----------
    profile_or_config : str | HttpClientConfig | dict
        Profile name (e.g. 'scryfall', 'archidekt') or custom config.
    **overrides : Any
        Keyword arguments overriding profile settings.
    """
    return SharedHttpClient.from_config(profile_or_config, **overrides)
