"""SM-2: deterministic static-vs-simulation comparison. Pure; no Redis, no Oracle.

build_sim_comparison(deck, sim_report, review) -> {tempo_comparison, card_comparison, pod_breakdown}

`review` is either `review_metrics.compute_review(...)` output or a saved `review_doc` doc
(sections[].data); `sim_report` is the daemon report (a saved sim doc with a "report" key also works).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from . import review_metrics as rm

TURNS = (1, 2, 3, 4, 5, 6)
MIN_DRAWN = 5
STRAGGLER_MAX_U = 0.30
DEAD_DRAW_MAX_U = 0.25
WORKHORSE_MIN_U = 0.70
STARVE_NOTE_DELTA = 0.10


def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(float(x) + 0.0, nd)


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _key(name: Any) -> str:
    return str(name or "").split(" // ")[0].strip().casefold()


def normalize_review(review: Optional[Mapping]) -> dict:
    """Flat metrics dict from either compute_review output or a saved review doc."""
    review = review or {}
    if "sections" in review:
        return {s["type"]: s.get("data") for s in review["sections"] if isinstance(s, Mapping) and s.get("type")}
    return dict(review)


def _first(d: Mapping, *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


# ---------------------------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------------------------

def _land_rates(report: Mapping) -> Dict[int, Optional[float]]:
    src = _first(report, "land_drop_rates_by_turn") or _first(report.get("curve_tempo_metrics") or {}, "land_drop_rate_t1_t6",
                                                              "land_drop_rates_by_turn") or {}
    out: Dict[int, Optional[float]] = {}
    for t in TURNS:
        out[t] = _num(src.get(f"T{t}", src.get(str(t), src.get(t)))) if isinstance(src, Mapping) else None
    return out


def _sim_tempo(report: Mapping) -> dict:
    ct = report.get("curve_tempo_metrics") or {}

    def pick(spec_key: str, daemon_key: str) -> Optional[float]:
        v = report.get(spec_key)
        return _num(v if v is not None else ct.get(daemon_key))

    return {
        "land_drop_rates_by_turn": _land_rates(report),
        "t5_mana_starvation_rate": pick("t5_mana_starvation_rate", "mana_starvation_rate_t5"),
        "avg_first_commander_turn": pick("avg_first_commander_turn", "commander_first_cast_mean_turn"),
        "avg_mana_utilization_t2_t6": pick("avg_mana_utilization_t2_t6", "mana_utilization_ratio_t2_t6"),
    }


def _deck_counts(deck: Mapping, curve_tempo: Mapping) -> dict:
    mana = curve_tempo.get("mana") or {}
    lands = mana.get("lands")
    library = curve_tempo.get("library_size")
    if library is None:
        cmdrs = set(deck.get("commanders") or [])
        library = sum(q for n, q in rm._entries(deck).items() if n not in cmdrs)
    return {"lands": lands, "library": max(int(library or 0), 1)}


def _metrics_available(report: Mapping) -> bool:
    """False for quiet-mode (random pods / verbose_log off): tempo metrics were never recorded."""
    params = report.get("params") or {}
    if params.get("verbose_log") is False:
        return False
    mode = params.get("opponent_mode") or report.get("opponent_mode")
    return mode != "random"


# metric -> True when a LOWER sim value than the math is the good direction.
_LOWER_IS_BETTER = {"t5_mana_starvation": True, "commander_turn": True, "commander_on_curve": False, "mana_utilization": False}
_READ_TOL = 0.08


def _read(metric: str, sim_v, stat_v, tol: float = _READ_TOL) -> dict:
    """Polarity-aware read: {read: worse|better|in_line, read_label}. Lower-is-better metrics invert."""
    if sim_v is None or stat_v is None:
        return {"read": None, "read_label": None}
    diff = sim_v - stat_v
    if abs(diff) <= tol:
        return {"read": "in_line", "read_label": "In line"}
    lower_good = _LOWER_IS_BETTER.get(metric, False)
    below = diff < 0
    good = below if lower_good else not below
    if metric == "commander_turn":
        label = "Later than math" if not below else "Earlier than math"
    elif metric == "t5_mana_starvation":
        label = "Worse than math" if not below else "Better than math"
    else:
        label = "Above math" if not below else "Below math"
    return {"read": "better" if good else "worse", "read_label": label}


def tempo_comparison(deck: Mapping, report: Mapping, metrics: Mapping) -> dict:
    ct = metrics.get("curve_tempo") or {}
    sim = _sim_tempo(report)
    counts = _deck_counts(deck, ct)
    lands, total = counts["lands"], counts["library"]
    mana = ct.get("mana") or {}
    on_curve = ct.get("commander_on_curve") or {}
    cmdr_cmc = on_curve.get("cmc")

    per_turn = []
    for t in TURNS:
        seen = min(rm.cards_seen(t), total)
        static_p = _r(rm._hyper_cdf_ge(total, lands, seen, t)) if lands is not None else None
        exp_lands = _r(seen * lands / total, 2) if lands is not None else None
        s = sim["land_drop_rates_by_turn"][t]
        per_turn.append({"turn": t, "cards_seen": seen, "sim_land_drop_rate": _r(s), "static_land_drop_probability": static_p,
                         "static_expected_lands": exp_lands,
                         "delta": _r(s - static_p) if s is not None and static_p is not None else None})

    exp = {f"t{t}": next(p["static_expected_lands"] for p in per_turn if p["turn"] == t) for t in (4, 5)}
    static_starve = None
    if lands is not None:
        seen5 = min(rm.cards_seen(5), total)
        static_starve = _r(1 - rm._hyper_cdf_ge(total, lands, seen5, 4))  # <=3 lands by T5 (sim definition)
    spend = [p for p in mana.get("per_turn") or []]
    tot_mana = sum(p.get("mana", 0) for p in spend)
    static_util = _r(sum(p.get("spend", 0) for p in spend) / tot_mana) if tot_mana else None

    sim_cmdr, sim_util, sim_starve = sim["avg_first_commander_turn"], sim["avg_mana_utilization_t2_t6"], sim["t5_mana_starvation_rate"]
    notes: List[str] = []
    if sim_starve is not None and static_starve is not None and sim_starve - static_starve > STARVE_NOTE_DELTA:
        notes.append("Simulated T5 mana starvation is above the hypergeometric expectation.")
    if sim_cmdr is not None and cmdr_cmc and sim_cmdr > cmdr_cmc + 1:
        notes.append("Commander lands more than one turn later than its mana value in simulation.")
    if sim_util is not None and static_util is not None and sim_util < static_util - STARVE_NOTE_DELTA:
        notes.append("Simulated mana use T2-T6 is below the static spend model.")
    low_land = [p["turn"] for p in per_turn if p["delta"] is not None and p["delta"] < -STARVE_NOTE_DELTA]
    if low_land:
        notes.append("Simulated land drops lag the static probability on turn(s) " + ", ".join(f"T{t}" for t in low_land) + ".")

    return {
        "land_drops": per_turn,
        "static": {"lands": lands, "library_size": total, "expected_lands_t4": exp["t4"], "expected_lands_t5": exp["t5"],
                   "commander": on_curve.get("commander"), "commander_cmc": cmdr_cmc,
                   "p_commander_on_curve": on_curve.get("p_with_ramp"),
                   "t5_starvation_probability": static_starve, "mana_utilization_t2_t6": static_util,
                   "unspent_avg_t2_t6": mana.get("unspent_avg_t2_t6")},
        "sim": {"t5_mana_starvation_rate": _r(sim_starve), "avg_first_commander_turn": _r(sim_cmdr),
                "avg_mana_utilization_t2_t6": _r(sim_util)},
        "deltas": {"t5_mana_starvation": _r(sim_starve - static_starve) if sim_starve is not None and static_starve is not None else None,
                   "commander_turn_vs_cmc": _r(sim_cmdr - cmdr_cmc) if sim_cmdr is not None and cmdr_cmc else None,
                   "mana_utilization": _r(sim_util - static_util) if sim_util is not None and static_util is not None else None},
        "notes": notes,
        "reads": {
            "t5_mana_starvation": _read("t5_mana_starvation", sim_starve, static_starve),
            "commander_turn": _read("commander_turn", sim_cmdr, cmdr_cmc, 1.0),
            "mana_utilization": _read("mana_utilization", sim_util, static_util),
        },
        "metrics_available": _metrics_available(report),
    }


# ---------------------------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------------------------

def _diag_rows(report: Mapping) -> Dict[str, dict]:
    """name -> {utilization_rate, games_drawn, games_cast, avg_turn_cast, diagnostic_flag}.

    Accepts a list of rows, {"cards": [...]}, {name: row}, or the daemon's current shape
    (dead_draw_stragglers / top_performers with `utilization_ratio`; games_drawn is then estimated
    from expected_draws_per_card and flagged `games_drawn_estimated`).
    """
    cd = report.get("card_diagnostics") or {}
    rows: List[dict] = []
    est = None
    if isinstance(cd, list):
        rows = [x for x in cd if isinstance(x, Mapping)]
    elif isinstance(cd, Mapping):
        if isinstance(cd.get("cards"), list):
            rows = [x for x in cd["cards"] if isinstance(x, Mapping)]
        elif isinstance(cd.get("cards"), Mapping):
            rows = [{"card": k, **v} for k, v in cd["cards"].items() if isinstance(v, Mapping)]
        elif any(isinstance(v, Mapping) and ("utilization_rate" in v or "games_drawn" in v) for v in cd.values()):
            rows = [{"card": k, **v} for k, v in cd.items() if isinstance(v, Mapping)]
        else:
            est = _num(cd.get("expected_draws_per_card"))
            for lst in ("dead_draw_stragglers", "top_performers"):
                rows += [x for x in cd.get(lst) or [] if isinstance(x, Mapping)]
    out: Dict[str, dict] = {}
    for x in rows:
        name = x.get("card") or x.get("name")
        if not name:
            continue
        u = _num(_first(x, "utilization_rate", "utilization_ratio"))
        drawn = _num(x.get("games_drawn"))
        estimated = False
        if drawn is None and est is not None:
            drawn, estimated = float(round(est)), True
        out[_key(name)] = {"name": name, "utilization_rate": _r(u), "games_drawn": None if drawn is None else int(drawn),
                           "games_cast": x.get("games_cast"), "avg_turn_cast": _r(_num(x.get("avg_turn_cast"))),
                           "diagnostic_flag": x.get("diagnostic_flag"), "games_drawn_estimated": estimated}
    return out


def card_comparison(deck: Mapping, report: Mapping, metrics: Mapping) -> dict:
    in_deck = {_key(n) for n in rm._entries(deck)}
    cmdrs = {_key(n) for n in deck.get("commanders") or []}
    strag = metrics.get("stragglers") or {}
    strong = metrics.get("strong_cards") or {}
    strag_names = {_key(c.get("name")) for c in strag.get("cards") or []}
    strong_names = {_key(c.get("name")) for c in strong.get("cards") or []}
    pets = {_key(c.get("name")) for c in strag.get("protected_pets") or []} | {_key(p) for p in (metrics.get("inputs") or {}).get("pets") or []}
    strag_score = {_key(c.get("name")): c.get("straggler_score") for c in strag.get("cards") or []}

    confirmed, surprise, workhorses, unmatched = [], [], [], []
    for k, d in sorted(_diag_rows(report).items()):
        if k in cmdrs:
            continue
        if k not in in_deck:
            unmatched.append(d["name"])
            continue
        u, drawn = d["utilization_rate"], d["games_drawn"]
        if u is None or drawn is None or drawn < MIN_DRAWN:
            continue
        row = {**d, "static_straggler": k in strag_names, "static_strong": k in strong_names, "pet": k in pets}
        if k in strag_names and u <= STRAGGLER_MAX_U:
            confirmed.append({**row, "straggler_score": strag_score.get(k)})
        elif k not in strag_names and u <= DEAD_DRAW_MAX_U:
            surprise.append(row)
        if u >= WORKHORSE_MIN_U:
            workhorses.append(row)
    confirmed.sort(key=lambda x: (x["utilization_rate"], x["name"]))
    surprise.sort(key=lambda x: (x["utilization_rate"], x["name"]))
    workhorses.sort(key=lambda x: (-x["utilization_rate"], x["name"]))
    return {
        "thresholds": {"min_games_drawn": MIN_DRAWN, "confirmed_straggler_max_u": STRAGGLER_MAX_U,
                       "surprise_dead_draw_max_u": DEAD_DRAW_MAX_U, "workhorse_min_u": WORKHORSE_MIN_U},
        "confirmed_stragglers": confirmed,
        "surprise_dead_draws": surprise,
        "confirmed_workhorses": workhorses,
        "static_stragglers_not_confirmed": sorted(n for n in (c.get("name") for c in strag.get("cards") or [])
                                                  if _key(n) not in {_key(c["name"]) for c in confirmed}),
        "unmatched_cards": sorted(unmatched),
    }


# ---------------------------------------------------------------------------------------------
# Pod
# ---------------------------------------------------------------------------------------------

def _rate(wins: int, games: int) -> Optional[float]:
    return _r(wins / games) if games else None


def wilson_ci95(wins: int, games: int) -> Optional[List[float]]:
    """95% Wilson score interval for a win rate, as [lo, hi] fractions (None when no games)."""
    if not games:
        return None
    z = 1.96
    p = wins / games
    denom = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / denom
    half = z * ((p * (1 - p) / games + z * z / (4 * games * games)) ** 0.5) / denom
    return [_r(max(0.0, centre - half)), _r(min(1.0, centre + half))]


def pod_breakdown(report: Mapping) -> dict:
    log = [g for g in report.get("games_log") or [] if isinstance(g, Mapping)]
    games, by_deck, by_ai, reasons = [], {}, {}, {}
    wins = 0
    for g in log:
        won = bool(g.get("user_won"))
        wins += won
        opps = [{"seat": o.get("seat"), "deck": o.get("deck"), "ai": o.get("ai"), "owned": str(o.get("deck") or "").startswith("user_")} for o in g.get("opponents") or []]
        games.append({"game": g.get("game"), "opponents": opps, "winner": g.get("winner"), "user_won": won,
                      "total_turns": g.get("total_turns"), "win_reason": g.get("win_reason")})
        if g.get("win_reason"):
            reasons[g["win_reason"]] = reasons.get(g["win_reason"], 0) + 1
        for bucket, field in ((by_deck, "deck"), (by_ai, "ai")):
            for v in {o.get(field) for o in opps if o.get(field)}:
                b = bucket.setdefault(v, {"games": 0, "wins": 0})
                b["games"] += 1
                b["wins"] += won
    for bucket in (by_deck, by_ai):
        for b in bucket.values():
            b["win_rate"] = _rate(b["wins"], b["games"])
    turns = [g["total_turns"] for g in games if isinstance(g.get("total_turns"), (int, float)) and g["total_turns"] > 0]
    return {
        "opponent_mode": (report.get("params") or {}).get("opponent_mode") or report.get("opponent_mode"),
        "games_played": len(games), "user_wins": wins, "win_rate": _rate(wins, len(games)),
        "win_rate_ci95": wilson_ci95(wins, len(games)),
        "avg_total_turns": _r(sum(turns) / len(turns), 2) if turns else None,
        "win_reasons": {k: reasons[k] for k in sorted(reasons)},
        "by_opponent_deck": {k: by_deck[k] for k in sorted(by_deck)},
        "by_opponent_ai": {k: by_ai[k] for k in sorted(by_ai)},
        "games": games,
    }


def build_sim_comparison(deck: Mapping, sim_report: Mapping, review_metrics_or_doc: Optional[Mapping]) -> dict:
    report = sim_report.get("report") if isinstance(sim_report.get("report"), Mapping) else sim_report
    metrics = normalize_review(review_metrics_or_doc)
    return {
        "deck": {"name": deck.get("name"), "commanders": list(deck.get("commanders") or [])},
        "fingerprint": rm.fingerprint(deck),
        "tempo_comparison": tempo_comparison(deck, report, metrics),
        "card_comparison": card_comparison(deck, report, metrics),
        "pod_breakdown": pod_breakdown(report),
    }
