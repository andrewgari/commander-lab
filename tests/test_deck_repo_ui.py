import json
import subprocess
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app import app


class TestDeckRepoUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_decks_page_contains_repo_indicator_css(self):
        """Verify templates/decks.html contains all required CSS rules for repo badges."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Container styling
        self.assertIn(".deck-status-row", html)
        self.assertIn("min-height: 22px", html)

        # Base badge and states
        self.assertIn(".repo-badge", html)
        self.assertIn(".repo-badge--clean", html)
        self.assertIn(".repo-badge--dirty", html)
        self.assertIn(".repo-badge--untracked", html)
        self.assertIn(".repo-badge--ahead", html)
        self.assertIn(".repo-badge--behind", html)
        self.assertIn(".repo-badge--diverged", html)
        self.assertIn(".repo-badge--error", html)
        self.assertIn(".repo-badge[data-tooltip]", html)

    def test_decks_page_contains_render_repo_badge_js(self):
        """Verify templates/decks.html contains renderRepoBadge and deck-status-row markup."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn("function renderRepoBadge", html)
        self.assertIn("function escapeHtml", html)
        self.assertIn('<div class="deck-status-row">', html)
        self.assertIn("${renderRepoBadge(deck)}", html)

    @patch("app.r")
    def test_api_decks_enriches_with_repo_status(self, mock_redis):
        """Verify GET /api/decks enriches deck objects with repo_status, dirty, and has_changes."""
        mock_redis.get.return_value = json.dumps([
            {"id": 101, "name": "Urza Artifice", "status": "physical"}
        ])
        response = self.client.get("/api/decks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("decks", data)
        self.assertEqual(len(data["decks"]), 1)
        deck = data["decks"][0]
        self.assertIn("repo_status", deck)
        self.assertIn("dirty", deck)
        self.assertIn("has_changes", deck)
        # Without a configured repo path, defaults to error "Path not specified"
        self.assertIn("error", deck["repo_status"])


class TestRenderRepoBadgeJS(unittest.TestCase):
    """Execute the exact renderRepoBadge function extracted from templates/decks.html via Node.js."""

    @classmethod
    def setUpClass(cls):
        with open("templates/decks.html", "r", encoding="utf-8") as f:
            content = f.read()

        # Extract escapeHtml and renderRepoBadge functions
        escape_html_idx = content.find("function escapeHtml(str)")
        render_badge_idx = content.find("function renderRepoBadge(deck, showClean")
        toggle_folder_idx = content.find("function toggleFolder(")

        cls.js_code = (
            content[escape_html_idx:render_badge_idx] +
            content[render_badge_idx:toggle_folder_idx]
        )

    def _call_js(self, deck_obj, show_clean=True):
        deck_json = json.dumps(deck_obj)
        show_clean_str = "true" if show_clean else "false"
        script = f"""
{self.js_code}
const deck = {deck_json};
const result = renderRepoBadge(deck, {show_clean_str});
process.stdout.write(result);
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def test_clean_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 0,
                "ahead": 0,
                "behind": 0,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--clean", html)
        self.assertIn("CLEAN", html)
        self.assertIn('aria-label="Repository clean on branch main"', html)
        self.assertIn('data-tooltip="Repository clean • Branch: main"', html)

    def test_clean_state_hidden_when_show_clean_false(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 0,
                "ahead": 0,
                "behind": 0,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck, show_clean=False)
        self.assertEqual(html, "")

    def test_dirty_modified_state(self):
        deck = {
            "repo_status": {
                "dirty": True,
                "staged": 0,
                "untracked": 0,
                "ahead": 0,
                "behind": 0,
                "branch": "feature/jund",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--dirty", html)
        self.assertIn("MODIFIED", html)
        self.assertIn('aria-label="Repository has uncommitted changes: modified on branch feature/jund"', html)
        self.assertIn('data-tooltip="modified • Branch: feature/jund"', html)

    def test_dirty_staged_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 2,
                "untracked": 0,
                "ahead": 0,
                "behind": 0,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--dirty", html)
        self.assertIn("2 STAGED", html)
        self.assertIn("2 staged • Branch: main", html)

    def test_dirty_staged_and_modified_state(self):
        deck = {
            "repo_status": {
                "dirty": True,
                "staged": 1,
                "untracked": 2,
                "ahead": 1,
                "behind": 0,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--dirty", html)
        self.assertIn("2 DIRTY", html)
        self.assertIn("1 staged, modified, 2 untracked, 1 ahead • Branch: main", html)

    def test_untracked_only_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 3,
                "ahead": 0,
                "behind": 0,
                "branch": "develop",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--untracked", html)
        self.assertIn("3 UNTRACKED", html)
        self.assertIn("3 untracked files • Branch: develop", html)

    def test_ahead_only_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 0,
                "ahead": 2,
                "behind": 0,
                "branch": "feature/dragons",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--ahead", html)
        self.assertIn("2 AHEAD", html)
        self.assertIn("2 commits ahead of upstream (ready to push)", html)

    def test_behind_only_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 0,
                "ahead": 0,
                "behind": 1,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--behind", html)
        self.assertIn("1 BEHIND", html)
        self.assertIn("1 commit behind upstream (pull needed)", html)

    def test_diverged_state(self):
        deck = {
            "repo_status": {
                "dirty": False,
                "staged": 0,
                "untracked": 0,
                "ahead": 2,
                "behind": 3,
                "branch": "main",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--diverged", html)
        self.assertIn("↑2 ↓3", html)
        self.assertIn("2 ahead, 3 behind upstream • Branch: main", html)

    def test_error_path_not_specified(self):
        deck = {
            "repo_status": {
                "error": "Path not specified",
            }
        }
        html = self._call_js(deck)
        # No repo configured: badge hidden (was an error badge on every deck)
        self.assertEqual(html, "")

    def test_error_generic(self):
        deck = {
            "repo_status": {
                "error": "fatal: not a git repository",
            }
        }
        html = self._call_js(deck)
        self.assertIn("repo-badge--error", html)
        self.assertIn("ERROR", html)
        self.assertIn("Git error: fatal: not a git repository", html)

    def test_null_repo_status(self):
        deck = {"repo_status": None}
        html = self._call_js(deck)
        self.assertEqual(html, "")

    def test_html_escaping(self):
        deck = {
            "repo_status": {
                "dirty": True,
                "branch": "<script>alert('xss')</script>",
                "error": None,
            }
        }
        html = self._call_js(deck)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
