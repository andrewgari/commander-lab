# Redis key pattern → PostgreSQL mapping

Documentation only. No migration, schema, Redis or application change.

## Source and scope

- **Instance:** `commander-lab-redis`, **db0** (only db with keys).
- **Scan date:** 2026-11-04. **DBSIZE at scan: 24698** (`INFO keyspace`: `db0:keys=24698,expires=1`).
- **Method:** read-only. `SCAN` plus `TYPE`, `TTL` and `STRLEN`/`LLEN`/`SCARD`/`ZCARD`/`HLEN` run in a single Lua script. No values were read. Nothing was written and no container was restarted. Sizes are bytes for strings, element counts for collections.
- **Supersedes** the earlier 7-key version of this doc (that scan hit the wrong instance).
- **PostgreSQL side:** `src/server/infra/db/orm_models.py` on main (`63f36f3`), 32 tables. Schema is also managed by Alembic (`src/server/infra/db/migrations/`, `env.py` runs against `Base.metadata`; revisions such as `a011_review_documents.py` add tables, indexes and CHECK constraints), so migration compatibility must be checked against those revisions, not only the ORM. `redis_pg_import.py` adds the generic `redis_import` landing table.
- Pattern counts below sum to **24698** (checked in code against the key list from `redis-cli --scan`; 0 unmatched keys, 0 duplicates).

## Totals

| Status | Keys | Meaning |
|---|---|---|
| mapped | 1 | typed PG table exists for this shape |
| partial | 10600 | a typed table exists but needs id/name resolution or its importer has unverified coverage |
| unmapped | 14097 | no typed table |

| Class | Keys |
|---|---|
| durable (user/lab-owned data) | 5825 |
| derived (rebuildable from other data) | 10527 |
| cache (refetchable external data) | 8332 |
| ops (locks, heartbeats, seed flags, version counters) | 14 |

Only **one** key in the whole keyspace has a TTL: `cards:catalog:names` (~590,000 s left at scan). Everything else is persistent, so Redis is holding caches forever and nothing evicts them.

## PostgreSQL tables that matter here

`composition_presets`, `deck_compositions`, `decks`, `deck_cards`, `deck_links`, `cards`, `printings`, `card_instances`, `instance_events`, `card_tags` (oracle_id, tag, source, confidence), `card_notes`, `card_interaction_edges`, `reviews`, `review_jobs`, `review_documents`, `review_runs`, `review_run_steps`, `derived_index_status`, `profile_snapshots`, `player_profile_versions`, and `redis_import` (generic JSONB landing for any key; not a typed target, so it is never counted as "mapped").

## All patterns

Variable segments: `{deck id}` is `archidekt:<n>` or `moxfield:<id>` unless noted. Status: mapped / partial / unmapped. Class: durable / derived / cache / ops.

| Pattern | Redis type | Keys | TTL | Size (min-max) | PG target | Status | Class | Notes |
|---|---|---|---|---|---|---|---|---|
| `instance:{uuid}` | string | 5610 | none | 611-882 B | card_instances (+instance_events) | partial | durable | scripts/migrate_instances_to_pg.py exists (dry-run/apply/verify) but omits the `pending_removal` field set by `instances.set_pending_removal()`; `card_instances` has no column for it. Unresolved resync decisions would be lost and `idx:instances_pending_removal:*` could not be rebuilt. Blocker until preserved or explicitly resolved. |
| `idx:instances_by_card:{card name}` | set | 2440 | none | 1-4058 members | — (rebuild from card_instances) | unmapped | derived | Index; freshness tracked by derived_index_status. |
| `idx:card_name_to_oracle:{card name}` | string | 2438 | none | 36 B | — (join cards.name) | unmapped | derived | Name→oracle_id lookup; also the resolver needed to import name-keyed tags. |
| `idx:instances_by_set:{set}` | set | 261 | none | — | — (rebuild) | unmapped | derived |  |
| `idx:instances_by_deck:{registry id}` | set | 41 | none | 1-2 members | — (rebuild) | unmapped | derived |  |
| `idx:instances_pending_removal:{deck id}` | set | 4 | none | — | — | unmapped | durable | Rebuildable only if `pending_removal` is preserved from `instance:*` (not imported today); see `instance:{uuid}`. |
| `idx:instances_by_status:{status}` | set | 2 | none | — | — (rebuild) | unmapped | derived |  |
| `card_meta:{oracle_id uuid}` | string | 2440 | none | 114-675 B | cards (Scryfall mirror) | partial | derived | Scryfall facts; cards table is refreshed by sync. |
| `card_meta:{card name} (legacy)` | string | 2438 | none | 114-675 B | cards | partial | derived | Legacy name-keyed twin; scripts/migrate_card_meta_to_oracle_id.py backfilled the uuid form and leaves these in place. |
| `price:card:{card name}` | string | 4115 | none | 36-42 B | printings.prices (cache) | unmapped | cache | Refetchable price. |
| `card_interactions:{card name}` | string | 3849 | none | 343-1368 B | — (card_interaction_edges is the graph) | unmapped | cache | Tagger output cache (2.4 MB). 13 keys contain a second ':' in the card name (e.g. `Summon: Choco/Mog`), so split on the first ':' only. |
| `card_eval:card:{slug}` | string | 172 | none | 223-40726 B | — | unmapped | cache | External-data cache. |
| `card_eval:edhrec:{slug}` | string | 1 | none | — | — | unmapped | cache |  |
| `edhrec:commander:{slug}` | string | 52 | none | 120-45639 B | — | unmapped | cache | EDHREC fetch cache (1.7 MB). |
| `spellbook:fmc:{hash}` | string | 59 | none | 341 B-1.5 MB | — | unmapped | cache | Commander Spellbook cache, largest group by bytes (13 MB). |
| `embeddings:card:{model}` | hash | 1 | none | 3793 fields | — | unmapped | cache | Embedding cache; rebuildable. |
| `cards:catalog:names` | string | 1 | ~590000 s left at scan (~6.8 d) | 739 KB | — | unmapped | cache | The ONLY key with a TTL (INFO: expires=1). |
| `cards:autocomplete:{scope}:{query}` | string | 26 | none | 46-2753 B | — | unmapped | cache |  |
| `brew:commander_search:{query}` | string | 21 | none | 2-12437 B | — | unmapped | cache |  |
| `cache:routes:__gen` | string | 1 | none | 2 B | — | unmapped | cache | Generation counter for redis_cache.py. |
| `github_changelog_stale` | string | 1 | none | 6509 B | — | unmapped | cache |  |
| `github_latest_version_stale` | string | 1 | none | 7 B | — | unmapped | cache |  |
| `decks` | string | 1 | none | 270603 B (JSON, 58 decks) | decks + deck_cards + deck_links | partial | durable | Single JSON blob; typed tables exist. `scripts/migrate_collection_to_pg.py` (via `collection_migration.py`) already imports the `decks` blob and status overrides, and `redis_pg_import.py` handles typed provider-link imports. Coverage/cutover gaps are unverified here. See open question 1. |
| `deck_status:{deck id}` | string | 58 | none | 7-8 B | decks.status | partial | durable | Bare numeric Archidekt ids; the PG decks.id is `archidekt:{id}`. |
| `deck_status:moxfield:{id}` | string | 32 | none | 7 B | decks.status | partial | durable | Moxfield-sourced decks; id form needs a decision (decks.id has no moxfield prefix convention in the ORM docstring). |
| `lab_tags` | string | 1 | none | 105990 B (JSON {card:[tags]}) | card_tags | partial | durable | card_tags is keyed by oracle_id: needs name→oracle via idx:card_name_to_oracle. |
| `lab_tags_source` | string | 1 | none | 60 B (JSON) | card_tags.source | partial | durable | Provenance; folds into card_tags.source (missing = learned). |
| `tag_vocabulary` | string | 1 | none | 128184 B (JSON) | — | unmapped | derived | Tag vocabulary from sync; no table. |
| `tag_query_index` | string | 1 | none | 688185 B | — | unmapped | derived |  |
| `comp:presets` | string | 1 | none | 784 B (JSON array) | composition_presets | mapped | durable | One row per element. |
| `review:{deck id}` | string | 5 | none | 30-39 KB | reviews / review_documents | partial | durable | Latest review doc per deck. |
| `review:doc:{deck id}:{doc id}` | string | 9 | none | 30-40 KB | review_documents | partial | durable |  |
| `review:index:{deck id}` | zset | 5 | none | 1-5 members | review_documents (order by created_at) | partial | durable | History order. |
| `journal:deck:{deck id}` | string | 11 | none | 2.5-4.6 KB | — | unmapped | durable | Deck journal. No table. |
| `journal:review:open:{deck id}` | string | 3 | none | 4.9-5.4 KB | — | unmapped | durable |  |
| `journal:reviews:{deck id}` | list | 7 | none | 1-2 items | — | unmapped | durable |  |
| `personality:deck:{deck id}` | string | 58 | none | 6-19.5 KB | — (profile_snapshots/player_profile_versions are user-level, not per deck) | unmapped | derived | Computed per-deck profile; recomputable, confirm. |
| `personality:read:{deck id}` | string | 3 | none | — | — | unmapped | derived |  |
| `personality:heartbeat` | string | 1 | none | 121 B | — | unmapped | ops |  |
| `identity:deck:{deck id}` | string | 58 | none | 2-4.4 KB | — | unmapped | derived | Evaluation output. |
| `bracket:eval:{deck id}` | string | 58 | none | 2.8-6.6 KB | — | unmapped | derived | Evaluation output. |
| `bracket:eval:heartbeat` | string | 1 | none | 102 B | — | unmapped | ops |  |
| `interaction_graph:deck:{deck id}` | string | 58 | none | 7.8 KB-533 KB | — (card_interaction_edges) | unmapped | derived | 11 MB total; rebuild, do not migrate. |
| `interaction_graph:collection` | string | 1 | none | 485314 B | — (card_interaction_edges) | unmapped | derived |  |
| `interactions:runs` | list | 1 | none | 76 items | — | unmapped | ops | Rebuild run log. |
| `interactions:engine_names` | string | 1 | none | 60093 B | — | unmapped | derived |  |
| `deck_card_tags:{deck id}` | string | 4 | none | 7.7-12.4 KB | — | unmapped | durable | Contains user-added entries (`source="user"`) preserved across auto-tagging; not droppable.  |
| `deck_card_tags_version:{deck id}:{card}` | set | 225 | none | 1-12 members | — | unmapped | derived | Version bookkeeping. 1 key has a card name with a ':'. |
| `deck_tags:{deck id}` | string | 4 | none | 2-419 B | — | unmapped | durable | Contains user-added entries (`source="user"`) preserved across auto-tagging; not droppable.  |
| `deck_tags_version:{deck id}` | set | 3 | none | 3-8 members | — | unmapped | derived |  |
| `deck_genre:{archetypes|genres}` | string | 2 | none | ~4.5 KB | — | unmapped | durable | Persists user additions/edits/deletions; recreating only restores bundled seeds.  |
| `deck_categories:{registry id}` | string | 2 | none | 2 B | — | unmapped | durable | Per-deck category map; tiny today. |
| `archetypes` | string | 1 | none | 5474 B | — | unmapped | durable | Editable registry. |
| `archetypes:version` | string | 1 | none | 1 B | — | unmapped | ops |  |
| `archetype_sel:{deck id}` | string | 2 | none | 86-88 B | — | unmapped | durable |  |
| `genres` | string | 1 | none | 12877 B | — | unmapped | durable | Editable registry. |
| `genres:version` | string | 1 | none | 2 B | — | unmapped | ops |  |
| `categories:global` | string | 1 | none | 876 B | — | unmapped | durable |  |
| `categories:version` | string | 1 | none | 1 B | — | unmapped | ops |  |
| `staple_themes` | string | 1 | none | 1916 B | — | unmapped | durable |  |
| `staple_themes:seeded` | string | 1 | none | 1 B | — | unmapped | ops | Seed flag. |
| `role_tags` | string | 1 | none | 38999 B | — | unmapped | durable |  |
| `role_tags:legacy_seeded` | string | 1 | none | 1 B | — | unmapped | ops | Seed flag. |
| `role_tags:version` | string | 1 | none | — | — | unmapped | ops |  |
| `role_targets` | string | 1 | none | 1458 B | — | unmapped | durable |  |
| `autotag:tags` | string | 1 | none | 2609 B | — | unmapped | durable |  |
| `autotag:tags:seeded` | string | 1 | none | 1 B | — | unmapped | ops | Seed flag. |
| `composition:template:* / comp:deck:*` | — | 0 | — | — | deck_compositions (comp:deck:*) | mapped | durable | Typed table exists; 0 keys live. |
| `deck:sim:{deck id}` | string | 3 | none | 31-34 KB | — | unmapped | durable | Simulation runs. |
| `deck:sim:job:{deck id}` | string | 3 | none | 229 B | — | unmapped | ops |  |
| `deck:1` | string | 1 | none | 45 B | — | unmapped | ops | Unidentified; not found in code. Likely a test/legacy artifact, confirm. |
| `sim:focus:catalog / sim:focus:seeded` | string | 2 | none | 1-1414 B | — | unmapped | durable |  |
| `tuner:count:{deck id}` | string | 17 | none | 1-6559 B | — | unmapped | cache |  |
| `tuner:prose:{deck id}` | string | 15 | none | — | — | unmapped | cache |  |
| `tunerec:rec:{id}` | string | 15 | none | 14-82 KB | — | unmapped | durable | Saved Tune recommendations. |
| `tunerec:index:{deck id}` | list | 7 | none | 1-5 items | — | unmapped | durable |  |
| `tunerec:active:{deck id}` | string | 7 | none | 32 B | — | unmapped | durable |  |
| `theory:{deck id}:{hash}` | string | 5 | none | 12-15 KB | — | unmapped | durable |  |
| `theory_index:{deck id}` | zset | 3 | none | 1-2 members | — | unmapped | durable |  |
| `primer:{deck id}` | string | 2 | none | 15-39 KB | — | unmapped | durable | Deck writeup. |
| `primer:draft:{deck id}[:...]` | string | 5 | none | — | — | unmapped | durable | Writeup drafts (not the PG `drafts` feature). |
| `primer:staged_desc:{deck id}` | string | 2 | none | — | — | unmapped | durable |  |
| `reconstruct:session:{id}` | string | 3 | none | 4.7-29 KB | — | unmapped | durable |  |
| `reconstruct:sessions` | set | 1 | none | 3 members | — | unmapped | durable |  |
| `linked_accounts` | string | 1 | none | 1975 B | users / deck_links | unmapped | durable | Account links; confirm target. |
| **Total** | | **24698** | | | | | | |

## Mapping notes for the typed targets

- **`comp:presets` → `composition_presets`:** one array element → one row; `id` ← element `id`, `data` ← element, `seq` ascending is the reverse of list order (Redis index 0 is newest). Copy `created_at` explicitly if original timestamps matter.
- **`instance:{uuid}` → `card_instances`:** the migration script already exists and is idempotent. Not re-run as part of this task.
- **`lab_tags` / `lab_tags_source` → `card_tags`:** the PG key is `oracle_id`, Redis is keyed by card name. Resolve through `idx:card_name_to_oracle:{name}` (2,438 keys). Unresolvable names must be reported, not dropped silently. Missing source entry means `learned`.
- **`decks` → `decks` + `deck_cards` + `deck_links`:** `decks.id` is `archidekt:{n}`. `deck_status:*` has two id forms (bare numeric for Archidekt, `moxfield:<id>`), so an id normalisation rule is needed before `deck_status` can fill `decks.status`.
- **`review:*`:** `reviews` (older) and `review_documents`/`review_runs` (newer pipeline) both exist. Which one is the target for the 5+9 live docs is undecided; marked partial.
- **Name-keyed keys** (`card_meta`, `card_interactions`, `price:card`, `idx:*`) contain card names with `:` (e.g. `Summon: Choco/Mog`). Strip each pattern's complete fixed prefix (e.g. `price:card:`, `idx:card_name_to_oracle:`, `card_meta:`) and keep the remainder unchanged as the card name; never split on a colon, since `price:card:Summon: Choco/Mog` would otherwise yield the wrong name.

## Cleanup candidates (not actioned)

- 2,438 legacy `card_meta:{name}` keys duplicate the uuid-keyed ones.
- `deck:1` has no matching code.

## Open questions for Andrew

1. **Should `decks` become a PG table?** A `decks` table (plus `deck_cards`, `deck_links`) already exists in the ORM, but the live source of truth is still the single 270 KB `decks` JSON blob (58 decks) and nothing imports it. Recommendation: **yes**, make PG authoritative for deck identity first; `deck_status`, `review:*`, `journal:*`, `deck:sim:*`, `tunerec:*`, `primer:*` and `theory:*` all key off deck id and cannot migrate cleanly before it.
2. Which review table is canonical for `review:*` (`reviews` or `review_documents`)?
3. Do `journal:*`, `tunerec:*`, `theory:*`, `primer:*`, `reconstruct:*`, `deck:sim:*` get typed tables, or stay in a generic JSONB store (`redis_import`-style)?
4. Moxfield deck id form in `decks.id` / `deck_status`.
5. Drop the derived and cache classes at cutover, or carry them?

See `docs/redis-postgres-blockers.md` for the blocker list.
