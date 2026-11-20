# Redis key patterns -> PostgreSQL schema

Source: read-only SCAN of `commander-lab-redis` db0 (7 keys: 6 strings, 1 list, no TTLs; point-in-time, one key per pattern) plus code reading on main (4209aa5). No Redis values were read or are stored here. Machine-readable twin: `mapping.json`.

The Redis->PG importer (`redis_pg_import.py`) is generic: it lands every key in `redis_import` (JSONB). Only two typed tables exist (`composition_presets`, `deck_compositions`, `db/orm_models.py`).

| Redis pattern | Type | Class | PostgreSQL target | Notes |
|---|---|---|---|---|
| `comp:presets` | string (JSON list, newest first) | durable | `composition_presets(seq, id, data JSONB, created_at)` | One row per element; `seq` ascending = reverse list order. Typed table exists; importer does not fan out into it yet. |
| `lab_tags` | string (JSON `{card:[tags]}`) | durable | none (gap) | Proposed `card_lab_tags(card_name, tag, source, pk(card_name, tag))`. |
| `lab_tags_source` | string (JSON `{card:{tag:src}}`) | durable | folds into `card_lab_tags.source` | Missing entry means `learned` (`lab_tags.py`). |
| `interactions:runs` | list (JSON per run, cap 200) | operational log | none | Optional `interaction_runs(id, run_id, created_at, data JSONB)`; import in reverse order. |
| `interaction_graph:collection` | string (JSON graph) | derived | none | Rebuilt by interactions rebuild; do not migrate. |
| `github_latest_version_stale` | string | cache | none | Stale fallback for GitHub fetch; do not migrate. |
| `github_changelog_stale` | string | cache | none | Same. |

## Documented but not observed
- `comp:deck:{registry_id}` -> `deck_compositions(registry_id, data JSONB, updated_at)`.
- `interaction_graph:deck:{registry_id}`, `cache:{ns}:{gen}:{key}`: derived/cache, no table.
- `decks`, `inventory`, `deck_status:{id}`, `card_tags:{name}`, `deck_categories:{registry_id}` (AGENTS.md): absent from the keyspace and no typed table exists. AGENTS.md may be stale or the keys renamed (`instance:*`, `card_meta:*` appear in `inventory_view.py`). Needs human confirmation.

## Caveats
- 7 keys, one per pattern: too small to generalise.
- Redis has no `requirepass`; restrict network access or add an ACL.
- Proposed DDL is a proposal only; nothing was applied.
