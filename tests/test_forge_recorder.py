"""Forge auto-recorder: parser, analyzer, store (sqlite) and API tests.

Fixtures are real captured logs (javaagent FORGELOG lines plus the plain
"Game Result:" terminator line); forge_human_game.txt is the same capture
with player names substituted (Ai(1)-Deck1 -> Andrew, Ai(2)-Deck2 -> Benni).
They use a .txt extension because *.log is gitignored.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.infra.db.orm_models import Base, UserRow
from src.server.infra.db.session import session_scope
from src.server.workers.forge_recorder import analyzer, parser, store

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> list[str]:
    with open(os.path.join(FIXTURES, name), errors="replace") as f:
        return f.read().splitlines()


# --- parser ---------------------------------------------------------------

def test_segment_sim_fixture_is_one_game():
    blocks = parser.segment_games(_fixture("forge_sim_game.txt"))
    # The capture is one Commander game; lines flushed after MATCH_RESULTS
    # (no fresh "Turn 1") merge back instead of forming a phantom game.
    assert len(blocks) == 1


def test_parse_sim_fixture():
    block = parser.segment_games(_fixture("forge_sim_game.txt"))[0]
    game = parser.parse_game(block)
    assert game is not None
    assert game["is_simulation"] is True
    assert game["players"] == ["Ai(1)-Deck1", "Ai(2)-Deck2"]
    assert game["winner"] == "Ai(1)-Deck1"
    assert game["win_reason"] == "all opponents have lost"
    assert game["turns"] >= 85
    assert game["match_score"] == {"Ai(1)-Deck1": 1, "Ai(2)-Deck2": 0}
    assert game["duration_ms"] == 52663
    st = game["player_stats"]["Ai(1)-Deck1"]
    assert st["kept_hand"] == 7 and st["mulligans"] == 0
    assert st["lands_played"] >= 1
    assert st["spells_cast"] >= 1
    # Noise types are dropped from stored events, essence kept.
    types = {t for t, _ in game["events"]}
    assert "PHASE" not in types and "MANA" not in types
    assert "TURN" in types and "GAME_OUTCOME" in types


def test_parse_human_fixture():
    block = parser.segment_games(_fixture("forge_human_game.txt"))[0]
    game = parser.parse_game(block)
    assert game is not None
    assert game["is_simulation"] is False
    assert set(game["players"]) == {"Andrew", "Benni"}
    assert game["winner"] == "Andrew"


def test_segment_two_games_on_turn_one_restart():
    lines = (
        _mini_game("Andrew", "Benni", winner="Andrew")
        + _mini_game("Andrew", "Benni", winner="Benni")
    )
    blocks = parser.segment_games(lines)
    assert len(blocks) == 2
    g1 = parser.parse_game(blocks[0])
    g2 = parser.parse_game(blocks[1])
    assert g1["winner"] == "Andrew" and g2["winner"] == "Benni"
    assert g1["game_uid"] != g2["game_uid"]


def test_segment_drops_incomplete_trailing_game():
    lines = _mini_game("Andrew", "Benni", winner="Andrew")
    lines += ["FORGELOG|TURN|Turn 1 (Andrew)", "FORGELOG|PHASE|Andrew's Untap step"]
    blocks = parser.segment_games(lines)
    assert len(blocks) == 1


def test_parse_garbage_returns_none():
    assert parser.parse_game(["random noise", "INFO | nothing"]) is None
    assert parser.segment_games([]) == []


def _mini_game(p1: str, p2: str, winner: str) -> list[str]:
    loser = p2 if winner == p1 else p1
    return [
        f"FORGELOG|MULLIGAN|{p1} has kept a hand of 7 cards",
        f"FORGELOG|MULLIGAN|{p2} has kept a hand of 6 cards",
        f"FORGELOG|TURN|Turn 1 ({p1})",
        f"FORGELOG|LAND|{p1} played Mountain (81)",
        f"FORGELOG|STACK_ADD|{p1} cast Lightning Bolt targeting [{p2}]",
        f"FORGELOG|DAMAGE|Lightning Bolt (4) deals 3 non-combat damage to {p2}.",
        f"FORGELOG|LIFE|Life: {p2} 40 > 37",
        f"FORGELOG|TURN|Turn 2 ({p2})",
        f"FORGELOG|GAME_OUTCOME|Turn 1",
        f"FORGELOG|GAME_OUTCOME|{winner} has won because all opponents have lost",
        f"FORGELOG|GAME_OUTCOME|{loser} has lost because life total reached 0",
        f"FORGELOG|MATCH_RESULTS|{p1}: {1 if winner == p1 else 0} {p2}: {1 if winner == p2 else 0} ",
    ]


# --- analyzer -------------------------------------------------------------

def test_build_profile_human_games():
    g1 = parser.parse_game(_mini_game("Andrew", "Benni", winner="Andrew"))
    g2 = parser.parse_game(_mini_game("Andrew", "Benni", winner="Benni"))
    profile = analyzer.build_profile("u1", [g1, g2])
    assert profile["games_analyzed"] == 2
    assert profile["primary_player"] == "Andrew"
    andrew = profile["players"]["Andrew"]
    assert andrew["games"] == 2 and andrew["wins"] == 1 and andrew["losses"] == 1
    assert andrew["win_rate"] == 0.5
    assert andrew["avg_lands"] == 1.0 and andrew["avg_spells"] == 1.0
    assert andrew["avg_kept_hand"] == 7 and andrew["mulligan_rate"] == 0.0
    benni = profile["players"]["Benni"]
    assert benni["avg_kept_hand"] == 6 and benni["mulligan_rate"] == 1.0
    assert benni["avg_damage_taken"] == 3.0 and benni["avg_life_lost"] == 3.0
    assert ("Lightning Bolt", 2) in andrew["favorite_cards"]
    assert profile["traits"]
    text = analyzer.render_profile_text(profile)
    assert "Andrew" in text and "games analyzed: 2" in text


def test_build_profile_skips_simulations():
    sim = parser.parse_game(parser.segment_games(_fixture("forge_sim_game.txt"))[0])
    profile = analyzer.build_profile("u1", [sim])
    assert profile["games_analyzed"] == 0
    assert profile["primary_player"] is None
    assert profile["players"] == {}


def test_summarize_game_shape():
    game = parser.parse_game(_mini_game("Andrew", "Benni", winner="Andrew"))
    s = analyzer.summarize_game(game)
    assert s["winner"] == "Andrew" and s["human_players"] == ["Andrew", "Benni"]
    assert s["key_plays"][0] == {"player": "Andrew", "card": "Lightning Bolt"}


# --- store (sqlite) --------------------------------------------------------

@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def _make_user(factory, uid="u1"):
    with session_scope(factory) as s:
        s.add(UserRow(id=uid, name="Andrew", archidekt_user_id=""))


def test_ingest_store_and_profile(factory):
    _make_user(factory)
    lines = _fixture("forge_human_game.txt")
    with session_scope(factory) as s:
        result = store.ingest_lines(s, None, lines)
    assert len(result["stored"]) == 1 and result["duplicates"] == []
    # Idempotent re-ingest.
    with session_scope(factory) as s:
        again = store.ingest_lines(s, None, lines)
    assert again["stored"] == [] and len(again["duplicates"]) == 1
    with session_scope(factory) as s:
        games = store.list_games(s)
    assert len(games) == 1
    assert games[0]["winner"] == "Andrew" and games[0]["is_simulation"] is False
    with session_scope(factory) as s:
        prof = store.get_profile(s)
    assert prof is not None
    assert prof["games_analyzed"] == 1
    assert prof["profile"]["primary_player"] == "Andrew"
    assert "Andrew" in prof["text"]


def test_ingest_requires_user(factory):
    with session_scope(factory) as s:
        with pytest.raises(store.IngestError):
            store.ingest_lines(s, None, _mini_game("Andrew", "Benni", winner="Andrew"))


def test_sim_game_stored_but_not_profiled(factory):
    _make_user(factory)
    with session_scope(factory) as s:
        result = store.ingest_lines(s, None, _fixture("forge_sim_game.txt"))
    assert len(result["stored"]) == 1
    with session_scope(factory) as s:
        games = store.list_games(s)
        prof = store.get_profile(s)
    assert games[0]["is_simulation"] is True
    assert prof["games_analyzed"] == 0


# --- API -------------------------------------------------------------------

@pytest.fixture()
def client(factory, monkeypatch):
    from fastapi.testclient import TestClient
    import src.server.app.main as app_mod
    from src.server.workers.forge_recorder import store as store_mod

    monkeypatch.setattr(store_mod, "session_scope",
                        lambda: session_scope(factory))
    _make_user(factory)
    return TestClient(app_mod.app)


def test_api_ingest_games_profile(client):
    resp = client.post("/api/forge/ingest",
                       json={"lines": _fixture("forge_human_game.txt")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] and len(body["stored"]) == 1

    resp = client.get("/api/forge/games")
    assert resp.status_code == 200
    games = resp.json()["games"]
    assert len(games) == 1 and games[0]["winner"] == "Andrew"

    resp = client.get("/api/forge/play-style")
    assert resp.status_code == 200
    prof = resp.json()
    assert prof["games_analyzed"] == 1
    assert prof["profile"]["primary_player"] == "Andrew"

    # Rebuild endpoint is stable.
    resp = client.post("/api/forge/play-style/rebuild", json={})
    assert resp.status_code == 200 and resp.json()["games_analyzed"] == 1


def test_api_ingest_bad_body(client):
    resp = client.post("/api/forge/ingest", json={"lines": "not-a-list"})
    assert resp.status_code == 400
    resp = client.post("/api/forge/ingest", json={"lines": [], "played_at": "nope"})
    assert resp.status_code == 400


def test_api_profile_404_before_any_game(client):
    resp = client.get("/api/forge/play-style")
    assert resp.status_code == 404
