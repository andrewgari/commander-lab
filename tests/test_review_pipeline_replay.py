"""Replay of ONE recorded live Pass A + Pass B run (Lara, golden deck). No network, no API keys.

Fixtures (tests/fixtures/review_pipeline/): the model replies and the fact pack captured by
``scripts/review_live_ab.py lara-uncharted OUT``. If a prompt, schema, fact-pack rendering or profile
change alters a request, the input-hash check fails with the re-record instruction rather than silently
replaying a reply that was written for a different prompt.
"""
from __future__ import annotations

import gzip
import json
import re
import unittest
from pathlib import Path

from src.server.domain.analytics.review_pipeline import config, context, runner
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp
from src.server.domain.analytics.review_pipeline import llm

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures" / "review_pipeline"
DECK = ROOT / "tests" / "review_evals" / "decks" / "lara-uncharted"
REREC = "re-record: OPENROUTER_API_KEY=... python scripts/review_live_ab.py lara-uncharted OUT (see script docstring)"


def parse_decklist(path: Path):
    cmd, cards, sect = [], [], None
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            sect = "cmd" if "Commander" in line and ":" in line else ("main" if "Mainboard" in line else sect)
            continue
        m = re.match(r"(\d+)\s+(.+)", line.strip())
        if m:
            (cmd if sect == "cmd" else cards).append((m.group(2), int(m.group(1))))
    return cmd, cards


class Replay:
    def __init__(self, replies):
        self.queues: dict[str, list] = {}
        for r in replies:
            self.queues.setdefault(r["label"], []).append(r)
        self.seen: list[llm.LLMRequest] = []

    def complete(self, request):
        self.seen.append(request)
        q = self.queues.get(request.label)
        if not q:
            raise AssertionError(f"no recorded reply for call {request.label!r}; {REREC}")
        r = q.pop(0)
        if r["input_hash"] != request.input_hash:
            raise AssertionError(f"request {request.label!r} no longer matches the recording; {REREC}")
        return llm.LLMResponse(text=r["text"], usage=llm.Usage.from_dict(r["usage"]), model=r["response_model"],
                               latency_ms=r["latency_ms"])


def build():
    rec = json.loads((FX / "lara_uncharted_replies.json").read_text())
    with gzip.open(FX / "lara_uncharted_fact_pack.json.gz", "rt", encoding="utf-8") as g:
        pack = json.load(g)
    cmd, cards = parse_decklist(DECK / "decklist.txt")
    meta = json.loads((DECK / "intent.json").read_text())
    profile = pp.profile_from_seed(json.loads((FX / "profile_seed.json").read_text()))
    ctx = context.ReviewContext.build(deck_id=meta["deck_id"], deck_name=meta["deck_name"], fact_pack=pack,
                                      deck_cards=cmd + cards, commanders=[n for n, _ in cmd], profile=profile,
                                      intent=UserDeckIntent(), owned=None)
    cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_A": rec["replies"][0]["model"],
                                          "REVIEW_MODEL_B": next(r["model"] for r in rec["replies"] if r["label"].startswith("B:"))})
    return rec, ctx, cfg


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec, cls.ctx, cls.cfg = build()
        cls.client = Replay(cls.rec["replies"])
        cls.res = runner.run_ab(cls.ctx, cls.client, cls.cfg)

    def test_replays_every_recorded_call_with_matching_request_hashes(self):
        self.assertEqual(len(self.client.seen), len(self.rec["replies"]))
        self.assertEqual(self.res.trace.status, "ok")
        self.assertEqual(self.res.trace.inputs["fact_pack_hash"], self.rec["recorded_with"]["fact_pack_hash"])

    def test_one_axis_score_per_axis_and_brief_has_open_questions(self):
        self.assertEqual({a["axis"] for a in self.res.analysis.axes}, {a.id for a in config.AXES})
        self.assertTrue(all(1 <= a["score"] <= 10 and a["one_line"] for a in self.res.analysis.axes))
        self.assertTrue(self.res.brief["questions_for_owner"])

    def test_no_kept_finding_references_a_card_outside_the_deck(self):
        deck = {n for n, _ in self.ctx.deck_cards}
        for f in self.res.analysis.strengths + self.res.analysis.problems:
            for c in f["evidence"]["cards"]:
                self.assertIn(self.ctx.resolver.resolve(c["name"]), deck, f["id"])

    def test_every_kept_finding_is_traceable_to_prompt_version_and_model(self):
        t = self.res.trace
        for f in self.res.analysis.strengths + self.res.analysis.problems:
            p = t.provenance_of(f["id"])
            self.assertEqual(p["pass"], "B")
            self.assertEqual(p["prompt_version"], "v1")
            self.assertEqual(p["model"], self.cfg.models["B"])
            self.assertTrue(p["kept"])
            self.assertTrue(f["id"].split("-")[1].startswith(p["axis"]))

    def test_recorded_run_keeps_the_cache_warm_after_the_first_axis_call(self):
        cached = [s["cached_tokens"] or 0 for s in self.res.trace.steps if s["pass"] == "B"]
        self.assertGreater(sum(1 for c in cached if c > 0), 0)

    def test_drops_are_logged_with_reason(self):
        for d in self.res.trace.drops:
            self.assertTrue(d["reason"])
            self.assertIn("finding", d)


if __name__ == "__main__":
    unittest.main()
