"""Pillar description generate/improve (backend). No network: LLM chain stubbed.

Run: python -m pytest tests/test_pillar_describe.py
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import identity, pillar_describe
from src.server.domain.analytics.interactions import llm as llm_mod
from tests.test_identity import JsonProvider
from tests.test_interactions import FakeRedis
from tests.test_pillar_fit import load_hh

GOOD = {"description": "Extra combat phases and untap effects that let the commander attack twice."}


def chain(*answers, fail=False):
    return llm_mod.LLMChain([JsonProvider(answers, fail=fail)])


class TestDescribe(unittest.TestCase):
    def setUp(self):
        self.deck, _ = load_hh()

    def run_it(self, payload, ch=None):
        return pillar_describe.describe_pillar(self.deck, ch or chain(GOOD), {}, payload)

    def test_generate_from_name_and_prompt(self):
        ch = chain(GOOD)
        out = self.run_it({"name": "Swing twice", "prompt": "focus on combat"}, ch)
        self.assertEqual(out["mode"], "generate")
        self.assertEqual(out["description"], GOOD["description"])
        prompt = ch.providers[0].prompts[0][1]
        self.assertIn("Pillar name: Swing twice", prompt)
        self.assertIn("User prompt: focus on combat", prompt)

    def test_improve_when_description_given(self):
        ch = chain(GOOD)
        out = self.run_it({"description": "more combat"}, ch)
        self.assertEqual(out["mode"], "improve")
        self.assertIn("Existing description to improve: more combat", ch.providers[0].prompts[0][1])

    def test_validation(self):
        for bad in (None, [], {}, {"name": "  "}, {"name": 5}, {"name": "x" * 41}, {"prompt": "y" * 1001}):
            with self.assertRaises(pillar_describe.PillarDescribeError, msg=bad):
                self.run_it(bad)

    def test_failures(self):
        for ch, kind in ((chain(GOOD, fail=True), "llm_failed"), (chain({"description": " "}), "bad_response"),
                         (chain(["nope"]), "bad_response"), (llm_mod.LLMChain([]), "no_llm")):
            with self.assertRaises(pillar_describe.PillarDescribeUnavailable) as cm:
                self.run_it({"name": "A"}, ch)
            self.assertEqual(cm.exception.kind, kind)

    def test_long_output_is_clamped(self):
        out = self.run_it({"name": "A"}, chain({"description": "Sentence one. " * 100}))
        self.assertLessEqual(len(out["description"]), identity.DESC_MAX)

    def test_other_pillars_in_prompt_excluding_self(self):
        r = FakeRedis()
        ident = identity.new_identity(self.deck)
        identity.save_identity(r, self.deck, ident)
        a = identity.add_pillar(r, self.deck, {"name": "Alpha", "description": "aaa"})
        b = identity.add_pillar(r, self.deck, {"name": "Beta", "description": "bbb"})
        ident = identity.load_identity(r, self.deck)
        ch = chain(GOOD)
        pillar_describe.describe_pillar(self.deck, ch, {}, {"name": "Beta"}, ident, exclude_id=b["id"])
        prompt = ch.providers[0].prompts[0][1]
        self.assertIn("- Alpha: aaa", prompt)
        self.assertNotIn("- Beta: bbb", prompt)


class TestRoute(unittest.TestCase):
    URL = "/api/decks/archidekt:417707/identity/pillars/describe"

    def setUp(self):
        from fastapi.testclient import TestClient
        from src.server.app.main import app
        import src.server.app.main as app_mod
        self.app_mod = app_mod
        self.client = TestClient(app)
        self.deck, _ = load_hh()
        self.fake = FakeRedis()

    def post(self, body, ch):
        with patch("src.server.app.main.registry") as reg, patch.object(self.app_mod, "r", self.fake), \
                patch("src.server.domain.analytics.interactions.llm.default_chain", return_value=ch):
            reg.find_deck.return_value = self.deck
            return self.client.post(self.URL, json=body)

    def test_ok_and_nothing_saved(self):
        res = self.post({"name": "Swing twice"}, chain(GOOD))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["description"], GOOD["description"])
        self.assertIsNone(identity.load_identity(self.fake, self.deck))

    def test_422_on_empty(self):
        self.assertEqual(self.post({}, chain(GOOD)).status_code, 422)

    def test_502_and_503(self):
        res = self.post({"name": "A"}, chain(GOOD, fail=True))
        self.assertEqual((res.status_code, res.json()["error_kind"]), (502, "llm_failed"))
        res = self.post({"name": "A"}, llm_mod.LLMChain([]))
        self.assertEqual((res.status_code, res.json()["error_kind"]), (503, "no_llm"))

    def test_unknown_deck_404(self):
        with patch("src.server.app.main.registry") as reg:
            reg.find_deck.return_value = None
            reg.list_decks.return_value = []
            self.assertEqual(self.client.post(self.URL, json={"name": "A"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
