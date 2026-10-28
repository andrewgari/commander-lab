"""Pass D: the verifier. Nothing reaches the review unless code AND a second model agree it should.

Order of work, per item (a finding, a swap, a ``cuts_to_consider`` entry, the ``top_changes`` selection):

1. **Code checks first** (``checks.py``, the same functions ``tests/review_evals/gates.py`` runs): names resolve,
   legality, color identity, singleton, budget, bracket delta, ids resolve, pool membership, pet cards,
   and "not previously rejected by the owner for this deck". Items that fail are not shown to the critic.
2. **Critic LLM** (``prompts/d_critic.v1.md``) on everything that passed code: unsupported claims, generic advice,
   contradictions with the brief, missing or hand-waved trade-offs. It must come from a *different model family*
   than every generator (D3, ``PipelineConfig.check_critic_is_independent``). A flag only counts if it quotes the
   offending words verbatim from the item (checked here, with a repair turn when it does not).
3. **Regenerate only the failing item**, with the reasons, on the generator's own cached prompt prefix. At most
   ``config.max_verify_loops`` (2) rounds; each round re-runs the code checks and the critic on the new item.
4. **Still failing, or withdrawn by the generator, means removed**: recorded in ``omitted[]`` with the reason and
   NEVER returned in ``strengths``/``problems``/``swaps``/``cuts_to_consider``/``top_changes``.

Findings are verified before swaps, because a swap is only as good as the problem it claims to resolve: a swap
whose problem finding was removed is omitted with that reason, not regenerated against a claim we cannot stand
behind. ``top_changes`` is judged last, on the swaps that survived.

Every model call is traced (pass ``critic`` for the critic, pass ``D`` for regeneration) like Pass A-C.
"""
from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.server.domain.analytics.review_pipeline import b_analysis, c_recommend
from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline import prompts, schemas
from src.server.domain.analytics.review_pipeline.calls import traced_call
from src.server.domain.analytics.review_pipeline.checks import (
    PIPELINE_CHECKS, CheckContext, Violation, cut_item_id, run_checks)
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.facts import FactIndex
from src.server.domain.analytics.review_pipeline.llm import LLMClient, LLMRequest, Message
from src.server.domain.analytics.review_pipeline.names import normalize, strip_typography
from src.server.domain.analytics.review_pipeline.structured import StructuredOutputError, portable_schema, validate_schema
from src.server.domain.analytics.review_pipeline.trace import TraceRecorder

PASS_CRITIC = "critic"
PASS_REGEN = "D"
DOSSIER_TEXT_CHARS = 700
MAX_FACT_LINES = 24
REASON_CHARS = 320
QUOTE_HINT_CHARS = 700
TOP_ID = "top_changes"


@dataclass(frozen=True)
class Reason:
    source: str          # "code" | "critic"
    category: str        # gate name, or one of schemas.CRITIC_CATEGORIES
    text: str
    quote: str = ""

    def short(self) -> str:
        body = f"{self.text}" + (f' (quoted: "{self.quote}")' if self.quote else "")
        return f"{self.source}:{self.category}: {body}"[:REASON_CHARS]


@dataclass
class Item:
    id: str
    kind: str                       # finding | swap | cut | top
    data: dict[str, Any]
    group: str = ""                 # finding: "strengths" | "problems"
    status: str = "active"          # active | removed
    regens: int = 0
    replaced: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VerifyResult:
    strengths: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    swaps: list[dict] = field(default_factory=list)
    cuts_to_consider: list[dict] = field(default_factory=list)
    top_changes: dict | None = None
    omitted: list[dict] = field(default_factory=list)
    sidecar: dict[str, dict] = field(default_factory=dict)
    swap_meta: dict[str, dict] = field(default_factory=dict)
    axes: list[dict] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)        # every flag/failure event with its outcome


class VerifyError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- helpers
def _compact(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", strip_typography(text)).casefold()


_ELLIPSIS = re.compile(r"\.{3,}|…")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def quote_in(quote: str, text: str) -> bool:
    """True when the quote is the item's own words. Typography, case and spacing are forgiven. A critic that elides
    with an ellipsis or stitches two sentences of the item is still quoting it, so such a quote passes when every
    piece (split at ellipses, then at sentence ends) appears in the text, in order. Any word not in the item fails."""
    haystack = _squash(text)
    q = _squash(quote).strip(" .…\"'")
    if not q:
        return False
    if q in haystack:
        return True
    pieces = [piece.strip(" .…\"'") for part in _ELLIPSIS.split(_squash(quote)) for piece in _SENTENCE_END.split(part)]
    pieces = [piece for piece in pieces if piece]
    pos = 0
    for piece in pieces:
        at = haystack.find(piece, pos)
        if at < 0:
            return False
        pos = at + len(piece)
    return bool(pieces)


def critic_validate(obj: Any, texts: Mapping[str, str]) -> list[str]:
    """Schema plus the semantic rules: one verdict per item, ok <-> category none, a flag quotes the item verbatim."""
    errs = validate_schema(obj, schemas.CRITIC_SCHEMA)
    if errs:
        return errs
    seen: dict[str, int] = {}
    for v in obj["verdicts"]:
        seen[v["item_id"]] = seen.get(v["item_id"], 0) + 1
    missing = [i for i in texts if i not in seen]
    extra = [i for i in seen if i not in texts]
    dupes = [i for i, n in seen.items() if n > 1]
    if missing:
        errs.append(f"no verdict for item(s) {', '.join(missing)}; return exactly one verdict per item id")
    if extra:
        errs.append(f"verdict for unknown item id(s) {', '.join(extra)}; use only the ids given")
    if dupes:
        errs.append(f"more than one verdict for {', '.join(dupes)}")
    for v in obj["verdicts"]:
        if v["item_id"] not in texts:
            continue
        if v["verdict"] == "ok":
            if v["category"] != "none":
                errs.append(f"{v['item_id']}: verdict ok must have category 'none'")
        else:
            if v["category"] == "none":
                errs.append(f"{v['item_id']}: a flag needs one of {', '.join(schemas.CRITIC_CATEGORIES)}")
            if not quote_in(v["quote"], texts[v["item_id"]]):
                errs.append(f"{v['item_id']}: the quote {v['quote']!r} does not appear verbatim in the TEXT UNDER REVIEW; "
                            "copy the exact words that are wrong, or return ok. The TEXT UNDER REVIEW for "
                            f"{v['item_id']} is: {_compact(texts[v['item_id']], QUOTE_HINT_CHARS)!r}. "
                            "Quote one contiguous span of it, nothing from any other item or from the dossier")
    return errs


# -------------------------------------------------------------------------------------------- verifier
class Verifier:
    def __init__(self, ctx: ReviewContext, brief: Mapping[str, Any], analysis: b_analysis.AnalysisResult,
                 recs: c_recommend.Recommendations, view: c_recommend.PoolView, client: LLMClient,
                 config: cfg.PipelineConfig, rec: TraceRecorder):
        config.check_critic_is_independent()
        self.ctx, self.brief, self.client, self.config, self.rec, self.view = ctx, dict(brief), client, config, rec, view
        self.question_ids = {q["id"] for q in brief["questions_for_owner"]}
        self.sidecar: dict[str, dict] = copy.deepcopy(analysis.sidecar)
        self.axes = copy.deepcopy(analysis.axes)
        self.items: dict[str, Item] = {}
        self.order: dict[str, list[str]] = {"strengths": [], "problems": [], "swaps": [], "cuts": []}
        for grp, rows in (("strengths", analysis.strengths), ("problems", analysis.problems)):
            for f in rows:
                self.items[f["id"]] = Item(f["id"], "finding", copy.deepcopy(f), group=grp)
                self.order[grp].append(f["id"])
        for s in recs.swaps:
            self.items[s["id"]] = Item(s["id"], "swap", copy.deepcopy(s))
            self.order["swaps"].append(s["id"])
        self.swap_meta = copy.deepcopy(recs.swap_meta)
        for c in recs.cuts_to_consider:
            iid = cut_item_id(c["card"]["name"])
            self.items[iid] = Item(iid, "cut", copy.deepcopy(c))
            self.order["cuts"].append(iid)
        self.top: dict | None = copy.deepcopy(recs.top_changes)
        self.top_item: Item | None = Item(TOP_ID, "top", self.top) if self.top else None
        if self.top_item:
            self.items[TOP_ID] = self.top_item
        self.omitted: list[dict] = []
        self.log: list[dict] = []
        self.unattributed: list[str] = []
        self.stats = {"critic_calls": 0, "critic_items_judged": 0, "critic_flag_events": 0, "code_fail_events": 0,
                      "regeneration_calls": 0, "regenerated_ok": 0, "removed": 0, "by_category": {}}
        self.cctx = self._check_context()

    # ------------------------------------------------------------------------------- check context / doc
    def _check_context(self) -> CheckContext:
        ctx = self.ctx
        deck_cards: list[str] = []
        scry: dict[str, dict] = {}
        for name, qty in ctx.deck_cards:
            deck_cards += [name] * max(int(qty), 1)
            info = ctx.card_info.get(name) or {}
            scry[name] = {"color_identity": info.get("color_identity") or [], "legalities": info.get("legalities") or {}}
        for c in self.view.all_cards:
            scry[c["name"]] = {"color_identity": c.get("color_identity") or [], "legalities": c.get("legalities") or {}}
        deck = {"commanders": list(ctx.commanders), "cards": deck_cards, "pet_cards": list(ctx.pet_cards),
                "budget_hard_cap": ctx.hard_cap_usd, "rejected_swaps": [list(p) for p in sorted(ctx.rejected_swaps)],
                "candidate_pool": sorted({c["name"] for c in self.view.shown}),
                "game_changers": list(ctx.game_changers) if ctx.game_changers is not None else None}
        return CheckContext(deck=deck, scryfall=scry)

    def _active(self, group: str) -> list[Item]:
        return [self.items[i] for i in self.order[group] if self.items[i].status == "active"]

    def doc(self) -> dict[str, Any]:
        """The review document as far as the checks need it, from the items that are still active."""
        swaps = [i.data for i in self._active("swaps")]
        top = None
        if self.top_item and self.top_item.status == "active":
            top = self.top_item.data
        return {"meta": {"commanders": [{"name": n} for n in self.ctx.commanders]},
                "strengths": [i.data for i in self._active("strengths")], "problems": [i.data for i in self._active("problems")],
                "swaps": swaps, "cuts_to_consider": [i.data for i in self._active("cuts")], "top_changes": top,
                "bracket": {"estimated": self.ctx.estimated_bracket, "target": self.ctx.target_bracket, "reasons": []}}

    # ------------------------------------------------------------------------------------- dossiers
    def _card_line(self, name: str) -> str:
        info = self.ctx.card_info.get(name) or {}
        text = _compact(info.get("oracle_text") or "oracle text unavailable", DOSSIER_TEXT_CHARS)
        roles = ",".join(info.get("roles") or []) or "-"
        return f"- {name} | {info.get('type_line') or 'type n/a'} | roles: {roles} | {text}"

    def _pool_line(self, c: Mapping) -> str:
        return "- " + self.view.line(c).replace(c["pool_id"] + " | ", "", 1)

    def _fact_lines(self, key: str) -> list[str]:
        idx = self.ctx.index
        if key in idx.leaves:
            return [f"{key} = {FactIndex._fmt(idx.leaves[key])}"]
        if idx.unavailable_reason(key):
            return [f"{key} = UNAVAILABLE ({idx.unavailable_reason(key)})"]
        lines = idx.render([key]).splitlines()
        return lines[:MAX_FACT_LINES] + ([f"... ({len(lines) - MAX_FACT_LINES} more lines)"] if len(lines) > MAX_FACT_LINES else [])

    def _brief_roles(self, card: str) -> list[str]:
        b, out = self.brief, []
        norm = lambda xs: {normalize(x) for x in xs}
        n = normalize(card)
        if n in norm(b["central_concept"]["key_cards"]):
            out.append("a key card of the deck's central concept")
        for w in b["win_paths"]:
            if n in norm(w["key_cards"]):
                out.append(f"a key card of the win path '{w['path']}'")
        for q in b["intentional_quirks"]:
            if n in norm(q["cards"]):
                out.append(f"part of the deliberate quirk '{q['quirk']}'")
        if n in norm(self.ctx.pet_cards):
            out.append("a PET CARD of the owner")
        strength_ids = [i.id for i in self._active("strengths")
                        if any(normalize(c["name"]) == n for c in i.data["evidence"]["cards"])]
        if strength_ids:
            out.append("cited as evidence by strength finding(s) " + ", ".join(strength_ids))
        return out

    def _finding_text(self, it: Item) -> str:
        f = it.data
        fix = (self.sidecar.get(f["id"]) or {}).get("fix_direction")
        return f"{f['claim']}" + (f" Fix direction: {fix}" if fix else "")

    def _swap_text(self, s: Mapping) -> str:
        alt = f" Alternative: {s['alternative']['name']}." if s.get("alternative") else ""
        return (f"Swap {s['id']}: cut {s['cut']['name']}, add {s['add']['name']}, resolves {s['resolves_finding_id']}. "
                f"Trade-off: {s['tradeoff']}{alt}")

    def _find(self, fid: str) -> dict | None:
        it = self.items.get(fid)
        return it.data if it and it.kind == "finding" else None

    def _swap_dossier(self, s: Mapping) -> str:
        meta = self.swap_meta.get(s["id"]) or {}
        add = self.view.get(meta.get("add_pool_id")) or self.view.by_name(s["add"]["name"])
        out = ["CUT (from the deck):", self._card_line(s["cut"]["name"])]
        br = self._brief_roles(s["cut"]["name"])
        out.append("The brief/profile/analysis say the cut card is: " + ("; ".join(br) if br else "not singled out"))
        out += ["ADD (from the candidate pool):", self._pool_line(add) if add else f"- {s['add']['name']} (pool entry unavailable)"]
        if s.get("alternative"):
            alt = self.view.get(meta.get("alternative_pool_id")) or self.view.by_name(s["alternative"]["name"])
            out += ["ALTERNATIVE (from the pool):", self._pool_line(alt) if alt else f"- {s['alternative']['name']}"]
        f = self._find(s["resolves_finding_id"])
        out.append("PROBLEM IT CLAIMS TO RESOLVE: " + (f"{f['id']}: {f['claim']}" if f else f"{s['resolves_finding_id']} (not available)"))
        if f:
            out.append("  cited cards: " + ("; ".join(c["name"] for c in f["evidence"]["cards"]) or "none"))
            fix = (self.sidecar.get(f["id"]) or {}).get("fix_direction")
            if fix:
                out.append(f"  fix direction from the analysis: {fix}")
        return "\n".join(out)

    def _dossier(self, it: Item) -> str:
        if it.kind == "finding":
            f = it.data
            lines = ["CITED CARDS:"] + [self._card_line(c["name"]) for c in f["evidence"]["cards"]] if f["evidence"]["cards"] else ["CITED CARDS: none"]
            lines.append("CITED FACTS:")
            for k in f["evidence"]["fact_keys"] or []:
                lines += self._fact_lines(k)
            if not f["evidence"]["fact_keys"]:
                lines.append("(none cited)")
            conf = f"severity {f['severity']}, confidence {f['confidence']:g}"
            return "\n".join(lines + [f"(the writer rated this finding: {conf})"])
        if it.kind == "swap":
            return self._swap_dossier(it.data)
        if it.kind == "cut":
            c = it.data
            lines = ["CARD:", self._card_line(c["card"]["name"]),
                     "The brief/profile/analysis say this card is: " + ("; ".join(self._brief_roles(c["card"]["name"])) or "not singled out"),
                     "CITED FACTS:"]
            for k in c["evidence"] or []:
                lines += self._fact_lines(k)
            if not c["evidence"]:
                lines.append("(none cited)")
            return "\n".join(lines)
        swaps = {s.id: s.data for s in self._active("swaps")}
        return "\n".join(f"{i + 1}. {self._swap_text(swaps[sid])}" for i, sid in enumerate(self.top["swap_ids"]) if sid in swaps) \
            + "\n(all swaps, ranked: " + ", ".join(f"{s.id}" for s in self._active("swaps")) + ")"

    def _text(self, it: Item) -> str:
        if it.kind == "finding":
            return self._finding_text(it)
        if it.kind == "swap":
            return self._swap_text(it.data)
        if it.kind == "cut":
            return f"Cut candidate: {it.data['card']['name']}. Reason: {it.data['reason']}"
        return f"Why these first: {it.data['why_these_first']}"

    # --------------------------------------------------------------------------------------- critic
    def _critic(self, items: Sequence[Item]) -> dict[str, tuple[str, str, str]]:
        """item id -> (category, quote, reason) for every flagged item. One call per ``critic_batch`` items."""
        if not items:
            return {}
        t = prompts.load("d_critic", self.config.prompt_version("d_critic"))
        std = prompts.quality_standard(self.config.prompt_version("quality_standard"))
        sys_blocks = (t.render("system.role", quality_standard=std.text),
                      t.render("system.context", profile=self.ctx.profile_block, intent=self.ctx.intent_text,
                               brief=c_recommend.a_brief.render_brief(self.brief), card_table=self.ctx.card_table))
        size = max(self.config.critic_batch, 1)
        batches = [list(items[i:i + size]) for i in range(0, len(items), size)]
        self.stats["critic_items_judged"] += len(items)

        def one(n_batch: tuple[int, list[Item]]) -> dict[str, tuple[str, str, str]]:
            n, batch = n_batch
            texts = {it.id: self._text(it) for it in batch}
            body = "\n\n".join(f"### ITEM {it.id} ({it.kind})\nTEXT UNDER REVIEW:\n{texts[it.id]}\nDOSSIER:\n{self._dossier(it)}" for it in batch)
            req = LLMRequest(
                model=self.config.model("critic"), system_blocks=sys_blocks,
                messages=(Message("user", t.render("user", deck_name=self.ctx.deck_name, items=body, count=len(batch),
                                                   ids=", ".join(texts))),),
                schema_name="critic_verdicts", schema=portable_schema(schemas.CRITIC_SCHEMA),
                max_tokens=self.config.max_tokens_critic, cache_blocks=2, label=f"critic:{n}")
            obj, _ = traced_call(self.client, req, validate=lambda o: critic_validate(o, texts), max_repairs=self.config.max_repairs,
                                 rec=self.rec, pass_name=PASS_CRITIC, axis=None, prompt_version=self.config.prompt_version("d_critic"))
            return {v["item_id"]: (v["category"], v["quote"], v["reason"]) for v in obj["verdicts"] if v["verdict"] == "flag"}

        base = self.stats["critic_calls"]
        self.stats["critic_calls"] += len(batches)
        jobs = list(enumerate(batches, start=base))      # call numbers are fixed before any thread starts (stable replay labels)
        out: dict[str, tuple[str, str, str]] = {}
        if len(jobs) == 1:
            out.update(one(jobs[0]))
        else:
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                for r in pool.map(one, jobs):
                    out.update(r)
        return out

    # ------------------------------------------------------------------------------------ evaluation
    def _evaluate(self, scope: set[str], to_judge: Sequence[Item], rnd: int) -> dict[str, list[Reason]]:
        """Code checks on the whole document (violations for items in ``scope`` only), then the critic on the
        ``to_judge`` items that passed code."""
        reasons: dict[str, list[Reason]] = {}
        for v in run_checks(self.doc(), self.cctx, PIPELINE_CHECKS):
            targets = [i for i in v.items if i in self.items and self.items[i].status == "active"]
            if not v.items:
                self.unattributed.append(str(v))
            for iid in targets:
                if iid in scope:
                    reasons.setdefault(iid, []).append(Reason("code", v.gate, v.message))
        judged = [it for it in to_judge if it.id not in reasons and it.status == "active"]
        flags = self._critic(judged)
        for iid, (cat, quote, why) in flags.items():
            reasons.setdefault(iid, []).append(Reason("critic", cat, why, quote))
        for iid, rs in reasons.items():
            it = self.items.get(iid) or self.top_item
            for r in rs:
                self.stats["code_fail_events" if r.source == "code" else "critic_flag_events"] += 1
                self.stats["by_category"][r.category] = self.stats["by_category"].get(r.category, 0) + 1
            it.history.append({"round": rnd, "reasons": [r.short() for r in rs]})
        return reasons

    def _event(self, it: Item, outcome: str, reasons: Sequence[Reason], note: str = "") -> None:
        self.log.append({"item": it.id, "kind": it.kind, "round": len(it.history) - 1, "outcome": outcome,
                         "source": sorted({r.source for r in reasons}), "categories": sorted({r.category for r in reasons}),
                         "reasons": [r.short() for r in reasons], "note": note})

    # ------------------------------------------------------------------------------------- removal
    def _label(self, it: Item) -> str:
        d = it.data
        if it.kind == "finding":
            return f"{it.id}: {_compact(d['claim'], 180)}"
        if it.kind == "swap":
            return f"{it.id}: cut {d['cut']['name']} -> add {d['add']['name']}"
        if it.kind == "cut":
            return f"{d['card']['name']}: {_compact(d['reason'], 140)}"
        return f"{TOP_ID}: {_compact(d['why_these_first'], 160)}"

    def _remove(self, it: Item, reasons: Sequence[Reason], note: str = "") -> None:
        it.status = "removed"
        kind = {"finding": "finding", "swap": "swap", "cut": "cut", "top": "claim"}[it.kind]
        why = "; ".join(r.short() for r in reasons) if reasons else ""
        if note:
            why = (why + "; " if why else "") + note
        why = why or "removed by the verifier"
        self.omitted.append({"kind": kind, "item": self._label(it), "reason": why[:600]})
        self.stats["removed"] += 1
        step = self.rec.step_of(it.id)
        self.rec.record_drop(step_index=step if step is not None else 0, axis=PASS_REGEN, finding={"id": it.id, "kind": it.kind, **it.data},
                             reason=reasons[0].category if reasons else "removed", detail=why[:600])
        self.rec.link_finding(it.id, step if step is not None else 0, kept=False)
        self._event(it, "removed", reasons, note)

    # ------------------------------------------------------------------------------- regeneration
    def _regen_call(self, system_blocks: Sequence[str], cache_blocks: int, model: str, user: str, schema: Mapping, schema_name: str,
                    validate, label: str, kind: str) -> Any:
        """One regeneration call, traced as pass D. Returns the validated object or None when the generator
        could not produce a valid one within the repair budget (the item is then removed)."""
        self.stats["regeneration_calls"] += 1
        req = LLMRequest(model=model, system_blocks=tuple(system_blocks), messages=(Message("user", user),), schema_name=schema_name,
                         schema=portable_schema(schema), max_tokens=self.config.max_tokens_c if kind != "finding" else self.config.max_tokens_b,
                         cache_blocks=cache_blocks, label=label)
        try:
            obj, step = traced_call(self.client, req, validate=validate, max_repairs=self.config.max_repairs, rec=self.rec,
                                    pass_name=PASS_REGEN, axis=f"regen_{kind}", prompt_version=self.config.prompt_version("d_regen"))
        except StructuredOutputError:
            return None
        return obj, step

    def _reasons_text(self, reasons: Sequence[Reason]) -> str:
        return "\n".join(f"- {r.text}" + (f' (you wrote: "{r.quote}")' if r.quote else "") + f" [{r.category}]" for r in reasons)

    def _current_blocks(self) -> tuple[str, str, str]:
        strengths = [i.data for i in self._active("strengths")]
        problems = [i.data for i in self._active("problems")]
        return c_recommend.system_blocks(self.ctx, self.brief, strengths, problems, self.sidecar, self.view, self.config)

    def _candidate_errors(self, swap_id: str | None, cut_key: str | None, new_swap: dict | None, new_cut: dict | None) -> list[str]:
        """Code-check a candidate replacement in place so the generator can repair it before the critic sees it."""
        doc = self.doc()
        if new_swap is not None:
            doc["swaps"] = [new_swap if s["id"] == swap_id else s for s in doc["swaps"]]
            if swap_id not in {s["id"] for s in doc["swaps"]}:
                doc["swaps"].append(new_swap)
        if new_cut is not None:
            doc["cuts_to_consider"] = [new_cut if cut_item_id(c["card"]["name"]) == cut_key else c for c in doc["cuts_to_consider"]]
            if cut_key not in {cut_item_id(c["card"]["name"]) for c in doc["cuts_to_consider"]}:
                doc["cuts_to_consider"].append(new_cut)
        target = {swap_id} if new_swap is not None else {cut_item_id(new_cut["card"]["name"]), cut_key} if new_cut is not None else set()
        return [v.message for v in run_checks(doc, self.cctx, PIPELINE_CHECKS) if set(v.items) & target]

    def _regen_swap(self, it: Item, reasons: Sequence[Reason]) -> tuple[bool, str]:
        t = prompts.load("d_regen", self.config.prompt_version("d_regen"))
        s = it.data
        pos = [x.id for x in self._active("swaps")].index(it.id) + 1
        others = [x for x in self._active("swaps") if x.id != it.id]
        accepted = "\n".join(f"- {o.id}: cut {o.data['cut']['name']} -> add {o.data['add']['name']} (resolves {o.data['resolves_finding_id']})"
                             for o in others) or "- (none)"
        user = t.render("user.swap", swap_id=it.id, position=pos, cut=s["cut"]["name"], add=s["add"]["name"], tradeoff=s["tradeoff"],
                        reasons=self._reasons_text(reasons), accepted=accepted)
        problem_ids = {i.id for i in self._active("problems")}
        holder: dict[str, Any] = {}

        def validate(obj: Any) -> list[str]:
            errs = validate_schema(obj, schemas.SWAP_REGEN_SCHEMA)
            if errs:
                return errs
            if obj["action"] == "withdraw":
                holder["withdraw"] = obj["note"]
                return []
            if obj["swap"] is None:
                return ["action is replace but swap is null; give the new swap or withdraw"]
            doc, meta, errs = c_recommend.build_swap(obj["swap"], it.id, self.ctx, self.view, problem_ids)
            if errs:
                return errs
            errs = self._candidate_errors(it.id, None, doc, None)
            if not errs:
                holder["swap"], holder["meta"] = doc, meta
            return errs

        got = self._regen_call(self._current_blocks(), 3, self.config.model("C"), user, schemas.SWAP_REGEN_SCHEMA, "swap_regen", validate,
                               f"D:regen:{it.id}:{it.regens}", "swap")
        if got is None:
            return False, "the generator could not produce a valid replacement"
        if "withdraw" in holder:
            return False, "the generator withdrew the swap: " + holder["withdraw"]
        it.data = holder["swap"]
        self.swap_meta[it.id] = holder["meta"]
        self.rec.link_finding(it.id, got[1], kept=True)
        return True, ""

    def _regen_finding(self, it: Item, reasons: Sequence[Reason]) -> tuple[bool, str]:
        t = prompts.load("d_regen", self.config.prompt_version("d_regen"))
        f = it.data
        axis = cfg.axis_by_id(f["axis"])
        kind = "strength" if it.group == "strengths" else "problem"
        user = t.render("user.finding", kind=kind.upper(), finding_id=it.id, claim=f["claim"], severity=f["severity"],
                        confidence=f"{f['confidence']:g}", cards="; ".join(c["name"] for c in f["evidence"]["cards"]) or "none",
                        fact_keys="; ".join(f["evidence"]["fact_keys"]) or "none", reasons=self._reasons_text(reasons))
        holder: dict[str, Any] = {}

        def validate(obj: Any) -> list[str]:
            errs = validate_schema(obj, schemas.FINDING_REGEN_SCHEMA)
            if errs:
                return errs
            if obj["action"] == "withdraw":
                holder["withdraw"] = obj["note"]
                return []
            if obj["finding"] is None:
                return ["action is rewrite but finding is null; give the finding or withdraw"]
            bad = b_analysis.check_finding(obj["finding"], self.ctx, self.question_ids)
            if bad:
                return [f"the finding fails a check: {bad[0]} {bad[1] if bad[1] else ''}".strip()]
            n = int(it.id.rsplit("-", 1)[1])
            doc, side = b_analysis._materialise(self.ctx, axis, it.id[0], n, obj["finding"], self.question_ids)
            doc["id"] = it.id
            holder["doc"], holder["side"] = doc, side
            return []

        blocks = b_analysis.build_request(self.ctx, self.brief, axis, self.config).system_blocks
        got = self._regen_call(blocks, 2, self.config.model("B", axis.id), user, schemas.FINDING_REGEN_SCHEMA, "finding_regen", validate,
                               f"D:regen:{it.id}:{it.regens}", "finding")
        if got is None:
            return False, "the generator could not produce a valid rewrite"
        if "withdraw" in holder:
            return False, "the generator withdrew the finding: " + holder["withdraw"]
        it.data = holder["doc"]
        self.sidecar[it.id] = holder["side"]
        self.rec.link_finding(it.id, got[1], kept=True)
        return True, ""

    def _regen_cut(self, it: Item, reasons: Sequence[Reason]) -> tuple[bool, str]:
        t = prompts.load("d_regen", self.config.prompt_version("d_regen"))
        c = it.data
        user = t.render("user.cut", card=c["card"]["name"], reason=c["reason"], fact_keys="; ".join(c["evidence"]) or "none",
                        reasons=self._reasons_text(reasons))
        swap_cuts = {s.data["cut"]["name"] for s in self._active("swaps")}
        holder: dict[str, Any] = {}

        def validate(obj: Any) -> list[str]:
            errs = validate_schema(obj, schemas.CUT_REGEN_SCHEMA)
            if errs:
                return errs
            if obj["action"] == "withdraw":
                holder["withdraw"] = obj["note"]
                return []
            if obj["cut"] is None:
                return ["action is replace but cut is null; give the entry or withdraw"]
            doc, errs = c_recommend.build_cut(obj["cut"], self.ctx)
            if errs:
                return errs
            if doc["card"]["name"] in swap_cuts:
                return [f"{doc['card']['name']!r} is already the cut of a swap"]
            errs = self._candidate_errors(None, it.id, None, doc)
            if not errs:
                holder["doc"] = doc
            return errs

        got = self._regen_call(self._current_blocks(), 3, self.config.model("C"), user, schemas.CUT_REGEN_SCHEMA, "cut_regen", validate,
                               f"D:regen:{it.id}:{it.regens}", "cut")
        if got is None:
            return False, "the generator could not produce a valid replacement"
        if "withdraw" in holder:
            return False, "the generator withdrew the cut: " + holder["withdraw"]
        new_id = cut_item_id(holder["doc"]["card"]["name"])
        it.data = holder["doc"]
        if new_id != it.id:                       # the entry now names another card: re-key it
            self.items.pop(it.id)
            self.order["cuts"] = [new_id if x == it.id else x for x in self.order["cuts"]]
            it.id = new_id
            self.items[new_id] = it
        self.rec.link_finding(it.id, got[1], kept=True)
        return True, ""

    def _regen_top(self, it: Item, reasons: Sequence[Reason]) -> tuple[bool, str]:
        t = prompts.load("d_regen", self.config.prompt_version("d_regen"))
        swaps = self._active("swaps")
        want = min(schemas.TOP_CHANGES, len(swaps))
        listing = "\n".join(f"{i + 1}. {s.id}: cut {s.data['cut']['name']} -> add {s.data['add']['name']}; resolves {s.data['resolves_finding_id']}; "
                            f"trade-off: {s.data['tradeoff']}" for i, s in enumerate(swaps))
        user = t.render("user.top", swaps=listing, previous=(it.data or {}).get("why_these_first", "(none)"),
                        reasons=self._reasons_text(reasons), want=want)
        holder: dict[str, Any] = {}

        def validate(obj: Any) -> list[str]:
            errs = validate_schema(obj, schemas.TOP_REGEN_SCHEMA)
            if errs:
                return errs
            r = obj["ranks"]
            if len(set(r)) != len(r) or any(x < 1 or x > len(swaps) for x in r):
                return [f"ranks must be distinct positions between 1 and {len(swaps)}; got {r}"]
            if len(r) != want:
                return [f"ranks must hold exactly {want} position(s); got {len(r)}"]
            if not obj["why_these_first"].strip():
                return ["why_these_first is empty"]
            holder["top"] = {"swap_ids": [swaps[x - 1].id for x in r], "why_these_first": obj["why_these_first"].strip()}
            return []

        got = self._regen_call(self._current_blocks(), 3, self.config.model("C"), user, schemas.TOP_REGEN_SCHEMA, "top_regen", validate,
                               f"D:regen:{TOP_ID}:{it.regens}", "top")
        if got is None:
            return False, "the generator could not produce a valid selection"
        it.data = holder["top"]
        self.top = holder["top"]
        return True, ""

    def _regenerate(self, it: Item, reasons: Sequence[Reason]) -> None:
        it.regens += 1
        ok, why = {"finding": self._regen_finding, "swap": self._regen_swap, "cut": self._regen_cut, "top": self._regen_top}[it.kind](it, reasons)
        if ok:
            it.replaced = True
            self.stats["regenerated_ok"] += 1
            self._event(it, "regenerated", reasons)
        else:
            self._remove(it, reasons, why)

    # ---------------------------------------------------------------------------------------- stages
    def _stage(self, items: Sequence[Item]) -> None:
        """Verify ``items``. Round 0 judges all of them; later rounds judge only what was just replaced. Code checks
        always cover every active item of the stage, because replacing one item can break another (singleton, bracket)."""
        stage_ids = [i.id for i in items]
        pending = [i for i in items if i.status == "active"]
        for rnd in range(self.config.max_verify_loops + 1):
            if not pending:
                return
            scope = {self.items[i].id for i in stage_ids if i in self.items and self.items[i].status == "active"}
            fails = self._evaluate(scope, pending, rnd)
            pending = []
            for iid, rs in fails.items():
                it = self.items[iid]
                if it.status != "active":
                    continue
                if rnd >= self.config.max_verify_loops:
                    self._remove(it, rs)
                    continue
                self._regenerate(it, rs)
                if it.status == "active":
                    pending.append(it)
                    if it.id not in stage_ids:                  # a cut entry re-keyed to another card
                        stage_ids.append(it.id)

    def _stage_findings(self) -> None:
        self._stage([*self._active("strengths"), *self._active("problems")])
        removed = {i.id for g in ("strengths", "problems") for i in (self.items[x] for x in self.order[g]) if i.status == "removed"}
        for g in ("strengths", "problems"):
            self.order[g] = [x for x in self.order[g] if self.items[x].status == "active" or x in removed]
        for row in self.axes:
            row["finding_ids"] = [f for f in row["finding_ids"] if f not in removed]

    def _drop_orphan_swaps(self) -> None:
        alive = {i.id for i in self._active("problems")}
        for it in list(self._active("swaps")):
            if it.data["resolves_finding_id"] not in alive:
                gone = it.data["resolves_finding_id"]
                self._remove(it, [Reason("code", "unresolved_finding_id", f"the problem it resolves ({gone}) was removed by the verifier")])

    def _stage_top(self) -> None:
        swaps = self._active("swaps")
        if not swaps:
            # Nothing to rank. top_changes is derived from the swaps, so this is not a second omission: the swap
            # omissions already say why (a review with no swaps simply has no top_changes).
            self.top_item, self.top = None, None
            self.items.pop(TOP_ID, None)
            return
        want = min(schemas.TOP_CHANGES, len(swaps))
        it = self.top_item
        if it is None or it.status != "active":
            it = self.top_item = Item(TOP_ID, "top", {"swap_ids": [], "why_these_first": ""})
        self.items[TOP_ID] = it
        sel = it.data.get("swap_ids") or []
        alive = {x.id for x in swaps}
        stale = len(sel) != want or any(x not in alive for x in sel) or any(self.items[x].replaced for x in sel)
        for rnd in range(self.config.max_verify_loops + 1):
            if stale and rnd == 0:
                reasons = [Reason("code", "top_changes_stale", "the swaps this selection was written for were removed or replaced")]
                it.history.append({"round": rnd, "reasons": [r.short() for r in reasons]})
                self.stats["code_fail_events"] += 1
                self.stats["by_category"]["top_changes_stale"] = self.stats["by_category"].get("top_changes_stale", 0) + 1
            else:
                reasons = self._evaluate({TOP_ID}, [it] if rnd == 0 or it.replaced else [], rnd).get(TOP_ID, [])
            if not reasons:
                self.top = it.data
                return
            if rnd >= self.config.max_verify_loops:
                self._remove(it, reasons)
                self.top_item, self.top = None, None
                return
            self._regenerate(it, reasons)
            if it.status != "active":
                self.top_item, self.top = None, None
                return

    def _stage_swaps(self) -> None:
        self._stage([*self._active("swaps"), *self._active("cuts")])

    def run(self) -> VerifyResult:
        self._stage_findings()
        self._drop_orphan_swaps()
        self._stage_swaps()
        self._stage_top()
        return self.result()

    def result(self) -> VerifyResult:
        problems = b_analysis._order([i.data for i in self._active("problems")])
        r = VerifyResult(
            strengths=b_analysis._order([i.data for i in self._active("strengths")]), problems=problems,
            swaps=[i.data for i in self._active("swaps")], cuts_to_consider=[i.data for i in self._active("cuts")],
            top_changes=copy.deepcopy(self.top) if self.top_item else None, omitted=list(self.omitted),
            sidecar={k: v for k, v in self.sidecar.items() if self.items.get(k) and self.items[k].status == "active"},
            swap_meta={k: v for k, v in self.swap_meta.items() if self.items.get(k) and self.items[k].status == "active"},
            axes=self.axes, log=list(self.log))
        flagged_items = {e["item"] for e in self.log if "critic" in e["source"]} | {
            iid for iid, it in self.items.items() if any(h["reasons"] and any(x.startswith("critic:") for x in h["reasons"]) for h in it.history)}
        r.stats = {**self.stats, "critic_flagged_items": len(flagged_items), "unattributed_violations": list(self.unattributed),
                   "omitted": len(self.omitted), "critic_model": self.config.model("critic")}
        return r


def verify(ctx: ReviewContext, brief: Mapping[str, Any], analysis: b_analysis.AnalysisResult, recs: c_recommend.Recommendations,
           view: c_recommend.PoolView, client: LLMClient, config: cfg.PipelineConfig, rec: TraceRecorder) -> VerifyResult:
    return Verifier(ctx, brief, analysis, recs, view, client, config, rec).run()
