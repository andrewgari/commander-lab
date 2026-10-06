import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from fastapi.testclient import TestClient

import github_sync
from app import app, extract_short_description


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

        # Truncation with ellipsis
        long_body = "word " * 100
        desc_truncated = extract_short_description(long_body, max_length=50)
        self.assertTrue(desc_truncated.endswith("..."))
        self.assertLessEqual(len(desc_truncated), 53)

    @patch("httpx.AsyncClient")
    def test_github_sync_caches_releases_from_api(self, mock_client_cls):
        # The GitHub fetch runs in the background (github_sync.update_github_data)
        # and writes both the active and stale changelog cache keys.
        tags_resp = MagicMock()
        tags_resp.json.return_value = [{"name": "v1.2.0", "commit": {"sha": "abc"}}]
        releases_resp = MagicMock()
        releases_resp.json.return_value = self.sample_releases

        mock_client = AsyncMock()
        mock_client.get.side_effect = [tags_resp, releases_resp]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        mock_redis = MagicMock()
        asyncio.run(github_sync.update_github_data(mock_redis))

        mock_redis.setex.assert_any_call(github_sync.CACHE_VERSION_KEY, 600, "1.2.0")
        changelog_writes = [
            c for c in mock_redis.setex.call_args_list
            if c.args[0] == github_sync.CACHE_CHANGELOG_KEY
        ]
        self.assertEqual(len(changelog_writes), 1)
        cached = json.loads(changelog_writes[0].args[2])
        self.assertEqual([r["tag_name"] for r in cached], ["v1.2.0", "v1.1.0-beta.1"])
        mock_redis.set.assert_any_call(
            github_sync.STALE_CHANGELOG_KEY, changelog_writes[0].args[2]
        )

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_renders_html_with_releases(self, mock_client_cls, mock_redis):
        mock_redis.get.side_effect = lambda key: {
            github_sync.CACHE_VERSION_KEY: "1.2.0",
            github_sync.CACHE_CHANGELOG_KEY: json.dumps(self.sample_releases),
        }.get(key)

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

    @patch("app.r")
    @patch("httpx.AsyncClient")
    def test_changelog_serves_from_redis_cache_without_api_call(self, mock_client_cls, mock_redis):
        # Redis cache hit for both the version and the changelog
        mock_redis.get.side_effect = lambda key: {
            github_sync.CACHE_VERSION_KEY: "1.2.0",
            github_sync.CACHE_CHANGELOG_KEY: json.dumps(self.sample_releases),
        }.get(key)

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
            if key == github_sync.STALE_CHANGELOG_KEY:
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
