# Decision review: layout and data stores (2026-10-29)

Status: **architect decision**. Andrew delegated authority to change these.
This amends `docs/ARCHITECTURE.md` §2–§4 and §9. Where the two disagree,
this file wins.

| # | Approved choice | Verdict |
|---|---|---|
| 1 | `src/server` (Python) + `src/web` (SvelteKit) layout | **Keep** |
| 2 | Postgres as the single source of truth | **Keep** |
| 3 | pgvector as a rebuildable index | **Keep** |
| 4 | Apache AGE as a rebuildable graph index | **Change**: drop AGE and use plain edge tables + recursive CTEs |
| 5 | (implicit) app tables share a schema with the extensions | **Change**: app tables go in `public`, and the database `search_path` stops leading with `ag_catalog` |

## 1. Layout: keep

- It already exists. 387 files import `src.server.*`. Import-linter enforces
  domain purity on these module paths (`pyproject.toml`), and CI, Docker and
  compose all point at them. Moving to a conventional
  `src/commander_lab/` package or to top-level `server/` and `web/` would
  rename every import and change nothing in how the code runs.
- One repo with one backend, one frontend and one API contract is the right
  size for a single-user app. A monorepo tool (pnpm/turbo/nx) would add
  machinery without fixing any real problem.
- Caveat (not a change): `src/web/templates` and `src/web/static` still hold
  the legacy Jinja HUD next to the SvelteKit app. That is temporary. It goes
  away when `docs/ui-cutover-order.md` finishes. Don't add new Jinja there.

## 2. Postgres as source of truth: keep

- The data is relational and transactional: decks, deck_cards, instances
  with an append-only event log, profile snapshots, reviews. Draft-mode
  commits need atomic multi-table writes. Nothing in the roadmap needs a
  document store or a second database.
- The extra cost is small. Postgres uses about 60 MB of RAM on Tower now,
  which suits a shared Unraid box.
- Status, not decision: production still serves from Redis. The prod `app`
  has no `DATABASE_URL`, `PG_CUTOVER` is off, and the prod database holds no
  app tables. That makes now the cheapest time to fix #5.

## 3. pgvector: keep

- The corpus is about 30k cards and fewer than 1k decks, as 384-d bge-small
  vectors. Any engine works at this size. pgvector wins because similarity
  queries can join and filter in the same SQL as the truth tables ("similar
  cards I own, in this color identity, not in this deck"). Qdrant or an
  in-process `.npz` would need a second copy of that filter data.
- It is already built: migration a007, `infra/vector/`, the
  `index_rebuild vector` worker, and endpoints that return "unavailable"
  when the index is missing.
- pgvector is the most widely packaged Postgres extension. It needs no
  `shared_preload_libraries` and is available for every PG major.
  `pgvector/pgvector:pg17` is an official image, so CI can match prod
  (see follow-ups).

## 4. Apache AGE: change to edge tables + recursive CTEs

ARCHITECTURE.md §4.1 and §9 already list this as the fallback "behind the
same interface". This review takes the fallback now instead of keeping it in
reserve.

Reasons:

1. **No code depends on AGE.** No `ag_catalog`/`cypher` usage exists in
   `src/server`. `index_rebuild graph` is still "PR2". The production
   database has zero graphs (`ag_graph` has 0 rows). Removing it costs
   nothing today and gets more expensive once code depends on it.
2. **The queries don't need a graph engine.** Every consumer in §4.4 is a
   1–2 hop join, or a short path over a small table:
   - provenance ("where has this copy been") is
     `instance_events WHERE instance_id = ? ORDER BY ts`, a linear log
     rather than a graph search;
   - deck neighborhoods are deck → deck_cards → cards → card_tags or
     interaction edges, which is plain joins;
   - synergy is already a table, `card_interaction_edges`;
   - `SIMILAR_TO` is a pgvector top-k query, computed on read.
   At a few hundred thousand edges, recursive CTEs over indexed edge tables
   run in milliseconds.
3. **AGE is the riskiest part of the stack.** Its ASF board reports
   describe it as "ongoing, but low activity". Its paid core team was let go
   in October 2024, and from September 2024 to May 2025 nothing was
   committed to master. Releases are separate builds for each PG major. The
   PG18 line started at 1.7.0 with no upgrade path from earlier versions, so
   moving Postgres to a new major means changing AGE builds at the same
   time. We also have to compile AGE ourselves (`docker/db/Dockerfile`).
4. **AGE takes over global settings.** It requires
   `shared_preload_libraries=age` in every session, and its docs tell you to
   put `ag_catalog` first on `search_path`. That second requirement already
   caused #5.
5. **The cost is awkward code for no gain.** Cypher runs inside a SQL
   wrapper (`SELECT * FROM cypher('g', $$…$$) AS (v agtype)`), SQLAlchemy
   can't type it, and we would have to write and test an `agtype` parser.
   Edge tables work with the ORM, Alembic, import-linter and the existing
   test fixtures.

The new design is in the same spirit as the AGE plan:

- **Truth edges are already relational**: `deck_cards`, `card_instances`,
  `instance_events`, `card_tags`. Don't copy them into a second graph store.
- **Derived edges** go in ordinary tables written only by
  `src/server/workers/index_rebuild.py` (AGENT_RULES rule 1):
  `card_interaction_edges` (exists) and, if needed later,
  `card_similarity_edges(src, dst, model_id, score)` as a materialized
  top-k from pgvector. `derived_index_status` keeps a `graph` row for
  freshness.
- **Read interface**: `src/server/infra/graph/` contains named query
  functions (`provenance(instance_id)`, `deck_neighborhood(deck_id, hops)`,
  `card_neighborhood(oracle_id, hops)`). Each is a recursive CTE, and each
  returns plain dicts. Callers never see SQL. If a graph engine is ever
  justified, it can replace this module without touching callers.
- **When to revisit**: only if a real feature needs variable-length path
  patterns over more than about 10⁶ edges, or graph algorithms that SQL
  can't express. If that happens, evaluate AGE again first, then Memgraph.

## 5. Schema placement: change

Live finding on the dev database (`commander-lab-db` on the Framework): all
14 app tables, including `alembic_version`, `decks`, `cards` and
`card_instances`, were created in **`ag_catalog`**. That's because
`00-extensions.sql` sets the database `search_path` to
`ag_catalog, "$user", public`, and Alembic creates tables in the first
schema on that path. `docs/DERIVED_INDEXES.md` notes this without fixing it.
CI runs `postgres:16-alpine`, which has no AGE, so the same migrations
create the tables in `public` there. Dev and CI therefore have different
schemas.

Problems:

- Source-of-truth data sits in a schema that belongs to an extension.
  Running `DROP SCHEMA ag_catalog CASCADE` (the usual AGE cleanup) would
  delete all app data.
- AGE's own objects come first when Postgres resolves unqualified names.
- Dev, CI and prod don't match.

Decision: app tables live in `public`, and the database `search_path`
returns to the default `"$user", public`. No app connection sets
`ag_catalog`. The production database has no app tables yet, so this
change costs almost nothing before the prod cutover. After the cutover it
becomes a data migration.

## Follow-ups (cards, not done here)

These are listed in order, and all must land before the prod `PG_CUTOVER`.

1. **coder**: write a migration that moves app tables from `ag_catalog` to
   `public` where they exist (`ALTER TABLE … SET SCHEMA public`, a no-op
   when they're already in `public`). Pin the schema explicitly in Alembic
   (`version_table_schema="public"`) and make connections use
   `"$user", public`. Update `docs/DERIVED_INDEXES.md`.
2. **tower**: rebuild the db image without AGE. That means pgvector only,
   or the stock `pgvector/pgvector:pg17` image. Drop
   `shared_preload_libraries=age` and the `ag_catalog` `search_path` line in
   `docker/db/init/00-extensions.sql`. Run `DROP EXTENSION age` on the dev
   and prod databases, which are empty of graphs. This must come after #1
   on dev.
3. **coder**: change the CI Postgres service from `postgres:16-alpine` to
   `pgvector/pgvector:pg17` so the pgvector tests run in CI instead of
   being skipped.
4. **coder** (when the graph features in Slice 6 PR2 are picked up): build
   `src/server/infra/graph/` with recursive CTEs as described in §4, plus
   the `index_rebuild graph` edge rebuild.
5. **docs** (done in this PR): mark ARCHITECTURE.md §4.1/§4.4/§9,
   AGENT_RULES.md and the README storage line as amended by this file.
