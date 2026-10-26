"""Slice 4 games-on-demand tables (ARCHITECTURE.md §4.2, §8 Slice 4).

Creates the canonical games model for on-demand Forge verbose-log imports:

- `game_imports` — one row per imported log; `log_hash` (sha256 of the raw
  log) fingerprints the file so re-importing the same log short-circuits
  (idempotent, no duplicate games).
- `games` — one row per completed game in the log. `played_at`, `result`
  and `turns` are NULL-able: Forge logs carry no timestamps, and a log
  without a 'has won!' line has no determinable result — unavailable is a
  first-class value with the reason in `notes`/telemetry, never a 0
  (AGENT_RULES.md §4).
- `game_telemetry` — parser output: one `meta` row per game, one `curve`
  row per seat, one `card_cast` row per seat+card; `drawn`/`stuck`/
  `mana_spent` stay NULL inside the jsonb value with explicit unavailable
  reasons (§7.4).

Also renames the pre-Slice-4 auto-recorder table `games` (a002, game_uid
shape) to `legacy_games`, freeing the canonical name. The rename is guarded
on the legacy shape so this migration is a no-op on databases that were
created after the ORM rename (fresh deploys, test databases).

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001–a003.
Enum vocabularies are CHECK constraints (portable across PostgreSQL and the
SQLite used by unit tests):
  import status: parsed | failed
  game result:   win | loss | draw
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

# PostgreSQL-only guard: rename the a002 auto-recorder table out of the way
# when (and only when) the legacy `games` shape is present.
RENAME_LEGACY_GAMES = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'games' AND column_name = 'game_uid'
    ) THEN
        ALTER TABLE games RENAME TO legacy_games;
    END IF;
END
$$;
"""

GAME_IMPORTS_DDL = """
CREATE TABLE IF NOT EXISTS game_imports (
    id             SERIAL PRIMARY KEY,
    deck_id        VARCHAR(255) NOT NULL REFERENCES decks(id),
    imported_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_log_uri    TEXT NOT NULL DEFAULT '',
    parser_version VARCHAR(64) NOT NULL,
    status         VARCHAR(8) NOT NULL
                   CHECK (status IN ('parsed','failed')),
    error          TEXT,
    log_hash       VARCHAR(64) NOT NULL UNIQUE,  -- sha256 of the raw log; idempotency key
    owner_id       VARCHAR(255)
)
"""

GAMES_DDL = """
CREATE TABLE IF NOT EXISTS games (
    id          SERIAL PRIMARY KEY,
    import_id   INTEGER NOT NULL REFERENCES game_imports(id),
    deck_id     VARCHAR(255) NOT NULL REFERENCES decks(id),
    played_at   TIMESTAMPTZ,                     -- NULL: Forge logs carry no timestamps
    seat_count  INTEGER NOT NULL,
    result      VARCHAR(8)
                CHECK (result IN ('win','loss','draw')),  -- NULL: unavailable (reason in notes)
    turns       INTEGER,                         -- NULL: unavailable (reason in notes)
    notes       TEXT NOT NULL DEFAULT '',
    owner_id    VARCHAR(255)
)
"""
GAMES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_games_import ON games (import_id)",
    "CREATE INDEX IF NOT EXISTS idx_games_deck ON games (deck_id)",
)

GAME_TELEMETRY_DDL = """
CREATE TABLE IF NOT EXISTS game_telemetry (
    id         SERIAL PRIMARY KEY,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    oracle_id  VARCHAR(64) REFERENCES cards(oracle_id),  -- NULL: card unresolved / metric not card-scoped
    metric     VARCHAR(32) NOT NULL,                     -- meta | curve | card_cast
    value      JSONB NOT NULL,
    owner_id   VARCHAR(255)
)
"""
GAME_TELEMETRY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_game_telemetry_game ON game_telemetry (game_id)",
)

revision = "a004_games_on_demand"
down_revision = "a003_slice1_core"


def upgrade() -> None:
    from alembic import op

    op.execute(RENAME_LEGACY_GAMES)
    op.execute(GAME_IMPORTS_DDL)
    op.execute(GAMES_DDL)
    for stmt in GAMES_INDEXES:
        op.execute(stmt)
    op.execute(GAME_TELEMETRY_DDL)
    for stmt in GAME_TELEMETRY_INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS game_telemetry")
    op.execute("DROP TABLE IF EXISTS games")
    op.execute("DROP TABLE IF EXISTS game_imports")
    op.execute("""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'legacy_games' AND column_name = 'game_uid'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables WHERE table_name = 'games'
    ) THEN
        ALTER TABLE legacy_games RENAME TO games;
    END IF;
END
$$;
    """)
