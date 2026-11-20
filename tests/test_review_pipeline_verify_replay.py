"""Replay of two recorded LIVE Pass A-D runs (no network, no API keys).

Fixtures (tests/fixtures/review_pipeline/abcd_*.json.gz) were captured by ``scripts/review_live_abcd.py``:
  * ``abcd_saffi``: the critic flagged 9 items; all were regenerated (incl. a second loop on a swap and two on ``top_changes``).
  * ``abcd_lara``:  the critic flagged 12 items; 2 were still failing after the loop cap / withdrawn and were REMOVED into ``omitted[]``.

Every recorded reply is pinned to its request hash: a prompt, schema, profile or fact-pack change that alters a request fails here
with the re-record instruction instead of replaying a reply written for a different prompt.
"""
from __future__ import annotations

import gzip
import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.server.domain.analytics.review_pipeline import config, context, llm, runner
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp
from tests.test_review_pipeline_replay import parse_decklist

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures" / "review_pipeline"
REREC = "re-record: OPENROUTER_API_KEY=... python -m scripts.review_live_abcd <deck> OUT (see docs/reviewer/PIPELINE_CD.md)"


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
        return llm.LLMResponse(text=r["text"], usage=llm.Usage.from_dict(r["usage"]), model=r["response_model"], latency_ms=r["latency_ms"])


@lru_cache(maxsize=None)
def replay(name: str):
    with gzip.open(FX / f"abcd_{name}.json.gz", "rt", encoding="utf-8") as g:
        rec = json.load(g)
    d = ROOT / "tests" / "review_evals" / "decks" / rec["deck"]
    cmd, cards = parse_decklist(d / "decklist.txt")
    meta = json.loads((d / "intent.json").read_text())
    profile = pp.profile_from_seed(json.loads((FX / "profile_seed.json").read_text()))
    ctx = context.ReviewContext.build(
        deck_id=meta["deck_id"], deck_name=meta["deck_name"], fact_pack=rec["fact_pack"], deck_cards=cmd + cards,
        commanders=[n for n, _ in cmd], profile=profile, intent=UserDeckIntent(), owned=None,
        extra_foreign_names=[c["name"] for c in rec["pool"]["cards"]])
    cfg = config.PipelineConfig.from_env({f"REVIEW_MODEL_{k}": v for k, v in rec["models"].items() if k in ("A", "B", "C")}
                                         | {"REVIEW_MODEL_CRITIC": rec["models"]["critic"]})
    client = Replay(rec["replies"])
    return rec, ctx, cfg, client, runner.run_abcd(ctx, client, rec["pool"], cfg)


class RegenerationLoop(unittest.TestCase):
    """Saffi: flagged items are rewritten (only those items), re-judged, and ship."""

    @classmethod
    def setUpClass(cls):
        cls.rec, cls.ctx, cls.cfg, cls.client, cls.res = replay("saffi")

    def test_every_recorded_call_is_replayed_with_matching_hashes(self):
        self.assertEqual(len(self.client.seen), len(self.rec["replies"]))
        self.assertEqual(self.res.trace.status, "ok")

    def test_critic_flags_are_regenerated_and_nothing_is_removed(self):
        s = self.res.verified.stats
        self.assertEqual((s["critic_flagged_items"], s["regenerated_ok"], s["removed"]), (9, 11, 0))
        self.assertEqual(self.res.verified.omitted, [])

    def test_only_flagged_items_are_regenerated(self):
        regen = {r.label.split(":")[2] for r in self.client.seen if r.label.startswith("D:regen")}
        flagged = {e["item"] for e in self.res.verified.log}
        self.assertEqual(regen, flagged)
        self.assertLess(len(regen), len(self.res.verified.problems) + len(self.res.verified.strengths) + len(self.res.verified.swaps))

    def test_loop_is_bounded_at_two_rounds(self):
        rounds = [int(r.label.rsplit(":", 1)[1]) for r in self.client.seen if r.label.startswith("D:regen")]
        self.assertEqual(max(rounds), 2)

    def test_regenerated_swap_got_a_second_loop_and_shipped_with_a_fix(self):
        labels = [r.label for r in self.client.seen]
        self.assertIn("D:regen:sw-1:1", labels)
        self.assertIn("D:regen:sw-1:2", labels)
        sw1 = next(s for s in self.res.verified.swaps if s["id"] == "sw-1")
        draft = next(s for s in self.res.recommendations.swaps if s["id"] == "sw-1")
        self.assertNotEqual(sw1["tradeoff"], draft["tradeoff"], "the shipped swap is the rewrite, not the flagged draft")

    def test_shipped_review_has_top_changes_of_three_surviving_swaps(self):
        top = self.res.verified.top_changes
        self.assertEqual(len(top["swap_ids"]), 3)
        self.assertTrue(set(top["swap_ids"]) <= {s["id"] for s in self.res.verified.swaps})


class Removal(unittest.TestCase):
    """Lara: an item that is still failing (or withdrawn by its generator) is removed and recorded, never shipped."""

    @classmethod
    def setUpClass(cls):
        cls.rec, cls.ctx, cls.cfg, cls.client, cls.res = replay("lara")

    def test_every_recorded_call_is_replayed_with_matching_hashes(self):
        self.assertEqual(len(self.client.seen), len(self.rec["replies"]))

    def test_two_items_removed_with_reasons(self):
        v = self.res.verified
        self.assertEqual(v.stats["removed"], 2)
        self.assertEqual({o["kind"] for o in v.omitted}, {"finding", "claim"})
        self.assertTrue(all(o["reason"] for o in v.omitted))

    def test_removed_finding_is_in_no_main_section(self):
        v = self.res.verified
        ids = [f["id"] for f in v.strengths + v.problems] + [s["id"] for s in v.swaps]
        self.assertNotIn("p-concept_execution-2", ids)
        self.assertNotIn("p-concept_execution-2", json.dumps([v.swaps, v.top_changes]))

    def test_top_changes_removed_after_two_failed_loops_is_not_shipped(self):
        self.assertIsNone(self.res.verified.top_changes)
        loops = [e for e in self.res.verified.log if e["item"] == "top_changes"]
        self.assertEqual([e["outcome"] for e in loops], ["regenerated", "regenerated", "removed"])

    def test_omitted_entries_name_the_critic_category(self):
        self.assertTrue(any("unsupported_claim" in o["reason"] for o in self.res.verified.omitted))
        self.assertTrue(any("tradeoff_missing" in o["reason"] for o in self.res.verified.omitted))


class CriticIndependence(unittest.TestCase):
    def test_recorded_critic_is_a_different_family_from_every_generator(self):
        for name in ("saffi", "lara"):
            rec, _, cfg, *_ = replay(name)
            cfg.check_critic_is_independent()
            critic_calls = {r["model"] for r in rec["replies"] if r["label"].startswith("critic")}
            gen_calls = {r["model"] for r in rec["replies"] if not r["label"].startswith("critic")}
            self.assertFalse(critic_calls & gen_calls)


if __name__ == "__main__":
    unittest.main()
