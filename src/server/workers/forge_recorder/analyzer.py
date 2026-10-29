"""Turn parsed Forge game records into per-game summaries and a cumulative
play-style profile.

Profile shape (stored as JSONB in play_style_profile.profile):

    {
      "user_id": str,
      "games_analyzed": int,          # human (non-simulation) games only
      "primary_player": str | None,   # most frequent human player name
      "players": {
        "<name>": {
          "games", "wins", "losses", "win_rate",
          "avg_turns", "avg_kept_hand", "mulligan_rate",
          "avg_lands", "avg_spells", "spells_per_turn",
          "avg_life_lost", "avg_damage_taken",
          "favorite_cards": [[card, cast_count], ... up to 10]
        }
      },
      "traits": [str, ...]            # plain-language bullets for the primary player
    }

Only games with at least one human (non-"Ai(...)") player feed the profile;
AI-vs-AI simulation games are recorded in `games` but excluded here.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable, List, Optional

from src.server.workers.forge_recorder.parser import AI_PLAYER_RE


def human_players(game: dict) -> List[str]:
    return [p for p in game.get("players") or [] if not AI_PLAYER_RE.match(p)]


def summarize_game(game: dict) -> dict:
    """Compact per-game summary stored alongside the raw events."""
    humans = human_players(game)
    stats = game.get("player_stats") or {}
    key_plays = []
    for name in game.get("players") or []:
        casts = (stats.get(name) or {}).get("casts") or []
        for card in casts[:3]:
            key_plays.append({"player": name, "card": card})
    return {
        "game_uid": game.get("game_uid"),
        "players": game.get("players") or [],
        "human_players": humans,
        "is_simulation": bool(game.get("is_simulation")),
        "turns": game.get("turns") or 0,
        "winner": game.get("winner"),
        "win_reason": game.get("win_reason"),
        "losers": game.get("losers") or [],
        "duration_ms": game.get("duration_ms"),
        "player_stats": stats,
        "key_plays": key_plays[:12],
    }


def _new_aggregate() -> dict:
    return {
        "games": 0, "wins": 0, "losses": 0,
        "turns": 0, "kept_hands": [], "mulligan_games": 0,
        "lands": 0, "spells": 0,
        "life_lost": 0, "damage_taken": 0,
        "cards": Counter(),
    }


def build_profile(user_id: str, games: Iterable[dict]) -> dict:
    """Aggregate parsed game records into a cumulative play-style profile.

    `games` are parser records (or stored rows' parsed payload). Simulation
    games and games with no human players are skipped.
    """
    aggs: dict = {}
    analyzed = 0
    for game in games:
        humans = human_players(game)
        if not humans or game.get("is_simulation"):
            continue
        analyzed += 1
        stats = game.get("player_stats") or {}
        winner = game.get("winner")
        for name in humans:
            agg = aggs.setdefault(name, _new_aggregate())
            agg["games"] += 1
            if winner == name:
                agg["wins"] += 1
            elif winner:
                agg["losses"] += 1
            agg["turns"] += int(game.get("turns") or 0)
            st = stats.get(name) or {}
            if st.get("kept_hand") is not None:
                agg["kept_hands"].append(st["kept_hand"])
            if (st.get("mulligans") or 0) > 0:
                agg["mulligan_games"] += 1
            agg["lands"] += int(st.get("lands_played") or 0)
            agg["spells"] += int(st.get("spells_cast") or 0)
            agg["life_lost"] += int(st.get("life_lost") or 0)
            agg["damage_taken"] += int(st.get("damage_taken") or 0)
            for card in st.get("casts") or []:
                agg["cards"][card] += 1

    players_out = {}
    primary: Optional[str] = None
    primary_games = 0
    for name, agg in sorted(aggs.items()):
        games_n = agg["games"] or 1
        avg_turns = agg["turns"] / games_n
        avg_spells = agg["spells"] / games_n
        players_out[name] = {
            "games": agg["games"],
            "wins": agg["wins"],
            "losses": agg["losses"],
            "win_rate": round(agg["wins"] / games_n, 3),
            "avg_turns": round(avg_turns, 1),
            "avg_kept_hand": (round(sum(agg["kept_hands"]) / len(agg["kept_hands"]), 2)
                              if agg["kept_hands"] else None),
            "mulligan_rate": round(agg["mulligan_games"] / games_n, 3),
            "avg_lands": round(agg["lands"] / games_n, 1),
            "avg_spells": round(avg_spells, 1),
            "spells_per_turn": round(avg_spells / avg_turns, 2) if avg_turns else None,
            "avg_life_lost": round(agg["life_lost"] / games_n, 1),
            "avg_damage_taken": round(agg["damage_taken"] / games_n, 1),
            "favorite_cards": agg["cards"].most_common(10),
        }
        if agg["games"] > primary_games:
            primary, primary_games = name, agg["games"]

    return {
        "user_id": user_id,
        "games_analyzed": analyzed,
        "primary_player": primary,
        "players": players_out,
        "traits": _traits(primary, players_out.get(primary) if primary else None),
    }


def _traits(primary: Optional[str], p: Optional[dict]) -> List[str]:
    if not primary or not p:
        return ["No recorded human Forge games yet."]
    traits = [
        f"Record: {p['wins']}W-{p['losses']}L over {p['games']} recorded games "
        f"({p['win_rate']:.0%} win rate).",
        f"Games average {p['avg_turns']} turns; casts about {p['avg_spells']} "
        f"spells per game (~{p['spells_per_turn']} per turn)." if p.get("spells_per_turn")
        else f"Games average {p['avg_turns']} turns.",
    ]
    if p.get("avg_kept_hand") is not None:
        if p["mulligan_rate"] <= 0.15:
            traits.append(f"Rarely mulligans ({p['mulligan_rate']:.0%} of games); "
                          f"average kept hand is {p['avg_kept_hand']} cards.")
        elif p["mulligan_rate"] >= 0.5:
            traits.append(f"Mulligans aggressively ({p['mulligan_rate']:.0%} of games); "
                          f"average kept hand is {p['avg_kept_hand']} cards.")
        else:
            traits.append(f"Mulligans in {p['mulligan_rate']:.0%} of games; "
                          f"average kept hand is {p['avg_kept_hand']} cards.")
    traits.append(f"Plays about {p['avg_lands']} lands per game.")
    traits.append(f"Loses about {p['avg_life_lost']} life per game "
                  f"(~{p['avg_damage_taken']} from direct damage).")
    favs = [c for c, _ in (p.get("favorite_cards") or [])[:5]]
    if favs:
        traits.append("Most-cast cards: " + ", ".join(favs) + ".")
    return traits


def render_profile_text(profile: dict) -> str:
    """Plain-text rendering for the Honcho peer card / oracle consumption."""
    primary = profile.get("primary_player")
    lines = [f"Andrew's Forge play-style profile (games analyzed: "
             f"{profile.get('games_analyzed', 0)})."]
    if primary:
        lines.append(f"Primary player name in Forge: {primary}.")
    for t in profile.get("traits") or []:
        lines.append(f"- {t}")
    others = [n for n in (profile.get("players") or {}) if n != primary]
    if others:
        lines.append("Other human player names seen: " + ", ".join(others) + ".")
    return "\n".join(lines)
