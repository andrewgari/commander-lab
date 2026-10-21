"""Oracle diagnosis of the latest sim run (stored as `report["diagnosis"]` in the saved sim doc).

Two layers:
  1. `build_findings` -- deterministic, pure. Every finding carries `evidence` (metric, value, n, source).
     Sim-backed findings cite telemetry; pillar findings cite the decklist/review and are labelled
     `sim_validated: False`. Missing / zero-sample telemetry is reported as "unavailable", never as 0%.
  2. `diagnose` -- optional Oracle prose summary over those findings only. Invented numbers are stripped;
     on any Oracle failure the deterministic summary is used. Findings are never model-written.

Sims are mechanical diagnostics: Forge bots differ from humans, so win rate is context only and never
drives a finding, and politics are not assessed.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Mapping, Optional

log = logging.getLogger("commander_lab.sim_diagnosis")

SCHEMA_VERSION = "1"
MIN_GAMES = 10          # below this: no claims at all
MIN_CARD_DRAWS = 5      # per-card observations needed for a straggler claim
STRAGGLER_MAX_CAST = 0.30
LAND_MISS_WARN = 0.25
LAND_TURNS = (2, 3, 4)
PILLARS = (("ramp", "Ramp"), ("card_advantage", "Card draw"), ("targeted_disruption", "Removal / interaction"),
           ("mass_disruption", "Mass interaction"), ("plan", "Win conditions"))
SUMMARY_MAX = 900

SYSTEM_PROMPT = (
    "Commander Lab request. Single turn, do not call tools. You summarise a Magic Commander deck simulation "
    "diagnosis in 2-4 sentences. Use ONLY the findings provided; do not introduce any number that is not in them. "
    "Simulations are mechanical diagnostics (Forge bots are not humans): do not lean on win rate, do not discuss "
    "politics, and make no strong claim about anything marked insufficient or unavailable. "
    'Reply with exactly one fenced ```json block: {"summary": "..."}.'
)


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.0f}%" if isinstance(x, (int, float)) else "n/a"


def _find_composition_rows(obj: Any) -> List[dict]:
    if isinstance(obj, Mapping):
        rows = obj.get("rows")
        if isinstance(rows, list) and rows and all(isinstance(r, Mapping) and "key" in r for r in rows) and any("actual" in r for r in rows):
            return [dict(r) for r in rows]
        for v in obj.values():
            got = _find_composition_rows(v)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_composition_rows(v)
            if got:
                return got
    return []


def _f(fid: str, category: str, claim: str, metric: str, value: Any, n: Optional[int], source: str,
       status: str = "supported") -> dict:
    return {"id": fid, "category": category, "claim": claim, "status": status, "sim_validated": source == "telemetry",
            "evidence": {"source": source, "metric": metric, "value": value, "n": n}}


def _land_findings(telemetry: Mapping, tested: str) -> List[dict]:
    out: List[dict] = []
    obs: Dict[int, List[bool]] = {t: [] for t in LAND_TURNS}
    for g in telemetry.get("games") or []:
        curve = (g.get("curves") or {}).get(tested)
        if not isinstance(curve, list):
            continue
        by_turn = {c.get("turn"): c for c in curve if isinstance(c, Mapping)}
        for t in LAND_TURNS:
            c = by_turn.get(t)
            if c is not None and "land_played" in c:
                obs[t].append(bool(c.get("land_played")))
    for t in LAND_TURNS:
        n = len(obs[t])
        if n < MIN_GAMES:
            out.append(_f(f"land_t{t}", "unavailable", f"Turn-{t} land drops cannot be assessed: {n} games with per-turn data, {MIN_GAMES} needed.",
                          f"land_drop_missed_t{t}", None, n, "telemetry", "insufficient_sample" if n else "unavailable"))
            continue
        miss = sum(1 for x in obs[t] if not x) / n
        cat = "not_working" if miss >= LAND_MISS_WARN else "working"
        verb = "missed" if miss >= LAND_MISS_WARN else "made"
        shown = miss if miss >= LAND_MISS_WARN else 1 - miss
        out.append(_f(f"land_t{t}", cat, f"{'Missed' if verb == 'missed' else 'Made'} the turn-{t} land drop in {_pct(shown)} of {n} games.",
                      f"land_drop_missed_t{t}", round(miss, 4), n, "telemetry"))
    return out


def _card_findings(telemetry: Mapping, tested: str) -> List[dict]:
    cards = ((telemetry.get("aggregate") or {}).get("cards") or {}).get(tested) or {}
    out: List[dict] = []
    observed = 0
    for name, c in sorted(cards.items()):
        cast = c.get("cast") or {}
        n = cast.get("n") or 0
        if n < MIN_CARD_DRAWS:
            continue
        observed += 1
        rate = (cast.get("count") or 0) / n
        if rate <= STRAGGLER_MAX_CAST:
            out.append(_f(f"straggler:{name}", "straggler",
                          f"{name} was cast in only {_pct(rate)} of {n} observed games.", "cast_rate", round(rate, 4), n, "telemetry"))
    if not cards or not observed:
        out.append(_f("stragglers_unavailable", "unavailable",
                      f"Card-level stragglers cannot be assessed: no card has {MIN_CARD_DRAWS}+ observations in this run (the daemon does not emit per-card data yet).",
                      "card_observations", 0, 0, "telemetry", "unavailable"))
    return out


def _pillar_findings(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    by_key = {r.get("key"): r for r in rows}
    for key, label in PILLARS:
        r = by_key.get(key)
        if not r or not isinstance(r.get("actual"), (int, float)) or not isinstance(r.get("target"), (int, float)):
            out.append(_f(f"pillar:{key}", "unavailable", f"{label}: no composition data in the saved review.", f"{key}_count", None, None, "review", "unavailable"))
            continue
        a, t = r["actual"], r["target"]
        short = a < t
        out.append(_f(f"pillar:{key}", "missing_pillar" if short else "working",
                      f"{label}: {a} cards vs a template target of {t} (deck composition, not sim-validated).",
                      f"{key}_count", a, a, "review"))
    return out


def build_findings(telemetry: Optional[Mapping], deck: Mapping, review: Optional[Mapping]) -> dict:
    tel = telemetry if isinstance(telemetry, Mapping) else {}
    agg = tel.get("aggregate") or {}
    n = int(agg.get("games") or 0)
    tested = str(tel.get("deck_id") or "")
    findings: List[dict] = []
    limitations = ["Forge bots differ from human players; read this as a mechanical diagnostic, not a verdict on the deck.",
                   "Politics and table dynamics are not assessed."]
    if n < MIN_GAMES:
        status = "insufficient_sample"
        limitations.insert(0, f"Only {n} completed games; at least {MIN_GAMES} are needed before drawing conclusions. Re-run with more games.")
        findings.append(_f("sample", "unavailable", f"{n} games is below the {MIN_GAMES}-game minimum; no conclusions drawn.", "games", n, n, "telemetry", "insufficient_sample"))
    else:
        status = "ready"
        gl = agg.get("game_length") or {}
        if gl.get("mean") is not None:
            findings.append(_f("game_length", "context", f"Games lasted {gl['mean']} turns on average (median {gl.get('median')}) over {gl.get('n')} games.",
                               "game_length_mean", gl["mean"], gl.get("n"), "telemetry"))
        wr = agg.get("win_rate") or {}
        if wr.get("value") is not None:
            findings.append(_f("win_rate", "context", f"Won {_pct(wr['value'])} of {wr.get('n')} games against Forge bots (context only; not used for cuts or adds).",
                               "win_rate", wr["value"], wr.get("n"), "telemetry"))
        findings += _land_findings(tel, tested)
        findings += _card_findings(tel, tested)
    findings += _pillar_findings(_find_composition_rows(review) if review else [])

    sim_ok = status == "ready"
    cuts = [{"card": f["id"].split(":", 1)[1], "reason": f["claim"], "finding": f["id"]}
            for f in findings if f["category"] == "straggler" and sim_ok]
    adds = [{"pillar": f["id"].split(":", 1)[1], "reason": f["claim"], "finding": f["id"]}
            for f in findings if f["category"] == "missing_pillar"]
    if not any(f["sim_validated"] and f["status"] == "supported" for f in findings):
        limitations.append("No sim-validated findings in this run; pillar notes come from the decklist/review only.")
    return {"status": status, "sample_games": n, "min_games": MIN_GAMES, "findings": findings,
            "suggested_cuts": cuts, "suggested_adds": adds, "limitations": limitations}


def _fallback_summary(d: Mapping) -> str:
    if d["status"] == "insufficient_sample":
        return (f"This run has {d['sample_games']} completed games, below the {d['min_games']}-game minimum, "
                "so no conclusions are drawn. Re-run with more games.")
    cats: Dict[str, int] = {}
    for f in d["findings"]:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    return (f"Over {d['sample_games']} games: {cats.get('working', 0)} things working, {cats.get('not_working', 0)} not working, "
            f"{cats.get('missing_pillar', 0)} pillars under template, {cats.get('straggler', 0)} stragglers. "
            "See the cited findings below; this is a mechanical read, not a verdict.")[:SUMMARY_MAX]


def _oracle_summary(d: Mapping, oracle_factory: Callable[[], Any]) -> str:
    from .review_doc import ReviewParseError, allowed_numbers, parse_reply, strip_invented_numbers

    client = oracle_factory()
    payload = {"status": d["status"], "sample_games": d["sample_games"], "findings": d["findings"],
               "limitations": d["limitations"]}
    reply = client.chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "```json\n" + json.dumps(payload, indent=1, default=str) + "\n```"},
    ], tools=[], tool_choice="none")
    text = parse_reply(reply).get("summary")
    if not isinstance(text, str) or not text.strip():
        raise ReviewParseError("summary missing")
    clean, _ = strip_invented_numbers(text.strip()[:SUMMARY_MAX], allowed_numbers(payload), [])
    if not clean.strip():
        raise ReviewParseError("summary held only invented numbers")
    return clean.strip()


def diagnose(telemetry: Optional[Mapping], deck: Mapping, review: Optional[Mapping],
             oracle_factory: Optional[Callable[[], Any]] = None, now_iso: str = "", run_id: Optional[str] = None) -> dict:
    d = build_findings(telemetry, deck, review)
    source, note = "deterministic", ""
    summary = _fallback_summary(d)
    if d["status"] == "ready":
        try:
            if oracle_factory is None:
                from .oracle_client import OracleClient

                oracle_factory = OracleClient.from_env
            summary, source = _oracle_summary(d, oracle_factory), "oracle"
        except Exception as exc:  # noqa: BLE001 - diagnosis must never break a run
            note = f"{type(exc).__name__}: {str(exc)[:200]}"
    return {"schema_version": SCHEMA_VERSION, "generated_at": now_iso, "run_id": run_id, "summary": summary,
            "summary_source": source, "note": note, **d}
