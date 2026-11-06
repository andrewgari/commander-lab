"""Slice 3 pipeline orchestration (ARCHITECTURE.md §6), pure:

    fact pack -> candidates -> verifier -> writer -> doc

Every external capability is injected: the LLM `writer` (a callable taking
the writer payload and returning the raw reply text) and the Scryfall
`resolver`. With both absent the pipeline still produces a complete stored
review: facts + "explanation offline" (REVIEW_SPEC core rule).

Returns {fact_pack, analysis, doc, jobs, status}. `jobs` is the review_jobs
audit trail (one row per stage); `status` is complete | partial.
"""
from __future__ import annotations

from typing import Callable, List, Mapping, Optional

from . import candidates as candidates_mod
from . import doc as doc_mod
from . import fact_pack as fact_pack_mod
from . import verifier as verifier_mod
from . import writer as writer_mod


def _job(stage: str, status: str, error: Optional[str] = None) -> dict:
    return {"stage": stage, "status": status, "error": error}


def run(deck: Mapping, texts: Mapping, *, deck_id: str,
        combos: Optional[Mapping] = None,
        combos_raw: Optional[Mapping] = None,
        edhrec_page: Optional[Mapping] = None,
        have_buy: Optional[Mapping] = None,
        snapshot: Optional[Mapping] = None,
        overrides: Optional[Mapping] = None,
        owned: Optional[Mapping] = None,
        collection_texts: Optional[Mapping] = None,
        color_identity: Optional[List[str]] = None,
        card_info: Optional[Mapping] = None,
        pets: Optional[List[str]] = None,
        journal: Optional[Mapping] = None,
        writer: Optional[Callable[[Mapping], str]] = None,
        resolver: Optional[verifier_mod.Resolver] = None) -> dict:
    jobs: List[dict] = []

    # -- fact pack (code only) -------------------------------------------------
    pack = fact_pack_mod.build_fact_pack(
        deck, texts, combos=combos, combos_raw=combos_raw, edhrec_page=edhrec_page,
        have_buy=have_buy, snapshot=snapshot, overrides=overrides, pets=pets, journal=journal,
    )
    degraded = []
    combos_err = (combos or {}).get("error")
    edhrec_err = (edhrec_page or {}).get("error")
    if combos_err:
        degraded.append(f"combos: {combos_err}")
    if edhrec_err:
        degraded.append(f"edhrec: {edhrec_err}")
    unknown = (pack.get("curve_tempo") or {}).get("unknown_cards") or []
    if unknown:
        degraded.append(f"no reference data for {len(unknown)} card(s)")
    jobs.append(_job("fact_pack", "degraded" if degraded else "ok",
                     "; ".join(degraded) or None))

    # -- candidate retrieval + recommendations (code) ---------------------------
    analysis = candidates_mod.retrieve(
        pack, deck, edhrec_page=edhrec_page, owned=owned or {},
        collection_texts=collection_texts, color_identity=color_identity or [],
        card_info=card_info,
    )
    src = analysis.get("sources") or {}
    cand_degraded = [k for k, v in src.items() if v.get("status") != "ok"]
    jobs.append(_job(
        "candidates", "degraded" if cand_degraded else "ok",
        "; ".join(f"{k}: {src[k].get('reason')}" for k in cand_degraded) or None,
    ))

    # -- verifier (code): every name against Scryfall ---------------------------
    if resolver is not None:
        analysis, analysis_audit = verifier_mod.verify_analysis(analysis, resolver)
        jobs.append(_job("verifier", "degraded" if analysis_audit["rejected"] else "ok",
                         f"rejected: {', '.join(analysis_audit['rejected'])}"
                         if analysis_audit["rejected"] else None))
    else:
        analysis_audit = {"checked": 0, "rejected": [], "renamed": {},
                          "rule": "verifier not run (no resolver)"}
        jobs.append(_job("verifier", "degraded", "no Scryfall resolver; names unverified"))

    # -- writer (LLM, prose/judgment only over verified facts) ------------------
    explanations = None
    writer_error: Optional[str] = None
    if writer is not None and resolver is not None:
        payload = writer_mod.build_writer_payload(pack, analysis)
        try:
            reply_text = writer(payload)
            reply = writer_mod.parse_reply(reply_text)
            allowed = set(writer_mod.allowed_card_names(pack, analysis))
            verified, expl_audit = verifier_mod.verify_explanations(
                {k: b.model_dump() for k, b in reply.items()}, allowed, resolver)
            explanations = writer_mod.apply_reply(reply, verified)
            jobs.append(_job("writer", "degraded" if expl_audit["rejected"] else "ok",
                             f"rejected cards: {', '.join(expl_audit['rejected'])}"
                             if expl_audit["rejected"] else None))
        except Exception as exc:  # noqa: BLE001 - an LLM failure degrades, never kills, the review
            writer_error = f"{type(exc).__name__}: {exc}"[:300]
    elif writer is None:
        writer_error = "LLM writer not configured"
    else:
        writer_error = "no Scryfall resolver; writer skipped (LLM never sees unverified data)"
    if explanations is None:
        explanations = writer_mod.offline_blocks(writer_error or "writer unavailable")
        jobs.append(_job("writer", "degraded", writer_error))

    # -- doc assembly (typed sections, code) ------------------------------------
    doc = doc_mod.build_doc(
        deck_id, pack["fingerprint"], pack, analysis, explanations,
        verification={"analysis": analysis_audit},
    )

    status = "complete" if all(j["status"] == "ok" for j in jobs) else "partial"
    return {"fact_pack": pack, "analysis": analysis, "doc": doc, "jobs": jobs, "status": status}
