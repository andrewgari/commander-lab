# Redis key patterns with no PostgreSQL target (blockers)

Companion to `docs/redis-postgres-key-mapping.md` (PR #281). That doc covers the 7 keys in the
exported fixture. This one covers patterns the code reads or writes at runtime that the export
does not show. Source: `origin/main` @ bd1c12f, grep of module-level key constants and
`r.get/set/...` call sites. No Redis access; names only.

## Scope: observed vs code-only

Live check (read-only, 2026-10-23): `commander-lab-redis` has `db0:keys=7` and no other db. Key
sizes: `comp:presets` 2 B, `lab_tags` 16 B, `lab_tags_source` 2 B, `interaction_graph:collection`
1366 B, `github_changelog_stale` 26089 B, `github_latest_version_stale` 7 B,
`interactions:runs` list of 3. `dump.rdb` is 16 KB; used memory 1.93 MB. No `comp:deck:*` keys.

- **Observed in production Redis (7):** `comp:presets` is mapped to `composition_presets`. The other
  6 are unmapped: B4 (`lab_tags`, `lab_tags_source`, durable, the real blockers), C1/C2
  (`interaction_graph:collection`, `interactions:runs`), C5 (the two `github_*_stale` keys).
- **Code-only (B1-B3, B5-B14):** the code reads or writes these, but none exist in the current
  production keyspace. They block any environment that writes them, and block retiring Redis.
  They are not production findings.
- **`inventory` / `card_tags:{name}`:** named in the task and AGENTS.md. No Redis key and no PG
  table exist for either. Kept as blockers pending owner confirmation of where this data lives.
- **Discrepancy:** the backup task t_04ea5f4e reported 23,912 keys and a 22 MB RDB. The live
  instance holds 7 keys and a 16 KB RDB, and the backup directory it names was not found on this
  host. Treat that figure as unreliable until someone confirms.

See `docs/redis-postgres-key-mapping.md` for the 7-key mapping.

## Current PostgreSQL targets

| Table | Typed for Redis pattern |
|---|---|
| `composition_presets` | `comp:presets` |
| `deck_compositions` | `comp:deck:{registry_id}` (in `db/orm_models.py`; no such keys in current Redis) |
| `redis_import` | generic landing for any key (not a typed target) |

Everything else below has no typed table. `redis_import` is a one-time import landing table and
should not be counted as a mapping for durable data.

## Blockers

Durable or user-facing data with no PostgreSQL table. These need a schema decision before any
migration.

| # | Pattern | Evidence | Why it blocks |
|---|---|---|---|
| B1 | `decks` (JSON array of all decks) | `registry.py:50,130,135`; read/written by ~70 call sites, including `analytics/*`, `scripts/*` | Core deck registry. Everything else keys off deck ids. No `decks` table. Blocks every deck-related mapping. |
| B2 | `deck_status:{id}` | `app.py`, `scripts/remove_orphan_decks.py`, tests | Physical status (`Have`/`Virtual`/`Pending`/`Possible`). Durable user state. No table. |
| B3 | `deck_categories:{registry_id}` | `app.py:1331,1358,1373,1673`; `inventory_view.py:123` | Durable per-deck category map. No table. |
| B4 | `lab_tags` / `lab_tags_source` (already in the export) | `lab_tags.py`, `inventory_view.py:112` | Durable tag data. Already listed as blocker in #281. |
| B5 | `review:{rid}`, `review:index:{rid}`, `review:doc:{rid}:{doc_id}`, `review:job:{job_id}` | `analytics/review_doc.py:38-41, 274-282` | Saved deck-review docs and history. Durable user output (zset history plus per-doc JSON). The 7-day `review:job:` TTL is the only expiring part. No table. |
| B6 | `journal:deck:*`, `journal:defaults`, `journal:review:open:*`, `journal:reviews:*` | `analytics/journal.py:45-48` | Deck journal and review history. Durable user notes. No table. |
| B7 | `personality:deck:*`, `personality:read:*`, `personality_read_job:*` | `analytics/personality_store.py`, `analytics/personality_read.py` | Computed profiles and read output. Durable. No table. |
| B8 | `deck:sim:*`, `deck:sim:job:*`, `deck:sim:run:*`, `deck:sim:focus:*`, `sim:focus:catalog`, `sim:standard_pod` | `analytics/sim_store.py`, `sim_service.py`, `sim_focus.py` | Simulation runs and saved pods. Durable. No table. |
| B9 | `card_review:job:*` | `analytics/card_review/jobs.py:13` | Job state for card review. Shape not checked; treat as durable until confirmed. |
| B10 | `archetypes`, `archetypes:version`, `archetype_sel:{rid}` | `analytics/archetypes.py:36-38` | Archetype definitions and per-deck selections. No table. |
| B11 | `categories:global`, `categories:version`, `categories:deck:*`, `cat:*` | `analytics/categories.py:30-33` | Category definitions and per-deck assignments. No table. |
| B12 | `identity:deck:*`, `bracket:eval:*`, `bracket:eval:pending`, `brackets:game_changers` | `analytics/identity.py:42`, `analytics/bracket_worker.py:51-55`, `analytics/brackets.py:235` | Evaluation results. Durable outputs. No table. |
| B13 | `composition:template:*` | `analytics/composition.py:44` | User-defined composition templates. Durable. No table. Note: `comp:presets` is mapped, but these templates are not. |
| B14 | `staple_themes`, `staple_themes:seeded`, `genres`, `genres:version` | `analytics/staple_theme_registry.py:35-36`, `analytics/genres.py:29-30` | Reference registries. Seeded once, then editable. Needs a schema call. |

Derived or cache data. Lower risk. No table is needed if the data can be rebuilt, but the
rebuild path must be confirmed.

| # | Pattern | Why it is derived |
|---|---|---|
| C1 | `interaction_graph:deck:*`, `interaction_graph:collection` (already in #281) | Rebuilt by `analytics/interactions/service.py`. |
| C2 | `interactions:runs` (already in #281), `interactions:engine_names`, `interactions:rebuild_lock`, `interactions:rebuild_rerun`, `interactions:otag_index` | Run log and locks. Ephemeral except the run log. |
| C3 | `card_interactions:*`, `spellbook:fmc:*` | Tagger and external-API caches. |
| C4 | `card_eval:card:*`, `card_eval:edhrec:*`, `edhrec:commander:*`, `embeddings:card:*` | External-data caches. |
| C5 | `github_changelog`, `github_changelog_stale`, `github_latest_version_stale` (already in #281) | Derived from GitHub. |
| C6 | `personality:pending`, `personality:heartbeat`, `bracket:eval:heartbeat`, `bracket:eval:wake` | Worker coordination. Not data. |
| C7 | `cache:{namespace}:{generation}:*` | `redis_cache.py`. Ephemeral. |

## Not in code at all

| Pattern | Finding |
|---|---|
| `inventory` | No literal `inventory` key is written. `inventory_view.build_inventory()` computes rows from `registry.list_decks()` plus `lab_tags` at request time. **AGENTS.md is stale on this.** |
| `card_tags:{name}` | No literal `card_tags:` key in the code. Tags use `lab_tags` (B4). **AGENTS.md is stale on this.** |

## Decisions needed

1. Is `decks` (B1) the source of truth for deck identity, and does it get a `decks` table? Most
   of B2–B8 keys by deck id, so this blocks them all.
2. Confirm AGENTS.md's `inventory` and `card_tags:{name}` entries and correct the doc.
3. Choose one approach for durable `review:*`, `journal:*`, `personality:*`, `deck:sim:*`
   output (typed tables vs one generic JSONB store like `redis_import`).
4. Confirm whether `redis_import` should stay a landing table only, or become a permanent
   fallback for the blockers above.

## Method and limits

- Patterns are from module-level constants and string literals in non-test `.py` files.
  Dynamic keys were matched by their literal prefix.
- Code-derived patterns were not found in live Redis. Only the 7-key snapshot above was checked live (counts, sizes; no values).
- Only `db/orm_models.py` and `redis_pg_import.py` were treated as PostgreSQL schema, as in #281.
