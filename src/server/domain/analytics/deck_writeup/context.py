"""Redis-backed context for the writeup prompts: the Deck Spirit (Journal + Identity) and the deterministic
Table Facts (Rule 0). Every function degrades to an empty answer instead of raising: a missing Journal or an
unreachable Spellbook must never stop a primer from being drafted."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

log = logging.getLogger("commander_lab.deck_writeup")

# Hand-kept stax / tax pieces (names, any case) on top of tags and oracle regexes. Mass land denial,
# extra turns and tutors come from analytics.bracket_check; this only fills the stax gap.
STAX_CARDS = {
    "winter orb", "static orb", "stasis", "rule of law", "ghostly prison", "propaganda", "sphere of resistance",
    "thalia, guardian of thraben", "thorn of amethyst", "trinisphere", "null rod", "stony silence",
    "collector ouphe", "drannith magistrate", "opposition agent", "rhystic study", "smokestack", "tangle wire",
    "grand arbiter augustin iv", "aven mindcensor", "hokori, dust drinker", "back to basics", "blind obedience",
    "damping sphere", "archon of emeria", "hushwing gryff", "torpor orb", "dovin's veto",
}
STAX_TAGS = {"stax", "tax", "hatebear", "lock", "prison"}
_STAX_RE = re.compile(r"players can't cast|can't cast more than one|spells (?:your opponents|opponents) cast cost|"
                      r"doesn't untap during its controller's untap step|permanents (?:your )?opponents control "
                      r"(?:enter|come into play) tapped|each spell costs? \{?\d\}? more", re.I)


def _registry_id(deck: Mapping[str, Any]) -> str:
    import src.server.domain.decks.registry as registry
    return registry.registry_id_of(dict(deck))


def spirit_for_deck(r, deck: Mapping[str, Any]) -> Dict[str, Any]:
    """The owner's stated intent for the deck, from the Journal and Identity.

    `confirmed` = the owner saved a Journal for this deck, or pinned/accepted a thesis or pillar. Otherwise
    everything here is generated draft and the prompt labels it 'draft spirit, may be wrong'."""
    out: Dict[str, Any] = {"confirmed": False, "thesis": "", "vision": "", "intent": "", "pillars": [], "pets": [],
                           "novelties": "", "wincons": [], "genre": {"archetype": [], "theme": [], "kindred": []},
                           "target_bracket": None, "target_turn_win": None, "tone": "", "notes": ""}
    try:
        from src.server.domain.analytics import journal as journal_mod
        rec = journal_mod.load_journal(r, deck)
        if rec:
            j = journal_mod.public(rec)
            dv = j.get("deck_vision") or {}
            wc = j.get("preferred_win_conditions") or {}
            out.update(vision=(dv.get("description") or "").strip(), intent=dv.get("intent") or "",
                       target_turn_win=dv.get("target_turn_win"), target_bracket=j.get("target_bracket"),
                       pets=[p for p in j.get("pet_cards") or [] if isinstance(p, str) and p.strip()],
                       novelties=(j.get("novelties") or "").strip(), notes=(j.get("freeform_notes") or "").strip(),
                       wincons=[x for x in [wc.get("primary")] + list(wc.get("secondary") or [])
                                + list(wc.get("custom_win_conditions") or []) if x])
            try:
                import src.server.domain.decks.deck_genre as deck_genre
                vals = deck_genre.values_of(j)
                out["genre"] = {k: list(vals.get(k) or []) for k in ("archetype", "theme", "kindred")}
            except Exception:  # noqa: BLE001
                pass
            out["confirmed"] = True
    except Exception as exc:  # noqa: BLE001
        log.debug("spirit: journal unavailable: %s", exc)
    try:
        from src.server.domain.analytics import identity as identity_mod
        ident = identity_mod.load_identity(r, dict(deck))
        if ident:
            th = ident.get("thesis") or {}
            text = (th.get("text") or "").strip()
            if text:
                out["thesis"] = text
                out["confirmed"] = True
            elif th.get("draft_text"):
                out["thesis"] = str(th["draft_text"]).strip()
            for p in identity_mod.live_pillars(ident):
                out["pillars"].append({"name": p.get("name") or "", "description": p.get("description") or ""})
                if p.get("state") in ("pinned", "accepted"):
                    out["confirmed"] = True
    except Exception as exc:  # noqa: BLE001
        log.debug("spirit: identity unavailable: %s", exc)
    return out


def _oracle_cache(r, names: Sequence[str]) -> Dict[str, dict]:
    if not names:
        return {}
    try:
        from src.server.domain.analytics.interactions import tagger
        keys = [tagger.tag_key(n) for n in names]
        vals = r.mget(keys) if hasattr(r, "mget") else [r.get(k) for k in keys]
        import json
        out = {}
        for n, raw in zip(names, vals):
            if raw:
                try:
                    out[n] = json.loads(raw)
                except ValueError:
                    pass
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("oracle cache unavailable: %s", exc)
        return {}


def mana_value_of(mana_cost: Optional[str]) -> Optional[float]:
    """Mana value from a Scryfall mana_cost string like '{2}{U}{U}' (split cards: front face)."""
    if not mana_cost:
        return None
    total = 0
    for sym in re.findall(r"\{([^}]+)\}", mana_cost.split(" // ")[0]):
        if sym.isdigit():
            total += int(sym)
        elif sym in ("X", "Y", "Z"):
            continue
        else:
            total += 1
    return float(total)


def enrich_card_meta(r, rows: List[dict]) -> List[dict]:
    """Fill mana_value / oracle_text on card_meta rows from the interaction tagger's Redis cache."""
    need = [row["name"] for row in rows if not row.get("oracle_text") or row.get("mana_value") is None]
    cache = _oracle_cache(r, need) if need else {}
    for row in rows:
        rec = cache.get(row["name"]) or {}
        if not row.get("oracle_text"):
            row["oracle_text"] = rec.get("oracle_text") or ""
        if row.get("mana_value") is None:
            row["mana_value"] = mana_value_of(rec.get("mana_cost"))
        if not row.get("type_line") and rec.get("type_line"):
            row["type_line"] = rec["type_line"]
    return rows


def _game_changer_names(r) -> List[str]:
    """Cached list, else the checked-in snapshot. Never touches the network."""
    from src.server.domain.analytics import brackets as brackets_mod
    cached = brackets_mod._read_cache(r)
    return list((cached or brackets_mod.load_fallback()).cards)


def _cached_combos(r, deck: Mapping[str, Any]) -> Optional[dict]:
    """Spellbook combos from the Redis cache only (a miss is None, never a slow external call)."""
    from src.server.domain.analytics.interactions import combos as combos_mod

    def _no_network(*a, **kw):
        raise combos_mod.SpellbookError("spellbook not cached; not fetching during a writeup")

    try:
        client = combos_mod.SpellbookClient(r, http_post=_no_network)
        return combos_mod.deck_combos(r, dict(deck), client=client, refresh=False)
    except combos_mod.SpellbookError:
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("combos unavailable: %s", exc)
        return None


def _stax_hits(card_meta: Sequence[Mapping[str, Any]]) -> List[str]:
    hits = []
    for c in card_meta:
        name = c.get("name") or ""
        tags = {str(t).lower() for t in c.get("tags") or []}
        if name.lower() in STAX_CARDS or tags & STAX_TAGS or _STAX_RE.search(c.get("oracle_text") or ""):
            hits.append(name)
    return hits


def _fact(fid: str, singular: str, plural: str, none_title: str, cards: Sequence[str], sub: str = "") -> dict:
    cards = list(dict.fromkeys(c for c in cards if c))
    n = len(cards)
    title = (f"{n} {singular if n == 1 else plural}" if n else none_title)
    shown = ", ".join(cards[:6]) + (f", +{n - 6}" if n > 6 else "")
    return {"id": fid, "title": title, "detail": shown if n else sub, "cards": cards, "count": n, "on": n > 0}


def table_facts_for_deck(r, deck: Mapping[str, Any], card_meta: Optional[Sequence[Mapping[str, Any]]] = None,
                         combos: Any = "cache") -> Dict[str, Any]:
    """Deterministic Rule 0 facts: Game Changers, tutors, combos, extra turns, mass land denial, stax.

    Returns {"facts": [{id, title, detail, cards, count, on}], "combos": [str], "combos_checked": bool,
    "minimum_bracket": int|None}. Facts with count == 0 have on=False and read as "No ..." in the UI."""
    from src.server.domain.analytics import bracket_check

    if combos == "cache":
        combos = _cached_combos(r, deck)
    try:
        gc_names = _game_changer_names(r)
    except Exception as exc:  # noqa: BLE001
        log.debug("game changers unavailable: %s", exc)
        gc_names = None
    oracle = {c["name"]: {"oracle_text": c.get("oracle_text") or ""} for c in card_meta or [] if c.get("name")}
    f = bracket_check.check_deck_brackets(dict(deck), game_changers=gc_names, combos=combos, oracle=oracle)
    combo_lines = []
    for hit in f.two_card_combos.combos:
        prod = f" -> {', '.join(hit.produces[:3])}" if hit.produces else ""
        combo_lines.append(f"{' + '.join(hit.cards)}{prod}")
    for hit in f.extra_turns.loops:
        combo_lines.append(f"{' + '.join(hit.cards)} -> repeatable extra turns")
    facts = [
        _fact("gc", "Game Changer", "Game Changers", "No Game Changers", f.game_changers.cards),
        _fact("tutors", "tutor", "tutors", "No tutors", [h.name for h in f.tutors.cards]),
        _fact("combo", "two-card combo", "two-card combos",
              "No infinite combos" if f.two_card_combos.checked else "Combos not checked",
              [" + ".join(c.cards) for c in f.two_card_combos.combos],
              "Checked against Commander Spellbook" if f.two_card_combos.checked else "No Spellbook data cached"),
        _fact("extra", "extra-turn card", "extra-turn cards", "No extra turns", [h.name for h in f.extra_turns.cards]),
        _fact("mld", "mass land denial card", "mass land denial cards", "No land destruction",
              [h.name for h in f.mass_land_denial.cards]),
        _fact("stax", "stax piece", "stax pieces", "No stax pieces", _stax_hits(card_meta or [])),
    ]
    return {"facts": facts, "combos": combo_lines, "combos_checked": f.two_card_combos.checked,
            "minimum_bracket": f.minimum_bracket}
