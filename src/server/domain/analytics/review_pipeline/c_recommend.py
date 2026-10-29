"""Pass C: recommendations. Swaps are chosen from the candidate pool by ID, never by name.

The model sees the pool as ``pc-N`` ids and returns ``add_id`` / ``alternative_id``; code turns ids into
card refs, prices and the owned flag from the pool's own data, so a card name the model invented cannot
reach the output (CONTRACT §2.2-2.3) and a price or ownership claim is never model text (§2.1).

This pass makes the output *well-formed* (every id resolves, every cut is a real non-commander deck card,
ranks are valid). Whether an item is *allowed* (pet card, previously rejected, over budget, bracket,
singleton, supported by evidence) is Pass D's job: ``d_verify.py``.
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.server.domain.analytics.review_pipeline import a_brief, b_analysis
from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline import prompts, schemas
from src.server.domain.analytics.review_pipeline.calls import traced_call
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.llm import LLMClient, LLMRequest, Message
from src.server.domain.analytics.review_pipeline.names import CardResolver, normalize
from src.server.domain.analytics.review_pipeline.structured import portable_schema, validate_schema
from src.server.domain.analytics.review_pipeline.trace import TraceRecorder
from src.server.domain.decks.commander_roles import FUNCTION_ROLES

LOG = logging.getLogger(__name__)
PASS = "C"
POOL_TEXT_CHARS = 240
PER_ROLE_FLOOR = 12          # when the prompt view is capped, every role keeps at least this many cards
OWNERSHIP_UNKNOWN = "Ownership unavailable"
_SWAP_ID = re.compile(r"^sw-(\d+)$")
_FINDING_ID = re.compile(r"^[sp]-[a-z_]+-\d+$")
_NO_ID = {"null", "none", "n/a", ""}          # how a cheaper model spells "no alternative" inside a string


# ----------------------------------------------------------------------------------------------- pool view
class PoolView:
    """The candidate pool as the model sees it: stable ids, a capped grouped listing, and id -> card lookup."""

    def __init__(self, cards: Sequence[Mapping[str, Any]], notes: Sequence[str] = (), *, max_cards: int = 150):
        self.all_cards = [dict(c) for c in cards]
        self.notes = list(notes)
        self.ownership_known = not any(str(n).startswith(OWNERSHIP_UNKNOWN) for n in self.notes)
        self.by_id: dict[str, dict] = {}
        for i, c in enumerate(self.all_cards, 1):
            cid = f"pc-{int(c.get('rank') or i)}"
            c["pool_id"] = cid
            self.by_id[cid] = c
        self.shown = self._select(max_cards)
        self._shown_ids = {c["pool_id"] for c in self.shown}
        self._by_name: dict[str, dict] = {normalize(c["name"]): c for c in self.all_cards}
        for c in self.all_cards:                                      # a face name must find the double-faced card too
            for face in c["name"].split(" // ")[1:]:
                self._by_name.setdefault(normalize(face), c)
            self._by_name.setdefault(normalize(c["name"].split(" // ")[0]), c)

    @classmethod
    def from_pool(cls, pool: Any, max_cards: int = 150) -> "PoolView":
        cards = pool.get("cards") if isinstance(pool, Mapping) else pool.cards
        notes = pool.get("notes", []) if isinstance(pool, Mapping) else pool.notes
        return cls(cards, notes, max_cards=max_cards)

    def _select(self, max_cards: int) -> list[dict]:
        if len(self.all_cards) <= max_cards:
            return list(self.all_cards)
        keep: dict[str, dict] = {}
        for role in FUNCTION_ROLES:
            for c in [c for c in self.all_cards if role in (c.get("roles") or [])][:PER_ROLE_FLOOR]:
                keep[c["pool_id"]] = c
        for c in self.all_cards:
            if len(keep) >= max_cards:
                break
            keep.setdefault(c["pool_id"], c)
        return sorted(keep.values(), key=lambda c: c.get("rank") or 0)

    # -- lookups ------------------------------------------------------------------------------------
    def get(self, pool_id: str) -> dict | None:
        """A card by id, but only one the model was actually shown: an id it could not have seen is a guess."""
        c = self.by_id.get(pool_id) if isinstance(pool_id, str) else None
        return c if c is not None and c["pool_id"] in self._shown_ids else None

    def by_name(self, name: str) -> dict | None:
        return self._by_name.get(normalize(name)) if name else None

    def names(self) -> set[str]:
        return {c["name"] for c in self.all_cards}

    def shown_ids(self) -> set[str]:
        return {c["pool_id"] for c in self.shown}

    # -- rendering ----------------------------------------------------------------------------------
    @staticmethod
    def _owned_text(c: Mapping, known: bool) -> str:
        if not known:
            return ""
        if not c.get("owned"):
            return "not owned"
        free, tot = c.get("owned_available") or 0, c.get("owned_copies") or 0
        if free:
            return f"OWNED x{tot} ({free} free)"
        return "owned but sleeved in " + (", ".join(c.get("owned_in_decks") or []) or "another deck")

    def line(self, c: Mapping, *, with_text: bool = True) -> str:
        e = c.get("edhrec") or {}
        bits = [c["pool_id"], c["name"] + (" [GC]" if c.get("game_changer") else ""),
                f"mv {c['cmc']:g}" if isinstance(c.get("cmc"), (int, float)) else "mv n/a", c.get("type_line") or "type n/a",
                "roles: " + (",".join(c.get("roles") or []) or "-"),
                f"${c['price_usd']:.2f}" if isinstance(c.get("price_usd"), (int, float)) else "price n/a"]
        own = self._owned_text(c, self.ownership_known)
        if own:
            bits.append(own)
        if e.get("inclusion") is not None:
            bits.append(f"EDHREC {e['inclusion'] * 100:.0f}% of decks" + (f", synergy {e['synergy']:+.2f}" if isinstance(e.get("synergy"), (int, float)) else ""))
        if c.get("themes"):
            bits.append("themes: " + ",".join(c["themes"]))
        if with_text:
            t = re.sub(r"\s+", " ", c.get("oracle_text") or "oracle text unavailable").strip().replace("|", "/")
            bits.append(t if len(t) <= POOL_TEXT_CHARS else t[:POOL_TEXT_CHARS].rstrip() + "…")
        return " | ".join(bits)

    def render(self) -> str:
        head = [f"POOL (the ONLY cards you may add; {len(self.shown)} of {len(self.all_cards)} shown, grouped by function, best first; "
                "[GC] = Game Changer, counts toward bracket)"]
        head += [f"Note: {n}" for n in self.notes]
        if not self.ownership_known:
            head.append("Ownership is UNKNOWN for this review: do not claim any card is owned or free.")
        groups: dict[str, list[dict]] = {}
        for c in self.shown:
            first = next((r for r in FUNCTION_ROLES if r in (c.get("roles") or [])), None)
            groups.setdefault(first or "other", []).append(c)
        out = list(head)
        for role in (*FUNCTION_ROLES, "other"):
            if groups.get(role):
                out.append(f"== {role.upper()} ({len(groups[role])}) ==")
                out += [self.line(c) for c in groups[role]]
        return "\n".join(out)


# ------------------------------------------------------------------------------------------ prompt inputs
def render_findings(strengths: Sequence[Mapping], problems: Sequence[Mapping], sidecar: Mapping[str, Mapping]) -> str:
    def cards(f: Mapping) -> str:
        return "; ".join(c["name"] for c in f["evidence"]["cards"]) or "none"

    lines = ["PROBLEMS (a swap may only resolve one of these; ordered most severe first)"]
    for f in problems:
        sc = sidecar.get(f["id"], {})
        lines.append(f"- {f['id']} | {f['severity']} | confidence {f['confidence']:g} | axis {f['axis']} | {f['claim']} | "
                     f"cards: {cards(f)}" + (f" | fix direction: {sc['fix_direction']}" if sc.get("fix_direction") else "")
                     + (f" | hinges on open question(s) {', '.join(sc['depends_on_questions'])}" if sc.get("depends_on_questions") else ""))
    if not problems:
        lines.append("- (none: the analysis found no problem worth a fix)")
    lines.append("STRENGTHS (do not cut the cards that carry these)")
    for f in strengths:
        lines.append(f"- {f['id']} | {f['severity']} | {f['claim']} | cards: {cards(f)}")
    return "\n".join(lines)


def render_rejected(pairs: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"- {c} -> {a}" for c, a in sorted(pairs)) or "- (none)"


FACT_PREFIXES = ("deck", "roles", "bracket", "mana", "curve.average_mv", "derived", "synergy", "combos.complete", "card")


def system_blocks(ctx: ReviewContext, brief: Mapping, strengths: Sequence[Mapping], problems: Sequence[Mapping],
                  sidecar: Mapping[str, Mapping], view: PoolView, config: cfg.PipelineConfig) -> tuple[str, str, str]:
    """The three static, cacheable blocks of every Pass C call (initial and regeneration share them byte for byte)."""
    t = prompts.load("c_recommend", config.prompt_version("c_recommend"))
    std = prompts.quality_standard(config.prompt_version("quality_standard"))
    return (
        t.render("system.role", quality_standard=std.text),
        t.render("system.context", profile=ctx.profile_block, brief=a_brief.render_brief(dict(brief)), intent=ctx.intent_text,
                 rejected=render_rejected(sorted(ctx.rejected_swaps)), card_table=ctx.card_table),
        t.render("system.evidence", findings=render_findings(strengths, problems, sidecar), facts=ctx.index.render(FACT_PREFIXES),
                 bracket=bracket_text(ctx), pool=view.render()),
    )


def bracket_text(ctx: ReviewContext) -> str:
    est = "unavailable" if ctx.estimated_bracket is None else str(ctx.estimated_bracket)
    tgt = "not stated by the owner" if ctx.target_bracket is None else str(ctx.target_bracket)
    return (f"Estimated bracket (minimum the current list qualifies for): {est}. Owner's target bracket: {tgt}.\n"
            + ("\n".join(f"- {r}" for r in ctx.bracket_reasons) or "- (no bracket reasons available)"))


# ----------------------------------------------------------------------------------------- materialisation
@dataclass
class Recommendations:
    """Pass C output, document-shaped. ``swap_meta`` keeps the pool ids behind each swap (for regeneration)."""
    swaps: list[dict[str, Any]] = field(default_factory=list)
    cuts_to_consider: list[dict[str, Any]] = field(default_factory=list)
    top_changes: dict[str, Any] | None = None
    swap_meta: dict[str, dict[str, Any]] = field(default_factory=dict)


def _ref(name: str, oracle_id: str | None) -> dict[str, str]:
    return {"name": name, "oracle_id": oracle_id or ""}


def budget_effect(add: Mapping, cut_price: float | None, owned_known: bool) -> str:
    ap = add.get("price_usd")
    if ap is None:
        base = "price of the add is unavailable"
    elif cut_price is None:
        base = f"adds ${ap:.2f}; the cut's price is unavailable"
    else:
        net = ap - cut_price
        base = f"net {'+' if net >= 0 else '-'}${abs(net):.2f} (adds ${ap:.2f}, cuts ${cut_price:.2f})"
    if not owned_known:
        return base + "; ownership unknown"
    if add.get("owned_available"):
        return base + "; you own a free copy, so no purchase"
    if add.get("owned"):
        return base + "; you own it but every copy is sleeved in another deck"
    return base + "; not owned"


def _no_id(value: Any) -> bool:
    """``null`` is the schema's way to say "no alternative"; the string "null" / "none" means the same thing."""
    return value is None or (isinstance(value, str) and value.strip().lower() in _NO_ID)


def build_swap(raw: Mapping[str, Any], swap_id: str, ctx: ReviewContext, view: PoolView, problem_ids: set[str]
               ) -> tuple[dict | None, dict | None, list[str]]:
    """(document swap, meta, errors). Errors are written for the model's repair turn."""
    errs: list[str] = []
    cut = ctx.resolver.resolve(raw.get("cut", ""))
    if cut is None:
        errs.append(f"{swap_id}: cut {raw.get('cut')!r} is not a card in this deck; copy a name from the card table exactly")
    elif cut in ctx.commanders:
        errs.append(f"{swap_id}: cut {cut!r} is the commander and can never be cut")
    add = view.get(raw.get("add_id"))
    if add is None:
        errs.append(f"{swap_id}: add_id {raw.get('add_id')!r} is not a pool id; use an id like pc-12 from the POOL block, never a name")
    alt = None
    if not _no_id(raw.get("alternative_id")):
        alt = view.get(raw["alternative_id"])
        if alt is None:
            errs.append(f"{swap_id}: alternative_id {raw['alternative_id']!r} is not a pool id")
        elif add is not None and alt["pool_id"] == add["pool_id"]:
            errs.append(f"{swap_id}: alternative_id must differ from add_id")
    if raw.get("resolves_finding_id") not in problem_ids:
        errs.append(f"{swap_id}: resolves_finding_id {raw.get('resolves_finding_id')!r} is not a PROBLEM id; use one of: "
                    f"{', '.join(sorted(problem_ids)) or '(there are no problems)'}")
    if errs:
        return None, None, errs
    if not add.get("oracle_id"):
        return None, None, [f"{swap_id}: pool card {add['name']!r} has no oracle id; choose another"]
    cut_oid = ctx.resolver.oracle_id(cut)
    if not cut_oid:
        return None, None, [f"{swap_id}: deck card {cut!r} has no oracle id; choose another cut"]
    owned = bool(add.get("owned"))
    doc = {"id": swap_id, "cut": _ref(cut, cut_oid), "add": _ref(add["name"], add["oracle_id"]),
           "resolves_finding_id": raw["resolves_finding_id"], "tradeoff": raw["tradeoff"].strip(),
           "bracket_effect": raw["bracket_effect"],
           "budget_effect": budget_effect(add, (ctx.card_info.get(cut) or {}).get("price"), view.ownership_known),
           "alternative": _ref(alt["name"], alt["oracle_id"]) if alt and alt.get("oracle_id") else None,
           "owned": owned, "price": add.get("price_usd")}
    meta = {"add_pool_id": add["pool_id"], "alternative_pool_id": alt["pool_id"] if alt else None}
    return doc, meta, []


def _bad_fact_key(key: str, ctx: ReviewContext) -> str:
    """Why one fact key was rejected, written for the model's repair turn."""
    if _FINDING_ID.match(str(key)):
        return (f"{key!r} is a finding id, not a fact key; finding ids are never evidence for a cut. Delete it and cite only "
                "`fact.key` names copied from the FACTS block (for example one starting with roles., mana., synergy. or card.)")
    if ctx.index.exists(key):
        return f"{key!r} is unavailable for this deck; do not cite it"
    near = difflib.get_close_matches(str(key), [*ctx.index.leaves, *ctx.index.nodes], n=3, cutoff=0.6)
    hint = f"; did you mean {', '.join(repr(n) for n in near)}?" if near else "; copy a key exactly from the FACTS block"
    return f"{key!r} is not a fact key{hint}"


def build_cut(raw: Mapping[str, Any], ctx: ReviewContext) -> tuple[dict | None, list[str]]:
    name = ctx.resolver.resolve(raw.get("card", ""))
    if name is None:
        return None, [f"cuts_to_consider: {raw.get('card')!r} is not a card in this deck"]
    if name in ctx.commanders:
        return None, [f"cuts_to_consider: {name!r} is the commander"]
    bad = [k for k in raw.get("fact_keys") or [] if not ctx.index.exists(k) or ctx.index.unavailable_reason(k)]
    if bad:
        return None, [f"cuts_to_consider: fact key(s) {bad} do not exist or are unavailable for {name!r}: "
                      + "; ".join(_bad_fact_key(k, ctx) for k in bad)]
    if not ctx.resolver.oracle_id(name):
        return None, [f"cuts_to_consider: {name!r} has no oracle id"]
    return {"card": _ref(name, ctx.resolver.oracle_id(name)), "reason": raw["reason"].strip(),
            "evidence": list(dict.fromkeys(raw.get("fact_keys") or []))}, []


def build_top(swap_ids: Sequence[str], why: str, available: Sequence[str]) -> dict[str, Any]:
    return {"swap_ids": list(swap_ids)[:schemas.TOP_CHANGES], "why_these_first": why.strip()}


def materialise(obj: Mapping[str, Any], ctx: ReviewContext, view: PoolView, problem_ids: set[str], *,
                drop_bad_cuts: bool = False) -> tuple[Recommendations, list[str]]:
    """``drop_bad_cuts``: leave out a ``cuts_to_consider`` entry that fails validation instead of reporting it. Only the final\n    attempt uses it: the list is advisory and may legitimately be empty, so losing one unsupportable entry beats losing the\n    whole review. Swap errors are never dropped."""
    errs: list[str] = []
    rec = Recommendations()
    swaps = obj.get("swaps") or []
    for i, raw in enumerate(swaps, 1):
        doc, meta, e = build_swap(raw, f"sw-{i}", ctx, view, problem_ids)
        errs += e
        if doc:
            rec.swaps.append(doc)
            rec.swap_meta[doc["id"]] = meta
    ranks = obj["top_changes"]["ranks"]
    want = min(schemas.TOP_CHANGES, len(swaps))
    if len(set(ranks)) != len(ranks) or any(r < 1 or r > len(swaps) for r in ranks):
        errs.append(f"top_changes.ranks must be distinct positions between 1 and {len(swaps)}; got {ranks}")
    elif len(ranks) != want:
        errs.append(f"top_changes.ranks must hold exactly {want} position(s) ({'3 changes' if want == 3 else 'all of the swaps'}); got {len(ranks)}")
    if swaps and not obj["top_changes"]["why_these_first"].strip():
        errs.append("top_changes.why_these_first is empty")
    if not errs:
        rec.top_changes = build_top([f"sw-{r}" for r in ranks], obj["top_changes"]["why_these_first"], [s["id"] for s in rec.swaps]) if swaps else None
    swap_cuts = {s["cut"]["name"] for s in rec.swaps}
    for raw in obj.get("cuts_to_consider") or []:
        doc, e = build_cut(raw, ctx)
        if e and drop_bad_cuts:
            LOG.warning("Pass C: dropped cuts_to_consider entry after the last attempt: %s", "; ".join(e))
            continue
        errs += e
        if doc and doc["card"]["name"] in swap_cuts and drop_bad_cuts:
            LOG.warning("Pass C: dropped cuts_to_consider entry that repeats a swap's cut: %r", doc["card"]["name"])
        elif doc and doc["card"]["name"] in swap_cuts:
            errs.append(f"cuts_to_consider: {doc['card']['name']!r} is already the cut of a swap; list only cards no swap removes")
        elif doc:
            rec.cuts_to_consider.append(doc)
    return rec, errs


# ------------------------------------------------------------------------------------------------- the pass
def build_request(ctx: ReviewContext, brief: Mapping, analysis: b_analysis.AnalysisResult, view: PoolView,
                  config: cfg.PipelineConfig) -> LLMRequest:
    t = prompts.load("c_recommend", config.prompt_version("c_recommend"))
    return LLMRequest(
        model=config.model(PASS),
        system_blocks=system_blocks(ctx, brief, analysis.strengths, analysis.problems, analysis.sidecar, view, config),
        messages=(Message("user", t.render("user", deck_name=ctx.deck_name, commanders=", ".join(ctx.commanders))),),
        schema_name="recommendations", schema=portable_schema(schemas.RECOMMEND_SCHEMA), max_tokens=config.max_tokens_c,
        cache_blocks=3, label=PASS)


def run(ctx: ReviewContext, brief: Mapping, analysis: b_analysis.AnalysisResult, view: PoolView, client: LLMClient,
        config: cfg.PipelineConfig, rec: TraceRecorder) -> tuple[Recommendations, int]:
    """Returns (recommendations, trace step index of the call that produced them)."""
    problem_ids = {f["id"] for f in analysis.problems}
    req = build_request(ctx, brief, analysis, view, config)
    holder: dict[str, Recommendations] = {}
    calls = {"n": 0}

    def validate(obj: Any) -> list[str]:
        calls["n"] += 1
        errs = validate_schema(obj, schemas.RECOMMEND_SCHEMA)
        if errs:
            return errs
        out, errs = materialise(obj, ctx, view, problem_ids, drop_bad_cuts=calls["n"] > config.max_repairs)
        holder["r"] = out
        return errs

    obj, step = traced_call(client, req, validate=validate, max_repairs=config.max_repairs, rec=rec, pass_name=PASS,
                            axis=None, prompt_version=config.prompt_version("c_recommend"))
    result = holder["r"]
    for s in result.swaps:
        rec.link_finding(s["id"], step, kept=True)
    return result, step
