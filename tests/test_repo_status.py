"""Unit and integration tests for git repository status detection service."""

import contextlib
import json
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
    get_cache_ttl,
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


class RepoFixtures:
    """Fixture factory creating temporary git repositories in designated states."""

    @staticmethod
    def clean_repo(base_dir: str) -> str:
        """Create a clean git repository with an initial commit on branch 'main'."""
        repo_dir = os.path.join(base_dir, "clean_repo")
        os.makedirs(repo_dir, exist_ok=True)
        _git_run(repo_dir, "init", "-b", "main")
        _git_run(repo_dir, "config", "user.email", "test@example.com")
        _git_run(repo_dir, "config", "user.name", "Tester")
        deck_file = os.path.join(repo_dir, "deck.txt")
        with open(deck_file, "w") as f:
            f.write("Sol Ring\n")
        _git_run(repo_dir, "add", "deck.txt")
        _git_run(repo_dir, "commit", "-m", "Initial commit")
        return repo_dir

    @staticmethod
    def modified_tracked_repo(base_dir: str) -> str:
        """Create a git repository with an uncommitted modification to a tracked file."""
        repo_dir = RepoFixtures.clean_repo(os.path.join(base_dir, "modified_sub"))
        deck_file = os.path.join(repo_dir, "deck.txt")
        with open(deck_file, "a") as f:
            f.write("Mana Crypt\n")
        return repo_dir

    @staticmethod
    def staged_change_repo(base_dir: str) -> str:
        """Create a git repository with a staged modification/addition."""
        repo_dir = RepoFixtures.clean_repo(os.path.join(base_dir, "staged_sub"))
        sideboard_file = os.path.join(repo_dir, "sideboard.txt")
        with open(sideboard_file, "w") as f:
            f.write("Dark Ritual\n")
        _git_run(repo_dir, "add", "sideboard.txt")
        return repo_dir

    @staticmethod
    def untracked_file_repo(base_dir: str) -> str:
        """Create a git repository with an untracked file and no tracked changes."""
        repo_dir = RepoFixtures.clean_repo(os.path.join(base_dir, "untracked_sub"))
        notes_file = os.path.join(repo_dir, "notes.txt")
        with open(notes_file, "w") as f:
            f.write("Deck construction notes\n")
        return repo_dir

    @staticmethod
    def ahead_upstream_repo(base_dir: str) -> str:
        """Create a repository tracking an upstream remote with commits ahead of upstream."""
        upstream_dir = os.path.join(base_dir, "upstream_origin")
        os.makedirs(upstream_dir, exist_ok=True)
        _git_run(upstream_dir, "init", "-b", "main")
        _git_run(upstream_dir, "config", "user.email", "test@example.com")
        _git_run(upstream_dir, "config", "user.name", "Tester")
        with open(os.path.join(upstream_dir, "README.md"), "w") as f:
            f.write("Upstream Deck Repo\n")
        _git_run(upstream_dir, "add", "README.md")
        _git_run(upstream_dir, "commit", "-m", "Upstream initial")

        local_dir = os.path.join(base_dir, "local_clone")
        _git_run(base_dir, "clone", upstream_dir, local_dir)
        _git_run(local_dir, "config", "user.email", "test@example.com")
        _git_run(local_dir, "config", "user.name", "Tester")

        with open(os.path.join(local_dir, "local_update.txt"), "w") as f:
            f.write("Ahead commit change\n")
        _git_run(local_dir, "add", "local_update.txt")
        _git_run(local_dir, "commit", "-m", "Local commit ahead of upstream")
        return local_dir

    @staticmethod
    def non_git_directory(base_dir: str) -> str:
        """Create a plain non-git directory containing normal files."""
        non_git_dir = os.path.join(base_dir, "plain_non_git_folder")
        os.makedirs(non_git_dir, exist_ok=True)
        with open(os.path.join(non_git_dir, "regular.txt"), "w") as f:
            f.write("Regular non-git file\n")
        return non_git_dir

    @staticmethod
    def missing_path(base_dir: str) -> str:
        """Return a non-existent path string."""
        return os.path.join(base_dir, "path_that_does_not_exist_xyz123")


class TestRepoStatusWithFixtures(unittest.TestCase):
    """Unit tests for status service using temporary git repositories created via fixtures."""

    def setUp(self):
        clear_repo_status_cache()
        set_cache_ttl(10.0)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = self._temp_dir.name

    def tearDown(self):
        clear_repo_status_cache()
        self._temp_dir.cleanup()

    def test_fixture_clean_repo(self):
        """Clean repository fixture reports clean status: no dirty files, no staged, has_changes=False."""
        repo_path = RepoFixtures.clean_repo(self.base_dir)
        status = get_repo_status(repo_path)

        self.assertFalse(status.dirty)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.untracked, 0)
        self.assertEqual(status.ahead, 0)
        self.assertEqual(status.behind, 0)
        self.assertEqual(status.branch, "main")
        self.assertFalse(status.has_changes)
        self.assertIsNone(status.error)

    def test_fixture_repo_with_modified_tracked_file(self):
        """Modified tracked file fixture reports dirty=True and has_changes=True."""
        repo_path = RepoFixtures.modified_tracked_repo(self.base_dir)
        status = get_repo_status(repo_path)

        self.assertTrue(status.dirty)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.untracked, 0)
        self.assertTrue(status.has_changes)
        self.assertEqual(status.branch, "main")
        self.assertIsNone(status.error)

    def test_fixture_repo_with_staged_change(self):
        """Staged change fixture reports staged=1 and has_changes=True."""
        repo_path = RepoFixtures.staged_change_repo(self.base_dir)
        status = get_repo_status(repo_path)

        self.assertEqual(status.staged, 1)
        self.assertFalse(status.dirty)
        self.assertEqual(status.untracked, 0)
        self.assertTrue(status.has_changes)
        self.assertEqual(status.branch, "main")
        self.assertIsNone(status.error)

    def test_fixture_repo_with_untracked_file_only(self):
        """Untracked file fixture reports untracked=1, dirty=False, and has_changes=True."""
        repo_path = RepoFixtures.untracked_file_repo(self.base_dir)
        status = get_repo_status(repo_path)

        self.assertFalse(status.dirty)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.untracked, 1)
        self.assertTrue(status.has_changes)
        self.assertEqual(status.branch, "main")
        self.assertIsNone(status.error)

    def test_fixture_repo_ahead_of_upstream_clone(self):
        """Repo ahead of upstream clone fixture reports ahead=1, behind=0, has_changes=True."""
        repo_path = RepoFixtures.ahead_upstream_repo(self.base_dir)
        status = get_repo_status(repo_path)

        self.assertEqual(status.ahead, 1)
        self.assertEqual(status.behind, 0)
        self.assertFalse(status.dirty)
        self.assertEqual(status.staged, 0)
        self.assertEqual(status.untracked, 0)
        self.assertTrue(status.has_changes)
        self.assertEqual(status.branch, "main")
        self.assertIsNone(status.error)

    def test_fixture_non_git_directory(self):
        """Non-git directory fixture reports graceful error without raising exceptions."""
        non_git_path = RepoFixtures.non_git_directory(self.base_dir)
        status = get_repo_status(non_git_path)

        self.assertFalse(status.dirty)
        self.assertFalse(status.has_changes)
        self.assertEqual(status.error, "Not a git repository")
        self.assertIsNone(status.branch)

    def test_fixture_missing_path(self):
        """Missing or non-existent path fixture reports graceful error without raising."""
        missing_path = RepoFixtures.missing_path(self.base_dir)
        status = get_repo_status(missing_path)

        self.assertFalse(status.dirty)
        self.assertFalse(status.has_changes)
        self.assertIn("Path does not exist", status.error or "")


class TestRepoStatusCache(unittest.TestCase):
    """Test cache behavior: repeated calls within TTL do not re-invoke git; calls after TTL expiry do."""

    def setUp(self):
        clear_repo_status_cache()
        set_cache_ttl(10.0)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = RepoFixtures.clean_repo(self._temp_dir.name)

    def tearDown(self):
        clear_repo_status_cache()
        self._temp_dir.cleanup()

    def test_repeated_calls_within_ttl_do_not_reinvoke_git_and_expiry_does(self):
        """Repeated calls within TTL hit in-memory cache without running git subprocess; expiry re-invokes git."""
        fake_time = 1000.0

        def current_time():
            return fake_time

        with patch("src.services.repo_status.time.time", side_effect=current_time):
            with patch("src.services.repo_status.subprocess.run", wraps=subprocess.run) as mock_git:
                # 1. First call: Cache miss -> invokes git once
                status1 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 1)
                self.assertFalse(status1.dirty)
                self.assertEqual(get_cache_size(), 1)
                self.assertEqual(status1.last_checked, 1000.0)

                # 2. Repeated call 1s later (well within 10s TTL): git must NOT be re-invoked
                fake_time = 1001.0
                status2 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 1)
                self.assertEqual(status2.last_checked, 1000.0)

                # 3. Repeated call 5s later (still within TTL): git must NOT be re-invoked
                fake_time = 1005.0
                status3 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 1)
                self.assertEqual(status3.last_checked, 1000.0)

                # 4. Repeated call 9.9s later (boundary condition within TTL): git must NOT be re-invoked
                fake_time = 1009.9
                status4 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 1)

                # 5. Call 10.1s later (TTL expired!): git MUST be re-invoked
                fake_time = 1010.1
                status5 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 2)
                self.assertEqual(status5.last_checked, 1010.1)

                # 6. Call 2s after second fetch (within renewed TTL): cached, no new git call
                fake_time = 1012.0
                status6 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 2)
                self.assertEqual(status6.last_checked, 1010.1)

                # 7. Call 11s after second fetch (second TTL expired): git re-invoked a 3rd time
                fake_time = 1021.5
                status7 = get_repo_status(self.repo_dir, ttl=10.0)
                self.assertEqual(mock_git.call_count, 3)
                self.assertEqual(status7.last_checked, 1021.5)

    def test_cache_ttl_zero_always_bypasses_cache(self):
        """Calls with ttl=0 or effective_ttl=0 bypass cache and re-invoke git every time."""
        with patch("src.services.repo_status.subprocess.run", wraps=subprocess.run) as mock_git:
            get_repo_status(self.repo_dir, ttl=0)
            self.assertEqual(mock_git.call_count, 1)

            get_repo_status(self.repo_dir, ttl=0)
            self.assertEqual(mock_git.call_count, 2)

            get_repo_status(self.repo_dir, ttl=0)
            self.assertEqual(mock_git.call_count, 3)

    def test_cache_ttl_getter_and_setter(self):
        """set_cache_ttl and get_cache_ttl update and return DEFAULT_CACHE_TTL correctly."""
        original = get_cache_ttl()
        try:
            set_cache_ttl(25.5)
            self.assertEqual(get_cache_ttl(), 25.5)
            set_cache_ttl(5.0)
            self.assertEqual(get_cache_ttl(), 5.0)
        finally:
            set_cache_ttl(original)

    def test_clear_repo_status_cache(self):
        """clear_repo_status_cache flushes all entries in memory."""
        get_repo_status(self.repo_dir, ttl=10.0)
        self.assertGreater(get_cache_size(), 0)

        clear_repo_status_cache()
        self.assertEqual(get_cache_size(), 0)


class TestRepoStatusEdgeCases(unittest.TestCase):
    """Test edge cases such as detached HEAD, empty input, and concurrent batch processing."""

    def setUp(self):
        clear_repo_status_cache()

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

    def test_detached_head(self):
        """Detached HEAD state reports error='Detached HEAD' and branch=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = RepoFixtures.clean_repo(tmpdir)
            _git_run(repo_path, "checkout", "HEAD~0")

            status = get_repo_status(repo_path)
            self.assertEqual(status.error, "Detached HEAD")
            self.assertIsNone(status.branch)

    def test_batch_repo_status_concurrency(self):
        """Batch query retrieves statuses across multiple paths concurrently and caches correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo1 = RepoFixtures.clean_repo(os.path.join(tmpdir, "r1"))
            repo2 = RepoFixtures.untracked_file_repo(os.path.join(tmpdir, "r2"))
            nongit = RepoFixtures.non_git_directory(os.path.join(tmpdir, "ng"))

            results = get_batch_repo_status([repo1, repo2, nongit, None], max_workers=4)

            self.assertEqual(len(results), 4)
            self.assertEqual(results[repo1].branch, "main")
            self.assertFalse(results[repo1].has_changes)
            self.assertTrue(results[repo2].has_changes)
            self.assertEqual(results[nongit].error, "Not a git repository")
            self.assertEqual(results[None].error, "Path not specified")


class TestDeckRepoEnrichment(unittest.TestCase):
    """Test deck payload enrichment and filesystem path resolution."""

    def setUp(self):
        clear_repo_status_cache()

    def tearDown(self):
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

    def test_enrich_decks_with_repo_status_additive(self):
        """Enriching decks attaches repo_status, has_changes, dirty in backward-compatible fashion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo1 = RepoFixtures.clean_repo(os.path.join(tmpdir, "d1"))
            repo2 = RepoFixtures.modified_tracked_repo(os.path.join(tmpdir, "d2"))

            decks = [
                {"id": 1, "name": "Deck Clean", "path": repo1, "color": "U"},
                {"id": 2, "name": "Deck Dirty", "path": repo2, "color": "B"},
                {"id": 3, "name": "Deck NoPath", "color": "R"},
            ]

            enriched = enrich_decks_with_repo_status(decks)
            self.assertEqual(len(enriched), 3)

            # Deck 1 (Clean) - verify legacy and new fields
            self.assertEqual(enriched[0]["id"], 1)
            self.assertEqual(enriched[0]["color"], "U")
            self.assertIn("repo_status", enriched[0])
            self.assertFalse(enriched[0]["dirty"])
            self.assertFalse(enriched[0]["has_changes"])
            self.assertFalse(enriched[0]["repo_status"]["dirty"])
            self.assertIsNone(enriched[0]["repo_status"]["error"])

            # Deck 2 (Dirty) - verify legacy and new fields
            self.assertEqual(enriched[1]["id"], 2)
            self.assertEqual(enriched[1]["color"], "B")
            self.assertTrue(enriched[1]["dirty"])
            self.assertTrue(enriched[1]["has_changes"])
            self.assertTrue(enriched[1]["repo_status"]["dirty"])

            # Deck 3 (No Path) - graceful handling
            self.assertEqual(enriched[2]["id"], 3)
            self.assertEqual(enriched[2]["color"], "R")
            self.assertFalse(enriched[2]["dirty"])
            self.assertFalse(enriched[2]["has_changes"])
            self.assertEqual(enriched[2]["repo_status"]["error"], "Path not specified")


class TestApiDecksIntegration(unittest.TestCase):
    """Integration and smoke tests asserting GET /api/decks includes repo status and preserves backward compatibility."""

    def setUp(self):
        self.client = TestClient(app)
        clear_repo_status_cache()

    def tearDown(self):
        clear_repo_status_cache()

    @patch("app.r")
    def test_api_decks_smoke_and_backward_compatibility(self, mock_redis):
        """GET /api/decks returns 200, includes new status fields, and maintains backward compatibility for existing consumers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = RepoFixtures.modified_tracked_repo(tmpdir)

            # Mock existing legacy deck list structure in Redis
            legacy_decks = [
                {
                    "id": 101,
                    "name": "Kenrith Reanimator",
                    "color": "WUBRG",
                    "commanders": ["Kenrith, the Returned King"],
                    "status": "Have",
                    "folder_id": 588380,
                    "path": repo_path,
                },
                {
                    "id": 102,
                    "name": "Karn Silver Golem",
                    "color": "C",
                    "commanders": ["Karn, Silver Golem"],
                    "status": "Virtual",
                    "folder_id": 588381,
                    # No local repo path attached
                },
            ]
            mock_redis.get.return_value = json.dumps(legacy_decks)

            response = self.client.get("/api/decks")
            self.assertEqual(response.status_code, 200)

            data = response.json()
            self.assertIn("decks", data)
            decks = data["decks"]
            self.assertEqual(len(decks), 2)

            # 1. Verify 100% backward compatibility of existing fields for existing consumers
            self.assertEqual(decks[0]["id"], 101)
            self.assertEqual(decks[0]["name"], "Kenrith Reanimator")
            self.assertEqual(decks[0]["color"], "WUBRG")
            self.assertEqual(decks[0]["commanders"], ["Kenrith, the Returned King"])
            self.assertEqual(decks[0]["status"], "Have")
            self.assertEqual(decks[0]["folder_id"], 588380)

            self.assertEqual(decks[1]["id"], 102)
            self.assertEqual(decks[1]["name"], "Karn Silver Golem")
            self.assertEqual(decks[1]["color"], "C")
            self.assertEqual(decks[1]["commanders"], ["Karn, Silver Golem"])
            self.assertEqual(decks[1]["status"], "Virtual")

            # 2. Verify presence and schema of new status fields
            for deck in decks:
                self.assertIn("repo_status", deck)
                self.assertIn("dirty", deck)
                self.assertIn("has_changes", deck)
                status_dict = deck["repo_status"]
                self.assertIn("dirty", status_dict)
                self.assertIn("staged", status_dict)
                self.assertIn("untracked", status_dict)
                self.assertIn("ahead", status_dict)
                self.assertIn("behind", status_dict)
                self.assertIn("branch", status_dict)
                self.assertIn("has_changes", status_dict)
                self.assertIn("last_checked", status_dict)
                self.assertIn("error", status_dict)

            # 3. Verify specific metrics on deck with modified tracked file
            deck_local = decks[0]
            self.assertTrue(deck_local["dirty"])
            self.assertTrue(deck_local["has_changes"])
            self.assertTrue(deck_local["repo_status"]["dirty"])
            self.assertTrue(deck_local["repo_status"]["has_changes"])
            self.assertEqual(deck_local["repo_status"]["branch"], "main")
            self.assertIsNone(deck_local["repo_status"]["error"])

            # 4. Verify deck without local repo path degrades gracefully without breaking consumers
            deck_remote = decks[1]
            self.assertFalse(deck_remote["dirty"])
            self.assertFalse(deck_remote["has_changes"])
            self.assertEqual(deck_remote["repo_status"]["error"], "Path not specified")


if __name__ == "__main__":
    unittest.main()
