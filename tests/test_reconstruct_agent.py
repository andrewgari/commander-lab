"""Reconstruct LLM agent wrapper (reconstruct_agent.py). The LLM client is always mocked."""
import json
import os
import sys
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reconstruct_agent as ra

PREFS = [
    {"name": "Bat Swarm", "commanders": ["Vito, Thorn of the Dusk Rose"], "themes": ["lifedrain"],
     "strategies": ["aristocrats"], "art_style": "gothic", "flavor": "vampire court", "notes": "no stax"},
    {"name": "Goblin Party", "commanders": ["Krenko, Mob Boss"], "themes": ["goblins"],
     "strategies": ["go-wide"], "art_style": "", "flavor": "", "notes": ""},
]
TIER1 = [{"name": "Sol Ring", "quantity": 1, "source_deck": "Old Vamps", "color_identity": []},
         {"name": "Sanguine Bond", "quantity": 1, "source_deck": "Old Vamps", "color_identity": ["B"]}]
TIER2 = {"Skullclamp": 2, "Goblin Bombardment": 1}


@dataclass
class T3Card:  # pool module may hand over dataclasses instead of dicts
    name: str
    price: float | None
    color_identity: tuple = ()


TIER3 = [T3Card("Exquisite Blood", 7.5, ("B",)), T3Card("Mystery Card", None, ())]


def card(name, tier=1, qty=1, **kw):
    return {"name": name, "quantity": qty, "tier": tier, "why": f"{name} serves P1", **kw}


def deck(name="Bat Swarm", commanders=("Vito, Thorn of the Dusk Rose",), n=100):
    cards = [card(c) for c in commanders] + [card("Swamp", tier=2, qty=n - len(commanders))]
    return {"name": name, "commanders": list(commanders), "cards": cards,
            "explanation": "Brief: drain the table.", "open_questions": []}


def reply(*decks, fenced=True, prose="Here are your drafts."):
    body = json.dumps({"decks": list(decks)})
    return f"{prose}\n\n```json\n{body}\n```" if fenced else body


def mock_client(text):
    c = MagicMock()
    c.chat.return_value = text
    return c


def call(client, num=2, **kw):
    return ra.call_agent(PREFS[:num], TIER1, TIER2, TIER3, num, client=client, **kw)


class PromptTests(unittest.TestCase):
    def test_system_prompt_is_guide_plus_tier_instruction(self):
        c = mock_client(reply(deck(), deck("Goblin Party", ("Krenko, Mob Boss",))))
        call(c)
        messages = c.chat.call_args.args[0]
        self.assertEqual(messages[0]["role"], "system")
        system = messages[0]["content"]
        guide = ra.load_guide()
        self.assertTrue(system.startswith(guide))
        self.assertIn("Reconstruct Builder", guide)
        self.assertIn("## 5. Splitting one shared pool between two decks", system)
        self.assertIn(ra.TIER_PREFERENCE_INSTRUCTION, system)
        self.assertIn("strongly prefer Tier 1", system)
        self.assertIn("then Tier 2", system)
        self.assertIn("Tier 3", system)
        self.assertIn(ra.SHARED_QUANTITY_INSTRUCTION, system)
        # no-tool-calls guard
        self.assertIn(ra.NO_TOOLS_INSTRUCTION, system)
        self.assertIn("DO NOT use any tools", system)
        # also restated next to the pools
        self.assertIn(ra.TIER_PREFERENCE_INSTRUCTION, messages[1]["content"])

    def test_guide_file_ships_in_image_path(self):
        self.assertTrue(ra.GUIDE_PATH.is_file())
        self.assertNotIn("docs", ra.GUIDE_PATH.parts)  # docs/ is .dockerignore'd

    def test_custom_guide_override(self):
        msgs = ra.build_messages(PREFS[:1], [], [], [], 1, guide="GUIDE TEXT")
        self.assertTrue(msgs[0]["content"].startswith("GUIDE TEXT"))

    def test_preferences_and_pools_serialized(self):
        c = mock_client(reply(deck(), deck("Goblin Party", ("Krenko, Mob Boss",))))
        call(c, context={"bracket": 2})
        user = c.chat.call_args.args[0][1]["content"]
        prefs_block = user.split("## Preferences", 1)[1].split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(prefs_block), PREFS)
        self.assertIn("build exactly 2 Commander decks", user)
        self.assertIn('"bracket": 2', user)
        # Tier 1: name|qty|deck|ci
        self.assertIn("### Tier 1 (previous_deck): 2 cards", user)
        self.assertIn("name|qty|deck|ci", user)
        self.assertIn("Sol Ring|1|Old Vamps|", user)
        self.assertIn("Sanguine Bond|1|Old Vamps|B", user)
        # Tier 2 from a {name: qty} mapping
        self.assertIn("### Tier 2 (owned): 2 cards", user)
        self.assertIn("Skullclamp|2", user)
        self.assertIn("Goblin Bombardment|1", user)
        # Tier 3 from dataclasses, prices, unknown price explicit
        self.assertIn("name|usd|ci", user)
        self.assertIn("Exquisite Blood|7.50|B", user)
        self.assertIn("Mystery Card|?|", user)
        # schema is embedded
        self.assertIn('"DraftDeck"', user)

    def test_pool_format_is_compact(self):
        big = [{"name": f"Card {i}", "price": 1.0} for i in range(500)]
        text = ra.format_pool(3, big)
        self.assertLess(len(text) / 500, 20)  # ~one short line per card
        self.assertEqual(ra.format_pool(2, None), "### Tier 2: (empty)")

    def test_structured_output_requested(self):
        c = mock_client(reply(deck()))
        call(c, num=1)
        rf = c.chat.call_args.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertEqual(rf["json_schema"]["name"], ra.SCHEMA_NAME)
        props = rf["json_schema"]["schema"]["$defs"]["DraftDeck"]["properties"]
        for k in ("commanders", "cards", "explanation"):
            self.assertIn(k, props)
        self.assertIn("why", rf["json_schema"]["schema"]["$defs"]["DraftCard"]["properties"])
        c2 = mock_client(reply(deck()))
        call(c2, num=1, structured=False)
        self.assertNotIn("response_format", c2.chat.call_args.kwargs)

    def test_not_repair_mode_without_errors(self):
        c = mock_client(reply(deck()))
        res = call(c, num=1, validation_errors=[])
        self.assertFalse(res.repair)
        self.assertNotIn("REPAIR MODE", c.chat.call_args.args[0][1]["content"])


class RepairTests(unittest.TestCase):
    def test_repair_prompt_includes_validator_errors_and_previous_drafts(self):
        errors = [
            {"code": "deck_size", "deck": 0, "cards": [], "message": "deck has 99 cards, expected 100"},
            {"code": "shared_pool", "deck": None, "cards": ["Sol Ring"], "message": "Sol Ring used 2x, own 1"},
        ]
        prev = [deck(n=99), deck("Goblin Party", ("Krenko, Mob Boss",))]
        c = mock_client(reply(deck(), deck("Goblin Party", ("Krenko, Mob Boss",))))
        res = call(c, validation_errors=errors, previous_drafts=prev)
        self.assertTrue(res.repair)
        user = c.chat.call_args.args[0][1]["content"]
        self.assertIn("## REPAIR MODE", user)
        self.assertIn("Fix ONLY the issues listed below", user)
        self.assertIn("deck has 99 cards, expected 100", user)
        self.assertIn("Sol Ring used 2x, own 1", user)
        self.assertIn('"code": "shared_pool"', user)
        self.assertIn("### Previous drafts", user)
        self.assertIn('"Goblin Party"', user)
        # system prompt is still the guide
        self.assertTrue(c.chat.call_args.args[0][0]["content"].startswith(ra.load_guide()))

    def test_repair_accepts_dataclass_and_string_errors(self):
        @dataclass
        class Err:
            code: str
            message: str

        text = ra.build_user_prompt(PREFS[:1], [], [], [], 1,
                                    validation_errors=[Err("banned", "Mana Crypt is banned"), "plain error"])
        self.assertIn('"code": "banned"', text)
        self.assertIn("Mana Crypt is banned", text)
        self.assertIn("plain error", text)


class ParseTests(unittest.TestCase):
    def test_successful_parse(self):
        d1 = deck()
        d1["cards"][0].update(source_deck="Old Vamps", roles=["commander"], pillar="P1 Drain",
                              cost_usd=0, flags=[])
        c = mock_client(reply(d1, deck("Goblin Party", ("Krenko, Mob Boss",))))
        res = call(c)
        self.assertIsInstance(res, ra.AgentResult)
        self.assertEqual(len(res.decks), 2)
        first = res.decks[0]
        self.assertEqual(first.commanders, ["Vito, Thorn of the Dusk Rose"])
        self.assertEqual(first.card_count, 100)
        self.assertEqual(first.cards[0].pillar, "P1 Drain")
        self.assertEqual(first.cards[0].tier, 1)
        self.assertEqual(first.explanation, "Brief: drain the table.")
        self.assertEqual(res.decks[1].name, "Goblin Party")
        self.assertEqual(res.as_dicts()[1]["commanders"], ["Krenko, Mob Boss"])
        self.assertIn("```json", res.raw)
        c.chat.assert_called_once()

    def test_bare_json_and_last_fenced_block(self):
        self.assertEqual(len(ra.parse_reply(reply(deck(), fenced=False), 1)), 1)
        text = "```json\n{\"draft\": 1}\n```\nrevised:\n" + reply(deck(), prose="")
        self.assertEqual(ra.parse_reply(text, 1)[0].name, "Bat Swarm")

    def test_partner_pair(self):
        d = deck(commanders=("Tymna the Weaver", "Kraum, Ludevic's Opus"))
        self.assertEqual(ra.parse_reply(reply(d), 1)[0].commanders, ["Tymna the Weaver", "Kraum, Ludevic's Opus"])

    def test_card_count_not_enforced_here(self):
        # 100-card / legality checks belong to the deterministic validator; the wrapper only parses.
        self.assertEqual(ra.parse_reply(reply(deck(n=99)), 1)[0].card_count, 99)


class MalformedTests(unittest.TestCase):
    def assert_retryable(self, exc_type, text, num=1):
        c = mock_client(text)
        with self.assertRaises(exc_type) as cm:
            call(c, num=num)
        self.assertIsInstance(cm.exception, ra.AgentOutputError)
        self.assertTrue(cm.exception.retryable)
        return cm.exception

    def test_malformed_json(self):
        e = self.assert_retryable(ra.MalformedJSONError, "```json\n{\"decks\": [ {\"name\": \n```")
        self.assertIn("{\"decks\"", e.raw)
        self.assert_retryable(ra.MalformedJSONError, "I could not build these decks.")
        self.assert_retryable(ra.MalformedJSONError, "")
        self.assert_retryable(ra.MalformedJSONError, None)
        self.assert_retryable(ra.MalformedJSONError, "```json\n\"just a string\"\n```")

    def test_schema_mismatch_missing_fields(self):
        d = deck()
        del d["explanation"]
        e = self.assert_retryable(ra.SchemaMismatchError, reply(d))
        self.assertTrue(any(x["loc"].endswith("explanation") for x in e.details))

    def test_schema_mismatch_bad_card(self):
        d = deck()
        d["cards"][1]["tier"] = 4
        self.assert_retryable(ra.SchemaMismatchError, reply(d))
        d = deck()
        d["cards"][0]["why"] = "   "
        self.assert_retryable(ra.SchemaMismatchError, reply(d))
        d = deck()
        d["cards"][1]["quantity"] = 0
        self.assert_retryable(ra.SchemaMismatchError, reply(d))

    def test_schema_mismatch_commanders(self):
        self.assert_retryable(ra.SchemaMismatchError, reply(deck(commanders=())))
        self.assert_retryable(ra.SchemaMismatchError, reply(deck(commanders=("A", "B", "C"))))

    def test_schema_mismatch_wrong_shape(self):
        self.assert_retryable(ra.SchemaMismatchError, '```json\n{"drafts": []}\n```')
        self.assert_retryable(ra.SchemaMismatchError, reply())

    def test_wrong_deck_count(self):
        e = self.assert_retryable(ra.SchemaMismatchError, reply(deck()), num=2)
        self.assertIn("expected 2", str(e))
        self.assert_retryable(ra.SchemaMismatchError, reply(deck(), deck(), deck()), num=2)

    def test_input_errors_not_retryable(self):
        c = mock_client(reply(deck()))
        for bad in (0, 3, True, "2"):
            with self.assertRaises(ra.AgentInputError) as cm:
                ra.call_agent(PREFS[:1], [], [], [], bad, client=c)
            self.assertFalse(cm.exception.retryable)
        with self.assertRaises(ra.AgentInputError):
            ra.call_agent(PREFS, [], [], [], 1, client=c)
        c.chat.assert_not_called()

    def test_oracle_errors_propagate(self):
        from analytics.oracle_client import OracleUnavailable

        c = MagicMock()
        c.chat.side_effect = OracleUnavailable("down")
        with self.assertRaises(OracleUnavailable):
            call(c, num=1)


class ClientTests(unittest.TestCase):
    def test_client_factory_used(self):
        c = mock_client(reply(deck()))
        factory = MagicMock(return_value=c)
        ra.call_agent(PREFS[:1], TIER1, TIER2, TIER3, 1, client_factory=factory)
        factory.assert_called_once_with()

    def test_default_client_is_existing_oracle_client(self):
        c = mock_client(reply(deck()))
        with patch("analytics.oracle_client.OracleClient.from_env", return_value=c) as fe:
            ra.call_agent(PREFS[:1], TIER1, TIER2, TIER3, 1)
        fe.assert_called_once()
        c.chat.assert_called_once()
        # Verify tool-calling is disabled — the model should return a single-turn JSON response.
        _, kwargs = c.chat.call_args
        assert kwargs.get("tools") == [], "tools must be empty list to prevent tool calls"
        assert kwargs.get("tool_choice") == "none", "tool_choice must be 'none' to prevent tool calls"

    def test_no_network(self):
        # Belt and braces: any real HTTP attempt during a mocked call fails the test.
        with patch("httpx.Client.request", side_effect=AssertionError("network call")):
            call(mock_client(reply(deck())), num=1)


if __name__ == "__main__":
    unittest.main()
