# Slice 1 notes — Archidekt import, have/buy split, first profile snapshot

Scope: docs/ARCHITECTURE.md §8 Slice 1. Canonical decisions live in the card
contract; this file records what landed and the few choices made in-flight.

## What shipped

- **Schema (§4.2)**: `cards`, `printings`, `decks`, `deck_cards`,
  `card_instances`, `instance_events`, `profile_snapshots` — SQLAlchemy 2
  models in `src/server/infra/db/models.py` (every table `owner_id NULL`),
  Alembic migration `a003_create_slice1_tables.py` (verified on PG16).
  All writes go through `src/server/infra/db/repositories.py` (boundary rule 4).
- **Importer**: `src/server/workers/importer.py` — one-way Archidekt import of
  ONE deck (`python -m src.server.workers.importer <id-or-url>`). Parses the
  Archidekt payload (boards preserved; commander on its own board so
  commander+main == the 100; first non-structural category becomes the
  thematic label), mirrors card facts from Scryfall `/cards/collection`
  (DB-first; facts only from DB or Scryfall, rule 3), upserts
  deck + deck_cards, and appends a `profile_snapshots` row (trigger `import`).
  Idempotent: re-import upserts cards, rebuilds deck_cards, appends a new
  snapshot. Cards Scryfall can't resolve import name-only with NULL facts —
  reported under `unresolved_cards`, never silently zeroed (rule 4).
  `providers/archidekt.py` is import-only now; the push/create code is
  deleted and `PushNotConfigured` guards the old paths.
- **Have/buy split**: `src/server/domain/inventory/have_buy.py` — pure math
  over `deck_cards ⋈ card_instances`: have = copies in_deck for the deck plus
  free in_collection pool (never double-counted), in_mail = pending,
  buy = max(0, required − have). Requirement boards are commander + main;
  maybe/considering are pools. Vocabulary matches the legacy
  `inventory_view.STATUS_LABELS` (parity test in
  `tests/test_slice1_have_buy.py`).
- **Profile snapshot**: `src/server/domain/profile/snapshot.py` — curve
  (0..6/7+ buckets like the old deck page, lands excluded by type_line, NULL
  cmc reported as `unavailable`), archetype mix from non-structural
  categories, color pip counts, ownership totals, and a deterministic
  template-rendered `summary_text` stored on the row. Snapshots are
  append-only history; latest = current profile.
- **API**: thin routers in `src/server/app/api/{decks,imports,instances,profile}.py`
  mounted under **`/api/v2/*`** (decision: the legacy Redis-backed
  `/api/decks` GET already exists, so the v2 prefix lets both coexist during
  the transition; the SvelteKit routes target v2). Endpoints:
  `GET /api/v2/decks`, `GET /api/v2/decks/{id}` (cards + have/buy),
  `GET /api/v2/decks/{id}/have-buy`, `POST /api/v2/imports/archidekt`,
  `GET /api/v2/instances`, `GET /api/v2/profile/latest`,
  `GET /api/v2/profile/snapshots`. 503 when `DATABASE_URL` is unset.
- **Instances migration**: `scripts/migrate_instances_to_pg.py` — one-time
  Redis→Postgres job for collection instances. Dry-run by default;
  `--apply` writes, inside one transaction, and prints a reconciliation
  report (counts per status, unmappable instances, unmapped-deck demotions).
  in_deck copies whose deck isn't imported yet are demoted to in_collection
  and reported loudly (never silently). Idempotent on instance id.
  **Prod cutover requires the reconciliation report — not done in Slice 1.**
- **SvelteKit**: `src/web/src/routes/decks/` (imported-deck list) and
  `src/web/src/routes/deck/[id]/` (have/buy table + profile snapshot panel),
  server-side loads via `src/lib/api.ts` (`API_URL` env, default
  `http://localhost:8000`). Jinja pages stay for non-imported decks.

## Tests

- `tests/test_slice1_importer.py` — payload parsing, end-to-end import on
  SQLite with stubbed fetchers, idempotency, snapshot creation, unresolved
  Scryfall cards.
- `tests/test_slice1_have_buy.py` — split math, no double counting, and the
  parity harness vs `inventory_view` (same deck + collection → same
  have/pending numbers as the old UI).
- `tests/test_slice1_profile.py` — curve buckets, archetype mix, summary text.
- `tests/test_slice1_api.py` — every v2 endpoint through FastAPI TestClient.
- `tests/test_slice1_migrate_instances.py` — migration dry-run plan, apply,
  reconciliation, idempotency, provenance events.

## Not in this slice (per contract)

Full collection migration + inventory/profile pages (Slice 2), review
pipeline, games, draft mode, vector/graph indexes, prod deployment/cutover,
deleting Jinja pages.
