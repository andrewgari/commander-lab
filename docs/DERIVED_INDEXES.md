# Derived indexes (Slice 6)

ARCHITECTURE.md §4.3/§4.4/§7.1 is canonical. This file records the shipped
state.

## PR1 (this PR): pgvector store only

- **Tables** (migration `a007_derived_vector_indexes`):
  `card_embeddings(oracle_id PK, model_id, embedding vector(384), embedded_at)`,
  `deck_embeddings(deck_id PK, model_id, embedding vector(384), embedded_at)`,
  `derived_index_status(store PK, rebuilt_at, source_watermark, row_count)`.
  HNSW index per embedding table (cosine ops). The embedding tables are not
  ORM-mapped (the `vector` type is PostgreSQL-only); all access is raw SQL in
  `src/server/infra/vector/`. Note: like every app table since slice 0, these
  land in the first schema on the database-level `search_path`
  (`ag_catalog, "$user", public`, set by `docker/db/init/00-extensions.sql`).
- **Rebuild**: `python -m src.server.workers.index_rebuild vector` truncates
  and re-embeds from `cards`/`decks` in ONE transaction, then upserts
  `derived_index_status(store='vector', rebuilt_at, source_watermark =
  max(cards.refreshed_at, decks.updated_at), row_count)`. It is the ONLY
  writer of these tables (§7.1). Embedding text = name + type line + oracle
  text + keywords + learned Lab Tags (source='learned' only, read from Redis;
  a Postgres `card_tags` table is a later slice). Model: bge-small int8,
  384-d, CPU (`OnnxEmbedder`). Deck embeddings are the normalized centroid of
  member-card embeddings (card2vec centroid math; boards
  commander/main/sideboard/maybe, basic lands excluded, each card once).
- **Reads**: `GET /api/v2/analytics/similar-cards/{oracle_id}?k=`,
  `GET /api/v2/analytics/similar-decks/{deck_id}?k=`,
  `GET /api/v2/analytics/derived-index-status`. An unindexed card/deck or a
  missing store answers `status: "unavailable"` with the reason — never an
  empty list dressed up as a result (AGENT_RULES.md §4).
- **Freshness**: a store is `fresh` when its recorded `source_watermark`
  covers the current max `cards.refreshed_at` / `decks.updated_at`.
- **Tests**: `tests/test_index_rebuild.py` — pure math/text tests run
  everywhere; the determinism/freshness/endpoint tests need real PostgreSQL
  with pgvector (CI runs them on the digest-pinned `pgvector/pgvector:pg17` service,
  matching the prod major, and a dedicated step fails the build if any are
  skipped; run locally against the `commander-lab-db` image:
  `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55446/commander_lab
  python -m pytest tests/test_index_rebuild.py`). The drop-and-rebuild test
  proves the store is derived: identical embedding rows and identical
  similarity results after `DROP TABLE` + rebuild.

## Deferred to PR2+ (follow-up commits on the same card)

- Apache AGE graph store (`index_rebuild.py graph`), `derived_index_status`
  row for `graph`.
- SvelteKit similar-cards/decks panels, provenance and synergy views.
- Review-pipeline candidate retrieval switch to vector+graph.
- card2vec/mech_similar `.npz` retirement after coverage parity.
