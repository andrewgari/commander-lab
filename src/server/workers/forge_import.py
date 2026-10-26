"""On-demand Forge verbose-log import pipeline (ARCHITECTURE.md §0/§8 Slice 4).

Flow: fingerprint the raw log (sha256) → short-circuit when already imported
→ store the raw log → parse with the verbatim `domain/forge` parser →
`game_imports` + `games` + `game_telemetry` rows → deterministic analysis
notes → append one `profile_snapshots` row with trigger='game'.

Parser failures still write a `game_imports` row (status='failed', error
set) so the audit trail is complete; unknown decks raise ForgeImportError.

Usage (CLI):
    DATABASE_URL=... python -m src.server.workers.forge_import <deck_id> <log_path> [--seat N] [--played-at ISO]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import sessionmaker

from src.server.domain.forge import forge_game_telemetry as fgt
from src.server.domain.forge import game_log, report
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import get_session_factory, session_scope

log = logging.getLogger("commander_lab.forge_import")


class ForgeImportError(Exception):
    """Raised for caller/input errors before an import row can be written
    (unknown deck). Parse failures are NOT raised — they are recorded on the
    game_imports row (status='failed') and returned."""


def _normalize_log_text(log_text: str) -> str:
    """CRLF→LF, trailing whitespace stripped: the same log pasted into a
    textarea or uploaded as a file fingerprints identically."""
    return (log_text or "").replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def fingerprint(log_text: str) -> str:
    return hashlib.sha256(_normalize_log_text(log_text).encode("utf-8")).hexdigest()


def _store_raw_log(log_dir: Optional[str], log_hash: str, log_text: str) -> str:
    """Persist the raw log next to its fingerprint; returns a file:// URI."""
    root = Path(log_dir or os.environ.get("FORGE_LOG_DIR", "data/forge_logs"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{log_hash}.log"
    if not path.exists():
        path.write_text(_normalize_log_text(log_text), encoding="utf-8")
    return f"file://{path.resolve()}"


def _resolve_oracle_ids(session, names: list[str]) -> dict:
    """card name -> oracle_id from the cards mirror; absent names stay
    unresolved (game_telemetry.oracle_id NULL, first-class per §4.2)."""
    if not names:
        return {}
    from sqlalchemy import select

    from src.server.infra.db.orm_models import CardRow

    rows = session.execute(
        select(CardRow.oracle_id, CardRow.name)
        .where(CardRow.name.in_(list(names)))
        .order_by(CardRow.oracle_id)
    ).all()
    resolved: dict = {}
    for oracle_id, name in rows:
        resolved.setdefault(name, oracle_id)
    return resolved


def _write_game(session, import_id: int, deck: dict, block: list[str],
                game_number: int, user_seat: int,
                played_at: Optional[datetime.datetime]) -> dict:
    """Parse one block and write its games + game_telemetry rows."""
    derived = game_log.derive_game(block)
    if not derived["seat_decks"]:
        raise ValueError(
            f"game {game_number}: no recognizable players — not a verbose "
            "Forge log block (expected 'Turn: Turn N (Ai(S)-<name>)' lines)"
        )
    players = fgt.parse_game(block, derived["seat_decks"])
    user_player = next((p for p in players if p["seat"] == user_seat), None)
    if user_player is None:
        raise ValueError(
            f"game {game_number}: seat {user_seat} not in log "
            f"(seats present: {sorted(derived['seat_decks'])})"
        )
    result, result_reason = game_log.game_result(derived, user_seat)
    notes = report.build_game_notes(deck["name"], user_seat, user_player, derived)
    game_id = repo.insert_game(session, {
        "import_id": import_id,
        "deck_id": deck["id"],
        "played_at": played_at,
        "seat_count": derived["seat_count"],
        "result": result,
        "turns": derived["turns"],
        "notes": notes,
    })
    repo.insert_telemetry(session, game_id, "meta", {
        "user_seat": user_seat,
        "game_number": game_number,
        "duration_ms": derived["duration_ms"],
        "unavailable": {
            "played_at": None if played_at else game_log.REASON_PLAYED_AT,
            "result": result_reason,
            "turns": None if derived["turns"] is not None else game_log.REASON_TURNS,
            "mana_spent": game_log.REASON_MANA_SPENT,
        },
    })
    for p in players:
        repo.insert_telemetry(session, game_id, "curve", {
            "seat": p["seat"],
            "deck": p["deck"],
            "points": p["curve"],
            "unavailable": {"mana_spent": game_log.REASON_MANA_SPENT},
        })
    names = sorted({name for p in players for name in p["cards"]})
    oracle_ids = _resolve_oracle_ids(session, names)
    for p in players:
        for card_name, fields in sorted(p["cards"].items()):
            repo.insert_telemetry(
                session, game_id, "card_cast", {
                    "seat": p["seat"],
                    "deck": p["deck"],
                    "card": card_name,
                    "drawn": fields.get("drawn"),
                    "cast_turn": fields.get("cast_turn"),
                    "stuck": fields.get("stuck"),
                    "unavailable": {
                        "drawn": game_log.REASON_DRAWS,
                        "stuck": game_log.REASON_STUCK,
                    },
                },
                oracle_id=oracle_ids.get(card_name),
            )
    return {
        "game_id": game_id, "user_seat": user_seat, "player": user_player,
        "derived": derived, "result": result, "notes": notes,
    }


def import_forge_log(
    deck_id: str,
    log_text: str,
    *,
    session_factory: Optional[sessionmaker] = None,
    log_dir: Optional[str] = None,
    played_at: Optional[datetime.datetime] = None,
    user_seat: int = 1,
) -> dict:
    """Import one verbose Forge log against a Commander Lab deck.

    Idempotent: the sha256 of the normalized log is the fingerprint; a
    re-import returns the existing import with duplicate=True and writes
    nothing (no duplicate games, no second snapshot).
    """
    log_hash = fingerprint(log_text)
    factory = session_factory or get_session_factory()
    with session_scope(factory) as session:
        deck = repo.get_deck(session, deck_id)
        if deck is None:
            raise ForgeImportError(f"unknown deck: {deck_id}")

        existing = repo.get_game_import_by_hash(session, log_hash)
        if existing is not None:
            games = repo.list_games_for_import(session, existing["id"])
            return {
                "duplicate": True,
                "import_id": existing["id"],
                "status": existing["status"],
                "error": existing["error"],
                "deck_id": existing["deck_id"],
                "deck_name": deck["name"],
                "log_hash": log_hash,
                "raw_log_uri": existing["raw_log_uri"],
                "games": [{"game_id": g["id"], "result": g["result"],
                           "turns": g["turns"], "seat_count": g["seat_count"]}
                          for g in games],
                "snapshot_id": None,
            }

        raw_log_uri = _store_raw_log(log_dir, log_hash, log_text)
        blocks = fgt.split_game_blocks(_normalize_log_text(log_text))

        failure: Optional[str] = None
        if not blocks:
            failure = "no completed games found in log (a game block ends with a 'Game Result:' line)"
        parsed: list = []
        if not failure:
            try:
                # Parse everything BEFORE writing rows: a bad block fails the
                # whole import loudly instead of leaving partial games.
                for i, block in enumerate(blocks, 1):
                    derived = game_log.derive_game(block)
                    if not derived["seat_decks"]:
                        raise ValueError(f"game {i}: no recognizable players")
                    if user_seat not in derived["seat_decks"]:
                        raise ValueError(
                            f"game {i}: seat {user_seat} not in log "
                            f"(seats present: {sorted(derived['seat_decks'])})"
                        )
            except ValueError as e:
                failure = str(e)

        if failure:
            import_id = repo.create_game_import(session, {
                "deck_id": deck_id,
                "raw_log_uri": raw_log_uri,
                "parser_version": game_log.PARSER_VERSION,
                "status": "failed",
                "error": failure,
                "log_hash": log_hash,
            })
            return {
                "duplicate": False,
                "import_id": import_id,
                "status": "failed",
                "error": failure,
                "deck_id": deck_id,
                "deck_name": deck["name"],
                "log_hash": log_hash,
                "raw_log_uri": raw_log_uri,
                "games": [],
                "snapshot_id": None,
            }

        import_id = repo.create_game_import(session, {
            "deck_id": deck_id,
            "raw_log_uri": raw_log_uri,
            "parser_version": game_log.PARSER_VERSION,
            "status": "parsed",
            "error": None,
            "log_hash": log_hash,
        })
        for i, block in enumerate(blocks, 1):
            parsed.append(_write_game(session, import_id, deck, block, i,
                                      user_seat, played_at))
        snap = report.build_snapshot(deck, import_id, log_hash, parsed)
        snapshot_id = repo.append_snapshot(
            session, "game", snap["inputs"], snap["metrics"], snap["summary_text"]
        )
        return {
            "duplicate": False,
            "import_id": import_id,
            "status": "parsed",
            "error": None,
            "deck_id": deck_id,
            "deck_name": deck["name"],
            "log_hash": log_hash,
            "raw_log_uri": raw_log_uri,
            "games": [{"game_id": g["game_id"], "result": g["result"],
                       "turns": g["derived"]["turns"],
                       "seat_count": g["derived"]["seat_count"],
                       "notes": g["notes"]} for g in parsed],
            "snapshot_id": snapshot_id,
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("deck_id", help="Commander Lab deck id (e.g. archidekt:5054882)")
    parser.add_argument("log_path", help="path to the verbose Forge log")
    parser.add_argument("--seat", type=int, default=1,
                        help="which seat the deck piloted (default 1)")
    parser.add_argument("--played-at", default=None,
                        help="ISO-8601 timestamp (Forge logs carry none)")
    parser.add_argument("--log-dir", default=None,
                        help="raw log store (default: FORGE_LOG_DIR or data/forge_logs)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    played_at = None
    if args.played_at:
        try:
            played_at = datetime.datetime.fromisoformat(args.played_at)
        except ValueError:
            log.error("bad --played-at: %r", args.played_at)
            return 2
    try:
        text = Path(args.log_path).read_text(encoding="utf-8", errors="replace")
        result = import_forge_log(args.deck_id, text, user_seat=args.seat,
                                  played_at=played_at, log_dir=args.log_dir)
    except ForgeImportError as e:
        log.error("import rejected: %s", e)
        return 2
    except Exception as e:  # DB/filesystem errors: fail loudly, no partial state
        log.error("import failed: %s", e)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result["status"] == "parsed" else 2


if __name__ == "__main__":
    sys.exit(main())
