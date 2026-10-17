"""
AN-2: engine detection on the AN-1 interaction graph.

An engine is a resource chain inside a deck: cards MAKE resource r1,
converter cards SPEND r1 to produce r2 (optionally r2 -> r3), and payoffs
consume the last resource (or the converter is itself the payoff when the
last resource is a value resource like drain or draw). Two-card cycles
(A feeds B, B feeds A) are reported as loops. Engines are named from a
deterministic archetype catalog first, then from a per-signature name
cache, then (during rebuilds only) by the LLM chain, else generically.

No network: the LLM is stubbed.

Run: python -m pytest tests/test_engines.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.interactions import engines as eng
from analytics.interactions import graph as graph_mod
from analytics.interactions import llm as llm_mod
from analytics.interactions import service

from tests.test_interactions import FakeProvider, FakeRedis, FakeScryfall


def rec(produces=(), consumes=()):
    return {"produces": list(produces), "consumes": list(consumes), "source": "scryfall"}


ARISTOCRATS = {
    "Bitterblossom": rec(["tokens", "etb", "enchantments"]),
    "Raise the Alarm": rec(["tokens", "etb", "spells"]),
    "Viscera Seer": rec(["sacrifice", "death"]),
    "Carrion Feeder": rec(["sacrifice", "death"]),
    "Blood Artist": rec(["life_loss", "life_gain"], ["death"]),
    "Zulaport Cutthroat": rec(["life_loss", "life_gain"], ["death"]),
    "Island": rec(),
}

KAALIA = {
    "Kaalia of the Vast": rec(["etb"], ["attacks"]),
    "Aurelia, the Warleader": rec(["attacks"], ["attacks"]),
    "Basandra, Battle Seraph": rec(["attacks"]),
    "Hellkite Charger": rec(["attacks"], ["mana", "attacks"]),
    "Dragon Tempest": rec(["attacks", "life_loss"], ["etb"]),
    "Master of Cruelties": rec(["life_loss"], ["attacks"]),
    "Sol Ring": rec(["mana", "artifacts"]),
}


def graph_of(tags, commanders=()):
    return graph_mod.build_graph(tags.keys(), tags, commanders=commanders)


class TestChainDetection(unittest.TestCase):
    def test_aristocrats_chain_is_detected_with_stages(self):
        engines = eng.detect_engines(graph_of(ARISTOCRATS))
        sigs = [e["signature"] for e in engines]
        self.assertIn("chain:death>life_loss", sigs)
        e = next(x for x in engines if x["signature"] == "chain:death>life_loss")
        self.assertEqual(e["kind"], "chain")
        self.assertEqual(e["stages"]["sources"], ["Carrion Feeder", "Viscera Seer"])
        self.assertEqual(e["stages"]["converters"], ["Blood Artist", "Zulaport Cutthroat"])
        # drain is a value resource: the converters ARE the payoff
        self.assertEqual(e["stages"]["payoffs"], [])
        # sac outlets are fed by token makers -> fuel stage
        self.assertEqual(e["stages"]["fuel"], ["Bitterblossom", "Raise the Alarm"])
        self.assertEqual(e["paths"], 4)  # 2 outlets x 2 drainers
        self.assertNotIn("Island", e["cards"])

    def test_aristocrats_is_named_from_the_catalog(self):
        engines = eng.name_engines(eng.detect_engines(graph_of(ARISTOCRATS)), cache={})
        e = next(x for x in engines if x["signature"] == "chain:death>life_loss")
        self.assertEqual(e["archetype"], "Aristocrats")
        self.assertEqual(e["name"], "Aristocrats: tokens → sac → drain")
        self.assertEqual(e["name_source"], "catalog")
        self.assertTrue(e["summary"])  # deterministic prose

    def test_kaalia_attack_cheat_chain(self):
        engines = eng.name_engines(eng.detect_engines(graph_of(KAALIA, ["Kaalia of the Vast"]), diverse=False), cache={})
        e = next(x for x in engines if x["signature"] == "chain:attacks>etb")
        self.assertEqual(e["stages"]["converters"], ["Kaalia of the Vast"])
        self.assertIn("Aurelia, the Warleader", e["stages"]["sources"])
        self.assertEqual(e["stages"]["payoffs"], ["Dragon Tempest"])
        self.assertEqual(e["archetype"], "Attack cheat")
        self.assertTrue(e["name"].startswith("Attack cheat: attack → ETB"))
        self.assertIn("Kaalia of the Vast", e["commanders"])

    def test_board_wipes_are_not_fueled_by_token_makers(self):
        tags = {"Wrath": rec(["death"]), "Token Maker": rec(["tokens", "etb"]),
                "Drainer": rec(["life_loss"], ["death"])}
        e = next(x for x in eng.detect_engines(graph_of(tags)) if x["signature"] == "chain:death>life_loss")
        self.assertEqual(e["stages"]["fuel"], [])
        self.assertNotIn("Token Maker", e["cards"])

    def test_three_step_chain(self):
        engines = eng.detect_engines(graph_of(KAALIA), diverse=False)
        sigs = {e["signature"] for e in engines}
        # attack -> Kaalia -> ETB -> Dragon Tempest -> drain
        self.assertIn("chain:attacks>etb>life_loss", sigs)
        e = next(x for x in engines if x["signature"] == "chain:attacks>etb>life_loss")
        self.assertEqual(e["resources"], ["attacks", "etb", "life_loss"])
        self.assertEqual(e["stages"]["converters"], ["Kaalia of the Vast", "Dragon Tempest"])

    def test_loops(self):
        engines = eng.detect_engines(graph_of(KAALIA))
        loops = [e for e in engines if e["kind"] == "loop"]
        self.assertTrue(loops)
        lp = next(e for e in loops if e["signature"] == "loop:attacks+etb")
        # Kaalia (attacks -> etb) and Dragon Tempest (etb -> haste/attacks) feed each other
        self.assertIn(["Dragon Tempest", "Kaalia of the Vast"], lp["cycles"])

    def test_overlapping_chains_fold_into_variants(self):
        folded = eng.detect_engines(graph_of(KAALIA))
        flat = eng.detect_engines(graph_of(KAALIA), diverse=False)
        self.assertLess(len([e for e in folded if e["kind"] == "chain"]),
                        len([e for e in flat if e["kind"] == "chain"]))
        top = folded[0]
        self.assertTrue(top.get("variants"))
        listed = {e["signature"] for e in folded}
        self.assertTrue(all(v not in listed for v in top["variants"]))

    def test_type_resources_are_not_chain_steps(self):
        # a converter can't "make artifacts" out of attacks: artifact is a card type
        tags = {"A": rec(["attacks"]), "B": rec(["artifacts"], ["attacks"]), "C": rec([], ["artifacts"])}
        sigs = {e["signature"] for e in eng.detect_engines(graph_of(tags), diverse=False)}
        self.assertNotIn("chain:attacks>artifacts", sigs)

    def test_engines_ranked_and_capped(self):
        engines = eng.detect_engines(graph_of(KAALIA), max_chains=2, max_loops=1)
        self.assertLessEqual(sum(1 for e in engines if e["kind"] == "chain"), 2)
        self.assertLessEqual(sum(1 for e in engines if e["kind"] == "loop"), 1)
        scores = [e["score"] for e in engines if e["kind"] == "chain"]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_engine_in_a_flat_deck(self):
        tags = {"Island": rec(), "Plains": rec(), "Sol Ring": rec(["mana", "artifacts"])}
        self.assertEqual(eng.detect_engines(graph_of(tags)), [])

    def test_engine_metrics(self):
        e = next(x for x in eng.detect_engines(graph_of(ARISTOCRATS)) if x["signature"] == "chain:death>life_loss")
        self.assertEqual(e["card_count"], len(e["cards"]))
        self.assertGreater(e["internal_edges"], 0)
        self.assertTrue(0 < e["density"] <= 1)


class TestNaming(unittest.TestCase):
    def test_generic_name_when_no_catalog_entry_and_no_cache(self):
        e = {"signature": "chain:enchantments>counters", "kind": "chain",
             "resources": ["enchantments", "counters"], "stages": {"fuel": [], "sources": ["A"]}}
        out = eng.name_engines([e], cache={})[0]
        self.assertEqual(out["name_source"], "generic")
        self.assertEqual(out["name"], "Enchantments → counters")

    def test_three_step_chain_inherits_catalog_prefix(self):
        e = {"signature": "chain:graveyard>etb>attacks", "kind": "chain",
             "resources": ["graveyard", "etb", "attacks"], "stages": {"fuel": [], "sources": ["A"]}}
        out = eng.name_engines([e], cache={})[0]
        self.assertEqual(out["name"], "Reanimator: graveyard → reanimate → attack")
        self.assertEqual(out["name_source"], "catalog")

    def test_cached_name_wins_over_generic(self):
        e = {"signature": "chain:enchantments>counters", "kind": "chain",
             "resources": ["enchantments", "counters"], "stages": {"fuel": [], "sources": ["A"]}}
        cache = {"chain:enchantments>counters": {"archetype": "Aura counters", "blurb": "x", "source": "llm"}}
        out = eng.name_engines([e], cache=cache)[0]
        self.assertEqual(out["name"], "Aura counters: enchantments → counters")
        self.assertEqual(out["name_source"], "llm")

    def test_unnamed_signatures(self):
        e1 = {"signature": "chain:enchantments>counters", "resources": ["enchantments", "counters"], "kind": "chain"}
        e2 = {"signature": "chain:death>life_loss", "resources": ["death", "life_loss"], "kind": "chain"}
        self.assertEqual(eng.unnamed_signatures([e1, e2], cache={}), ["chain:enchantments>counters"])


class GenProvider(FakeProvider):
    """FakeProvider that also supports the generic JSON call used for naming."""

    def __init__(self, name, reply=None, fail=False, **kw):
        super().__init__(name, fail=fail, **kw)
        self.reply = reply
        self.gen_calls = []

    def generate_json(self, system, prompt, schema=None):
        self.gen_calls.append(prompt)
        if self.fail:
            raise llm_mod.LLMProviderError("down")
        return self.reply, llm_mod.Usage(input_tokens=500, output_tokens=50)


class TestLLMNaming(unittest.TestCase):
    ENG = {"signature": "chain:enchantments>counters", "kind": "chain",
           "resources": ["enchantments", "counters"],
           "stages": {"fuel": [], "sources": ["A"], "converters": ["B"], "payoffs": []}, "cards": ["A", "B"]}

    def test_chain_generate_falls_back_and_logs_cost(self):
        reply = {"engines": [{"signature": "chain:enchantments>counters", "archetype": "Aura counters",
                              "blurb": "Auras grow the team."}]}
        gem = GenProvider("gemini", fail=True)
        nous = GenProvider("nous", reply=reply, model="Hermes-4-70B")
        calls = []
        names = eng.llm_name_signatures(llm_mod.LLMChain([gem, nous]), [self.ENG], on_call=calls.append)
        self.assertEqual(names["chain:enchantments>counters"]["archetype"], "Aura counters")
        self.assertEqual(names["chain:enchantments>counters"]["provider"], "nous")
        self.assertEqual([c["provider"] for c in calls], ["gemini", "nous"])
        self.assertFalse(calls[0]["ok"])
        self.assertTrue(calls[1]["ok"])
        self.assertEqual(calls[1]["purpose"], "engine_names")
        self.assertGreater(calls[1]["cost_usd"], 0)

    def test_provider_without_generate_is_skipped(self):
        # a tag-only provider (no generate_json override) must fall through, not crash
        tag_only = FakeProvider("gemini")
        names = eng.llm_name_signatures(llm_mod.LLMChain([tag_only]), [self.ENG], on_call=None)
        self.assertEqual(names, {})

    def test_llm_archetype_outside_request_is_dropped(self):
        reply = {"engines": [{"signature": "chain:bogus", "archetype": "X", "blurb": ""}]}
        names = eng.llm_name_signatures(llm_mod.LLMChain([GenProvider("gemini", reply=reply)]), [self.ENG])
        self.assertEqual(names, {})

    def test_gemini_generate_json(self):
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"engines": []})}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2}}

        def fake_post(url, json=None, headers=None, timeout=None):
            class Resp:
                def json(self_inner):
                    return body

                def raise_for_status(self_inner):
                    return None
            return Resp()

        p = llm_mod.GeminiProvider(api_key="K", http_post=fake_post)
        data, usage = p.generate_json("sys", "prompt", schema={"type": "OBJECT"})
        self.assertEqual(data, {"engines": []})
        self.assertEqual(usage.input_tokens, 10)

    def test_nous_generate_json(self):
        def fake_post(url, json=None, headers=None, timeout=None):
            class Resp:
                def json(self_inner):
                    return {"choices": [{"message": {"content": "```json\n{\"engines\": []}\n```"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

                def raise_for_status(self_inner):
                    return None
            return Resp()

        data, usage = llm_mod.NousProvider(api_key="n", http_post=fake_post).generate_json("s", "p")
        self.assertEqual(data, {"engines": []})


# ---------------------------------------------------------------------------
# Service: reads + rebuild naming pass
# ---------------------------------------------------------------------------


def seed(r):
    deck = {"id": 9, "name": "Aristo", "registry_id": "archidekt:9", "commanders": [],
            "cards": [{"name": n, "quantity": 1} for n in ARISTOCRATS]}
    odd = {"id": 10, "name": "Odd", "registry_id": "archidekt:10", "commanders": [],
           "cards": [{"name": n, "quantity": 1} for n in ("Aura A", "Aura B", "Grower", "Grower 2")]}
    r.set("decks", json.dumps([deck, odd]))
    tags = dict(ARISTOCRATS)
    tags.update({"Aura A": rec(["enchantments"]), "Aura B": rec(["enchantments"]),
                 "Grower": rec(["counters"], ["enchantments"]), "Grower 2": rec(["counters"], ["enchantments"])})
    for name, t in tags.items():
        r.set(f"card_interactions:{name}", json.dumps({"name": name, **t, "vocab_version": 1, "tagged_at": "x"}))
    return deck, odd


class TestServiceEngines(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck, self.odd = seed(self.r)

    def test_get_deck_engines_without_llm(self):
        out = service.get_deck_engines(self.r, self.deck)
        self.assertEqual(out["deck"]["name"], "Aristo")
        names = [e["name"] for e in out["engines"]]
        self.assertIn("Aristocrats: tokens → sac → drain", names)
        self.assertEqual(out["counts"]["engines"], len(out["engines"]))

    def test_rebuild_names_new_signatures_once_and_logs_cost(self):
        reply = {"engines": [{"signature": "chain:enchantments>counters", "archetype": "Aura counters",
                              "blurb": "Enchantments grow creatures."}]}
        gem = GenProvider("gemini", reply=reply, model="gemini-3.8-flash")
        chain = llm_mod.LLMChain([gem])
        sf = FakeScryfall([])
        run = service.rebuild(self.r, trigger="manual", scryfall=sf, llm=chain)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(len([p for p in gem.gen_calls if not p.startswith("Deck:")]), 1)  # AN-3 pillar drafts also use generate_json
        self.assertEqual(run["engine_naming"]["named"], 1)
        naming = [c for c in run["llm_calls"] if c.get("purpose") == "engine_names"]
        self.assertEqual(len(naming), 1)
        self.assertGreater(run["llm_cost_usd"], 0)
        odd = service.get_deck_engines(self.r, self.odd)
        e = next(x for x in odd["engines"] if x["signature"] == "chain:enchantments>counters")
        self.assertEqual(e["name"], "Aura counters: enchantments → counters")
        # second rebuild: cached name, no new call
        run2 = service.rebuild(self.r, trigger="nightly", scryfall=sf, llm=chain)
        self.assertEqual(len([p for p in gem.gen_calls if not p.startswith("Deck:")]), 1)
        self.assertEqual(run2["engine_naming"]["named"], 0)

    def test_rebuild_survives_naming_failure(self):
        chain = llm_mod.LLMChain([GenProvider("gemini", fail=True)])
        run = service.rebuild(self.r, trigger="manual", scryfall=FakeScryfall([]), llm=chain)
        self.assertEqual(run["status"], "ok")
        odd = service.get_deck_engines(self.r, self.odd)
        e = next(x for x in odd["engines"] if x["signature"] == "chain:enchantments>counters")
        self.assertEqual(e["name_source"], "generic")


if __name__ == "__main__":
    unittest.main()
