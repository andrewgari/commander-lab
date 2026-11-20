"""Slice 5 tests: draft mode (combine/dismantle) — staging isolation,
one-transaction commit, abandon zero residue, provenance query, and the
tuner/reconstruct suggestion port. In-memory SQLite; no network.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.draft import draft_mode, suggestions
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import (
    Base,
    CardInstanceRow,
    DeckCardRow,
    DeckRow,
    DraftCardRow,
    DraftInstanceMoveRow,
    DraftRow,
    InstanceEventRow,
)
from src.server.infra.db.session import session_scope


# --- fixtures -----------------------------------------------------------------

@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def _card(oid, name=None, ci=None, type_line="Creature", cmc=3.0):
    return {"oracle_id": oid, "name": name or oid, "type_line": type_line,
            "oracle_text": "", "mana_cost": "", "colors": [],
            "color_identity": ci or [], "cmc": cmc, "keywords": []}


def _seed_two_decks(s):
    """Two source decks (A: o-a1 main, o-acmd commander; B: o-b1, o-b2) plus
    cards o-free (collection) and o-donor (in a third deck C)."""
    for c in [
        _card("o-acmd", "Commander A", ["R"]), _card("o-a1", "Card A1", ["R"]),
        _card("o-bcmd", "Commander B", ["G"]), _card("o-b1", "Card B1", ["G"]),
        _card("o-b2", "Card B2", ["G"]), _card("o-free", "Free Card", ["R"]),
        _card("o-donor", "Donor Card", ["R"]), _card("o-land", "Forest", ["G"], "Land", 0),
    ]:
        repo.upsert_card(s, c)
    repo.upsert_deck(s, {"id": "deck-a", "name": "Deck A", "status": "have",
                         "color_identity": ["R"], "commanders": ["Commander A"]})
    repo.upsert_deck(s, {"id": "deck-b", "name": "Deck B", "status": "have",
                         "color_identity": ["G"], "commanders": ["Commander B"]})
    repo.upsert_deck(s, {"id": "deck-c", "name": "Deck C", "status": "have",
                         "color_identity": ["R"], "commanders": []})
    repo.replace_deck_cards(s, "deck-a", [
        {"oracle_id": "o-acmd", "qty": 1, "board": "commander", "category": None},
        {"oracle_id": "o-a1", "qty": 1, "board": "main", "category": "Ramp"},
    ])
    repo.replace_deck_cards(s, "deck-b", [
        {"oracle_id": "o-bcmd", "qty": 1, "board": "commander", "category": None},
        {"oracle_id": "o-b1", "qty": 1, "board": "main", "category": None},
        {"oracle_id": "o-b2", "qty": 2, "board": "main", "category": None},
        {"oracle_id": "o-land", "qty": 10, "board": "main", "category": "Land"},
    ])
    # Physical copies: a1 in deck A, b1 in deck B, free in collection,
    # donor in deck C, one in mail (not movable).
    repo.insert_instance(s, {"id": "i-a1", "oracle_id": "o-a1",
                             "status": "in_deck", "deck_id": "deck-a"})
    repo.insert_instance(s, {"id": "i-b1", "oracle_id": "o-b1",
                             "status": "in_deck", "deck_id": "deck-b"})
    repo.insert_instance(s, {"id": "i-free", "oracle_id": "o-free",
                             "status": "in_collection"})
    repo.insert_instance(s, {"id": "i-donor", "oracle_id": "o-donor",
                             "status": "in_deck", "deck_id": "deck-c"})
    repo.insert_instance(s, {"id": "i-mail", "oracle_id": "o-free",
                             "status": "in_mail"})


def _canon_snapshot(s):
    """(decks, deck_cards, card_instances, instance_events) row digests —
    the canonical tables draft mode must not touch before Commit."""
    decks = sorted((d.id, d.name, d.status, d.source)
                   for d in s.scalars(select(DeckRow)).all())
    cards = sorted((c.deck_id, c.oracle_id, c.board, c.qty)
                   for c in s.scalars(select(DeckCardRow)).all())
    insts = sorted((i.id, i.status, i.deck_id)
                   for i in s.scalars(select(CardInstanceRow)).all())
    events = sorted((e.instance_id, e.kind, e.from_deck, e.to_deck, e.draft_id)
                    for e in s.scalars(select(InstanceEventRow)).all())
    return decks, cards, insts, events


def _make_draft(s, kind="combine", sources=("deck-a", "deck-b"), prefill=True):
    src_lists = {d: repo.get_deck_cards(s, d) for d in sources}
    draft = repo.create_draft(s, {
        "id": draft_mode.new_draft_id(), "kind": kind, "name": "Test draft",
        "target_name": "Combined Brew",
        "source_decks": [{"id": d, "name": d} for d in sources],
    })
    if prefill:
        for card in draft_mode.prefill_cards(src_lists):
            repo.upsert_draft_card(s, draft["id"], card)
    return draft


# --- domain: creation rules ----------------------------------------------------

def test_validate_create_kinds():
    assert draft_mode.validate_create("combine", ["a", "b"], "T") == ["a", "b"]
    assert draft_mode.validate_create("dismantle", ["a"], "T") == ["a"]
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_create("combine", ["a"], "T")
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_create("dismantle", ["a", "b"], "T")
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_create("wat", ["a", "b"], "T")
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_create("combine", ["a", "a"], "T")
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_create("combine", ["a", "b"], "  ")


def test_prefill_union_commander_wins():
    lists = {
        "a": [{"oracle_id": "o1", "qty": 1, "board": "main", "category": "Ramp"},
              {"oracle_id": "o2", "qty": 1, "board": "commander", "category": None}],
        "b": [{"oracle_id": "o1", "qty": 3, "board": "main", "category": None},
              {"oracle_id": "o3", "qty": 1, "board": "main", "category": None}],
    }
    rows = {r["oracle_id"]: r for r in draft_mode.prefill_cards(lists)}
    assert rows["o1"]["qty"] == 3 and rows["o1"]["category"] == "Ramp"
    assert rows["o2"]["board"] == "commander"
    assert set(rows) == {"o1", "o2", "o3"}


def test_validate_move_state_machine():
    inst = lambda status: {"status": status, "deck_id": "d"}  # noqa: E731
    draft_mode.validate_move("assign", inst("in_collection"))
    draft_mode.validate_move("assign", inst("in_deck"))
    draft_mode.validate_move("unassign", inst("in_deck"))
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_move("assign", inst("in_mail"))
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_move("assign", inst("not_owned"))
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_move("unassign", inst("in_collection"))
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_move("assign", None)
    with pytest.raises(draft_mode.DraftError):
        draft_mode.validate_move("teleport", inst("in_deck"))


# --- domain: commit plan + provenance ------------------------------------------

def test_plan_commit_events_and_updates():
    draft = {"id": "draft-x", "status": "open", "target_name": "New Deck"}
    cards = [{"oracle_id": "o1", "qty": 1, "board": "commander", "category": None,
              "name": "Cmdr", "color_identity": ["R"]},
             {"oracle_id": "o2", "qty": 1, "board": "main", "category": None,
              "name": "Card", "color_identity": ["R", "G"]}]
    moves = [{"instance_id": "i1", "action": "assign"},
             {"instance_id": "i2", "action": "unassign"}]
    insts = {"i1": {"id": "i1", "status": "in_deck", "deck_id": "deck-a"},
             "i2": {"id": "i2", "status": "in_deck", "deck_id": "deck-b"}}
    plan = draft_mode.plan_commit(draft, cards, moves, insts)
    assert plan["deck"]["id"] == "draft:draft-x"
    assert plan["deck"]["source"] == "draft" and plan["deck"]["status"] == "have"
    assert plan["deck"]["commanders"] == ["Cmdr"]
    assert plan["deck"]["color_identity"] == ["G", "R"]
    assert ("i1", "in_deck", "draft:draft-x") in plan["updates"]
    assert ("i2", "in_collection", None) in plan["updates"]
    by_id = {e["instance_id"]: e for e in plan["events"]}
    assert by_id["i1"] == {"instance_id": "i1", "kind": "assigned",
                           "from_deck": "deck-a", "to_deck": "draft:draft-x",
                           "draft_id": "draft-x"}
    assert by_id["i2"]["kind"] == "unassigned" and by_id["i2"]["to_deck"] is None


def test_plan_commit_rejects_empty_and_closed():
    with pytest.raises(draft_mode.DraftError):
        draft_mode.plan_commit({"id": "d", "status": "open"}, [], [], {})
    with pytest.raises(draft_mode.DraftError):
        draft_mode.plan_commit({"id": "d", "status": "committed"},
                               [{"oracle_id": "o", "qty": 1, "board": "main"}], [], {})


def test_provenance_path_summary():
    events = [
        {"ts": "2026-01-01", "kind": "assigned", "from_deck": "deck-a",
         "to_deck": "draft:x", "draft_id": "draft-x"},
        {"ts": "2026-02-01", "kind": "unassigned", "from_deck": "draft:x",
         "to_deck": None, "draft_id": "draft-y"},
    ]
    path = draft_mode.provenance_path("i1", events, {"deck-a": "Deck A", "draft:x": "New"})
    assert path["count"] == 2
    assert path["steps"][0]["summary"] == "moved Deck A → New"
    assert path["steps"][1]["summary"] == "moved New → collection"


# --- domain: suggestions (tuner/reconstruct port) --------------------------------

def test_candidate_score_math():
    # mean of the parts that exist; EDHREC capped at EDHREC_FULL (0.50)
    assert suggestions.candidate_score(edhrec_inclusion=0.25) == 0.5
    assert suggestions.candidate_score(edhrec_inclusion=0.9) == 1.0
    assert suggestions.candidate_score(pillar_fit=0.8, played_with=0.4) == 0.6
    assert suggestions.candidate_score() is None  # unavailable, never 0


def test_build_pools_tiers():
    pools = suggestions.build_pools(
        {"deck-a": [{"oracle_id": "o1", "qty": 2}]},
        {"o2": 3},
        {"o3": {"deck-c": 1}},
    )
    assert pools["o1"]["tier"] == "previous_deck" and pools["o1"]["qty"] == 2
    assert pools["o2"]["tier"] == "owned" and pools["o2"]["qty"] == 3
    assert pools["o3"]["tier"] == "donor_pull" and pools["o3"]["donors"] == {"deck-c": 1}


def test_suggest_adds_own_first_and_color_filter():
    pools = {
        "o-src": {"tier": "previous_deck", "qty": 1, "donors": {}, "source_decks": ["a"]},
        "o-own": {"tier": "owned", "qty": 2, "donors": {}, "source_decks": []},
        "o-new": {"tier": "new", "qty": 0, "donors": {}, "source_decks": []},
        "o-off": {"tier": "owned", "qty": 1, "donors": {}, "source_decks": []},
    }
    facts = {
        "o-src": {"name": "Src", "color_identity": ["R"]},
        "o-own": {"name": "Own", "color_identity": ["R"]},
        "o-new": {"name": "New", "color_identity": []},
        "o-off": {"name": "OffColor", "color_identity": ["U"]},
    }
    out = suggestions.suggest_adds(pools, facts, draft_oracle_ids=[],
                                   color_identity=["R"], scores={})
    tiers = [r["tier"] for r in out["adds"]]
    assert tiers == ["previous_deck", "owned", "new"]  # own-first order
    assert "o-off" not in [r["oracle_id"] for r in out["adds"]]  # off-color dropped
    # already-in-draft cards excluded
    out2 = suggestions.suggest_adds(pools, facts, draft_oracle_ids=["o-src"],
                                    color_identity=["R"], scores={})
    assert "o-src" not in [r["oracle_id"] for r in out2["adds"]]


def test_suggest_adds_donor_rule():
    pools = {
        "o-taken": {"tier": "donor_pull", "qty": 1, "donors": {"deck-c": 1}, "source_decks": []},
        "o-repl": {"tier": "owned", "qty": 1, "donors": {}, "source_decks": []},
    }
    facts = {"o-taken": {"name": "Taken", "color_identity": ["R"]},
             "o-repl": {"name": "Repl", "color_identity": ["R"]}}
    # Replacement scores at least as well -> pull allowed, donor labelled.
    out = suggestions.suggest_adds(
        pools, facts, [], ["R"], scores={"o-taken": 0.5, "o-repl": 0.6})
    taken = next(r for r in out["adds"] if r["oracle_id"] == "o-taken")
    assert taken["donor"]["deck_id"] == "deck-c"
    assert taken["donor"]["replacement_oracle_id"] == "o-repl"
    assert out["hidden_donor_pulls"] == 0
    # No adequate replacement -> pull hidden, reason given.
    out2 = suggestions.suggest_adds(
        pools, facts, [], ["R"], scores={"o-taken": 0.9, "o-repl": 0.1})
    assert "o-taken" not in [r["oracle_id"] for r in out2["adds"]]
    assert out2["hidden_donor_pulls"] == 1 and out2["hidden_reason"]


def test_weak_cuts_math_and_unavailable():
    cards = [
        {"oracle_id": "o-cmd", "name": "Cmdr", "board": "commander", "type_line": "Creature"},
        {"oracle_id": "o-land", "name": "Forest", "board": "main", "type_line": "Land"},
        {"oracle_id": "o-pet", "name": "Pet", "board": "main", "type_line": "Creature"},
        {"oracle_id": "o-a", "name": "A", "board": "main", "type_line": "Creature"},
        {"oracle_id": "o-b", "name": "B", "board": "main", "type_line": "Creature"},
    ]
    # No signals: everything neutral, inputs marked unavailable with reasons.
    out = suggestions.weak_cuts(cards)
    names = [c["name"] for c in out["cuts"]]
    assert "Cmdr" not in names and "Forest" not in names  # never cuts
    assert all(c["weak_score"] == 0 for c in out["cuts"])
    assert set(out["inputs_unavailable"]) == {"edges", "pillar_fit", "edhrec"}
    # Injected signals: the tuner weak-performer math (weak_parts) is what runs.
    out2 = suggestions.weak_cuts(
        cards, degrees={"A": 0, "B": 4, "Pet": 0},
        fits={"A": 0.1, "B": 0.9}, inclusions={"A": 0.0, "B": 0.5},
        pets={"Pet"})
    by_name = {c["name"]: c for c in out2["cuts"]}
    assert "Pet" not in by_name
    assert by_name["A"]["weak_score"] > by_name["B"]["weak_score"]
    assert by_name["A"]["parts"]["edhrec"] == 1.0
    assert by_name["B"]["parts"]["edhrec"] == 0.0  # full inclusion
    assert out2["inputs_unavailable"] == {}


# --- repositories: staging isolation + commit + abandon -------------------------

def test_staging_isolation_until_commit(factory):
    """Build a draft combining two decks: nothing in decks, deck_cards or
    card_instances changes until Commit (acceptance, verbatim)."""
    with session_scope(factory) as s:
        _seed_two_decks(s)
    with session_scope(factory) as s:
        before = _canon_snapshot(s)
        draft = _make_draft(s)
        repo.upsert_draft_card(s, draft["id"],
                               {"oracle_id": "o-free", "qty": 1, "board": "main"})
        repo.remove_draft_card(s, draft["id"], "o-b2", "main")
        repo.stage_move(s, draft["id"], "i-a1", "assign")
        repo.stage_move(s, draft["id"], "i-b1", "assign")
        repo.stage_move(s, draft["id"], "i-free", "assign")
        repo.unstage_move(s, draft["id"], "i-free")
        repo.stage_move(s, draft["id"], "i-free", "assign")
        s.flush()
        assert _canon_snapshot(s) == before  # zero canonical drift
    with session_scope(factory) as s:
        assert _canon_snapshot(s) == before  # and after the staging commit
        state = repo.get_draft_state(s, draft["id"])
        assert state["status"] == "open"
        assert len(state["cards"]) == 6  # 6 prefilled - o-b2 + o-free
        assert len(state["moves"]) == 3


def test_commit_one_transaction_and_provenance(factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)
        draft = _make_draft(s)
        repo.stage_move(s, draft["id"], "i-a1", "assign")   # deck-a -> new
        repo.stage_move(s, draft["id"], "i-free", "assign")  # collection -> new
        repo.stage_move(s, draft["id"], "i-b1", "unassign")  # deck-b -> collection
        draft_id = draft["id"]
    with session_scope(factory) as s:
        result = repo.commit_draft(s, draft_id)
    assert result["deck_id"] == f"draft:{draft_id}"
    assert result["moves_applied"] == 3 and result["events_written"] == 3
    with session_scope(factory) as s:
        deck = repo.get_deck(s, result["deck_id"])
        assert deck["name"] == "Combined Brew" and deck["source"] == "draft"
        assert deck["status"] == "have"
        cards = repo.get_deck_cards(s, result["deck_id"])
        assert {c["oracle_id"] for c in cards} == {
            "o-acmd", "o-a1", "o-bcmd", "o-b1", "o-b2", "o-land"}
        insts = {i["id"]: i for i in repo.list_instances(s)}
        assert insts["i-a1"]["deck_id"] == result["deck_id"]
        assert insts["i-a1"]["status"] == "in_deck"
        assert insts["i-free"]["deck_id"] == result["deck_id"]
        assert insts["i-b1"]["status"] == "in_collection"
        assert insts["i-b1"]["deck_id"] is None
        # Provenance: "this copy moved A→B" is queryable, draft-linked.
        path = repo.instance_provenance(s, "i-a1")
        assert path["steps"][0]["from_deck"] == "deck-a"
        assert path["steps"][0]["to_deck"] == result["deck_id"]
        assert path["steps"][0]["draft_id"] == draft_id
        assert path["steps"][0]["summary"] == f"moved Deck A → Combined Brew"
        free = repo.instance_provenance(s, "i-free")
        assert free["steps"][0]["summary"] == "moved collection → Combined Brew"
        b1 = repo.instance_provenance(s, "i-b1")
        assert b1["steps"][0]["kind"] == "unassigned"
        assert b1["steps"][0]["to_deck"] is None
        assert repo.get_draft(s, draft_id)["status"] == "committed"


def test_commit_failure_rolls_back_everything(factory):
    """A move that was legal when staged but no longer is fails the commit;
    the one transaction leaves zero canonical changes."""
    with session_scope(factory) as s:
        _seed_two_decks(s)
        draft = _make_draft(s)
        repo.stage_move(s, draft["id"], "i-a1", "assign")
        draft_id = draft["id"]
    with session_scope(factory) as s:  # the world changes after staging
        inst = s.get(CardInstanceRow, "i-a1")
        inst.status = "not_owned"
        inst.deck_id = None
    with session_scope(factory) as s:
        before = _canon_snapshot(s)
        with pytest.raises(draft_mode.DraftError):
            repo.commit_draft(s, draft_id)
        s.rollback()
    with session_scope(factory) as s:
        assert _canon_snapshot(s) == before
        assert repo.get_deck(s, f"draft:{draft_id}") is None
        assert repo.get_draft(s, draft_id)["status"] == "open"  # still editable


def test_commit_twice_rejected(factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)
        draft_id = _make_draft(s)["id"]
        repo.commit_draft(s, draft_id)
    with session_scope(factory) as s:
        with pytest.raises(draft_mode.DraftError):
            repo.commit_draft(s, draft_id)
        s.rollback()


def test_abandon_zero_residue(factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)
        draft = _make_draft(s)
        repo.stage_move(s, draft["id"], "i-a1", "assign")
        draft_id = draft["id"]
    with session_scope(factory) as s:
        before = _canon_snapshot(s)
        result = repo.abandon_draft(s, draft_id)
    assert result["staged_cards_deleted"] == 6
    assert result["staged_moves_deleted"] == 1
    with session_scope(factory) as s:
        assert _canon_snapshot(s) == before  # canonical untouched
        # zero residue in every staging table
        for model in (DraftRow, DraftCardRow, DraftInstanceMoveRow):
            n = s.scalar(select(func.count()).select_from(model)
                         .filter_by(draft_id=draft_id)
                         if model is not DraftRow else
                         select(func.count()).select_from(model).filter_by(id=draft_id))
            assert n == 0
        assert repo.get_draft(s, draft_id) is None


# --- /api/v2/drafts router -------------------------------------------------------

@pytest.fixture()
def client(factory):
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_full_draft_flow(client, factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)

    r = client.post("/api/v2/drafts", json={
        "kind": "combine", "source_deck_ids": ["deck-a", "deck-b"],
        "name": "My draft", "target_name": "Gruul Smash", "prefill": True})
    assert r.status_code == 201, r.text
    draft_id = r.json()["draft"]["id"]
    assert len(r.json()["cards"]) == 6

    # Stage edits + moves — still nothing canonical.
    r = client.post(f"/api/v2/drafts/{draft_id}/cards",
                    json={"oracle_id": "o-free", "qty": 1, "board": "main"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v2/drafts/{draft_id}/moves",
                    json={"instance_id": "i-a1", "action": "assign"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v2/drafts/{draft_id}/moves",
                    json={"instance_id": "i-mail", "action": "assign"})
    assert r.status_code == 400  # in_mail is not assignable
    with session_scope(factory) as s:
        assert repo.get_deck(s, f"draft:{draft_id}") is None
        assert repo.event_counts_by_instance(s, ["i-a1"]) == {}

    r = client.get(f"/api/v2/drafts/{draft_id}/suggestions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inputs_unavailable"]  # honest unavailable markers
    assert all(a["tier"] in ("previous_deck", "owned", "donor_pull", "new")
               for a in body["adds"])

    r = client.post(f"/api/v2/drafts/{draft_id}/commit")
    assert r.status_code == 200, r.text
    committed = r.json()
    assert committed["events_written"] == 1

    r = client.get("/api/v2/instances/i-a1/provenance")
    assert r.status_code == 200, r.text
    path = r.json()
    assert path["steps"][0]["from_deck"] == "deck-a"
    assert path["steps"][0]["to_deck"] == committed["deck_id"]
    assert path["steps"][0]["draft_id"] == draft_id

    # Committed drafts are closed to further edits.
    r = client.post(f"/api/v2/drafts/{draft_id}/cards",
                    json={"oracle_id": "o-free", "qty": 1, "board": "main"})
    assert r.status_code == 409


def test_api_abandon_flow(client, factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)
    r = client.post("/api/v2/drafts", json={
        "kind": "dismantle", "source_deck_ids": ["deck-b"],
        "name": "Tear down B", "target_name": "Parts Bin", "prefill": True})
    assert r.status_code == 201, r.text
    draft_id = r.json()["draft"]["id"]
    r = client.post(f"/api/v2/drafts/{draft_id}/abandon")
    assert r.status_code == 200, r.text
    with session_scope(factory) as s:
        assert repo.get_draft(s, draft_id) is None
        assert repo.get_deck_cards(s, "deck-b")  # source deck intact
    r = client.get(f"/api/v2/drafts/{draft_id}")
    assert r.status_code == 404


def test_api_create_validation(client, factory):
    with session_scope(factory) as s:
        _seed_two_decks(s)
    r = client.post("/api/v2/drafts", json={
        "kind": "combine", "source_deck_ids": ["deck-a"],
        "name": "x", "target_name": "y"})
    assert r.status_code == 400
    r = client.post("/api/v2/drafts", json={
        "kind": "combine", "source_deck_ids": ["deck-a", "nope"],
        "name": "x", "target_name": "y"})
    assert r.status_code == 404
