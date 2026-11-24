"""Reviewer panel for Brew's judgment stages (core construction, deck construction, final review).

Independent reviewers, each with ONE lens (power/consistency, synergy/flavor, mana base, playstyle fit),
vote on a stage's proposed cards. Cost is bounded by design:

  * Reviewers run in parallel, one chat call each (LLM task "brew_panel").
  * A critic (devil's advocate) is called AT MOST ONCE per stage and only for CHALLENGED picks:
    contested (reviewers split) or high-impact (Game Changers, fast mana, tutors, pricey) picks that at
    least one reviewer cut. Unanimous cuts and uncontested keeps never reach the critic.
  * Reconciliation is deterministic (no further LLM call): see `reconcile`.
  * Budgets (`PanelBudget`, env BREW_PANEL_BUDGET as JSON): max_tokens per call, one retry with a larger
    budget when a reply is truncated (#526), max calls and max critic picks per stage.

The panel can never break a build: a reviewer that is unavailable, truncated twice, unparseable or over
budget is recorded and ignored; with no usable reviewer every proposal stands (`status: unavailable`).
Cuts only ever remove proposed cards and suggestions are only ever taken from the offered candidates, so
the panel cannot introduce an unknown card; the builder still admits every addition through its own gate.

The outcome is recorded on `session["stage_reviews"][stage]` (JSON-safe) for the UI.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from src.server.domain.llm.port import LLMError, LLMTruncatedError

log = logging.getLogger(__name__)

TASK = "brew_panel"
LENSES = ("power_consistency", "synergy_flavor", "mana_base", "playstyle_fit")
STAGE_LENSES: Dict[str, Sequence[str]] = {
    "core": ("power_consistency", "mana_base"),
    "deck": LENSES,
    "review": LENSES,
}
HIGH_IMPACT_PRICE = 25.0


@dataclass(frozen=True)
class PanelBudget:
    max_tokens: int = 2500          # per reviewer / critic call
    retry_max_tokens: int = 8000    # one retry after a truncated reply (#526)
    max_calls: int = 6              # per stage, retries included
    max_critic_picks: int = 12      # challenged picks sent to the critic
    max_proposals: int = 60         # proposals shown to reviewers (keeps prompts bounded)

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "PanelBudget":
        env = os.environ if env is None else env
        try:
            raw = json.loads(env.get("BREW_PANEL_BUDGET") or "{}")
        except ValueError:
            raw = {}
        out = {}
        for f in ("max_tokens", "retry_max_tokens", "max_calls", "max_critic_picks", "max_proposals"):
            v = raw.get(f) if isinstance(raw, dict) else None
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                out[f] = v
        return cls(**out)


_client_factory: Optional[Callable[[str], Any]] = None


def set_client_factory(factory: Optional[Callable[[str], Any]]) -> None:
    """Chat-client factory (task -> client), wired by the app layer at startup (as brew_review does)."""
    global _client_factory
    _client_factory = factory


def _default_client() -> Any:
    if _client_factory is None:
        raise LLMError("no chat client factory configured")
    return _client_factory(TASK)


@dataclass
class PanelOutcome:
    cuts: Set[str] = field(default_factory=set)          # names (as proposed) to remove
    adds: List[str] = field(default_factory=list)        # candidate names to try, best first
    record: dict = field(default_factory=dict)           # JSON-safe, persisted on the session


class _Calls:
    def __init__(self, client: Any, budget: PanelBudget):
        self.client, self.budget, self.n = client, budget, 0
        self._lock = threading.Lock()

    def _take(self) -> bool:
        with self._lock:
            if self.n >= self.budget.max_calls:
                return False
            self.n += 1
            return True

    def chat(self, messages: list) -> str:
        if not self._take():
            raise LLMError("panel call budget exhausted")
        try:
            return self.client.chat(messages, max_tokens=self.budget.max_tokens)
        except LLMTruncatedError:
            if not self._take():
                raise
            return self.client.chat(messages, max_tokens=self.budget.retry_max_tokens)


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _norm(n: Any) -> str:
    return str(n or "").split(" // ")[0].strip().casefold()


def _prompt(role: str, ctx: dict, proposals: Sequence[str], candidates: Sequence[str],
            challenged: Optional[Dict[str, List[str]]] = None) -> list:
    from src.server.domain.knowledge import build_messages
    lines = [f"ROLE: {'critic' if role == 'critic' else 'reviewer, lens = ' + role}",
             f"Commander(s): {', '.join(ctx.get('commanders') or [])}",
             f"Target power (1-10): {ctx.get('target_power')}",
             f"Deck idea: {ctx.get('deck_idea') or '-'}", f"Notes: {ctx.get('notes') or '-'}",
             f"Themes: {', '.join(ctx.get('themes') or []) or '-'}",
             f"PLAYSTYLE: {ctx.get('playstyle') or 'unknown'}",
             f"Stage: {ctx.get('stage')}",
             "Template categories (target): " + ", ".join(ctx.get("template") or []),
             "Already in the deck: " + "; ".join(ctx.get("deck") or [])]
    if challenged is not None:
        lines.append("CHALLENGED picks (with the reviewers' cut reasons):")
        lines += [f"- {n}: {' / '.join(rs) or 'high-impact card, no cut vote'}" for n, rs in challenged.items()]
    else:
        lines.append("PROPOSED: " + "; ".join(proposals))
        lines.append("CANDIDATES: " + "; ".join(candidates))
    messages, _ = build_messages(TASK, user_text="\n".join(lines))
    return messages


def _run_lens(calls: _Calls, lens: str, ctx: dict, proposals: Sequence[str], candidates: Sequence[str]) -> dict:
    try:
        data = _parse(calls.chat(_prompt(lens, ctx, proposals, candidates)))
    except LLMTruncatedError:
        return {"status": "unavailable", "error": "reply truncated twice"}
    except LLMError as exc:
        return {"status": "unavailable", "error": str(exc)[:160]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    if "votes" not in data and "suggest" not in data:
        return {"status": "unavailable", "error": "reply was not a valid vote"}
    return {"status": "ok", "votes": data.get("votes") or [], "suggest": data.get("suggest") or []}


def reconcile(proposals: Sequence[str], lens_results: Dict[str, dict], critic: Optional[Dict[str, dict]],
              challenged: Sequence[str], protected: Iterable[str] = ()) -> List[dict]:
    """Deterministic decision per proposed card: [{card, action: cut|keep, cuts, keeps, critic, reason}].

    Votes: each usable reviewer counts once (a card it does not mention is a keep). Rules:
      * Unanimous cut by >= 2 reviewers -> cut (no critic needed).
      * A CHALLENGED pick (see module doc) is decided by reviewer votes plus the critic as one extra
        voice: cut iff cut weight > keep weight. A tie keeps the pick (status quo), and a `protected`
        pick (owned or EDHREC-core) also needs a strict majority of REVIEWERS, not just the critic.
      * A pick with cuts but not challenged (critic over budget/unavailable) and not unanimous stands.
      * A lone usable reviewer's cut never removes a card by itself.
    """
    ok = {l: r for l, r in lens_results.items() if r.get("status") == "ok"}
    n = len(ok)
    prot = {_norm(p) for p in protected}
    chall = {_norm(c) for c in challenged}
    cut_reasons: Dict[str, List[str]] = {}
    for lens, res in ok.items():
        for v in res.get("votes") or []:
            if isinstance(v, dict) and str(v.get("vote", "")).lower() == "cut":
                cut_reasons.setdefault(_norm(v.get("card")), []).append(f"{lens}: {str(v.get('reason') or '')[:140]}")
    out = []
    for card in proposals:
        k = _norm(card)
        cuts = len(cut_reasons.get(k, []))
        keeps = n - cuts
        crit = (critic or {}).get(k)
        action, reason = "keep", ""
        if n >= 2 and cuts == n:
            action, reason = "cut", "all reviewers cut it: " + cut_reasons[k][0]
        elif k in chall and crit is not None and n >= 1:
            cut_w = cuts + (1 if crit["verdict"] == "cut" else 0)
            keep_w = keeps + (1 if crit["verdict"] == "stand" else 0)
            if cut_w > keep_w and not (k in prot and cuts <= keeps):
                action = "cut"
                reason = (crit.get("reason") or (cut_reasons.get(k) or [""])[0])
        out.append({"card": card, "action": action, "cuts": cuts, "keeps": keeps,
                    "critic": crit["verdict"] if crit else None, "reason": reason[:200]})
    return out


def review_stage(stage: str, ctx: dict, proposals: Sequence[str], candidates: Sequence[str] = (), *,
                 high_impact: Iterable[str] = (), protected: Iterable[str] = (),
                 client: Any = None, budget: Optional[PanelBudget] = None,
                 lenses: Optional[Sequence[str]] = None) -> PanelOutcome:
    """Run the panel for `stage` over `proposals`. Never raises."""
    budget = budget or PanelBudget.from_env()
    lenses = list(lenses or STAGE_LENSES.get(stage, LENSES))
    ctx = {**ctx, "stage": stage}
    props = list(dict.fromkeys(proposals))[: budget.max_proposals]
    record: dict = {"stage": stage, "lenses": {}, "critic": {"status": "skipped"}, "contested": [],
                    "decisions": [], "adds": [], "calls": 0}
    if not props:
        record["status"] = "skipped"
        return PanelOutcome(record=record)
    try:
        client = client or _default_client()
    except Exception as exc:  # noqa: BLE001 - no LLM configured: the stage simply stands
        record.update(status="unavailable", error=str(exc)[:160])
        return PanelOutcome(record=record)
    calls = _Calls(client, budget)
    cands = [c for c in dict.fromkeys(candidates) if c][:80]

    with ThreadPoolExecutor(max_workers=max(len(lenses), 1)) as pool:
        futures = {lens: pool.submit(_run_lens, calls, lens, ctx, props, cands) for lens in lenses}
        results = {lens: f.result() for lens, f in futures.items()}
    for lens, res in results.items():
        record["lenses"][lens] = ({"status": "ok", "cuts": sum(1 for v in res["votes"] if isinstance(v, dict)
                                                                 and str(v.get("vote", "")).lower() == "cut")}
                                  if res["status"] == "ok" else {"status": "unavailable", "error": res.get("error", "")})
    ok = {l: r for l, r in results.items() if r["status"] == "ok"}
    if not ok:
        record["status"] = "unavailable"
        record["calls"] = calls.n
        return PanelOutcome(record=record)

    # Which picks get the critic: contested (some, not all, reviewers cut) or high-impact with >= 1 cut.
    pset = {_norm(p): p for p in props}
    cut_by: Dict[str, List[str]] = {}
    for lens, res in ok.items():
        for v in res["votes"]:
            if isinstance(v, dict) and str(v.get("vote", "")).lower() == "cut" and _norm(v.get("card")) in pset:
                cut_by.setdefault(_norm(v["card"]), []).append(f"{lens}: {str(v.get('reason') or '')[:120]}")
    hi = {_norm(h) for h in high_impact}
    n = len(ok)
    challenged = [k for k, rs in cut_by.items() if (n >= 2 and len(rs) < n) or n == 1 or k in hi]
    challenged = [k for k in challenged if not (n >= 2 and len(cut_by[k]) == n)]  # unanimous: no critic needed
    challenged.sort(key=lambda k: (-(k in hi), -len(cut_by[k]), k))
    challenged = challenged[: budget.max_critic_picks]
    record["contested"] = [pset[k] for k in challenged]

    critic: Dict[str, dict] = {}
    if challenged:
        try:
            data = _parse(calls.chat(_prompt("critic", ctx, props, cands, {pset[k]: cut_by[k] for k in challenged})))
            for a in data.get("attacks") or []:
                if isinstance(a, dict) and _norm(a.get("card")) in pset:
                    verdict = "cut" if str(a.get("verdict", "")).lower() == "cut" else "stand"
                    critic[_norm(a["card"])] = {"verdict": verdict, "reason": str(a.get("reason") or "")[:200]}
            record["critic"] = {"status": "ok" if critic else "unavailable", "picks": len(challenged)}
        except Exception as exc:  # noqa: BLE001 - truncation/unavailable: reviewers' votes alone decide
            record["critic"] = {"status": "unavailable", "error": str(exc)[:160], "picks": len(challenged)}

    decisions = reconcile(props, results, critic, [pset[k] for k in challenged], protected)
    record["decisions"] = [d for d in decisions if d["action"] == "cut" or d["cuts"] or d["critic"]]
    cuts = {d["card"] for d in decisions if d["action"] == "cut"}

    # Suggestions: only offered candidates, backed by >= 2 reviewers (or the only usable one).
    cand_set = {_norm(c): c for c in cands}
    backing: Dict[str, int] = {}
    for res in ok.values():
        for s in res["suggest"][:5]:
            if isinstance(s, dict) and _norm(s.get("card")) in cand_set:
                backing[_norm(s["card"])] = backing.get(_norm(s["card"]), 0) + 1
    need = 2 if n >= 2 else 1
    adds = [cand_set[k] for k, c in sorted(backing.items(), key=lambda kv: (-kv[1], kv[0])) if c >= need]
    record.update(status="ok", adds=adds, calls=calls.n, cuts=sorted(cuts))
    return PanelOutcome(cuts=cuts, adds=adds, record=record)


def persist(session: dict, stage: str, record: dict) -> None:
    session.setdefault("stage_reviews", {})[stage] = record


__all__ = ["PanelBudget", "PanelOutcome", "review_stage", "reconcile", "persist", "set_client_factory",
           "STAGE_LENSES", "LENSES", "HIGH_IMPACT_PRICE"]
