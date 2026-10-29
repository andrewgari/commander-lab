"""Slice 6 PR1: derived vector index tables (ARCHITECTURE.md §4.2/§4.3, §7.1).

The pgvector store is a REBUILDABLE INDEX, never truth: the only writer is
``src/server/workers/index_rebuild.py`` (truncate + re-embed from
``cards``/``decks``). These tables may be dropped and rebuilt at any time.

- `card_embeddings(oracle_id PK, model_id, embedding vector(384))` — bge-small
  int8 embeddings of oracle text + learned tags.
- `deck_embeddings(deck_id PK, model_id, embedding vector(384))` — normalized
  centroid of member-card embeddings (same math as the card2vec centroid).
- `derived_index_status(store, rebuilt_at, source_watermark, row_count)` —
  proves the index is fresh or stale. `store` is 'vector' today; 'graph'
  arrives with the AGE slice (§4.4).

HNSW index per embedding table (cosine ops; pgvector 0.8.2 in the db image).

The embedding tables are intentionally NOT in orm_models.py: the `vector`
column type is PostgreSQL-only and the SQLite used by unit tests could not
create them. All access goes through `src/server/infra/vector/` with raw SQL.
`derived_index_status` is portable, so it does get an ORM row.

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001–a005.

NOTE: down_revision is the two current heads (a005_draft_mode +
a004_slice2_profile_overrides), making this revision the merge point while
the dedicated a006_merge_heads merge revision (separate PR) is in flight.
Whichever lands second re-points its down_revision at the other.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

EMBEDDING_DIM = 384

CREATE_EXTENSION_DDL = """
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public
"""

CARD_EMBEDDINGS_DDL = f"""
CREATE TABLE IF NOT EXISTS card_embeddings (
    oracle_id   VARCHAR(64) PRIMARY KEY REFERENCES cards(oracle_id),
    model_id    VARCHAR(128) NOT NULL,
    embedding   vector({EMBEDDING_DIM}) NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""

DECK_EMBEDDINGS_DDL = f"""
CREATE TABLE IF NOT EXISTS deck_embeddings (
    deck_id     VARCHAR(255) PRIMARY KEY REFERENCES decks(id),
    model_id    VARCHAR(128) NOT NULL,
    embedding   vector({EMBEDDING_DIM}) NOT NULL,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""

DERIVED_INDEX_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS derived_index_status (
    store            VARCHAR(16) PRIMARY KEY
                     CHECK (store IN ('vector','graph')),
    rebuilt_at       TIMESTAMPTZ NOT NULL,
    source_watermark TIMESTAMPTZ,
    row_count        INTEGER NOT NULL DEFAULT 0,
    owner_id         VARCHAR(255)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_card_embeddings_hnsw"
    " ON card_embeddings USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS idx_deck_embeddings_hnsw"
    " ON deck_embeddings USING hnsw (embedding vector_cosine_ops)",
)

revision = "a007_derived_vector_indexes"
down_revision = ("a005_draft_mode", "a004_slice2_profile_overrides")


def upgrade() -> None:
    from alembic import op

    op.execute(CREATE_EXTENSION_DDL)
    op.execute(CARD_EMBEDDINGS_DDL)
    op.execute(DECK_EMBEDDINGS_DDL)
    op.execute(DERIVED_INDEX_STATUS_DDL)
    for stmt in INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS derived_index_status")
    op.execute("DROP TABLE IF EXISTS deck_embeddings")
    op.execute("DROP TABLE IF EXISTS card_embeddings")
