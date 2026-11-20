"""Per-game card/curve telemetry from verbose Forge `sim` logs (stdlib only).

Shared by the forge-sim daemon (deployed copy in mtg-forge/forge/sim-workspace) and tests.
Output per player matches analytics/sim_telemetry.py input:
    {"seat", "deck", "curve":[{turn, land_played, mana_spent, spells_cast}],
     "cards":{name:{drawn, cast_turn, stuck}}}
Turn numbers are the player's OWN turn count (1 = their first turn).
Forge logs no draws/hand contents, so `drawn` and `stuck` are None (never guessed);
`mana_spent` is None (Forge mana lines carry no player). Cards only appear if cast.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Optional

RE_RESULT = re.compile(r"^Game Result: Game (\d+) ended")
RE_TURN = re.compile(r"^Turn: Turn (\d+) \(Ai\((\d)\)-")
RE_LAND = re.compile(r"^Land: Ai\((\d)\)-.*? played (.+?)(?: \(\d+\))?\s*$")
RE_CAST = re.compile(r"^Add To Stack: Ai\((\d)\)-.*? cast (.+?)(?: targeting \[.*)?\s*$")


def split_game_blocks(text: str) -> List[List[str]]:
    """One list of lines per completed game (same boundary the daemon uses)."""
    blocks: List[List[str]] = []
    cur: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r")
        cur.append(line)
        if RE_RESULT.match(line):
            blocks.append(cur)
            cur = []
    return blocks


def parse_game(lines: Iterable[str], seat_decks: Mapping[int, str]) -> List[dict]:
    """Return players[] for one game. Never raises on malformed lines."""
    own_turn: Dict[int, int] = {s: 0 for s in seat_decks}
    curve: Dict[int, Dict[int, dict]] = {s: {} for s in seat_decks}
    cards: Dict[int, dict] = {s: {} for s in seat_decks}

    def row(seat: int) -> Optional[dict]:
        t = own_turn.get(seat, 0)
        if seat not in curve or t < 1:
            return None
        return curve[seat].setdefault(t, {"turn": t, "land_played": None, "mana_spent": None, "spells_cast": 0})

    for ln in lines:
        try:
            m = RE_TURN.match(ln)
            if m:
                seat = int(m.group(2))
                if seat in own_turn:
                    own_turn[seat] += 1
                    row(seat)
                continue
            m = RE_LAND.match(ln)
            if m and int(m.group(1)) in own_turn:
                r = row(int(m.group(1)))
                if r is not None and r["land_played"] is None:
                    r["land_played"] = m.group(2).strip()
                continue
            m = RE_CAST.match(ln)
            if m and int(m.group(1)) in own_turn:
                seat, name = int(m.group(1)), m.group(2).strip()
                r = row(seat)
                if r is None:
                    continue  # cast before any turn started: ignore
                r["spells_cast"] += 1
                c = cards[seat].setdefault(name, {"drawn": None, "cast_turn": r["turn"], "stuck": None})
                c["cast_turn"] = min(c["cast_turn"], r["turn"])
        except (ValueError, IndexError):
            continue
    return [{"seat": s, "deck": seat_decks[s], "curve": [curve[s][t] for t in sorted(curve[s])], "cards": cards[s]}
            for s in sorted(seat_decks)]


def enrich_games_log(log_text: str, games_log: List[dict], user_deck_id: str) -> List[dict]:
    """Attach players[] to each games_log record (in place) from a verbose Forge log.

    games_log[i] is matched to the i-th completed game block. Seat 1 is the tested deck
    (`user_deck_id`, e.g. "user_417707"); seats 2.. come from the record's `opponents`
    (their `deck` is the backend-facing id: `user_{id}` for Commander Lab decks).
    Records with no matching verbose block (e.g. `-q` runs) keep no players[] (stays null).
    """
    blocks = split_game_blocks(log_text)
    for rec, block in zip(games_log, blocks):
        seats = {1: user_deck_id}
        for o in rec.get("opponents") or []:
            if isinstance(o.get("seat"), int):
                seats[o["seat"]] = str(o.get("deck") or "")
        rec["players"] = parse_game(block, seats)
    return games_log
