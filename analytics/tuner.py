"""
AN-6 Tuner (ANALYTICS_SPEC Layer 3, UX_SPEC §3.4): cuts, adds, shape
fixes and weak performers. Owned-first, with per-card price caps.

Every score here is deterministic. The LLM (Gemini, then Nous) only writes
the one-line "why" for a swap and the reason half of a soft-cap note. The
numbers in those lines are always filled in by this module.

Score (0..1, higher = stronger in THIS deck). It uses the same three inputs
as the weak-performer score (analytics/weak_performers.py), so a cut and
its replacement compare like with like:

    edges   min(1, plan edges between the card and the deck / p75)
            plan edges ignore mana/lands-only reasons; p75 = the deck's
            75th-percentile plan degree (the weak-performer p75)
    fit     the card's best pillar fit (local embeddings); dropped when the
            deck has no live pillars yet
    edhrec  min(1, inclusion on the commander's EDHREC page / 0.50); not
            listed = 0; dropped when EDHREC is unavailable
    score = mean of the parts that exist

Cuts are the deck's weak performers (weakest first), never pets, never
lands or commanders. Each cut gets the best owned and the best buy among
candidates that:
    - are in the commander's colour identity and not already in the deck;
    - keep the cut's role: a card in a functional composition row (ramp,
      card advantage, targeted or mass disruption) is replaced by a card in
      one of the same rows; a plan card by any non-land card;
    - beat the cut by at least MIN_UPGRADE;
    - aren't used by an earlier swap (a card is suggested once).

Owned-first (UX_SPEC D5): a buy is shown only when
buy.score - best_owned.score >= GAP_WORTH. Otherwise the row says "keep
yours" and names the near buy with its gap and price. Price caps (USD per
card, Scryfall; Journal override, else global default):
    hard cap  never an option; only counted in hidden_hard
    soft cap  shown, flagged, with a justification; the pick stays owned
              unless the gap is >= GAP_SOFT_OVERRIDE
Modes: owned_first (default), owned_only (no buys), best (higher score wins).

Redis:
    tuner:state:{registry_id}   {review_id, decisions{cut: {decision, add}}, adds[]}
                                reset when a newer finished review exists
    tuner:prose:{registry_id}   {cache key: text}  LLM prose cache
    tuner:count:{registry_id}   open suggestion count (deck tab badge)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from . import composition as comp
from . import journal as journal_mod
from . import weak_performers as weak_mod

log = logging.getLogger("commander_lab.tuner")

GAP_WORTH = 0.10
GAP_SOFT_OVERRIDE = 0.25
MIN_UPGRADE = 0.05
EDHREC_FULL = weak_mod.EDHREC_FULL
GLUE_REASONS = weak_mod.GLUE_REASONS
MODES = ("owned_first", "owned_only", "best")
DECISIONS = ("own", "buy", "dismissed")
MAX_SWAPS = 12

STATE_PREFIX = "tuner:state:"
PROSE_PREFIX = "tuner:prose:"
COUNT_PREFIX = "tuner:count:"

BASIC_BY_COLOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
WUBRG = "WUBRG"


class TunerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rid(deck: Mapping) -> str:
    return journal_mod._registry_id(deck)


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------


def score(edges: int, p75: float, fit: Optional[float], inclusion: Optional[float], has_pillars: bool,
          has_edhrec: bool, played: Optional[float] = None) -> float:
    parts = [min(1.0, max(0.0, (edges or 0) / (p75 or 1.0)))]
    if has_pillars:
        parts.append(min(1.0, max(0.0, fit if fit is not None else 0.0)))
    if has_edhrec:
        parts.append(min(1.0, max(0.0, (inclusion or 0.0) / EDHREC_FULL)))
    if played is not None:  # AN-7 card2vec: only when the model knows the card
        parts.append(min(1.0, max(0.0, played)))
    return round(sum(parts) / len(parts), 4)


def in_identity(card_colors: Optional[str], deck_colors: Optional[str]) -> bool:
    c = set(card_colors or "") - {"C"}
    d = set(deck_colors or "") - {"C"}
    return c <= d


def basics_for(colors: Optional[str], n: int) -> List[str]:
    cols = [c for c in WUBRG if c in set(colors or "")]
    names = [BASIC_BY_COLOR[c] for c in cols] or ["Wastes"]
    return [names[i % len(names)] for i in range(max(0, n))]


def edges_into(deck_tags: Mapping[str, Mapping], cand_tags: Mapping) -> Dict[str, int]:
    """{deck card: plan edges (each direction counts once)} for one candidate."""
    prod = set(cand_tags.get("produces") or []) - GLUE_REASONS
    cons = set(cand_tags.get("consumes") or []) - GLUE_REASONS
    out: Dict[str, int] = {}
    for name, t in deck_tags.items():
        n = 0
        if prod & set(t.get("consumes") or []):
            n += 1
        if cons & set(t.get("produces") or []):
            n += 1
        if n:
            out[name] = n
    return out


def _role_ok(cut_rows: List[str], cand: Mapping) -> bool:
    if cand.get("land"):
        return False
    func = [r for r in cut_rows if r in comp.FUNCTIONAL_ROWS and r != "lands"]
    if not func:
        return True
    return bool(set(func) & set(cand.get("rows") or []))


def _cap_of(price: Optional[float], caps: Mapping) -> Optional[str]:
    if caps.get("no_budget") or price is None:
        return None
    if caps.get("hard_cap") is not None and price > caps["hard_cap"]:
        return "hard"
    if caps.get("soft_cap") is not None and price > caps["soft_cap"]:
        return "soft"
    return None


def _option(c: Mapping, s: float, buy: bool, caps: Mapping) -> dict:
    o = {"name": c["name"], "score": s, "pillar": c.get("pillar"), "edges": c.get("edges", 0),
         "fit": c.get("fit"), "edhrec": c.get("inclusion"), "played": c.get("played"),
         "on_plan": bool(c.get("on_plan")), "combo": c.get("combo")}
    if buy:
        o.update(source="buy", price=c.get("price"), cap=_cap_of(c.get("price"), caps))
    else:
        free = (c.get("available") or 0) > 0
        o.update(source="free" if free else "deck", deck=None if free else ((c.get("decks") or [None])[0]),
                 price=0.0, list_price=c.get("price"), cap=None)
    return o


def soft_note(buy: Mapping, own: Optional[Mapping], gap: Optional[float], caps: Mapping, pick: str,
              reason: Optional[str] = None) -> str:
    money = journal_mod.money
    head = f"Soft cap: {buy['name']} ({money(buy.get('price'))})"
    if own and gap is not None:
        head += f" beats the owned {own['name']} by {gap:.2f}"
    head += "."
    if reason:
        head += " " + reason.strip().rstrip(".") + "."
    if pick == "buy":
        tail = (f" Picked anyway: the gap is at least {GAP_SOFT_OVERRIDE:.2f}, which is worth breaking your "
                f"{money(caps.get('soft_cap'))} soft cap.")
    else:
        tail = (f" Kept owned: {buy['name']} is over your {money(caps.get('soft_cap'))} soft cap, and breaking"
                f" the soft cap takes a gap of {GAP_SOFT_OVERRIDE:.2f} or more.")
    return head + tail


def _deck_colors(deck: Mapping) -> str:
    c = deck.get("color") or ""
    return "" if c == "C" else c


def shape_fixes(comp_rows: List[dict], cands: List[dict], deck: Mapping, limit: int = 3) -> List[dict]:
    out = []
    for row in comp_rows or []:
        key = row.get("key")
        if key == "plan" or (row.get("delta") or 0) > -2:
            continue
        short = -int(row["delta"])
        mine = [c for c in cands if c.get("owned") and key in (c.get("rows") or [])]
        free = sorted((c for c in mine if (c.get("available") or 0) > 0),
                      key=lambda c: (-(c.get("score") or 0), c["name"]))
        elsewhere = sorted((c for c in mine if (c.get("available") or 0) <= 0),
                           key=lambda c: (-(c.get("score") or 0), c["name"]))
        owned = free + elsewhere
        names = [c["name"] for c in free[:limit]]
        far = [f"{c['name']} (in {(c.get('decks') or ['another deck'])[0]})" for c in elsewhere[:max(0, limit - len(names))]]
        label = row.get("label") or key
        listing = ""
        if names:
            listing += " Owned, free: " + ", ".join(names) + "."
        if far:
            listing += " Owned, sleeved elsewhere: " + ", ".join(far) + "."
        # The gap column already says "−3 · 35 / 38"; the advice line only names cards (AN-6R).
        if key == "lands":
            basics = basics_for(_deck_colors(deck), short)
            advice = (listing + " Or add basics.").strip() if listing else "Nothing you own fits: add basics."
            action = {"kind": "add_basics", "count": short, "basics": basics, "label": f"Add {short} lands"}
        else:
            advice = listing.strip() or "Nothing you own fits."
            action = ({"kind": "filter", "row": key, "label": f"See {len(owned)} owned"} if owned else None)
        out.append({"key": key, "label": label, "actual": row.get("actual"), "target": row.get("target"),
                    "gap": row["delta"], "owned": [c["name"] for c in owned], "free": [c["name"] for c in free],
                    "advice": advice, "action": action})
    return out


def build_tune(deck: Mapping, weak: Mapping, composition: Mapping, cands: List[dict], caps: Mapping, *,
               p75: float, has_pillars: bool, has_edhrec: bool, mode: str = "owned_first", sleeved: bool = True,
               pets: Iterable[str] = (), state: Optional[Mapping] = None, max_swaps: int = MAX_SWAPS) -> dict:
    if mode not in MODES:
        raise TunerError(f"mode must be one of {list(MODES)}")
    pets = set(pets or ()) | {w["name"] for w in weak.get("cards") or [] if w.get("pet")}
    decisions = dict((state or {}).get("decisions") or {})
    in_deck = set(journal_mod.deck_entries(deck)) | set(deck.get("commanders") or [])
    # AN-7: when card2vec is on for this deck, EVERY card (cut or candidate)
    # gets a played part; a card the model doesn't know gets the deck's median
    # played score (neutral). So all scores are means over the same parts.
    has_played = bool((weak.get("inputs") or {}).get("played"))
    neutral = (weak.get("weights") or {}).get("played_neutral")

    def _played(v):
        if not has_played or neutral is None:
            return None
        return v if v is not None else neutral

    pool = []
    for c in cands:
        if c["name"] in in_deck:
            continue
        if c.get("owned"):
            if not sleeved and (c.get("available") or 0) <= 0:
                continue
        elif c.get("price") is None:
            continue  # an unpriced card can't be a buy
        c = dict(c)
        c["score"] = score(c.get("edges", 0), p75, c.get("fit"), c.get("inclusion"), has_pillars, has_edhrec,
                           _played(c.get("played")))
        pool.append(c)
    pool.sort(key=lambda c: (-c["score"], c["name"]))

    used = set()
    swaps = []
    weak_rows = [w for w in weak.get("cards") or [] if w["name"] not in pets]
    weak_rows.sort(key=lambda w: (-w["weak_score"], w["name"]))
    weak_rows = weak_rows[:max_swaps * 2]

    def _functional(w):
        return any(x in comp.FUNCTIONAL_ROWS and x != "lands" for x in w.get("rows") or [])

    # Role-bound cuts (removal, ramp, draw) pick first so a plan cut can't
    # take the only removal a removal cut could use. Rows are re-sorted below.
    for w in [w for w in weak_rows if _functional(w)] + [w for w in weak_rows if not _functional(w)]:
        dec = decisions.get(w["name"]) or {}
        if dec.get("decision") == "dismissed":
            continue
        cut_score = score(w.get("edges") or 0, p75, w.get("fit"), w.get("edhrec_inclusion"), has_pillars, has_edhrec,
                          _played(w.get("played")))
        fits = [c for c in pool if c["name"] not in used and _role_ok(w.get("rows") or [], c)
                and c["score"] >= cut_score + MIN_UPGRADE]
        owned = [c for c in fits if c.get("owned")]
        buys = [c for c in fits if not c.get("owned")] if mode != "owned_only" else []
        best_own = owned[0] if owned else None
        base = best_own["score"] if best_own else cut_score
        need = 0.0 if mode == "best" else (GAP_WORTH if best_own else MIN_UPGRADE)
        allowed = [c for c in buys if _cap_of(c.get("price"), caps) != "hard"]
        hidden = sum(1 for c in buys if _cap_of(c.get("price"), caps) == "hard" and c["score"] - base >= need
                     and (best_own is None or c["score"] > best_own["score"]))
        best_buy = allowed[0] if allowed else None
        show_buy = bool(best_buy and (best_own is None or best_buy["score"] - best_own["score"] >= need)
                        and (best_own is None or best_buy["score"] > best_own["score"]))
        if not best_own and not show_buy:
            continue
        own_o = _option(best_own, best_own["score"], False, caps) if best_own else None
        buy_o = _option(best_buy, best_buy["score"], True, caps) if show_buy else None
        gap = round(buy_o["score"] - own_o["score"], 4) if own_o and buy_o else None
        near = None
        if own_o and not buy_o and best_buy and mode == "owned_first" and best_buy["score"] > own_o["score"]:
            near = {"name": best_buy["name"], "price": best_buy.get("price"),
                    "gap": round(best_buy["score"] - own_o["score"], 4), "score": best_buy["score"],
                    "cap": _cap_of(best_buy.get("price"), caps)}
        if own_o and buy_o:
            if mode == "best":
                pick, verdict = "buy", "best"
            elif buy_o["cap"] == "soft" and gap < GAP_SOFT_OVERRIDE:
                pick, verdict = "own", "owned_good_enough"
            else:
                pick, verdict = "buy", "worth_buying"
        elif buy_o:
            pick, verdict = "buy", "buy"
        else:
            pick, verdict = "own", ("keep_yours" if near else "owned")
        used.add((own_o or {}).get("name"))
        used.add((buy_o or {}).get("name"))
        row = {
            "cut": {"name": w["name"], "weak_score": w["weak_score"], "score": cut_score,
                    "why": {"edges": w.get("edges") or 0, "fit": w.get("fit"), "edhrec": w.get("edhrec_inclusion"),
                            **({"played": w.get("played")} if w.get("played") is not None else {})},
                    "rows": w.get("rows") or [], "on_plan": w.get("fit_bucket") in ("pillar", "glue"),
                    "reasons": w.get("reasons") or []},
            "own": own_o, "buy": buy_o, "gap": gap, "pick": pick, "verdict": verdict, "near_buy": near,
            "hidden_hard": hidden, "soft_note": None, "why": None,
            "decision": dec.get("decision"), "decision_add": dec.get("add"),
        }
        if buy_o and buy_o["cap"] == "soft":
            row["soft_note"] = soft_note(buy_o, own_o, gap, caps, pick)
        swaps.append(row)
    # UX_SPEC: ordered by the cut's weak score (descending), then by gap.
    swaps.sort(key=lambda s: (-s["cut"]["weak_score"], -(s["gap"] or 0), s["cut"]["name"]))
    swaps = swaps[:max_swaps]

    with_swap = {s["cut"]["name"] for s in swaps}
    weak_table = [{"name": w["name"], "edges": w.get("edges") or 0, "fit": w.get("fit"),
                   "edhrec": w.get("edhrec_inclusion"), "played": w.get("played"), "weak_score": w["weak_score"],
                   "pet": w["name"] in pets, "has_swap": w["name"] in with_swap, "reasons": w.get("reasons") or []}
                  for w in sorted(weak.get("cards") or [], key=lambda w: (-w["weak_score"], w["name"]))]
    notice = weak.get("pet_notice")
    if not notice and pets:
        pw = sorted((w for w in weak.get("cards") or [] if w["name"] in pets), key=lambda w: -w["weak_score"])
        if pw:
            notice = {"name": pw[0]["name"], "weak_score": pw[0]["weak_score"]}
    if notice:
        wr = next((w for w in weak.get("cards") or [] if w["name"] == notice["name"]), {})
        notice = {"name": notice["name"], "weak_score": notice.get("weak_score"), "edges": wr.get("edges"),
                  "fit": wr.get("fit")}
    fixes = shape_fixes(composition.get("rows") or [], pool, deck)
    open_swaps = sum(1 for s in swaps if not s["decision"])
    return {
        "mode": mode, "sleeved": sleeved, "caps": dict(caps), "fixes": fixes, "swaps": swaps,
        "weak": weak_table, "pet_notice": notice,
        "counts": {"fixes": len(fixes), "swaps": len(swaps), "open_swaps": open_swaps,
                   "open": len(fixes) + open_swaps + (1 if notice else 0), "candidates": len(pool)},
        "constants": {"gap_worth": GAP_WORTH, "gap_soft_override": GAP_SOFT_OVERRIDE, "min_upgrade": MIN_UPGRADE,
                      "p75_degree": p75, "edhrec_full_at": EDHREC_FULL},
        "inputs": {"pillars": has_pillars, "edhrec": has_edhrec, "played": has_played and neutral is not None,
                   "played_neutral": neutral if has_played else None},
        "adds": list((state or {}).get("adds") or []),
        "note": "Score = mean of edges into the deck, pillar fit, EDHREC inclusion and played-with (card2vec; a "
                "card the model doesn't know counts as typical for this deck). All numbers are computed; the LLM "
                "only writes the why.",
    }


# ---------------------------------------------------------------------------
# State: decisions + extra adds (per deck, per review)
# ---------------------------------------------------------------------------


def _latest_review_id(r, deck) -> Optional[str]:
    last = next(iter(journal_mod.list_reviews(r, deck, 1)), None)
    return last and last.get("id")


def load_state(r, deck: Mapping) -> dict:
    rid = _latest_review_id(r, deck)
    raw = journal_mod._get_json(r, STATE_PREFIX + _rid(deck)) or {}
    if raw.get("review_id") != rid:
        return {"review_id": rid, "decisions": {}, "adds": []}
    return {"review_id": rid, "decisions": dict(raw.get("decisions") or {}), "adds": list(raw.get("adds") or [])}


def _save_state(r, deck, st: dict) -> dict:
    r.set(STATE_PREFIX + _rid(deck), json.dumps(st))
    return st


def decide(r, deck: Mapping, cut: str, decision: Optional[str], add: Optional[str] = None) -> dict:
    if decision is not None and decision not in DECISIONS:
        raise TunerError(f"decision must be one of {list(DECISIONS)} or null")
    if not isinstance(cut, str) or not cut.strip():
        raise TunerError("cut is required")
    st = load_state(r, deck)
    if decision is None:
        st["decisions"].pop(cut, None)
    else:
        if decision in ("own", "buy") and not (isinstance(add, str) and add.strip()):
            raise TunerError("accepting a swap needs the add card")
        st["decisions"][cut] = {"decision": decision, "add": add if decision != "dismissed" else None, "at": _now()}
    return _save_state(r, deck, st)


def set_adds(r, deck: Mapping, adds) -> dict:
    if not isinstance(adds, list):
        raise TunerError("adds must be a list")
    clean = []
    for a in adds:
        if not isinstance(a, Mapping) or not isinstance(a.get("name"), str) or not a["name"].strip():
            raise TunerError("each add needs a name")
        q = a.get("qty", 1)
        if not isinstance(q, int) or isinstance(q, bool) or not 1 <= q <= 99:
            raise TunerError("qty must be an integer 1-99")
        clean.append({"name": a["name"].strip(), "qty": q, "source": str(a.get("source") or "manual")[:40]})
    st = load_state(r, deck)
    st["adds"] = clean
    return _save_state(r, deck, st)


def mark_pet(r, deck: Mapping, name: str) -> dict:
    if name not in journal_mod.deck_entries(deck):
        raise TunerError(f"'{name}' is not in this deck")
    rec = journal_mod.load_journal(r, deck) or {}
    pets = list(rec.get("pet_cards") or [])
    if name not in pets:
        pets.append(name)
    saved = journal_mod.save_journal(r, deck, {"pet_cards": pets})
    st = load_state(r, deck)
    st["decisions"].pop(name, None)
    _save_state(r, deck, st)
    return {"pet_cards": saved.get("pet_cards") or []}


def caps_for(r, deck: Mapping) -> dict:
    return journal_mod.effective_caps(r, journal_mod.public(journal_mod.load_journal(r, deck)))


# ---------------------------------------------------------------------------
# LLM prose (the "why" only)
# ---------------------------------------------------------------------------

PROSE_SYSTEM = (
    "You write one short sentence per Magic: The Gathering Commander deck swap, explaining in plain words "
    "why the added card does the deck's job better than the cut card in this deck. Mention a concrete "
    "interaction with the commander or the deck's plan. Never state numbers, scores, percentages or prices: "
    "the app adds those itself. No hype. Max 25 words. Answer JSON: {\"items\": [{\"id\": ..., \"why\": ...}]}."
)
PROSE_SCHEMA = {"type": "OBJECT", "properties": {"items": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"id": {"type": "STRING"}, "why": {"type": "STRING"}},
    "required": ["id", "why"]}}}, "required": ["items"]}


def _prose_key(cut: str, add: str) -> str:
    return hashlib.sha1(f"{cut}\u2192{add}".encode()).hexdigest()[:16]


def _prose_items(tune: Mapping) -> List[dict]:
    items = []
    for s in tune.get("swaps") or []:
        cut = s["cut"]["name"]
        add = (s["buy"] if s["pick"] == "buy" else s["own"]) or s["own"] or s["buy"]
        items.append({"id": _prose_key(cut, add["name"]), "cut": cut, "add": add["name"], "slot": cut,
                      "kind": "why"})
        if s.get("buy") and s["buy"].get("cap") == "soft" and s.get("own"):
            items.append({"id": _prose_key(s["own"]["name"], s["buy"]["name"]), "cut": s["own"]["name"],
                          "add": s["buy"]["name"], "slot": cut, "kind": "soft"})
    return items


def _load_prose(r, deck) -> Dict[str, str]:
    return journal_mod._get_json(r, PROSE_PREFIX + _rid(deck)) or {}


def attach_cached_prose(r, deck: Mapping, tune: dict) -> dict:
    cache = _load_prose(r, deck)
    by_slot = {}
    for it in _prose_items(tune):
        if it["id"] in cache:
            by_slot.setdefault(it["slot"], {})[it["kind"]] = cache[it["id"]]
    for s in tune.get("swaps") or []:
        got = by_slot.get(s["cut"]["name"]) or {}
        if got.get("why"):
            s["why"] = got["why"]
        if got.get("soft") and s.get("buy"):
            s["soft_note"] = soft_note(s["buy"], s.get("own"), s.get("gap"), tune.get("caps") or {}, s["pick"],
                                       got["soft"])
    return tune


def _prose_prompt(deck: Mapping, items: List[dict], context: str = "") -> str:
    head = f"Commander: {' + '.join(deck.get('commanders') or [])}. Deck: {deck.get('name')}.\n"
    if context:
        head += context.strip() + "\n"
    payload = [{"id": i["id"], "cut": i["cut"], "add": i["add"]} for i in items]
    return head + "For each item, why is `add` better than `cut` in this deck?\nSWAPS:" + json.dumps(payload)


def generate_prose(r, deck: Mapping, tune: dict, chain, context: str = "",
                   on_call: Optional[Callable[[dict], None]] = None) -> dict:
    items = _prose_items(tune)
    cache = _load_prose(r, deck)
    todo = [i for i in items if i["id"] not in cache]
    llm = {"status": "cached", "provider": None, "model": None, "cost_usd": 0.0, "calls": 0,
           "cached": sum(1 for s in tune.get("swaps") or []
                         if all(i["id"] in cache for i in items if i["slot"] == s["cut"]["name"]))}
    calls: List[dict] = []
    if todo:
        if chain is None or not chain.available():
            llm["status"] = "no_llm"
        else:
            def _log(c):
                calls.append(c)
                if on_call:
                    on_call(c)

            data, provider, model = chain.generate_json(PROSE_SYSTEM, _prose_prompt(deck, todo, context),
                                                        schema=PROSE_SCHEMA, purpose="tuner_why", on_call=_log)
            llm.update(provider=provider, model=model, calls=len(calls),
                       cost_usd=round(sum(c.get("cost_usd") or 0.0 for c in calls), 6))
            if data is None:
                llm["status"] = "llm_failed"
            else:
                wanted = {i["id"] for i in todo}
                for it in (data.get("items") if isinstance(data, dict) else None) or []:
                    if isinstance(it, dict) and it.get("id") in wanted and isinstance(it.get("why"), str):
                        cache[it["id"]] = it["why"].strip()[:300]
                r.set(PROSE_PREFIX + _rid(deck), json.dumps(cache))
                llm["status"] = "ok"
            _record_run(r, deck, calls, llm)
    attach_cached_prose(r, deck, tune)
    prose = {}
    if llm["status"] != "llm_failed":
        for s in tune.get("swaps") or []:
            if s.get("why") or (s.get("soft_note") and s["buy"] and s["buy"]["name"] in (s["soft_note"] or "")):
                prose[s["cut"]["name"]] = {"why": s.get("why"), "soft_note": s.get("soft_note")}
        prose = {k: v for k, v in prose.items() if v.get("why")}
    return {"prose": prose, "llm": llm}


def _record_run(r, deck, calls: List[dict], llm: Mapping) -> None:
    try:
        from .interactions import service as interactions_service

        run = interactions_service.RunLog("tuner")
        for c in calls:
            run.add_llm_call({**c, "deck": _rid(deck)})
        run.status = "ok" if llm.get("status") == "ok" else llm.get("status", "error")
        run.extra["tuner"] = {"deck": _rid(deck), "status": llm.get("status"), "provider": llm.get("provider")}
        interactions_service._record_run(r, run.to_dict())
    except Exception:  # noqa: BLE001 - the run log is best effort
        log.exception("could not record tuner run")


# ---------------------------------------------------------------------------
# I/O: gather the inputs from Redis and compute
# ---------------------------------------------------------------------------


def _json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _mget(r, keys: List[str]) -> List:
    if not keys:
        return []
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    out = []
    for i in range(0, len(keys), 500):
        out.extend(mget(keys[i:i + 500]))
    return out


def _owned(r) -> Dict[str, dict]:
    from .interactions import combos as combos_mod

    return {v["name"]: v for v in combos_mod.owned_index(r).values()}


def candidate_pool(r, deck: Mapping, graph: Mapping, inclusion: Mapping, ident: Optional[Mapping], embedder,
                   fetch_prices: Optional[Callable] = None, scryfall=None,
                   c2v: Optional[Callable[[List[str]], Mapping]] = None) -> List[dict]:
    """Owned cards (any status) + cards on the commander's EDHREC page, minus
    the deck, in colour identity. Each with tags, edges into the deck, fit,
    inclusion, played-with (card2vec; `c2v(names)` -> {name: {sim, score}}),
    composition rows, ownership and (for buys) a price."""
    from . import edhrec_inclusion
    from . import pillar_fit
    from . import prices as prices_mod
    from .interactions.tagger import InteractionTagger, tag_key

    deck_names = set(journal_mod.deck_entries(deck)) | set(deck.get("commanders") or [])
    colors = _deck_colors(deck)
    owned = _owned(r)

    deck_tags = {n["name"]: {"produces": n.get("produces") or [], "consumes": n.get("consumes") or []}
                 for n in graph.get("nodes") or []}

    page_cards = {}
    try:
        page = edhrec_inclusion.commander_page(r, list(deck.get("commanders") or []))
        page_cards = page.get("cards") or {}
    except Exception as exc:  # noqa: BLE001 - EDHREC is optional
        log.info("tuner: EDHREC page unavailable: %s", exc)
    inc_by_key = {k: v.get("inclusion") for k, v in page_cards.items()}

    names = set()
    meta: Dict[str, dict] = {}
    owned_names = [n for n in owned if n not in deck_names]
    for n, raw in zip(owned_names, _mget(r, [f"card_meta:{n}" for n in owned_names])):
        m = _json(raw)
        if not m or not in_identity(m.get("color") or "C", colors):
            continue
        meta[n] = m
        names.add(n)
    for v in page_cards.values():
        n = v.get("name")
        if n and n not in deck_names:
            names.add(n)
    names = sorted(names)

    tags: Dict[str, dict] = {}
    for n, raw in zip(names, _mget(r, [tag_key(n) for n in names])):
        t = _json(raw)
        if t:
            tags[n] = t
    missing = [n for n in names if n not in tags]
    if missing:
        try:
            from .interactions.llm import LLMChain
            from .interactions.service import RunLog
            from .interactions.tagger import ScryfallSource

            tagger = InteractionTagger(r, scryfall=scryfall if scryfall is not None else ScryfallSource(r),
                                       llm=LLMChain([]))
            tags.update(tagger.tag_cards(missing, RunLog("tuner-tags")))
        except Exception as exc:  # noqa: BLE001 - untagged cards just score 0 edges
            log.warning("tuner: tagging %d candidates failed: %s", len(missing), exc)

    texts = {}
    for n in names:
        card = (tags.get(n) or {}).get("card")
        if not card and n in meta:
            m = meta[n]
            card = {"type_line": " ".join((m.get("super_types") or []) + [m.get("type") or ""]).strip(),
                    "oracle_text": m.get("oracle_text") or "", "mana_cost": ""}
        texts[n] = card or {}

    pillars = pillar_fit.live_pillars(ident)
    fit_by: Dict[str, tuple] = {}
    if pillars and embedder is not None and embedder.available():
        ctext = {n: pillar_fit.card_text(n, texts.get(n)) for n in names}
        cvecs = pillar_fit.card_vectors(r, embedder, ctext)
        pvecs = embedder.embed([pillar_fit.pillar_text(p) for p in pillars], kind="query")
        for n in names:
            v = cvecs.get(n)
            if v is None:
                continue
            sims = [sum(a * b for a, b in zip(v, pv)) for pv in pvecs]
            i = max(range(len(sims)), key=lambda k: sims[k])
            fit_by[n] = (embedder.scale(sims[i]), sims[i] >= embedder.on_pillar_sim, pillars[i]["name"])

    buy_names = [n for n in names if n not in owned]
    played_by: Dict[str, float] = {}
    if c2v is not None:
        try:
            played_by = {n: v["score"] for n, v in (c2v(names) or {}).items() if v.get("score") is not None}
        except Exception as exc:  # noqa: BLE001 - card2vec is an optional signal
            log.warning("tuner: card2vec failed: %s", exc)
    prices = {}
    if buy_names:
        try:
            prices = prices_mod.card_prices(r, buy_names, fetch=fetch_prices)
        except Exception as exc:  # noqa: BLE001
            log.warning("tuner: prices failed: %s", exc)

    out = []
    for n in names:
        rows = comp.classify_card(n, texts.get(n)) or ["plan"]
        t = tags.get(n) or {}
        by = edges_into(deck_tags, t)
        f = fit_by.get(n)
        o = owned.get(n)
        land = rows == ["lands"]
        out.append({
            "name": n, "edges": sum(by.values()), "edges_by": dict(sorted(by.items(), key=lambda kv: -kv[1])[:5]),
            "fit": f[0] if f else None, "pillar": f[2] if f and f[1] else None,
            "on_plan": bool((f and f[1] and not land) or any(x in comp.FUNCTIONAL_ROWS for x in rows)),
            "inclusion": inc_by_key.get(edhrec_inclusion.name_key(n)) or 0.0, "rows": rows, "land": land,
            "played": None if land else played_by.get(n),
            "owned": bool(o), "available": (o or {}).get("available", 0), "decks": (o or {}).get("decks", []),
            "price": None if o else prices.get(n),
            "sources": (["owned"] if o else []) + (["edhrec"] if edhrec_inclusion.name_key(n) in inc_by_key else []),
        })
    return out


def compute(r, deck: Mapping, mode: str = "owned_first", sleeved: bool = True, embedder=None,
            fetch_prices: Optional[Callable] = None, scryfall=None) -> dict:
    """Full Tune payload for one deck (GET /api/decks/{id}/tune)."""
    from . import embeddings
    from . import identity
    from . import insights
    from .interactions import service as interactions_service

    if mode not in MODES:
        raise TunerError(f"mode must be one of {list(MODES)}")
    t0 = time.monotonic()
    deck = dict(deck)
    texts = insights.card_texts(r, deck)
    ident = identity.load_identity(r, deck)
    emb = embedder or embeddings.default_embedder()
    fit = insights.pillar_fit_safe(r, deck, ident, texts) if embedder is None else \
        insights.pillar_fit.deck_fit(r, deck, ident, texts, emb)
    inc = insights.deck_edhrec(r, deck, texts=texts)
    graph = interactions_service.get_deck_graph(r, deck)
    pets = insights.pet_cards(r, deck)
    played = insights.deck_played(r, deck, texts)
    weak = weak_mod.compute_weak_performers(deck, graph, fit, inc, pets=pets, card_texts=texts, played=played)
    composition = insights.deck_composition(r, deck, texts)
    caps = caps_for(r, deck)
    c2v = (lambda names: insights.deck_played(r, deck, texts, names).get("candidates")) \
        if played.get("status") == "ok" else None
    cands = candidate_pool(r, deck, graph, inc, ident, emb, fetch_prices=fetch_prices, scryfall=scryfall, c2v=c2v)
    state = load_state(r, deck)
    out = build_tune(deck, weak, composition, cands, caps, p75=weak["weights"]["p75_degree"],
                     has_pillars=weak["inputs"]["pillars"], has_edhrec=weak["inputs"]["edhrec"], mode=mode,
                     sleeved=sleeved, pets=pets, state=state)
    attach_cached_prose(r, deck, out)
    price_ages = []
    for raw in _mget(r, [f"price:card:{s['buy']['name']}" for s in out["swaps"] if s.get("buy")]):
        rec = _json(raw)
        if rec and rec.get("at"):
            price_ages.append(rec["at"])
    out["deck"] = {"registry_id": _rid(deck), "name": deck.get("name"), "commanders": deck.get("commanders") or [],
                   "colors": _deck_colors(deck)}
    out["on_plan"] = {"pct": fit.get("on_plan_pct"), "on": (fit.get("pillar") or 0) + (fit.get("glue") or 0),
                      "total": fit.get("total_cards")}
    out["prices_at"] = min(price_ages) if price_ages else None
    out["review_id"] = state.get("review_id")
    out["elapsed_ms"] = round(1000 * (time.monotonic() - t0))
    try:
        r.set(COUNT_PREFIX + _rid(deck), str(out["counts"]["open"]))
    except Exception:  # noqa: BLE001
        pass
    return out


def open_count(r, deck: Mapping) -> Optional[int]:
    try:
        raw = r.get(COUNT_PREFIX + _rid(deck))
        return int(raw) if raw not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def prose_context(r, deck: Mapping) -> str:
    """Thesis + pillars for the prose prompt (labels only, no numbers)."""
    from . import identity

    ident = identity.load_identity(r, deck) or {}
    bits = []
    if (ident.get("thesis") or {}).get("text"):
        bits.append("Thesis: " + ident["thesis"]["text"])
    ps = [p.get("name") for p in identity.live_pillars(ident)]
    if ps:
        bits.append("Pillars: " + "; ".join(ps))
    return "\n".join(bits)
