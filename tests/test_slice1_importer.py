"""Slice 1: Archidekt one-way importer (workers/importer.py).

Runs on in-memory SQLite; fetchers are stubbed so no network is touched.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.infra.db.orm_models import Base
from src.server.infra.db import repositories as repo
from src.server.workers import importer


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()
    eng.dispose()


def _archidekt_payload():
    """Minimal Archidekt v3-shaped deck: 1 commander + 3 mainboard cards."""
    def entry(name, oid, qty, categories, types, colors, cmc=None):
        return {
            "quantity": qty,
            "categories": categories,
            "card": {
                "uid": f"print-{oid}",
                "collectorNumber": "1",
                "edition": {"editioncode": "tst"},
                "oracleCard": {
                    "uid": oid, "name": name, "types": types,
                    "colorIdentity": colors,
                },
            },
        }
    return {
        "id": 417707,
        "name": "Test Deck",
        "cards": [
            entry("Test Commander", "oid-cmd", 1, ["Commander"], ["Creature"], ["Red", "Green"]),
            entry("Sol Ring", "oid-ring", 1, ["Ramp"], ["Artifact"], []),
            entry("Forest", "oid-forest", 10, ["Land"], ["Land"], []),
            entry("Llanowar Elves", "oid-elves", 1, ["Ramp", "Maybeboard"], ["Creature"], ["Green"]),
        ],
    }


def _scryfall_cards():
    def card(oid, name, type_line, cmc, colors):
        return {"oracle_id": oid, "name": name, "type_line": type_line,
                "cmc": cmc, "colors": colors, "keywords": [], "legalities": {},
                "image_uris": {"normal": f"https://img/{oid}.png"}}
    return {
        "oid-cmd": card("oid-cmd", "Test Commander", "Legendary Creature — Test", 4.0, ["R", "G"]),
        "oid-ring": card("oid-ring", "Sol Ring", "Artifact", 1.0, []),
        "oid-forest": card("oid-forest", "Forest", "Basic Land — Forest", None, ["G"]),
        "oid-elves": card("oid-elves", "Llanowar Elves", "Creature — Elf Druid", 1.0, ["G"]),
    }


def _import(session, **kw):
    return importer.import_deck(
        session, "417707",
        fetch_raw=lambda _id: _archidekt_payload(),
        fetch_cards=lambda _ids: _scryfall_cards(),
        **kw,
    )


def test_parse_payload_boards_and_categories():
    parsed = importer.parse_deck_payload(_archidekt_payload())
    by_name = {c["name"]: c for c in parsed["deck_cards"]}
    assert parsed["commanders"] == ["Test Commander"]
    # commander lives ONLY on the commander board (required == deck size)
    assert by_name["Test Commander"]["board"] == "commander"
    # first non-structural category wins as the thematic label
    assert by_name["Llanowar Elves"]["category"] == "Ramp"
    assert by_name["Llanowar Elves"]["board"] == "maybe"
    assert by_name["Forest"]["qty"] == 10
    # structural category alone is not a thematic label
    assert by_name["Test Commander"]["category"] is None
    assert parsed["color_identity"] == ["R", "G"] or set(parsed["color_identity"]) == {"R", "G"}


def test_import_deck_persists_cards_deck_and_snapshot(session):
    result = _import(session)
    assert result["snapshot_id"] is not None
    deck = repo.get_deck(session, result["deck_id"])
    assert deck["name"] == "Test Deck"
    assert deck["archidekt_id"] == "417707"
    cards = repo.deck_cards_for(session, result["deck_id"])
    assert len(cards) == 4
    ring = next(c for c in cards if c["name"] == "Sol Ring")
    assert ring["cmc"] == 1.0 and ring["category"] == "Ramp"

    snap = repo.latest_snapshot(session)
    assert snap["id"] == result["snapshot_id"]
    assert snap["trigger"] == "import"
    assert snap["metrics"]["curve"]["land_count"] == 10
    assert snap["summary_text"]  # plain-language summary stored on the row


def test_import_is_idempotent_and_appends_snapshots(session):
    first = _import(session)
    second = _import(session)
    assert first["deck_id"] == second["deck_id"]
    assert len(repo.deck_cards_for(session, first["deck_id"])) == 4
    snaps = repo.list_snapshots(session)
    assert len(snaps) == 2  # append-only history
    assert snaps[0]["id"] == second["snapshot_id"]


def test_import_unresolved_scryfall_cards_reported_not_zeroed(session):
    result = importer.import_deck(
        session, "417707",
        fetch_raw=lambda _id: _archidekt_payload(),
        fetch_cards=lambda _ids: {},  # Scryfall finds nothing
    )
    assert set(result["unresolved_cards"]) == {"oid-cmd", "oid-ring", "oid-forest", "oid-elves"}
    cards = repo.deck_cards_for(session, result["deck_id"])
    assert len(cards) == 4  # deck still imports with name-only card rows
    snap = repo.latest_snapshot(session)
    # NULL cmc surfaces as unavailable, never counted as 0 (AGENT_RULES §4)
    # 1 cmd + 1 ring + 10 forests on requirement boards (elves is maybe-board);
    # with type_line unknown nothing is recognized as a land
    assert snap["metrics"]["curve"]["unavailable_cmc_count"] == 12
    assert snap["metrics"]["curve"]["avg_cmc"] is None


def test_import_empty_deck_rejected(session):
    with pytest.raises(importer.ImportError_):
        importer.import_deck(
            session, "x",
            fetch_raw=lambda _id: {"id": 1, "name": "empty", "cards": []},
            fetch_cards=lambda _ids: {},
        )
