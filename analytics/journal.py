"""
Deck Journal + review check-in (AN-5, UX_SPEC.md §3.3, ANALYTICS_SPEC Layer 2).

The journal is the user_intent.py schema for one deck plus the AN-5 fields
(pet_cards[], novelties, budget_constraints.hard_cap / soft_cap) and the BR-2
target_bracket (official Commander Bracket 1-5, or null = no bracket checks;
stored beside target_power_level, never derived from it). It is
filled once and editable anytime. Every review opens with a check-in:
"since the last review: +/- cards, power, price, on-plan; is this still
true?", one row per field, each with optional computed evidence.

Redis keys (all JSON, no TTL):
    journal:deck:{registry_id}         resolved intent + {"_journal": {created_at, updated_at, baseline}}
                                       (insights.pet_cards reads pet_cards from here)
    journal:defaults                   global per-card caps {hard_cap, soft_cap}
    journal:review:open:{registry_id}  the check-in in progress (answers save per row)
    journal:reviews:{registry_id}      LIST of finished reviews, newest first (max HISTORY_MAX)

A review record: {id, started_at, finished_at, skipped, since, snapshot,
deltas, answers{field: {answer, was?, at}}}. `snapshot` = {at, cards{name:
qty}, power, price, on_plan}; the next check-in diffs against the last
review's snapshot (or the journal's baseline, taken when it was first saved).

Everything here is deterministic. No LLM.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from user_intent import POWER_SCALE_TIER_MAP, resolve_intent_defaults, validate_intent

from .brackets import BRACKETS

log = logging.getLogger("commander_lab.journal")

BRACKET_NAMES = {b.number: b.name for b in BRACKETS}  # BR-2 check-in row label (BR-1 is the source)

KEY_PREFIX = "journal:deck:"
DEFAULTS_KEY = "journal:defaults"
OPEN_PREFIX = "journal:review:open:"
REVIEWS_PREFIX = "journal:reviews:"
HISTORY_MAX = 50
DEFAULT_CAPS = {"hard_cap": 30.0, "soft_cap": 10.0}
POWER_EVIDENCE_GAP = 1.5  # same tolerance as analytics.engine's power_aligned

FIELDS = ["vision", "win_cons", "power", "bracket", "budget", "pets", "novelties", "pillars", "cards"]
LABELS = {"vision": "Vision", "win_cons": "Win cons", "power": "Target power", "bracket": "Target bracket",
          "budget": "Budget", "pets": "Pet cards", "novelties": "Novelties", "pillars": "Pillars",
          "cards": "Card changes"}
# Journal sections a "Change" on each row may write. Pillars live on Identity; card changes are context.
SECTIONS = {"vision": ("deck_vision",), "win_cons": ("preferred_win_conditions",),
            "power": ("target_power_level",), "bracket": ("target_bracket",), "budget": ("budget_constraints",),
            "pets": ("pet_cards",), "novelties": ("novelties", "freeform_notes"), "pillars": (), "cards": ()}
SECTION_KEYS = ("deck_vision", "target_power_level", "target_bracket", "budget_constraints",
                "preferred_win_conditions", "archetype_preferences", "pet_cards", "novelties", "freeform_notes",
                "metadata")

INTENT_LABELS = {"new_build": "New build", "tune_up": "Tune-up", "overhaul": "Overhaul"}
WINCON_LABELS = {"combat": "Combat", "combo": "Combo", "attrition": "Attrition",
                 "commander_damage": "Commander damage", "alternate_wincon": "Alt win", "mill": "Mill",
                 "burn": "Burn", "lockout": "Lockout"}
# Form order: the 4 most common first (shown), the rest behind "More…" (UX_SPEC §3.3.1).
WINCON_ORDER = ("combat", "combo", "attrition", "commander_damage", "alternate_wincon", "burn", "lockout", "mill")
WINCON_SHORT = dict(WINCON_LABELS, commander_damage="Cmdr dmg")


class JournalError(ValueError):
    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


class NoOpenReview(JournalError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registry_id(deck: Mapping) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def _get_json(r, key: str):
    raw = r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"${v:,.0f}" if abs(v - round(v)) < 0.005 else f"${v:,.2f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------
# Journal store
# ---------------------------------------------------------------------------


def load_journal(r, deck: Mapping) -> Optional[dict]:
    return _get_json(r, KEY_PREFIX + _registry_id(deck))


def public(rec: Optional[Mapping]) -> dict:
    """The journal without bookkeeping, every field resolved."""
    src = {k: v for k, v in (rec or {}).items() if k != "_journal"}
    out = resolve_intent_defaults(src)
    out["target_power_level"]["tier"] = POWER_SCALE_TIER_MAP.get(out["target_power_level"]["scale"], "optimized")
    return out


def _normalize(payload: dict) -> dict:
    p = copy.deepcopy(payload)
    pl = p.get("target_power_level")
    if isinstance(pl, dict):
        pl.pop("tier", None)  # the tier is derived from the scale, never picked
    bc = p.get("budget_constraints")
    if isinstance(bc, dict):
        if "hard_cap" in bc:
            bc["max_card_price"] = bc["hard_cap"]
        if bc.get("no_budget") is True:
            for k in ("total_budget", "max_card_price", "hard_cap", "soft_cap"):
                bc[k] = None
    pets = p.get("pet_cards")
    if isinstance(pets, list) and all(isinstance(x, str) for x in pets):
        seen, clean = set(), []
        for x in (s.strip() for s in pets):
            if x and x not in seen:
                seen.add(x)
                clean.append(x)
        p["pet_cards"] = clean
    return p


def save_journal(r, deck: Mapping, payload: Mapping, snapshot: Optional[dict] = None,
                 now: Optional[str] = None) -> dict:
    """Merge `payload` (whole top-level sections) into the stored journal,
    validate, and store. `snapshot` becomes the baseline the first check-in
    diffs against; it is only used on the first save."""
    if not isinstance(payload, Mapping):
        raise JournalError("Journal must be a JSON object.")
    now = now or _now()
    old = load_journal(r, deck)
    meta = dict((old or {}).get("_journal") or {})
    merged = {k: v for k, v in (old or {}).items() if k != "_journal"}
    merged.update({k: v for k, v in payload.items() if k != "_journal"})
    merged = _normalize(merged)
    ok, errors = validate_intent(merged, strict_coherence=False)
    if not ok:
        raise JournalError("Invalid journal: " + "; ".join(errors), errors)
    rec = public(merged)
    meta.setdefault("created_at", now)
    meta["updated_at"] = now
    if not meta.get("baseline") and snapshot:
        meta["baseline"] = snapshot
    rec["_journal"] = meta
    r.set(KEY_PREFIX + _registry_id(deck), json.dumps(rec))
    return rec


# ---------------------------------------------------------------------------
# Caps (global default + per-deck override)
# ---------------------------------------------------------------------------


def get_defaults(r) -> dict:
    out = dict(DEFAULT_CAPS)
    stored = _get_json(r, DEFAULTS_KEY) or {}
    for k in out:
        if isinstance(stored.get(k), (int, float)) and not isinstance(stored.get(k), bool):
            out[k] = float(stored[k])
    return out


def set_defaults(r, payload: Mapping) -> dict:
    if not isinstance(payload, Mapping):
        raise JournalError("Defaults must be a JSON object.")
    cur = get_defaults(r)
    errors = []
    for k in ("hard_cap", "soft_cap"):
        if k not in payload:
            continue
        v = payload[k]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            errors.append(f"{k} must be a number >= 0.")
        else:
            cur[k] = float(v)
    if not errors and cur["soft_cap"] > cur["hard_cap"]:
        errors.append(f"soft_cap ({cur['soft_cap']}) cannot exceed hard_cap ({cur['hard_cap']}).")
    if errors:
        raise JournalError("; ".join(errors), errors)
    r.set(DEFAULTS_KEY, json.dumps(cur))
    return cur


def effective_caps(r, rec: Mapping) -> dict:
    bc = (rec or {}).get("budget_constraints") or {}
    d = get_defaults(r)
    if bc.get("no_budget"):
        return {"no_budget": True, "hard_cap": None, "soft_cap": None, "hard_source": "none",
                "soft_source": "none", "defaults": d}
    out = {"no_budget": False, "defaults": d}
    for k, src in (("hard_cap", "hard_source"), ("soft_cap", "soft_source")):
        v = bc.get(k)
        out[k], out[src] = (float(v), "deck") if v is not None else (d[k], "global")
    return out


# ---------------------------------------------------------------------------
# Snapshots + deltas
# ---------------------------------------------------------------------------


def deck_entries(deck: Mapping) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        n = c.get("name")
        if n:
            out[n] = out.get(n, 0) + int(c.get("quantity") or 1)
    return out


def snapshot(deck: Mapping, ctx: Mapping, at: Optional[str] = None) -> dict:
    return {"at": at or ctx.get("at") or _now(), "cards": deck_entries(deck), "power": ctx.get("power"),
            "price": ctx.get("price"), "on_plan": ctx.get("on_plan")}


def deltas(prev: Optional[Mapping], cur: Mapping) -> dict:
    prev = prev or {}
    a, b = prev.get("cards"), cur.get("cards") or {}
    if a is None:
        added, removed = [], []
    else:
        added = sorted(n for n, q in b.items() if q > a.get(n, 0))
        removed = sorted(n for n, q in a.items() if q > b.get(n, 0))
    return {"added": added, "removed": removed,
            **{k: [prev.get(k), cur.get(k)] for k in ("power", "price", "on_plan")}}


def _days_between(a: Optional[str], b: str) -> Optional[int]:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Check-in rows
# ---------------------------------------------------------------------------


def _sentence(s: str) -> str:
    s = (s or "").strip()
    return s if not s or s[-1] in ".!?" else s + "."


def _value(field: str, rec: Mapping, ctx: Mapping, caps: Mapping) -> dict:
    if field == "vision":
        dv = rec["deck_vision"]
        parts = [INTENT_LABELS.get(dv["intent"], dv["intent"]) + "."]
        if dv.get("description"):
            parts.append(_sentence(dv["description"]))
        if dv.get("target_turn_win"):
            parts.append(f"Win by turn {dv['target_turn_win']}.")
        return {"value": " ".join(parts)}
    if field == "win_cons":
        wc = rec["preferred_win_conditions"]
        lab = lambda k: WINCON_LABELS.get(k, k)  # noqa: E731
        parts = [f"{lab(wc['primary'])} (primary)"] + [lab(k) for k in wc.get("secondary") or []]
        parts += list(wc.get("custom_win_conditions") or [])
        if wc.get("disliked_or_excluded"):
            parts.append("excluded: " + ", ".join(lab(k) for k in wc["disliked_or_excluded"]))
        return {"value": " · ".join(parts)}
    if field == "power":
        pl = rec["target_power_level"]
        return {"value": f"{pl['scale']} · {POWER_SCALE_TIER_MAP.get(pl['scale'], pl.get('tier'))}"}
    if field == "bracket":
        tb = rec.get("target_bracket")
        if tb is None:
            return {"value": "Not set (no bracket checks)."}
        name = BRACKET_NAMES.get(tb)
        return {"value": f"Bracket {tb} · {name}" if name else f"Bracket {tb}"}
    if field == "budget":
        bc = rec["budget_constraints"]
        if bc.get("no_budget"):
            return {"value": "No budget (caps ignored)"}
        parts = []
        if bc.get("total_budget") is not None:
            parts.append(f"{money(bc['total_budget'])} total")
        cap = f"per-card hard {money(caps.get('hard_cap'))} / soft {money(caps.get('soft_cap'))}"
        if caps.get("hard_source") == "global" and caps.get("soft_source") == "global":
            cap += " (global defaults)"
        parts.append(cap)
        return {"value": " · ".join(parts)}
    if field == "pets":
        pets = list(rec.get("pet_cards") or [])
        return {"value": ", ".join(pets) if pets else "No pets yet.", "value_list": pets}
    if field == "novelties":
        return {"value": rec.get("novelties") or "None."}
    if field == "pillars":
        live = [p for p in ctx.get("pillars") or [] if p.get("state") != "rejected"]
        return {"value": " · ".join(p.get("name", "") for p in live) if live else "No pillars yet."}
    if field == "cards":
        d = ctx.get("deltas") or {}
        add, rem = list(d.get("added") or []), list(d.get("removed") or [])
        if not add and not rem:
            return {"value": "No card changes."}

        def line(sign, names):
            shown = names[:6]
            return f"{sign} " + ", ".join(shown) + (f", …{len(names) - 6} more" if len(names) > 6 else "")
        return {"value": " · ".join(x for x in [line("+", add) if add else "", line("−", rem) if rem else ""] if x)}
    raise JournalError(f"Unknown field '{field}'.")


def _over(prices: Mapping, names: List[str], cap: float, lo_exclusive: Optional[float] = None) -> List[tuple]:
    out = []
    for n in names:
        p = prices.get(n)
        if p is not None and p > cap and (lo_exclusive is None or p <= lo_exclusive):
            out.append((n, p))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def _over_line(rows: List[tuple], kind: str, cap: float) -> str:
    listed = ", ".join(f"{n} {money(p)}" for n, p in rows[:3]) + (f", …{len(rows) - 3} more" if len(rows) > 3 else "")
    return f"{_plural(len(rows), 'card')} over {kind} cap {money(cap)} ({listed})."


def _evidence(field: str, rec: Mapping, ctx: Mapping, caps: Mapping, deck: Mapping) -> Optional[str]:
    names = sorted(deck_entries(deck)) if deck else []
    if field == "vision":
        if not (rec["deck_vision"].get("description") or "").strip():
            return "Empty: no vision written yet. It feeds the thesis generator."
        return None
    if field == "power":
        est, target = ctx.get("power"), rec["target_power_level"]["scale"]
        if est is not None and abs(est - target) > POWER_EVIDENCE_GAP:
            return f"Estimate {est:g} vs target {target}."
        return None
    if field == "budget":
        bc = rec["budget_constraints"]
        if bc.get("no_budget"):
            return None
        out, price, prices = [], ctx.get("price"), ctx.get("prices") or {}
        if bc.get("total_budget") and price is not None:
            out.append(f"Deck is {money(price)}, {round(100 * price / bc['total_budget'])}% of budget.")
        hard, soft = caps.get("hard_cap"), caps.get("soft_cap")
        if hard is not None:
            rows = _over(prices, names, hard)
            if rows:
                out.append(_over_line(rows, "hard", hard))
        if soft is not None:
            rows = _over(prices, names, soft, lo_exclusive=hard)
            if rows:
                out.append(_over_line(rows, "soft", soft))
        return " ".join(out) or None
    if field == "pets":
        pets = list(rec.get("pet_cards") or [])
        if not pets:
            return None
        out = []
        gone = [p for p in pets if names and p not in names]
        if gone:
            out.append(f"{', '.join(gone)} {'is' if len(gone) == 1 else 'are'} no longer in the deck.")
        notice = ctx.get("pet_notice") or {}
        if notice.get("name") in pets:
            edges = notice.get("edges")
            e = f" has {_plural(edges, 'edge')} and" if isinstance(edges, int) else ""
            out.append(f"{notice['name']}{e} is your weakest pet. Still a pet?")
        return " ".join(out) or None
    if field == "pillars":
        live = [p for p in ctx.get("pillars") or [] if p.get("state") != "rejected"]
        if not live:
            return "No pillars yet. Draft them on Identity."
        out = []
        n = ((ctx.get("proposal") or {}).get("new")) or 0
        if n:
            out.append(f"Re-sync proposed {_plural(n, 'new pillar')}.")
        drafts = sum(1 for p in live if p.get("state") == "draft")
        if drafts:
            out.append(f"{_plural(drafts, 'pillar')} {'is' if drafts == 1 else 'are'} still a draft.")
        return " ".join(out) or None
    return None


def build_rows(rec: Mapping, ctx: Mapping, answers: Mapping, caps: Mapping, deck: Mapping) -> List[dict]:
    rows = []
    for f in FIELDS:
        a = answers.get(f) or {}
        row = {"field": f, "label": LABELS[f], **_value(f, rec, ctx, caps),
               "evidence": _evidence(f, rec, ctx, caps, deck), "answer": a.get("answer"), "was": a.get("was"),
               "editable": bool(SECTIONS[f]), "sections": list(SECTIONS[f])}
        if f == "pillars":
            row["link"] = "/deck/" + quote(deck.get("name") or "") + "/identity"
        if f == "cards":
            row["context"] = True
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Review lifecycle
# ---------------------------------------------------------------------------


def _open_key(deck) -> str:
    return OPEN_PREFIX + _registry_id(deck)


def list_reviews(r, deck: Mapping, limit: int = 20) -> List[dict]:
    out = []
    for raw in r.lrange(REVIEWS_PREFIX + _registry_id(deck), 0, limit - 1) or []:
        try:
            out.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def load_open(r, deck: Mapping) -> Optional[dict]:
    return _get_json(r, _open_key(deck))


def _save_open(r, deck, review: dict) -> None:
    r.set(_open_key(deck), json.dumps(review))


def review_view(r, deck: Mapping, review: Mapping, rec: Mapping, now: Optional[str] = None) -> dict:
    now = now or _now()
    ctx = dict(review.get("ctx") or {})
    since = review.get("since") or {}
    cur = snapshot(deck, ctx, at=now)
    d = deltas(since.get("snapshot"), cur)
    ctx["deltas"] = d
    answers = review.get("answers") or {}
    rows = build_rows(rec, ctx, answers, effective_caps(r, rec), deck)
    return {
        "id": review["id"], "started_at": review.get("started_at"),
        "since": {"at": since.get("at"), "days": _days_between(since.get("at"), now), "kind": since.get("kind")},
        "deltas": d,
        "metrics": {"power": ctx.get("power"), "price": ctx.get("price"), "on_plan": ctx.get("on_plan")},
        "rows": rows,
        "progress": {"total": len(rows), "answered": sum(1 for x in rows if x["answer"]),
                     "changed": sum(1 for x in rows if x["answer"] == "changed")},
    }


def _ctx_for_storage(ctx: Mapping) -> dict:
    keep = ("power", "price", "on_plan", "prices", "pillars", "proposal", "pet_notice", "unpriced")
    return {k: ctx.get(k) for k in keep}


def start_review(r, deck: Mapping, ctx: Mapping, now: Optional[str] = None) -> dict:
    """Open a check-in (or resume the open one with fresh metrics)."""
    rec = load_journal(r, deck)
    if not rec:
        raise JournalError("Fill in the Deck Journal first: the check-in reviews it.")
    now = now or _now()
    review = load_open(r, deck)
    if not review:
        last = next(iter(list_reviews(r, deck, 1)), None)
        if last:
            since = {"at": last.get("finished_at"), "snapshot": last.get("snapshot"), "kind": "review"}
        else:
            base = (rec.get("_journal") or {}).get("baseline") or {}
            since = {"at": base.get("at") or (rec.get("_journal") or {}).get("created_at"), "snapshot": base or None,
                     "kind": "journal"}
        review = {"id": uuid.uuid4().hex[:12], "started_at": now, "since": since, "answers": {}}
    review["ctx"] = _ctx_for_storage(ctx)
    _save_open(r, deck, review)
    return review_view(r, deck, review, rec, now)


def answer_row(r, deck: Mapping, field: str, answer: Optional[str], journal_payload: Optional[Mapping] = None,
               now: Optional[str] = None) -> dict:
    """answer: "true" (still true), "changed" (journal_payload holds the
    edited section(s), saved now), or None (undo: a change is reverted)."""
    review = load_open(r, deck)
    if not review:
        raise NoOpenReview("No check-in in progress. Start one first.")
    if field not in FIELDS:
        raise JournalError(f"Unknown field '{field}'. Allowed: {FIELDS}")
    if answer not in (None, "true", "changed"):
        raise JournalError("answer must be \"true\", \"changed\" or null.")
    now = now or _now()
    answers = review.setdefault("answers", {})
    prior = answers.get(field) or {}
    rec = load_journal(r, deck) or {}
    if answer is None:
        if prior.get("answer") == "changed" and prior.get("prev"):
            rec = save_journal(r, deck, prior["prev"], now=now)
        answers.pop(field, None)
    elif answer == "true":
        answers[field] = {"answer": "true", "at": now}
    else:
        allowed = SECTIONS[field]
        if not allowed:
            raise JournalError(f"'{LABELS[field]}' can't be changed here.")
        if not isinstance(journal_payload, Mapping) or not journal_payload:
            raise JournalError("A change needs the edited journal section.")
        extra = sorted(set(journal_payload) - set(allowed))
        if extra:
            raise JournalError(f"'{LABELS[field]}' may only change {list(allowed)}, not {extra}.")
        caps = effective_caps(r, rec)
        old_value = _value(field, public(rec), dict(review.get("ctx") or {}), caps)["value"]
        prev = prior.get("prev") if prior.get("answer") == "changed" else {k: copy.deepcopy(rec.get(k))
                                                                           for k in allowed}
        was = prior.get("was") if prior.get("answer") == "changed" else "was " + old_value
        rec = save_journal(r, deck, journal_payload, now=now)
        answers[field] = {"answer": "changed", "was": was, "prev": prev, "at": now}
    _save_open(r, deck, review)
    return review_view(r, deck, review, rec or load_journal(r, deck), now)


def finish_review(r, deck: Mapping, skipped: bool = False, now: Optional[str] = None) -> dict:
    review = load_open(r, deck)
    if not review:
        raise NoOpenReview("No check-in in progress.")
    now = now or _now()
    snap = snapshot(deck, review.get("ctx") or {}, at=now)
    since = review.get("since") or {}
    record = {
        "id": review["id"], "started_at": review.get("started_at"), "finished_at": now, "skipped": bool(skipped),
        "since": since.get("at"), "snapshot": snap, "deltas": deltas(since.get("snapshot"), snap),
        "answers": {f: {k: v for k, v in a.items() if k != "prev"} for f, a in (review.get("answers") or {}).items()},
    }
    key = REVIEWS_PREFIX + _registry_id(deck)
    r.lpush(key, json.dumps(record))
    r.ltrim(key, 0, HISTORY_MAX - 1)
    r.delete(_open_key(deck))
    return record


# ---------------------------------------------------------------------------
# Live metrics (I/O): power estimate, prices, on-plan, pillars, weakest pet
# ---------------------------------------------------------------------------


def collect_context(r, deck: Mapping, rec: Optional[Mapping] = None) -> dict:
    """Never raises: a missing input just leaves its metric None."""
    ctx: Dict[str, Any] = {"power": None, "price": None, "on_plan": None, "prices": {}, "pillars": [],
                           "proposal": None, "pet_notice": None, "unpriced": None}
    try:
        from deck_review import review_deck

        ctx["power"] = review_deck(deck=dict(deck)).playfeel_summary.power_level_estimate
    except Exception as exc:  # noqa: BLE001
        log.warning("power estimate failed: %s", exc)
    try:
        from . import prices as prices_mod

        ps = prices_mod.card_prices(r, deck_entries(deck))
        tot = prices_mod.deck_total(deck, ps)
        ctx.update(prices=ps, price=tot["price"], unpriced=tot["unpriced"])
    except Exception as exc:  # noqa: BLE001
        log.warning("prices failed: %s", exc)
    try:
        from . import insights

        ident = insights.identity_view(r, dict(deck), include_cards=False)
        ctx["on_plan"] = (ident.get("fit") or {}).get("on_plan_pct")
        ctx["pillars"] = [{"name": p.get("name"), "state": p.get("state")} for p in ident.get("pillars") or []]
        ctx["proposal"] = ident.get("proposal")
    except Exception as exc:  # noqa: BLE001
        log.warning("identity failed: %s", exc)
    try:
        if (rec or {}).get("pet_cards"):
            from . import insights
            from .interactions import service as interactions_service

            graph = interactions_service.get_deck_graph(r, dict(deck))
            weak = insights.deck_weak_performers(r, dict(deck), graph)
            notice = weak.get("pet_notice")
            if notice:
                row = next((c for c in weak.get("cards") or [] if c.get("name") == notice["name"]), {})
                ctx["pet_notice"] = {"name": notice["name"], "weak_score": notice.get("weak_score"),
                                     "edges": row.get("edges")}
    except Exception as exc:  # noqa: BLE001
        log.warning("weak performers failed: %s", exc)
    return ctx


def journal_view(r, deck: Mapping) -> dict:
    rec = load_journal(r, deck)
    meta = (rec or {}).get("_journal") or {}
    pub = public(rec)
    last = next(iter(list_reviews(r, deck, 1)), None)
    return {"exists": rec is not None, "journal": pub,
            "meta": {"created_at": meta.get("created_at"), "updated_at": meta.get("updated_at")},
            "caps": effective_caps(r, pub), "last_review": last and {"id": last["id"],
                                                                     "finished_at": last.get("finished_at"),
                                                                     "skipped": last.get("skipped")},
            "open_review": load_open(r, deck) is not None}
