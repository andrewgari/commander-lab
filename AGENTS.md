# Commander Lab - Agent Guidelines

## Overview
Commander Lab is a Magic: The Gathering deck analytics, physical card inventory, and archetype tag management tool that integrates with the Archidekt API and Scryfall.

## Tech Stack
- Backend: Python 3.11+, FastAPI, Uvicorn
- Storage: Redis (caching decks, inventory, physical deck statuses, card archetype tags)
- Frontend: Jinja2 templates, Tailwind CSS (CDN), FontAwesome icons, Scryfall card imagery
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

## PR / completion process (kanban-dispatched workers)

Every card is completed against `completion_contract=andrewgari/commander-lab`, which gates
`kanban_complete` on CI success at the exact PR head SHA — do not treat "PR opened" as done.

1. Open a real PR against `main` (branch off `main` unless the card says otherwise).
2. Wait for GitHub Copilot's automated PR review (`copilot-pull-request-reviewer`) to post.
   Address every comment it raises (or, if a suggestion is wrong for this codebase, reply
   explaining why and leave a `kanban_comment` note) before proceeding — don't ignore a
   "changes recommended" verdict.
3. Wait for the `Test & Lint` CI check to go green on the PR's current head commit
   (branch protection on `main` requires it). Push fixes and re-wait if it fails.
4. Manually exercise the change locally before completing: `docker compose up -d` (or
   `uvicorn app:app --reload --port 8000` against a real/local Redis), hit the actual
   endpoint/UI path the card touches, and note what you ran + observed in the completion
   summary. A green CI run is not a substitute for this — CI here is `python -m unittest`
   against mocked Redis, not an end-to-end check.
5. Call `kanban_complete` with `metadata.published_pr` set to the PR URL and the summary
   including: PR URL, Copilot review outcome, CI run URL/status, and the manual test steps
   + observed result. Only then does the completion gate accept it (it re-checks CI at the
   exact head SHA server-side).

## Domain Conventions & Data Model
- **Physical Deck Statuses**: `Have` (physically assembled), `Virtual`, `Pending`, `Possible`.
- **Structural Categories**: `Commander`, `Sideboard`, `Maybeboard`, `Considering` (structural categories are preserved and excluded from thematic tag learning).
- **Active Physical Folder**: Folder ID `588380` contains primary active physical decks.
- **Redis Keys**:
  - `decks`: JSON array of all synced decks.
  - `inventory`: JSON dict of card inventory and deck appearances.
  - `deck_status:{deck_id}`: Physical status string.
  - `card_tags:{card_name}`: Learned and manual card tags.
