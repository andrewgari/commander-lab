"""Pass E name checker: prose may repeat anything the verified payload literally says, and nothing from outside it."""
import json
import unittest
from dataclasses import asdict

from src.server.domain.analytics.review_pipeline import e_writer
from tests.test_review_pipeline_verify_replay import replay

NAME_ERR = "unverified card name or proper-name phrase"


def _name_errors(prose, verified):
    text = json.dumps({"verdict": verified["verdict"], "summary": prose})
    return [e for e in e_writer.check_writer_output(text, verified) if e.startswith(NAME_ERR)]


class PayloadEcho(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verified = asdict(replay("saffi")[-1].verified) | {"verdict": {"grade": "B", "confidence": 0.7}}

    def test_every_reviewable_payload_string_echoed_verbatim_has_no_name_errors(self):
        prose = "\n".join(v for _, v in e_writer._walk_verified(self.verified) if isinstance(v, str))
        self.assertEqual(_name_errors(prose, self.verified), [])

    def test_card_absent_from_payload_is_still_rejected(self):
        self.assertTrue(_name_errors("Craterhoof Behemoth ends the game.", self.verified))
        self.assertTrue(_name_errors("Cast Craterhoof Behemoth.", self.verified))


class PhraseAllowlist(unittest.TestCase):
    def setUp(self):
        self.verified = {
            "verdict": {"grade": "B", "confidence": 0.8},
            "strengths": [{"id": "s1", "title": "Scattered Groves fixes Selesnya colors", "body": "Fauna Shaman's tutor suite is deep."}],
            "swaps": [{"id": "sw-1", "cut": {"name": "Plains"}, "add": {"name": "Sol Ring"}}],
            "omitted": [{"kind": "swap", "item": "sw-2: cut Mana Crypt -> add Rhystic Study", "reason": "removed"}],
        }

    def check(self, prose):
        return _name_errors(prose, self.verified)

    def test_phrase_from_payload_prose_passes(self):
        self.assertEqual(self.check("Scattered Groves keeps Selesnya stable."), [])

    def test_possessive_of_payload_phrase_passes(self):
        self.assertEqual(self.check("Scattered Groves\u2019s slot and Fauna Shaman's role matter."), [])

    def test_possessive_of_payload_name_passes(self):
        self.assertEqual(self.check("Sol Ring's speed matters."), [])

    def test_sentence_initial_ordinary_words_are_not_names(self):
        self.assertEqual(self.check("She plays it. Most lists run it. Many do. No one cuts it. All of it works."), [])

    def test_names_only_in_omitted_are_still_rejected(self):
        self.assertTrue(self.check("Mana Crypt would help."))
        self.assertTrue(self.check("Add Rhystic Study."))

    def test_unrelated_capitalized_phrase_is_rejected(self):
        self.assertTrue(self.check("Craterhoof Behemoth ends it."))

    def test_phrase_must_match_whole_not_substring_of_word(self):
        self.assertTrue(self.check("Scatter Grove is fine."))


if __name__ == "__main__":
    unittest.main()
