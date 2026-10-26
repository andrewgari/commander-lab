# Redis key pattern → PostgreSQL mapping

Documentation only. No migration, schema, Redis or application change.

- **Redis source:** key-pattern export from task t_47a951b0 (`commander-lab-redis`, db0, point-in-time scan, names/types/TTLs/sizes only; no values). 7 keys, 7 patterns, all exact literals, 6 strings + 1 list, TTL = -1 (no expiry) for all.
- **PostgreSQL source:** schema created from `db/orm_models.py` (`Base.metadata.create_all`) plus the `DDL` in `redis_pg_import.py`, loaded into a throwaway `postgres:16` and read back via `information_schema.columns` / `pg_indexes`. Repo commit: `4209aa5` (main). The repo has no Alembic migrations, so these two sources are the whole schema.
- **Value shapes** were taken from the code that reads/writes each key, not from production values.

## PostgreSQL schema that exists today

| Table | Columns (type, null) | Keys / indexes |
|---|---|---|
| `composition_presets` | `seq` integer NOT NULL (serial); `id` varchar(255) NOT NULL; `data` jsonb NOT NULL; `created_at` timestamptz NOT NULL default now() | PK `seq`; UNIQUE `id` |
| `deck_compositions` | `registry_id` varchar(255) NOT NULL; `data` jsonb NOT NULL; `updated_at` timestamptz NOT NULL default now() | PK `registry_id` |
| `redis_import` (generic landing table, `redis_pg_import.py`) | `redis_key` text NOT NULL; `redis_type` text NOT NULL; `value` jsonb NOT NULL; `ttl_ms` bigint; `expires_at` timestamptz; `checksum` text NOT NULL; `imported_at` timestamptz NOT NULL default now() | PK `redis_key` |

`redis_import` accepts any Redis key verbatim (string/list/etc. decoded to JSONB), so every pattern below has a **landing** destination there. The "typed target" column says whether a purpose-built table exists.

## Summary

| # | Key pattern | Redis type | TTL | Typed target table | Status |
|---|---|---|---|---|---|
| 1 | `comp:presets` | string (JSON array) | none | `composition_presets` | **mapped** |
| 2 | `github_changelog_stale` | string (JSON array) | none | — | **unmapped** |
| 3 | `github_latest_version_stale` | string (plain text) | none | — | **unmapped** |
| 4 | `interaction_graph:collection` | string (JSON object) | none | — | **unmapped** |
| 5 | `interactions:runs` | list (JSON strings) | none | — | **unmapped** |
| 6 | `lab_tags` | string (JSON object) | none | — | **unmapped** |
| 7 | `lab_tags_source` | string (JSON object) | none | — | **unmapped** |

Result: 1 mapped, 6 unmapped, 0 ambiguous. Every pattern lands in `redis_import` (see last section).

## Details

All seven keys are exact literals, so there are **no key variables** and no PK/FK derived from key segments.

### 1. `comp:presets` — mapped → `composition_presets`
- Value: JSON array of preset dicts, newest first (`analytics/composition_store.py`: `user.insert(0, norm)`). Same shape as the PG store `analytics/composition_pg_store.py`, which already targets this table.
- Mapping (one array element → one row):

| Redis value | Column | Type | Note |
|---|---|---|---|
| element `["id"]` | `composition_presets.id` | varchar(255) | unique |
| whole element dict | `composition_presets.data` | jsonb | normalized preset |
| array position | `composition_presets.seq` | integer PK | Redis index 0 = newest = highest `seq`; insert in reverse order |
| element `["created_at"]` | `composition_presets.created_at` | timestamptz | column has `server_default now()`; copy the element value explicitly if original timestamps must be kept (otherwise `data` still carries it) |

- Confidence: high (same repo, same shape, PG store mirrors Redis store).
- Related, **not in the export:** `comp:deck:{registry_id}` → `deck_compositions` (`registry_id` ← key variable, PK; `data` ← JSON value; `updated_at` ← default now()).

### 2. `github_changelog_stale` — unmapped
Last-good copy of the GitHub releases list (`github_sync.py`; written beside the 600 s-TTL key `github_changelog`). Derived, re-fetchable cache of external data; no table models it.

### 3. `github_latest_version_stale` — unmapped
Last-good version string (`github_sync.py`), same reason as #2.

### 4. `interaction_graph:collection` — unmapped
Materialized collection graph (nodes + resource index), rebuilt by `analytics/interactions/service.py`. Derived data; no graph tables exist. Sibling `interaction_graph:deck:{registry_id}` follows the same pattern and is not in the export.

### 5. `interactions:runs` — unmapped
List of the last 200 rebuild run logs as JSON strings, newest first (`lpush` + `ltrim`). A natural fit would be a run-log table (run id, trigger, started_at, JSON payload, ordinal) but none exists; not guessed.

### 6. `lab_tags` — unmapped
JSON object `{card_name: [tag, ...]}` (`lab_tags.py`, `app.py`, `sync.py`). Durable user/learned data, so it needs a real table, but no tags table exists. **Needs a schema decision** (e.g. card/tag rows keyed by card name or oracle id).

### 7. `lab_tags_source` — unmapped
JSON object `{card_name: {tag: "learned"|"manual"}}` (`lab_tags.py`). Provenance for #6; should be designed together with it (likely a `source` column on a tag row). Not mappable independently.

## Landing mapping (all 7 keys, `redis_import`)

| Redis | Column | Type |
|---|---|---|
| key name | `redis_key` | text PK |
| TYPE | `redis_type` | text |
| value (string → parsed JSON or JSON string; list → JSON array) | `value` | jsonb |
| PTTL (null when -1) | `ttl_ms` | bigint |
| now + PTTL | `expires_at` | timestamptz |
| sha256 of canonical `{t, v}` | `checksum` | text |
| import time | `imported_at` | timestamptz |

## Items for human review
1. `AGENTS.md` lists `decks`, `inventory`, `deck_status:{id}`, `card_tags:{name}`, `deck_categories:{registry_id}` as Redis keys. None are in the keyspace and none have typed PG tables. They were not mapped because they are outside the export; `AGENTS.md` may be stale or they may have moved (e.g. to `card:{oracle_id}` / instance records).
2. `cache:{namespace}:{generation}:{key}` (`redis_cache.py`) has 0 observed keys; ephemeral cache, no PG counterpart expected.
3. The export is a 7-key snapshot; other patterns can appear at runtime (`comp:deck:*`, `interaction_graph:deck:*`, `card_interactions:*`, `spellbook:fmc:*`, `interactions:*` locks).

## Verification
Every table and column named in the schema table above was read back from `information_schema.columns` on a clean PostgreSQL 16 after `create_all` + the importer DDL (2026-10-23): 3 tables, 14 columns, indexes `composition_presets_pkey`, `composition_presets_id_key`, `deck_compositions_pkey`, `redis_import_pkey`. All columns referenced in the mappings exist with the types stated.

See also `docs/redis-postgres-blockers.md` for code-referenced patterns that lack PostgreSQL targets.
