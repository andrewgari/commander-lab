"""Live Pass A+B run on one golden deck; records every model reply as a replay fixture.

    OPENROUTER_API_KEY=... REVIEW_MODEL_A=openrouter:anthropic/claude-sonnet-5.5 \
    REVIEW_MODEL_B=openrouter:anthropic/claude-opus-5.5 python scripts/review_live_ab.py lara-uncharted OUTDIR

Writes OUTDIR/{trace.json,result.json,fixture.json,fact_pack.json}. Not run in CI (live LLM calls). To refresh the replay fixtures, copy fixture.json to
tests/fixtures/review_pipeline/lara_uncharted_replies.json (plus recorded_with from trace.json) and fact_pack.json gzipped next to it."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from src.server.domain.analytics.fact_pack import build_fact_pack
from src.server.domain.analytics.review_pipeline import config as cfg, context, runner
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp
from src.server.infra.llm.provider import RoutingClient

ROOT = Path(__file__).resolve().parents[1]


def parse_decklist(path: Path):
    cmd, cards, sect = [], [], None
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            sect = "cmd" if "Commander" in line and ":" in line else ("main" if "Mainboard" in line else sect)
            continue
        m = re.match(r"(\d+)\s+(.+)", line.strip())
        if m:
            (cmd if sect == "cmd" else cards).append({"name": m.group(2), "quantity": int(m.group(1))})
    return cmd, cards


class Recorder:
    def __init__(self, inner):
        self.inner, self.log = inner, []

    def complete(self, request):
        resp = self.inner.complete(request)
        self.log.append({"label": request.label, "model": request.model, "text": resp.text,
                         "input_hash": request.input_hash, "usage": resp.usage.to_dict(), "response_model": resp.model, "latency_ms": resp.latency_ms})
        return resp


def main(slug: str, out: Path) -> None:
    d = ROOT / "tests/review_evals/decks" / slug
    cmd, cards = parse_decklist(d / "decklist.txt")
    deck = {"commanders": cmd, "cards": cmd + cards}
    pack = build_fact_pack(deck)
    meta = json.loads((d / "intent.json").read_text())
    deck_cards = [(c["name"], c["quantity"]) for c in cmd + cards]
    profile = pp.profile_from_seed(json.loads((ROOT / "tests/fixtures/review_pipeline/profile_seed.json").read_text()))
    ctx = context.ReviewContext.build(deck_id=meta["deck_id"], deck_name=meta["deck_name"], fact_pack=pack, deck_cards=deck_cards,
                                      commanders=[c["name"] for c in cmd], profile=profile, intent=UserDeckIntent(), owned=None)
    rec = Recorder(RoutingClient.from_env())
    sink = []
    out.mkdir(parents=True, exist_ok=True)
    (out / "fact_pack.json").write_text(json.dumps(pack.data if hasattr(pack, "data") else pack, ensure_ascii=False))
    try:
        res = runner.run_ab(ctx, rec, cfg.PipelineConfig.from_env(), on_trace=sink.append)
    finally:
        out.mkdir(parents=True, exist_ok=True)
        if sink:
            (out / "trace.json").write_text(json.dumps(sink[0].to_dict(), indent=1))
        (out / "fixture.json").write_text(json.dumps({"deck": slug, "replies": rec.log}, indent=1))
    (out / "result.json").write_text(json.dumps({"brief": res.brief, "scorecard": res.analysis.axes, "strengths": res.analysis.strengths,
                                                 "problems": res.analysis.problems, "sidecar": res.analysis.sidecar}, indent=1, ensure_ascii=False))
    t = res.trace
    print("status", t.status, "cost", t.cost_usd, "steps", len(t.steps), "drops", len(t.drops))
    for dr in t.drops:
        print("DROP", dr["axis"], dr["reason"], dr["detail"])
    for a in res.analysis.axes:
        print(a["axis"], a["score"], a["one_line"][:90])


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
