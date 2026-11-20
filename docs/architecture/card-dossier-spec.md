# Card Dossier Spec

One page per card: Andrew opens ONE card and sees everything about it, fed by real
PostgreSQL data. No placeholder panels — a panel renders only when its query returns
rows, otherwise it renders an explicit "no data yet" empty state.

Verified: 2026-10-25 @ origin/main `5b010f3`.

## 1. Card identity & URLs

- Canonical identity is the **Scryfall `oracle_id`** (printing-independent UUID).
  Verified pattern: `cards.py` keys canonical records as `card:{oracle_id}` with
  `idx:card_name_to_oracle:{name}`; `reconciliation.py` already groups by
  `oracle:{oracle_id}` with name fallback.
- Page route: `GET /card/{oracle_id}` (template `card.html`, extends `base.html`).
- Printing selection: `GET /card/{oracle_id}?printing={scryfall_id}`. The
  `scryfall_id` is the per-printing Scryfall UUID; it only picks which row of
  `card_printings` supplies art/set/rarity. All other panels are oracle-keyed and
  unaffected by printing. Unknown/absent `printing` falls back to the printings row
  flagged `is_default` (newest non-promo English printing, chosen at import).
- Name-based links from elsewhere in the app resolve through the existing
  name→oracle_id index (Redis `idx:card_name_to_oracle:*` today, `cards.name` unique
  index after migration) and 302 to `/card/{oracle_id}`.

## 2. Data model — existing vs NEW

### Already exists (verified)
- `db/engine.py`, `db/session.py`, `db/orm_models.py`, `alembic.ini`,
  `db/migrations/` with one version `a001_create_users_deck_links`
  (users, deck_links; composition_presets/deck_compositions rows exist in
  `orm_models.py`). SQLAlchemy 2.0, sqlite-aware engine for tests.
- `deck_links` PG table — provider-agnostic deck records (`registry_id`
  `"{source}:{source_id}"` per `registry.py`).
- Redis sources of truth to be mirrored into PG (established cutover pattern:
  `redis_pg_import.py`, `scripts/verify_cutover.py`, `PG_CUTOVER` flag — see
  Data Foundation note): `card:{oracle_id}` card records, `card_meta:{name}`
  oracle text/type/mana, `lab_tags` + `lab_tags_source`, `instance:*` ownership,
  `decks` registry list, `interaction_graph:*` computed edges.

### NEW tables (one Alembic migration, `a002_card_dossier`, in `db/migrations/versions/`)
| Table | Purpose | Key columns |
|---|---|---|
| `cards` | canonical oracle-level record | `oracle_id` PK, `name` unique, `mana_cost`, `type_line`, `oracle_text`, `colors`, `cmc` |
| `card_printings` | one row per physical printing | `scryfall_id` PK, `oracle_id` FK→cards, `set_code`, `set_name`, `collector_number`, `rarity`, `image_uri_normal`, `image_uri_art_crop`, `released_at`, `is_default` |
| `card_tags` | lab tags w/ provenance | `oracle_id` FK, `tag`, `source` (`learned`/`manual`, mirrors `lab_tags_source`), PK (`oracle_id`,`tag`) |
| `card_interaction_edges` | persisted synergy edges | `src_oracle_id` FK, `dst_oracle_id` FK, `vocab_version`, `reasons` JSONB (resource keys), `reason_labels` JSONB, `weight` int, PK (`src_oracle_id`,`dst_oracle_id`,`vocab_version`) |
| `card_notes` | Andrew's affinity/notes | `oracle_id` PK/FK, `affinity` smallint NULL (1–5), `notes` text, `updated_at` |
| `card_instances` | per-copy ownership (typed port of `instances.py`) | `instance_id` PK, `oracle_id` FK, `ownership_status`, `deck_id` NULL, `finish`, `condition` |
| `deck_cards` | decklist membership for ALL decks (virtual included) | `registry_id` FK→deck_links, `oracle_id` FK, `quantity`, `category` (Commander/Sideboard/…, preserved per AGENTS.md), PK (`registry_id`,`oracle_id`,`category`) |

**Discrepancy flag (must read):** the task body says "reuse existing
`card_interaction_edges`" — that table does NOT exist on main (`5b010f3`). Edges
are computed in-memory by `analytics/interactions/graph.py` (edge = shared
produce/consume resource keys; `weight = len(reasons)`; per-reason explanation
strings already generated at `graph.py:35` `_explain`) and cached only in Redis.
This spec therefore includes `card_interaction_edges` as a NEW table whose writer is
the existing interaction rebuild (`analytics/interactions/service.py`): on each
rebuild, persist the collection graph's edges for cards in Andrew's decks/collection
(same shape as `build_graph` edges: src, dst, reasons, weight, labels). No new graph
store; the in-memory builder stays the algorithm, PG becomes the read store.

Import: extend the Phase-1b cutover runbook per domain (BGSAVE →
`redis_pg_import.py` idempotent import + sha256 verify → `PG_CUTOVER` flag flip →
Redis read-only). Printings have no Redis source: one new importer calls Scryfall
`GET /cards/search?q=oracleid:{oracle_id}&unique=prints` lazily per card on first
dossier view, then caches rows; bulk backfill is a follow-up script, not page
blocking.

## 3. Endpoints (panel-by-panel)

One aggregate endpoint feeds the page; each panel maps 1:1 to a response key so a
panel can be lazy-loaded later without route changes.

`GET /api/card/{oracle_id}/dossier?printing={scryfall_id}` →

```jsonc
{
  "card": { // oracle text, mana cost, type line
    "oracle_id": "...", "name": "...", "mana_cost": "{2}{G}",
    "type_line": "Creature — ...", "oracle_text": "...", "colors": ["G"], "cmc": 3.0
  },
  "printing": { "scryfall_id": "...", "set_code": "...", "set_name": "...",
                "collector_number": "...", "rarity": "...",
                "image_uri_normal": "...", "image_uri_art_crop": "..." },
  "printings": [ /* all card_printings rows for oracle_id, same shape, ordered released_at desc */ ],
  "tags": [ {"tag": "ramp", "source": "learned"} ],
  "decks": [ {"registry_id": "archidekt:123", "name": "Ghired", "category": "main",
              "quantity": 1} ],           // deck NAMES from deck_links, never raw ids
  "synergy": [ {"oracle_id": "...", "name": "...", "direction": "out",
                "weight": 4, "reasons": ["tokens","+1/+1 counters"],
                "reason_text": "Ghired produces tokens; this consumes tokens"} ],
                                            // ORDER BY weight DESC, LIMIT 20; both directions merged
  "affinity": { "affinity": 4, "notes": "...", "updated_at": "..." } | null,
  "collection": { "owned": true, "copies": 2, "statuses": ["in_deck","in_collection"],
                  "finishes": ["nonfoil","foil"] }   // aggregated from card_instances
}
```

Mutations (small, follow existing write patterns):
- `PUT /api/card/{oracle_id}/notes` body `{affinity: int|null, notes: str}` → upsert `card_notes`.
- Tag add/remove reuses the existing lab-tags write path; dossier just reads.

Queries per panel (all indexed):
- printings: `WHERE oracle_id=:o ORDER BY released_at DESC`
- decks: `deck_cards ⨝ deck_links ON registry_id WHERE oracle_id=:o`
- synergy: `WHERE (src=:o OR dst=:o) AND vocab_version=:current ORDER BY weight DESC LIMIT 20`
- collection: `WHERE oracle_id=:o AND ownership_status IN ('in_collection','in_deck','in_mail')`

## 4. Page & sequencing

- `templates/card.html` (NEW), extends `base.html`, HUD components only
  (`partials/hud.html` macros), art from `image_uri_normal`, printing switcher =
  `<select>` over `printings` navigating to `?printing=`.
- Suggested implementation order (future coder cards): (1) a002 migration +
  importers; (2) dossier endpoint; (3) `card.html`; (4) edge persistence writer in
  `analytics/interactions/service.py`.

## 5. Risks / boundaries

- Deck membership for **virtual** decks lives only in Redis decklist blobs today;
  `deck_cards` import must run from `sync.py`'s synced decklists, or the "my decks"
  panel silently misses virtual decks.
- Edge persistence adds write volume on each rebuild; cap to edges touching
  owned/decked cards (collection graph can have 100k+ edges — `graph.py:8`).
- pgvector is NOT needed for any dossier panel; embeddings stay out of scope.
- SQLite tests: JSONB columns need `JSON` variant with PG `JSONB` cast
  (mirror existing orm_models sqlite-aware approach in `db/engine.py`).
- Non-goals honored: no deck-to-deck map, no deck-editor changes, no Neo4j.

## In-Scope Files (for WORK CONTRACT)
- `db/orm_models.py` (extend), `db/migrations/versions/a002_card_dossier.py` (NEW)
- `redis_pg_import.py` (extend), `scripts/backfill_card_printings.py` (NEW)
- `analytics/interactions/service.py` (edge persistence hook only)
- `app.py` (`/card/{oracle_id}`, `/api/card/{oracle_id}/dossier`, notes PUT)
- `templates/card.html` (NEW), `card_repository.py` (NEW, mirrors `deck_links_repository`)
