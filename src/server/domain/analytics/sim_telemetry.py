"""Per-game sim telemetry + aggregation (schema: docs/design/simulate-forge-telemetry.md).

Pure functions. `build_telemetry(deck, params, report, owned_ids)` turns the daemon report into the
`telemetry` block persisted inside the latest saved sim doc (`deck:sim:{registry_id}`, replaced by
each completed run, untouched by failed ones).

Per-game input (report["games_log"][i]), all keys optional:
    opponents[] {seat, deck, ai}      pod (seat 1 is the tested deck)
    winner, user_won, total_turns, win_reason
    players[] {deck, seat, curve:[{turn, land_played, mana_spent, spells_cast}],
               cards:{name:{drawn, cast_turn, stuck}}}
Missing data stays null (Forge cannot yet emit draws/hand state); it is never zero-filled.

Card metrics exist ONLY for owned decks (tested deck + owned decks seated), keyed by deck id.
"""
from __future__ import annotations

from statistics import mean, median
from typing import Any, Iterable, Mapping, Optional, Set

SCHEMA_VERSION = "1"


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _stat(values: list) -> dict:
    v = [x for x in values if x is not None]
    return {"n": len(v), "mean": round(mean(v), 3) if v else None, "median": median(v) if v else None}


def owned_deck_ids(r_decks: Iterable[Mapping], tested: Mapping) -> Set[str]:
    """Opponent-style ids (`user_{id}`) of every Commander Lab deck, tested deck included."""
    from .sim_service import user_opponent_id

    ids = {user_opponent_id(d) for d in r_decks if d.get("name")}
    ids.add(user_opponent_id(tested))
    return ids


def _clean_game(g: Mapping, tested_id: str, owned: Set[str]) -> dict:
    pod = [{"seat": 1, "deck_id": tested_id, "owned": True, "tested": True}]
    for o in g.get("opponents") or []:
        d = str(o.get("deck") or "")
        pod.append({"seat": o.get("seat"), "deck_id": d, "ai": o.get("ai"), "owned": d in owned, "tested": False})
    cards: dict = {}
    curves: dict = {}
    for p in g.get("players") or []:
        did = str(p.get("deck") or "")
        if not did:
            continue
        if isinstance(p.get("curve"), list):
            curves[did] = p["curve"]  # full curve, every game, every seat
        if did in owned and isinstance(p.get("cards"), Mapping):
            cards[did] = {n: {"drawn": c.get("drawn"), "cast_turn": c.get("cast_turn"), "stuck": c.get("stuck")}
                          for n, c in p["cards"].items() if isinstance(c, Mapping)}
    return {"game": g.get("game"), "pod": pod, "winner": g.get("winner"), "user_won": bool(g.get("user_won")),
            "total_turns": g.get("total_turns"), "win_reason": g.get("win_reason"),
            "curves": curves, "card_metrics": cards}


def aggregate(games: list) -> dict:
    n = len(games)
    wins = sum(1 for g in games if g["user_won"])
    lengths = [g["total_turns"] for g in games if _num(g.get("total_turns")) and g["total_turns"] > 0]
    per_card: dict = {}
    for g in games:
        for did, cards in g["card_metrics"].items():
            for name, c in cards.items():
                a = per_card.setdefault(did, {}).setdefault(name, {"games": 0, "drawn": [], "cast": [], "stuck": [], "turns": []})
                a["games"] += 1
                a["drawn"].append(c.get("drawn"))
                a["cast"].append(None if c.get("drawn") is None and c.get("cast_turn") is None else c.get("cast_turn") is not None)
                a["stuck"].append(c.get("stuck"))
                if c.get("cast_turn") is not None:
                    a["turns"].append(c["cast_turn"])
    cards_out: dict = {}
    for did, cs in per_card.items():
        for name, a in cs.items():
            def cnt(lst):
                obs = [x for x in lst if x is not None]
                return {"n": len(obs), "count": sum(1 for x in obs if x)}
            cards_out.setdefault(did, {})[name] = {"games": a["games"], "drawn": cnt(a["drawn"]), "cast": cnt(a["cast"]),
                                                   "stuck_in_hand": cnt(a["stuck"]), "first_cast_turn": _stat(a["turns"])}
    return {"games": n, "user_wins": wins, "win_rate": {"n": n, "value": round(wins / n, 4) if n else None},
            "game_length": _stat(lengths), "cards": cards_out}


def build_telemetry(deck: Mapping, params: Mapping, report: Mapping, owned_ids: Set[str]) -> dict:
    from .sim_service import user_opponent_id

    tested_id = user_opponent_id(deck)
    games = [_clean_game(g, tested_id, owned_ids) for g in report.get("games_log") or [] if isinstance(g, Mapping)]
    return {"schema_version": SCHEMA_VERSION, "deck_id": tested_id,
            "params": {k: params.get(k) for k in ("opponent_mode", "games", "opponents", "notes")},
            "notes": params.get("notes") or "", "games": games, "aggregate": aggregate(games)}
