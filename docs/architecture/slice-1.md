# Slice 1 — Import one deck, have/buy, first profile

Implements ARCHITECTURE.md §8 Slice 1 on top of the Slice 0 foundation
(src/server + src/web reorg, Postgres 17 + pgvector/AGE `db` service).

## What exists now

### Schema (Alembic `a003_slice1_core`)
DDL-constant migration in the style of a001/a002, per ARCHITECTURE.md §4.2:

- `cards` — one row per Oracle card (oracle_id PK), with type/mana fields the
  curve and archetype metrics need.
- `printings` — set/collector-number rows per card.
- `decks` — `archidekt:{id}` PK convention, source enum
  (`archidekt|brew|draft|manual`), content fingerprint, status.
- `deck_cards` — deck membership with board
  (`commander|main|sideboard|maybe|considering`), primary category, quantity.
- `card_instances` — physical collection registry; zero-loss columns carried
  from the Redis registry (`legacy_deck_ref`, acquired metadata, etc.),
  status enum `not_owned|in_mail|in_collection|in_deck`, optional `deck_id`
  binding.
- `instance_events` — append-only audit (`acquired|assigned|unassigned|
  mailed|traded|lost|migrated`).
- `profile_snapshots` — append-only; trigger
  (`import|game|rating|manual|override`), inputs, metrics (jsonb),
  `summary_text`.

### Import (one-way, Archidekt → Postgres)
- `domain/decks/deck_import.py` — pure normalizer: Archidekt payload →
  decks/deck_cards/cards dicts, board mapping, primary category, content
  fingerprint, folder-588380 → status `have` rule. No infra imports.
- `infra/services/import_service.py` — one transaction: upsert cards +
  printings, replace deck_cards, claim already-migrated instances whose
  `legacy_deck_ref` matches this deck, compute have/buy, append a
  `profile_snapshots` row only when the deck is new or its fingerprint
  changed (idempotent re-imports).
- `workers/importer.py` — CLI:
  `DATABASE_URL=... python -m src.server.workers.importer 5054882`
  (accepts an id or an archidekt.com/decks URL).

### Collection migration (Redis → Postgres, §9)
`scripts/migrate_instances_to_pg.py`:
- `--dry-run` — counts + unresolved card names, writes nothing.
- `--apply` — inserts instances idempotently (existing ids skipped), stubs
  `cards` rows for oracle_ids no deck import has created yet, writes
  `migrated` events, then rebinds instances whose deck was imported after an
  earlier apply.
- `--verify` — reconciliation report: Redis vs Postgres counts per
  (oracle_id, status).

### Have/buy math
`domain/inventory/have_buy.py` — `deck_cards` ⋈ `card_instances`:
have = min(need, copies bound to this deck + free copies in the collection),
pending from `in_mail`, buy = need − have − pending. Copies sleeved in other
decks are reported as `elsewhere` but not counted. `not_owned` placeholder
rows never count.

### Profile snapshot
`domain/profile/snapshot.py` — from the imported deck + collection:
mana curve (0..6 and 7+ buckets, lands vs spells, average MV non-land),
archetype mix (primary-category shares), and a deterministic plain-language
summary.

### API (`/api/v2`, typed pydantic responses in `src/server/app/api/`)
- `POST /api/v2/imports/archidekt` — import by id or URL.
- `GET /api/v2/decks` — deck list with have/buy totals.
- `GET /api/v2/decks/{id}` — per-line have/buy detail.
- `GET /api/v2/profile/latest` — latest profile snapshot.
All return 503 `db_unavailable` when `DATABASE_URL` is not configured.

### Web (SvelteKit, `src/web`)
- `/decks` — deck list (name, commanders, color identity, have/buy counts).
- `/decks/[id]` — per-card have/buy table with board/category grouping and
  the profile summary.
Data is fetched server-side from the FastAPI `/api/v2` endpoints; these
routes replace the Jinja deck list/deck page for imported decks.

## Verification (real data)
Against a tower Redis dump (24,446 keys; 5,608 instance records) and a
Postgres 17 test instance, for Archidekt deck 5054882 "The Return of the
King":

1. `migrate --dry-run`: 5,608 records, all resolvable, 0 unresolved names.
2. `migrate --apply` then `--verify`: 5,608 = 5,608, reconciliation OK per
   (oracle_id, status). Verified in both orders (import→migrate with rebind,
   and migrate→import).
3. Import: 100 cards / 92 distinct, commander Aragorn, the Uniter; have/buy
   need=100 have=100 buy=0 — matching the old UI's inventory rollup for the
   same deck against the same Redis dump (100 rows, all `have`).
4. Re-import is a no-op (fingerprint unchanged: `changed: false`, no second
   snapshot row).
5. `profile_snapshots` row: curve buckets [0,5,16,19,14,8,2,0], 36 lands,
   avg MV 3.16 non-land, archetype mix Land 36% / Ramp 16% / Legendary 14%,
   summary "…You own 100 of 100 cards — the deck is fully assembled."
6. `/api/v2/decks`, `/api/v2/decks/archidekt:5054882`,
   `/api/v2/profile/latest` all return 200 with the above data from a live
   uvicorn against Postgres.
7. `tests/test_slice1.py` (13 tests, sqlite-backed): normalizer, have/buy
   math, snapshot determinism, import idempotency, migration
   dry-run/apply/verify + rebind, API router contracts.
8. `npm run check` (0 errors) and `npm run build` pass in `src/web`.
