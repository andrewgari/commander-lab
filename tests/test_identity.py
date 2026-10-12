"""
AN-3: thesis + pillars (the deck's identity).

The LLM drafts a thesis and 3-5 pillars from the deck. The user edits,
pins, keeps or rejects them. Contract with the UI (UX_SPEC.md §3.2):
  - Edited and Kept pillars are pinned automatically.
  - A re-sync never changes existing pillars or the thesis text; it only
    ADDS new draft pillars (max 5 live pillars). Proposals that match an
    existing or rejected pillar (embedding similarity >= 0.85) are dropped.
  - Rejected pillars are never proposed again.
  - Every LLM call goes through the AN-1 LLMChain (Gemini -> Nous) and is
    logged with purpose "pillars", tokens and cost.

No network: the LLM chain and the embedder are stubbed.

Run: python -m pytest tests/test_identity.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import identity
from analytics.interactions import llm as llm_mod
from tests.test_interactions import FakeRedis
from tests.test_pillar_fit import HashEmbedder, load_hh


class JsonProvider(llm_mod.LLMTagProvider):
    """LLM stub: returns queued JSON answers for generate_json."""

    def __init__(self, answers, name="gemini", fail=False):
        self._name = name
        self.model = "gemini-3.8-flash"
        self.answers = list(answers)
        self.fail = fail
        self.prompts = []

    @property
    def name(self):
        return self._name

    def is_configured(self):
        return True

    def tag_batch(self, cards):  # pragma: no cover - unused
        raise llm_mod.LLMProviderError("n/a")

    def generate_json(self, system, prompt, schema=None):
        self.prompts.append((system, prompt, schema))
        if self.fail:
            raise llm_mod.LLMProviderError(f"{self._name} down")
        return self.answers.pop(0), llm_mod.Usage(input_tokens=3000, output_tokens=400)


FIRST = {
    "thesis": {"text": "Kaalia cheats huge angels, demons and dragons into play attacking.",
               "emphasis": "cheats huge angels"},
    "pillars": [
        {"name": "Payoffs worth cheating", "description": "Expensive angel, demon and dragon creatures with "
                                                          "attack or enters triggers that Kaalia puts into play."},
        {"name": "Swing twice", "description": "Extra combat phases and untap effects."},
        {"name": "Keep her alive", "description": "Haste, shroud, hexproof and protection for the commander."},
        {"name": "Life is a resource", "description": "Pay life and lose life to draw cards."},
    ],
}

RESYNC = {
    "thesis": {"text": "A different thesis the user must not see replace theirs.", "emphasis": ""},
    "pillars": [
        # Duplicate of a live pillar (same text) -> dropped.
        {"name": "Swing twice", "description": "Extra combat phases and untap effects."},
        # Same as the pillar the user rejected -> dropped.
        {"name": "Life is a resource", "description": "Pay life and lose life to draw cards."},
        # New -> added as a draft.
        {"name": "Graveyard insurance", "description": "Reanimate and return creatures from the graveyard."},
        # Would exceed 5 live pillars -> not added.
        {"name": "Dragon storm", "description": "Dragons entering deal damage and make more dragons."},
    ],
}


def chain_with(*answers, fail_primary=False):
    primary = JsonProvider(answers, fail=fail_primary)
    fallback = JsonProvider(answers, name="nous")
    return llm_mod.LLMChain([primary, fallback]), primary, fallback


class Base(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck, self.tags = load_hh()
        self.cards = {n: t["card"] for n, t in self.tags.items()}
        self.emb = HashEmbedder()
        self.calls = []

    def generate(self, chain, deck=None):
        return identity.generate_identity(self.r, deck or self.deck, chain, card_texts=self.cards,
                                          embedder=self.emb, on_call=self.calls.append)


class TestGenerate(Base):
    def test_first_generation_creates_drafts_and_logs_cost(self):
        chain, primary, _ = chain_with(FIRST)
        out = self.generate(chain)
        self.assertEqual(out["status"], "generated")
        ident = identity.load_identity(self.r, self.deck)
        self.assertEqual(ident["thesis"]["text"], FIRST["thesis"]["text"])
        self.assertEqual(ident["thesis"]["state"], "draft")
        self.assertEqual(ident["thesis"]["emphasis"], "cheats huge angels")
        self.assertEqual([p["name"] for p in ident["pillars"]], [p["name"] for p in FIRST["pillars"]])
        self.assertTrue(all(p["state"] == "draft" for p in ident["pillars"]))
        self.assertEqual([p["order"] for p in ident["pillars"]], [1, 2, 3, 4])
        self.assertEqual(len({p["id"] for p in ident["pillars"]}), 4)
        self.assertEqual(ident["generator"]["provider"], "gemini")
        # cost is logged with the AN-1 shape
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["purpose"], "pillars")
        self.assertGreater(self.calls[0]["cost_usd"], 0)
        # the prompt is built from the deck: commander + card text
        _, prompt, schema = primary.prompts[0]
        self.assertIn("Kaalia of the Vast", prompt)
        self.assertIn("Dragon Tempest", prompt)
        self.assertIsNotNone(schema)

    def test_nous_fallback(self):
        chain, primary, fallback = chain_with(FIRST, fail_primary=True)
        self.generate(chain)
        self.assertEqual(identity.load_identity(self.r, self.deck)["generator"]["provider"], "nous")
        self.assertEqual([c["ok"] for c in self.calls], [False, True])

    def test_all_providers_down_leaves_identity_untouched(self):
        primary = JsonProvider([], fail=True)
        out = self.generate(llm_mod.LLMChain([primary]))
        self.assertEqual(out["status"], "llm_failed")
        self.assertIsNone(identity.load_identity(self.r, self.deck))

    def test_pillar_count_is_clamped_and_names_trimmed(self):
        many = {"thesis": {"text": "t", "emphasis": ""},
                "pillars": [{"name": f"Pillar number {i} " + "x" * 60, "description": f"desc {i}"} for i in range(8)]}
        chain, _, _ = chain_with(many)
        self.generate(chain)
        ps = identity.load_identity(self.r, self.deck)["pillars"]
        self.assertEqual(len(ps), 5)
        self.assertTrue(all(len(p["name"]) <= 40 for p in ps))

    def test_no_llm_configured(self):
        out = self.generate(llm_mod.LLMChain([]))
        self.assertEqual(out["status"], "no_llm")


class TestResync(Base):
    def setUp(self):
        super().setUp()
        chain, _, _ = chain_with(FIRST)
        self.generate(chain)
        ident = identity.load_identity(self.r, self.deck)
        self.ids = [p["id"] for p in ident["pillars"]]
        # user: edit pillar 1, keep pillar 2, reject pillar 4, edit the thesis
        identity.update_pillar(self.r, self.deck, self.ids[0], {"description": "My own words about cheating fatties."},
                               embedder=self.emb)
        identity.update_pillar(self.r, self.deck, self.ids[1], {"action": "keep"}, embedder=self.emb)
        identity.update_pillar(self.r, self.deck, self.ids[3], {"action": "reject"}, embedder=self.emb)
        identity.update_thesis(self.r, self.deck, {"text": "My thesis.", "emphasis": "My"})
        self.before = identity.load_identity(self.r, self.deck)

    def test_edits_pin_automatically(self):
        ps = {p["id"]: p for p in self.before["pillars"]}
        self.assertEqual(ps[self.ids[0]]["state"], "pinned")
        self.assertEqual(ps[self.ids[0]]["updated_by"], "user")
        self.assertEqual(ps[self.ids[1]]["state"], "pinned")  # Keep pins
        self.assertEqual(ps[self.ids[2]]["state"], "draft")
        self.assertEqual(ps[self.ids[3]]["state"], "rejected")
        self.assertEqual(self.before["thesis"]["state"], "pinned")
        self.assertEqual(self.before["thesis"]["updated_by"], "user")

    def test_resync_only_adds_new_pillars(self):
        chain, primary, _ = chain_with(RESYNC)
        out = self.generate(chain)
        self.assertEqual(out["status"], "proposed")
        after = identity.load_identity(self.r, self.deck)
        before_ps = {p["id"]: p for p in self.before["pillars"]}
        after_ps = {p["id"]: p for p in after["pillars"]}
        # every existing pillar is byte-for-byte unchanged (incl. the unpinned draft and the rejected one)
        for pid, p in before_ps.items():
            self.assertEqual(after_ps[pid], p)
        new = [p for pid, p in after_ps.items() if pid not in before_ps]
        self.assertEqual([p["name"] for p in new], ["Graveyard insurance"])
        self.assertEqual(new[0]["state"], "draft")
        self.assertEqual(new[0]["origin"], "resync")
        self.assertEqual(len([p for p in after["pillars"] if p["state"] != "rejected"]), 4)
        # the thesis text is the user's; the new draft is offered alongside it
        self.assertEqual(after["thesis"]["text"], "My thesis.")
        self.assertEqual(after["thesis"]["draft_text"], RESYNC["thesis"]["text"])
        self.assertEqual(after["proposal"]["new"], 1)
        self.assertEqual(sorted(after["proposal"]["dropped"]), ["Dragon storm", "Life is a resource", "Swing twice"])
        # the re-sync prompt tells the LLM what exists and what was rejected
        _, prompt, _ = primary.prompts[0]
        self.assertIn("Swing twice", prompt)
        self.assertIn("REJECTED", prompt)
        self.assertIn("Life is a resource", prompt)

    def test_cap_of_five_live_pillars(self):
        identity.update_pillar(self.r, self.deck, self.ids[3], {"action": "restore"}, embedder=self.emb)
        chain, _, _ = chain_with(RESYNC)
        self.generate(chain)
        after = identity.load_identity(self.r, self.deck)
        self.assertEqual(len([p for p in after["pillars"] if p["state"] != "rejected"]), 5)

    def test_rebuild_only_regenerates_when_cards_change(self):
        chain, primary, _ = chain_with(RESYNC, RESYNC)
        stats = identity.generate_for_rebuild(self.r, [self.deck], chain, card_texts=self.cards,
                                              embedder=self.emb, on_call=self.calls.append)
        self.assertEqual(stats["generated"], 0)
        self.assertEqual(stats["skipped_unchanged"], 1)
        self.assertEqual(primary.prompts, [])
        changed = json.loads(json.dumps(self.deck))
        changed["cards"] = [c for c in changed["cards"] if c["name"] != "Sol Ring"]
        stats = identity.generate_for_rebuild(self.r, [changed], chain, card_texts=self.cards,
                                              embedder=self.emb, on_call=self.calls.append)
        self.assertEqual(stats["generated"], 1)
        self.assertEqual(len(primary.prompts), 1)

    def test_rebuild_cap(self):
        decks = []
        for i in range(3):
            d = json.loads(json.dumps(self.deck))
            d["registry_id"] = f"archidekt:{i}"
            d["id"] = i
            decks.append(d)
        chain, primary, _ = chain_with(FIRST, FIRST, FIRST)
        stats = identity.generate_for_rebuild(self.r, decks, chain, card_texts=self.cards, embedder=self.emb,
                                              max_decks=2)
        self.assertEqual(stats["generated"], 2)
        self.assertEqual(stats["deferred"], 1)


class TestEdits(Base):
    def setUp(self):
        super().setUp()
        chain, _, _ = chain_with(FIRST)
        self.generate(chain)
        self.ident = identity.load_identity(self.r, self.deck)

    def test_add_user_pillar_is_pinned(self):
        p = identity.add_pillar(self.r, self.deck, {"name": "Dragons", "description": "Dragon tribal."},
                                embedder=self.emb)
        self.assertEqual(p["state"], "pinned")
        self.assertEqual(p["origin"], "user")
        self.assertEqual(p["order"], 5)

    def test_sixth_live_pillar_is_refused(self):
        identity.add_pillar(self.r, self.deck, {"name": "Five", "description": "five"}, embedder=self.emb)
        with self.assertRaises(identity.IdentityError):
            identity.add_pillar(self.r, self.deck, {"name": "Six", "description": "six"}, embedder=self.emb)

    def test_restore_respects_cap(self):
        pid = self.ident["pillars"][0]["id"]
        identity.update_pillar(self.r, self.deck, pid, {"action": "reject"}, embedder=self.emb)
        identity.add_pillar(self.r, self.deck, {"name": "A", "description": "a"}, embedder=self.emb)
        identity.add_pillar(self.r, self.deck, {"name": "B", "description": "b"}, embedder=self.emb)
        with self.assertRaises(identity.IdentityError):
            identity.update_pillar(self.r, self.deck, pid, {"action": "restore"}, embedder=self.emb)

    def test_unpin_and_pin(self):
        pid = self.ident["pillars"][0]["id"]
        identity.update_pillar(self.r, self.deck, pid, {"action": "pin"}, embedder=self.emb)
        self.assertEqual(identity.find_pillar(self.r, self.deck, pid)["state"], "pinned")
        identity.update_pillar(self.r, self.deck, pid, {"action": "unpin"}, embedder=self.emb)
        self.assertEqual(identity.find_pillar(self.r, self.deck, pid)["state"], "accepted")

    def test_bad_inputs(self):
        pid = self.ident["pillars"][0]["id"]
        with self.assertRaises(identity.IdentityError):
            identity.update_pillar(self.r, self.deck, pid, {"name": ""}, embedder=self.emb)
        with self.assertRaises(identity.IdentityError):
            identity.update_pillar(self.r, self.deck, pid, {"name": "x" * 41}, embedder=self.emb)
        with self.assertRaises(identity.IdentityError):
            identity.update_pillar(self.r, self.deck, pid, {"action": "explode"}, embedder=self.emb)
        with self.assertRaises(identity.PillarNotFound):
            identity.update_pillar(self.r, self.deck, "nope", {"action": "pin"}, embedder=self.emb)

    def test_reorder(self):
        ids = [p["id"] for p in self.ident["pillars"]]
        identity.reorder_pillars(self.r, self.deck, list(reversed(ids)))
        ps = sorted(identity.load_identity(self.r, self.deck)["pillars"], key=lambda p: p["order"])
        self.assertEqual([p["id"] for p in ps], list(reversed(ids)))


class TestIdentityRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import app
        import app as app_mod
        self.app_mod = app_mod
        self.client = TestClient(app)
        self.deck, _ = load_hh()
        self.fake = FakeRedis()

    @patch("app.registry")
    def test_get_without_identity(self, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        with patch.object(self.app_mod, "r", self.fake), \
                patch("app.pillar_fit.deck_fit", return_value={"per_card": []}):
            res = self.client.get("/api/decks/archidekt:417707/identity")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()["identity"]
        self.assertIsNone(body["thesis"])
        self.assertEqual(body["pillars"], [])

    @patch("app.registry")
    def test_patch_unknown_pillar_404_and_bad_action_400(self, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        with patch.object(self.app_mod, "r", self.fake):
            identity.save_identity(self.fake, self.deck, identity.new_identity(self.deck))
            self.assertEqual(self.client.patch("/api/decks/archidekt:417707/identity/pillars/nope",
                                               json={"action": "pin"}).status_code, 404)
            res = self.client.post("/api/decks/archidekt:417707/identity/pillars",
                                   json={"name": "Dragons", "description": "Dragon tribal"})
            self.assertEqual(res.status_code, 200, res.text)
            pid = res.json()["pillar"]["id"]
            self.assertEqual(self.client.patch(f"/api/decks/archidekt:417707/identity/pillars/{pid}",
                                               json={"action": "explode"}).status_code, 400)

    @patch("app.registry")
    def test_unknown_deck(self, mock_registry):
        mock_registry.find_deck.return_value = None
        mock_registry.list_decks.return_value = []
        self.assertEqual(self.client.get("/api/decks/nope/identity").status_code, 404)
        self.assertEqual(self.client.get("/api/decks/nope/weak-performers").status_code, 404)


if __name__ == "__main__":
    unittest.main()
