"""Pytest fixture-based unit and integration tests for git repository change detection."""

import os
import subprocess
import time
import unittest
from unittest.mock import patch

try:
    import pytest
except ImportError as exc:
    raise unittest.SkipTest("pytest not installed; install pytest to run pytest-based tests") from exc

from repo_status import (
    clear_repo_status_cache,
    enrich_decks_with_repo_status,
    get_cache_size,
    get_repo_status,
    set_cache_ttl,
)


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _create_clean_repo(repo_dir: str) -> str:
    os.makedirs(repo_dir, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Tester")
    with open(os.path.join(repo_dir, "deck.txt"), "w") as f:
        f.write("Sol Ring\n")
    _git(repo_dir, "add", "deck.txt")
    _git(repo_dir, "commit", "-m", "Initial commit")
    return repo_dir


@pytest.fixture
def clean_repo(tmp_path):
    """Fixture creating a clean git repository."""
    clear_repo_status_cache()
    repo_dir = str(tmp_path / "clean_repo")
    _create_clean_repo(repo_dir)
    yield repo_dir
    clear_repo_status_cache()


@pytest.fixture
def modified_tracked_repo(tmp_path):
    """Fixture creating a git repository with an uncommitted tracked change."""
    clear_repo_status_cache()
    repo_dir = str(tmp_path / "modified_repo")
    _create_clean_repo(repo_dir)
    with open(os.path.join(repo_dir, "deck.txt"), "a") as f:
        f.write("Mana Crypt\n")
    yield repo_dir
    clear_repo_status_cache()


@pytest.fixture
def staged_change_repo(tmp_path):
    """Fixture creating a git repository with a staged change."""
    clear_repo_status_cache()
    repo_dir = str(tmp_path / "staged_repo")
    _create_clean_repo(repo_dir)
    sideboard = os.path.join(repo_dir, "sideboard.txt")
    with open(sideboard, "w") as f:
        f.write("Dark Ritual\n")
    _git(repo_dir, "add", "sideboard.txt")
    yield repo_dir
    clear_repo_status_cache()


@pytest.fixture
def untracked_file_repo(tmp_path):
    """Fixture creating a git repository with an untracked file only."""
    clear_repo_status_cache()
    repo_dir = str(tmp_path / "untracked_repo")
    _create_clean_repo(repo_dir)
    notes = os.path.join(repo_dir, "notes.txt")
    with open(notes, "w") as f:
        f.write("Deck construction notes\n")
    yield repo_dir
    clear_repo_status_cache()


@pytest.fixture
def ahead_upstream_repo(tmp_path):
    """Fixture creating a local git repository clone ahead of a local upstream clone."""
    clear_repo_status_cache()
    upstream = str(tmp_path / "upstream")
    os.makedirs(upstream, exist_ok=True)
    _git(upstream, "init", "-b", "main")
    _git(upstream, "config", "user.email", "test@example.com")
    _git(upstream, "config", "user.name", "Tester")
    with open(os.path.join(upstream, "README.md"), "w") as f:
        f.write("Upstream Deck\n")
    _git(upstream, "add", "README.md")
    _git(upstream, "commit", "-m", "Upstream initial commit")

    local = str(tmp_path / "local_clone")
    _git(str(tmp_path), "clone", upstream, local)
    _git(local, "config", "user.email", "test@example.com")
    _git(local, "config", "user.name", "Tester")

    with open(os.path.join(local, "card.txt"), "w") as f:
        f.write("Demonic Tutor\n")
    _git(local, "add", "card.txt")
    _git(local, "commit", "-m", "Local commit ahead")
    yield local
    clear_repo_status_cache()


@pytest.fixture
def non_git_directory(tmp_path):
    """Fixture creating a non-git directory."""
    d = str(tmp_path / "plain_dir")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "file.txt"), "w") as f:
        f.write("Hello\n")
    return d


@pytest.fixture
def missing_path(tmp_path):
    """Fixture providing a missing non-existent path."""
    return str(tmp_path / "does_not_exist_abc123")


def test_clean_repo_fixture(clean_repo):
    """Clean repo fixture must report clean status with no errors."""
    status = get_repo_status(clean_repo)
    assert status.dirty is False
    assert status.staged == 0
    assert status.untracked == 0
    assert status.ahead == 0
    assert status.behind == 0
    assert status.has_changes is False
    assert status.branch == "main"
    assert status.error is None


def test_modified_tracked_repo_fixture(modified_tracked_repo):
    """Modified tracked file fixture must report dirty=True and has_changes=True."""
    status = get_repo_status(modified_tracked_repo)
    assert status.dirty is True
    assert status.has_changes is True
    assert status.staged == 0
    assert status.untracked == 0
    assert status.error is None


def test_staged_change_repo_fixture(staged_change_repo):
    """Staged change fixture must report staged=1 and has_changes=True."""
    status = get_repo_status(staged_change_repo)
    assert status.staged == 1
    assert status.dirty is False
    assert status.has_changes is True
    assert status.error is None


def test_untracked_file_repo_fixture(untracked_file_repo):
    """Untracked file fixture must report untracked=1, dirty=False, and has_changes=True."""
    status = get_repo_status(untracked_file_repo)
    assert status.untracked == 1
    assert status.dirty is False
    assert status.staged == 0
    assert status.has_changes is True
    assert status.error is None


def test_ahead_upstream_repo_fixture(ahead_upstream_repo):
    """Ahead of upstream clone fixture must report ahead=1, behind=0, and has_changes=True."""
    status = get_repo_status(ahead_upstream_repo)
    assert status.ahead == 1
    assert status.behind == 0
    assert status.dirty is False
    assert status.has_changes is True
    assert status.branch == "main"
    assert status.error is None


def test_non_git_directory_fixture(non_git_directory):
    """Non-git directory fixture must report 'Not a git repository' error without raising."""
    status = get_repo_status(non_git_directory)
    assert status.dirty is False
    assert status.has_changes is False
    assert status.error == "Not a git repository"


def test_missing_path_fixture(missing_path):
    """Missing path fixture must report path error without raising."""
    status = get_repo_status(missing_path)
    assert status.dirty is False
    assert status.has_changes is False
    assert "Path does not exist" in (status.error or "")


def test_cache_behavior_within_ttl_and_after_expiry(clean_repo):
    """Repeated calls within TTL do not re-invoke git; calls after TTL expiry do."""
    fake_time = 2000.0

    def current_time():
        return fake_time

    with patch("src.services.repo_status.time.time", side_effect=current_time):
        with patch("src.services.repo_status.subprocess.run", wraps=subprocess.run) as mock_git:
            # 1. First query: cache miss -> git executed once
            status1 = get_repo_status(clean_repo, ttl=10.0)
            assert mock_git.call_count == 1
            assert status1.last_checked == 2000.0

            # 2. Repeated query 3 seconds later (within 10s TTL): git NOT re-invoked
            fake_time = 2003.0
            status2 = get_repo_status(clean_repo, ttl=10.0)
            assert mock_git.call_count == 1
            assert status2.last_checked == 2000.0

            # 3. Repeated query 9.5 seconds later (within TTL): git NOT re-invoked
            fake_time = 2009.5
            status3 = get_repo_status(clean_repo, ttl=10.0)
            assert mock_git.call_count == 1
            assert status3.last_checked == 2000.0

            # 4. Query after 10.1 seconds (TTL expired): git MUST be re-invoked
            fake_time = 2010.1
            status4 = get_repo_status(clean_repo, ttl=10.0)
            assert mock_git.call_count == 2
            assert status4.last_checked == 2010.1


def test_deck_repo_list_backward_compatible(clean_repo, modified_tracked_repo):
    """Deck repo enrichment returns new status fields while preserving all existing consumer fields."""
    decks = [
        {"id": 1, "name": "Clean Deck", "color": "W", "path": clean_repo},
        {"id": 2, "name": "Dirty Deck", "color": "U", "path": modified_tracked_repo},
        {"id": 3, "name": "Virtual Deck", "color": "B"},
    ]

    enriched = enrich_decks_with_repo_status(decks)
    assert len(enriched) == 3

    # Existing fields unchanged
    assert enriched[0]["id"] == 1
    assert enriched[0]["name"] == "Clean Deck"
    assert enriched[0]["color"] == "W"

    assert enriched[1]["id"] == 2
    assert enriched[1]["name"] == "Dirty Deck"
    assert enriched[1]["color"] == "U"

    assert enriched[2]["id"] == 3
    assert enriched[2]["name"] == "Virtual Deck"
    assert enriched[2]["color"] == "B"

    # New status fields attached
    assert enriched[0]["dirty"] is False
    assert enriched[0]["has_changes"] is False
    assert enriched[0]["repo_status"]["dirty"] is False

    assert enriched[1]["dirty"] is True
    assert enriched[1]["has_changes"] is True
    assert enriched[1]["repo_status"]["dirty"] is True

    assert enriched[2]["dirty"] is False
    assert enriched[2]["has_changes"] is False
    assert enriched[2]["repo_status"]["error"] == "Path not specified"
