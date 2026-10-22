# Commander Lab - Agent Guidelines

## Overview
Commander Lab is a Magic: The Gathering deck analytics, physical card inventory, and archetype tag management tool that integrates with the Archidekt API and Scryfall.

## Tech Stack
- Backend: Python 3.11+, FastAPI, Uvicorn
- Storage: Redis (caching decks, inventory, physical deck statuses, card archetype tags)
- Frontend: Jinja2 templates, shared HUD stylesheet `static/css/hud.css` (tokens in `docs/DESIGN_TOKENS.md`), HUD component kit (Jinja macros in `templates/partials/hud.html`, JS helpers in `static/js/hud.js`, live reference at `/styleguide`), inline SVG icons, Scryfall card imagery
- Containerization: Docker & Docker Compose

## Repository Structure
- `app.py`: Core FastAPI application and REST endpoints (`/`, `/decks`, `/inventory`, `/tags`, `/deck/{deck_name}`, `/api/*`).
- `sync.py`: Sync script pulling decks and cards from Archidekt v3 API and Scryfall into Redis.
- `templates/`: Jinja2 HTML templates (`base.html`, `decks.html`, `deck.html`, `inventory.html`, `tags.html`).
- `docs/`: Architecture and feature implementation documentation.
- `scripts/`: One-off utilities, auth setup, and sync helpers.
- `tests/`: API and integration verification scripts.
- `docker-compose.yml` & `Dockerfile`: Container environment definitions.
- `Makefile`: Common operations (`make sync`).

## Common Workflows
- Start containers: `docker compose up -d`
- Run local server: `uvicorn app:app --reload --port 8000`
- Run data sync: `make sync` (Docker) or `python sync.py` (local)
- Run tests: `python tests/<test_name>.py`

## Domain Conventions & Data Model
- **Physical Deck Statuses**: `Have` (physically assembled), `Virtual`, `Pending`, `Possible`.
- **Structural Categories**: `Commander`, `Sideboard`, `Maybeboard`, `Considering` (structural categories are preserved and excluded from thematic tag learning).
- **Active Physical Folder**: Folder ID `588380` contains primary active physical decks.
- **Redis Keys**:
  - `decks`: JSON array of all synced decks.
  - `inventory`: JSON dict of card inventory and deck appearances.
  - `deck_status:{deck_id}`: Physical status string.
  - `card_tags:{card_name}`: Learned and manual card tags.

## Changelog entries (Keep a Changelog 1.1.0)
`CHANGELOG.md` is the single source of release notes. The release workflow publishes the entries a merge adds under `## [Unreleased]` as the GitHub Release body; it never uses commit logs, PR titles/bodies or generated notes. Releases with no new entries say "Maintenance release, no user-facing changes."
- A PR with a user-facing change adds ONE line (or a few consolidated lines) under `[Unreleased]` in the same PR, under `### Added|Changed|Deprecated|Removed|Fixed|Security` only. Omit empty sections. If there is no user-facing change (refactor, CI, docs, tests, version bump) tick the PR template checkbox and add nothing.
- Write for users: describe the impact ("Simulate tab: failed runs now show ..."), not the implementation. Consolidate related changes into one entry.
- Never write merge/PR numbers/commit hashes/branch names/task ids/version-bump/CI lines.
- List deprecations and breaking changes explicitly (prefix breaking ones with `**BREAKING:**` under Changed/Removed).
- Dated sections are created by `python3 scripts/changelog.py promote vX.Y.Z` (`--dry-run` to preview); check with `python3 scripts/changelog.py validate`.
