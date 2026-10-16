"""
Deep Review metrics (RV-2): the deterministic numbers behind a saved review.
See docs/REVIEW_SPEC.md.

``compute_review(inputs)`` is pure: same decklist + same genre registry version (+ the same
cached graph / combos / journal) gives byte-identical JSON. No clock, no randomness, no LLM, no
Redis. ``build_inputs(r, deck)`` is the only I/O: it gathers the inputs from the app's stores.
oracle only ever writes prose over these numbers; it never decides them. Every number below
carries a ``why`` (the inputs it came from) so the prose can explain it.

Sections
    fingerprint      sha256 of sorted card names + qty + commanders
    genre            primary + up to 2 secondary from genres.genre_match (role coverage)
    composition      Command Zone template rows (composition.py)
    curve_tempo      P(on-curve commander), expected unspent mana T2-T6, active share,
                     durdle tax, flex (instant-speed share), per-card flags
    focus            1-3 win lines (combos, engines, wincon cards) + diff vs Journal win cons
    strong_cards     high plan degree, in a win line or engine, active
    stragglers       Tuner weak-performer math + durdle, outside every win line; pets protected
    combos_engines   combos in deck / one card away; engines in deck

Mana model (documented so the numbers are explainable, not exact): on the play, cards seen by
turn t = 7 + (t - 1). Land drops come from the exact hypergeometric over the deck's land count.
Each ramp card (composition row ``ramp``) with cmc <= t-1 adds one mana, weighted by the chance
it has been seen. Spend on turn t = min(mana, cumulative expected castable demand - mana already
spent); unspent = mana - spend. Castable demand = expected cmc of nonland cards seen with
cmc <= mana, plus the commander (always available). Unspent mana never carries over.
"""
from __future__ import annotations

import hashlib
import json
import re
from math import comb
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import composition as comp
from . import genres as genres_mod
from . import weak_performers as weak_mod
from .interactions import engines as engines_mod

SCHEMA = "review_metrics/1"
DECK_SIZE = 100
OPENING_HAND = 7
TURNS = (2, 3, 4, 5, 6)

# genre
MIN_GENRE_COVERAGE = 0.25
MAX_SECONDARY = 2

# straggler / strong
STRAGGLER_MIN = 0.60
DURDLE_BONUS = 0.15
MAX_STRAGGLERS = 10
MAX_STRONG = 12
MAX_LINES = 3
MAX_ONE_AWAY = 10

WINCON_TYPES = ("combat", "combo", "attrition", "commander_damage", "alternate_wincon", "burn", "lockout", "mill")


def _r(x: float, nd: int = 4) -> float:
    return round(float(x) + 0.0, nd)


# ---------------------------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------------------------

def _entries(deck: Mapping) -> Dict[str, int]:
    qty: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        n = c.get("name")
        if n:
            try:
                q = int(c.get("quantity") or 1)
            except (TypeError, ValueError):
                q = 1
            qty[n] = qty.get(n, 0) + max(q, 1)
    for n in deck.get("commanders") or []:
        if n:
            qty.setdefault(n, 1)
    return qty


def fingerprint(deck: Mapping) -> str:
    """sha256 over sorted (card name, qty) pairs and the sorted commanders."""
    payload = {"cards": sorted(_entries(deck).items()),
               "commanders": sorted(n for n in deck.get("commanders") or [] if n)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Card facts
# ---------------------------------------------------------------------------------------------

_SYMBOL = re.compile(r"\{([^}]+)\}")


def cmc_of(mana_cost: Optional[str]) -> int:
    """Mana value from a Scryfall mana_cost string. Front face only; X = 0; hybrid = its larger side."""
    total = 0
    for sym in _SYMBOL.findall((mana_cost or "").split("//")[0]):
        s = sym.upper()
        if s.isdigit():
            total += int(s)
        elif s == "X" or s == "Y" or s == "Z":
            continue
        elif "/" in s:
            nums = [int(p) for p in s.split("/") if p.isdigit()]
            total += max(nums) if nums else 1
        else:
            total += 1
    return total


def _front(card: Optional[Mapping]) -> Tuple[str, str]:
    c = card or {}
    tl = (c.get("type_line") or "").split("//")[0].lower()
    tx = comp._norm((c.get("oracle_text") or "").split("\n//")[0])
    return tl, tx


_ETB = re.compile(r"\b(?:when|whenever) (?:~|this \w+|it|[^.,\n]{0,40}?) enters\b|\benters (?:the battlefield )?(?:with|and)\b"
                  r"|\bwhen(?:ever)? [^.\n]{0,60} enters\b")
_CANTRIP = re.compile(r"\bdraws? (?:a|one|two|three|\w+) cards?\b|\bscry \d|\bsurveil \d|\bsearch your library\b"
                      r"|\bdiscover \d|\bmill \d|\bcreate (?:a|an|\w+) [^.]*tokens?\b")
_MANA = re.compile(r"\{t\}[^.\n:]*:\s*add\b|\badd (?:\{|one|two|three|x mana|an amount)|\bcreate (?:a|an|\w+) treasure")
_DELAYED = re.compile(r"\bsuspend\b|\btime counters?\b|\bvanishing\b|\bfading\b|\bsaga\b|\bchapter\b"
                      r"|\bat the beginning of your next\b|\bat the beginning of the next\b|\bimpending\b")
_TRIGGER_ONLY = re.compile(r"^(?:whenever|at the beginning|when )", re.M)


def _tapland(type_line: str, text: str) -> bool:
    if "land" not in type_line or "creature" in type_line:
        return False
    if not re.search(r"enters (?:the battlefield )?tapped|enter tapped|enters tapped", text):
        return False
    return not re.search(r"unless|if you (?:control|have|reveal)|you may pay|reveal|choose|may have it enter", text)


def card_facts(name: str, card: Optional[Mapping], qty: int, ramp: bool) -> dict:
    tl, tx = _front(card)
    land = "land" in tl and "creature" not in tl
    cmc = 0 if land else cmc_of((card or {}).get("mana_cost"))
    instant_speed = (not land) and ("instant" in tl or bool(re.search(r"\bflash\b", tx)))
    reasons: List[str] = []
    if not land:
        if "instant" in tl or "sorcery" in tl:
            reasons.append("spell_effect")
        if "creature" in tl:
            reasons.append("creature_body")
        if _ETB.search(tx):
            reasons.append("etb")
        if _CANTRIP.search(tx) and ("instant" in tl or "sorcery" in tl or "etb" in reasons):
            reasons.append("cantrip")
        if ramp or _MANA.search(tx):
            reasons.append("mana")
        if "planeswalker" in tl:
            reasons.append("planeswalker")
    reasons = sorted(set(reasons))
    active = bool(reasons)
    durdle: List[str] = []
    if _tapland(tl, tx):
        durdle.append("tapland")
    if not land:
        if (_DELAYED.search(tx) or "saga" in tl) and "creature" not in tl:
            durdle.append("pay_now_payoff_later")
        if not active and "creature" not in tl:
            durdle.append("setup_only")
    return {"name": name, "qty": qty, "cmc": cmc, "land": land, "ramp": bool(ramp and not land),
            "active": active, "active_reasons": reasons, "instant_speed": instant_speed,
            "durdle": bool(durdle), "durdle_reasons": sorted(durdle)}


# ---------------------------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------------------------

def _hyper_cdf_ge(total: int, hits: int, drawn: int, k: int) -> float:
    """P(X >= k), X ~ Hypergeometric(total, hits, drawn)."""
    if k <= 0:
        return 1.0
    drawn = min(drawn, total)
    denom = comb(total, drawn)
    if denom == 0:
        return 0.0
    p = 0
    for x in range(k, min(hits, drawn) + 1):
        p += comb(hits, x) * comb(total - hits, drawn - x)
    return p / denom


def _expected_min(total: int, hits: int, drawn: int, cap: int) -> float:
    """E[min(X, cap)] = sum_{k=1..cap} P(X >= k)."""
    return sum(_hyper_cdf_ge(total, hits, drawn, k) for k in range(1, cap + 1))


def cards_seen(turn: int) -> int:
    return OPENING_HAND + max(turn - 1, 0)


def tempo_math(facts: Sequence[dict], commander_cmc: Optional[int], deck_total: int) -> dict:
    total = max(deck_total, 1)
    lands = sum(f["qty"] for f in facts if f["land"])
    nonland = [f for f in facts if not f["land"]]
    ramp_cards = [f for f in nonland if f["ramp"]]
    by_cmc: Dict[int, int] = {}
    for f in nonland:
        by_cmc[f["cmc"]] = by_cmc.get(f["cmc"], 0) + f["qty"]
    spent = 0.0
    per_turn: List[dict] = []
    for t in range(1, max(TURNS) + 1):
        seen = min(cards_seen(t), total)
        land_mana = _expected_min(total, lands, seen, t)
        ramp_mana = sum(min(1.0, f["qty"] * seen / total) for f in ramp_cards if f["cmc"] <= t - 1)
        # expected number of ramp cards, not copies-of-one-card, caps at the copies present
        mana = land_mana + ramp_mana
        # demand: nonland cards seen with cmc <= mana; the commander is always available
        cmc_cap = int(mana + 1e-9)
        demand = sum(c * n * seen / total for c, n in by_cmc.items() if 0 < c <= cmc_cap)
        if commander_cmc and commander_cmc <= cmc_cap:
            demand += commander_cmc
        spend = max(0.0, min(mana, demand - spent))
        spent += spend
        per_turn.append({"turn": t, "cards_seen": seen, "land_mana": _r(land_mana), "ramp_mana": _r(ramp_mana),
                         "mana": _r(mana), "castable_demand_cum": _r(demand), "spend": _r(spend),
                         "unspent": _r(mana - spend)})
    window = [p for p in per_turn if p["turn"] in TURNS]
    return {
        "lands": lands, "ramp": sum(f["qty"] for f in ramp_cards), "nonland": sum(f["qty"] for f in nonland),
        "cmc_counts": {str(k): by_cmc[k] for k in sorted(by_cmc)},
        "per_turn": window,
        "unspent_total_t2_t6": _r(sum(p["unspent"] for p in window)),
        "unspent_avg_t2_t6": _r(sum(p["unspent"] for p in window) / len(window)),
        "why": (f"{lands} lands + {sum(f['qty'] for f in ramp_cards)} ramp in {total} cards; "
                f"on the play, 7 + (t-1) cards seen by turn t; spend = min(mana, cumulative castable demand - spent)."),
    }


def commander_on_curve(commander: Optional[str], cmc: Optional[int], lands: int, ramp_cheap: int, total: int) -> Optional[dict]:
    if not commander or not cmc:
        return None
    seen = min(cards_seen(cmc), total)
    p_lands = _hyper_cdf_ge(total, lands, seen, cmc)
    p_sources = _hyper_cdf_ge(total, lands + ramp_cheap, seen, cmc)
    return {"commander": commander, "cmc": cmc, "p_lands_only": _r(p_lands), "p_with_ramp": _r(p_sources),
            "why": (f"P(at least {cmc} of {lands} lands in {seen} cards seen by turn {cmc}) = {_r(p_lands)}; "
                    f"counting {ramp_cheap} cheap ramp cards as sources: {_r(p_sources)}.")}


# ---------------------------------------------------------------------------------------------
# Genre
# ---------------------------------------------------------------------------------------------

def genre_section(graph: Mapping, qty: Mapping[str, int], genres: Sequence[dict], version: Optional[int]) -> dict:
    ranked = genres_mod.genre_match(genres_mod.deck_from_graph(dict(graph), dict(qty)), genres)
    eligible = [m for m in ranked if m["coverage"] >= MIN_GENRE_COVERAGE]
    pick = lambda m: {"id": m["genre"]["id"], "name": m["genre"]["name"], "coverage": m["coverage"],  # noqa: E731
                      "roles_hit": m["roles_hit"], "roles_missing": m["roles_missing"]}
    return {
        "registry_version": version,
        "primary": pick(eligible[0]) if eligible else None,
        "secondary": [pick(m) for m in eligible[1:1 + MAX_SECONDARY]],
        "ranking": [{"id": m["genre"]["id"], "name": m["genre"]["name"], "coverage": m["coverage"]} for m in ranked[:5]],
        "why": (f"Role coverage = weighted mean of min(copies carrying the tag / min_count, 1) over each genre's "
                f"required roles; picks need coverage >= {MIN_GENRE_COVERAGE}; {len(ranked)} genres scored."),
    }


# ---------------------------------------------------------------------------------------------
# Focus: win lines
# ---------------------------------------------------------------------------------------------

_ALT_WIN = re.compile(r"\byou win the game\b|\b(?:target (?:player|opponent)|each opponent|that player) loses the game\b")
_BURN = re.compile(r"\beach opponent loses (?:\d+|x|that much)\b|\bdeals? (?:\d+|x|that much) damage to each opponent\b"
                   r"|\btarget (?:player|opponent) loses (?:\d+|x)\b|\bdeals? x damage to (?:any target|each opponent)")
_MILL = re.compile(r"\b(?:target (?:player|opponent)|each opponent|each player) mills?\b|\bmills? (?:\d+|x|that many|cards equal)")
_LOCK = re.compile(r"\b(?:your )?opponents can't\b|\bplayers can't\b|\bcan't cast (?:more than|spells)\b"
                   r"|\bspells your opponents cast cost\b|\beach opponent can't\b|\bskip (?:your|their|that player's) \w+")
_TRAMPLE = re.compile(r"\bdouble strike\b")
LINE_MIN = {"alternate_wincon": 1, "burn": 2, "mill": 3, "lockout": 3, "combat": 5, "commander_damage": 4,
            "combo": 1, "attrition": 1}


def _card_wincons(tl: str, tx: str) -> List[str]:
    out = []
    if _ALT_WIN.search(tx):
        out.append("alternate_wincon")
    if _BURN.search(tx):
        out.append("burn")
    if _MILL.search(tx):
        out.append("mill")
    if _LOCK.search(tx):
        out.append("lockout")
    return out


def _engine_type(e: Mapping) -> str:
    last = (e.get("resources") or [""])[-1]
    return "attrition" if last in ("life_loss", "cards", "life_gain") else "combat"


def win_lines(texts: Mapping[str, Mapping], graph: Mapping, engines: Sequence[Mapping], combos_in: Sequence[Mapping],
              commanders: Sequence[str]) -> dict:
    """Candidate lines per Journal win-con type; the top MAX_LINES (by strength) are the focus."""
    acc: Dict[str, dict] = {}

    def add(kind: str, cards: Iterable[str], ev: dict):
        line = acc.setdefault(kind, {"type": kind, "cards": set(), "evidence": []})
        line["cards"].update(c for c in cards if c)
        line["evidence"].append(ev)

    for c in combos_in:
        add("combo", c.get("card_names") or [], {"kind": "combo", "id": c.get("id"), "produces": list(c.get("produces") or [])[:4],
                                                  "cards": sorted(c.get("card_names") or [])})
    for e in engines:
        add(_engine_type(e), e.get("cards") or [], {"kind": "engine", "signature": e.get("signature"), "score": e.get("score"),
                                                     "cards": sorted(e.get("cards") or [])})
    nodes = {n["name"]: n for n in graph.get("nodes") or []}
    for name in sorted(texts):
        tl, tx = _front(texts[name])
        for k in _card_wincons(tl, tx):
            add(k, [name], {"kind": "card", "name": name, "tag": k})
    attackers = sorted(n for n, node in nodes.items() if "attacks" in (node.get("consumes") or []))
    if attackers:
        add("combat", attackers, {"kind": "attack_payoffs", "count": len(attackers)})
    cmdr_creature = any("creature" in _front(texts.get(c))[0] for c in commanders)
    voltron = sorted(n for n in texts if re.search(r"\bequipment\b|\baura\b", _front(texts[n])[0])
                     or _TRAMPLE.search(_front(texts[n])[1]))
    if cmdr_creature and voltron:
        add("commander_damage", list(commanders) + voltron, {"kind": "voltron_pieces", "count": len(voltron), "cards": voltron[:12]})

    lines = []
    for kind, line in acc.items():
        cards = sorted(line["cards"])
        n_ev = len(line["evidence"])
        support = len(cards) if kind not in ("combo",) else n_ev
        if kind in ("combat", "commander_damage", "mill", "lockout", "burn", "alternate_wincon"):
            support = len([c for c in cards if c not in commanders]) or len(cards)
        if support < LINE_MIN.get(kind, 1):
            continue
        strength = len(cards) + 3 * n_ev + (10 if kind in ("combo", "alternate_wincon") else 0)
        lines.append({"type": kind, "strength": strength, "support": support, "cards": cards,
                      "evidence": sorted(line["evidence"], key=lambda e: json.dumps(e, sort_keys=True))[:6],
                      "why": f"{len(cards)} cards, {n_ev} piece(s) of evidence; strength = cards + 3*evidence"
                             f"{' + 10 (decides games on its own)' if kind in ('combo', 'alternate_wincon') else ''}."})
    lines.sort(key=lambda l: (-l["strength"], l["type"]))
    return {"all": lines, "top": lines[:MAX_LINES]}


def focus_section(lines: dict, journal: Optional[Mapping]) -> dict:
    wc = ((journal or {}).get("preferred_win_conditions") or {}) if journal else {}
    claimed = [x for x in [wc.get("primary")] + list(wc.get("secondary") or []) if x]
    claimed = sorted(set(claimed))
    excluded = sorted(set(wc.get("disliked_or_excluded") or []))
    supported_all = {l["type"] for l in lines["all"]}
    top = {l["type"] for l in lines["top"]}
    return {
        "win_lines": [{k: v for k, v in l.items()} for l in lines["top"]],
        "other_supported": [l["type"] for l in lines["all"][MAX_LINES:]],
        "journal": {"claimed": claimed, "custom": list(wc.get("custom_win_conditions") or []), "excluded": excluded,
                    "present": bool(journal)},
        "claimed_unsupported": [c for c in claimed if c not in supported_all],
        "supported_unclaimed": sorted(t for t in top if t not in claimed and t not in excluded),
        "supported_but_excluded": sorted(t for t in supported_all if t in excluded),
        "why": ("Lines come from Spellbook combos in the deck, detected engines (by last resource), and win-condition "
                "text patterns; claimed = Journal primary + secondary. claimed_unsupported = claimed types with no "
                "supporting line at all; supported_unclaimed = top-3 lines the Journal does not claim."),
    }


# ---------------------------------------------------------------------------------------------
# Strong cards, stragglers
# ---------------------------------------------------------------------------------------------

def strong_and_stragglers(facts: Mapping[str, dict], weak: Mapping, graph: Mapping, lines_top: Sequence[Mapping],
                          engines: Sequence[Mapping], pets: Iterable[str], commanders: Sequence[str]) -> Tuple[dict, dict]:
    pets_cf = {p.casefold() for p in pets if isinstance(p, str)}
    cmdr_set = set(commanders)
    in_line = {c for l in lines_top for c in l["cards"]}
    in_engine = {c for e in engines for c in e.get("cards") or []}
    deg = weak_mod.plan_degrees(dict(graph))
    weak_by = {row["name"]: row for row in weak.get("cards") or []}
    p75 = weak.get("weights", {}).get("p75_degree") or 1.0
    spells = sorted(n for n, f in facts.items() if not f["land"] and n not in cmdr_set)
    wi = weak.get("inputs") or {}
    avail = ["edges"] + [k for k, on in (("pillar", wi.get("pillars")), ("edhrec", wi.get("edhrec")),
                                         ("played", wi.get("played"))) if on]

    def weak_norm(w: Mapping) -> float:
        """Tuner weak score re-averaged over the parts that exist for this deck (a missing input is not 'strong')."""
        parts = w.get("parts") or {}
        vals = [parts[k] for k in avail if k in parts]
        return _r(sum(vals) / len(vals)) if vals else _r(w["weak_score"])

    strong_rows = []
    for n in spells:
        d = deg.get(n, 0)
        f = facts[n]
        score = 0.4 * min(1.0, d / p75) + 0.3 * (n in in_line) + 0.15 * (n in in_engine) + 0.15 * f["active"]
        ok = d >= p75 and (n in in_line or n in in_engine) and f["active"]
        if ok:
            strong_rows.append({"name": n, "strong_score": _r(score), "plan_degree": d, "in_win_line": n in in_line,
                                "in_engine": n in in_engine, "active": f["active"], "active_reasons": f["active_reasons"],
                                "pet": n.casefold() in pets_cf,
                                "why": f"plan degree {d} >= p75 {_r(p75, 2)}; "
                                       f"{'in a win line' if n in in_line else 'in an engine'}; active ({', '.join(f['active_reasons'])})."})
    strong_rows.sort(key=lambda x: (-x["strong_score"], -x["plan_degree"], x["name"]))

    rows, protected = [], []
    for n in spells:
        w = weak_by.get(n)
        if not w:
            continue
        f = facts[n]
        base = weak_norm(w)
        score = _r(base + (DURDLE_BONUS if f["durdle"] else 0.0))
        row = {"name": n, "straggler_score": score, "weak_score": w["weak_score"], "weak_norm": base,
               "weak_parts": w.get("parts"),
               "weak_reasons": w.get("reasons") or [], "durdle": f["durdle"], "durdle_reasons": f["durdle_reasons"],
               "plan_degree": deg.get(n, 0), "in_win_line": n in in_line, "in_engine": n in in_engine}
        if n.casefold() in pets_cf:
            protected.append({**row, "protected": True,
                              "would_be_straggler": score >= STRAGGLER_MIN and n not in in_line,
                              "why": "Pet card (Journal): protected, never a straggler."})
            continue
        if n in in_line or score < STRAGGLER_MIN:
            continue
        row["why"] = (f"weak score {base} (mean of {', '.join(avail)})" + (f" + {DURDLE_BONUS} durdle ({', '.join(f['durdle_reasons'])})" if f["durdle"] else "")
                      + f" = {score} >= {STRAGGLER_MIN}; in no win line.")
        rows.append(row)
    rows.sort(key=lambda x: (-x["straggler_score"], x["name"]))
    protected.sort(key=lambda x: (-x["straggler_score"], x["name"]))
    return (
        {"cards": strong_rows[:MAX_STRONG], "count": len(strong_rows),
         "why": "Strong = plan-graph degree >= the deck's p75, in a win line or engine, and active. "
                "Score = .4*min(1,degree/p75) + .3*win line + .15*engine + .15*active."},
        {"cards": rows[:MAX_STRAGGLERS], "count": len(rows), "protected_pets": protected,
         "threshold": STRAGGLER_MIN, "durdle_bonus": DURDLE_BONUS, "weak_parts_used": avail,
         "why": f"Straggler = Tuner weak score, averaged over the parts this deck has (+{DURDLE_BONUS} if durdle) >= {STRAGGLER_MIN}, outside every win line, "
                "not a pet card. Lands and commanders are never scored."},
    )


# ---------------------------------------------------------------------------------------------
# Combos / engines
# ---------------------------------------------------------------------------------------------

def combos_engines_section(combos: Optional[Mapping], engines: Sequence[Mapping]) -> dict:
    combos = combos or {}
    in_deck = sorted(combos.get("in_deck") or [], key=lambda c: (-int(c.get("popularity") or 0), str(c.get("id"))))
    one_away = list(combos.get("one_away") or [])

    def slim(c, away=False):
        out = {"id": c.get("id"), "cards": sorted(c.get("card_names") or []), "produces": list(c.get("produces") or [])[:5],
               "popularity": int(c.get("popularity") or 0), "bracket_tag": c.get("bracket_tag"), "url": c.get("url")}
        if away:
            out.update(missing=sorted(c.get("missing") or []), missing_owned=bool(c.get("missing_owned")))
        return out

    return {
        "combos_available": bool(combos) and not combos.get("error"),
        "combos_error": (combos or {}).get("error"),
        "combos_in_deck": [slim(c) for c in in_deck],
        "combos_one_away": [slim(c, True) for c in one_away[:MAX_ONE_AWAY]],
        "combos_one_away_total": len(one_away),
        "engines": [{"signature": e.get("signature"), "kind": e.get("kind"), "resources": list(e.get("resources") or []),
                     "cards": sorted(e.get("cards") or []), "score": e.get("score"), "paths": e.get("paths"),
                     "density": e.get("density"), "summary": engines_mod.summarize(dict(e))} for e in engines],
        "why": "Combos: Commander Spellbook included / almost-included (one card missing) for this exact decklist. "
               "Engines: resource chains and loops on the interaction graph (sources -> converters -> payoffs). "
               "Engines are reported for the cards in the deck only.",
    }


# ---------------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------------

def tempo_section(facts: Sequence[dict], commanders: Sequence[str], texts: Mapping[str, Mapping], total: int) -> dict:
    # partner commanders: the cheapest one (nonzero cmc) is on curve first
    priced = sorted((cmc_of((texts.get(c) or {}).get("mana_cost")), c) for c in commanders)
    priced = [(m, c) for m, c in priced if m]
    cmdr_cmc, cmdr = priced[0] if priced else (None, commanders[0] if commanders else None)
    non_cmd = [f for f in facts if f["name"] not in set(commanders)]
    math = tempo_math(non_cmd, cmdr_cmc, total)
    ramp_cheap = sum(f["qty"] for f in non_cmd if f["ramp"] and cmdr_cmc and f["cmc"] <= max(cmdr_cmc - 1, 0))
    nonland_q = sum(f["qty"] for f in non_cmd if not f["land"])
    active_q = sum(f["qty"] for f in non_cmd if not f["land"] and f["active"])
    inst_q = sum(f["qty"] for f in non_cmd if f["instant_speed"])
    durdle_cards = [f for f in non_cmd if f["durdle"]]
    by_reason: Dict[str, int] = {}
    for f in durdle_cards:
        for rr in f["durdle_reasons"]:
            by_reason[rr] = by_reason.get(rr, 0) + f["qty"]
    return {
        "commander_on_curve": commander_on_curve(cmdr, cmdr_cmc, math["lands"], ramp_cheap, total),
        "mana": math,
        "active_share": {"value": _r(active_q / nonland_q) if nonland_q else 0.0, "active": active_q, "nonland": nonland_q,
                        "why": "Active = resolves with an immediate effect: instant/sorcery, creature body, ETB, cantrip, mana."},
        "durdle_tax": {"value": _r(sum(f["qty"] for f in durdle_cards) / max(total, 1)),
                       "cards": sum(f["qty"] for f in durdle_cards), "by_reason": {k: by_reason[k] for k in sorted(by_reason)},
                       "why": "Durdle = unconditional taplands, setup-only permanents (no immediate effect), "
                              "pay-now-payoff-later (suspend, sagas, time counters). Share of the 100."},
        "flex": {"value": _r(inst_q / nonland_q) if nonland_q else 0.0, "instant_speed": inst_q, "nonland": nonland_q,
                 "why": "Instant-speed share of nonland cards (instants and flash)."},
        "durdle_cards": [{"name": f["name"], "qty": f["qty"], "reasons": f["durdle_reasons"]}
                         for f in sorted(durdle_cards, key=lambda x: x["name"])],
    }


def compute_review(inputs: Mapping) -> dict:
    """Pure. inputs: deck, texts, graph, engines, combos, genres, genre_version, journal, pets, template, weak."""
    deck = inputs["deck"]
    texts = inputs.get("texts") or {}
    graph = inputs.get("graph") or {"nodes": [], "edges": []}
    engines = list(inputs.get("engines") or [])
    commanders = [c for c in deck.get("commanders") or [] if c]
    qty = _entries(deck)
    total = sum(qty.values())
    composition = comp.compute_composition(deck, texts, template=inputs.get("template"))
    by_card = composition["by_card"]
    facts = {n: card_facts(n, texts.get(n), q, "ramp" in (by_card.get(n) or [])) for n, q in sorted(qty.items())}

    pets = sorted(p for p in (inputs.get("pets") or []) if isinstance(p, str))
    weak = inputs.get("weak")
    if weak is None:
        weak = weak_mod.compute_weak_performers(dict(deck), dict(graph), {}, {}, pets=pets, card_texts=texts)

    lines = win_lines(texts, graph, engines, (inputs.get("combos") or {}).get("in_deck") or [], commanders)
    strong, stragglers = strong_and_stragglers(facts, weak, graph, lines["top"], engines, pets, commanders)

    comp_out = {"template": {k: composition["template"][k] for k in ("name",) + comp.ROW_KEYS if k in composition["template"]},
                "rows": composition["rows"], "total_cards": composition["total_cards"],
                "unknown_cards": sorted(composition["unknown_cards"]),
                "why": "Counts by quantity vs the Command Zone template; a card can sit in two rows."}
    return {
        "schema": SCHEMA,
        "deck": {"registry_id": deck.get("registry_id"), "name": deck.get("name"), "commanders": commanders},
        "fingerprint": fingerprint(deck),
        "genre_registry_version": inputs.get("genre_version"),
        "genre": genre_section(graph, qty, inputs.get("genres") or [], inputs.get("genre_version")),
        "composition": comp_out,
        "curve_tempo": tempo_section(list(facts.values()), commanders, texts, total),
        "focus": focus_section(lines, inputs.get("journal")),
        "strong_cards": strong,
        "stragglers": stragglers,
        "combos_engines": combos_engines_section(inputs.get("combos"), engines),
        "card_flags": [{"name": f["name"], "qty": f["qty"], "cmc": f["cmc"], "active": f["active"],
                        "active_reasons": f["active_reasons"], "durdle": f["durdle"],
                        "durdle_reasons": f["durdle_reasons"], "instant_speed": f["instant_speed"]}
                       for f in facts.values() if not f["land"] or f["durdle"]],
        "inputs": {"graph_nodes": len(graph.get("nodes") or []), "graph_edges": len(graph.get("edges") or []),
                   "texts": len(texts), "weak_inputs": weak.get("inputs"), "pets": pets},
    }


def dumps(review: Mapping) -> str:
    """The canonical serialisation the determinism guarantee is stated against."""
    return json.dumps(review, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_inputs(r, deck: dict, combos_client=None) -> dict:
    """I/O: gather the cached inputs for a deck. Never calls an LLM; never raises on a missing input."""
    from . import insights, journal as journal_mod
    from .interactions import combos as combos_mod
    from .interactions import service as svc

    texts = insights.card_texts(r, deck)
    graph = svc.get_deck_graph(r, deck)
    engines = engines_mod.detect_engines(graph)
    try:
        combos = combos_mod.deck_combos(r, deck, client=combos_client)
    except Exception as exc:  # noqa: BLE001 - Spellbook is optional; the review renders without it
        combos = {"in_deck": [], "one_away": [], "error": str(exc)[:200]}
    genres_mod.ensure_seeded(r)
    pets = insights.pet_cards(r, deck)
    try:
        rec = journal_mod.load_journal(r, deck)
        journal = journal_mod.public(rec) if rec else None
    except Exception:  # noqa: BLE001
        journal = None
    return {"deck": deck, "texts": texts, "graph": graph, "engines": engines, "combos": combos,
            "genres": genres_mod.list_genres(r), "genre_version": genres_mod.get_version(r),
            "journal": journal, "pets": pets, "template": comp.get_template(r, deck),
            "weak": insights.deck_weak_performers(r, deck, graph)}


def review_deck(r, deck: dict, combos_client=None) -> dict:
    return compute_review(build_inputs(r, deck, combos_client))
