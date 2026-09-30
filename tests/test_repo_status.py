"""Unit and integration tests for git repository status detection service."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from repo_status import (
    DEFAULT_CACHE_TTL,
    RepoStatus,
    clear_repo_status_cache,
    enrich_decks_with_repo_status,
    get_batch_repo_status,
    get_cache_size,
    get_repo_status,
    resolve_deck_repo_path,
    set_cache_ttl,
)


def _git_run(cwd: str, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in cwd and ensure it succeeds."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


class TestRepoStatusService(unittest.TestCase):
    """Test git change detection on various git repository states."""

    def setUp(self):
        clear_repo_status_cache()
        set_cache_ttl(10.0)

    def tearDown(self):
        clear_repo_status_cache()

    def test_missing_or_empty_path(self):
        """Missing or None path must return an error and not raise."""
        status_none = get_repo_status(None)
        self.assertFalse(status_none.dirty)
        self.assertEqual(status_none.error, "Path not specified")
        self.assertFalse(status_none.has_changes)

        status_empty = get_repo_status("")
        self.assertEqual(status_empty.error, "Path not specified")

        status_nonexistent = get_repo_status("/path/that/definitely/does/not/exist_12345")
        self.assertIn("Path does not exist", status_nonexistent.error or "")
        self.assertFalse(status_nonexistent.dirty)

    def test_non_git_directory(self):
        """Non-git directory must report an error string rather than crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status = get_repo_status(tmpdir)
            self.assertFalse(status.dirty)
            self.assertEqual(status.error, "Not a git repository")
            self.assertFalse(status.has_changes)
            self.assertIsNone(status.branch)

    def test_clean_repo(self):
        """Clean git repository reports dirty=false and has_changes=false."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            test_file = os.path.join(tmpdir, "card.txt")
            with open(test_file, "w") as f:
                f.write("Sol Ring\n")
            _git_run(tmpdir, "add", "card.txt")
            _git_run(tmpdir, "commit", "-m", "Initial commit")

            status = get_repo_status(tmpdir)
            self.assertFalse(status.dirty)
            self.assertEqual(status.staged, 0)
            self.assertEqual(status.untracked, 0)
            self.assertEqual(status.ahead, 0)
            self.assertEqual(status.behind, 0)
            self.assertEqual(status.branch, "main")
            self.assertFalse(status.has_changes)
            self.assertIsNone(status.error)

    def test_repo_with_modified_tracked_file(self):
        """Repo with an uncommitted tracked edit reports dirty=true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            test_file = os.path.join(tmpdir, "deck.txt")
            with open(test_file, "w") as f:
                f.write("Arcane Signet\n")
            _git_run(tmpdir, "add", "deck.txt")
            _git_run(tmpdir, "commit", "-m", "Add card")

            # Uncommitted tracked modification
            with open(test_file, "a") as f:
                f.write("Mana Crypt\n")

            status = get_repo_status(tmpdir)
            self.assertTrue(status.dirty)
            self.assertEqual(status.staged, 0)
            self.assertEqual(status.untracked, 0)
            self.assertTrue(status.has_changes)
            self.assertIsNone(status.error)

    def test_repo_with_staged_change(self):
        """Repo with staged changes reports staged count and has_changes=true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            test_file = os.path.join(tmpdir, "deck.txt")
            with open(test_file, "w") as f:
                f.write("Swamp\n")
            _git_run(tmpdir, "add", "deck.txt")
            _git_run(tmpdir, "commit", "-m", "Initial")

            new_file = os.path.join(tmpdir, "sideboard.txt")
            with open(new_file, "w") as f:
                f.write("Dark Ritual\n")
            _git_run(tmpdir, "add", "sideboard.txt")

            status = get_repo_status(tmpdir)
            self.assertEqual(status.staged, 1)
            self.assertFalse(status.dirty)
            self.assertEqual(status.untracked, 0)
            self.assertTrue(status.has_changes)

    def test_repo_with_untracked_file(self):
        """Repo with only untracked file reports untracked count and has_changes=true."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            test_file = os.path.join(tmpdir, "deck.txt")
            with open(test_file, "w") as f:
                f.write("Island\n")
            _git_run(tmpdir, "add", "deck.txt")
            _git_run(tmpdir, "commit", "-m", "Initial")

            untracked = os.path.join(tmpdir, "notes.txt")
            with open(untracked, "w") as f:
                f.write("Maybe add Brainstorm\n")

            status = get_repo_status(tmpdir)
            self.assertFalse(status.dirty)
            self.assertEqual(status.staged, 0)
            self.assertEqual(status.untracked, 1)
            self.assertTrue(status.has_changes)

    def test_repo_ahead_and_behind_upstream(self):
        """Repo tracking upstream branch reports ahead and behind counts correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_dir = os.path.join(tmpdir, "remote")
            os.makedirs(remote_dir)
            _git_run(remote_dir, "init", "-b", "main")
            _git_run(remote_dir, "config", "user.email", "test@example.com")
            _git_run(remote_dir, "config", "user.name", "Tester")

            with open(os.path.join(remote_dir, "README.md"), "w") as f:
                f.write("Initial\n")
            _git_run(remote_dir, "add", "README.md")
            _git_run(remote_dir, "commit", "-m", "Commit 1")

            local_dir = os.path.join(tmpdir, "local")
            _git_run(tmpdir, "clone", remote_dir, local_dir)
            _git_run(local_dir, "config", "user.email", "test@example.com")
            _git_run(local_dir, "config", "user.name", "Tester")

            # Local commit ahead
            with open(os.path.join(local_dir, "local.txt"), "w") as f:
                f.write("Local change\n")
            _git_run(local_dir, "add", "local.txt")
            _git_run(local_dir, "commit", "-m", "Commit 2 local")

            status = get_repo_status(local_dir)
            self.assertEqual(status.ahead, 1)
            self.assertEqual(status.behind, 0)
            self.assertTrue(status.has_changes)
            self.assertFalse(status.dirty)

    def test_detached_head(self):
        """Detached HEAD state reports error='Detached HEAD' and branch=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            with open(os.path.join(tmpdir, "f.txt"), "w") as f:
                f.write("content\n")
            _git_run(tmpdir, "add", "f.txt")
            _git_run(tmpdir, "commit", "-m", "init")

            _git_run(tmpdir, "checkout", "HEAD~0")

            status = get_repo_status(tmpdir)
            self.assertEqual(status.error, "Detached HEAD")
            self.assertIsNone(status.branch)

    def test_cache_ttl_behavior(self):
        """Repeated calls within TTL hit cache; calls after TTL expiry re-query."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _git_run(tmpdir, "init", "-b", "main")
            _git_run(tmpdir, "config", "user.email", "test@example.com")
            _git_run(tmpdir, "config", "user.name", "Tester")

            with open(os.path.join(tmpdir, "f.txt"), "w") as f:
                f.write("init\n")
            _git_run(tmpdir, "add", "f.txt")
            _git_run(tmpdir, "commit", "-m", "init")

            # First query populates cache
            status1 = get_repo_status(tmpdir, ttl=5.0)
            self.assertFalse(status1.dirty)
            self.assertEqual(get_cache_size(), 1)

            # Modify file without updating cache
            with open(os.path.join(tmpdir, "f.txt"), "a") as f:
                f.write("modified\n")

            # Within TTL -> still returns cached clean status
            status2 = get_repo_status(tmpdir, ttl=5.0)
            self.assertFalse(status2.dirty)
            self.assertEqual(status1.last_checked, status2.last_checked)

            # With ttl=0 (bypass cache) -> gets fresh dirty status
            status3 = get_repo_status(tmpdir, ttl=0)
            self.assertTrue(status3.dirty)

    def test_batch_repo_status(self):
        """Batch query retrieves statuses across multiple paths concurrently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo1 = os.path.join(tmpdir, "repo1")
            repo2 = os.path.join(tmpdir, "repo2")
            nongit = os.path.join(tmpdir, "nongit")
            os.makedirs(repo1)
            os.makedirs(repo2)
            os.makedirs(nongit)

            _git_run(repo1, "init", "-b", "main")
            _git_run(repo2, "init", "-b", "dev")

            results = get_batch_repo_status([repo1, repo2, nongit, None], max_workers=4)

            self.assertEqual(len(results), 4)
            self.assertEqual(results[repo1].branch, "main")
            self.assertEqual(results[repo2].branch, "dev")
            self.assertEqual(results[nongit].error, "Not a git repository")
            self.assertEqual(results[None].error, "Path not specified")


class TestDeckRepoEnrichment(unittest.TestCase):
    """Test deck payload enrichment and path resolution."""

    def setUp(self):
        clear_repo_status_cache()

    def test_resolve_deck_repo_path(self):
        """Verify resolution order: repo_path / path / git_path / base_dir match."""
        deck1 = {"id": 1, "name": "Muldrotha", "repo_path": "/custom/path"}
        self.assertEqual(resolve_deck_repo_path(deck1), "/custom/path")

        deck2 = {"id": 2, "name": "Urza", "path": "/another/path"}
        self.assertEqual(resolve_deck_repo_path(deck2), "/another/path")

        with tempfile.TemporaryDirectory() as tmpdir:
            deck_dir = os.path.join(tmpdir, "Atraxa")
            os.makedirs(deck_dir)

            deck3 = {"id": 3, "name": "Atraxa"}
            resolved = resolve_deck_repo_path(deck3, base_dir=tmpdir)
            self.assertEqual(resolved, deck_dir)

            deck_missing = {"id": 4, "name": "Unknown"}
            self.assertIsNone(resolve_deck_repo_path(deck_missing, base_dir=tmpdir))

    def test_enrich_decks_with_repo_status(self):
        """Enriching decks attaches repo_status, has_changes, dirty in backward-compatible fashion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo1 = os.path.join(tmpdir, "deck_clean")
            repo2 = os.path.join(tmpdir, "deck_dirty")
            os.makedirs(repo1)
            os.makedirs(repo2)

            _git_run(repo1, "init", "-b", "main")
            _git_run(repo1, "config", "user.email", "test@example.com")
            _git_run(repo1, "config", "user.name", "Tester")
            with open(os.path.join(repo1, "deck.txt"), "w") as f:
                f.write("Sol Ring\n")
            _git_run(repo1, "add", "deck.txt")
            _git_run(repo1, "commit", "-m", "Initial")

            _git_run(repo2, "init", "-b", "main")
            _git_run(repo2, "config", "user.email", "test@example.com")
            _git_run(repo2, "config", "user.name", "Tester")
            with open(os.path.join(repo2, "deck.txt"), "w") as f:
                f.write("Sol Ring\n")
            _git_run(repo2, "add", "deck.txt")
            _git_run(repo2, "commit", "-m", "Initial")
            with open(os.path.join(repo2, "deck.txt"), "a") as f:
                f.write("Command Tower\n")

            decks = [
                {"id": 1, "name": "Deck Clean", "path": repo1},
                {"id": 2, "name": "Deck Dirty", "path": repo2},
                {"id": 3, "name": "Deck NoPath"},
            ]

            enriched = enrich_decks_with_repo_status(decks)
            self.assertEqual(len(enriched), 3)

            # Deck 1 (Clean)
            self.assertIn("repo_status", enriched[0])
            self.assertFalse(enriched[0]["dirty"])
            self.assertFalse(enriched[0]["has_changes"])
            self.assertFalse(enriched[0]["repo_status"]["dirty"])
            self.assertIsNone(enriched[0]["repo_status"]["error"])

            # Deck 2 (Dirty)
            self.assertTrue(enriched[1]["dirty"])
            self.assertTrue(enriched[1]["has_changes"])
            self.assertTrue(enriched[1]["repo_status"]["dirty"])

            # Deck 3 (No Path)
            self.assertFalse(enriched[2]["dirty"])
            self.assertFalse(enriched[2]["has_changes"])
            self.assertEqual(enriched[2]["repo_status"]["error"], "Path not specified")


class TestApiDecksIntegration(unittest.TestCase):
    """Integration test asserting /api/decks returns repo status additively."""

    def setUp(self):
        self.client = TestClient(app)
        clear_repo_status_cache()

    @patch("app.r")
    def test_api_decks_includes_repo_status(self, mock_redis):
        """GET /api/decks attaches repo_status while preserving all existing deck fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = os.path.join(tmpdir, "MyDeck")
            os.makedirs(repo_path)
            _git_run(repo_path, "init", "-b", "main")
            _git_run(repo_path, "config", "user.email", "test@example.com")
            _git_run(repo_path, "config", "user.name", "Tester")
            with open(os.path.join(repo_path, "deck.txt"), "w") as f:
                f.write("Card\n")
            _git_run(repo_path, "add", "deck.txt")
            _git_run(repo_path, "commit", "-m", "init")

            # Uncommitted change
            with open(os.path.join(repo_path, "deck.txt"), "a") as f:
                f.write("Card 2\n")

            mock_decks = [
                {
                    "id": 101,
                    "name": "My Deck",
                    "color": "WUBRG",
                    "commanders": ["Kenrith"],
                    "path": repo_path,
                },
                {
                    "id": 102,
                    "name": "Remote Only Deck",
                    "color": "C",
                    "commanders": ["Karn"],
                },
            ]
            import json
            mock_redis.get.return_value = json.dumps(mock_decks)

            response = self.client.get("/api/decks")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertIn("decks", data)
            decks = data["decks"]
            self.assertEqual(len(decks), 2)

            # Verify backward-compatible existing fields
            self.assertEqual(decks[0]["id"], 101)
            self.assertEqual(decks[0]["name"], "My Deck")
            self.assertEqual(decks[0]["color"], "WUBRG")

            # Verify additive repo status fields
            self.assertIn("repo_status", decks[0])
            self.assertTrue(decks[0]["repo_status"]["dirty"])
            self.assertTrue(decks[0]["repo_status"]["has_changes"])
            self.assertEqual(decks[0]["repo_status"]["branch"], "main")
            self.assertIsNone(decks[0]["repo_status"]["error"])

            # Verify deck without path has graceful error status without breaking
            self.assertIn("repo_status", decks[1])
            self.assertEqual(decks[1]["repo_status"]["error"], "Path not specified")
            self.assertFalse(decks[1]["repo_status"]["dirty"])


if __name__ == "__main__":
    unittest.main()
