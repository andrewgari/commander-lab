"""Parse Forge logs with FORGELOG|<TYPE>|<message> lines into game records.

Line/event formats come from forge.game.GameLogFormatter (verified against a
real Commander game captured by the javaagent, ~/.forge/forge.20261025-113928.log):

    TURN          "Turn 3 (Andrew)"
    MULLIGAN      "Andrew has kept a hand of 7 cards" / "... mulliganed ..."
    LAND          "Andrew played Mountain (81)"
    STACK_ADD     "Andrew cast Lightning Bolt targeting [X (201)]"
    LIFE          "Life: Andrew 40 > 37"
    DAMAGE        "Lightning Bolt (4) deals 3 non-combat damage to Andrew."
    GAME_OUTCOME  "Turn 85" / "Andrew has won because all opponents have lost"
                  / "Ai(2)-Deck2 has lost because life total reached 0"
    MATCH_RESULTS "Andrew: 1 Ai(1)-Deck1: 0 "

Plain (non-FORGELOG) lines that matter: "Game Result: Game N ended in N ms. W
has won!" terminates a game block in sim/headless logs.

Design decisions (v1):
- A game is "simulation" when every player name starts with "Ai(" — sim games
  are stored but excluded from the human play-style profile.
- Stored events drop PHASE and MANA (high-volume noise); everything else is
  kept so the full play-by-play essence survives.
- Deck names are not in the GameLog for GUI games, so v1 records no deck
  identity; favorite cards come from STACK_ADD casts instead.
- Never raises on malformed lines: unknown shapes are skipped.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Optional, Tuple

FORGELOG_RE = re.compile(r"^FORGELOG\|([A-Z_]+)\|(.*)$")
GAME_RESULT_RE = re.compile(r"^Game Result: Game (\d+) ended in (\d+) ms\. (.+?) has won!")

RE_TURN = re.compile(r"^Turn (\d+) \((.+?)\)\s*$")
RE_OUTCOME_TURN = re.compile(r"^Turn (\d+)\s*$")
RE_WON = re.compile(r"^(.+?) has won because (.+)$")
RE_LOST = re.compile(r"^(.+?) has lost because (.+)$")
RE_KEPT = re.compile(r"^(.+?) has kept a hand of (\d+) cards")
RE_MULLIGANED = re.compile(r"^(.+?) has mulliganed")
RE_LAND = re.compile(r"^(.+?) played (.+?)(?: \(\d+\))?\s*$")
RE_CAST = re.compile(r"^(.+?) cast (.+?)(?: targeting .*)?\s*$")
RE_LIFE = re.compile(r"^Life: (.+?) (\d+) > (\d+)\s*$")
RE_DAMAGE = re.compile(r"^(.+?) deals (\d+) (?:non-combat |combat )?damage to (.+?)\.?\s*$")
RE_MATCH_PAIR = re.compile(r"(.+?): (\d+)(?: |$)")

AI_PLAYER_RE = re.compile(r"^Ai\(\d+\)-")

# High-volume noise types dropped from the stored event list.
NOISE_TYPES = frozenset({"PHASE", "MANA"})
MAX_STORED_EVENTS = 5000


def iter_events(lines: Iterable[str]) -> List[Tuple[str, str]]:
    """Extract (type, message) pairs from FORGELOG lines. Plain lines are skipped."""
    events: List[Tuple[str, str]] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = FORGELOG_RE.match(line)
        if m:
            events.append((m.group(1), m.group(2)))
    return events


def segment_games(lines: Iterable[str]) -> List[List[str]]:
    """Split log lines into per-game blocks.

    A block closes on a plain "Game Result: Game N ended" line or a
    MATCH_RESULTS event. A "Turn 1 (" TURN event while an outcome was already
    seen also closes the previous block (GUI multi-game matches without a
    match-results line between games). Incomplete trailing blocks are dropped.
    """
    blocks: List[List[str]] = []
    cur: List[str] = []
    seen_outcome = False
    pending: Optional[List[str]] = None
    pending_result: Optional[str] = None

    def flush_pending() -> None:
        nonlocal pending, pending_result
        if pending is not None:
            if pending_result is not None:
                pending.append(pending_result)
            blocks.append(pending)
            pending = None
            pending_result = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = FORGELOG_RE.match(line)
        turn_m = RE_TURN.match(m.group(2)) if m and m.group(1) == "TURN" else None
        if turn_m and turn_m.group(1) == "1" and pending is not None:
            flush_pending()
            cur = []
            seen_outcome = False
        elif turn_m and turn_m.group(1) == "1" and cur and seen_outcome:
            blocks.append(cur)
            cur = []
            seen_outcome = False
        if pending is not None:
            if m and m.group(1) == "MATCH_RESULTS":
                pending.extend(cur)
                cur = []
                pending.append(line)
                pending_result = None
                continue
            if GAME_RESULT_RE.match(line):
                pending_result = line
                continue
            pending.append(line)
            continue
        cur.append(line)
        if m and m.group(1) == "GAME_OUTCOME":
            seen_outcome = True
        if m and m.group(1) == "MATCH_RESULTS":
            pending, cur = cur, []
            pending_result = None
        elif GAME_RESULT_RE.match(line):
            if cur:
                blocks.append(cur)
            cur = []
            seen_outcome = False
    if pending is not None:
        flush_pending()
    # Drop unterminated content: it's an incomplete game.
    return _merge_stray_continuations(blocks)


def _merge_stray_continuations(blocks: List[List[str]]) -> List[List[str]]:
    """Forge flushes log lines from worker threads, so events belonging to a
    game can land AFTER its MATCH_RESULTS / Game Result terminator. A block
    that has FORGELOG content but no "Turn 1" event is such a stray tail —
    merge it back into the previous block. A real next game always starts a
    fresh game log with Turn 1."""
    merged: List[List[str]] = []
    for block in blocks:
        events = iter_events(block)
        has_turn_one = any(
            t == "TURN" and (tm := RE_TURN.match(m)) and tm.group(1) == "1"
            for t, m in events
        )
        if events and not has_turn_one and merged:
            merged[-1].extend(block)
        else:
            merged.append(block)
    return merged


def _player_stats() -> dict:
    return {
        "lands_played": 0,
        "spells_cast": 0,
        "casts": [],
        "life_lost": 0,
        "life_gained": 0,
        "damage_taken": 0,
        "mulligans": 0,
        "kept_hand": None,
    }


def parse_game(lines: Iterable[str]) -> Optional[dict]:
    """Parse one game block into a structured record. Returns None when the
    block has no game content (no players). Never raises."""
    lines = list(lines)
    events = iter_events(lines)

    players: List[str] = []
    stats: Dict[str, dict] = {}
    winner: Optional[str] = None
    win_reason: Optional[str] = None
    losers: List[dict] = []
    turns = 0
    duration_ms: Optional[int] = None
    match_score: Dict[str, int] = {}

    def player(name: str) -> dict:
        if name not in stats:
            stats[name] = _player_stats()
            players.append(name)
        return stats[name]

    for etype, msg in events:
        try:
            if etype == "TURN":
                m = RE_TURN.match(msg)
                if m:
                    turns = max(turns, int(m.group(1)))
                    player(m.group(2))
            elif etype == "MULLIGAN":
                m = RE_KEPT.match(msg)
                if m:
                    st = player(m.group(1))
                    st["kept_hand"] = int(m.group(2))
                    st["mulligans"] = max(0, 7 - st["kept_hand"])
                else:
                    mm = RE_MULLIGANED.match(msg)
                    if mm:
                        player(mm.group(1))["mulligans"] += 1
            elif etype == "LAND":
                m = RE_LAND.match(msg)
                if m:
                    player(m.group(1))["lands_played"] += 1
            elif etype == "STACK_ADD":
                m = RE_CAST.match(msg)
                if m:
                    st = player(m.group(1))
                    st["spells_cast"] += 1
                    card = m.group(2).strip()
                    if card and card not in st["casts"]:
                        st["casts"].append(card)
            elif etype == "LIFE":
                m = RE_LIFE.match(msg)
                if m:
                    st = player(m.group(1))
                    before, after = int(m.group(2)), int(m.group(3))
                    if after < before:
                        st["life_lost"] += before - after
                    else:
                        st["life_gained"] += after - before
            elif etype == "DAMAGE":
                m = RE_DAMAGE.match(msg)
                if m:
                    target = m.group(3).strip()
                    if target in players or AI_PLAYER_RE.match(target) or target in stats:
                        player(target)["damage_taken"] += int(m.group(2))
            elif etype == "GAME_OUTCOME":
                m = RE_OUTCOME_TURN.match(msg)
                if m:
                    turns = max(turns, int(m.group(1)))
                    continue
                m = RE_WON.match(msg)
                if m:
                    winner, win_reason = m.group(1), m.group(2)
                    player(winner)
                    continue
                m = RE_LOST.match(msg)
                if m:
                    losers.append({"player": m.group(1), "reason": m.group(2)})
                    player(m.group(1))
            elif etype == "MATCH_RESULTS":
                for pm in RE_MATCH_PAIR.finditer(msg):
                    name, score = pm.group(1).strip(), int(pm.group(2))
                    match_score[name] = score
                    player(name)
        except (ValueError, IndexError):
            continue

    for raw in lines:
        gm = GAME_RESULT_RE.match(raw.rstrip("\r\n"))
        if gm:
            duration_ms = int(gm.group(2))
            if winner is None and gm.group(3):
                winner = gm.group(3).removesuffix(" has won!")

    if not players:
        return None

    is_simulation = all(AI_PLAYER_RE.match(p) for p in players)
    stored_events = [[t, m] for t, m in events if t not in NOISE_TYPES][:MAX_STORED_EVENTS]

    uid_src = "|".join([
        ",".join(players), str(winner), str(turns),
        stored_events[0][1] if stored_events else "",
        stored_events[-1][1] if stored_events else "",
        str(len(events)),
    ])
    game_uid = hashlib.sha256(uid_src.encode("utf-8")).hexdigest()[:32]

    return {
        "game_uid": game_uid,
        "players": players,
        "is_simulation": is_simulation,
        "turns": turns,
        "winner": winner,
        "win_reason": win_reason,
        "losers": losers,
        "match_score": match_score,
        "duration_ms": duration_ms,
        "player_stats": {name: stats[name] for name in players},
        "events": stored_events,
    }
