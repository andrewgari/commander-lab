"""Slice 5 draft-mode staging tables (ARCHITECTURE.md §7.8, §8 Slice 5).

Draft mode (combine decks / dismantle one to build another) is staging-only:
nothing is written to the canonical tables (`decks`, `deck_cards`,
`card_instances`) until the user commits. These three tables hold ALL
uncommitted work:

- `drafts` — one row per draft session (kind combine|dismantle, status
  open|committed|abandoned). `source_decks` is a jsonb snapshot of the
  source deck ids/names captured at creation; `result_deck_id` is set only
  by Commit.
- `draft_cards` — the staged decklist for the deck being built
  (PK (draft_id, oracle_id, board), mirroring `deck_cards`).
- `draft_instance_moves` — staged physical-copy moves
  (PK (draft_id, instance_id)); `from_deck` snapshots the instance's deck
  binding at staging time, `action` is assign (into the result deck) or
  unassign (back to the collection).

Commit applies `deck_cards` changes + `instance_events` provenance
(kind=assigned|unassigned, from_deck/to_deck, draft_id) in ONE transaction
(§4.2). Abandon deletes every staging row for the draft — zero residue.
Committed drafts keep their staging rows as the audit record that
`instance_events.draft_id` points back to.

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001–a004.
Enum vocabularies are CHECK constraints (portable across PostgreSQL and the
SQLite used by unit tests):
  draft kind:    combine | dismantle
  draft status:  open | committed | abandoned
  move action:   assign | unassign
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

DRAFTS_DDL = """
CREATE TABLE IF NOT EXISTS drafts (
    id                 VARCHAR(64) PRIMARY KEY,
    kind               VARCHAR(16) NOT NULL
                       CHECK (kind IN ('combine','dismantle')),
    status             VARCHAR(16) NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open','committed','abandoned')),
    name               VARCHAR(255) NOT NULL,
    source_decks       JSONB NOT NULL,        -- [{id, name}] snapshot at creation
    target_name        VARCHAR(255) NOT NULL,
    target_commanders  JSONB NOT NULL DEFAULT '[]',
    result_deck_id     VARCHAR(255),          -- set by Commit only
    notes              TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at          TIMESTAMPTZ,
    owner_id           VARCHAR(255)
)
"""

DRAFT_CARDS_DDL = """
CREATE TABLE IF NOT EXISTS draft_cards (
    draft_id   VARCHAR(64) NOT NULL REFERENCES drafts(id),
    oracle_id  VARCHAR(64) NOT NULL REFERENCES cards(oracle_id),
    qty        INTEGER NOT NULL,
    board      VARCHAR(16) NOT NULL
               CHECK (board IN ('commander','main','sideboard','maybe','considering')),
    category   VARCHAR(255),
    owner_id   VARCHAR(255),
    PRIMARY KEY (draft_id, oracle_id, board)
)
"""

DRAFT_INSTANCE_MOVES_DDL = """
CREATE TABLE IF NOT EXISTS draft_instance_moves (
    draft_id    VARCHAR(64) NOT NULL REFERENCES drafts(id),
    instance_id VARCHAR(64) NOT NULL REFERENCES card_instances(id),
    from_deck   VARCHAR(255),                 -- binding snapshot at staging time
    action      VARCHAR(16) NOT NULL
                CHECK (action IN ('assign','unassign')),
    owner_id    VARCHAR(255),
    PRIMARY KEY (draft_id, instance_id)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_draft_cards_draft ON draft_cards (draft_id)",
    "CREATE INDEX IF NOT EXISTS idx_draft_moves_draft ON draft_instance_moves (draft_id)",
    "CREATE INDEX IF NOT EXISTS idx_instance_events_draft ON instance_events (draft_id)",
)

revision = "a005_draft_mode"
down_revision = "a004_games_on_demand"


def upgrade() -> None:
    from alembic import op

    op.execute(DRAFTS_DDL)
    op.execute(DRAFT_CARDS_DDL)
    op.execute(DRAFT_INSTANCE_MOVES_DDL)
    for stmt in INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS draft_instance_moves")
    op.execute("DROP TABLE IF EXISTS draft_cards")
    op.execute("DROP TABLE IF EXISTS drafts")
