"""
Tests for Reconstruct deck builder (reconstruct_builder.py).

Verifies the Reconstruct builder pipeline:
- Happy path generation to drafts_ready
- Validation failure followed by successful LLM repair
- Deterministic repair for trivial issues (deck size, singleton deduplication)
- Shared-pool conflicts across two target decks triggering repair
- Retry exhaustion leading to failure status (STATUS_FAILED)
- Correct tier tags (Tier 1/2/3) and cost totals
- Async background task enqueueing via the REST endpoint without blocking
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deck_validator as dv
from deck_validator import CardCatalog, CardInfo
from fastapi.testclient import TestClient
import instances
import reconstruct
import reconstruct_agent as ra
from reconstruct_builder import ReconstructBuildError, ReconstructBuilder
import reconstruct_pools as rp
from reconstruct_pools import CatalogCard, InMemoryCatalog, Tier
import registry


class FakeRedis:
    """In-memory Redis fake matching reconstruct and instances test fixtures."""

    def __init__(self):
        self.store: dict[str, Any] = {}
        self.sets: dict[str, set] = {}

    def get(self, k: str):
        return self.store.get(k)

    def set(self, k: str, v: Any):
        self.store[k] = v

    def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)
            self.sets.pop(k, None)

    def sadd(self, k: str, *members: Any):
        s = self.sets.setdefault(k, set())
        for m in members:
            s.add(str(m))

    def smembers(self, k: str):
        return set(self.sets.get(k, set()))

    def sinter(self, *ks: str):
        out = None
        for k in ks:
            out = self.smembers(k) if out is None else out & self.smembers(k)
        return out or set()

    def srem(self, k: str, *members: Any):
        s = self.sets.get(k, set())
        for m in members:
            s.discard(str(m))

    def keys(self, pattern: str = "*"):
        pre = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]

    def pipeline(self):
        return FakePipe(self)


class FakePipe:
    def __init__(self, r: FakeRedis):
        self.r = r
        self.ops: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        def queue(*a: Any):
            self.ops.append((name, a))
            return self
        return queue

    def execute(self):
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


def make_card_info(name: str, ci: str = "", legality: str = "legal", types=("Artifact",), text="") -> CardInfo:
    return CardInfo(
        name=name,
        color_identity=dv.parse_color_identity(ci),
        types=tuple(types),
        commander_legality=legality,
        oracle_text=text,
    )


def make_legend_info(name: str, ci: str = "B", text: str = "") -> CardInfo:
    return CardInfo(
        name=name,
        color_identity=dv.parse_color_identity(ci),
        types=("Creature",),
        supertypes=("Legendary",),
        subtypes=("Human", "Vampire"),
        commander_legality="legal",
        oracle_text=text,
    )


def sample_catalog() -> CardCatalog:
    cat = CardCatalog()
    # Commanders
    cat.add(make_legend_info("Vito, Thorn of the Dusk Rose", ci="B"))
    cat.add(make_legend_info("Krenko, Mob Boss", ci="R"))
    cat.add(make_legend_info("Atraxa, Praetors' Voice", ci="WUBG"))
    cat.add(CardInfo("Partner One", dv.parse_color_identity("U"), ("Creature",), ("Legendary",), ("Human",), ("Partner",), "", "legal"))
    cat.add(CardInfo("Partner Two", dv.parse_color_identity("R"), ("Creature",), ("Legendary",), ("Human",), ("Partner",), "", "legal"))
    # Cards
    cat.add(make_card_info("Sol Ring", ci="", types=("Artifact",)))
    cat.add(make_card_info("Arcane Signet", ci="", types=("Artifact",)))
    cat.add(make_card_info("Skullclamp", ci="", types=("Artifact",)))
    cat.add(make_card_info("Demonic Tutor", ci="B", types=("Sorcery",)))
    cat.add(make_card_info("Sanguine Bond", ci="B", types=("Enchantment",)))
    cat.add(make_card_info("Exquisite Blood", ci="B", types=("Enchantment",)))
    cat.add(make_card_info("Goblin Bombardment", ci="R", types=("Enchantment",)))
    cat.add(make_card_info("Lightning Bolt", ci="R", types=("Instant",)))
    # Banned / illegal cards
    cat.add(make_card_info("Black Lotus", ci="", legality="banned", types=("Artifact",)))
    return cat


def mock_deck_payload(
    name: str = "Bat Swarm",
    commander: str = "Vito, Thorn of the Dusk Rose",
    other_cards: list[tuple[str, int, int]] | None = None,  # (name, tier, qty)
    basic: str = "Swamp",
    basic_tier: int = 2,
    total_cards: int = 100,
) -> dict:
    """Build a raw deck dictionary matching reconstruct_agent schema."""
    cards = [{"name": commander, "quantity": 1, "tier": 1, "why": "Designated commander"}]
    used_qty = 1
    if other_cards:
        for cname, ctier, cqty in other_cards:
            cards.append({"name": cname, "quantity": cqty, "tier": ctier, "why": f"Role for {cname}"})
            used_qty += cqty

    if used_qty < total_cards:
        cards.append({
            "name": basic,
            "quantity": total_cards - used_qty,
            "tier": basic_tier,
            "why": f"Basic mana source ({basic})",
        })

    return {
        "name": name,
        "commanders": [commander],
        "cards": cards,
        "explanation": f"Detailed synergy explanation for {name}.",
        "open_questions": [],
    }


def llm_reply(*decks: dict) -> str:
    body = json.dumps({"decks": list(decks)})
    return f"Here are the drafts:\n\n```json\n{body}\n```"


class TestReconstructBuilder(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.catalog = sample_catalog()

        # Seed an active physical source deck in Redis
        deck_data = {
            "id": 42,
            "name": "Old Vampire Drain",
            "commanders": ["Vito, Thorn of the Dusk Rose"],
            "status": "physical",
            "tags": ["active"],
            "source": "archidekt",
            "source_id": "42",
            "cards": [
                {"name": "Vito, Thorn of the Dusk Rose", "quantity": 1},
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Sanguine Bond", "quantity": 1},
            ],
        }
        self.r.set("decks", json.dumps([deck_data]))
        self.r.set("deck_status:42", "physical")

        # Seed instances
        self.inst_vito = instances.create_instance(self.r, "Vito, Thorn of the Dusk Rose", ownership_status="in_deck", deck_id="archidekt:42")
        self.inst_sol = instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck", deck_id="archidekt:42")
        self.inst_bond = instances.create_instance(self.r, "Sanguine Bond", ownership_status="in_deck", deck_id="archidekt:42")
        self.inst_clamp = instances.create_instance(self.r, "Skullclamp", ownership_status="in_collection")

        # Price lookup
        self.prices = {
            "Sol Ring": 1.50,
            "Skullclamp": 6.00,
            "Demonic Tutor": 35.00,
            "Exquisite Blood": 25.00,
            "Vito, Thorn of the Dusk Rose": 4.00,
            "Arcane Signet": 1.25,
            "Swamp": 0.25,
            "Mountain": 0.25,
        }
        self.price_lookup = lambda names: {n: self.prices.get(n) for n in names}

    def test_happy_path_to_drafts_ready(self):
        """Happy path: 1-deck session builds, validates on first try, stores drafts, status=drafts_ready."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["Lifedrain"]}
        ])

        # LLM returns a valid 100-card deck
        draft_payload = mock_deck_payload(
            name="Vito Drain Draft",
            commander="Vito, Thorn of the Dusk Rose",
            other_cards=[
                ("Sol Ring", 1, 1),        # Tier 1
                ("Skullclamp", 2, 1),      # Tier 2
                ("Demonic Tutor", 3, 1),   # Tier 3
                ("Sanguine Bond", 1, 1),   # Tier 1
            ],
            basic="Swamp",
            total_cards=100,
        )

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(draft_payload)

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        # Trigger build synchronously
        result_session = reconstruct.trigger_build(self.r, sid, sync=True)

        self.assertEqual(result_session["status"], reconstruct.STATUS_DRAFTS_READY)
        drafts = result_session.get("drafts", [])
        self.assertEqual(len(drafts), 1)

        d0 = drafts[0]
        self.assertEqual(d0["name"], "Vito Drain Draft")
        self.assertEqual(d0["commanders"], ["Vito, Thorn of the Dusk Rose"])
        self.assertEqual(len(d0["cards"]), 6)  # Vito, Sol Ring, Skullclamp, Demonic Tutor, Sanguine Bond, Swamp
        self.assertEqual(sum(c["quantity"] for c in d0["cards"]), 100)
        self.assertIn("Detailed synergy explanation", d0["explanation"])

        # Check that mock_client.chat was called once (no repair needed)
        self.assertEqual(mock_client.chat.call_count, 1)

    def test_validation_failure_followed_by_successful_repair(self):
        """Turn 1 fails validation (illegal commander); Turn 2 fixes it in repair mode."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["Lifedrain"]}
        ])

        # Turn 1: Deck with an illegal commander (Sol Ring is an artifact, not a legal commander)
        invalid_deck = mock_deck_payload(
            name="Illegal Commander Deck",
            commander="Sol Ring",  # COMMANDER_ILLEGAL in validator
            basic="Swamp",
            total_cards=100,
        )

        # Turn 2: Repaired deck with valid commander
        valid_deck = mock_deck_payload(
            name="Repaired Vito Draft",
            commander="Vito, Thorn of the Dusk Rose",
            other_cards=[("Sol Ring", 1, 1)],
            basic="Swamp",
            total_cards=100,
        )

        mock_client = MagicMock()
        # First call returns invalid deck, second call returns valid deck
        mock_client.chat.side_effect = [
            llm_reply(invalid_deck),
            llm_reply(valid_deck),
        ]

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        result_session = reconstruct.trigger_build(self.r, sid, sync=True)

        self.assertEqual(result_session["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(mock_client.chat.call_count, 2)

        # Verify second call was in REPAIR MODE
        second_call_messages = mock_client.chat.call_args_list[1].args[0]
        user_prompt = second_call_messages[1]["content"]
        self.assertIn("## REPAIR MODE", user_prompt)
        self.assertIn("COMMANDER_ILLEGAL", user_prompt)

    def test_deterministic_repair_for_trivial_size_and_singleton(self):
        """Trivial issues (99 cards + duplicate singleton) are repaired deterministically without 2nd LLM call."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["Lifedrain"]}
        ])

        # 99 cards total: Vito x1, Sol Ring x2 (duplicate singleton!), Swamp x96 -> total 99
        trivial_flawed_deck = {
            "name": "Trivial Flawed Deck",
            "commanders": ["Vito, Thorn of the Dusk Rose"],
            "cards": [
                {"name": "Vito, Thorn of the Dusk Rose", "quantity": 1, "tier": 1, "why": "Cmd"},
                {"name": "Sol Ring", "quantity": 2, "tier": 1, "why": "Ramp"},  # duplicate singleton!
                {"name": "Swamp", "quantity": 96, "tier": 2, "why": "Mana"},     # 1 + 2 + 96 = 99 cards!
            ],
            "explanation": "Off-by-one with duplicate Sol Ring.",
            "open_questions": [],
        }

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(trivial_flawed_deck)

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        result_session = reconstruct.trigger_build(self.r, sid, sync=True)

        # Should be repaired to exactly 100 cards and 1 Sol Ring without calling the agent a second time!
        self.assertEqual(result_session["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(mock_client.chat.call_count, 1)

        d = result_session["drafts"][0]
        self.assertEqual(sum(c["quantity"] for c in d["cards"]), 100)
        sol_ring_entry = next(c for c in d["cards"] if c["name"] == "Sol Ring")
        self.assertEqual(sol_ring_entry["quantity"], 1)

    def test_shared_pool_conflict_across_two_decks_triggers_repair(self):
        """Cross-deck quantity conflict (both decks use only 1 available Sol Ring) triggers repair."""
        # Add a second deck to Redis to start a 2-deck session
        deck2_data = {
            "id": 43,
            "name": "Goblins",
            "commanders": ["Krenko, Mob Boss"],
            "status": "physical",
            "tags": [],
            "source": "archidekt",
            "source_id": "43",
            "cards": [
                {"name": "Krenko, Mob Boss", "quantity": 1},
                {"name": "Goblin Bombardment", "quantity": 1},
            ],
        }
        self.r.set("decks", json.dumps([
            json.loads(self.r.store["decks"])[0],
            deck2_data,
        ]))
        self.r.set("deck_status:43", "physical")

        session = reconstruct.start_reconstruct(self.r, ["archidekt:42", "archidekt:43"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["Drain"]},
            {"commanders": ["Krenko, Mob Boss"], "themes": ["Goblins"]},
        ])

        # In pools: Sol Ring has quantity 1 across Tier 1 + Tier 2.
        # Turn 1: Deck 0 uses Sol Ring x1, AND Deck 1 uses Sol Ring x1 -> SHARED_POOL_CONFLICT!
        deck0_turn1 = mock_deck_payload("Vito Draft", "Vito, Thorn of the Dusk Rose", [("Sol Ring", 1, 1)], "Swamp")
        deck1_turn1 = mock_deck_payload("Krenko Draft", "Krenko, Mob Boss", [("Sol Ring", 1, 1)], "Mountain")

        # Turn 2: Deck 1 replaces Sol Ring with Arcane Signet
        deck0_turn2 = deck0_turn1
        deck1_turn2 = mock_deck_payload("Krenko Draft", "Krenko, Mob Boss", [("Arcane Signet", 3, 1)], "Mountain")

        mock_client = MagicMock()
        mock_client.chat.side_effect = [
            llm_reply(deck0_turn1, deck1_turn1),
            llm_reply(deck0_turn2, deck1_turn2),
        ]

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        result_session = reconstruct.trigger_build(self.r, sid, sync=True)

        self.assertEqual(result_session["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(mock_client.chat.call_count, 2)

        repair_prompt = mock_client.chat.call_args_list[1].args[0][1]["content"]
        self.assertIn("SHARED_POOL_CONFLICT", repair_prompt)
        self.assertIn("Sol Ring", repair_prompt)

    def test_retry_exhaustion_transitions_to_failure_status(self):
        """When retries are exhausted, session status transitions to STATUS_FAILED with error message."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"]}
        ])

        # Always returns invalid deck (Sol Ring is an illegal commander)
        illegal_deck = mock_deck_payload(
            "Illegal Deck",
            commander="Sol Ring",  # COMMANDER_ILLEGAL
            basic="Swamp",
            total_cards=100,
        )

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(illegal_deck)

        builder = ReconstructBuilder(
            max_retries=2,
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        with self.assertRaises(ReconstructBuildError):
            reconstruct.trigger_build(self.r, sid, sync=True)

        # Inspect session in Redis
        saved_session = reconstruct.get_session(self.r, sid)
        self.assertIsNotNone(saved_session)
        assert saved_session is not None
        self.assertEqual(saved_session["status"], reconstruct.STATUS_FAILED)
        self.assertIn("COMMANDER_ILLEGAL", saved_session.get("error", ""))
        self.assertIn("COMMANDER_ILLEGAL", saved_session.get("error_message", ""))
        # Called initial attempt + 2 retries = 3 calls
        self.assertEqual(mock_client.chat.call_count, 3)

    def test_correct_tier_tags_and_cost_totals(self):
        """Verifies cards receive correct tiers (1=previous_deck, 2=owned, 3=new) and costing."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"]}
        ])

        # Vito: Tier 1 (was in source deck archidekt:42)
        # Sol Ring: Tier 1 (was in source deck)
        # Skullclamp: Tier 2 (owned in collection)
        # Demonic Tutor: Tier 3 (not owned, price $35.00)
        # Swamp: Basic land (cost 0.0)
        deck_payload = mock_deck_payload(
            "Vito Tier Test",
            "Vito, Thorn of the Dusk Rose",
            other_cards=[
                ("Sol Ring", 1, 1),
                ("Skullclamp", 2, 1),
                ("Demonic Tutor", 3, 1),
            ],
            basic="Swamp",
            total_cards=100,
        )

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck_payload)

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        res = reconstruct.trigger_build(self.r, sid, sync=True)
        draft = res["drafts"][0]

        card_map = {c["name"]: c for c in draft["cards"]}

        # Vito: Tier 1, cost 0.0
        self.assertEqual(card_map["Vito, Thorn of the Dusk Rose"]["tier"], 1)
        self.assertEqual(card_map["Vito, Thorn of the Dusk Rose"]["tier_label"], "previous_deck")
        self.assertEqual(card_map["Vito, Thorn of the Dusk Rose"]["cost"], 0.0)

        # Sol Ring: Tier 1, cost 0.0
        self.assertEqual(card_map["Sol Ring"]["tier"], 1)
        self.assertEqual(card_map["Sol Ring"]["tier_label"], "previous_deck")
        self.assertEqual(card_map["Sol Ring"]["cost"], 0.0)

        # Skullclamp: Tier 2, cost 0.0
        self.assertEqual(card_map["Skullclamp"]["tier"], 2)
        self.assertEqual(card_map["Skullclamp"]["tier_label"], "owned")
        self.assertEqual(card_map["Skullclamp"]["cost"], 0.0)

        # Demonic Tutor: Tier 3, cost 35.0
        self.assertEqual(card_map["Demonic Tutor"]["tier"], 3)
        self.assertEqual(card_map["Demonic Tutor"]["tier_label"], "new")
        self.assertEqual(card_map["Demonic Tutor"]["cost"], 35.0)

        # Swamp: cost 0.0 (basics free)
        self.assertEqual(card_map["Swamp"]["cost"], 0.0)

        # Total new cost: only Demonic Tutor = 35.00
        self.assertEqual(draft["total_new_cost"], 35.00)

        # Check rationales are preserved
        self.assertEqual(card_map["Demonic Tutor"]["rationale"], "Role for Demonic Tutor")

    def test_async_endpoint_enqueues_background_task_without_blocking(self):
        """POST /api/reconstruct/{id}/build with sync=false returns 202 immediately, session status='building'."""
        import app as main_app

        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(mock_deck_payload("Async Draft", "Vito, Thorn of the Dusk Rose"))

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        # Patch app's redis client with self.r
        with patch.object(main_app, "r", self.r):
            client = TestClient(main_app.app)
            response = client.post(f"/api/reconstruct/{sid}/build", json={"sync": False})

            # Check that request returned immediately with 202 Accepted
            self.assertEqual(response.status_code, 202)
            data = response.json()
            self.assertTrue(data["success"])
            # Status at response time was 'building' (in progress)
            self.assertEqual(data["status"], "building")

            # Check session in Redis is now 'drafts_ready' after background execution ran
            session_after = reconstruct.get_session(self.r, sid)
            self.assertIsNotNone(session_after)
            assert session_after is not None
            self.assertEqual(session_after["status"], reconstruct.STATUS_DRAFTS_READY)
            self.assertEqual(len(session_after["drafts"]), 1)

    def test_malformed_json_retry_exhaustion_transitions_to_failed(self):
        """Unparseable agent reply retried until max_retries exhausted -> STATUS_FAILED."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])

        mock_client = MagicMock()
        mock_client.chat.return_value = "Sorry, as an AI language model I cannot output JSON."

        builder = ReconstructBuilder(
            max_retries=2,
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        with self.assertRaises(ReconstructBuildError):
            reconstruct.trigger_build(self.r, sid, sync=True)

        saved = reconstruct.get_session(self.r, sid)
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["status"], reconstruct.STATUS_FAILED)
        self.assertIn("Agent output failed after 2 retries", saved.get("error", ""))
        self.assertEqual(mock_client.chat.call_count, 3)

    def test_happy_path_two_decks(self):
        """Happy path for a 2-deck session producing 2 valid drafts."""
        deck2_data = {
            "id": 43,
            "name": "Goblins",
            "commanders": ["Krenko, Mob Boss"],
            "status": "physical",
            "tags": [],
            "source": "archidekt",
            "source_id": "43",
            "cards": [
                {"name": "Krenko, Mob Boss", "quantity": 1},
                {"name": "Goblin Bombardment", "quantity": 1},
            ],
        }
        self.r.set("decks", json.dumps([
            json.loads(self.r.store["decks"])[0],
            deck2_data,
        ]))
        self.r.set("deck_status:43", "physical")

        session = reconstruct.start_reconstruct(self.r, ["archidekt:42", "archidekt:43"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["Lifedrain"]},
            {"commanders": ["Krenko, Mob Boss"], "themes": ["Goblins"]},
        ])

        d0 = mock_deck_payload("Vito Draft", "Vito, Thorn of the Dusk Rose", [("Sol Ring", 1, 1)], "Swamp")
        d1 = mock_deck_payload("Krenko Draft", "Krenko, Mob Boss", [("Arcane Signet", 3, 1)], "Mountain")

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(d0, d1)

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        res = reconstruct.trigger_build(self.r, sid, sync=True)
        self.assertEqual(res["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(len(res["drafts"]), 2)
        self.assertEqual(res["drafts"][0]["name"], "Vito Draft")
        self.assertEqual(res["drafts"][1]["name"], "Krenko Draft")

    def test_partner_commanders_handling(self):
        """Deck with 2 valid partner commanders builds and validates properly."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Partner One", "Partner Two"], "themes": ["Partners"]}
        ])

        # 2 commanders + 98 islands -> 100 cards
        cards = [
            {"name": "Partner One", "quantity": 1, "tier": 3, "why": "Partner 1"},
            {"name": "Partner Two", "quantity": 1, "tier": 3, "why": "Partner 2"},
            {"name": "Island", "quantity": 98, "tier": 3, "why": "Basic land"},
        ]
        deck_dict = {
            "name": "Partner Draft",
            "commanders": ["Partner One", "Partner Two"],
            "cards": cards,
            "explanation": "Partner deck.",
            "open_questions": [],
        }

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck_dict)

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=self.price_lookup,
        )
        reconstruct.set_builder(builder)

        res = reconstruct.trigger_build(self.r, sid, sync=True)
        self.assertEqual(res["status"], reconstruct.STATUS_DRAFTS_READY)
        draft = res["drafts"][0]
        self.assertEqual(draft["commanders"], ["Partner One", "Partner Two"])
        self.assertEqual(sum(c["quantity"] for c in draft["cards"]), 100)

    def test_unpriced_tier3_card_handles_none_cost(self):
        """Tier 3 card with missing price has cost=None and total_new_cost reflects known prices."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])

        deck_dict = mock_deck_payload(
            "Unpriced Test",
            "Vito, Thorn of the Dusk Rose",
            other_cards=[
                ("Demonic Tutor", 3, 1),   # Priced $35.00
                ("Arcane Signet", 3, 1),   # Not in price lookup -> cost None
            ],
            basic="Swamp",
            total_cards=100,
        )

        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck_dict)

        # Price lookup that only has Demonic Tutor, Arcane Signet is None
        sparse_prices = lambda names: {"Demonic Tutor": 35.00}

        builder = ReconstructBuilder(
            client=mock_client,
            catalog=self.catalog,
            price_lookup=sparse_prices,
        )
        reconstruct.set_builder(builder)

        res = reconstruct.trigger_build(self.r, sid, sync=True)
        draft = res["drafts"][0]

        card_map = {c["name"]: c for c in draft["cards"]}
        self.assertEqual(card_map["Demonic Tutor"]["cost"], 35.00)
        self.assertIsNone(card_map["Arcane Signet"]["cost"])
        self.assertFalse(card_map["Arcane Signet"]["price_known"])
        # Total new cost includes only known prices
        self.assertEqual(draft["total_new_cost"], 35.00)

    def test_unpriced_tier3_flags_draft_incomplete(self):
        """BUG-1: unpriced Tier 3 cards are surfaced on the stored draft."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])
        deck = mock_deck_payload("Unpriced", "Vito, Thorn of the Dusk Rose",
                                 other_cards=[("Demonic Tutor", 3, 1)], basic="Swamp", total_cards=100)
        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck)
        reconstruct.set_builder(ReconstructBuilder(
            client=mock_client, catalog=self.catalog,
            price_lookup=lambda names: {n: None for n in names}))
        draft = reconstruct.trigger_build(self.r, sid, sync=True)["drafts"][0]
        self.assertFalse(draft["is_complete"])
        self.assertIn("Demonic Tutor", draft["unpriced_cards"])

    def test_priced_draft_is_complete(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])
        deck = mock_deck_payload("Priced", "Vito, Thorn of the Dusk Rose",
                                 other_cards=[("Demonic Tutor", 3, 1)], basic="Swamp", total_cards=100)
        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck)
        reconstruct.set_builder(ReconstructBuilder(
            client=mock_client, catalog=self.catalog, price_lookup=self.price_lookup))
        draft = reconstruct.trigger_build(self.r, sid, sync=True)["drafts"][0]
        self.assertTrue(draft["is_complete"])
        self.assertEqual(draft["unpriced_cards"], [])

    def test_commander_swapped_by_llm_fails_after_retries(self):
        """BUG-2: LLM-swapped commander is a validation failure."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])
        deck = mock_deck_payload("Swapped", "Krenko, Mob Boss", basic="Mountain", total_cards=100)
        mock_client = MagicMock()
        mock_client.chat.return_value = llm_reply(deck)
        reconstruct.set_builder(ReconstructBuilder(
            max_retries=2, client=mock_client, catalog=self.catalog, price_lookup=self.price_lookup))
        with self.assertRaises(ReconstructBuildError):
            reconstruct.trigger_build(self.r, sid, sync=True)
        saved = reconstruct.get_session(self.r, sid)
        self.assertEqual(saved["status"], reconstruct.STATUS_FAILED)
        self.assertIn("COMMANDER_MISMATCH", saved.get("error", ""))
        self.assertEqual(mock_client.chat.call_count, 3)

    def test_commander_swap_recovers_on_retry(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Vito, Thorn of the Dusk Rose"]}])
        bad = mock_deck_payload("Swapped", "Krenko, Mob Boss", basic="Mountain", total_cards=100)
        good = mock_deck_payload("Good", "Vito, Thorn of the Dusk Rose", basic="Swamp", total_cards=100)
        mock_client = MagicMock()
        mock_client.chat.side_effect = [llm_reply(bad), llm_reply(good)]
        reconstruct.set_builder(ReconstructBuilder(
            client=mock_client, catalog=self.catalog, price_lookup=self.price_lookup))
        res = reconstruct.trigger_build(self.r, sid, sync=True)
        self.assertEqual(res["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(res["drafts"][0]["commanders"], ["Vito, Thorn of the Dusk Rose"])
        self.assertEqual(mock_client.chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
