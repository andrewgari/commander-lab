"""DT-7: recommendation explanations + reconsider / "recommend something else".

Overlay spec §3.3 (docs/architecture/deck-tuner-overlay-spec.md): every swap row
carries an entry id, a status (open|accepted|dismissed|ignored) and a short
explanation — LLM prose when cached, else the deterministic fallback built from
the swap's own computed fields. "Reconsider" stores its answer on the entry;
"recommend something else" marks the entry ignored (kept in entry_log) and
appends the replacement under a fresh id.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod
from app import app
from analytics import tune_store, tuner
from fastapi.testclient import TestClient
from tests.test_tuner import CAPS, COMP, DECK, WEAK, build, cand

DECK_KEY = "archidekt:417707"
ALPHA = cand("Alpha Bomb", 4, 0.9, 0.4, owned=True, available=1)
BETA = cand("Beta Bomb", 3, 0.8, 0.3, owned=True, available=1)


class FakeRedis:
    """Strings + lists + a buffered pipeline (the ops tune_store / tuner state use)."""

    def __init__(self):
        self.store, self.lists = {}, {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def mget(self, ks):
        return [self.store.get(k) for k in ks]

    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)
            self.lists.pop(k, None)

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def lrange(self, k, a, b):
        items = self.lists.get(k, [])
        return items[a:] if b == -1 else items[a:b + 1]

    def ltrim(self, k, a, b):
        items = self.lists.get(k, [])
        self.lists[k] = items[a:] if b == -1 else items[a:b + 1]

    def pipeline(self):
        return _Pipe(self)


class _Pipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def set(self, *a, **k):
        self.ops.append(("set", a, k))
        return self

    def lpush(self, *a):
        self.ops.append(("lpush", a, {}))
        return self

    def execute(self):
        for name, a, k in self.ops:
            getattr(self.r, name)(*a, **k)
        self.ops = []


class StubChain:
    """LLMChain stand-in: always 'available', returns a fixed JSON payload."""

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def available(self):
        return [self]

    def generate_json(self, system, prompt, schema=None, purpose="generate", on_call=None):
        self.prompts.append(prompt)
        if on_call:
            on_call({"provider": "stub", "model": "m1", "ok": True, "purpose": purpose,
                     "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0})
        return self.payload, "stub", "m1"


def real_compute(r, deck, mode="flex", in_deck=True, buy=True, goal="balanced", **kw):
    """What tuner.compute would return for the test fixtures, honouring state."""
    return tuner.build_tune(deck, WEAK, COMP, [dict(ALPHA), dict(BETA)], CAPS, p75=4.0,
                            has_pillars=True, has_edhrec=True, mode=mode, in_deck=in_deck,
                            buy=buy, goal=goal, state=tuner.load_state(r, deck))


def save_payload(r, cands=None, strip_entries=False):
    payload = build([dict(c) for c in (cands or [ALPHA, BETA])])
    if strip_entries:  # a record saved before DT-7 existed
        for s in payload["swaps"]:
            for k in ("entry_id", "status", "reason"):
                s.pop(k, None)
    rec, _ = tune_store.save_active(r, DECK_KEY, payload,
                                    {"mode": "flex", "in_deck": True, "buy": True, "goal": "balanced"})
    return rec


class TestEntryFields(unittest.TestCase):
    def test_every_swap_has_id_status_reason(self):
        out = build([dict(ALPHA), dict(BETA)])
        self.assertTrue(out["swaps"])
        for s in out["swaps"]:
            self.assertTrue(s["entry_id"])
            self.assertEqual(s["status"], "open")
            self.assertTrue(s["reason"])
            self.assertIn(s["own"]["name"], s["reason"])

    def test_entry_id_is_deterministic(self):
        self.assertEqual(tuner.entry_id("A", "B"), tuner.entry_id("A", "B"))
        self.assertNotEqual(tuner.entry_id("A", "B"), tuner.entry_id("A", "C"))

    def test_reason_names_the_kept_role(self):
        out = build([cand("Removal Bomb", 4, 0.9, 0.4, owned=True, available=1,
                          rows=("targeted_disruption",))])
        s = next(x for x in out["swaps"] if x["cut"]["name"] == "Weak Removal")
        self.assertIn("targeted disruption", s["reason"])

    def test_accepted_decision_sets_status(self):
        st = {"review_id": None, "decisions": {"Weak Plan": {"decision": "own", "add": "Alpha Bomb"}},
              "adds": [], "ignored": {}}
        out = build([dict(ALPHA)], state=st)
        s = next(x for x in out["swaps"] if x["cut"]["name"] == "Weak Plan")
        self.assertEqual(s["status"], "accepted")

    def test_fallback_reason_empty_without_option(self):
        self.assertEqual(tuner.fallback_reason({"cut": {"name": "X"}}), "")


class TestIgnored(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_decide_ignored_records_add_and_clears_accept(self):
        tuner.decide(self.r, DECK, "Weak Plan", "own", "Alpha Bomb")
        st = tuner.decide(self.r, DECK, "Weak Plan", "ignored", "Alpha Bomb")
        self.assertEqual(st["ignored"], {"Weak Plan": ["Alpha Bomb"]})
        self.assertNotIn("Weak Plan", st["decisions"])  # the accept is gone
        again = tuner.decide(self.r, DECK, "Weak Plan", "ignored", "Alpha Bomb")
        self.assertEqual(again["ignored"]["Weak Plan"], ["Alpha Bomb"])  # no duplicate
        self.assertEqual(tuner.load_state(self.r, DECK)["ignored"], st["ignored"])  # persisted

    def test_decide_ignored_needs_the_add(self):
        with self.assertRaises(tuner.TunerError):
            tuner.decide(self.r, DECK, "Weak Plan", "ignored")

    def test_ignored_add_is_filtered_for_that_slot(self):
        base = build([dict(ALPHA), dict(BETA)])
        s = next(x for x in base["swaps"] if x["cut"]["name"] == "Weak Plan")
        self.assertEqual(s["own"]["name"], "Alpha Bomb")
        st = {"review_id": None, "decisions": {}, "adds": [], "ignored": {"Weak Plan": ["Alpha Bomb"]}}
        out = build([dict(ALPHA), dict(BETA)], state=st)
        s = next(x for x in out["swaps"] if x["cut"]["name"] == "Weak Plan")
        self.assertEqual(s["own"]["name"], "Beta Bomb")

    def test_ignoring_the_last_candidate_drops_the_swap(self):
        st = {"review_id": None, "decisions": {}, "adds": [], "ignored": {"Weak Plan": ["Alpha Bomb"]}}
        out = build([dict(ALPHA)], state=st)
        self.assertIsNone(next((x for x in out["swaps"] if x["cut"]["name"] == "Weak Plan"), None))


class TestReconsider(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_no_saved_rec_raises(self):
        with self.assertRaises(tuner.TunerError):
            tuner.reconsider(self.r, DECK, "Weak Plan", None)

    def test_unknown_cut_raises(self):
        save_payload(self.r)
        with self.assertRaises(tuner.TunerError):
            tuner.reconsider(self.r, DECK, "No Such Card", None)

    def test_fallback_without_llm_is_stored_and_cached(self):
        save_payload(self.r)
        out = tuner.reconsider(self.r, DECK, "Weak Plan", None)
        self.assertEqual(out["llm"]["status"], "no_llm")
        self.assertIn("recommendation stands", out["response"])
        self.assertEqual(out["add"], "Alpha Bomb")
        stored = tune_store.get_active(self.r, DECK_KEY)["payload"]["swaps"]
        row = next(s for s in stored if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["reconsideration"], out["response"])
        again = tuner.reconsider(self.r, DECK, "Weak Plan", None)
        self.assertEqual(again["llm"]["status"], "cached")
        self.assertEqual(again["response"], out["response"])

    def test_llm_answer_wins_and_is_stored(self):
        save_payload(self.r)
        chain = StubChain({"response": "It turns every dragon draw into immediate pressure."})
        out = tuner.reconsider(self.r, DECK, "Weak Plan", chain, "Thesis: beatdown")
        self.assertEqual(out["llm"]["status"], "ok")
        self.assertEqual(out["response"], "It turns every dragon draw into immediate pressure.")
        self.assertIn("Weak Plan", chain.prompts[0])
        self.assertIn("Alpha Bomb", chain.prompts[0])
        row = next(s for s in tune_store.get_active(self.r, DECK_KEY)["payload"]["swaps"]
                   if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["reconsideration"], out["response"])

    def test_llm_garbage_falls_back(self):
        save_payload(self.r)
        out = tuner.reconsider(self.r, DECK, "Weak Plan", StubChain({"nope": 1}))
        self.assertEqual(out["llm"]["status"], "llm_failed")
        self.assertIn("recommendation stands", out["response"])


class TestAlternative(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_no_saved_rec_raises(self):
        with self.assertRaises(tuner.TunerError):
            tuner.alternative(self.r, DECK, "Weak Plan")

    def test_unknown_cut_raises(self):
        save_payload(self.r)
        with self.assertRaises(tuner.TunerError):
            tuner.alternative(self.r, DECK, "No Such Card")

    def test_happy_path_swaps_the_entry(self):
        rec = save_payload(self.r)
        old_row = next(s for s in rec["payload"]["swaps"] if s["cut"]["name"] == "Weak Plan")
        with patch.object(tuner, "compute", real_compute):
            out = tuner.alternative(self.r, DECK, "Weak Plan")
        self.assertIsNone(out["message"])
        self.assertEqual(out["alternative"]["card"], "Beta Bomb")
        self.assertEqual(out["ignored"]["card"], "Alpha Bomb")
        self.assertEqual(out["ignored"]["status"], "ignored")
        self.assertEqual(out["ignored"]["superseded_by"], out["alternative"]["id"])
        self.assertEqual(out["alternative"]["status"], "open")
        self.assertNotEqual(out["alternative"]["id"], old_row["entry_id"])
        # Saved record: live row is the replacement, with a fresh id + explanation.
        saved = tune_store.get_active(self.r, DECK_KEY)["payload"]
        row = next(s for s in saved["swaps"] if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["own"]["name"], "Beta Bomb")
        self.assertEqual(row["entry_id"], out["alternative"]["id"])
        self.assertTrue(row["reason"])
        self.assertEqual(row["supersedes"], old_row["entry_id"])
        self.assertEqual(len(saved["swaps"]), len(rec["payload"]["swaps"]))  # replaced, not duplicated
        self.assertEqual([e["status"] for e in saved["entry_log"]], ["ignored", "open"])
        # The rejected add stays filtered for the rest of the review.
        st = tuner.load_state(self.r, DECK)
        self.assertEqual(st["ignored"], {"Weak Plan": ["Alpha Bomb"]})
        self.assertNotIn("Weak Plan", st["decisions"])

    def test_alternative_clears_a_prior_accept(self):
        rec = save_payload(self.r)
        tuner.decide(self.r, DECK, "Weak Plan", "own", "Alpha Bomb")
        with patch.object(tuner, "compute", real_compute):
            out = tuner.alternative(self.r, DECK, "Weak Plan")
        self.assertEqual(out["alternative"]["card"], "Beta Bomb")
        self.assertNotIn("Weak Plan", tuner.load_state(self.r, DECK)["decisions"])

    def test_no_alternative_rolls_everything_back(self):
        # Alpha is the only candidate the pool can offer, so ignoring it leaves nothing.
        def only_alpha(r, deck, mode="flex", in_deck=True, buy=True, goal="balanced", **kw):
            return tuner.build_tune(deck, WEAK, COMP, [dict(ALPHA)], CAPS, p75=4.0,
                                    has_pillars=True, has_edhrec=True, mode=mode, in_deck=in_deck,
                                    buy=buy, goal=goal, state=tuner.load_state(r, deck))

        save_payload(self.r, cands=[ALPHA])
        with patch.object(tuner, "compute", only_alpha):
            out = tuner.alternative(self.r, DECK, "Weak Plan")
        self.assertIsNone(out["alternative"])
        self.assertIn("No other card", out["message"])
        self.assertEqual(tuner.load_state(self.r, DECK)["ignored"], {})  # rolled back
        saved = tune_store.get_active(self.r, DECK_KEY)["payload"]
        self.assertNotIn("entry_log", saved)
        row = next(s for s in saved["swaps"] if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["own"]["name"], "Alpha Bomb")  # untouched

    def test_rec_entries_view(self):
        save_payload(self.r)
        with patch.object(tuner, "compute", real_compute):
            tuner.alternative(self.r, DECK, "Weak Plan")
        entries = tuner.rec_entries(tune_store.get_active(self.r, DECK_KEY))
        by_card = {e["card"]: e for e in entries}
        self.assertEqual(by_card["Alpha Bomb"]["status"], "ignored")
        self.assertEqual(by_card["Beta Bomb"]["status"], "open")
        self.assertEqual(by_card["Beta Bomb"]["cut"], "Weak Plan")
        self.assertEqual(by_card["Beta Bomb"]["kind"], "add")


class TestSyncEntries(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_no_active_rec_is_a_noop(self):
        self.assertIsNone(tuner.sync_entries(self.r, DECK, {"swaps": []}))

    def test_legacy_record_is_backfilled(self):
        save_payload(self.r, strip_entries=True)
        rec = tuner.sync_entries(self.r, DECK, build([dict(ALPHA), dict(BETA)]))
        rows = rec["payload"]["swaps"]
        self.assertTrue(all(s.get("entry_id") and s.get("status") and s.get("reason") for s in rows))
        stored = tune_store.get_active(self.r, DECK_KEY)["payload"]["swaps"]  # persisted
        self.assertTrue(all(s.get("entry_id") for s in stored))
        entries = tuner.rec_entries(rec)
        self.assertEqual(len(entries), len(rows))
        self.assertTrue(all(e["kind"] == "add" and e["reason"] for e in entries))

    def test_fresh_reason_overwrites_but_keeps_row_card(self):
        rec = save_payload(self.r)
        tune = build([dict(ALPHA), dict(BETA)])
        for s in tune["swaps"]:
            s["why"] = "LLM line for " + s["cut"]["name"]
        tuner.sync_entries(self.r, DECK, tune)
        rows = tune_store.get_active(self.r, DECK_KEY)["payload"]["swaps"]
        row = next(s for s in rows if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["reason"], "LLM line for Weak Plan")
        self.assertEqual(row["own"]["name"], rec["payload"]["swaps"][0]["own"]["name"])


class TestApi(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.client = TestClient(app)
        self.patches = [patch.object(app_mod, "r", self.r),
                        patch("app.registry.find_deck",
                              side_effect=lambda r, d: DECK if d in ("archidekt:417707", "417707") else None),
                        patch("app.registry.list_decks", return_value=[DECK]),
                        patch.object(tuner, "compute", real_compute)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    URL = "/api/decks/archidekt:417707/tune"

    def test_reconsider_404_without_saved_rec(self):
        res = self.client.post(self.URL + "/reconsider", json={"cut": "Weak Plan"})
        self.assertEqual(res.status_code, 404)

    def test_reconsider_requires_cut(self):
        self.assertEqual(self.client.post(self.URL + "/reconsider", json={}).status_code, 400)

    def test_reconsider_after_recompute_then_cached(self):
        self.client.post(self.URL + "/recompute", json={})
        res = self.client.post(self.URL + "/reconsider", json={"cut": "Weak Plan"})
        self.assertEqual(res.status_code, 200)
        j = res.json()
        self.assertTrue(j["success"])
        self.assertIn("recommendation stands", j["response"])  # no LLM keys in tests
        self.assertEqual(j["add"], "Alpha Bomb")
        again = self.client.post(self.URL + "/reconsider", json={"cut": "Weak Plan"}).json()
        self.assertEqual(again["llm"]["status"], "cached")
        self.assertEqual(again["response"], j["response"])

    def test_why_returns_entries_and_backfills_saved_rec(self):
        self.client.post(self.URL + "/recompute", json={})
        res = self.client.post(self.URL + "/why", json={})
        self.assertEqual(res.status_code, 502)  # no LLM configured in tests
        entries = res.json()["entries"]
        self.assertTrue(entries)
        self.assertTrue(all(e["reason"] and e["status"] == "open" for e in entries))
        saved = self.client.get(self.URL + "/saved").json()["recommendation"]["payload"]
        self.assertTrue(all(s.get("entry_id") and s.get("reason") for s in saved["swaps"]))

    def test_alternative_flow_over_http(self):
        self.client.post(self.URL + "/recompute", json={})
        res = self.client.post(self.URL + "/alternative", json={"cut": "Weak Plan"})
        self.assertEqual(res.status_code, 200)
        j = res.json()
        self.assertEqual(j["alternative"]["card"], "Beta Bomb")
        self.assertEqual(j["ignored"]["status"], "ignored")
        saved = self.client.get(self.URL + "/saved").json()["recommendation"]["payload"]
        row = next(s for s in saved["swaps"] if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["own"]["name"], "Beta Bomb")
        self.assertEqual(len(saved["entry_log"]), 2)
        st = self.client.get(self.URL + "/state").json()["state"]
        self.assertEqual(st["ignored"], {"Weak Plan": ["Alpha Bomb"]})
        # Again: Beta was the last candidate, so the second ask rolls back cleanly.
        j2 = self.client.post(self.URL + "/alternative", json={"cut": "Weak Plan"}).json()
        self.assertIsNone(j2["alternative"])
        self.assertIn("No other card", j2["message"])
        row = next(s for s in self.client.get(self.URL + "/saved").json()
                   ["recommendation"]["payload"]["swaps"] if s["cut"]["name"] == "Weak Plan")
        self.assertEqual(row["own"]["name"], "Beta Bomb")

    def test_alternative_unknown_cut_404(self):
        self.client.post(self.URL + "/recompute", json={})
        res = self.client.post(self.URL + "/alternative", json={"cut": "No Such Card"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
