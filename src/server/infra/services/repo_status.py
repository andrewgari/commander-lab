"""Git repository status detection service for Deck repositories.

Provides inspection of local git repository status using single-subprocess
porcelain v2 calls, with short TTL in-memory caching and bounded concurrency.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from pydantic import BaseModel, Field


# Default TTL for the in-memory repository status cache (in seconds).
DEFAULT_CACHE_TTL: float = float(os.getenv("REPO_STATUS_CACHE_TTL", "10.0"))


class RepoStatus(BaseModel):
    """Data model representing the local git status of a Deck repository."""

    dirty: bool = False
    staged: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    branch: Optional[str] = None
    has_changes: bool = False
    last_checked: float = Field(default_factory=time.time)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return status as a serializable dictionary."""
        return self.model_dump()

    def dict(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """Pydantic v1 backwards compatibility."""
        return self.model_dump(*args, **kwargs)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


# Thread-safe in-memory cache: resolved_path -> (RepoStatus, timestamp)
_cache: Dict[str, Tuple[RepoStatus, float]] = {}
_cache_lock = threading.Lock()


def get_cache_ttl() -> float:
    """Get the current default cache TTL in seconds."""
    return DEFAULT_CACHE_TTL


def set_cache_ttl(ttl: float) -> None:
    """Set the default cache TTL in seconds."""
    global DEFAULT_CACHE_TTL
    DEFAULT_CACHE_TTL = float(ttl)


def clear_repo_status_cache() -> None:
    """Clear all cached repository statuses."""
    with _cache_lock:
        _cache.clear()


def get_cache_size() -> int:
    """Return the number of entries currently in the cache."""
    with _cache_lock:
        return len(_cache)


def _maybe_cache(resolved_path: str, status: RepoStatus, ttl: float) -> None:
    """Store status in cache if ttl > 0."""
    if ttl > 0:
        with _cache_lock:
            _cache[resolved_path] = (status, time.time())


def _compute_repo_status(resolved_path: str) -> RepoStatus:
    """Execute git status --porcelain=v2 --branch and parse into RepoStatus.
    
    Catches all subprocess and OS errors, returning a populated error field
    without raising exceptions to caller.
    """
    now = time.time()
    if not os.path.exists(resolved_path):
        return RepoStatus(error="Path does not exist", last_checked=now)
    if not os.path.isdir(resolved_path):
        return RepoStatus(error="Path is not a directory", last_checked=now)

    try:
        proc = subprocess.run(
            ["git", "-C", resolved_path, "status", "--porcelain=v2", "--branch"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except FileNotFoundError:
        return RepoStatus(error="Git binary not found", last_checked=now)
    except PermissionError as exc:
        return RepoStatus(error=f"Permission denied: {exc}", last_checked=now)
    except subprocess.TimeoutExpired:
        return RepoStatus(error="Git command timed out", last_checked=now)
    except Exception as exc:
        return RepoStatus(error=f"Subprocess execution error: {exc}", last_checked=now)

    if proc.returncode != 0:
        err_msg = proc.stderr.strip()
        if "not a git repository" in err_msg.lower():
            err_msg = "Not a git repository"
        elif not err_msg:
            err_msg = f"Git exited with code {proc.returncode}"
        return RepoStatus(error=err_msg, last_checked=now)

    dirty = False
    staged = 0
    untracked = 0
    ahead = 0
    behind = 0
    branch: Optional[str] = None
    error: Optional[str] = None

    for line in proc.stdout.splitlines():
        if line.startswith("# branch.head "):
            head_val = line[len("# branch.head "):].strip()
            if head_val == "(detached)":
                branch = None
                error = "Detached HEAD"
            else:
                branch = head_val
        elif line.startswith("# branch.ab "):
            ab_val = line[len("# branch.ab "):].strip()
            for part in ab_val.split():
                if part.startswith("+"):
                    try:
                        ahead = int(part[1:])
                    except ValueError:
                        pass
                elif part.startswith("-"):
                    try:
                        behind = int(part[1:])
                    except ValueError:
                        pass
        elif line.startswith("1 "):
            tokens = line.split(None, 8)
            if len(tokens) >= 2:
                xy = tokens[1]
                if len(xy) >= 2:
                    if xy[0] != ".":
                        staged += 1
                    if xy[1] != ".":
                        dirty = True
        elif line.startswith("2 "):
            tokens = line.split(None, 9)
            if len(tokens) >= 2:
                xy = tokens[1]
                if len(xy) >= 2:
                    if xy[0] != ".":
                        staged += 1
                    if xy[1] != ".":
                        dirty = True
        elif line.startswith("u "):
            staged += 1
            dirty = True
        elif line.startswith("? "):
            untracked += 1

    has_changes = bool(dirty or staged > 0 or untracked > 0 or ahead > 0)
    return RepoStatus(
        dirty=dirty,
        staged=staged,
        untracked=untracked,
        ahead=ahead,
        behind=behind,
        branch=branch,
        has_changes=has_changes,
        last_checked=now,
        error=error,
    )


def get_repo_status(
    path: Union[str, Path, None],
    ttl: Optional[float] = None,
) -> RepoStatus:
    """Retrieve git status for a single repository path with TTL caching.
    
    Parameters:
        path: Path to the git repository, or None.
        ttl: Cache TTL in seconds. None uses DEFAULT_CACHE_TTL. 0 disables caching.
    
    Returns:
        RepoStatus: Data model with status metrics or error message. Never raises.
    """
    now = time.time()
    if not path:
        return RepoStatus(error="Path not specified", last_checked=now)

    resolved_path = os.path.abspath(str(path))
    effective_ttl = DEFAULT_CACHE_TTL if ttl is None else float(ttl)

    if effective_ttl > 0:
        with _cache_lock:
            cached = _cache.get(resolved_path)
            if cached is not None:
                cached_status, cached_time = cached
                if (now - cached_time) < effective_ttl:
                    return cached_status

    status = _compute_repo_status(resolved_path)
    _maybe_cache(resolved_path, status, effective_ttl)
    return status


def get_batch_repo_status(
    paths: Iterable[Union[str, Path, None]],
    max_workers: int = 8,
    ttl: Optional[float] = None,
) -> Dict[Union[str, Path, None], RepoStatus]:
    """Retrieve git status for multiple paths using bounded thread concurrency.
    
    Cached paths return immediately without dispatching thread tasks.
    """
    unique_paths = list(dict.fromkeys(paths))
    results: Dict[Union[str, Path, None], RepoStatus] = {}
    pending_to_fetch: List[Union[str, Path]] = []

    effective_ttl = DEFAULT_CACHE_TTL if ttl is None else float(ttl)
    now = time.time()

    for p in unique_paths:
        if not p:
            results[p] = RepoStatus(error="Path not specified", last_checked=now)
            continue

        resolved = os.path.abspath(str(p))
        if effective_ttl > 0:
            with _cache_lock:
                cached = _cache.get(resolved)
                if cached is not None:
                    cached_status, cached_time = cached
                    if (now - cached_time) < effective_ttl:
                        results[p] = cached_status
                        continue

        pending_to_fetch.append(p)

    if pending_to_fetch:
        worker_count = max(1, min(max_workers, len(pending_to_fetch)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_path = {
                executor.submit(get_repo_status, p, effective_ttl): p
                for p in pending_to_fetch
            }
            for future in concurrent.futures.as_completed(future_to_path):
                original_path = future_to_path[future]
                try:
                    results[original_path] = future.result()
                except Exception as exc:
                    results[original_path] = RepoStatus(
                        error=f"Error in status worker: {exc}",
                        last_checked=time.time(),
                    )

    return results


def resolve_deck_repo_path(deck: dict, base_dir: Optional[str] = None) -> Optional[str]:
    """Resolve the filesystem git repository path for a deck dictionary."""
    if not isinstance(deck, dict):
        return None

    for key in ("repo_path", "path", "git_path"):
        val = deck.get(key)
        if val and isinstance(val, (str, Path)):
            return str(val)

    configured_base = base_dir or os.getenv("DECKS_REPO_DIR") or os.getenv("DECKS_DIR")
    if configured_base and os.path.exists(configured_base):
        name = deck.get("name")
        if name:
            cand = os.path.join(configured_base, name)
            if os.path.exists(cand):
                return cand
        deck_id = deck.get("id")
        if deck_id is not None:
            cand = os.path.join(configured_base, str(deck_id))
            if os.path.exists(cand):
                return cand
        slug = deck.get("slug")
        if slug:
            cand = os.path.join(configured_base, slug)
            if os.path.exists(cand):
                return cand

    return None


def enrich_decks_with_repo_status(
    decks: List[Dict[str, Any]],
    base_dir: Optional[str] = None,
    max_workers: int = 8,
    ttl: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Enrich a list of deck dictionaries with repo_status.
    
    Additive and backward compatible. Adds:
    - deck['repo_status']: dict representation of RepoStatus
    - deck['has_changes']: bool
    - deck['dirty']: bool
    """
    if not decks:
        return decks

    deck_paths = [resolve_deck_repo_path(d, base_dir=base_dir) for d in decks]
    status_map = get_batch_repo_status(deck_paths, max_workers=max_workers, ttl=ttl)

    for deck, path in zip(decks, deck_paths):
        status = status_map.get(path) or get_repo_status(path, ttl=ttl)
        deck["repo_status"] = status.to_dict()
        deck["has_changes"] = status.has_changes
        deck["dirty"] = status.dirty

    return decks
