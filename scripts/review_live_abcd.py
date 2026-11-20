"""Live Pass A-D run on one golden deck: pool -> A -> B -> C -> D -> deterministic gates. Not run in CI (live LLM calls).

    OPENROUTER_API_KEY=... REVIEW_MODEL_A=openrouter:anthropic/claude-sonnet-5.5 REVIEW_MODEL_B=openrouter:anthropic/claude-opus-5.5 \
    REVIEW_MODEL_C=openrouter:anthropic/claude-opus-5.5 REVIEW_MODEL_CRITIC=openrouter:google/gemini-3.1-pro-preview \
    python scripts/review_live_abcd.py lara-uncharted OUTDIR

Writes OUTDIR/{trace,result,fixture,fact_pack,pool,case,verification}.json. ``case.json`` is a tests/review_evals case, so
``python -m tests.review_evals.run_gates --dir OUTDIR/gates`` runs the SAME gates as `make eval-gates` over the live review.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.review_live_ab import Recorder, parse_decklist
from src.server.domain.analytics import candidates
from src.server.domain.analytics.fact_pack import build_fact_pack
from src.server.domain.analytics.fact_sources import default_fetchers, scryfall_search
from src.server.domain.analytics.review_pipeline import checks, config as cfg, context, runner
from src.server.domain.analytics.review_pipeline.names import normalize
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp
from src.server.infra.llm.provider import RoutingClient

ROOT = Path(__file__).resolve().parents[1]


def _retry_on_429() -> None:
    """Live runs share an IP with other jobs; honour Scryfall's Retry-After instead of building a degraded fact pack."""
    import time

    import requests

    for verb in ("get", "post"):
        orig = getattr(requests, verb)

        def wrapped(*a, _orig=orig, **kw):
            for _ in range(12):
                r = _orig(*a, **kw)
                if r.status_code != 429:
                    return r
                time.sleep(min(float(r.headers.get("Retry-After") or 30) + 2, 90))
            return r
        setattr(requests, verb, wrapped)


def main(slug: str, out: Path) -> int:
    _retry_on_429()
    d = ROOT / "tests/review_evals/decks" / slug
    cmd, cards = parse_decklist(d / "decklist.txt")
    deck = {"commanders": cmd, "cards": cmd + cards}
    pack = build_fact_pack(deck)
    meta = json.loads((d / "intent.json").read_text())
    deck_cards = [(c["name"], c["quantity"]) for c in cmd + cards]
    profile = pp.profile_from_seed(json.loads((ROOT / "tests/fixtures/review_pipeline/profile_seed.json").read_text()))
    intent = UserDeckIntent()
    f = default_fetchers()
    pool = candidates.candidate_pool(deck, intent, profile, sources=candidates.CandidateSources(
        edhrec=lambda names: __import__("src.server.domain.analytics.providers.edhrec", fromlist=["x"]).EDHRecClient().get_commander_page(list(names)),
        scryfall_batch=f["scryfall_batch"], scryfall_search=scryfall_search), owned=None)
    ctx = context.ReviewContext.build(deck_id=meta["deck_id"], deck_name=meta["deck_name"], fact_pack=pack, deck_cards=deck_cards,
                                      commanders=[c["name"] for c in cmd], profile=profile, intent=intent, owned=None,
                                      extra_foreign_names=[c["name"] for c in pool.cards])
    out.mkdir(parents=True, exist_ok=True)
    unavailable = [c["name"] for c in pack["cards"] if isinstance(c.get("color_identity"), dict)]
    print("no-identity cards:", unavailable)
    if not pool.cards or len(unavailable) > max(6, len(pack["cards"]) // 10):  # a few "A // B" DFC names have no identity (known fact-pack gap)  # a run on degraded inputs (Scryfall rate limit / EDHREC down) proves nothing: refuse it
        print(f"ABORT: degraded inputs (pool cards={len(pool.cards)}, cards without Scryfall color identity={len(unavailable)}); "
              f"pool notes={pool.notes}. Wait for the Scryfall rate limit to clear and rerun, one deck at a time.")
        return 2
    (out / "pool.json").write_text(json.dumps(pool.to_dict(), ensure_ascii=False, indent=1))
    (out / "fact_pack.json").write_text(json.dumps(dict(pack), ensure_ascii=False))
    rec, sink = Recorder(RoutingClient.from_env()), []
    config = cfg.PipelineConfig.from_env()
    try:
        res = runner.run_abcd(ctx, rec, pool, config, on_trace=sink.append)
    finally:
        if sink:
            (out / "trace.json").write_text(json.dumps(sink[0].to_dict(), indent=1))
        (out / "fixture.json").write_text(json.dumps({"deck": slug, "replies": rec.log}, indent=1))
    v = res.verified
    result = {"brief": res.brief, **res.document_sections(), "sidecar": v.sidecar, "stats": v.stats, "log": v.log,
              "draft_swaps": res.recommendations.swaps, "draft_cuts": res.recommendations.cuts_to_consider}
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    # --- deterministic gates over the SHIPPED sections, through the same library `make eval-gates` uses -----------------
    scry = {}
    for c in pack["cards"]:
        scry[c["name"]] = {"color_identity": c.get("color_identity") or [], "legalities": c.get("legalities") or {}}
    for c in pool.cards:
        scry[c["name"]] = {"color_identity": c["color_identity"], "legalities": c["legalities"]}
    names = []
    for n, q in deck_cards:
        names += [n] * q
    gdeck = {"commanders": [c["name"] for c in cmd], "cards": names, "pet_cards": list(ctx.pet_cards), "budget_hard_cap": ctx.hard_cap_usd,
             "rejected_swaps": [list(p) for p in ctx.rejected_swaps], "candidate_pool": sorted(pool.names()),
             "game_changers": list(ctx.game_changers or [])}
    review = {"meta": {"commanders": [{"name": c["name"]} for c in cmd], "deck_id": meta["deck_id"], "deck_name": meta["deck_name"],
                       "fact_pack_hash": "", "pipeline_version": cfg.PIPELINE_VERSION},
              **res.document_sections(), "bracket": {"estimated": ctx.estimated_bracket or 1, "target": ctx.target_bracket, "reasons": []},
              "charts": {}}
    fresh = checks.CheckContext(deck=gdeck, scryfall=scry)
    violations = checks.run_checks(review, fresh, checks.PIPELINE_CHECKS)
    case_dir = out / "gates"
    case_dir.mkdir(exist_ok=True)
    from tests.review_evals.gates import fresh_fact_pack
    ff = fresh_fact_pack({"commanders": gdeck["commanders"], "cards": list(dict.fromkeys(names))}, scry)
    review["meta"]["fact_pack_hash"], review["charts"] = ff.hash, ff.get("charts")
    (case_dir / f"{slug}.json").write_text(json.dumps({"deck": {**gdeck, "cards": list(dict.fromkeys(names))}, "scryfall": scry, "review": review}, indent=1))
    summary = {"deck": slug, "status": res.trace.status, "cost_usd": res.trace.cost_usd, "steps": len(res.trace.steps), "models": res.trace.models,
               "swaps": len(v.swaps), "problems": len(v.problems), "strengths": len(v.strengths), "omitted": v.omitted, "stats": v.stats,
               "pool_cards": len(pool.cards), "gate_violations": [str(x) for x in violations]}
    (out / "verification.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in ("deck", "status", "cost_usd", "steps", "swaps", "problems", "pool_cards", "gate_violations")}))
    print("stats", json.dumps(v.stats)[:900])
    for o in v.omitted:
        print("OMITTED", o["kind"], o["item"][:90], "|", o["reason"][:200])
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2])))
