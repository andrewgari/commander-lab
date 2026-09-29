"""Unit tests for src.shared.http.retry using mocked HTTP responses."""
import asyncio
import os
import sys
import unittest
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.shared.http.exceptions import ApiError, HttpClientError, RateLimitError
from src.shared.http.exceptions import TimeoutError as HttpTimeoutError
from src.shared.http.retry import RetryConfig, retry_request


@dataclass
class FakeResponse:
    status_code: int
    headers: dict = field(default_factory=dict)
    text: str = ""


def run(coro):
    return asyncio.run(coro)


async def _noop_sleep(_delay: float) -> None:
    """Sleep stand-in that returns instantly, used to keep tests fast."""
    return None


class RetryRequestTests(unittest.TestCase):
    def test_successful_retry_after_transient_failure(self):
        """Fails once with a retryable 503, then succeeds on the 2nd try."""
        responses = [FakeResponse(503), FakeResponse(200, text="ok")]
        calls = []

        async def request_fn():
            calls.append(1)
            return responses.pop(0)

        result = run(
            retry_request(
                request_fn,
                config=RetryConfig(max_retries=3, base_delay=0.01),
                url="https://example.test/x",
                sleep=_noop_sleep,
            )
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(calls), 2)

    def test_max_retries_exhausted_raises_exception(self):
        """Always returns 500; after max_retries+1 attempts it raises ApiError."""
        call_count = {"n": 0}

        async def request_fn():
            call_count["n"] += 1
            return FakeResponse(500, text="boom")

        with self.assertRaises(ApiError) as ctx:
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(max_retries=2, base_delay=0.01),
                    url="https://example.test/x",
                    sleep=_noop_sleep,
                )
            )
        self.assertEqual(ctx.exception.status_code, 500)
        # initial attempt + 2 retries = 3 calls
        self.assertEqual(call_count["n"], 3)

    def test_retry_after_header_respected(self):
        """A 429 with Retry-After should sleep for that many seconds, not
        the computed exponential backoff delay."""
        responses = [
            FakeResponse(429, headers={"Retry-After": "5"}),
            FakeResponse(200, text="ok"),
        ]
        sleep_calls = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        async def request_fn():
            return responses.pop(0)

        result = run(
            retry_request(
                request_fn,
                config=RetryConfig(max_retries=3, base_delay=1.0),
                url="https://example.test/x",
                sleep=fake_sleep,
            )
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sleep_calls, [5.0])

    def test_retry_after_exhausted_raises_rate_limit_error(self):
        async def request_fn():
            return FakeResponse(429, headers={"Retry-After": "1"})

        with self.assertRaises(RateLimitError) as ctx:
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(max_retries=1, base_delay=0.01),
                    url="https://example.test/x",
                    sleep=_noop_sleep,
                )
            )
        self.assertEqual(ctx.exception.retry_after, 1.0)

    def test_non_retryable_status_raises_immediately(self):
        """A 404 is not in the default retryable set, so it should raise
        on the very first attempt without retrying."""
        call_count = {"n": 0}

        async def request_fn():
            call_count["n"] += 1
            return FakeResponse(404, text="not found")

        with self.assertRaises(ApiError) as ctx:
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(max_retries=3, base_delay=0.01),
                    url="https://example.test/x",
                    sleep=_noop_sleep,
                )
            )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(call_count["n"], 1)

    def test_successful_first_attempt_no_retries(self):
        async def request_fn():
            return FakeResponse(200, text="ok")

        result = run(
            retry_request(
                request_fn,
                config=RetryConfig(max_retries=3, base_delay=0.01),
                sleep=_noop_sleep,
            )
        )
        self.assertEqual(result.status_code, 200)

    def test_custom_retryable_status_codes(self):
        """A status not in the custom retryable set raises immediately."""

        async def request_fn():
            return FakeResponse(418)

        with self.assertRaises(ApiError):
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(
                        max_retries=3,
                        base_delay=0.01,
                        retryable_status_codes=frozenset({500}),
                    ),
                    sleep=_noop_sleep,
                )
            )

    def test_network_exception_retried_then_raises_http_client_error(self):
        async def request_fn():
            raise ConnectionError("connection reset")

        with self.assertRaises(HttpClientError):
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(max_retries=1, base_delay=0.01),
                    sleep=_noop_sleep,
                )
            )

    def test_timeout_exception_retried_then_raises_timeout_error(self):
        async def request_fn():
            raise asyncio.TimeoutError()

        with self.assertRaises(HttpTimeoutError):
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(max_retries=1, base_delay=0.01),
                    sleep=_noop_sleep,
                )
            )

    def test_backoff_delay_grows_and_is_capped(self):
        """With jitter disabled, delays should be base*2^(n-1) capped at max_delay."""
        call_count = {"n": 0}
        sleep_calls = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        async def request_fn():
            call_count["n"] += 1
            return FakeResponse(503)

        with self.assertRaises(ApiError):
            run(
                retry_request(
                    request_fn,
                    config=RetryConfig(
                        max_retries=4,
                        base_delay=1.0,
                        max_delay=3.0,
                        jitter=False,
                    ),
                    sleep=fake_sleep,
                )
            )
        # attempts: 1(delay 1), 2(delay 2), 3(delay 3 capped), 4(delay 3 capped), 5 raises
        self.assertEqual(sleep_calls, [1.0, 2.0, 3.0, 3.0])


class RetryConfigValidationTests(unittest.TestCase):
    def test_negative_max_retries_rejected(self):
        with self.assertRaises(ValueError):
            RetryConfig(max_retries=-1)

    def test_negative_base_delay_rejected(self):
        with self.assertRaises(ValueError):
            RetryConfig(base_delay=-1.0)

    def test_negative_max_delay_rejected(self):
        with self.assertRaises(ValueError):
            RetryConfig(max_delay=-1.0)

    def test_max_delay_below_base_delay_rejected(self):
        with self.assertRaises(ValueError):
            RetryConfig(base_delay=5.0, max_delay=1.0)


class ExceptionHierarchyTests(unittest.TestCase):
    def test_rate_limit_error_is_http_client_error(self):
        self.assertTrue(issubclass(RateLimitError, HttpClientError))

    def test_api_error_is_http_client_error(self):
        self.assertTrue(issubclass(ApiError, HttpClientError))

    def test_timeout_error_is_http_client_error(self):
        self.assertTrue(issubclass(HttpTimeoutError, HttpClientError))

    def test_api_error_carries_status_and_body(self):
        err = ApiError("bad", status_code=500, body="oops", url="https://x")
        self.assertEqual(err.status_code, 500)
        self.assertEqual(err.body, "oops")
        self.assertIn("500", str(err))


if __name__ == "__main__":
    unittest.main()
