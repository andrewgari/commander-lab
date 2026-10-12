"""
AN-1: card produce/consume tagging + interaction graph.

Covers the fixed vocabulary, the deterministic (Scryfall Tagger + oracle
text) pass, the LLM pass with Gemini -> Nous fallback and the empty
Anthropic slot, the forever tag cache (only unseen cards cost a call), the
per-run call/cost log, and deck / collection graph construction.

No network: Scryfall and the LLM providers are stubbed.

Run: python -m pytest tests/test_interactions.py
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.interactions import graph as graph_mod
from analytics.interactions import llm as llm_mod
from analytics.interactions import oracle_rules
from analytics.interactions import service
from analytics.interactions import tagger as tagger_mod
from analytics.interactions import vocab


class FakeRedis:
    """In-memory stand-in for the redis calls the interactions code makes."""

    def __init__(self):
        self.store = {}
        self.lists = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def delete(self, *keys):
        n = 0
        for k in keys:
            n += 1 if self.store.pop(k, None) is not None else 0
            self.lists.pop(k, None)
        return n

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.store) + list(self.lists) if k.startswith(prefix)]

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]

    def pipeline(self):
        return self

    def execute(self):
        return []


def card(name, type_line="Creature", oracle_text="", mana_cost=""):
    return {"name": name, "type_line": type_line, "oracle_text": oracle_text, "mana_cost": mana_cost}


class FakeScryfall:
    """Stub for tagger_mod.ScryfallSource."""

    def __init__(self, cards, otag_index=None):
        self.cards = {c["name"]: c for c in cards}
        self.index = otag_index or {}
        self.fetch_calls = []
        self.index_calls = 0
        self.requests = 0

    def fetch_cards(self, names):
        self.fetch_calls.append(list(names))
        self.requests += 1
        found = {n: self.cards[n] for n in names if n in self.cards}
        missing = [n for n in names if n not in self.cards]
        return found, missing

    def otag_index(self):
        self.index_calls += 1
        return self.index


class FakeProvider(llm_mod.LLMTagProvider):
    def __init__(self, name, answers=None, fail=False, configured=True, model="fake-1"):
        self._name = name
        self.model = model
        self.answers = answers or {}
        self.fail = fail
        self._configured = configured
        self.calls = []

    @property
    def name(self):
        return self._name

    def is_configured(self):
        return self._configured

    def tag_batch(self, cards):
        self.calls.append([c["name"] for c in cards])
        if self.fail:
            raise llm_mod.LLMProviderError(f"{self._name} down")
        out = {c["name"]: self.answers.get(c["name"], {"produces": [], "consumes": []}) for c in cards}
        return out, llm_mod.Usage(input_tokens=1000 * len(cards), output_tokens=100 * len(cards))


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class TestVocabulary(unittest.TestCase):
    def test_vocabulary_is_fixed_and_covers_the_spec_resources(self):
        for key in ("tokens", "sacrifice", "etb", "counters", "graveyard", "mana", "cards", "life_gain"):
            self.assertIn(key, vocab.RESOURCE_KEYS)
        self.assertEqual(len(set(vocab.RESOURCE_KEYS)), len(vocab.RESOURCE_KEYS))

    def test_normalize_drops_unknown_and_accepts_loose_spellings(self):
        got = vocab.normalize_resources(["Tokens", "life-gain", "made_up", "ETB", 7, "tokens"])
        self.assertEqual(got, ["tokens", "etb", "life_gain"])

    def test_every_resource_has_produce_and_consume_text(self):
        for res in vocab.RESOURCES:
            self.assertTrue(res.produce and res.consume, res.key)


# ---------------------------------------------------------------------------
# Deterministic pass
# ---------------------------------------------------------------------------


class TestOracleRules(unittest.TestCase):
    def tags(self, c):
        p, q = oracle_rules.tag_from_oracle(c)
        return set(p), set(q)

    def test_token_maker(self):
        p, _ = self.tags(card("Bitterblossom", "Tribal Enchantment — Faerie",
                              "At the beginning of your upkeep, you lose 1 life and create a 1/1 black Faerie Rogue creature token with flying."))
        self.assertIn("tokens", p)
        self.assertIn("etb", p)  # creature tokens entering feed ETB payoffs

    def test_aristocrat_payoff_and_outlet(self):
        p, q = self.tags(card("Carrion Feeder", "Creature — Zombie",
                              "Carrion Feeder can't block.\n{2}, Sacrifice a creature: Put a +1/+1 counter on Carrion Feeder."))
        self.assertIn("sacrifice", p)
        self.assertIn("death", p)
        self.assertIn("counters", p)
        p, q = self.tags(card("Blood Artist", "Creature — Vampire",
                              "Whenever Blood Artist or another creature dies, target player loses 1 life and you gain 1 life."))
        self.assertIn("death", q)
        self.assertIn("life_loss", p)
        self.assertIn("life_gain", p)

    def test_mana_rock_and_sink(self):
        p, _ = self.tags(card("Sol Ring", "Artifact", "{T}: Add {C}{C}."))
        self.assertIn("mana", p)
        self.assertIn("artifacts", p)
        _, q = self.tags(card("Exsanguinate", "Sorcery", "Each opponent loses X life. You gain life equal to the life lost this way.", "{X}{B}{B}"))
        self.assertIn("mana", q)

    def test_basic_land_has_no_tags(self):
        p, q = self.tags(card("Plains", "Basic Land — Plains", "({T}: Add {W}.)"))
        self.assertEqual((p, q), (set(), set()))

    def test_attack_trigger_and_haste(self):
        p, q = self.tags(card("Kaalia of the Vast", "Legendary Creature — Human Cleric",
                              "Flying\nWhenever Kaalia of the Vast attacks an opponent, you may put an Angel, Demon, or Dragon creature card from your hand onto the battlefield tapped and attacking that opponent."))
        self.assertIn("attacks", q)
        self.assertIn("etb", p)


class TestDeterministicTagging(unittest.TestCase):
    def test_otag_index_and_oracle_rules_are_merged(self):
        r = FakeRedis()
        sf = FakeScryfall(
            [card("Viscera Seer", "Creature — Vampire Wizard", "Sacrifice a creature: Scry 1.")],
            otag_index={"Viscera Seer": {"produces": ["sacrifice"], "consumes": ["etb"]}},
        )
        t = tagger_mod.InteractionTagger(r, scryfall=sf, llm=llm_mod.LLMChain([]))
        run = service.RunLog("test")
        tags = t.tag_cards(["Viscera Seer"], run)
        rec = tags["Viscera Seer"]
        self.assertIn("sacrifice", rec["produces"])
        self.assertIn("etb", rec["consumes"])  # from the Tagger index
        self.assertIn("death", rec["produces"])  # from oracle rules
        self.assertEqual(rec["source"], "scryfall")
        self.assertIn("otag", rec["evidence"])


# ---------------------------------------------------------------------------
# LLM pass, providers, cache, cost log
# ---------------------------------------------------------------------------


VANILLA_TEXT = "Whenever an opponent casts a creature spell, you may pay {1}. If you do, scry 1."  # no rule hits


class TestLLMPass(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.sf = FakeScryfall([
            card("Weird Card", "Creature — Horror", VANILLA_TEXT),
            card("Sol Ring", "Artifact", "{T}: Add {C}{C}."),
            card("Plains", "Basic Land — Plains", "({T}: Add {W}.)"),
        ])

    def test_only_cards_the_deterministic_pass_left_untagged_go_to_the_llm(self):
        gem = FakeProvider("gemini", answers={"Weird Card": {"produces": ["cards", "bogus"], "consumes": ["Mana"]}})
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([gem]))
        run = service.RunLog("test")
        tags = t.tag_cards(["Weird Card", "Sol Ring", "Plains"], run)
        self.assertEqual(gem.calls, [["Weird Card"]])  # not Sol Ring (rules), not Plains (basic)
        self.assertEqual(tags["Weird Card"]["produces"], ["cards"])  # 'bogus' dropped
        self.assertEqual(tags["Weird Card"]["consumes"], ["mana"])
        self.assertEqual(tags["Weird Card"]["source"], "llm:gemini")
        self.assertEqual(tags["Plains"]["source"], "none")

    def test_tags_are_cached_forever_and_a_second_run_costs_nothing(self):
        gem = FakeProvider("gemini")
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([gem]))
        t.tag_cards(["Weird Card", "Sol Ring"], service.RunLog("first"))
        stored = json.loads(self.r.get("card_interactions:Weird Card"))
        self.assertEqual(stored["vocab_version"], vocab.VOCAB_VERSION)
        # Cache key has no TTL: FakeRedis.set got no `ex`, and the record is reused.
        run2 = service.RunLog("second")
        sf_calls_before = len(self.sf.fetch_calls)
        t.tag_cards(["Weird Card", "Sol Ring"], run2)
        self.assertEqual(len(gem.calls), 1)
        self.assertEqual(len(self.sf.fetch_calls), sf_calls_before)
        self.assertEqual(run2.cache_hits, 2)
        self.assertEqual(run2.llm_calls, [])

    def test_stale_vocab_version_is_retagged(self):
        self.r.set("card_interactions:Sol Ring", json.dumps({"name": "Sol Ring", "produces": [], "consumes": [], "vocab_version": 0}))
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([]))
        tags = t.tag_cards(["Sol Ring"], service.RunLog("t"))
        self.assertIn("mana", tags["Sol Ring"]["produces"])

    def test_gemini_failure_falls_back_to_nous(self):
        gem = FakeProvider("gemini", fail=True)
        nous = FakeProvider("nous", answers={"Weird Card": {"produces": ["tokens"], "consumes": []}})
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([gem, nous]))
        run = service.RunLog("t")
        tags = t.tag_cards(["Weird Card"], run)
        self.assertEqual(tags["Weird Card"]["source"], "llm:nous")
        self.assertEqual([c["provider"] for c in run.llm_calls], ["gemini", "nous"])
        self.assertFalse(run.llm_calls[0]["ok"])
        self.assertTrue(run.llm_calls[1]["ok"])

    def test_no_llm_available_keeps_card_pending_for_a_later_run(self):
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([]))
        tags = t.tag_cards(["Weird Card"], service.RunLog("t"))
        self.assertTrue(tags["Weird Card"]["llm_pending"])
        gem = FakeProvider("gemini", answers={"Weird Card": {"produces": ["tokens"], "consumes": []}})
        t2 = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([gem]))
        tags = t2.tag_cards(["Weird Card"], service.RunLog("t2"))
        self.assertEqual(gem.calls, [["Weird Card"]])
        self.assertEqual(tags["Weird Card"]["produces"], ["tokens"])
        self.assertFalse(tags["Weird Card"].get("llm_pending"))

    def test_batches_respect_batch_size(self):
        names = [f"Weird {i}" for i in range(5)]
        sf = FakeScryfall([card(n, "Creature", VANILLA_TEXT) for n in names])
        gem = FakeProvider("gemini")
        t = tagger_mod.InteractionTagger(self.r, scryfall=sf, llm=llm_mod.LLMChain([gem]), batch_size=2)
        t.tag_cards(names, service.RunLog("t"))
        self.assertEqual([len(c) for c in gem.calls], [2, 2, 1])

    def test_run_log_records_calls_tokens_and_cost(self):
        gem = FakeProvider("gemini", model="gemini-2.5-flash")
        t = tagger_mod.InteractionTagger(self.r, scryfall=self.sf, llm=llm_mod.LLMChain([gem]))
        run = service.RunLog("manual")
        t.tag_cards(["Weird Card"], run)
        self.assertEqual(len(run.llm_calls), 1)
        call = run.llm_calls[0]
        self.assertEqual((call["input_tokens"], call["output_tokens"]), (1000, 100))
        expected = llm_mod.estimate_cost("gemini-2.5-flash", 1000, 100)
        self.assertAlmostEqual(call["cost_usd"], expected)
        self.assertGreater(expected, 0)
        d = run.to_dict()
        self.assertAlmostEqual(d["llm_cost_usd"], expected)
        self.assertEqual(d["newly_tagged"], 1)


class TestProviders(unittest.TestCase):
    def test_default_chain_is_gemini_then_nous_then_anthropic_slot(self):
        chain = llm_mod.default_chain(env={"GOOGLE_API_KEY": "k", "NOUS_API_KEY": "n"})
        self.assertEqual([p.name for p in chain.providers], ["gemini", "nous", "anthropic"])
        self.assertTrue(chain.providers[0].is_configured())
        self.assertTrue(chain.providers[1].is_configured())
        self.assertFalse(chain.providers[2].is_configured())  # empty slot

    def test_unconfigured_providers_are_skipped(self):
        chain = llm_mod.default_chain(env={})
        self.assertEqual(chain.available(), [])

    def test_anthropic_slot_raises_not_implemented(self):
        with self.assertRaises(llm_mod.LLMProviderError):
            llm_mod.AnthropicProvider(api_key="x").tag_batch([card("X")])

    def test_gemini_parses_structured_response(self):
        body = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"cards": [
                {"name": "Weird Card", "produces": ["tokens"], "consumes": ["death"]}]})}]}}],
            "usageMetadata": {"promptTokenCount": 321, "candidatesTokenCount": 45, "thoughtsTokenCount": 5},
        }
        seen = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["payload"] = json

            class Resp:
                status_code = 200

                def json(self_inner):
                    return body

                def raise_for_status(self_inner):
                    return None
            return Resp()

        p = llm_mod.GeminiProvider(api_key="KEY", http_post=fake_post)
        out, usage = p.tag_batch([card("Weird Card", "Creature", VANILLA_TEXT)])
        self.assertEqual(out["Weird Card"], {"produces": ["tokens"], "consumes": ["death"]})
        self.assertEqual((usage.input_tokens, usage.output_tokens), (321, 50))
        self.assertIn("generateContent", seen["url"])
        self.assertIn("tokens", json.dumps(seen["payload"]))  # vocabulary is in the prompt

    def test_nous_parses_openai_style_response(self):
        content = json.dumps({"cards": [{"name": "Weird Card", "produces": [], "consumes": ["tokens"]}]})

        def fake_post(url, json=None, headers=None, timeout=None):
            class Resp:
                status_code = 200

                def json(self_inner):
                    return {"choices": [{"message": {"content": "```json\n" + content + "\n```"}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}

                def raise_for_status(self_inner):
                    return None
            return Resp()

        p = llm_mod.NousProvider(api_key="n", http_post=fake_post)
        out, usage = p.tag_batch([card("Weird Card")])
        self.assertEqual(out["Weird Card"]["consumes"], ["tokens"])
        self.assertEqual(usage.output_tokens, 3)


# ---------------------------------------------------------------------------
# Graphs
# ---------------------------------------------------------------------------


def rec(name, produces=(), consumes=(), source="scryfall"):
    return {"name": name, "produces": list(produces), "consumes": list(consumes), "source": source}


class TestDeckGraph(unittest.TestCase):
    def setUp(self):
        self.tags = {
            "Bitterblossom": rec("Bitterblossom", ["tokens", "etb"], []),
            "Viscera Seer": rec("Viscera Seer", ["sacrifice", "death"], []),
            "Blood Artist": rec("Blood Artist", ["life_loss", "life_gain"], ["death"]),
            "Pitiless Plunderer": rec("Pitiless Plunderer", ["mana", "artifacts", "tokens"], ["death", "tokens"]),
            "Plains": rec("Plains", [], [], source="none"),
        }
        self.g = graph_mod.build_graph(list(self.tags), self.tags)

    def edge(self, a, b):
        return next((e for e in self.g["edges"] if e["source"] == a and e["target"] == b), None)

    def test_edge_when_a_produces_what_b_consumes(self):
        e = self.edge("Bitterblossom", "Pitiless Plunderer")
        self.assertIsNotNone(e)
        self.assertEqual(e["reasons"], ["tokens"])
        self.assertEqual(e["weight"], 1)
        self.assertIn("Tokens", e["label"])
        self.assertIsNone(self.edge("Pitiless Plunderer", "Bitterblossom"))  # it consumes nothing BB makes

    def test_weight_is_number_of_reasons(self):
        e = self.edge("Viscera Seer", "Pitiless Plunderer")
        self.assertEqual(e["reasons"], ["death"])
        tags = dict(self.tags)
        tags["Pitiless Plunderer"] = rec("Pitiless Plunderer", [], ["death", "sacrifice"])
        g = graph_mod.build_graph(list(tags), tags)
        e = next(e for e in g["edges"] if e["source"] == "Viscera Seer" and e["target"] == "Pitiless Plunderer")
        self.assertEqual(e["weight"], 2)
        self.assertEqual(len(e["explanations"]), 2)

    def test_no_self_loops(self):
        self.assertIsNone(self.edge("Pitiless Plunderer", "Pitiless Plunderer"))

    def test_degrees_and_counts(self):
        nodes = {n["name"]: n for n in self.g["nodes"]}
        # Blood Artist <- Viscera Seer (death), <- Plunderer? Plunderer produces no death -> only Seer.
        self.assertEqual(nodes["Blood Artist"]["in_degree"], 1)
        self.assertEqual(nodes["Viscera Seer"]["out_degree"], 2)  # Blood Artist, Plunderer
        self.assertEqual(nodes["Plains"]["degree"], 0)
        self.assertEqual(self.g["counts"]["nodes"], 5)
        self.assertEqual(self.g["counts"]["edges"], len(self.g["edges"]))
        self.assertEqual(self.g["counts"]["untagged"], 1)
        n = nodes["Viscera Seer"]
        self.assertEqual(n["degree"], n["in_degree"] + n["out_degree"])
        self.assertEqual(n["reason_counts"]["death"]["out"], 2)

    def test_top_connected_sorted_by_degree(self):
        degs = [n["degree"] for n in self.g["top_connected"]]
        self.assertEqual(degs, sorted(degs, reverse=True))
        self.assertTrue(self.g["top_connected"][0]["reasons"])

    def test_untagged_card_in_tags_missing_is_a_node(self):
        g = graph_mod.build_graph(["Bitterblossom", "Unknown Card"], self.tags)
        names = [n["name"] for n in g["nodes"]]
        self.assertIn("Unknown Card", names)
        self.assertEqual(g["counts"]["untagged"], 1)


class TestCollectionGraph(unittest.TestCase):
    def test_index_and_edge_count_match_materialized_graph(self):
        tags = {
            "A": rec("A", ["tokens", "death"], []),
            "B": rec("B", [], ["tokens", "death"]),
            "C": rec("C", ["tokens"], ["tokens"]),
        }
        coll = graph_mod.build_collection_graph(list(tags), tags)
        full = graph_mod.build_graph(list(tags), tags)
        self.assertEqual(coll["counts"]["edges"], full["counts"]["edges"])
        self.assertEqual(coll["counts"]["nodes"], 3)
        self.assertEqual(sorted(coll["index"]["tokens"]["producers"]), ["A", "C"])
        degs = {n["name"]: n["degree"] for n in coll["nodes"]}
        self.assertEqual(degs, {n["name"]: n["degree"] for n in full["nodes"]})

    def test_neighbors(self):
        tags = {"A": rec("A", ["tokens"]), "B": rec("B", [], ["tokens"]), "C": rec("C")}
        coll = graph_mod.build_collection_graph(list(tags), tags)
        nb = graph_mod.collection_neighbors(coll, "A")
        self.assertEqual([e["target"] for e in nb["out"]], ["B"])
        self.assertEqual(nb["in"], [])


# ---------------------------------------------------------------------------
# Service: rebuild orchestration, storage, run log, lock, nightly schedule
# ---------------------------------------------------------------------------


def seed_library(r):
    decks = [
        {"id": 1, "name": "Aristo", "registry_id": "archidekt:1", "commanders": ["Blood Artist"],
         "cards": [{"name": "Blood Artist", "quantity": 1}, {"name": "Viscera Seer", "quantity": 1},
                   {"name": "Plains", "quantity": 10}]},
        {"id": 2, "name": "Other", "registry_id": "archidekt:2", "commanders": [],
         "cards": [{"name": "Sol Ring", "quantity": 1}]},
    ]
    r.set("decks", json.dumps(decks))
    for i, (n, st) in enumerate([("Blood Artist", "in_deck"), ("Viscera Seer", "in_deck"),
                                 ("Sol Ring", "in_collection"), ("Ghost Card", "not_owned")]):
        r.set(f"instance:{i}", json.dumps({"id": str(i), "card_name": n, "ownership_status": st}))


def library_scryfall():
    return FakeScryfall([
        card("Blood Artist", "Creature — Vampire", "Whenever Blood Artist or another creature dies, target player loses 1 life and you gain 1 life."),
        card("Viscera Seer", "Creature — Vampire Wizard", "Sacrifice a creature: Scry 1."),
        card("Plains", "Basic Land — Plains", "({T}: Add {W}.)"),
        card("Sol Ring", "Artifact", "{T}: Add {C}{C}."),
        card("Ghost Card", "Creature", "Flying"),
    ])


class TestRebuild(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        seed_library(self.r)
        self.sf = library_scryfall()
        self.chain = llm_mod.LLMChain([FakeProvider("gemini")])

    def rebuild(self, trigger="manual"):
        return service.rebuild(self.r, trigger=trigger, scryfall=self.sf, llm=self.chain)

    def test_rebuild_stores_deck_and_collection_graphs_and_a_run_log(self):
        run = self.rebuild()
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["graphs_built"], 3)  # 2 decks + collection
        g = json.loads(self.r.get("interaction_graph:deck:archidekt:1"))
        self.assertEqual(g["counts"]["nodes"], 3)
        self.assertTrue(any(e["source"] == "Viscera Seer" and e["target"] == "Blood Artist" for e in g["edges"]))
        coll = json.loads(self.r.get("interaction_graph:collection"))
        # owned only: Ghost Card is not_owned
        self.assertEqual(sorted(n["name"] for n in coll["nodes"]), ["Blood Artist", "Sol Ring", "Viscera Seer"])
        runs = service.list_runs(self.r)
        self.assertEqual(runs[0]["run_id"], run["run_id"])
        self.assertEqual(runs[0]["trigger"], "manual")

    def test_second_rebuild_hits_cache_only(self):
        self.rebuild()
        before = len(self.sf.fetch_calls)
        run = self.rebuild("nightly")
        self.assertEqual(len(self.sf.fetch_calls), before)
        self.assertEqual(run["newly_tagged"], 0)
        self.assertEqual(run["llm_cost_usd"], 0)

    def test_get_deck_graph_builds_from_cache_when_missing(self):
        self.rebuild()
        self.r.delete("interaction_graph:deck:archidekt:2")
        deck = {"id": 2, "name": "Other", "registry_id": "archidekt:2", "cards": [{"name": "Sol Ring", "quantity": 1}]}
        g = service.get_deck_graph(self.r, deck)
        self.assertEqual(g["counts"]["nodes"], 1)
        self.assertEqual(g["counts"]["untagged"], 0)

    def test_concurrent_rebuild_is_deferred_not_lost(self):
        self.r.set(service.LOCK_KEY, "someone-else")
        run = self.rebuild("sync")
        self.assertEqual(run["status"], "deferred")
        self.assertEqual(self.r.get(service.RERUN_KEY), "1")

    def test_lock_holder_reruns_when_a_trigger_arrived_meanwhile(self):
        calls = []
        orig = service._rebuild_once

        def wrapped(*a, **kw):
            calls.append(kw.get("trigger"))
            if len(calls) == 1:
                self.r.set(service.RERUN_KEY, "1")  # a sync arrived mid-run
            return orig(*a, **kw)

        service._rebuild_once = wrapped
        try:
            self.rebuild("manual")
        finally:
            service._rebuild_once = orig
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.r.get(service.LOCK_KEY))

    def test_scope_to_one_deck(self):
        run = service.rebuild(self.r, trigger="manual", scryfall=self.sf, llm=self.chain, deck_ids=["archidekt:2"])
        self.assertEqual(run["graphs_built"], 1)
        self.assertIsNone(self.r.get("interaction_graph:deck:archidekt:1"))


class TestNightlySchedule(unittest.TestCase):
    def test_seconds_until_next_run(self):
        now = datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)
        self.assertEqual(service.seconds_until_next_run(now, hour_utc=7), 30 * 60)
        now = datetime(2026, 10, 12, 7, 0, 1, tzinfo=timezone.utc)
        self.assertAlmostEqual(service.seconds_until_next_run(now, hour_utc=7), 24 * 3600 - 1)


if __name__ == "__main__":
    unittest.main()
