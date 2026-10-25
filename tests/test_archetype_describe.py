"""Archetype description Draft/Enhance (ARCH-1). No network: LLM chain stubbed.

Run: python -m pytest tests/test_archetype_describe.py   (or python tests/test_archetype_describe.py)
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import archetype_describe as ad
from analytics import archetypes as ar
from analytics.interactions import llm as llm_mod
from tests.test_identity import JsonProvider

GOOD = {"description": "This deck wants to flood the board with tokens and turn them sideways. "
                       "It wins by going wide, then pumping the team for one big attack."}


def chain(*answers, fail=False):
    return llm_mod.LLMChain([JsonProvider(answers, fail=fail)])


VOCAB = ar.role_vocabulary()
CARDS = ["Sol Ring", "Rhystic Study", "Doubling Season"]
OTHERS = [{"name": "Voltron", "description": "Suit up one creature."},
          {"name": "Spellslinger", "description": "Cast lots of instants and sorceries."}]


class TestCleanRequest(unittest.TestCase):
    def test_draft_mode_when_empty(self):
        out = ad.clean_request({"name": "Go Wide"})
        self.assertEqual(out["mode"], "draft")
        self.assertEqual(out["description"], "")

    def test_improve_mode_when_text(self):
        out = ad.clean_request({"name": "Go Wide", "description": "tokens lol"})
        self.assertEqual(out["mode"], "improve")

    def test_bad_input(self):
        for bad in (None, [], {}, {"name": "  ", "description": ""}, {"name": 5},
                    {"name": "x" * (ar.MAX_NAME + 1)},
                    {"description": "y" * (ar.MAX_DESCRIPTION + 201)}):
            with self.assertRaises(ad.ArchetypeDescribeError, msg=repr(bad)):
                ad.clean_request(bad)

    def test_roles_filtered_to_strings_and_capped(self):
        out = ad.clean_request({"name": "A", "roles": ["produces:treasure", 7, None] + ["consumes:life"] * 20})
        self.assertEqual(out["roles"][0], "produces:treasure")
        self.assertLessEqual(len(out["roles"]), ar.MAX_ROLES)
        self.assertNotIn(7, out["roles"])


class TestValidateReply(unittest.TestCase):
    def test_rejects_card_name(self):
        with self.assertRaises(ad.ArchetypeDescribeUnavailable) as cm:
            ad.validate_reply({"description": "Tokens are great, Doubling Season makes more."}, CARDS)
        self.assertEqual(cm.exception.kind, "bad_response")
        self.assertIn("named a specific card", str(cm.exception))

    def test_rejects_hard_count(self):
        for text in ("Run 10 token makers and go wide.",
                     "You want at least 5 board wipes.",
                     "Include 38 lands and 10 ramp spells."):
            with self.assertRaises(ad.ArchetypeDescribeUnavailable, msg=text):
                ad.validate_reply({"description": text}, CARDS)

    def test_rejects_empty(self):
        for data in ({"description": " "}, {}, ["nope"], {"description": None}):
            with self.assertRaises(ad.ArchetypeDescribeUnavailable):
                ad.validate_reply(data, CARDS)

    def test_ok_passes_through(self):
        out = ad.validate_reply(GOOD, CARDS)
        self.assertTrue(out.startswith("This deck wants"))


class TestDescribe(unittest.TestCase):
    def run_it(self, payload, ch):
        return ad.describe_archetype(ch, payload, roles_vocab=VOCAB, card_names=CARDS,
                                     archetypes=OTHERS)

    def test_draft(self):
        ch = chain(GOOD)
        out = self.run_it({"name": "Go Wide"}, ch)
        self.assertEqual((out["mode"], out["description"]), ("draft", GOOD["description"]))
        self.assertEqual(out["provider"], "gemini")
        prompt = ch.providers[0].prompts[0][1]
        self.assertIn("Task: draft an archetype description.", prompt)
        self.assertIn("Archetype name: Go Wide", prompt)

    def test_enhance_keeps_user_text_in_prompt(self):
        ch = chain(GOOD)
        out = self.run_it({"name": "Go Wide", "description": "tokens lol"}, ch)
        self.assertEqual(out["mode"], "improve")
        self.assertIn("Existing description to improve: tokens lol", ch.providers[0].prompts[0][1])

    def test_roles_and_other_archetypes_in_prompt(self):
        ch = chain(GOOD)
        self.run_it({"name": "Go Wide", "roles": ["produces:treasure"]}, ch)
        prompt = ch.providers[0].prompts[0][1]
        self.assertIn("produces:treasure:", prompt)          # tag with its meaning
        self.assertIn("Voltron", prompt)                     # registry context
        self.assertNotIn("Go Wide: ", prompt.split("Task:")[0].replace("Archetype name: Go Wide", ""))

    def test_failures(self):
        for ch, kind in ((chain(GOOD, fail=True), "llm_failed"),
                         (chain({"description": " "}), "bad_response"),
                         (chain(["nope"]), "bad_response"),
                         (llm_mod.LLMChain([]), "no_llm")):
            with self.assertRaises(ad.ArchetypeDescribeUnavailable) as cm:
                self.run_it({"name": "A"}, ch)
            self.assertEqual(cm.exception.kind, kind)

    def test_bad_input_never_calls_model(self):
        ch = chain(GOOD)
        with self.assertRaises(ad.ArchetypeDescribeError):
            self.run_it({}, ch)
        self.assertEqual(ch.providers[0].prompts, [])

    def test_long_output_trimmed(self):
        out = self.run_it({"name": "A"}, chain({"description": "Sentence one. " * 400}))
        self.assertLessEqual(len(out["description"]), ar.MAX_DESCRIPTION)


class TestRoute(unittest.TestCase):
    URL = "/api/archetypes/describe"

    def setUp(self):
        from fastapi.testclient import TestClient
        from app import app
        import app as app_mod
        from tests.test_archetypes import FakeRedis
        self.app_mod = app_mod
        self.client = TestClient(app)
        self.fake = FakeRedis()
        self.fake.set("inventory", __import__("json").dumps({n: {} for n in CARDS}))

    def post(self, body, ch):
        with patch.object(self.app_mod, "r", self.fake), \
                patch("analytics.interactions.llm.default_chain", return_value=ch):
            return self.client.post(self.URL, json=body)

    def test_draft_ok_nothing_saved(self):
        res = self.post({"name": "Go Wide"}, chain(GOOD))
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertEqual((data["mode"], data["description"]), ("draft", GOOD["description"]))
        self.assertEqual(ar.list_archetypes(self.fake), [])   # stateless: registry untouched

    def test_enhance_ok(self):
        res = self.post({"name": "Go Wide", "description": "tokens lol"}, chain(GOOD))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["mode"], "improve")

    def test_422_on_empty(self):
        self.assertEqual(self.post({}, chain(GOOD)).status_code, 422)

    def test_503_no_llm(self):
        res = self.post({"name": "A"}, llm_mod.LLMChain([]))
        self.assertEqual((res.status_code, res.json()["error_kind"]), (503, "no_llm"))

    def test_502_llm_failed(self):
        res = self.post({"name": "A"}, chain(GOOD, fail=True))
        self.assertEqual((res.status_code, res.json()["error_kind"]), (502, "llm_failed"))

    def test_502_draft_named_a_card_plain_message(self):
        res = self.post({"name": "A"}, chain({"description": "Just play Sol Ring and win."}))
        self.assertEqual(res.status_code, 502)
        self.assertIn("named a specific card", res.json()["error"])

    def test_502_draft_with_hard_count_plain_message(self):
        res = self.post({"name": "A"}, chain({"description": "Run 10 ramp spells, always."}))
        self.assertEqual(res.status_code, 502)
        self.assertIn("hard count", res.json()["error"])


if __name__ == "__main__":
    unittest.main()
