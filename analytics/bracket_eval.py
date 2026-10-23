"""
Bracket evaluator (BR-4): deck + journal + metadata -> estimated bracket,
estimated power level, conflicts with the journal targets, explanation.

    evaluate_bracket(deck, journal, metadata) -> BracketEvaluation

Pure and deterministic, no LLM, no network. Inputs:

  deck      deck dict (commanders + cards) or an iterable of names.
  journal   Deck Journal (analytics.journal.public() shape) or None. Read:
            target_bracket (1-5 | None), target_power_level.scale (1-10),
            pet_cards, novelties, deck_vision.description.
  metadata  dict, every key optional:
              findings       BR-3 BracketFindings (else computed from the next three)
              game_changers  Game Changer names (bracket_check default: BR-1 snapshot)
              combos         Commander Spellbook in-deck combos (None = not checked)
              oracle         {name: {oracle_text}} regex fallback for bracket_check
              commandersalt  Commander Salt MetaScores, or {power, salt, bracket_rating}
              power_estimate our own 1-10 estimate (engine playfeel power_level_estimate)
              edhrec         analytics.edhrec_inclusion.deck_inclusion() output
              price          deck total in USD (float) or {price, unpriced}
              tags           deck tags (list of str)

The signal -> bracket / power mapping is documented in docs/BRACKETS_SPEC.md
("Bracket evaluator (BR-4)"). Short version:

  estimated_bracket = max(BR-3 minimum_bracket,
                          2 if the minimum is 1 and the user didn't target 1,
                          bracket_for_power(Commander Salt power))      never 5
  estimated_power   = Commander Salt power, else our estimate clamped into the
                      estimated bracket's power band, else that band's midpoint.

Hard conflicts are the BR-3 hard rules the target bracket breaks. Everything
else (exact bracket mismatch, power band, power vs target, pet cards) is
advisory.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, Field

from analytics import bracket_check as bc
from analytics import brackets as br
from analytics.engine import POWER_ALIGN_TOLERANCE

HARD, ADVISORY = br.HARD, br.ADVISORY

# 1-10 power band each bracket usually plays at (community mapping, inclusive).
# Bands only translate Commander Salt power into a bracket and bound our own
# estimate; they never override a hard-rule floor.
POWER_BANDS: Dict[int, Tuple[float, float]] = {
    1: (1.0, 4.0),
    2: (4.0, 6.0),
    3: (6.0, 8.0),
    4: (8.0, 10.0),
    5: (9.0, 10.0),
}

RULE_LABELS = {"game_changers": "Game Changers", "mass_land_denial": "Mass land denial",
               "extra_turns": "Extra turns", "two_card_combos": "Two-card combos"}
RULE_SOURCES = {
    "game_changers": "Scryfall is:gamechanger (official Game Changers list)",
    "mass_land_denial": "Scryfall Tagger otag:mass-land-denial + WotC examples",
    "extra_turns": "Scryfall Tagger otag:extra-turn + Commander Spellbook loops",
    "two_card_combos": "Commander Spellbook",
    "tutors": "Scryfall Tagger otag:tutor",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Conflict(BaseModel):
    severity: str = Field(description="hard | advisory")
    rule: str
    message: str
    cards: List[str] = Field(default_factory=list)


class Signal(BaseModel):
    signal: str
    value: Any = None
    effect: str
    source: str


class Explanation(BaseModel):
    signals: List[Signal] = Field(description="ordered: hard floors (highest first), bracket adjustments, "
                                              "power, then context")
    summary: str


class BracketEvaluation(BaseModel):
    estimated_bracket: int
    estimated_bracket_name: str
    estimated_bracket_reason: str
    minimum_bracket: int = Field(description="BR-3 hard-rule minimum")
    target_bracket: Optional[int] = None
    target_bracket_name: Optional[str] = None
    no_target: bool
    aligned: Optional[bool] = Field(None, description="estimated == target and no hard conflict; None without a target")
    estimated_power_level: float
    power_source: str = Field(description="commandersalt | our_estimate | bracket_band")
    target_power_level: Optional[int] = None
    power_delta: Optional[float] = Field(None, description="estimated - target power")
    power_aligned: Optional[bool] = None
    conflicts: List[Conflict] = Field(default_factory=list)
    explanation: Explanation
    findings: Dict[str, Any] = Field(default_factory=dict, description="the BR-3 BracketFindings")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def bracket_for_power(power: float) -> int:
    """Lowest bracket (1-4) whose power band reaches `power`. Never 5."""
    for n in (1, 2, 3):
        if power <= POWER_BANDS[n][1]:
            return n
    return 4


def _band_text(n: int) -> str:
    lo, hi = POWER_BANDS[n]
    return f"{lo:g}-{hi:g}"


def _name(n: Optional[int]) -> Optional[str]:
    b = br.get_bracket(n) if n is not None else None
    return b.name if b else None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _commandersalt(cs: Any) -> Dict[str, Optional[float]]:
    """{power, salt, bracket_rating} from MetaScores, a dict, or None."""
    if cs is None:
        return {"power": None, "salt": None, "bracket_rating": None}
    if isinstance(cs, Mapping):
        return {"power": _num(cs.get("power")), "salt": _num(cs.get("salt")),
                "bracket_rating": _num(cs.get("bracket_rating"))}
    power = getattr(getattr(cs, "power", None), "score", None)
    salt = getattr(getattr(cs, "salt", None), "score", None)
    pm = getattr(cs, "provider_metrics", None) or {}
    return {"power": _num(power), "salt": _num(salt), "bracket_rating": _num(pm.get("bracket_rating"))}


def _target_bracket(journal: Optional[Mapping]) -> Optional[int]:
    v = (journal or {}).get("target_bracket")
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5 else None


def _target_power(journal: Optional[Mapping]) -> Optional[int]:
    pl = (journal or {}).get("target_power_level")
    v = pl.get("scale") if isinstance(pl, Mapping) else pl
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _cards_text(cards: Iterable[str], limit: int = 4) -> str:
    cards = list(cards)
    head = ", ".join(cards[:limit])
    return head + (f" +{len(cards) - limit} more" if len(cards) > limit else "")


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _mentions(text: str, card: str) -> bool:
    if not text:
        return False
    face = card.split(" // ")[0].strip()
    return bool(face) and re.search(r"(?<!\w)" + re.escape(face) + r"(?!\w)", text, re.I) is not None


# ---------------------------------------------------------------------------
# Hard-rule messages
# ---------------------------------------------------------------------------


def _rule_cards(f: bc.BracketFindings, rule: str) -> List[str]:
    for fl in f.floors:
        if fl.rule == rule:
            return list(fl.cards)
    return []


def _hard_message(f: bc.BracketFindings, rule: str, target: int) -> str:
    b = br._require(target)
    tname = f"Bracket {target} ({b.name})"
    if rule == "game_changers":
        n = f.game_changers.count
        cap = b.max_game_changers
        allows = "none" if cap == 0 else f"up to {cap}"
        return f"{_plural(n, 'Game Changer')} in the deck; {tname} allows {allows}."
    if rule == "mass_land_denial":
        return f"Mass land denial ({_cards_text(h.name for h in f.mass_land_denial.cards)}); {tname} doesn't allow it."
    if rule == "extra_turns":
        if f.extra_turns.loops:
            loop = " + ".join(f.extra_turns.loops[0].cards)
            return f"Extra-turn loop ({loop}); {tname} doesn't allow chained or looped extra turns."
        return f"Extra-turn cards ({_cards_text(h.name for h in f.extra_turns.cards)}); {tname} allows none."
    if rule == "two_card_combos":
        combos = f.two_card_combos.combos
        early = [c for c in combos if c.early]
        shown = early or combos
        lines = "; ".join(" + ".join(c.cards) for c in shown[:3])
        if target <= 2:
            return f"{_plural(len(combos), 'two-card combo')} ({lines}); {tname} allows none."
        return (f"{_plural(len(early), 'early two-card combo')} (Spellbook Ruthless/Banned: {lines}); "
                f"{tname} only allows late-game two-card combos.")
    return f"{RULE_LABELS.get(rule, rule)} breaks {tname}."


def _pet_reason(f: bc.BracketFindings, rule: str) -> str:
    return {"game_changers": "a Game Changer", "mass_land_denial": "mass land denial",
            "extra_turns": "an extra-turn piece", "two_card_combos": "part of a two-card combo"}.get(rule, rule)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def evaluate_bracket(deck: Union[Mapping[str, Any], Iterable[Any]], journal: Optional[Mapping[str, Any]],
                     metadata: Optional[Mapping[str, Any]] = None) -> BracketEvaluation:
    meta = dict(metadata or {})
    f: bc.BracketFindings = meta.get("findings") or bc.check_deck_brackets(
        deck, game_changers=meta.get("game_changers"), combos=meta.get("combos"), oracle=meta.get("oracle"))
    target = _target_bracket(journal)
    target_power = _target_power(journal) if journal is not None else None
    cs = _commandersalt(meta.get("commandersalt"))
    ours = _num(meta.get("power_estimate"))
    signals: List[Signal] = []

    # 1. Hard-rule floors (highest first), then the rules that set no floor.
    floor_rules = {fl.rule: fl for fl in f.floors}
    for fl in f.floors:  # already sorted (-floor, rule)
        value: Any = {"game_changers": f.game_changers.count, "mass_land_denial": f.mass_land_denial.count,
                      "extra_turns": f.extra_turns.count, "two_card_combos": f.two_card_combos.count}[fl.rule]
        signals.append(Signal(signal=fl.rule, value=value, source=RULE_SOURCES[fl.rule],
                              effect=f"floor Bracket {fl.floor}: {_cards_text(fl.cards)}"))
    for rule, value in (("game_changers", f.game_changers.count), ("mass_land_denial", f.mass_land_denial.count),
                        ("extra_turns", f.extra_turns.count), ("two_card_combos", f.two_card_combos.count)):
        if rule in floor_rules:
            continue
        effect = "no floor"
        if rule == "two_card_combos" and not f.two_card_combos.checked:
            effect = "no floor (not checked: no Commander Spellbook data, minimum may be higher)"
        signals.append(Signal(signal=rule, value=value, effect=effect, source=RULE_SOURCES[rule]))
    if f.tutors.count:
        signals.append(Signal(signal="tutors", value=f.tutors.count, source=RULE_SOURCES["tutors"],
                              effect=f"advisory only, never moves the bracket ({_cards_text(h.name for h in f.tutors.cards)})"))

    # 2. Estimated bracket.
    minimum = f.minimum_bracket
    estimated = minimum
    if f.floors:
        top = f.floors[0]
        reason = f"{RULE_LABELS[top.rule]} ({_cards_text(top.cards, 3)}) set the minimum: Bracket {minimum}."
    else:
        reason = "No hard-rule floor."
    if minimum == 1 and target != 1:
        estimated = 2
        reason = ("Passes every Bracket 1 hard rule, but Exhibition is about theme and only you can pick it, "
                  "so the estimate is Bracket 2.")
        signals.append(Signal(signal="exhibition_rule", value=1, source="docs/BRACKETS_SPEC.md",
                              effect="minimum 1 shown as Bracket 2 unless the journal targets Bracket 1"))
    if cs["power"] is not None:
        by_power = bracket_for_power(cs["power"])
        if by_power > estimated:
            estimated = by_power
            reason += (f" Commander Salt power {cs['power']:g} is in the Bracket {by_power} band "
                       f"({_band_text(by_power)}), so it plays like Bracket {by_power}.")
            cs_effect = f"power estimate; raises the bracket to {by_power} (band {_band_text(by_power)})"
        else:
            cs_effect = f"power estimate; Bracket {by_power} band, doesn't move the bracket"
        signals.append(Signal(signal="commandersalt_power", value=cs["power"], effect=cs_effect,
                              source="Commander Salt"))
    estimated = min(estimated, 4)
    reason = f"Bracket {estimated} ({_name(estimated)}). " + reason

    # 3. Estimated power level.
    lo, hi = POWER_BANDS[estimated]
    if cs["power"] is not None:
        power, power_source = round(cs["power"], 1), "commandersalt"
        if ours is not None:
            signals.append(Signal(signal="our_power_estimate", value=ours, source="Commander Lab engine",
                                  effect="not used (Commander Salt preferred)"))
    elif ours is not None:
        power, power_source = round(max(lo, min(hi, ours)), 1), "our_estimate"
        effect = "power estimate"
        if power != round(ours, 1):
            effect += f", clamped to {power:.1f} (Bracket {estimated} band {_band_text(estimated)})"
        signals.append(Signal(signal="our_power_estimate", value=ours, effect=effect, source="Commander Lab engine"))
    else:
        power, power_source = round((lo + hi) / 2, 1), "bracket_band"
        signals.append(Signal(signal="power_fallback", value=power, source="docs/BRACKETS_SPEC.md",
                              effect=f"no power data: midpoint of the Bracket {estimated} band ({_band_text(estimated)})"))

    # 4. Context signals (never move either estimate).
    if cs["bracket_rating"] is not None:
        signals.append(Signal(signal="commandersalt_bracket_rating", value=cs["bracket_rating"],
                              source="Commander Salt", effect="second opinion, not used (proprietary)"))
    if cs["salt"] is not None:
        signals.append(Signal(signal="commandersalt_salt", value=cs["salt"], source="Commander Salt",
                              effect="context only"))
    ed = meta.get("edhrec") or {}
    if isinstance(ed, Mapping) and ed.get("status") == "ok" and ed.get("overlap_pct") is not None:
        signals.append(Signal(signal="edhrec_overlap", value=ed.get("overlap_pct"), source="EDHREC",
                              effect=f"context only: average inclusion of the non-land cards "
                                     f"({ed.get('decks_sampled') or '?'} decks sampled)"))
    price_raw = meta.get("price")
    price = _num(price_raw.get("price") if isinstance(price_raw, Mapping) else price_raw)
    if price is not None:
        signals.append(Signal(signal="price", value=price, source="Scryfall prices", effect="context only"))
    tags = [t for t in meta.get("tags") or [] if isinstance(t, str)]
    if tags:
        signals.append(Signal(signal="deck_tags", value=tags, source="deck tags", effect="context only"))

    # 5. Conflicts with the journal.
    conflicts: List[Conflict] = []
    hard_rules: List[str] = []
    if target is not None:
        for fl in f.floors:
            if fl.floor > target:
                hard_rules.append(fl.rule)
                conflicts.append(Conflict(severity=HARD, rule=fl.rule, message=_hard_message(f, fl.rule, target),
                                          cards=list(fl.cards)))
        tname = f"Bracket {target} ({_name(target)})"
        pets = [p for p in (journal or {}).get("pet_cards") or [] if isinstance(p, str)]
        pet_keys = {bc.name_key(p) for p in pets}
        novel = str((journal or {}).get("novelties") or "")
        vision = str(((journal or {}).get("deck_vision") or {}).get("description") or "")
        for rule in hard_rules:
            for card in _rule_cards(f, rule):
                why = _pet_reason(f, rule)
                if bc.name_key(card) in pet_keys:
                    conflicts.append(Conflict(
                        severity=ADVISORY, rule="pet_card", cards=[card],
                        message=(f"{card} is one of your pet cards, and it's {why}, which breaks {tname}. "
                                 f"Keeping it is your call: keep it and play a higher bracket, or cut it to stay at {target}.")))
                else:
                    where = "novelties" if _mentions(novel, card) else ("deck vision" if _mentions(vision, card) else None)
                    if where:
                        conflicts.append(Conflict(
                            severity=ADVISORY, rule="journal_card", cards=[card],
                            message=(f"Your {where} mention {card}, and it's {why}, which breaks {tname}. "
                                     f"That's your call: the card or the bracket.")))
        if estimated != target:
            if target == 5:
                msg = (f"Target Bracket 5 (cEDH): we never estimate Bracket 5 (4 vs 5 is intent), "
                       f"so the estimate stays Bracket {estimated}.")
            elif estimated > target:
                msg = f"Estimated Bracket {estimated} ({_name(estimated)}): plays above target {tname}."
            else:
                msg = f"Estimated Bracket {estimated} ({_name(estimated)}): plays below target {tname}."
            conflicts.append(Conflict(severity=ADVISORY, rule="bracket_mismatch", message=msg))
        if cs["power"] is not None:
            tlo, thi = POWER_BANDS[target]
            if not tlo <= cs["power"] <= thi:
                side = "above" if cs["power"] > thi else "below"
                conflicts.append(Conflict(
                    severity=ADVISORY, rule="power_band",
                    message=(f"Commander Salt power {cs['power']:g} is {side} the {tname} band "
                             f"({_band_text(target)}).")))

    power_delta = round(power - target_power, 1) if target_power is not None else None
    power_aligned = None if power_delta is None else abs(power_delta) <= POWER_ALIGN_TOLERANCE
    if power_delta is not None and not power_aligned:
        side = "above" if power_delta > 0 else "below"
        conflicts.append(Conflict(
            severity=ADVISORY, rule="power_target",
            message=(f"Estimated power {power:.1f} is {abs(power_delta):.1f} {side} your target power "
                     f"{target_power} (tolerance {POWER_ALIGN_TOLERANCE:g}).")))

    aligned = None if target is None else (estimated == target and not hard_rules)

    # 6. Summary paragraph.
    parts = []
    if f.game_changers.count:
        parts.append(f"{_plural(f.game_changers.count, 'Game Changer')} ({_cards_text(f.game_changers.cards, 3)})")
    if f.two_card_combos.count:
        combos = " / ".join(" + ".join(c.cards) for c in f.two_card_combos.combos[:2])
        parts.append(f"{_plural(f.two_card_combos.count, 'two-card combo')} ({combos})")
    if f.extra_turns.loops:
        parts.append(f"extra-turn loop ({' + '.join(f.extra_turns.loops[0].cards)})")
    elif f.extra_turns.count:
        parts.append(_plural(f.extra_turns.count, "extra-turn card"))
    if f.mass_land_denial.count:
        parts.append(f"mass land denial ({_cards_text((h.name for h in f.mass_land_denial.cards), 2)})")
    head = " + ".join(parts) if parts else "No hard-rule hits"
    s = f"{head} -> Bracket {estimated} ({_name(estimated)})"
    if estimated != minimum:
        s += f" (hard-rule minimum {minimum})"
    s += ". "
    if power_source == "commandersalt":
        src = f"Commander Salt power {cs['power']:g}"
    elif power_source == "our_estimate":
        src = f"our estimate {ours:g}"
    else:
        src = f"no power data, Bracket {estimated} band midpoint"
    s += f"{src} -> power {power:.1f}/10"
    s += f" vs target {target_power}." if target_power is not None else " (no target power)."
    ctx = []
    if cs["salt"] is not None:
        ctx.append(f"Commander Salt salt {cs['salt']:g}")
    if cs["bracket_rating"] is not None:
        ctx.append(f"Commander Salt bracket rating {cs['bracket_rating']:g}")
    if any(x.signal == "edhrec_overlap" for x in signals):
        ctx.append(f"EDHREC overlap {ed.get('overlap_pct')}%")
    if price is not None:
        ctx.append(f"deck price {_money(price)}")
    if ctx:
        s += " " + "; ".join(ctx) + "."
    if target is None:
        s += " No target bracket set, so nothing to compare."
    else:
        nh = sum(1 for c in conflicts if c.severity == HARD)
        na = len(conflicts) - nh
        s += (f" Target Bracket {target}: " + ("aligned." if aligned else
              f"{_plural(nh, 'hard conflict')}, {_plural(na, 'advisory')}."))

    return BracketEvaluation(
        estimated_bracket=estimated, estimated_bracket_name=_name(estimated) or "",
        estimated_bracket_reason=reason.strip(), minimum_bracket=minimum,
        target_bracket=target, target_bracket_name=_name(target), no_target=target is None, aligned=aligned,
        estimated_power_level=power, power_source=power_source, target_power_level=target_power,
        power_delta=power_delta, power_aligned=power_aligned, conflicts=conflicts,
        explanation=Explanation(signals=signals, summary=s), findings=f.model_dump(),
    )


# ---------------------------------------------------------------------------
# Convenience: gather the live inputs for one deck (not pure; for live checks / BR-5)
# ---------------------------------------------------------------------------


async def evaluate_bracket_live(r, deck: Mapping[str, Any], commandersalt: bool = True) -> BracketEvaluation:
    """BR-3 live findings + stored journal + metadata we already have, then the
    pure evaluator. Every metadata input is optional; a failure just leaves it out."""
    import asyncio
    import logging

    log = logging.getLogger("commander_lab.bracket_eval")
    from analytics import journal as journal_mod

    findings = await bc.check_deck_brackets_live(r, deck)
    rec = journal_mod.load_journal(r, deck)
    jr = journal_mod.public(rec) if rec is not None else None
    meta: Dict[str, Any] = {"findings": findings}
    try:
        from deck_review import review_deck

        meta["power_estimate"] = review_deck(deck=dict(deck)).playfeel_summary.power_level_estimate
    except Exception as exc:  # noqa: BLE001
        log.warning("power estimate failed: %s", exc)
    try:
        from analytics import prices as prices_mod

        meta["price"] = prices_mod.deck_total(deck, prices_mod.card_prices(r, journal_mod.deck_entries(deck)))
    except Exception as exc:  # noqa: BLE001
        log.warning("prices failed: %s", exc)
    try:
        from analytics import edhrec_inclusion

        meta["edhrec"] = edhrec_inclusion.deck_inclusion(r, dict(deck))
    except Exception as exc:  # noqa: BLE001
        log.warning("edhrec failed: %s", exc)
    if commandersalt:
        try:
            from analytics.aggregator import coerce_to_decklist_input
            from analytics.providers.commandersalt import CommanderSaltProvider

            di = coerce_to_decklist_input(dict(deck))
            meta["commandersalt"] = await asyncio.to_thread(CommanderSaltProvider().get_meta_scores, di)
        except Exception as exc:  # noqa: BLE001 - not ingested is the common case
            log.info("commander salt unavailable: %s", exc)
    return evaluate_bracket(deck, jr, meta)
