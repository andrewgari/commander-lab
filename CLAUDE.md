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

## Domain Conventions & Data Model
- **Physical Deck Statuses**: `Have` (physically assembled), `Virtual`, `Pending`, `Possible`.
- **Structural Categories**: `Commander`, `Sideboard`, `Maybeboard`, `Considering` (structural categories are preserved and excluded from thematic tag learning).
- **Active Physical Folder**: Folder ID `588380` contains primary active physical decks.
- **Redis Keys**:
  - `decks`: JSON array of all synced decks.
  - `inventory`: JSON dict of card inventory and deck appearances.
  - `deck_status:{deck_id}`: Physical status string.
  - `card_tags:{card_name}`: Learned and manual card tags.
