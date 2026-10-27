"""Pass E output must stay within the verified Pass D evidence."""
import json
import unittest

from src.server.domain.analytics.review_pipeline import e_writer, prompts


class WriterChecks(unittest.TestCase):
    def setUp(self):
        self.verified = {
            "verdict": {"headline": "Ramp supports the plan", "summary": "Mana is stable.", "grade": "B", "confidence": 0.8},
            "deck_identity": {"what_it_does": "Makes tokens.", "mana": {"summary": "36 lands; 12 sources."}},
            "scorecard": [{"axis": "mana", "score": 7, "one_line": "Enough ramp."}],
            "swaps": [{"id": "sw-1", "cut": {"name": "Front // Back"}, "add": {"name": "Sol Ring"}}],
            "top_changes": {"swap_ids": ["sw-1"], "why_these_first": "Sol Ring accelerates the plan."},
            "omitted": [{"kind": "swap", "item": "sw-2: cut X -> add Y", "reason": "removed"}],
            "questions_for_owner": ["Keep the token theme?"]
        }

    def test_clean_prose_passes(self):
        errors = e_writer.check_writer_output('{"verdict":{"grade":"B","confidence":0.8},"text":"The deck improves. Sol Ring helps. Front supports the plan; 36 lands and 12 sources."}', self.verified)
        self.assertEqual(errors, [])

    def test_unknown_card_name_is_rejected(self):
        self.assertTrue(any("card name" in e for e in e_writer.check_writer_output("Black Lotus wins.", self.verified)))

    def test_unknown_numeral_is_rejected(self):
        self.assertTrue(any("number" in e for e in e_writer.check_writer_output("The curve is 4.", self.verified)))

    def test_double_faced_card_full_name_and_faces_are_allowed(self):
        for name in ("Front // Back", "Front", "Back"):
            output = json.dumps({"verdict": {"grade": "B", "confidence": 0.8}, "text": f"{name} supports the plan."})
            self.assertEqual(e_writer.check_writer_output(output, self.verified), [])

    def test_omitted_swap_id_cannot_be_resurrected(self):
        self.assertTrue(any("swap id" in e for e in e_writer.check_writer_output("Prioritize sw-2.", self.verified)))

    def test_structured_verdict_cannot_upgrade_grade_or_confidence(self):
        output = '{"verdict":{"grade":"A","confidence":0.9}}'
        errors = e_writer.check_writer_output(output, self.verified)
        self.assertIn("verdict grade does not exactly match the verified input", errors)
        self.assertIn("verdict confidence does not exactly match the verified input", errors)
        embedded_claim = '{"verdict":{"grade":"B","confidence":0.8},"summary":"Grade A confidence 0.9."}'
        errors = e_writer.check_writer_output(embedded_claim, self.verified)
        self.assertIn("verdict grade claim does not match the verified input", errors)
        self.assertIn("verdict confidence claim does not match the verified input", errors)

    def test_grade_claim_in_prose_is_rejected(self):
        errors = e_writer.check_writer_output("The grade is A, and the deck is strong.", self.verified)
        self.assertTrue(any("verdict" in e for e in errors))

    def test_missing_or_invalid_output_verdict_is_rejected(self):
        for output in ('{"summary":"Fine"}', '{"verdict":"A"}', 'Final: {"verdict":{"grade":"A","confidence":0.8}}'):
            with self.subTest(output=output):
                self.assertTrue(any("verdict" in e for e in e_writer.check_writer_output(output, self.verified)))

    def test_single_word_unknown_card_at_sentence_start_is_rejected(self):
        self.assertTrue(any("card name" in e for e in e_writer.check_writer_output("Lotus wins the game.", self.verified)))

    def test_names_and_numbers_from_omitted_items_are_not_allowed(self):
        for phrase in ("Thrasios is the real problem here.", "Kindred Discovery is strong.", "There are 2 weak swaps."):
            with self.subTest(phrase=phrase):
                self.assertTrue(e_writer.check_writer_output(phrase, self.verified))

    def test_prompt_renders_only_from_verified_json_and_preferences(self):
        template = prompts.load("e_writer")
        rendered = template.render("user", verified_json='{"grade":"B"}', review_preferences="plain")
        self.assertIn('{"grade":"B"}', rendered)
        self.assertIn("plain", rendered)
        self.assertNotIn("{{", rendered)


if __name__ == "__main__":
    unittest.main()
