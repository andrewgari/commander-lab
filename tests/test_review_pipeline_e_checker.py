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


class BareListEvidence(unittest.TestCase):
    """Strings and numbers that sit directly in a payload list (win_paths, success_criteria, quirks) are evidence too."""

    def setUp(self):
        self.verified = {
            "verdict": {"grade": "B", "confidence": 0.8},
            "deck_identity": {
                "success_criteria": ["Soldiers have meaningful lords in play by mid-game (Field Marshal, Coat of Arms)."],
                "intentional_quirks": ["Heavy snow mana (24 Snow-Covered Plains) and Rashmi and Ragavan as enablers."],
            },
        }

    def test_phrase_and_number_from_a_bare_list_element_pass(self):
        prose = "Soldiers have meaningful lords. Rashmi and Ragavan help. Run 24 Snow-Covered Plains."
        self.assertEqual(_name_errors(prose, self.verified), [])
        text = json.dumps({"verdict": self.verified["verdict"], "summary": prose})
        self.assertEqual([e for e in e_writer.check_writer_output(text, self.verified) if "number" in e], [])

    def test_number_not_in_the_payload_is_still_rejected(self):
        text = json.dumps({"verdict": self.verified["verdict"], "summary": "Run 25 Snow-Covered Plains."})
        self.assertTrue([e for e in e_writer.check_writer_output(text, self.verified) if "number" in e])


class PlainSentenceOpeners(unittest.TestCase):
    def setUp(self):
        self.verified = {"verdict": {"grade": "B", "confidence": 0.8},
                         "strengths": [{"id": "s1", "claim": "Combat damage with Sol Ring.", "cards": [{"name": "Sol Ring"}]}]}

    def test_capitalized_ordinary_words_opening_a_sentence_are_not_names(self):
        for prose in ("Critically, the deck is slow.", "Mostly fine. Apply pressure early.",
                      "Flavor is fine.", "Combat damage matters."):
            self.assertEqual(_name_errors(prose, self.verified), [], prose)

    def test_color_words_are_not_names(self):
        self.assertEqual(_name_errors("Blue/Red spells compete against Green ramp. White decks differ.", self.verified), [])

    def test_a_real_one_word_card_name_not_in_the_payload_is_still_rejected(self):
        self.assertTrue(_name_errors("Entomb finds the target.", self.verified))
        self.assertTrue(_name_errors("Cast it. Counterspell answers it.", self.verified))

    def test_fabricated_one_word_names_are_still_rejected(self):
        for prose in ("Zorblax ends the game.", "Xqzvorn is the wincon here.", "Blorgly ends the game.", "Quickly, Zorblax ends it."):
            self.assertTrue(_name_errors(prose, self.verified), prose)

    def test_sentence_starter_allowlist_holds_no_real_card_name(self):
        try:
            import numpy as np
            from src.server.domain.analytics import card2vec
            with np.load(card2vec.DEFAULT_PATH) as z:
                names = {e_writer.normalize(str(n)) for n in z["names"]}
        except Exception:
            self.skipTest("card corpus unavailable")
        self.assertEqual(sorted(w for w in e_writer._SENTENCE_STARTERS
                                if w in names and w not in {"six"}), [])  # "six" is a pre-existing numeral starter

    def test_prose_opener_before_a_color_identity_word_is_accepted(self):
        for prose in ("This Mardu list wants more pressure.", "Redundant Selesnya recursion is the theme.", "Scores by axis are fine.",
                      "Volume is fine and colors are the problem.", "Grade C stays."):
            self.assertFalse(_name_errors(prose, self.verified), prose)

    def test_prose_opener_does_not_launder_an_invented_name(self):
        for prose in ("This Zorblax ends the game.", "Redundant Zorblax ends it.", "Then Craterhoof Behemoth ends it."):
            self.assertTrue(_name_errors(prose, self.verified), prose)

    def test_live_writer_openers_are_prose_not_names(self):
        # Seen from claude-sonnet-5-5 on lara-uncharted: headline / bullet openers that are ordinary review vocabulary.
        for prose in ("Coherent Sol Ring plan with a smooth curve.", "Proposed fixes: Sol Ring first.",
                      "Proposed: swap in Sol Ring.", "Protection for Sol Ring is layered."):
            self.assertEqual(_name_errors(prose, self.verified), [], prose)

    def test_vocabulary_openers_do_not_launder_an_invented_name(self):
        for prose in ("Coherent Zorblax plan.", "Proposed Zorblax for Sol Ring.", "Protection for Zorblax is layered.",
                      "Proposed Craterhoof Behemoth."):
            self.assertTrue(_name_errors(prose, self.verified), prose)

    def test_multi_word_unverified_name_is_still_rejected(self):
        self.assertTrue(_name_errors("Critically, Craterhoof Behemoth ends it.", self.verified))


if __name__ == "__main__":
    unittest.main()
