# Shared HTTP Client (`src.shared.http`)

Reusable async HTTP client infrastructure for third-party MTG API integrations (Scryfall, Archidekt, EDHRec, Commander Salt, Moxfield), providing:

- **Per-Host Token Bucket Rate Limiting** (`rate_limiter.py`): Non-blocking token-bucket rate limiter ensuring clients do not exceed vendor rate limits.
- **In-Memory TTL Response Caching** (`cache.py`): Thread/asyncio-safe response caching with per-entry TTL and LRU eviction to prevent redundant outbound queries.
- **Retries with Exponential Backoff & Jitter** (`retry.py`): Automatic retries on transient errors (503, 502, 500, 429, timeouts) respecting server `Retry-After` headers.
- **Unified Client Wrapper** (`client.py`): Composes all three modules into `SharedHttpClient`, wrapping `httpx.AsyncClient`.
- **Standardized Exception Hierarchy** (`exceptions.py`): `HttpClientError`, `RateLimitError`, `TimeoutError`, and `ApiError`.

---

## Quickstart

### Using Pre-configured MTG Profiles

The easiest way to instantiate a client for an MTG service is `SharedHttpClient.from_config('<profile_name>')`:

```python
import asyncio
from src.shared.http import SharedHttpClient

async def main():
    # Scryfall profile (10 req/s, 5 min TTL cache, retry with backoff)
    async with SharedHttpClient.from_config("scryfall") as client:
        resp = await client.get("/cards/named", params={"exact": "Sol Ring"})
        card = resp.json()
        print(f"Loaded: {card['name']} (from cache: {getattr(resp, 'from_cache', False)})")

        # Second identical request hits in-memory cache immediately
        cached_resp = await client.get("/cards/named", params={"exact": "Sol Ring"})
        print(f"Cached hit: {getattr(cached_resp, 'from_cache', False)}")

asyncio.run(main())
```

### Pre-configured Profiles

| Profile Name | Base URL | Rate Limit | Cache TTL | Retries |
|---|---|---|---|---|
| `scryfall` | `https://api.scryfall.com` | 10 req/s (burst 10) | 300s (5m) | 3 retries (0.5s base, 10s max) |
| `archidekt` | `https://archidekt.com/api` | 5 req/s (burst 10) | 120s (2m) | 3 retries (1.0s base, 15s max) |
| `edhrec` | `https://json.edhrec.com` | 2 req/s (burst 2) | 600s (10m) | 3 retries (0.5s base, 10s max) |
| `commandersalt` | `https://api.commandersalt.com` | 2 req/s (burst 2) | 300s (5m) | 3 retries (1.0s base, 15s max) |
| `moxfield` | `https://api.moxfield.com/v2` | 5 req/s (burst 5) | 300s (5m) | 3 retries (0.5s base, 10s max) |
| `default` | None | 10 req/s (burst 10) | 300s (5m) | 3 retries (1.0s base, 30s max) |

---

## Custom Configuration

### Customizing Profiles

You can override any profile option when creating the client:

```python
from src.shared.http import SharedHttpClient, RateLimitConfig, RetryConfig

client = SharedHttpClient.from_config(
    "scryfall",
    headers={"Authorization": "Bearer secret_token"},
    cache_ttl=600,
    rate_limit=RateLimitConfig(requests_per_second=5.0, burst=5.0),
    retry_config=RetryConfig(max_retries=5, base_delay=0.2),
)
```

### Manual Configuration

You can also configure `SharedHttpClient` directly with `HttpClientConfig` or keyword arguments:

```python
from src.shared.http import SharedHttpClient, HttpClientConfig, RateLimitConfig

config = HttpClientConfig(
    base_url="https://api.custom-service.com",
    rate_limit=RateLimitConfig(requests_per_second=4.0),
    cache_ttl=180,
    cache_max_size=500,
    timeout=15.0,
)

async with SharedHttpClient(config) as client:
    resp = await client.get("/status")
```

### Registering Custom Profiles

New profiles can be registered globally at runtime:

```python
from src.shared.http import SharedHttpClient, HttpClientConfig

SharedHttpClient.register_profile(
    "custom_api",
    HttpClientConfig(
        base_url="https://api.myservice.org",
        rate_limit={"requests_per_second": 3.0, "burst": 6.0},
        cache_ttl=60,
    ),
)

async with SharedHttpClient.from_config("custom_api") as client:
    ...
```

---

## Request Lifecycle

For every outbound call:
1. **Cache Check**: If method is `GET` and caching is enabled, queries `TTLCache`. On a cache hit, returns the cached response immediately (`resp.from_cache == True`), skipping rate limiting and network traffic.
2. **Rate Limiting**: Acquires an execution slot from `RateLimiter` for the destination host (or per-endpoint key). Excess calls pause asynchronously until tokens refill.
3. **Execution & Retry**: Dispatches request via `httpx.AsyncClient` with `retry_request`. On retryable errors (503, 502, 500, 429, timeouts), performs exponential backoff with jitter and respects `Retry-After`.
4. **Cache Storage**: Successful GET responses (200-299) are stored in `TTLCache`.
5. **Response Delivery**: Returns `httpx.Response` or raises typed `HttpClientError` subclass.

---

## Error Handling

Standardized exceptions raised on non-recoverable failures:

```python
from src.shared.http import (
    SharedHttpClient,
    HttpClientError,
    RateLimitError,
    TimeoutError,
    ApiError,
)

async with SharedHttpClient.from_config("scryfall") as client:
    try:
        resp = await client.get("/cards/named", params={"exact": "NonexistentCardName"})
    except RateLimitError as e:
        print(f"Rate limited (retry after {e.retry_after}s): {e}")
    except TimeoutError as e:
        print(f"Request timed out: {e}")
    except ApiError as e:
        print(f"API Error HTTP {e.status_code}: {e.message}, body: {e.body}")
    except HttpClientError as e:
        print(f"General HTTP client error: {e}")
```
