"""Document assembly validates the complete v1 payload at the pipeline boundary."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator, FormatChecker

from src.server.domain.analytics.review_pipeline import config
from src.server.domain.analytics.review_pipeline.runner import assemble_review_document
from tests.review_document_refs import load_schema

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "tests/fixtures/review_document/example.json").read_text())


class AssemblyTests(unittest.TestCase):
    def assemble(self, *, top_changes=None, swaps=None):
        ctx = SimpleNamespace(
            deck_id="fixture-atraxa-counters", deck_name="Atraxa, Counters Everywhere",
            commanders=("Atraxa, Praetors' Voice",), fact_pack_hash="f" * 64,
            estimated_bracket=3, target_bracket=3, bracket_reasons=("Test reason",),
        )
        verified = SimpleNamespace(
            strengths=EXAMPLE["strengths"], problems=EXAMPLE["problems"],
            swaps=EXAMPLE["swaps"][:1] if swaps is None else swaps,
            cuts_to_consider=EXAMPLE["cuts_to_consider"], top_changes=top_changes,
            omitted=EXAMPLE["omitted"], axes=EXAMPLE["scorecard"],
        )
        writer = {key: copy.deepcopy(EXAMPLE[key]) for key in
                  ("verdict", "deck_identity", "mana", "questions_for_owner")}
        writer["deck_identity"]["win_paths"] = ["Counters"]
        writer["deck_identity"]["success_criteria"] = []
        writer["deck_identity"]["intentional_quirks"] = []
        return assemble_review_document(ctx=ctx, fact_pack={"charts": EXAMPLE["charts"], "cards": [
                                            {"name": "Atraxa, Praetors' Voice", "oracle_id": "7e6b9b59-cd68-4e3c-827b-38833c92d6eb"}]},
                                        verified=verified, writer_output=writer,
                                        config=config.PipelineConfig(), generated_at="2026-10-26T18:30:00Z")

    def test_assembled_document_validates_and_uses_code_owned_metadata_and_charts(self):
        doc = self.assemble(top_changes=EXAMPLE["top_changes"], swaps=EXAMPLE["swaps"])
        errors = list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(doc))
        self.assertEqual(errors, [])
        self.assertEqual(doc["charts"], EXAMPLE["charts"])
        self.assertEqual(doc["meta"]["prompt_versions"]["A"], "v1")
        self.assertEqual(doc["meta"]["prompt_versions"]["E"], "v1")
        self.assertEqual(doc["meta"]["fact_pack_hash"], "f" * 64)
        self.assertIn("A", doc["meta"]["models"])

    def test_fewer_than_three_verified_swaps_are_not_padded_and_validate(self):
        doc = self.assemble(top_changes={"swap_ids": ["sw-1"], "why_these_first": "One valid change."})
        self.assertEqual(len(doc["swaps"]), 1)
        self.assertEqual(doc["top_changes"]["swap_ids"], ["sw-1"])
        self.assertEqual(list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(doc)), [])

    def test_null_top_changes_after_verification_validates_without_inventing_swaps(self):
        doc = self.assemble(top_changes=None)
        self.assertIsNone(doc["top_changes"])
        self.assertEqual(list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(doc)), [])

    def test_run_review_runs_a_through_d_e_and_returns_schema_validated_document(self):
        """The public pipeline entrypoint returns the assembled v1 doc, not partial pass outputs."""
        from src.server.domain.analytics.review_pipeline.runner import run_review

        calls = []
        def writer(verified):
            calls.append(verified)
            return json.dumps({"verdict": verified["verdict"], "deck_identity": {
                "what_it_does": "test deck.", "win_paths": ["combat"],
                "success_criteria": [], "intentional_quirks": []}, "mana": {"summary": "mana.", "issues": []},
                "questions_for_owner": []})

        ctx = SimpleNamespace(
            deck_id="fixture-atraxa-counters", deck_name="Atraxa, Counters Everywhere",
            commanders=("Atraxa, Praetors' Voice",), fact_pack_hash="f" * 64,
            estimated_bracket=3, target_bracket=3, bracket_reasons=("Test reason",),
        )
        fact_pack = {"charts": EXAMPLE["charts"], "cards": [
            {"name": "Atraxa, Praetors' Voice", "oracle_id": "7e6b9b59-cd68-4e3c-827b-38833c92d6eb"}]}
        result = run_review(ctx=ctx, fact_pack=fact_pack, pool=object(), client=object(),
                            writer=writer, config=config.PipelineConfig(),
                            run_passes=lambda *args, **kwargs: SimpleNamespace(
                                verified=SimpleNamespace(verdict={"headline": "test verdict", "summary": "test summary", "grade": "B", "confidence": 0.8}, swaps=[], strengths=[], problems=[], cuts_to_consider=[],
                                    top_changes=None, omitted=[], axes=EXAMPLE["scorecard"]), trace=None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["schema_version"], "review_document.v1")
        self.assertEqual(list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(result)), [])


if __name__ == "__main__":
    unittest.main()
