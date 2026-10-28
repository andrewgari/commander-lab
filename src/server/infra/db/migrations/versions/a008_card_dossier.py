"""Card dossier tables (docs/architecture/card-dossier-spec.md, adapted to the
src/server + SvelteKit layout of ARCHITECTURE.md).

One page per card (``/card/{oracle_id}?printing={scryfall_id}``) fed by one
aggregate endpoint. This migration creates the dossier's NEW tables and
extends the existing Slice-1 ``printings`` table with the art/set columns the
printings panel needs:

- ``printings`` (ALTER) — gains ``set_name``, ``image_uri_normal``,
  ``image_uri_art_crop``, ``released_at``, ``is_default``. Existing columns
  (scryfall_id PK, oracle_id FK, set_code, collector_number, rarity, prices)
  are reused unchanged.
- ``card_tags`` — lab tags with provenance (``learned`` | ``manual``),
  mirrored from the Redis ``lab_tags`` / ``lab_tags_source`` keys by the
  dossier backfill (scripts/backfill_card_dossier.py). ARCHITECTURE.md §4.2.
- ``card_interaction_edges`` — persisted synergy edges. The in-memory builder
  (domain/analytics/interactions/graph.py) stays the algorithm; PG becomes
  the read store (spec §2 discrepancy flag: no such table existed on main).
  Writer: the interaction rebuild's registered edge sink, capped to edges
  touching owned/decked cards (deck graphs + the owned-only collection
  graph). Rebuilt wholesale per ``vocab_version`` — derived, disposable.
- ``card_notes`` — Andrew's per-card affinity (1–5) and free-text notes,
  written by PUT /api/v2/card/{oracle_id}/notes.

Deck membership for the "my decks" panel reuses the existing Slice-1
``deck_cards`` ⨝ ``decks`` tables (virtual decks included — the importer
writes deck_cards for every synced decklist); collection status reuses
``card_instances``. No duplication.

Idempotent (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), same
convention as a001–a007. This revision is also the merge point for the two
a007 heads (a007_create_reviews + a007_derived_vector_indexes), same pattern
as a006_merge_heads.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

# --- printings: art/set columns for the printings panel ---------------------
PRINTINGS_ALTER_DDLS = [
    "ALTER TABLE printings ADD COLUMN IF NOT EXISTS set_name VARCHAR(255) NOT NULL DEFAULT ''",
    "ALTER TABLE printings ADD COLUMN IF NOT EXISTS image_uri_normal VARCHAR(512) NOT NULL DEFAULT ''",
    "ALTER TABLE printings ADD COLUMN IF NOT EXISTS image_uri_art_crop VARCHAR(512) NOT NULL DEFAULT ''",
    "ALTER TABLE printings ADD COLUMN IF NOT EXISTS released_at DATE",
    "ALTER TABLE printings ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE",
]

PRINTINGS_ORACLE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_printings_oracle_id ON printings (oracle_id)
"""

# --- lab tags with provenance (ARCHITECTURE.md §4.2) -------------------------
CARD_TAGS_DDL = """
CREATE TABLE IF NOT EXISTS card_tags (
    oracle_id  VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    tag        VARCHAR(255) NOT NULL,
    source     VARCHAR(16) NOT NULL DEFAULT 'learned'
               CHECK (source IN ('learned','manual')),
    confidence DOUBLE PRECISION,
    owner_id   VARCHAR(255),
    PRIMARY KEY (oracle_id, tag, source)
)
"""

# --- persisted synergy edges (derived; rebuilt by the interaction rebuild) ---
CARD_INTERACTION_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS card_interaction_edges (
    src_oracle_id VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    dst_oracle_id VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    vocab_version INTEGER NOT NULL,
    reasons       JSONB NOT NULL DEFAULT '[]',   -- shared resource keys
    reason_labels JSONB NOT NULL DEFAULT '[]',   -- human labels per key
    reason_text   TEXT NOT NULL DEFAULT '',      -- "A produces X; B consumes X" per key
    weight        INTEGER NOT NULL DEFAULT 0,
    owner_id      VARCHAR(255),
    PRIMARY KEY (src_oracle_id, dst_oracle_id, vocab_version)
)
"""

CARD_INTERACTION_EDGES_DST_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_card_interaction_edges_dst ON card_interaction_edges (dst_oracle_id)
"""

# --- per-card affinity / notes -----------------------------------------------
CARD_NOTES_DDL = """
CREATE TABLE IF NOT EXISTS card_notes (
    oracle_id  VARCHAR(64) PRIMARY KEY REFERENCES cards(oracle_id),
    affinity   SMALLINT CHECK (affinity BETWEEN 1 AND 5),
    notes      TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id   VARCHAR(255)
)
"""

ALL_DDLS = [
    *PRINTINGS_ALTER_DDLS,
    PRINTINGS_ORACLE_INDEX_DDL,
    CARD_TAGS_DDL,
    CARD_INTERACTION_EDGES_DDL,
    CARD_INTERACTION_EDGES_DST_INDEX_DDL,
    CARD_NOTES_DDL,
]

revision = "a008_card_dossier"
down_revision = ("a007_create_reviews", "a007_derived_vector_indexes")
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    for stmt in ALL_DDLS:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS card_notes")
    op.execute("DROP TABLE IF EXISTS card_interaction_edges")
    op.execute("DROP TABLE IF EXISTS card_tags")
    op.execute("ALTER TABLE printings DROP COLUMN IF EXISTS is_default")
    op.execute("ALTER TABLE printings DROP COLUMN IF EXISTS released_at")
    op.execute("ALTER TABLE printings DROP COLUMN IF EXISTS image_uri_art_crop")
    op.execute("ALTER TABLE printings DROP COLUMN IF EXISTS image_uri_normal")
    op.execute("ALTER TABLE printings DROP COLUMN IF EXISTS set_name")
