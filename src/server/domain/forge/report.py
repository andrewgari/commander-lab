"""Deterministic analysis notes and profile-snapshot payloads for imported
Forge games (Slice 4). Pure text/metric assembly over parser output — no
LLM, no infra (AGENT_RULES.md §2/§5: numbers and labels are code).

`build_game_notes` becomes `games.notes`; `build_snapshot` becomes the
append-only `profile_snapshots` row with trigger='game'. Both state the
unavailable fields and their reasons in plain language (§4).
"""
from __future__ import annotations

from typing import Optional

from src.server.domain.forge import game_log


def build_game_notes(deck_name: str, user_seat: int, player: dict, derived: dict) -> str:
    """One deterministic analysis paragraph for a single imported game."""
    stats = game_log.seat_stats(player)
    result, result_reason = game_log.game_result(derived, user_seat)
    seats = derived.get("seat_count") or 0
    parts = []
    label = player.get("deck") or f"seat {user_seat}"
    if result is None:
        parts.append(f"{deck_name} (seat {user_seat}, \"{label}\") played a "
                     f"{seats}-player game; result unavailable: {result_reason}.")
    else:
        parts.append(f"{deck_name} (seat {user_seat}, \"{label}\") {('won' if result == 'win' else 'lost')} "
                     f"a {seats}-player game" + (f" in {derived['turns']} turns." if derived.get("turns") else "."))
    play = (f"Cast {stats['spells_cast']} spell(s) across {stats['own_turns']} own turn(s); "
            f"{stats['lands_played']} land(s) played")
    if stats["first_land_turn"] is not None:
        play += f", first land on turn {stats['first_land_turn']} ({stats['first_land']})"
    parts.append(play + ".")
    if stats["distinct_cards_cast"]:
        parts.append(f"{stats['distinct_cards_cast']} distinct card(s) cast, earliest on turn "
                     f"{stats['earliest_cast_turn']}.")
    parts.append(f"Draw/stuck data unavailable: {game_log.REASON_DRAWS_STUCK}.")
    if derived.get("turns") is None:
        parts.append(f"Turn count unavailable: {game_log.REASON_TURNS}.")
    return " ".join(parts)


def game_metrics(game_id: int, user_seat: int, player: dict, derived: dict,
                 result: Optional[str]) -> dict:
    """The per-game metrics block stored on the snapshot and served to the UI."""
    stats = game_log.seat_stats(player)
    return {
        "game_id": game_id,
        "result": result,
        "turns": derived.get("turns"),
        "duration_ms": derived.get("duration_ms"),
        "seat_count": derived.get("seat_count"),
        "user_seat": user_seat,
        "own_turns": stats["own_turns"],
        "spells_cast": stats["spells_cast"],
        "lands_played": stats["lands_played"],
        "first_land_turn": stats["first_land_turn"],
        "distinct_cards_cast": stats["distinct_cards_cast"],
    }


def build_snapshot(deck: dict, import_id: int, log_hash: str, games: list) -> dict:
    """(inputs, metrics, summary_text) for the trigger='game' snapshot.

    `games` is a list of {game_id, user_seat, player, derived, result, notes}.
    """
    deck_name = deck.get("name") or "This deck"
    metrics_games = [
        game_metrics(g["game_id"], g["user_seat"], g["player"], g["derived"], g["result"])
        for g in games
    ]
    wins = sum(1 for g in games if g["result"] == "win")
    losses = sum(1 for g in games if g["result"] == "loss")
    unknown = len(games) - wins - losses
    inputs = {
        "deck_id": deck.get("id"),
        "deck_name": deck_name,
        "import_id": import_id,
        "game_ids": [g["game_id"] for g in games],
        "log_hash": log_hash,
        "parser_version": game_log.PARSER_VERSION,
    }
    metrics = {
        "games": metrics_games,
        "count": len(games),
        "record": {"wins": wins, "losses": losses, "unknown": unknown},
        "unavailable": {
            "draws": game_log.REASON_DRAWS,
            "stuck": game_log.REASON_STUCK,
            "played_at": game_log.REASON_PLAYED_AT,
        },
    }
    n = len(games)
    if n == 1:
        g = games[0]
        stats = game_log.seat_stats(g["player"])
        res = {"win": "won", "loss": "lost"}.get(g["result"], "finished (result unavailable)")
        turns = g["derived"].get("turns")
        summary = (
            f"{deck_name} {res} a {g['derived'].get('seat_count')}-player Forge game"
            + (f" in {turns} turns" if turns else "")
            + f": {stats['spells_cast']} spell(s) cast, {stats['lands_played']} land(s) played."
        )
        summary += f" Draw/stuck data unavailable: {game_log.REASON_DRAWS_STUCK}."
    else:
        summary = (f"Imported {n} Forge games for {deck_name}: "
                   f"{wins} win(s), {losses} loss(es)"
                   + (f", {unknown} with unavailable result" if unknown else "")
                   + f". Draw/stuck data unavailable: {game_log.REASON_DRAWS_STUCK}.")
    return {"inputs": inputs, "metrics": metrics, "summary_text": summary}
