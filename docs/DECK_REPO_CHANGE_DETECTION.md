# Deck Repository Change Detection

## Overview

Commander Lab automatically monitors local Git repositories associated with Magic: The Gathering decks. The status detection service (`src/services/repo_status.py`, re-exported via `repo_status.py`) provides real-time change detection with configurable caching and bounded concurrency.

## Status Indicator States

The indicator covers 7 operational states with strict evaluation precedence:

1. **Clean**: No uncommitted modifications, no staged files, no untracked files, and local branch matches remote tracking branch (`has_changes: false`). Displayed as a subtle green badge (`CLEAN`).
2. **Dirty (Uncommitted)**: Uncommitted modifications in tracked files or staged additions/modifications (`dirty: true` or `staged > 0`). Displayed as an amber warning pill (`MODIFIED` or `DIRTY`).
3. **Untracked Only**: Working directory contains newly created files not yet tracked by Git (`untracked > 0`, `dirty: false`, `staged: 0`). Displayed as a sky-blue informational pill (`UNTRACKED`).
4. **Ahead of Upstream**: Local branch has committed changes that have not yet been pushed upstream (`ahead > 0`, `behind: 0`). Displayed as a violet sync-ready pill (`↑ {ahead} AHEAD`).
5. **Behind Upstream**: Upstream tracking branch has commits that have not been pulled into local branch (`behind > 0`, `ahead: 0`). Displayed as an indigo pill (`↓ {behind} BEHIND`).
6. **Diverged**: Both local and upstream branches have unique commits (`ahead > 0` and `behind > 0`). Displayed as a diverged badge (`↑ {ahead} ↓ {behind}`).
7. **Error / Unknown**: Git command failures, uninitialized paths, missing directories, detached HEAD, or subprocess execution errors. Displayed as a crimson warning badge (`ERROR`, `NOT A GIT REPO`, or `NO REPO`).

### State Precedence Order

When multiple conditions occur concurrently (e.g. untracked files and unpushed commits):
```
Error > Dirty (Uncommitted) > Untracked > Diverged > Ahead > Behind > Clean
```

## Performance & Cache Configuration

Git status inspection is backed by single-subprocess `git status --porcelain=v2 --branch` executions. To avoid process thrashing during page loads and list queries, results are cached in an in-memory thread-safe cache.

### Configuration Knob: `REPO_STATUS_CACHE_TTL`

- **Environment Variable**: `REPO_STATUS_CACHE_TTL`
- **Default Value**: `10.0` seconds
- **Behavior**:
  - Positive float (e.g. `10.0`): Status is cached for that duration. Calls within the window return the cached model without executing git subprocesses.
  - Zero (`0`): Disables caching completely, forcing a fresh git inspection on every call.
  - Negative values: Invalidate cache immediately.

### Runtime Controls

```python
from repo_status import get_cache_ttl, set_cache_ttl, clear_repo_status_cache

# Read current default TTL
ttl = get_cache_ttl()

# Change TTL at runtime
set_cache_ttl(30.0)

# Flush cached entries
clear_repo_status_cache()
```

## Concurrency & Batch Processing

The `get_batch_repo_status(paths, max_workers=8)` helper queries multiple repository paths concurrently using a `ThreadPoolExecutor`. Cached repository paths are returned immediately from the calling thread without submitting worker tasks, minimizing thread contention.

## REST API Contract (`GET /api/decks`)

`GET /api/decks` automatically enriches each deck dictionary with:
- `repo_status`: Dict serialized from `RepoStatus`
  - `dirty`: `bool`
  - `staged`: `int`
  - `untracked`: `int`
  - `ahead`: `int`
  - `behind`: `int`
  - `branch`: `str | None`
  - `has_changes`: `bool`
  - `last_checked`: `float` (epoch timestamp)
  - `error`: `str | None`
- `dirty`: `bool` (convenience shortcut for `repo_status.dirty`)
- `has_changes`: `bool` (convenience shortcut for `repo_status.has_changes`)

Existing deck schema fields (`id`, `name`, `color`, `commanders`, `status`, `folder_id`) remain unmodified to ensure complete backward compatibility.
