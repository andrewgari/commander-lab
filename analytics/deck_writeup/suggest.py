"""Deterministic, grounded suggestions for the primer builder: key-card candidates, per-field proposals and
one-line "why" reasons. Built only from the deck's real cards, tags, oracle text, Journal spirit and table
facts, so the builder never hangs on (or invents anything for) an offline oracle. The model is used for the
full primer; these are cheap starting points the owner accepts or ignores."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

MAX_KEY_CARDS = 8
FIELDS = ("pitch", "early", "mid", "late", "mull", "mist", "weak")

_ROLE_ORDER = [  # (role label, tag fragments, rank) – earlier = more "key"
    ("Finisher", ("finisher", "win", "wincon", "alt win", "combo"), 0),
    ("Engine", ("engine", "draw", "card advantage", "tokens", "token", "aristocrats", "recursion", "sacrifice"), 1),
    ("Protection", ("protection", "counterspell", "hexproof", "indestructible"), 2),
    ("Ramp", ("ramp", "mana", "rock", "dork"), 3),
    ("Removal", ("removal", "wipe", "board wipe", "sweeper", "interaction"), 4),
]


def _lc(xs) -> List[str]:
    return [str(x).lower() for x in xs or []]


def roles_of(card: Mapping[str, Any]) -> List[str]:
    tags = _lc(card.get("tags"))
    out = []
    for label, frags, _ in _ROLE_ORDER:
        if any(f == t or f in t for f in frags for t in tags) and label not in out:
            out.append(label)
    return out


def _is_land(c: Mapping[str, Any]) -> bool:
    return "land" in (c.get("type_line") or "").lower()


def _first_clause(text: str, limit: int = 110) -> str:
    t = " ".join((text or "").replace("\n", " ").split())
    t = re.sub(r"\([^)]*\)", "", t).strip()
    m = re.match(r"(.+?[.;])(\s|$)", t)
    t = (m.group(1) if m else t).rstrip(".;")
    return t[: limit - 1].rsplit(" ", 1)[0] + "…" if len(t) > limit else t


def key_card_candidates(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]],
                        spirit: Optional[Mapping[str, Any]] = None, limit: int = MAX_KEY_CARDS) -> List[dict]:
    """Up to `limit` suggested pillars: commander(s), Journal pets, pillar-matching cards, then the best by role."""
    by_name = {c["name"]: c for c in card_meta if c.get("name")}
    pets = [p for p in (spirit or {}).get("pets") or [] if p in by_name]
    pillar_words = {w for p in (spirit or {}).get("pillars") or [] for w in re.findall(r"[a-z]{4,}", (p.get("name") or "").lower())}
    out: List[dict] = []
    seen = set()

    def add(name: str, roles: List[str]):
        if name in seen or len(out) >= limit or name not in by_name:
            return
        seen.add(name)
        out.append({"name": name, "roles": roles[:3], "on": len(out) < 5, "why": "", "src": "empty"})

    for n in deck.get("commanders") or []:
        add(n, ["Commander"])
    for n in pets:
        add(n, ["Pet"] + roles_of(by_name[n]))
    scored = []
    for c in card_meta:
        if not c.get("name") or c["name"] in seen or _is_land(c):
            continue
        roles = roles_of(c)
        pillar_hit = bool(pillar_words & set(_lc(c.get("tags")) + re.findall(r"[a-z]{4,}", c["name"].lower())))
        if not roles and not pillar_hit:
            continue
        rank = min([r[2] for r in _ROLE_ORDER if r[0] in roles] or [5])
        scored.append((0 if pillar_hit else 1, rank, -len(c.get("tags") or []), c["name"], roles))
    for _, _, _, name, roles in sorted(scored):
        # keep a spread: at most 3 of the same lead role
        if sum(1 for o in out if o["roles"][:1] == roles[:1]) >= 3:
            continue
        add(name, roles or ["Synergy"])
    return out


def why_for(card: Mapping[str, Any], roles: Sequence[str]) -> str:
    if card.get("oracle_text"):
        return _first_clause(card["oracle_text"])[:120]
    lead = next((r for r in roles if r not in ("Commander", "Pet")), "")
    return f"Core {lead.lower()} piece of the plan." if lead else "Core piece of the plan."


def _names(cards: Sequence[Mapping[str, Any]], n: int) -> str:
    names = [c["name"] for c in cards[:n]]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _label(kind: str, value: str) -> str:
    try:
        import deck_genre
        return deck_genre.label_of(kind, value)
    except Exception:  # noqa: BLE001
        return str(value)


def theme_words(spirit: Optional[Mapping[str, Any]], card_meta: Sequence[Mapping[str, Any]]) -> List[str]:
    g = (spirit or {}).get("genre") or {}
    words = [_label("archetype", a) for a in g.get("archetype") or []] + [_label("theme", t) for t in g.get("theme") or []]
    words += [_label("kindred", k) for k in g.get("kindred") or []]
    if not words:
        counts: Dict[str, int] = {}
        for c in card_meta:
            for t in c.get("tags") or []:
                counts[str(t)] = counts.get(str(t), 0) + 1
        words = [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2]]
    return words


def suggest_fields(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]],
                   spirit: Optional[Mapping[str, Any]] = None,
                   table_facts: Optional[Mapping[str, Any]] = None) -> Dict[str, List[str]]:
    cmdr = " & ".join(deck.get("commanders") or []) or deck.get("name") or "This deck"
    nonland = [c for c in card_meta if c.get("name") and not _is_land(c) and not c.get("is_commander")]

    def with_role(role: str, max_mv: Optional[float] = None):
        out = [c for c in nonland if role in roles_of(c) and (max_mv is None or (c.get("mana_value") or 0) <= max_mv)]
        return sorted(out, key=lambda c: (c.get("mana_value") if c.get("mana_value") is not None else 9, c["name"]))

    ramp, engine, prot = with_role("Ramp", 3), with_role("Engine"), with_role("Protection")
    fin, wipes = with_role("Finisher"), with_role("Removal")
    themes = theme_words(spirit, card_meta)
    theme_txt = " / ".join(themes[:3]) if themes else "a Commander deck"
    s = spirit or {}
    pitch = []
    if s.get("vision"):
        pitch.append(_clip(s["vision"], 140))
    pitch.append(_clip(f"{cmdr}: {theme_txt}" + (f", leaning on {_names(engine, 2)}." if engine else "."), 140))
    if fin:
        pitch.append(_clip(f"{theme_txt} with {cmdr} at the helm, closing through {_names(fin, 2)}.", 140))
    early = f"Turns 1–3: land drops" + (f", then {_names(ramp, 2)}" if ramp else "") + \
            ". Develop mana and a first piece of the plan before committing the commander."
    mid = f"Turns 4–7: land {cmdr}" + (f" and engine pieces like {_names(engine, 2)}" if engine else "") + \
          (f"; keep {_names(prot, 1)} up before overcommitting." if prot else ".")
    wins = [str(w) for w in s.get("wincons") or []]
    late = ("Turn 8+: close through " + (_names(fin, 2) if fin else (", ".join(wins[:2]) if wins else "combat damage")) + ".")
    mull = ("Keep 3–4 lands with " + (f"ramp like {_names(ramp, 2)}" if ramp else "an early play") +
            ". Mulligan hands that can't start the plan by turn 3.")
    mist = f"Overcommitting into a sweeper" + (f" (this deck runs {_names(wipes, 1)} itself)" if wipes else "") + \
           f". Casting {cmdr} before the plan is ready."
    weak = "Mass removal, graveyard hate, and fast combo. Stax effects slow the plan down."
    return {"pitch": pitch, "early": [early], "mid": [mid], "late": [late], "mull": [mull], "mist": [mist],
            "weak": [weak]}


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def win_candidates(spirit: Optional[Mapping[str, Any]], card_meta: Sequence[Mapping[str, Any]],
                   table_facts: Optional[Mapping[str, Any]] = None) -> List[dict]:
    """Proposed win conditions: the Journal's if set (src journal), else derived from combos / tags (src draft)."""
    out: List[dict] = []
    try:
        from analytics.journal import WINCON_LABELS
    except Exception:  # noqa: BLE001
        WINCON_LABELS = {}
    for w in (spirit or {}).get("wincons") or []:
        out.append({"id": "j-" + re.sub(r"\W+", "-", str(w).lower()), "t": WINCON_LABELS.get(w, str(w)), "on": True, "src": "journal"})
    if not out:
        tags = {t.lower() for c in card_meta for t in _lc(c.get("tags"))}
        if (table_facts or {}).get("combos"):
            out.append({"id": "combo", "t": "Combo", "on": False, "src": "draft"})
        if tags & {"tokens", "token", "go-wide", "anthem"}:
            out.append({"id": "wide", "t": "Wide combat", "on": False, "src": "draft"})
        if tags & {"aristocrats", "drain", "sacrifice"}:
            out.append({"id": "drain", "t": "Aristocrats drain", "on": False, "src": "draft"})
        out.append({"id": "cdmg", "t": "Commander damage", "on": False, "src": "draft"})
    return out[:4]
