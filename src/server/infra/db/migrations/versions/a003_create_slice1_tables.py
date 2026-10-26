"""Create the Slice-1 canonical tables (docs/ARCHITECTURE.md §4.2).

cards, printings, decks, deck_cards, card_instances, instance_events,
profile_snapshots. Enum vocabularies are VARCHAR + CHECK constraints (the same
shape src.server.infra.db.models produces via Base.metadata.create_all, so
tests and migrated deploys agree). Idempotent (CREATE TABLE IF NOT EXISTS),
same convention as a001/a002.

Every table carries ``owner_id NULL`` per the §4.2 single-user-ready note.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

# --- cards (Scryfall oracle-level mirror) ----------------------------------
CARDS_DDL = """
CREATE TABLE IF NOT EXISTS cards (
    oracle_id     VARCHAR(64) PRIMARY KEY,
    name          VARCHAR(255) NOT NULL,
    type_line     VARCHAR(255),
    oracle_text   TEXT,
    colors        JSONB NOT NULL DEFAULT '[]',
    cmc           DOUBLE PRECISION,
    keywords      JSONB NOT NULL DEFAULT '[]',
    legalities    JSONB NOT NULL DEFAULT '{}',
    image_uri     VARCHAR(512),
    etag          VARCHAR(128),
    refreshed_at  TIMESTAMPTZ,
    owner_id      VARCHAR(255)
)
"""

# --- printings --------------------------------------------------------------
PRINTINGS_DDL = """
CREATE TABLE IF NOT EXISTS printings (
    scryfall_id      VARCHAR(64) PRIMARY KEY,
    oracle_id        VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    set_code         VARCHAR(16),
    collector_number VARCHAR(32),
    rarity           VARCHAR(32),
    prices           JSONB NOT NULL DEFAULT '{}',
    owner_id         VARCHAR(255)
)
"""
PRINTINGS_ORACLE_IDX = """
CREATE INDEX IF NOT EXISTS ix_printings_oracle_id ON printings (oracle_id)
"""

# --- decks ------------------------------------------------------------------
DECKS_DDL = """
CREATE TABLE IF NOT EXISTS decks (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(255) NOT NULL,
    archidekt_id   VARCHAR(64) UNIQUE,
    status         VARCHAR(32) NOT NULL DEFAULT 'virtual'
                   CHECK (status IN ('have', 'virtual', 'pending', 'possible')),
    color_identity JSONB NOT NULL DEFAULT '[]',
    commanders     JSONB NOT NULL DEFAULT '[]',
    source         VARCHAR(32) NOT NULL DEFAULT 'manual'
                   CHECK (source IN ('archidekt', 'brew', 'draft', 'manual')),
    fingerprint    VARCHAR(64),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at    TIMESTAMPTZ,
    owner_id       VARCHAR(255)
)
"""

# --- deck_cards -------------------------------------------------------------
DECK_CARDS_DDL = """
CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id   INTEGER NOT NULL REFERENCES decks(id),
    oracle_id VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    board     VARCHAR(32) NOT NULL
              CHECK (board IN ('commander', 'main', 'sideboard', 'maybe', 'considering')),
    qty       INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0),
    category  VARCHAR(255),
    owner_id  VARCHAR(255),
    PRIMARY KEY (deck_id, oracle_id, board)
)
"""

# --- card_instances ---------------------------------------------------------
CARD_INSTANCES_DDL = """
CREATE TABLE IF NOT EXISTS card_instances (
    id          VARCHAR(64) PRIMARY KEY,
    oracle_id   VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    status      VARCHAR(32) NOT NULL
                CHECK (status IN ('not_owned', 'in_mail', 'in_collection', 'in_deck')),
    deck_id     INTEGER REFERENCES decks(id),
    finish      VARCHAR(32) NOT NULL DEFAULT 'nonfoil',
    condition   VARCHAR(8) NOT NULL DEFAULT 'NM',
    printing_id VARCHAR(64) REFERENCES printings(scryfall_id),
    price_paid  DOUBLE PRECISION,
    acquired_at TIMESTAMPTZ,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""
CARD_INSTANCES_ORACLE_IDX = """
CREATE INDEX IF NOT EXISTS ix_card_instances_oracle_id ON card_instances (oracle_id)
"""
CARD_INSTANCES_STATUS_IDX = """
CREATE INDEX IF NOT EXISTS ix_card_instances_status ON card_instances (status)
"""
CARD_INSTANCES_DECK_IDX = """
CREATE INDEX IF NOT EXISTS ix_card_instances_deck_id ON card_instances (deck_id)
"""

# --- instance_events --------------------------------------------------------
INSTANCE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS instance_events (
    id          SERIAL PRIMARY KEY,
    instance_id VARCHAR(64) NOT NULL REFERENCES card_instances(id),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        VARCHAR(32) NOT NULL
                CHECK (kind IN ('acquired', 'assigned', 'unassigned', 'mailed', 'traded', 'lost')),
    from_deck   INTEGER REFERENCES decks(id),
    to_deck     INTEGER REFERENCES decks(id),
    draft_id    VARCHAR(64),
    owner_id    VARCHAR(255)
)
"""
INSTANCE_EVENTS_INSTANCE_IDX = """
CREATE INDEX IF NOT EXISTS ix_instance_events_instance_id ON instance_events (instance_id)
"""

# --- profile_snapshots ------------------------------------------------------
PROFILE_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS profile_snapshots (
    id           SERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger      VARCHAR(32) NOT NULL
                 CHECK (trigger IN ('import', 'game', 'rating', 'manual', 'override')),
    inputs       JSONB NOT NULL DEFAULT '{}',
    metrics      JSONB NOT NULL DEFAULT '{}',
    summary_text TEXT NOT NULL DEFAULT '',
    owner_id     VARCHAR(255)
)
"""

revision = "a003_create_slice1_tables"
down_revision = "a002_create_games_play_style"

_UPGRADE = (
    CARDS_DDL, PRINTINGS_DDL, PRINTINGS_ORACLE_IDX, DECKS_DDL, DECK_CARDS_DDL,
    CARD_INSTANCES_DDL, CARD_INSTANCES_ORACLE_IDX, CARD_INSTANCES_STATUS_IDX,
    CARD_INSTANCES_DECK_IDX, INSTANCE_EVENTS_DDL, INSTANCE_EVENTS_INSTANCE_IDX,
    PROFILE_SNAPSHOTS_DDL,
)
_DOWNGRADE = (
    "DROP TABLE IF EXISTS profile_snapshots",
    "DROP TABLE IF EXISTS instance_events",
    "DROP TABLE IF EXISTS card_instances",
    "DROP TABLE IF EXISTS deck_cards",
    "DROP TABLE IF EXISTS decks",
    "DROP TABLE IF EXISTS printings",
    "DROP TABLE IF EXISTS cards",
)


def upgrade() -> None:
    from alembic import op

    for stmt in _UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    for stmt in _DOWNGRADE:
        op.execute(stmt)
