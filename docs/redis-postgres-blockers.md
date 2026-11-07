# Redis key patterns with no PostgreSQL target (blockers)

Companion to `docs/redis-postgres-key-mapping.md`. Rebuilt from the live instance; the earlier version was based on a 7-key scan of the wrong instance.

- **Instance:** `commander-lab-redis`, **db0**. **DBSIZE at scan: 24698**. **Scan date: 2026-11-04.**
- Read-only (`SCAN` + `TYPE`/`TTL`/size). No values read, nothing written.
- Full pattern table (all 24698 keys, type, TTL, target, class) is in the mapping doc. This doc only lists what blocks a Redis retirement.

## What blocks

Blockers are **durable** patterns (user- or lab-owned data that cannot be rebuilt) with no fully typed PG target. Derived, cache and ops keys do not block; they can be rebuilt after cutover.

38 patterns / 5824 keys are durable and not yet mapped:

| # | Pattern | Keys | Type | Closest PG table | Status | Note |
|---|---|---|---|---|---|---|
| B1 | `decks` | 1 | string | decks + deck_cards + deck_links | partial | Single JSON blob; typed tables exist. `scripts/migrate_collection_to_pg.py` already imports it with status overrides; coverage gaps unverified. See open question 1. |
| B2 | `deck_status:{deck id}` | 58 | string | decks.status | partial | Bare numeric Archidekt ids; the PG decks.id is `archidekt:{id}`. |
| B3 | `deck_status:moxfield:{id}` | 32 | string | decks.status | partial | Moxfield-sourced decks; id form needs a decision (decks.id has no moxfield prefix convention in the ORM docstring). |
| B4 | `lab_tags` | 1 | string | card_tags | partial | card_tags is keyed by oracle_id: needs name→oracle via idx:card_name_to_oracle. |
| B5 | `lab_tags_source` | 1 | string | card_tags.source | partial | Provenance; folds into card_tags.source (missing = learned). |
| B6 | `review:{deck id}` | 5 | string | reviews / review_documents | partial | Latest review doc per deck. |
| B7 | `review:doc:{deck id}:{doc id}` | 9 | string | review_documents | partial |  |
| B8 | `review:index:{deck id}` | 5 | zset | review_documents (order by created_at) | partial | History order. |
| B9 | `journal:deck:{deck id}` | 11 | string | — | unmapped | Deck journal. No table. |
| B10 | `journal:review:open:{deck id}` | 3 | string | — | unmapped |  |
| B11 | `journal:reviews:{deck id}` | 7 | list | — | unmapped |  |
| B12 | `deck_categories:{registry id}` | 2 | string | — | unmapped | Per-deck category map; tiny today. |
| B13 | `archetypes` | 1 | string | — | unmapped | Editable registry. |
| B14 | `archetype_sel:{deck id}` | 2 | string | — | unmapped |  |
| B15 | `genres` | 1 | string | — | unmapped | Editable registry. |
| B16 | `categories:global` | 1 | string | — | unmapped |  |
| B17 | `staple_themes` | 1 | string | — | unmapped |  |
| B18 | `role_tags` | 1 | string | — | unmapped |  |
| B19 | `role_targets` | 1 | string | — | unmapped |  |
| B20 | `autotag:tags` | 1 | string | — | unmapped |  |
| B21 | `deck:sim:{deck id}` | 3 | string | — | unmapped | Simulation runs. |
| B22 | `sim:focus:catalog / sim:focus:seeded` | 2 | string | — | unmapped |  |
| B23 | `tunerec:rec:{id}` | 15 | string | — | unmapped | Saved Tune recommendations. |
| B24 | `tunerec:index:{deck id}` | 7 | list | — | unmapped |  |
| B25 | `tunerec:active:{deck id}` | 7 | string | — | unmapped |  |
| B26 | `theory:{deck id}:{hash}` | 5 | string | — | unmapped |  |
| B27 | `theory_index:{deck id}` | 3 | zset | — | unmapped |  |
| B28 | `primer:{deck id}` | 2 | string | — | unmapped | Deck writeup. |
| B29 | `primer:draft:{deck id}[:...]` | 5 | string | — | unmapped | Writeup drafts (not the PG `drafts` feature). |
| B30 | `primer:staged_desc:{deck id}` | 2 | string | — | unmapped |  |
| B31 | `reconstruct:session:{id}` | 3 | string | — | unmapped |  |
| B32 | `reconstruct:sessions` | 1 | set | — | unmapped |  |
| B33 | `linked_accounts` | 1 | string | users / deck_links | unmapped | Account links; confirm target. |
| B34 | `instance:{uuid}` | 5610 | string | card_instances | partial | Importer omits `pending_removal`; see mapping doc. |
| B35 | `idx:instances_pending_removal:{deck id}` | 4 | set | — | unmapped | Rebuildable only after `pending_removal` is preserved. |
| B36 | `deck_card_tags:{deck id}` | 4 | string | — | unmapped | Holds user-added tags (`source="user"`). |
| B37 | `deck_tags:{deck id}` | 4 | string | — | unmapped | Holds user-added tags (`source="user"`). |
| B38 | `deck_genre:{archetypes/genres}` | 2 | string | — | unmapped | User-edited vocabulary registries. |

Of these, the ones that block the most others are:

1. **`decks`** (single 270 KB JSON blob, 58 decks). `decks`/`deck_cards`/`deck_links` tables exist and `scripts/migrate_collection_to_pg.py` already imports the blob; remaining coverage/cutover gaps are unverified. Almost every per-deck pattern keys off the deck id, so this goes first.
2. **`lab_tags` / `lab_tags_source`** (106 KB). `card_tags` exists but is oracle_id-keyed; import needs name→oracle resolution through `idx:card_name_to_oracle:*`.
3. **`deck_status:*`** (90 keys, two id forms: bare Archidekt number and `moxfield:<id>`). Folds into `decks.status` once id normalisation is decided.
4. **`review:*`** (19 keys): two candidate target tables (`reviews`, `review_documents`).
5. **Registries with no table:** `archetypes`, `genres`, `categories:global`, `staple_themes`, `role_tags`, `role_targets`, `autotag:tags`, `sim:focus:*`, `linked_accounts`. Small, editable, seeded; need a schema call (one generic `registry(name, data jsonb)` table would cover them all).
6. **User-added deck tags and genre registries:** `deck_tags:*`, `deck_card_tags:*`, `deck_genre:*` hold user entries and are not droppable. Their `*_version:*` sets stay derived.
7. **`instance:*` importer gap:** `pending_removal` is not carried over.
8. **Per-deck user output with no table:** `journal:*`, `tunerec:*`, `theory:*`, `primer:*`, `reconstruct:*`, `deck:sim:*`, `archetype_sel:*`, `deck_categories:*`.

## Not blockers (rebuildable), by key count

| Pattern | Keys | Class |
|---|---|---|
| `idx:instances_by_card:{card name}` | 2440 | derived |
| `idx:card_name_to_oracle:{card name}` | 2438 | derived |
| `idx:instances_by_set:{set}` | 261 | derived |
| `idx:instances_by_deck:{registry id}` | 41 | derived |
| `idx:instances_pending_removal:{deck id}` | 4 | derived |
| `idx:instances_by_status:{status}` | 2 | derived |
| `card_meta:{oracle_id uuid}` | 2440 | derived |
| `card_meta:{card name} (legacy)` | 2438 | derived |
| `price:card:{card name}` | 4115 | cache |
| `card_interactions:{card name}` | 3849 | cache |
| `card_eval:card:{slug}` | 172 | cache |
| `card_eval:edhrec:{slug}` | 1 | cache |
| `edhrec:commander:{slug}` | 52 | cache |
| `spellbook:fmc:{hash}` | 59 | cache |
| `embeddings:card:{model}` | 1 | cache |
| `cards:catalog:names` | 1 | cache |
| `cards:autocomplete:{scope}:{query}` | 26 | cache |
| `brew:commander_search:{query}` | 21 | cache |
| `cache:routes:__gen` | 1 | cache |
| `github_changelog_stale` | 1 | cache |
| `github_latest_version_stale` | 1 | cache |
| `tag_vocabulary` | 1 | derived |
| `tag_query_index` | 1 | derived |
| `personality:deck:{deck id}` | 58 | derived |
| `personality:read:{deck id}` | 3 | derived |
| `identity:deck:{deck id}` | 58 | derived |
| `bracket:eval:{deck id}` | 58 | derived |
| `interaction_graph:deck:{deck id}` | 58 | derived |
| `interaction_graph:collection` | 1 | derived |
| `interactions:engine_names` | 1 | derived |
| `deck_card_tags_version:{deck id}:{card}` | 225 | derived |
| `deck_tags_version:{deck id}` | 3 | derived |
| `tuner:count:{deck id}` | 17 | cache |
| `tuner:prose:{deck id}` | 15 | cache |

Notes:
- `card_meta:*` is partly covered by `cards` (Scryfall mirror) and is refetchable.
- `interaction_graph:*` is 11.8 MB and `spellbook:fmc:*` 13 MB, so most of the dataset by bytes is derived. Skipping them keeps the migration small.
- `instance:*` (5,610) is the largest durable group and is **partial** (`card_instances`; `scripts/migrate_instances_to_pg.py` exists but drops `pending_removal`).
- Mapped durable: `comp:presets`, `comp:deck:*` (0 live keys).

## Previous claims corrected

| Earlier claim | Reality at scan |
|---|---|
| Keyspace is 7 keys, 16 KB RDB | 24698 keys; backup RDB is 22 MB |
| `inventory` is a Redis key / listed in AGENTS.md | No `inventory` key and no code writes one. Inventory is built from `instance:*`, `card_meta:*`, `idx:*` and `lab_tags` |
| `card_tags:{name}` exists | 0 keys; tags live in `lab_tags` / `lab_tags_source` (and PG `card_tags`) |
| `deck_status`, `deck_categories` absent | Present: 90 and 2 keys |
| B5-B14 are "code-only, not in production" | Most are live (`review`, `journal`, `personality`, `deck:sim`, `archetypes`, `genres`, `categories`, `staple_themes`, `identity`, `bracket:eval`). Not live: `card_review:job:*`, `composition:template:*`, `brackets:game_changers`, `bracket:eval:pending`, `personality:pending`, `review:job:*`, `sim:standard_pod` |

## Decisions needed (in order)

1. **Should `decks` become the authoritative PG table?** Recommendation: yes; import the blob into `decks`/`deck_cards`/`deck_links` first.
2. Moxfield deck id form in `decks.id` and `deck_status`.
3. Canonical review table (`reviews` vs `review_documents`).
4. Typed tables vs one generic JSONB registry for the small durable groups in items 5-6 above.
5. Whether derived/cache/ops keys are dropped or carried at cutover (recommendation: drop, rebuild).
6. Whether `redis_import` stays a landing-only table.

## Method and limits

- Enumeration grouped keys by literal prefix and id shape; the patterns partition the keyspace exactly (sum = 24698).
- "Live" means seen in the scan; "durable/derived/cache" is judged from the code that reads/writes the prefix and is a judgement, not a measured property.
- `docs/redis_pg/mapping.md` still describes the 7-key snapshot and is out of scope here; it should be refreshed or removed in a follow-up.
- Redis has no `requirepass`.
