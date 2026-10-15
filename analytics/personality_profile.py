"""
Player profile from every deck's personality scores (PS-3).

    build_player_profile(all_scores, *, failed=(), scope="all", decks_total=None) -> PlayerProfile

Pure and deterministic: no Redis, no clock, no LLM. The same stored scores
always give the same profile, whatever order they come in. Rules (owned
here, see docs/PERSONALITY_SPEC.md section 10):

* average: per axis, the mean of the decks' FINAL scores, rounded half up.
* dominant / headline_kind from the averages, high to low (ties break in
  axis order): "even" when top - third < 3 (dominant = "balanced"),
  "close" when top - second <= 5 (dominant_runner_up = the second axis),
  otherwise "clear". No decks: "none".
* low_data: fewer than 3 scored decks. The averages are still returned,
  the headline says "Early read", and there are no trend callouts.
* trends (at most 8, never two naming the same deck + axis), in this order:
  most_common_lean, average_vs_count, none (axes no deck leans) or
  rarest_lean, balanced_count, outlier (up to 2), group (tribal /
  universe decks), spread, journal_coverage.

`all_scores` entries: {id, name, commanders, commander_uids, status,
scores: PersonalityScores | dict, computed_at?}. `failed` entries:
{id, name, reason, status?}; they're listed but never averaged.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, Field

AXES = ("timmy", "johnny", "spike", "vorthos", "melvin")
LOW_DATA_UNDER = 3
MAX_TRENDS = 8
EVEN_TOP_MINUS_THIRD = 3
CLOSE_TOP_MINUS_SECOND = 5
OUTLIER_GAP = 30          # deck score at least this far above the axis average
OUTLIER_MIN_SCORE = 70
MAX_OUTLIERS = 2
SPREAD_MIN_RANGE = 40
GROUP_MIN_DECKS = 2
GROUP_MIN_LIFT = 10       # group average this far above the rest
TRIBAL_TYPE_SHARE = 0.50  # top creature subtype / creatures (V1 type part)
UNIVERSE_SET_SHARE = 0.60  # top set / non-ramp nonland (V1 set part)
NAME_MAX = 34

V1_RE = re.compile(r"type (?P<sub>.+?) (?P<sn>\d+)/(?P<sd>\d+); set (?P<set>.+?) (?P<tn>\d+)/(?P<td>\d+)$")

Axis = Literal["timmy", "johnny", "spike", "vorthos", "melvin"]


class TrendCallout(BaseModel):
    kind: str
    number: Union[int, str]
    of: Optional[int] = None
    unit: Optional[str] = None
    text: str                          # the sentence after the number; <b> only around axis/deck names
    axis: Optional[str] = None
    deck_id: Optional[str] = None
    deck_ids: List[str] = Field(default_factory=list)  # every deck this callout names
    group: Optional[str] = None
    action: Optional[Dict[str, str]] = None  # {type: filter|sort|open, value}


class ProfileDeck(BaseModel):
    id: str
    name: str
    commanders: List[str] = Field(default_factory=list)
    commander_uids: List[str] = Field(default_factory=list)
    status: Optional[str] = None
    scores: Dict[str, int]
    leans_toward: str
    runner_up: Optional[str] = None
    journal_used: bool = False
    notes: List[str] = Field(default_factory=list)
    groups: List[str] = Field(default_factory=list)
    computed_at: Optional[str] = None


class FailedDeck(BaseModel):
    id: str
    name: Optional[str] = None
    reason: str = ""
    status: Optional[str] = None


class PlayerProfile(BaseModel):
    scope: str
    decks_scored: int
    decks_total: int
    low_data: bool
    decks_to_unlock_trends: int
    average: Optional[Dict[str, int]] = None
    dominant: Optional[str] = None             # axis or "balanced"; None with no decks
    dominant_runner_up: Optional[str] = None
    headline_kind: Literal["clear", "close", "even", "none"]
    headline: str
    why: str
    lean_counts: Dict[str, int]
    journal_count: int
    trends: List[TrendCallout] = Field(default_factory=list)
    decks: List[ProfileDeck] = Field(default_factory=list)
    failed: List[FailedDeck] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _round(x: float) -> int:
    return int(math.floor(x + 0.5))


def _title(axis: str) -> str:
    return axis.capitalize()


def _b(axis: str) -> str:
    return f"<b>{_title(axis)}</b>"


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _deck_name(name: str) -> str:
    """Quoted, word-boundary truncated at 34 chars, HTML-escaped."""
    name = name or ""
    if len(name) > NAME_MAX:
        cut = name[:NAME_MAX].rsplit(" ", 1)[0] or name[:NAME_MAX]
        name = cut.rstrip(" ,.;:") + "…"
    return f"<b>“{_esc(name)}”</b>"


def _join(words: List[str], last: str = "and") -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + f" {last} " + words[-1]


def _as_dict(scores: Any) -> dict:
    if hasattr(scores, "model_dump"):
        return scores.model_dump()
    return dict(scores or {})


def _groups(sc: Mapping) -> List[str]:
    """Tribal / universe from the V1 evidence value ("type Vampire 25/31; set C17 9/40")."""
    ev = next((e for e in (sc.get("vorthos") or {}).get("evidence") or [] if e.get("signal") == "V1"), None)
    m = V1_RE.match(str((ev or {}).get("value") or ""))
    if not m:
        return []
    out = []
    sn, sd, tn, td = (int(m.group(k)) for k in ("sn", "sd", "tn", "td"))
    if m.group("sub") != "None" and sd and sn / sd >= TRIBAL_TYPE_SHARE:
        out.append("tribal")
    if m.group("set") != "None" and td and tn / td >= UNIVERSE_SET_SHARE:
        out.append("universe")
    return out


def _rank(avg: Mapping[str, float]) -> List[str]:
    return sorted(AXES, key=lambda a: (-avg[a], AXES.index(a)))


def _in_scope(status: Optional[str], scope: str) -> bool:
    return scope != "physical" or status == "physical"


# ---------------------------------------------------------------------------
# headline
# ---------------------------------------------------------------------------


def _headline(avg: Optional[Dict[str, int]], low: bool):
    if not avg:
        return None, None, "none", "No decks scored yet."
    rank = _rank(avg)
    s = [avg[a] for a in rank]
    if s[0] - s[2] < EVEN_TOP_MINUS_THIRD:
        dominant, runner, kind = "balanced", None, "even"
        who = "<b>a bit of everything</b>"
    elif s[0] - s[1] <= CLOSE_TOP_MINUS_SECOND:
        dominant, runner, kind = rank[0], rank[1], "close"
        who = _b(rank[0])
    else:
        dominant, runner, kind = rank[0], None, "clear"
        who = _b(rank[0])
    if low:
        text = f"Early read: {who}, so far."
    elif kind == "even":
        text = f"You play {who}."
    elif kind == "close":
        text = f"You are mostly {who}, with a lot of {_title(runner)}."
    else:
        text = f"You are mostly {who}."
    return dominant, runner, kind, text


def _why(n: int, scope: str, counts: Dict[str, int], low: bool) -> str:
    if n == 0:
        return "Sync your decks and they'll be scored in the background."
    where = "physical " if scope == "physical" else ""
    deck_s = "deck" if n == 1 else "decks"
    head = f"That's your average over {n} {where}{deck_s}."
    if low:
        need = LOW_DATA_UNDER - n
        return f"{head} Trends need at least {LOW_DATA_UNDER} scored decks: {need} more to go."
    by = sorted(AXES, key=lambda a: (-counts[a], AXES.index(a)))
    top = counts[by[0]]
    if top == 0:
        return f"{head} Every deck is balanced: no axis stands out."
    tied = [a for a in by if counts[a] == top]
    rest = [a for a in by if 0 < counts[a] < top][:2]
    if len(tied) > 1:
        s = f"{head} By count, {_join([_title(a) for a in tied])} tie: {top} decks lean each way"
    else:
        s = f"{head} By count, {_title(tied[0])} wins: {top} {'deck leans' if top == 1 else 'decks lean'} that way"
    if rest:
        s += ", then " + _join([f"{_title(a)} ({counts[a]})" for a in rest])
    s += "."
    if counts["balanced"]:
        s += f" {counts['balanced']} {'is' if counts['balanced'] == 1 else 'are'} balanced."
    return s


# ---------------------------------------------------------------------------
# trends
# ---------------------------------------------------------------------------


def _trends(decks: List[ProfileDeck], avg_exact: Dict[str, float], avg: Dict[str, int],
            counts: Dict[str, int], journal_count: int) -> List[TrendCallout]:
    n = len(decks)
    out: List[TrendCallout] = []
    named: set = set()  # (deck_id, axis) pairs already named

    def add(t: TrendCallout) -> bool:
        pairs = {(d, t.axis) for d in t.deck_ids}
        if pairs & named:
            return False
        named.update(pairs)
        out.append(t)
        return True

    by_count = sorted(AXES, key=lambda a: (-counts[a], AXES.index(a)))
    top = counts[by_count[0]]

    # 1. most common lean
    if top > 0:
        tied = [a for a in by_count if counts[a] == top]
        if len(tied) == 1:
            text = f"{'deck leans' if top == 1 else 'decks lean'} {_b(tied[0])}, your most common lean."
        else:
            text = f"decks lean {_join([_b(a) for a in tied])} each, your most common leans."
        add(TrendCallout(kind="most_common_lean", number=top, of=n, text=text, axis=tied[0],
                         action={"type": "filter", "value": tied[0]}))

    # 2. highest average isn't the most common lean
    avg_top = _rank(avg_exact)[0]
    if top > 0 and counts[avg_top] < top and avg[avg_top] > avg[_rank(avg_exact)[1]] - 0.5:
        most = [a for a in by_count if counts[a] == top]
        add(TrendCallout(kind="average_vs_count", number=avg[avg_top], unit="avg", axis=avg_top,
                         text=(f"{_b(avg_top)} is your highest average, but fewer decks lean that way than "
                               f"{_join([_title(a) for a in most], 'or')}."),
                         action={"type": "sort", "value": avg_top}))

    # 3. axes no deck leans, else the rarest
    zero = [a for a in AXES if counts[a] == 0]
    if zero:
        add(TrendCallout(kind="none", number=0, of=n, axis=zero[0],
                         text=f"decks lean {_join([_b(a) for a in zero], 'or')}.",
                         action={"type": "sort", "value": zero[0]}))
    else:
        low = min(counts[a] for a in AXES)
        if low < top:
            rare = [a for a in AXES if counts[a] == low]
            text = (f"{'deck leans' if low == 1 else 'decks lean'} {_b(rare[0])}, the rarest lean in your collection."
                    if len(rare) == 1 else
                    f"{'deck leans' if low == 1 else 'decks lean'} each of {_join([_b(a) for a in rare])}, "
                    f"your rarest leans.")
            add(TrendCallout(kind="rarest_lean", number=low, of=n, axis=rare[0], text=text,
                             action={"type": "filter", "value": rare[0]}))

    # 4. balanced decks
    if counts["balanced"]:
        b = counts["balanced"]
        add(TrendCallout(kind="balanced_count", number=b, of=n,
                         text=f"{'deck is' if b == 1 else 'decks are'} balanced: no axis stands out.",
                         action={"type": "filter", "value": "balanced"}))

    # 5. outliers: a deck far above the collection on one axis
    cands = []
    for d in decks:
        for a in AXES:
            gap = d.scores[a] - avg_exact[a]
            if d.scores[a] >= OUTLIER_MIN_SCORE and gap >= OUTLIER_GAP:
                cands.append((-gap, AXES.index(a), d.name, d.id, a))
    cands.sort()
    used_decks: set = set()
    shown = 0
    for _, _, name, did, a in cands:
        if shown >= MAX_OUTLIERS or did in used_decks:
            continue
        score = next(x.scores[a] for x in decks if x.id == did)
        if add(TrendCallout(kind="outlier", number=score, unit=_title(a), axis=a, deck_id=did, deck_ids=[did],
                            text=(f"your most {_title(a)} deck is {_deck_name(name)}, "
                                  f"{score - avg[a]} points above your average."),
                            action={"type": "open", "value": did})):
            used_decks.add(did)
            shown += 1

    # 6. groups: "your Vorthos is highest in tribal decks"
    for g in ("tribal", "universe"):
        ins = [d for d in decks if g in d.groups]
        rest = [d for d in decks if g not in d.groups]
        if len(ins) < GROUP_MIN_DECKS or not rest:
            continue
        lift = []
        for a in AXES:
            gi = sum(d.scores[a] for d in ins) / len(ins)
            ro = sum(d.scores[a] for d in rest) / len(rest)
            lift.append((-(gi - ro), AXES.index(a), a, gi, ro))
        lift.sort()
        neg, _, a, gi, ro = lift[0]
        if -neg < GROUP_MIN_LIFT:
            continue
        label = "tribal" if g == "tribal" else "single-universe"
        add(TrendCallout(kind="group", number=_round(gi), of=len(ins), unit="avg", axis=a, group=g,
                         text=(f"avg {_b(a)} in your {len(ins)} {label} decks, vs {_round(ro)} in the rest: "
                               f"your {_title(a)} is highest there."),
                         action={"type": "filter", "value": g}))

    # 7. spread: the widest axis that isn't already named for its extreme decks
    spreads = []
    for a in AXES:
        lo = min(decks, key=lambda d: (d.scores[a], d.name, d.id))
        hi = max(decks, key=lambda d: (d.scores[a], [-ord(c) for c in d.name], d.id))
        rng = hi.scores[a] - lo.scores[a]
        if rng >= SPREAD_MIN_RANGE:
            spreads.append((-rng, AXES.index(a), a, lo, hi))
    spreads.sort(key=lambda t: (t[0], t[1]))
    for _, _, a, lo, hi in spreads:
        if add(TrendCallout(kind="spread", number=f"{lo.scores[a]}–{hi.scores[a]}", unit="range", axis=a,
                            deck_ids=[lo.id, hi.id],
                            text=(f"{_b(a)} is all or nothing: from {_deck_name(lo.name)} to "
                                  f"{_deck_name(hi.name)}."),
                            action={"type": "sort", "value": a})):
            break

    # 8. journal coverage
    if journal_count == n:
        text = "decks have a journal: every score includes your stated intent."
    else:
        text = ("decks have a journal. The rest are scored from cards alone; a journal can move each axis "
                "by up to 10 points.")
    add(TrendCallout(kind="journal_coverage", number=journal_count, of=n, text=text,
                     action={"type": "filter", "value": "no_journal"}))
    return out[:MAX_TRENDS]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_player_profile(all_scores: Iterable[Mapping], *, failed: Iterable[Mapping] = (), scope: str = "all",
                         decks_total: Optional[int] = None) -> PlayerProfile:
    decks: List[ProfileDeck] = []
    for e in all_scores:
        if not _in_scope(e.get("status"), scope):
            continue
        sc = _as_dict(e.get("scores"))
        decks.append(ProfileDeck(
            id=str(e.get("id")), name=e.get("name") or str(e.get("id")),
            commanders=list(e.get("commanders") or []), commander_uids=list(e.get("commander_uids") or []),
            status=e.get("status"), scores={a: int(sc[a]["score"]) for a in AXES},
            leans_toward=sc.get("leans_toward") or "balanced", runner_up=sc.get("runner_up"),
            journal_used=bool(sc.get("journal_used")), notes=list(sc.get("notes") or []),
            groups=_groups(sc), computed_at=e.get("computed_at")))
    decks.sort(key=lambda d: (d.name.lower(), d.id))
    fails = sorted((FailedDeck(id=str(f.get("id")), name=f.get("name"), reason=str(f.get("reason") or ""),
                               status=f.get("status"))
                    for f in failed if _in_scope(f.get("status"), scope)),
                   key=lambda f: ((f.name or "").lower(), f.id))
    n = len(decks)
    counts = {a: 0 for a in AXES}
    counts["balanced"] = 0
    for d in decks:
        counts[d.leans_toward if d.leans_toward in counts else "balanced"] += 1
    journal_count = sum(1 for d in decks if d.journal_used)
    low = n < LOW_DATA_UNDER
    if n:
        avg_exact = {a: sum(d.scores[a] for d in decks) / n for a in AXES}
        avg = {a: _round(avg_exact[a]) for a in AXES}
    else:
        avg_exact, avg = {}, None
    dominant, runner, kind, headline = _headline(avg, low)
    trends = [] if low else _trends(decks, avg_exact, avg, counts, journal_count)
    return PlayerProfile(
        scope=scope, decks_scored=n, decks_total=decks_total if decks_total is not None else n + len(fails),
        low_data=low, decks_to_unlock_trends=max(0, LOW_DATA_UNDER - n), average=avg, dominant=dominant,
        dominant_runner_up=runner, headline_kind=kind, headline=headline, why=_why(n, scope, counts, low),
        lean_counts=counts, journal_count=journal_count, trends=trends, decks=decks, failed=fails)
