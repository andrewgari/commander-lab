"""STA-10 work package A: read-only Archidekt reconciliation preview.

Covers the STA-9 source-validation acceptance checks that belong to this
slice: SRC-01..SRC-04 fully, the preview-relevant parts of SRC-05/SRC-06.
Runs on in-memory SQLite with fixture payloads (no network, no Redis).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.decks import reconciliation_preview as rp
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope
from src.server.infra.services import import_service

FIXTURES = Path(__file__).parent / "fixtures" / "archidekt_reconciliation"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def _db_state(factory):
    """Everything a destructive preview would have to touch."""
    with session_scope(factory) as s:
        decks = repo.list_decks(s, include_archived=True)
        return {
            "decks": [dict(d, cards=repo.get_deck_cards(s, d["id"])) for d in decks],
            "snapshots": len(repo.list_snapshots(s)),
            "instances": repo.list_instances(s),
        }


# --- SRC-02: Aragorn row vs quantity ------------------------------------------

def test_aragorn_rows_vs_quantities():
    payload = load("aragorn_5054882.json")
    facts = rp.snapshot_deck_facts(payload)
    assert facts["row_count"] == 92            # raw rows
    assert facts["quantity_sum"] == 100         # units: NOT a 92-card deck
    assert facts["included_unit_count"] == 100  # every unit has included categories
    assert facts["mixed_inclusion_rows"] == []
    assert facts["commanders"] == ["Aragorn, the Uniter"]
    assert facts["exceptions"] == []

    preview = rp.preview_deck_refresh(payload, None, None)
    assert preview["writes"] == "none"
    assert preview["main_deck_selection"] == "included_categories"
    # row count never presented as deck size in the proposed import either
    assert preview["proposed_import"]["main_and_commander_qty"] == 100


# --- SRC-03: Gamer auxiliary units ---------------------------------------------

def test_gamer_auxiliary_units_preserved_and_flagged():
    payload = load("gamer_4099658.json")
    facts = rp.snapshot_deck_facts(payload)
    assert facts["row_count"] == 158
    assert facts["quantity_sum"] == 178
    assert facts["included_unit_count"] == 100
    assert facts["units_by_category"]["Attraction Deck"] == 34
    assert facts["units_by_category"]["Stickers"] == 44

    preview = rp.preview_deck_refresh(payload, None, None)
    # Auxiliary units are neither dropped nor added to the included count...
    assert preview["source_facts"]["included_unit_count"] == 100
    # ...but the current importer maps Stickers rows into the mainboard, so
    # the preview surfaces a visible mapping conflict instead of silently
    # accepting the inflated main deck.
    assert rp.EX_MAPPING_CONFLICT in preview["exceptions"]
    assert preview["proposed_import"]["main_and_commander_qty"] == 144


# --- SRC-04: Atla ambiguity ------------------------------------------------------

def test_atla_stays_ambiguous():
    payload = load("atla_417278.json")
    facts = rp.snapshot_deck_facts(payload)
    assert facts["row_count"] == 113
    assert facts["quantity_sum"] == 148
    assert len(facts["mixed_inclusion_rows"]) == 34
    assert facts["units_by_category"]["Sideboard"] == 46 + 8
    assert facts["units_by_category"]["Maybeboard"] == 8 + 1
    assert facts["units_by_category"]["Egg Carton"] == 34

    preview = rp.preview_deck_refresh(payload, None, None)
    assert rp.EX_AMBIGUOUS_INCLUSION in preview["exceptions"]
    assert preview["main_deck_selection"] == "ambiguous"
    # Never silently normalized to a definitive 100-card main deck:
    assert preview["proposed_import"]["main_and_commander_qty"] != 100
    assert preview["source_facts"]["included_unit_count"] != 100


# --- SRC-01: roster reconciliation ----------------------------------------------

def test_roster_manifest_and_dedupe():
    roster = load("folder_588380_roster.json")["decks"]
    assert len(roster) == 39
    assert len({r["source_id"] for r in roster}) == 39

    with_dupes = roster + [roster[0], roster[5]]
    local = [
        {"id": "archidekt:5054882", "archidekt_id": "5054882", "name": "The Return of the King"},
        {"id": "archidekt:417278", "archidekt_id": "417278", "name": "Where the Wild Things Will Be"},
    ]
    out = rp.preview_folder_roster(with_dupes, local)
    assert out["writes"] == "none"
    assert out["roster_count"] == 41          # rows seen
    assert out["distinct_source_deck_count"] == 39  # deduped by source id
    assert out["duplicate_source_ids"] == sorted(
        {str(roster[0]["source_id"]), str(roster[5]["source_id"])})
    assert out["matched_count"] == 2
    assert out["new_upstream_count"] == 37
    # matched by stable identity, never by name
    assert {m["local_deck_id"] for m in out["matched"]} == {
        "archidekt:5054882", "archidekt:417278"}

    # Idempotent: rerunning the same reconciliation yields the identical
    # result and no duplicate identities appear.
    again = rp.preview_folder_roster(with_dupes, local)
    assert again == out


def test_roster_per_deck_coverage_is_explicit():
    roster = load("folder_588380_roster.json")["decks"]
    payloads = {
        "5054882": load("aragorn_5054882.json"),
        "417278": load("atla_417278.json"),
        "4099658": load("gamer_4099658.json"),
    }
    out = rp.preview_folder_roster(roster, [], payloads=payloads)
    assert len(out["per_deck"]) == 39
    detailed = [d for d in out["per_deck"] if d["source_facts"] is not None]
    missing = [d for d in out["per_deck"] if d["source_facts"] is None]
    assert len(detailed) == 3
    assert len(missing) == 36
    for entry in missing:
        assert rp.EX_SOURCE_SNAPSHOT_MISSING in entry["exceptions"]
        assert "not inspected" in entry["coverage"]


# --- SRC-05: refresh proposes, never overwrites ----------------------------------

def test_local_edit_yields_diff_not_overwrite(factory):
    payload = load("aragorn_5054882.json")
    fetcher = lambda identifier: payload
    import_service.import_archidekt_deck("5054882", session_factory=factory,
                                         fetcher=fetcher)

    # A local edit: take one card off the local list and rename the deck.
    with session_scope(factory) as s:
        cards = repo.get_deck_cards(s, "archidekt:5054882")
        victim = next(c for c in cards if c["board"] == "main" and c["qty"] >= 2)
        trimmed = [dict(c, qty=c["qty"] - 1) if c is victim else c for c in cards]
        repo.replace_deck_cards(s, "archidekt:5054882", trimmed)
        deck = repo.get_deck(s, "archidekt:5054882")
        repo.upsert_deck(s, {**deck, "name": "My Local Name"})

    before = _db_state(factory)
    preview = import_service.preview_archidekt_deck(
        "5054882", session_factory=factory, fetcher=fetcher)
    assert preview["writes"] == "none"
    assert preview["identity"]["matched_locally"] is True

    diff = preview["differences"]
    assert diff["name"]["differs"] is True           # local name wins
    assert diff["name"]["local"] == "My Local Name"
    assert diff["cards"] and diff["cards"][0]["status"] == "quantity_change"
    assert diff["cards"][0]["action"] == "proposed_only"
    assert diff["status"]["differs"] is False        # status untouched

    # Local state is byte-identical after the preview (no overwrite, no
    # snapshot append, no instance creation).
    assert _db_state(factory) == before


def test_folder_removal_is_a_proposal_not_a_deletion(factory):
    payload = load("aragorn_5054882.json")
    import_service.import_archidekt_deck(
        "5054882", session_factory=factory, fetcher=lambda _: payload)
    roster = [r for r in load("folder_588380_roster.json")["decks"]
              if r["source_id"] != 5054882]  # deck left the folder

    with session_scope(factory) as s:
        local_decks = repo.list_decks(s, include_archived=True)
    before = _db_state(factory)

    out = rp.preview_folder_roster(roster, local_decks)
    assert [n["local_deck_id"] for n in out["local_not_in_roster"]] == [
        "archidekt:5054882"]
    assert out["local_not_in_roster"][0]["action"] == "propose_roster_change"

    # The local record still exists, unchanged.
    assert _db_state(factory) == before


# --- SRC-06: printing/finish preserved, local side honestly unknown -------------

def test_printing_metadata_preserved_local_unknown(factory):
    payload = load("aragorn_5054882.json")
    import_service.import_archidekt_deck(
        "5054882", session_factory=factory, fetcher=lambda _: payload)

    preview = import_service.preview_archidekt_deck(
        "5054882", session_factory=factory, fetcher=lambda _: payload)
    aragorn = preview["printing_metadata"]["preserved_in_preview"][
        "Aragorn, the Uniter"]
    assert aragorn["scryfall_id"] == "sf-fx-0001"
    assert aragorn["finish"] == "foil"
    assert aragorn["set_code"] == "fx"
    # The local side of a printing comparison is unknown, never invented.
    assert "unavailable" in (preview["printing_metadata"]["local_comparison"] or "")
    assert rp.EX_PRINTING_LOCAL_UNKNOWN in preview["exceptions"]

    # No inventory instances were created, bound, or moved by the preview
    # (a cross-deck duplicate can never reuse a bound instance here).
    with session_scope(factory) as s:
        assert repo.list_instances(s) == []


# --- idempotency: same source + same local state -> same preview -----------------

def test_preview_is_idempotent(factory):
    for name, sid in (("aragorn_5054882.json", "5054882"),
                      ("atla_417278.json", "417278"),
                      ("gamer_4099658.json", "4099658")):
        payload = load(name)
        import_service.import_archidekt_deck(
            sid, session_factory=factory, fetcher=lambda _: payload)

    before = _db_state(factory)
    roster = load("folder_588380_roster.json")["decks"]
    payloads = {sid: load(f) for sid, f in (
        ("5054882", "aragorn_5054882.json"),
        ("417278", "atla_417278.json"),
        ("4099658", "gamer_4099658.json"),
    )}
    with session_scope(factory) as s:
        local_decks = repo.list_decks(s, include_archived=True)
        local_cards = {d["id"]: repo.get_deck_cards(s, d["id"]) for d in local_decks}

    first = rp.preview_folder_roster(roster, local_decks, payloads, local_cards)
    second = rp.preview_folder_roster(roster, local_decks, payloads, local_cards)
    assert first == second
    assert first["writes"] == "none"
    assert first["distinct_source_deck_count"] == 39
    assert _db_state(factory) == before  # zero writes across all previews


def test_unmatched_deck_reports_no_local_counterpart(factory):
    preview = import_service.preview_archidekt_deck(
        "417278", session_factory=factory,
        fetcher=lambda _: load("atla_417278.json"))
    assert preview["identity"]["matched_locally"] is False
    assert preview["identity"]["local_deck_id"] is None
    assert preview["differences"] == {}
    assert rp.EX_AMBIGUOUS_INCLUSION in preview["exceptions"]


# --- API surface ------------------------------------------------------------------

@pytest.fixture()
def client(factory, monkeypatch):
    from fastapi.testclient import TestClient

    from src.server.app.api import deps
    from src.server.app.main import app

    def _override():
        s = factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[deps.db_session] = _override
    monkeypatch.setattr(
        "src.server.infra.providers.archidekt.fetch_deck_raw",
        lambda identifier: load("atla_417278.json"))
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_preview_endpoint_read_only(client, factory):
    before = _db_state(factory)
    r = client.post("/api/v2/imports/archidekt/preview",
                    json={"identifier": "417278"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["writes"] == "none"
    assert body["identity"]["registry_id"] == "archidekt:417278"
    assert body["main_deck_selection"] == "ambiguous"
    assert "ambiguous_inclusion" in body["exceptions"]
    assert body["source_facts"]["row_count"] == 113
    assert body["source_facts"]["quantity_sum"] == 148
    # The endpoint wrote nothing to canonical tables.
    assert _db_state(factory) == before


def test_api_preview_bad_identifier(client):
    r = client.post("/api/v2/imports/archidekt/preview",
                    json={"identifier": "not-a-deck"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_identifier"
