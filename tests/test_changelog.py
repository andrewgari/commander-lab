import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from fastapi.testclient import TestClient

from app import app, extract_short_description, render_release_body


class TestChangelog(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sample_releases = [
            {
                "tag_name": "v1.2.0",
                "name": "v1.2.0 - Performance & Caching",
                "published_at": "2026-09-29T18:00:00Z",
                "prerelease": False,
                "body": "## What's Changed\n* Added Redis caching to changelog route\n* Fixed layout consistency with base.html",
            },
            {
                "tag_name": "v1.1.0-beta.1",
                "name": "v1.1.0-beta.1",
                "published_at": "2026-09-20T12:30:00Z",
                "prerelease": True,
                "body": "Beta release testing new inventory views and card tracking.",
            },
        ]

    def test_extract_short_description(self):
        # Empty body
        self.assertEqual(extract_short_description(None), "")
        self.assertEqual(extract_short_description(""), "")

        # Markdown headers stripped, lines joined
        body = "## What's Changed\n* Added feature A\n* Added feature B"
        desc = extract_short_description(body)
        self.assertNotIn("## What's Changed", desc)
        self.assertIn("Added feature A", desc)

        # No truncation, no ellipsis -- the full text is always returned
        long_body = "word " * 100
        desc_full = extract_short_description(long_body, max_length=50)
        self.assertFalse(desc_full.endswith("..."))
        self.assertIn("word", desc_full)
        self.assertEqual(desc_full.count("word"), 100)

    def test_render_release_body_empty(self):
        self.assertEqual(render_release_body(None), "")
        self.assertEqual(render_release_body(""), "")

    def test_render_release_body_preserves_headings_and_bullets(self):
        body = "## What's Changed\n* Added feature A\n* Added feature B\n* Fixed bug C"
        html_out = render_release_body(body)
        self.assertIn("<h2>", html_out)
        self.assertIn("<ul>", html_out)
        self.assertIn("<li>", html_out)
        self.assertIn("Added feature A", html_out)
        self.assertIn("Fixed bug C", html_out)

    def test_render_release_body_never_truncates_long_text(self):
        long_body = "## Release notes\n" + "\n".join(f"* Item number {i}" for i in range(100))
        html_out = render_release_body(long_body)
        self.assertNotIn("...", html_out)
        self.assertIn("Item number 0", html_out)
        self.assertIn("Item number 99", html_out)

    def test_render_release_body_sanitizes_dangerous_html(self):
        body = "Hello <script>alert('xss')</script> world\n\n<img src=x onerror=alert(1)>"
        html_out = render_release_body(body)
        self.assertNotIn("<script", html_out)
        self.assertNotIn("onerror", html_out)
        self.assertIn("Hello", html_out)
        self.assertIn("world", html_out)

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_renders_html_with_releases_from_api(self, mock_client_cls, mock_redis):
        # Redis cache miss initially
        mock_redis.get.return_value = None

        # Mock httpx response
        mock_resp = MagicMock()
        mock_resp.json.return_value = self.sample_releases
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        response = self.client.get("/changelog")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

        html = response.text
        # Check base.html extension
        self.assertIn("Cmdr Lab", html)
        self.assertIn("Changelog", html)

        # Check tag, date, description
        self.assertIn("v1.2.0", html)
        self.assertIn("2026-09-29", html)
        self.assertIn("Redis caching to changelog route", html)

        # Check pre-release badge
        self.assertIn("v1.1.0-beta.1", html)
        self.assertIn("Pre-release", html)

        # Verify Redis caching was performed
        mock_redis.setex.assert_called_once_with(
            "github_releases", 600, json.dumps(self.sample_releases)
        )
        mock_redis.set.assert_called_once_with(
            "github_releases_stale", json.dumps(self.sample_releases)
        )

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_serves_from_redis_cache_without_api_call(self, mock_client_cls, mock_redis):
        # Redis cache hit
        mock_redis.get.side_effect = lambda key: (
            json.dumps(self.sample_releases) if key == "github_releases" else None
        )

        response = self.client.get("/changelog")
        self.assertEqual(response.status_code, 200)

        # Verify httpx client was never called
        mock_client_cls.assert_not_called()

        html = response.text
        self.assertIn("v1.2.0", html)
        self.assertIn("2026-09-29", html)

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_api_unreachable_and_cache_empty_shows_unavailable_message(
        self, mock_client_cls, mock_redis
    ):
        # Redis cache empty (neither active nor stale)
        mock_redis.get.return_value = None

        # httpx raises connection error
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Network unreachable")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        response = self.client.get("/changelog")
        self.assertEqual(response.status_code, 200)

        html = response.text
        self.assertIn("unavailable", html.lower())
        self.assertNotIn("Traceback", html)

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_api_unreachable_serves_stale_cache(
        self, mock_client_cls, mock_redis
    ):
        # Active cache is expired (None), but stale cache has data
        def redis_get(key):
            if key == "github_releases_stale":
                return json.dumps(self.sample_releases)
            return None

        mock_redis.get.side_effect = redis_get

        # httpx raises an exception
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        response = self.client.get("/changelog")
        self.assertEqual(response.status_code, 200)

        html = response.text
        # Verify stale releases are displayed despite API failure
        self.assertIn("v1.2.0", html)
        self.assertIn("2026-09-29", html)
        self.assertNotIn("unavailable", html.lower())

    def test_base_html_version_badge_links_to_changelog(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('href="/changelog"', html)
        self.assertIn('id="app-version"', html)

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_scrollable_container_present(self, mock_client_cls, mock_redis):
        mock_redis.get.return_value = json.dumps(self.sample_releases)

        response = self.client.get("/changelog")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("changelog-scrollable", html)


if __name__ == "__main__":
    unittest.main()
