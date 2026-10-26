"""Create the Slice 1 core tables (ARCHITECTURE.md §4.2).

Card reference mirror (cards, printings), Commander Lab-owned decks and
collection (decks, deck_cards, card_instances, instance_events) and the
append-only profile history (profile_snapshots).

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001/a002, so it
can be applied to a database that already carries tables created by
``Base.metadata.create_all`` and to a fresh PostgreSQL deploy alike.

Enum vocabularies are CHECK constraints (portable across PostgreSQL and the
SQLite used by unit tests); the load-bearing values live in AGENT_RULES.md §9
and ARCHITECTURE.md §4.2:
  deck status:   have | virtual | pending | possible
  board:         commander | main | sideboard | maybe | considering
  deck source:   archidekt | brew | draft | manual
  instance:      not_owned | in_mail | in_collection | in_deck
  event kind:    acquired | assigned | unassigned | mailed | traded | lost
  trigger:       import | game | rating | manual | override
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for the importer, verification scripts and
# tests in environments where alembic is not installed (CI unit-test job,
# which installs requirements.txt only).

# --- card reference (Scryfall mirror) --------------------------------------
CARDS_DDL = """
CREATE TABLE IF NOT EXISTS cards (
    oracle_id       VARCHAR(64) PRIMARY KEY,      -- Scryfall oracle id
    name            VARCHAR(255) NOT NULL,
    type_line       VARCHAR(255) NOT NULL DEFAULT '',
    oracle_text     TEXT NOT NULL DEFAULT '',
    mana_cost       VARCHAR(64) NOT NULL DEFAULT '',
    colors          JSONB NOT NULL DEFAULT '[]',  -- WUBRG letters
    color_identity  JSONB NOT NULL DEFAULT '[]',
    cmc             DOUBLE PRECISION NOT NULL DEFAULT 0,
    keywords        JSONB NOT NULL DEFAULT '[]',
    legalities      JSONB NOT NULL DEFAULT '{}',
    image_uri       VARCHAR(512) NOT NULL DEFAULT '',
    etag            VARCHAR(128),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id        VARCHAR(255)
)
"""

PRINTINGS_DDL = """
CREATE TABLE IF NOT EXISTS printings (
    scryfall_id      VARCHAR(64) PRIMARY KEY,     -- Scryfall printing id
    oracle_id        VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    set_code         VARCHAR(16) NOT NULL DEFAULT '',
    collector_number VARCHAR(32) NOT NULL DEFAULT '',
    rarity           VARCHAR(32) NOT NULL DEFAULT '',
    prices           JSONB NOT NULL DEFAULT '{}',
    owner_id         VARCHAR(255)
)
"""

# --- decks & collection (Commander Lab owns these) --------------------------
DECKS_DDL = """
CREATE TABLE IF NOT EXISTS decks (
    id              VARCHAR(255) PRIMARY KEY,     -- registry id, e.g. archidekt:5054882
    name            VARCHAR(255) NOT NULL,
    archidekt_id    VARCHAR(64) UNIQUE,           -- NULL for brew/draft/manual decks
    status          VARCHAR(16) NOT NULL DEFAULT 'virtual'
                    CHECK (status IN ('have','virtual','pending','possible')),
    color_identity  JSONB NOT NULL DEFAULT '[]',
    commanders      JSONB NOT NULL DEFAULT '[]',  -- list of commander card names
    source          VARCHAR(16) NOT NULL DEFAULT 'manual'
                    CHECK (source IN ('archidekt','brew','draft','manual')),
    fingerprint     VARCHAR(64) NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ,
    owner_id        VARCHAR(255)
)
"""

DECK_CARDS_DDL = """
CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id     VARCHAR(255) NOT NULL REFERENCES decks(id),
    oracle_id   VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    qty         INTEGER NOT NULL CHECK (qty > 0),
    board       VARCHAR(16) NOT NULL
                CHECK (board IN ('commander','main','sideboard','maybe','considering')),
    category    VARCHAR(255),                     -- primary thematic category; NULL allowed
    owner_id    VARCHAR(255),
    PRIMARY KEY (deck_id, oracle_id, board)
)
"""
DECK_CARDS_ORACLE_IDX = """
CREATE INDEX IF NOT EXISTS idx_deck_cards_oracle ON deck_cards (oracle_id)
"""

CARD_INSTANCES_DDL = """
CREATE TABLE IF NOT EXISTS card_instances (
    id                  VARCHAR(64) PRIMARY KEY,  -- uuid (carried from the Redis registry)
    oracle_id           VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    status              VARCHAR(16) NOT NULL DEFAULT 'not_owned'
                        CHECK (status IN ('not_owned','in_mail','in_collection','in_deck')),
    deck_id             VARCHAR(255) REFERENCES decks(id),  -- set iff status = in_deck
    finish              VARCHAR(32) NOT NULL DEFAULT 'nonfoil',
    condition           VARCHAR(16) NOT NULL DEFAULT 'NM',
    acquired_at         TIMESTAMPTZ,
    notes               TEXT NOT NULL DEFAULT '',
    -- carried over from the Redis instance registry (zero-loss migration):
    set_code            VARCHAR(16) NOT NULL DEFAULT '',
    collector_number    VARCHAR(32) NOT NULL DEFAULT '',
    price_paid          DOUBLE PRECISION NOT NULL DEFAULT 0,
    source              VARCHAR(64) NOT NULL DEFAULT '',
    considered_for_deck VARCHAR(255),
    legacy_deck_ref     VARCHAR(255),             -- unresolvable pre-migration deck binding
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id            VARCHAR(255)
)
"""
CARD_INSTANCES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_card_instances_oracle ON card_instances (oracle_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_instances_deck ON card_instances (deck_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_instances_status ON card_instances (status)",
)

INSTANCE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS instance_events (
    id          SERIAL PRIMARY KEY,
    instance_id VARCHAR(64) NOT NULL REFERENCES card_instances(id),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        VARCHAR(16) NOT NULL
                CHECK (kind IN ('acquired','assigned','unassigned','mailed','traded','lost')),
    from_deck   VARCHAR(255),
    to_deck     VARCHAR(255),
    draft_id    VARCHAR(255),
    owner_id    VARCHAR(255)
)
"""
INSTANCE_EVENTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_instance_events_instance ON instance_events (instance_id)
"""

# --- profile (evolving taste; append-only) ----------------------------------
PROFILE_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS profile_snapshots (
    id           SERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger      VARCHAR(16) NOT NULL
                 CHECK (trigger IN ('import','game','rating','manual','override')),
    inputs       JSONB NOT NULL DEFAULT '{}',
    metrics      JSONB NOT NULL DEFAULT '{}',
    summary_text TEXT NOT NULL DEFAULT '',
    owner_id     VARCHAR(255)
)
"""

revision = "a003_slice1_core"
down_revision = "a002_create_games_play_style"


def upgrade() -> None:
    from alembic import op

    op.execute(CARDS_DDL)
    op.execute(PRINTINGS_DDL)
    op.execute(DECKS_DDL)
    op.execute(DECK_CARDS_DDL)
    op.execute(DECK_CARDS_ORACLE_IDX)
    op.execute(CARD_INSTANCES_DDL)
    for stmt in CARD_INSTANCES_INDEXES:
        op.execute(stmt)
    op.execute(INSTANCE_EVENTS_DDL)
    op.execute(INSTANCE_EVENTS_IDX)
    op.execute(PROFILE_SNAPSHOTS_DDL)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS profile_snapshots")
    op.execute("DROP TABLE IF EXISTS instance_events")
    op.execute("DROP TABLE IF EXISTS card_instances")
    op.execute("DROP TABLE IF EXISTS deck_cards")
    op.execute("DROP TABLE IF EXISTS decks")
    op.execute("DROP TABLE IF EXISTS printings")
    op.execute("DROP TABLE IF EXISTS cards")
