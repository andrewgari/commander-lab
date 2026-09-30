# Commander Lab

Commander Lab is an MTG (Magic: The Gathering) deck analytics, physical card inventory, and archetype tag management tool integrating with Archidekt, Moxfield, Scryfall, EDHREC, and Commander Salt.

---

## Deck Repository Change Detection

Commander Lab tracks the local git repository status for each deck, providing glanceable status indicators on the Deck list view (`/decks` and `GET /api/decks`).

### Status Indicator States

Each deck card renders a responsive, zero-layout-shift status badge reflecting its underlying Git repository state:

| State | Badge | Description | Trigger Conditions |
| :--- | :--- | :--- | :--- |
| **Clean** | `CLEAN` (Green) | Repository working tree is clean with all commits synced upstream. | `has_changes == false`, `error == null`, no modified/staged/untracked files, and branch in sync with remote. |
| **Dirty (Uncommitted)** | `MODIFIED` / `DIRTY` (Amber) | Uncommitted modifications or staged changes exist in tracked files. | `dirty == true` or `staged > 0`. |
| **Untracked Only** | `UNTRACKED` (Sky Blue) | Working tree has new, untracked files without modified tracked files. | `dirty == false`, `staged == 0`, and `untracked > 0`. |
| **Ahead of Upstream** | `↑ {ahead} AHEAD` (Violet) | Local commits ready to push; working tree clean. | `ahead > 0`, `behind == 0`, and working tree clean. |
| **Behind Upstream** | `↓ {behind} BEHIND` (Indigo) | Upstream has new commits; pull needed; working tree clean. | `behind > 0`, `ahead == 0`, and working tree clean. |
| **Diverged** | `↑ {ahead} ↓ {behind}` (Purple/Amber) | Both local and upstream have independent commits requiring rebase or merge. | `ahead > 0` and `behind > 0`. |
| **Error / Unknown** | `ERROR` / `NO REPO` (Crimson) | Repository path missing, not a valid git repository, detached HEAD, or subprocess error. | `error != null` (e.g. `Not a git repository`, `Path not specified`, `Detached HEAD`). |

#### State Precedence
When multiple conditions exist concurrently (e.g., untracked files alongside unpushed commits), precedence is evaluated in the following order:
**Error > Dirty (Uncommitted) > Untracked > Diverged > Ahead > Behind > Clean**

### Cache TTL Configuration Knob

To prevent excessive subprocess spawns and maintain high request throughput across hundreds of deck repositories, status queries are cached in memory using an expiring thread-safe cache.

- **Environment Variable**: `REPO_STATUS_CACHE_TTL`
  - **Default**: `10.0` seconds
  - **Usage**: Set in `.env` or container environment (e.g. `REPO_STATUS_CACHE_TTL=30.0` or `REPO_STATUS_CACHE_TTL=0` to disable caching during debugging).
- **Runtime API**:
  - `get_cache_ttl() -> float`: Read the active default TTL in seconds.
  - `set_cache_ttl(seconds: float) -> None`: Dynamically update the default TTL at runtime.
  - `clear_repo_status_cache() -> None`: Immediately invalidate and clear all cached repository statuses.
- **Concurrency**: `get_batch_repo_status()` utilizes a bounded `ThreadPoolExecutor` (default 8 workers) to inspect uncached repositories concurrently while returning cached repositories immediately.

### API Integration & Backward Compatibility

`GET /api/decks` enriches each deck payload additively with repository metrics:

```json
{
  "id": 101,
  "name": "Kenrith Reanimator",
  "color": "WUBRG",
  "dirty": false,
  "has_changes": false,
  "repo_status": {
    "dirty": false,
    "staged": 0,
    "untracked": 0,
    "ahead": 0,
    "behind": 0,
    "branch": "main",
    "has_changes": false,
    "last_checked": 1727712000.0,
    "error": null
  }
}
```

All existing fields (`id`, `name`, `color`, `commanders`, `status`, `folder_id`) remain intact, preserving backward compatibility for all existing API consumers.

---

## Tech Stack & Architecture

- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Storage**: Redis (caching decks, inventory, physical deck statuses, card archetype tags)
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), FontAwesome icons, Scryfall card imagery
- **Containerization**: Docker & Docker Compose

## Quick Start

```bash
# Start containers
docker compose up -d

# Run local development server
uvicorn app:app --reload --port 8000

# Run test suite
python -m unittest discover -s tests
```
