"""Slice 4 tests: Forge log import -> game_imports/games/game_telemetry ->
analysis notes -> profile snapshot (trigger='game'); idempotent re-import.

Runs on in-memory SQLite (no Postgres needed), mirroring test_slice1.py.
Golden parser facts come from the real captured log
fixtures/forge/verbose_game1.txt (4 seats, winner seat 4, 19 turns,
45496 ms).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.forge import forge_game_telemetry as fgt
from src.server.domain.forge import game_log
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import (
    Base,
    GameImportRow,
    GameRow,
    GameTelemetryRow,
    ProfileSnapshotRow,
)
from src.server.workers import forge_import

FIX = Path(__file__).parent / "fixtures" / "forge" / "verbose_game1.txt"
TEXT = FIX.read_text(encoding="utf-8")

DECK = {
    "id": "archidekt:5054882",
    "name": "The Return of the King",
    "archidekt_id": 5054882,
    "status": "have",
    "color_identity": ["W", "R", "G"],
    "commanders": ["Aragorn, King Elessar"],
    "source": "archidekt",
    "fingerprint": "fp",
}


@pytest.fixture()
def factory(tmp_path):
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from src.server.infra.db.session import session_scope
    with session_scope(factory) as s:
        repo.upsert_deck(s, DECK)
    yield factory
    Base.metadata.drop_all(eng)
    eng.dispose()


def _run(factory, tmp_path, text=TEXT, deck_id=DECK["id"], **kw):
    return forge_import.import_forge_log(
        deck_id, text, session_factory=factory,
        log_dir=str(tmp_path / "logs"), **kw)


def _rows(factory, model):
    from src.server.infra.db.session import session_scope
    with session_scope(factory) as s:
        return s.scalars(select(model)).all()


# --- fingerprint / normalization --------------------------------------------


def test_fingerprint_normalizes_crlf_and_whitespace():
    assert forge_import.fingerprint(TEXT) == forge_import.fingerprint(
        TEXT.replace("\n", "\r\n"))
    assert forge_import.fingerprint(TEXT) == forge_import.fingerprint(
        "  " + TEXT + "  \n")
    assert forge_import.fingerprint(TEXT) != forge_import.fingerprint(
        TEXT + "extra line\n")


# --- game_log golden derivation ----------------------------------------------


def test_derive_game_golden_facts():
    blocks = fgt.split_game_blocks(TEXT)
    assert len(blocks) == 1
    d = game_log.derive_game(blocks[0])
    assert d["seat_count"] == 4
    assert d["winner_seat"] == 4
    assert d["turns"] == 19
    assert d["duration_ms"] == 45496
    assert d["seat_decks"][1] == "user_417707"


def test_game_result_and_unavailable_reason():
    blocks = fgt.split_game_blocks(TEXT)
    d = game_log.derive_game(blocks[0])
    assert game_log.game_result(d, 4) == ("win", None)
    assert game_log.game_result(d, 1) == ("loss", None)
    result, reason = game_log.game_result({**d, "winner_seat": None}, 1)
    assert result is None and reason == game_log.REASON_RESULT


# --- full import flow --------------------------------------------------------


def test_import_writes_import_game_telemetry_and_snapshot(factory, tmp_path):
    res = _run(factory, tmp_path, user_seat=1)
    assert res["status"] == "parsed" and res["duplicate"] is False
    assert res["error"] is None
    assert len(res["games"]) == 1
    g = res["games"][0]
    assert g["result"] == "loss" and g["turns"] == 19 and g["seat_count"] == 4
    assert "unavailable" in g["notes"]  # draw/stuck explicitly stated
    assert res["snapshot_id"] is not None

    imports = _rows(factory, GameImportRow)
    assert len(imports) == 1
    assert imports[0].status == "parsed"
    assert imports[0].log_hash == forge_import.fingerprint(TEXT)
    assert imports[0].raw_log_uri.startswith("file://")

    games = _rows(factory, GameRow)
    assert len(games) == 1
    assert games[0].deck_id == DECK["id"]

    tel = _rows(factory, GameTelemetryRow)
    metrics = {t.metric for t in tel}
    assert {"meta", "curve", "card_cast"} <= metrics
    meta = next(t for t in tel if t.metric == "meta")
    un = meta.value["unavailable"]
    # "unavailable" is first-class with explicit reasons (§7.4), never 0
    assert un["played_at"] == game_log.REASON_PLAYED_AT
    assert un["mana_spent"] == game_log.REASON_MANA_SPENT
    casts = [t for t in tel if t.metric == "card_cast"]
    assert casts, "expected per-card telemetry rows"
    for t in casts:
        assert t.value["drawn"] is None
        assert t.value["stuck"] is None
        assert t.value["unavailable"]["drawn"] == game_log.REASON_DRAWS
        assert t.value["unavailable"]["stuck"] == game_log.REASON_STUCK
    # a known card resolved its cast turn
    signet = next(t for t in casts
                  if t.value["card"] == "Arcane Signet" and t.value["seat"] == 1)
    assert signet.value["cast_turn"] == 2

    snaps = _rows(factory, ProfileSnapshotRow)
    assert len(snaps) == 1
    assert snaps[0].trigger == "game"
    assert snaps[0].metrics["count"] == 1
    assert snaps[0].metrics["record"] == {"wins": 0, "losses": 1, "unknown": 0}
    assert snaps[0].metrics["unavailable"]["draws"] == game_log.REASON_DRAWS
    assert snaps[0].inputs["import_id"] == res["import_id"]


def test_reimport_is_idempotent(factory, tmp_path):
    first = _run(factory, tmp_path, user_seat=1)
    second = _run(factory, tmp_path, user_seat=1)
    assert second["duplicate"] is True
    assert second["import_id"] == first["import_id"]
    assert second["snapshot_id"] is None
    assert [g["game_id"] for g in second["games"]] == \
           [g["game_id"] for g in first["games"]]
    assert len(_rows(factory, GameImportRow)) == 1
    assert len(_rows(factory, GameRow)) == 1
    assert len(_rows(factory, ProfileSnapshotRow)) == 1


def test_failed_import_records_failed_row_and_is_idempotent(factory, tmp_path):
    garbage = "this is not a forge log\nno markers here\n"
    res = _run(factory, tmp_path, text=garbage)
    assert res["status"] == "failed" and res["duplicate"] is False
    assert res["error"] and res["games"] == []
    imports = _rows(factory, GameImportRow)
    assert len(imports) == 1 and imports[0].status == "failed"
    assert imports[0].error == res["error"]
    assert _rows(factory, GameRow) == []
    assert _rows(factory, ProfileSnapshotRow) == []
    again = _run(factory, tmp_path, text=garbage)
    assert again["duplicate"] is True and again["status"] == "failed"
    assert len(_rows(factory, GameImportRow)) == 1


def test_unknown_deck_raises(factory, tmp_path):
    with pytest.raises(forge_import.ForgeImportError):
        _run(factory, tmp_path, deck_id="archidekt:nope")
    assert _rows(factory, GameImportRow) == []


def test_wrong_seat_fails_loudly(factory, tmp_path):
    res = _run(factory, tmp_path, user_seat=9)
    assert res["status"] == "failed"
    assert "seat 9" in res["error"]
