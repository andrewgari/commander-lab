# Commander Lab — Target Architecture

Status: **plan (v1.1), 2026-10-25** — no code, no migrations in this task.
v1 approved by Andrew 2026-10-25 with amendments (applied in v1.1): both apps
live under `src/` (`src/server/`, `src/web/`); dropped sync features are
deferred, not rejected (§5/§9); coder consults the Oracle on MTG questions
(§7); slices 0–6 are built back-to-back (§8).
Supersedes the Redis/Jinja monolith described in `AGENTS.md` ("Current state").
The old tag-system architecture doc moved to `docs/architecture/tag-system.md`.

Commander Lab becomes an MTG/Commander analysis app: Archidekt decks in, a
taste profile that evolves over time, inventory (have/buy), and reviews that
exist both as a styled document and a live dashboard rendered from the same
stored data.

## 0. Ratified decisions (Andrew)

These are inputs, not open questions:

- **Inputs**: Archidekt is a one-way import; after import Commander Lab owns
  decks/collection. Push back to Archidekt and two-way account linking are
  **deferred, not rejected** — the importer/provider interface stays
  extensible so they can return as additive work (see §5/§9). Scryfall,
  EDHREC, CommanderSalt are read-only references.
  Forge game logs are imported **on demand only**
  (import → analyze → notes → report → update profile).
- **Profile**: inferred from decks, ratings, and games; user-steerable
  overrides; full history kept; plain-language summary + charts (curve,
  archetype mix, power level).
- **Inventory**: important but secondary. Owned cards, have/buy split per
  deck, draft mode (combine decks / dismantle one to build another — nothing
  committed until the user commits), card provenance tracked between decks.
- **Delivery**: thin vertical slices touching decks+inventory+profile
  together, not two phases. Slice 1 = import Archidekt deck, show have/buy,
  update profile.
- **Stack**: Python + FastAPI + Postgres; reorganize the 40+ loose root
  `.py` files and split `app.py` (4,870 lines); frontend replaced with
  SvelteKit; Docker on Tower (API container + UI container). Reuse existing
  code where sensible (`analytics/`, `forge_sim/`, `providers/archidekt.py`).
- **Data stores**: Postgres is the source of truth (decks, inventory, games,
  profile history). Vector DB and Graph DB are **derived, rebuildable
  indexes**, chosen for best dashboards (products picked in §4).
- **Reviews**: fact pack → intent + player profile → multi-pass analysis →
  candidate retrieval → recommendations → verifier → writer → render. LLM
  does judgment/prose only; all card facts come from code/data; every LLM
  card name is validated against Scryfall; missing data renders an explicit
  "unavailable" + reason.

## 1. Goals / non-goals

**Goals**: the layout, stack, data stores, parity guarantees, slice roadmap,
and agent rules below — sufficient that any agent can start Slice 1 without
another design round.

**Non-goals**: implementation, running migrations, multi-user/accounts beyond
a single-user-ready design, choosing charting libraries, UI visual design.

## 2. Directory layout and module boundaries

```
commander-lab/
├── src/
│   ├── server/                 # Python backend (absorbs today's root *.py;
│   │                          #   src/integrations + src/shared fold into infra)
│   │   ├── app/                 # composition root — the only FastAPI-aware layer
│   │   │   ├── main.py              # app factory, middleware, router includes
│   │   │   ├── config.py            # env-driven settings (pydantic-settings)
│   │   │   ├── deps.py              # dependency injection (db session, clients)
│   │   │   └── api/             # one router module per resource
│   │   │       ├── decks.py  inventory.py  instances.py  tags.py
│   │   │       ├── profile.py  reviews.py    games.py    imports.py
│   │       ├── draft.py    analytics.py  reference.py  admin.py
│   │   ├── domain/              # pure logic; NO FastAPI, NO db/session imports
│   │   │   ├── analytics/           # moved from today's analytics/ (composition,
│   │   │   │                    #   interactions, brackets, tuner math, identity…)
│   │   │   ├── tags/                # autotag/ engine + tag_registry
│   │   │   ├── review/              # fact-pack builder, analysis passes, verifier
│   │   │   ├── profile/             # personality_profile, journal rollups, snapshots
│   │   │   ├── inventory/           # instances.py state machine, have/buy math
│   │   │   ├── draft/               # reconstruct_pools/builder, combine/dismantle
│   │   │   ├── forge/               # forge_sim log parsing (log → telemetry dicts)
│   │   │   └── intake/              # decklist_parser, user_intent, deck_intake
│   │   ├── infra/               # everything that touches a network/socket/file
│   │   │   ├── db/                  # engine, session, SQLAlchemy models, alembic/
│   │   │   ├── cache/               # Redis (cache + job queues ONLY, never truth)
│   │   │   ├── providers/           # archidekt (import), scryfall, edhrec,
│   │   │   │                    #   commandersalt, moxfield (read-only)
│   │   │   ├── llm/                 # oracle client, prompt assembly, schema validation
│   │   │   ├── vector/              # pgvector read/write, embedding runner
│   │   │   └── graph/               # Apache AGE read/write (openCypher)
│   │   └── workers/             # long-running/CLI entrypoints
│   │       ├── importer.py          # Archidekt one-way import job
│   │       ├── forge_import.py      # on-demand game-log import pipeline
│   │       ├── index_rebuild.py # vector + graph rebuild from Postgres
│   │       └── bracket_worker.py    # today's analytics/bracket_worker.py pattern
│   └── web/                     # SvelteKit app (replaces templates/ + static/)
│       ├── src/routes/          # /decks, /deck/[id]/..., /inventory, /profile…
│       ├── src/lib/             # api client, components, charts
│       └── tests/
├── docs/                        # specs (unchanged)
├── scripts/                     # one-offs (existing; new ones go here, not root)
├── tests/                       # pytest; mirrors src/server/ layout
├── docker-compose.yml           # dev: api + web + postgres + redis
├── docker-compose.prod.yml      # Tower/Portainer stack
└── AGENTS.md                    # agent rules (§7), current + target state
```

**Boundary rules** (enforced, see §7):

1. `domain/` imports nothing from `app/` or `infra/`. Checked by
   import-linter (CI) and a one-line grep in review.
2. `app/api/*` is thin: parse request → call domain/infra → serialize
   pydantic response. No business logic in routers.
3. `src/server/infra/llm/` is the only module that may call an LLM. `domain/review/`
   receives LLM output as data and validates it.
4. All persistence writes go through `infra/db/` repositories. No raw SQL
   outside `infra/db/`.
5. No new top-level `.py` files. Ever.

**Reuse map** (existing code → new home, mostly move-and-adapt):

| Today | New home | Notes |
|---|---|---|
| `analytics/` (60+ modules) | `src/server/domain/analytics/` | keep as-is; it is already the healthiest layer |
| `instances.py` | `src/server/domain/inventory/` | state machine + transitions are proven; only the Redis persistence changes |
| `providers/archidekt.py` | `src/server/infra/providers/` | import path only; push code deleted |
| `forge_sim/forge_game_telemetry.py` | `src/server/domain/forge/` | stdlib-only parser, keep verbatim |
| `reconstruct_*.py` | `src/server/domain/draft/` | pools/builder logic keep; session store Redis→Postgres |
| `autotag/` | `src/server/domain/tags/` | engine keep; Redis registry→Postgres |
| `src/integrations/` (scryfall, edhrec, commandersalt) | `src/server/infra/providers/` | folds into `src/server/infra/`; keep; already clean clients |
| `src/shared/http/` | `src/server/infra/http/` | folds into `src/server/infra/`; cache/retry/rate-limit, keep |
| `app.py` routes | `src/server/app/api/*` | split by resource; delete as ported |
| `templates/`, `static/` | `src/web/` (SvelteKit) | HUD design tokens (`docs/DESIGN_TOKENS.md`) carry over as CSS custom properties |
| `sync.py` | `src/server/workers/importer.py` | becomes one-way import; `sync_to_archidekt.py` deleted |

## 3. Stack and containers

- **API**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2 + Alembic,
  pydantic v2. Container `commander-lab-api`.
- **UI**: SvelteKit (adapter-node), TypeScript. Container
  `commander-lab-web`, proxies `/api/*` to the API. The Jinja HUD component
  kit is re-expressed as Svelte components over the same design tokens.
- **Postgres 17** in one container with two extensions (see §4). Container
  `commander-lab-db`, named volume on Tower (`/mnt/user/appdata/commander-lab/pgdata`).
- **Redis** stays, demoted: response caches and background-job queues only.
  Removing it later must not lose data.
- **Workers**: same image as the API, different command (today's
  bracket-worker pattern). No new deploy machinery.
- **Tower**: Portainer Git stack, same GitOps flow as today
  (`docs/GITOPS.md`). Compose adds `db` and `web`; `app` becomes `api`.
  Tower is an Unraid box running many services — the architecture must not
  add JVM-grade memory hogs (this constraint decides §4).

## 4. Data stores

### 4.1 Product picks (architect's choice, justified)

| Need | Pick | Why |
|---|---|---|
| Source of truth | **PostgreSQL 17** | ratified; transactional, proven, one container |
| Vector index | **pgvector** (in the same Postgres) | Tower-friendly: zero extra containers, ~100 MB RAM overhead; our corpus is small (≈30k cards, <1k decks) so HNSW in pgvector is milliseconds; today's embeddings are already 384-d CPU vectors (`analytics/embeddings.py`), so no GPU or embedding service is needed |
| Graph index | **Apache AGE** (in the same Postgres) | openCypher for card↔deck↔player↔tag↔game traversal and provenance paths; lives in the same container and volume; Neo4j/Memgraph would add a JVM service and a second backup story for what is a *derived* index on a shared Unraid box |

One Postgres container serves truth + both derived indexes. If AGE proves
unavailable for the pinned Postgres image, the fallback is plain edge tables
+ recursive CTEs behind the same `infra/graph/` interface — the rest of the
plan is unaffected.

### 4.2 Postgres schema (canonical; types abbreviated)

Card reference (Scryfall mirror, refreshed by sync jobs):

- `cards(oracle_id PK, name, type_line, oracle_text, colors[], cmc,
  keywords[], legalities jsonb, image_uri, etag, refreshed_at)`
- `printings(scryfall_id PK, oracle_id FK, set_code, collector_number,
  rarity, prices jsonb)`

Decks & collection (Commander Lab owns these):

- `decks(id PK, name, archidekt_id UNIQUE NULL, status
  ENUM(have,virtual,pending,possible), color_identity[], commanders jsonb,
  source ENUM(archidekt,brew,draft,manual), fingerprint, created_at,
  updated_at, archived_at NULL)`
- `deck_cards(deck_id FK, oracle_id FK, qty, board
  ENUM(commander,main,sideboard,maybe,considering), category NULL,
  PK(deck_id, oracle_id, board))`
- `card_instances(id PK, oracle_id FK, status
  ENUM(not_owned,in_mail,in_collection,in_deck), deck_id FK NULL, finish,
  condition, acquired_at, notes)` — one row per physical copy; carries
  today's `instances.py` state machine and `ALLOWED_TRANSITIONS`.
- `instance_events(id PK, instance_id FK, ts, kind
  ENUM(acquired,assigned,unassigned,mailed,traded,lost), from_deck NULL,
  to_deck NULL, draft_id NULL)` — append-only; this **is** provenance.
- `card_tags(oracle_id FK, tag, source ENUM(learned,manual), confidence,
  PK(oracle_id, tag, source))` + `tag_registry(id, name, kind, payload jsonb)`

Games (on-demand Forge imports):

- `game_imports(id PK, deck_id FK, imported_at, raw_log_uri, parser_version,
  status ENUM(parsed,failed), error NULL)`
- `games(id PK, import_id FK, deck_id FK, played_at, seat_count, result,
  turns, notes)`
- `game_telemetry(game_id FK, oracle_id FK NULL, metric, value jsonb)` —
  holds the parser output (`drawn/cast_turn/stuck` may be NULL; Forge logs
  don't carry draws — "unavailable" is a first-class value, per §0)

Profile (evolving taste):

- `profile_snapshots(id PK, created_at, trigger
  ENUM(import,game,rating,manual,override), inputs jsonb, metrics jsonb,
  summary_text)` — **append-only, never updated**; current profile = latest
  snapshot + active overrides. `metrics` holds curve prefs, archetype mix,
  power-level estimate.
- `profile_overrides(key PK, value jsonb, set_at)` — user steering; every
  override change also appends a snapshot so history is complete.
- `card_ratings(oracle_id FK, rating, rated_at)` and
  `deck_ratings(deck_id FK, rating, rated_at)` — feed the profile.

Reviews:

- `reviews(id PK, deck_id FK, version, fingerprint, status
  ENUM(complete,partial), fact_pack jsonb, analysis jsonb, doc jsonb,
  created_at)` — `doc` holds typed sections (today's Theory-doc pattern);
  the styled document and the dashboard both render from `fact_pack` +
  `doc`, so they can never disagree.
- `review_jobs(id PK, review_id FK, stage, status, error, ts)` — pipeline
  audit trail.

Ops:

- `derived_index_status(store ENUM(vector,graph), rebuilt_at,
  source_watermark, row_count)` — proves an index is fresh or stale.

Single-user-ready: no `users` table now; every table above gets an
`owner_id NULL` column so multi-user is an additive migration, not a
redesign. Redis → Postgres data migration (decks, inventory, tags, statuses)
is a scripted one-time job in Slice 1–2, not this task.

### 4.3 Vector index (pgvector) — what and how it rebuilds

- **What**: `card_embeddings(oracle_id PK, model_id, embedding vector(384))`
  from oracle text + learned tags; `deck_embeddings(deck_id PK, model_id,
  embedding vector(384))` as the normalized centroid of member cards
  (same math as today's card2vec centroid). Model stays bge-small-int8
  (CPU, already in the image). HNSW index per table.
- **Consumers**: "similar cards/decks", candidate retrieval in the review
  pipeline (§6), draft-mode suggestions.
- **Rebuild**: `src/server/workers/index_rebuild.py vector` truncates and re-embeds
  straight from `cards`/`decks`, then updates `derived_index_status`.
  Triggered manually or when `source_watermark` (max `updated_at`) lags.
  Never hand-edited; treat as a cache with a build step.

### 4.4 Graph index (Apache AGE) — what and how it rebuilds

- **Vertices**: `Card`, `Deck`, `Player` (one row today), `Tag`, `Game`,
  `Instance`.
- **Edges**: `CONTAINS` (deck→card, qty/board props), `OWNS`
  (player→instance), `INSTANCE_OF` (instance→card), `MOVED`
  (instance→deck, from `instance_events` — provenance paths),
  `TAGGED` (card→tag), `PLAYED` (deck→game), `APPEARED_IN`
  (card→game), `SIMILAR_TO` (card→card, deck→deck — top-k from pgvector),
  `SYNERGIZES` (card→card, from `analytics/interactions`).
- **Consumers**: provenance queries ("where has this copy been"), dashboard
  neighborhoods ("everything connected to this deck"), synergy overlays.
- **Rebuild**: `src/server/workers/index_rebuild.py graph` drops and reloads the AGE
  graph from Postgres (+ vector top-k), then updates
  `derived_index_status`. Same rule: derived, disposable, never truth.

## 5. Feature parity table

Nothing that works today is silently lost. **Keep** = logic ports with at
most persistence/UI adaptation. **Rebuild** = rewritten on the new stack.
**Drop** = deliberate, with reason.

| Current feature (where it lives) | Fate | Plan |
|---|---|---|
| Decks list/detail/manage pages (`app.py`, `deck*.html`) | **Rebuild** | SvelteKit pages over `/api/decks`; manage/resync-review flows port |
| Deck statuses Have/Virtual/Pending/Possible | **Keep** | enum carries over unchanged |
| Archidekt sync (`sync.py`, `providers/archidekt.py`) | **Rebuild** | one-way importer worker; push/`sync_to_archidekt` **dropped** (one-way decision) |
| Inventory & instances (`instances.py`, `inventory_view.py`) | **Keep** | state machine + transitions unchanged; Redis→`card_instances` + `instance_events` (adds provenance) |
| Have/buy split per deck | **Keep** | becomes Slice-1 headline view; math from `deck_cards` ⋈ `card_instances` |
| Tags + auto-tag (`tags.py`, `autotag/`, `lab_tags.py`) | **Keep** | engine ports; registry Redis→Postgres; high-confidence learning rule unchanged |
| Deep deck review (`analytics/review_*`, `docs/REVIEW_SPEC.md`) | **Rebuild** | replaced by the §6 pipeline; deterministic metrics modules are reused as the fact pack |
| Old deterministic `deck_review.py` GET endpoint | **Drop** | already flagged "no UI caller" in `docs/REVIEW_SPEC.md` |
| Forge sim (headless games, `analytics/sim_*`, forge daemon) | **Keep** | sim service + telemetry stay; UI rebuilt; storage of runs Redis→Postgres |
| Forge game ingest (`forge_sim/forge_game_telemetry.py`) | **Keep** | parser verbatim; becomes on-demand import pipeline (Slice 4) |
| Play-style / personality (`analytics/personality*`) | **Rebuild** | folded into Profile (snapshots + overrides + charts); `user_intent.py` feeds intent input |
| Brew / reconstruct (`reconstruct*.py`, `docs/RECONSTRUCT_*.md`) | **Keep** | pools/builder/rating logic ports; session state Redis→Postgres; UI rebuilt |
| Tuneup draft / Tuner (`analytics/tuner.py`, `tune_store.py`) | **Keep** | scoring math (edges/fit/EDHREC/played-with) keep; draft flow unifies with Slice-5 draft mode |
| Linked accounts (`linked_accounts.py`) | **Drop as sync; Keep as import** | no more two-way account linking; Archidekt/Moxfield become read-only import sources |
| Composition + presets (`analytics/composition*`) | **Keep** | Command Zone template math + presets port; UI rebuilt |
| Interactions / engines / combos (`analytics/interactions/`) | **Keep** | nightly tagger keeps worker pattern; results also emitted as `SYNERGIZES` graph edges |
| Genres / archetypes / staple themes | **Keep** | registries port Redis→Postgres; genre match feeds review fact pack |
| Journal & check-ins (`analytics/journal.py`) | **Keep** | becomes profile/game history input; UI rebuilt |
| Brackets (`analytics/bracket*`, bracket-worker) | **Keep** | evaluator + off-request worker pattern unchanged |
| card2vec / mech_similar (`analytics/card2vec.py`, `.npz` artifacts) | **Keep → supersede** | keep as scoring signals until pgvector `SIMILAR_TO` matches coverage (Slice 6), then retire artifacts |
| EDHREC / CommanderSalt / Scryfall clients (`src/integrations/`) | **Keep** | read-only reference, unchanged |
| Deck intake (`deck_intake.py`, `decklist_parser.py`) | **Keep** | parsers port; entry point becomes import endpoints |
| Deck export (`deck_export.py`) | **Keep** | text/Archidekt-format export stays |
| Primer / write-up generation (`analytics/deck_writeup/`) | **Keep** | ports after Slice 3 reuses the review LLM path |
| Identity / pillars (`analytics/identity.py`, pillar fit) | **Keep** | bge-small pillar-fit math carries over to pgvector embeddings |
| GitHub sync (`github_sync.py`), TCGPlayer (`tcgplayer.py`), salvage, deck_repair, deck_genre | **Keep** | low-traffic utilities port as-is or fold into nearest module |
| Jinja frontend + HUD (`templates/`, `static/`) | **Rebuild** | SvelteKit; HUD tokens + component kit re-expressed as Svelte; `/styleguide` rebuilt first |
| Redis as primary store | **Drop** | demoted to cache/queues; Postgres takes truth |

## 6. Review pipeline (document + dashboard from one record)

```
┌ fact pack (code only) ─ deterministic metrics: composition, curve/tempo,
│   genre match, interactions, brackets, weak performers, inventory have/buy
▼
┌ intent + profile ─ user_intent answers + latest profile snapshot + overrides
▼
┌ multi-pass analysis (code) ─ per-section passes over the fact pack;
│   candidate retrieval via pgvector (similar owned cards) + tags + EDHREC
▼
┌ recommendations (code) ─ ranked adds/cuts with deterministic scores
▼
┌ verifier (code) ─ every card name in LLM input/output checked against
│   Scryfall; unknown names rejected; missing data marked "unavailable"+reason
▼
┌ writer (LLM, via infra/llm) ─ judgment + prose ONLY over verified facts;
│   schema-validated output (today's Theory-doc typed-section pattern)
▼
└ render ─ same stored review row → styled document AND live dashboard
```

Hard rules: the LLM never computes a number, never invents a card, never
sees unverified data; an offline LLM degrades the review to facts + an
"explanation offline" note (today's REVIEW_SPEC core rule, extended).

## 7. Agent coding rules (short, enforceable)

The canonical file is `AGENT_RULES.md` at the repo root (every agent reads
it; `AGENTS.md` links to it). Summary:

1. Postgres is the only source of truth. Redis = cache/queues. Vector and
   graph stores are rebuildable indexes — never write to them outside
   `workers/index_rebuild.py`.
2. `src/server/domain/` must not import FastAPI, SQLAlchemy, or `src/server/infra/*`
   (import-linter in CI).
3. All card facts come from the DB or Scryfall. LLM output is prose/judgment
   only; validate every LLM card name against Scryfall before storing.
4. Missing data renders "unavailable" + reason — never guessed, never None
   dressed up as 0.
5. Numbers and labels are deterministic code. If the LLM could have made it
   up, it doesn't ship.
6. No new root-level `.py` files; new endpoints go in `src/server/app/api/` with
   typed pydantic responses; new logic in the matching `domain/` module.
7. Work in thin vertical slices that touch decks+inventory+profile together;
   every slice ships with tests and a docs update.
8. Uncommitted work stays uncommitted: draft mode writes nothing to
   canonical tables until the user commits (staging tables only).
9. Changelog discipline per `AGENTS.md` (one user-facing line or the
   no-user-facing checkbox).

## 8. Slice roadmap

Each slice is vertical (decks+inventory+profile), shippable, and verified on
Tower behind the existing GitOps flow. Slices are sequential; parity ports
(§5) are scheduled inside the slice that needs them.

### Slice 0 — Foundations (repo reorg, no user-facing change)
Move root modules into `src/server/` layout (existing `src/integrations`
and `src/shared` fold into `src/server/infra/`); split `app.py` into routers;
Postgres + Alembic in compose; SvelteKit shell (`src/web/`) serving `/styleguide`
from design tokens; import-linter + CI green; Redis→Postgres migration
scripts written and dry-run (not run against prod until Slice 1).
*Acceptance*: `docker compose up` brings up api+web+db+redis; all existing
pytest suites pass unmodified against the reorg; no feature removed.

### Slice 1 — Import one deck, have/buy, first profile
Archidekt one-way import of a deck into Postgres; collection instances
migrated; deck page shows have/buy split; first `profile_snapshots` row
computed from the imported deck + collection; SvelteKit deck list + deck page
replace their Jinja counterparts for imported decks.
*Acceptance*: import a real Archidekt deck → it appears in Postgres with
correct cards/categories → deck page shows have/buy counts matching the old
UI's numbers for the same deck → a profile snapshot exists with curve +
archetype-mix metrics and renders a plain-language summary.

### Slice 2 — Collection & profile v1
Full collection import/migration (all decks, all instances with provenance
events seeded from current state); inventory page in SvelteKit; profile page
with summary + charts (curve, archetype mix, power level) + override
controls; overrides append snapshots.
*Acceptance*: every card copy in today's Redis inventory exists exactly once
in `card_instances` (counts reconciled); editing an override creates a new
snapshot; profile history is browsable.

### Slice 3 — Review pipeline v1
§6 pipeline for one deck: fact pack from ported analytics modules, candidate
retrieval (tags + EDHREC first pass), LLM writer with Scryfall validation,
stored review rendered as both document and dashboard panels.
*Acceptance*: rebuild a review twice on an unchanged deck → identical facts;
every card name in the rendered review resolves on Scryfall; kill the LLM →
review still renders with "explanation offline"; document and dashboard show
the same numbers.

### Slice 4 — Games on demand
Forge log import (parser unchanged) → `game_imports`/`games`/`game_telemetry`
→ analysis notes → game report → profile snapshot update.
*Acceptance*: import a verbose Forge log → games and telemetry rows exist
(draw/stuck fields explicitly "unavailable") → report renders → profile
snapshot created with `trigger=game`; re-importing the same log is
idempotent (no duplicate games).

### Slice 5 — Draft mode
Combine decks / dismantle one to build another; all changes in staging
tables; commit applies `deck_cards` changes + `instance_events` provenance in
one transaction; tuner weak-performer and reconstruct pools power
suggestions.
*Acceptance*: build a draft combining two decks → nothing in `decks`,
`deck_cards`, or `card_instances` changes until Commit → after commit,
provenance paths are queryable ("this copy moved A→B"); Abandon leaves zero
residue.

### Slice 6 — Derived indexes & similarity dashboards
pgvector + AGE populated by `index_rebuild.py`; similar-cards/decks panels;
provenance and synergy graph views; candidate retrieval in reviews switches
to vector+graph; card2vec/mech_similar artifacts retired once coverage
matches.
*Acceptance*: `derived_index_status` shows both stores fresh; a similarity
panel and a provenance query answer from the derived stores; drop the stores,
rebuild, and panels return identical results (proving they're derived).

### Later (parity tail, each its own card)
Composition/presets UI, interactions UI, genres/archetypes/staple themes UI,
journal/check-in UI, brackets UI, primer/write-up, identity/pillars UI,
export buttons — all ported per §5 as **Keep** items, scheduled by usage.

## 9. Risks and open questions

- **AGE packaging**: Apache AGE needs a Postgres image that carries the
  extension; if pinning is painful, fall back to edge tables + recursive
  CTEs behind `infra/graph/` (decided in Slice 0, cheap to defer).
- **Migration rehearsal**: Redis→Postgres needs a dry-run + reconciliation
  report before prod cutover (counts per key family), same discipline as
  today's `scripts/_verify_reconciliation.py`.
- **SvelteKit parity cost**: the HUD kit is large; `/styleguide` first keeps
  the visual contract testable while pages port.
- **LLM access**: review writer depends on the oracle profile
  (`ORACLE_URL`/`ORACLE_KEY` already in prod env); offline behavior is
  specified, so this is a degradation path, not a blocker.

## 10. Current-state notes (pre-reorg, until Slice 0 lands)

### Read/Write Switch (`DATA_ACCESS_MODE`)

`datastore.py` wraps the Redis client used by `app.py` and `sync.py`.

- `DATA_ACCESS_MODE=readwrite` (default): unchanged behaviour.
- `DATA_ACCESS_MODE=readonly`: reads pass through; any mutating Redis command (including via pipelines) raises `WritesDisabledError` before reaching Redis, logs a warning, and the API returns HTTP 503 `{"error": "writes_disabled"}`.
- Any other value raises `ValueError` (fail loudly). The flag is read on every call.
- Scripts under `scripts/` use their own clients and are not covered.
