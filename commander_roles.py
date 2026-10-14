"""Commander roles, derived from Scryfall data (no manual tagging).

One helper for every view that draws a deck's commanders: /api/decks ships
`commander_roles` (decks.html, deck.html, inventory.html read it) and the
shared deck header (deck tabs incl. stats) gets the same list via
app._deck_head_context.

Role of each card in the command zone:
  partner            Partner / Partner with / Friends forever / Choose a
                     Background pairs (and any 2-commander deck with no better
                     signal): equal-size portraits.
  doctor             a Doctor paired with a Doctor's companion.
  doctors_companion  "Doctor's companion" card next to its Doctor: drawn a bit
                     smaller, tucked against the Doctor.
  companion          a true Companion (Keruga) listed with the commanders:
                     small and set apart, still shown.
  commander          anything else (the plain single commander).

Independent flag `can_be_commander`: the card commands but is not a legendary
creature (oracle "can be your commander", e.g. Minsc & Boo, or a legendary
Vehicle like Shorikai). Generic on purpose so new cards work without a list.
`type_label` names what it is instead ("Planeswalker", "Vehicle").
"""
import json
import re

ROLES = ("partner", "doctor", "doctors_companion", "companion", "commander")
_ORDER = {"doctor": 0, "partner": 0, "commander": 0, "doctors_companion": 1, "companion": 2}
_COMPANION_RE = re.compile(r"(^|\n)\s*companion\s*[\u2014-]", re.IGNORECASE)
_PARTNER_RE = re.compile(r"(^|\n)\s*(partner\b|friends forever\b|choose a background\b)", re.IGNORECASE)


def _front(type_line):
    return (type_line or "").split(" // ")[0]


def card_flags(card):
    """Role signals for one card. `card`: {type_line, oracle_text, keywords};
    missing fields count as unknown (no signal)."""
    card = card or {}
    text = card.get("oracle_text") or ""
    low = text.lower()
    kws = {str(k).lower() for k in card.get("keywords") or []}
    front = _front(card.get("type_line"))
    types, _, subs = front.partition("\u2014")
    subtypes = subs.split()
    legendary_creature = "Legendary" in types and "Creature" in types
    commands = bool(front) and not legendary_creature and "Background" not in subtypes
    label = None
    if commands or "can be your commander" in low:
        label = next((s for s in subtypes if s in ("Vehicle", "Spacecraft")), None) or next(
            (t for t in reversed(types.split()) if t not in ("Legendary", "Snow", "Basic")), None)
    return {
        "partner": bool(kws & {"partner", "partner with", "friends forever"}) or bool(_PARTNER_RE.search(text)),
        "doctor": "Doctor" in subtypes,
        "doctors_companion": "doctor's companion" in kws or "doctor's companion" in low,
        "companion": "companion" in kws or bool(_COMPANION_RE.search(text)),
        "can_be_commander": "can be your commander" in low or commands,
        "type_label": label,
    }


def assign_roles(cards):
    """cards: [card dict or None] in deck order -> [(role, flags)] same order."""
    flags = [card_flags(c) for c in cards]
    roles = ["commander"] * len(cards)
    if len(cards) < 2:
        return list(zip(roles, flags))
    # A true companion only when someone else is the commander.
    comp = [i for i, f in enumerate(flags) if f["companion"]]
    if len(comp) < len(cards):
        for i in comp:
            roles[i] = "companion"
    main = [i for i in range(len(cards)) if roles[i] != "companion"]
    if len(main) >= 2:
        doctors = [i for i in main if flags[i]["doctor"]]
        pals = [i for i in main if flags[i]["doctors_companion"] and not flags[i]["doctor"]]
        for i in main:
            roles[i] = "partner"
        if doctors and pals:
            for i in doctors:
                roles[i] = "doctor"
            for i in pals:
                roles[i] = "doctors_companion"
    return list(zip(roles, flags))


def _meta_card(meta):
    """card_meta:* record -> {type_line, oracle_text, keywords}."""
    head = " ".join(list(meta.get("super_types") or []) + [meta.get("type") or ""]).strip()
    subs = " ".join(meta.get("sub_types") or [])
    return {"type_line": head + (" \u2014 " + subs if subs else ""), "oracle_text": meta.get("oracle_text") or "",
            "keywords": meta.get("keywords") or []}


def _loads(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def load_cards(r, names):
    """{name: card} from what sync already stored: card_meta:{name}, then the
    oracle-keyed card_meta, then the interaction tagger's Scryfall copy
    (card_interactions:{name}.card, which has no keywords). Unknown -> absent."""
    names = sorted({n for n in names if n})
    if not names:
        return {}
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    out = {}
    for n, raw in zip(names, mget([f"card_meta:{n}" for n in names])):
        meta = _loads(raw)
        if meta:
            out[n] = _meta_card(meta)
    rest = [n for n in names if n not in out]
    if rest:
        oids = mget([f"idx:card_name_to_oracle:{n}" for n in rest])
        for n, raw in zip(rest, mget([f"card_meta:{o}" if o else "card_meta:" for o in oids])):
            meta = _loads(raw)
            if meta:
                out[n] = _meta_card(meta)
    rest = [n for n in names if n not in out]
    if rest:
        for n, raw in zip(rest, mget([f"card_interactions:{n}" for n in rest])):
            card = (_loads(raw) or {}).get("card")
            if card:
                out[n] = {"type_line": card.get("type_line") or "", "oracle_text": card.get("oracle_text") or "",
                          "keywords": card.get("keywords") or []}
    return out


def deck_commanders(deck, cards):
    """Display list for a deck: [{name, uid, role, can_be_commander, type_label}],
    main commanders first (Doctor before companion), true companion last.
    Dual-source sync can leave two printing UIDs for one commander, so names
    set the count; UIDs alone only when names are missing (max 2 portraits
    plus a companion)."""
    names = list(deck.get("commanders") or [])
    uids = [u for u in (deck.get("commander_uids") or []) if u]
    if not names:
        names = [""] * min(len(uids), 2)
    rows = []
    for i, (role, f) in enumerate(assign_roles([cards.get(n) for n in names])):
        rows.append({"name": names[i], "uid": uids[i] if i < len(uids) else None, "role": role,
                     "can_be_commander": f["can_be_commander"], "type_label": f["type_label"]})
    rows.sort(key=lambda c: _ORDER[c["role"]])  # stable: partners keep deck order
    return rows


def roles_for_decks(r, decks):
    """Attach `commander_roles` to each deck dict in place; returns decks."""
    cards = load_cards(r, [n for d in decks for n in (d.get("commanders") or [])])
    for d in decks:
        d["commander_roles"] = deck_commanders(d, cards)
    return decks
