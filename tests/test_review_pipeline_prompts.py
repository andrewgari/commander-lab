"""Prompt templates: loading, rendering, versioning, and the quality-standard-verbatim guarantee."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.server.domain.analytics.review_pipeline import config, prompts, schemas

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "docs" / "reviewer" / "CONTRACT.md").read_text()


def contract_section6_bullets() -> list[str]:
    sec = CONTRACT.split("## 6. Review quality standard", 1)[1].split("\n## 7.", 1)[0]
    return [l.strip() for l in sec.splitlines() if l.strip().startswith("- ")]


class TemplateTests(unittest.TestCase):
    def test_sections_parse_and_render(self):
        t = prompts.Template.parse("a_x", "v1", "<!-- @header\nnote\n-->\n<!-- @section one -->\nHello {{name}}\n<!-- @section two -->\nBye")
        self.assertEqual(t.render("one", name="Bob"), "Hello Bob")
        self.assertEqual(t.render("two"), "Bye")
        self.assertEqual(t.version, "v1")

    def test_missing_placeholder_value_fails_loudly(self):
        t = prompts.Template.parse("a_x", "v1", "<!-- @section one -->\nHello {{name}} {{other}}")
        with self.assertRaises(prompts.PromptError) as cm:
            t.render("one", name="Bob")
        self.assertIn("other", str(cm.exception))

    def test_unknown_section_fails_loudly(self):
        t = prompts.Template.parse("a_x", "v1", "<!-- @section one -->\nx")
        with self.assertRaises(prompts.PromptError):
            t.render("nope")

    def test_values_containing_braces_are_not_reinterpreted(self):
        t = prompts.Template.parse("a_x", "v1", "<!-- @section one -->\n{{a}} {{b}}")
        self.assertEqual(t.render("one", a="{{b}}", b="x"), "{{b}} x")

    def test_release_files_exist_and_versions_parse(self):
        for name in ("a_brief", "b_analysis", "quality_standard"):
            t = prompts.load(name)
            self.assertEqual(t.version, "v1")
            self.assertTrue(t.sections)

    def test_load_unknown_version_raises(self):
        with self.assertRaises(prompts.PromptError):
            prompts.load("a_brief", "v99")


class QualityStandardTests(unittest.TestCase):
    def test_quality_standard_is_contract_section6_verbatim(self):
        text = prompts.quality_standard().text
        bullets = contract_section6_bullets()
        self.assertEqual(len(bullets), 7)
        for b in bullets:
            self.assertIn(b, text)

    def test_every_pass_prompt_embeds_the_standard(self):
        std = contract_section6_bullets()
        for name, section in (("a_brief", "system.role"), ("b_analysis", "system.role")):
            rendered = prompts.load(name).render(section, quality_standard=prompts.quality_standard().text)
            for b in std:
                self.assertIn(b, rendered, f"{name} lost a CONTRACT §6 bullet")


class BAnalysisPromptContentTests(unittest.TestCase):
    """The craft requirements from the card, pinned so a careless edit cannot silently remove them."""

    def setUp(self):
        self.t = prompts.load("b_analysis")
        self.role = self.t.sections["system.role"]

    def test_contrast_examples_present(self):
        self.assertIn("CONTRAST EXAMPLES", self.role)
        self.assertGreaterEqual(len(re.findall(r"Generic:", self.role)), 3)
        self.assertGreaterEqual(len(re.findall(r"Specific:", self.role)), 3)

    def test_unconventional_decks_and_no_edhrec_standard(self):
        self.assertIn("deliberately unconventional", self.role)
        self.assertIn("NOT EDHREC", self.role)

    def test_calibration_and_no_problems_escape_hatch(self):
        self.assertIn("CALIBRATED", self.role)
        self.assertIn("no_significant_problems", self.role)
        self.assertIn("NO SIGNIFICANT PROBLEMS", self.role)

    def test_open_questions_stay_open(self):
        self.assertIn("OPEN QUESTIONS STAY OPEN", self.role)
        self.assertIn("depends_on_questions", self.role)

    def test_one_section_per_axis(self):
        for ax in config.AXES:
            self.assertIn(f"system.axis.{ax.id}", self.t.sections, ax.id)
            self.assertIn("{{axis_facts}}", self.t.sections[f"system.axis.{ax.id}"])

    def test_exactly_the_seven_card_axes(self):
        self.assertEqual([a.id for a in config.AXES], ["plan_synergy", "mana_consistency", "interaction_resilience",
                                                       "win_paths_closing", "power_bracket", "budget_inventory",
                                                       "concept_execution"])


class ABriefPromptContentTests(unittest.TestCase):
    def test_never_guess_and_questions(self):
        role = prompts.load("a_brief").sections["system.role"]
        self.assertIn("NEVER GUESS INTENT", role)
        self.assertIn("questions_for_owner", role)
        self.assertIn("deliberately unconventional", role)
        self.assertIn("intentional_quirks", role)
        self.assertIn("Generic and guessing", role)


class SchemaTests(unittest.TestCase):
    def test_schemas_are_valid_json_schema(self):
        from jsonschema import Draft202012Validator
        for s in (schemas.BRIEF_SCHEMA, schemas.ANALYSIS_SCHEMA):
            Draft202012Validator.check_schema(s)

    def test_finding_shape_matches_document_schema_fields(self):
        import json
        doc = json.loads((ROOT / "schemas" / "review_document.schema.json").read_text())
        doc_fields = set(doc["$defs"]["finding"]["required"])
        produced = set(schemas.FINDING_FIELDS_IN_DOC)
        self.assertEqual(doc_fields, produced)

    def test_analysis_schema_rejects_unknown_fields_and_bad_values(self):
        from src.server.domain.analytics.review_pipeline.structured import validate_schema
        ok = {"axis_score": {"score": 7, "one_line": "x"}, "no_significant_problems": False,
              "strengths": [], "problems": [{"claim": "c", "evidence": {"cards": ["A"], "fact_keys": []}, "severity": "med",
                                             "confidence": 0.6, "depends_on_questions": [], "fix_direction": "f"}]}
        self.assertEqual(validate_schema(ok, schemas.ANALYSIS_SCHEMA), [])
        bad = dict(ok, axis_score={"score": 11, "one_line": "x"})
        self.assertTrue(validate_schema(bad, schemas.ANALYSIS_SCHEMA))
        bad2 = dict(ok, extra=1)
        self.assertTrue(validate_schema(bad2, schemas.ANALYSIS_SCHEMA))
        bad3 = dict(ok, problems=[dict(ok["problems"][0], severity="huge")])
        self.assertTrue(validate_schema(bad3, schemas.ANALYSIS_SCHEMA))


class ConfigTests(unittest.TestCase):
    def test_models_come_from_env_not_pass_logic(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "x:a", "REVIEW_MODEL_B": "x:b"})
        self.assertEqual(cfg.model("A"), "x:a")
        self.assertEqual(cfg.model("B"), "x:b")

    def test_per_axis_override(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "x:a", "REVIEW_MODEL_B": "x:b", "REVIEW_MODEL_B_MANA_CONSISTENCY": "x:m"})
        self.assertEqual(cfg.model("B", "mana_consistency"), "x:m")
        self.assertEqual(cfg.model("B", "plan_synergy"), "x:b")

    def test_defaults_follow_decisions_d3(self):
        cfg = config.PipelineConfig.from_env({})
        self.assertTrue(cfg.model("A").startswith("anthropic:"))
        self.assertTrue(cfg.model("B").startswith("anthropic:"))

    def test_pass_logic_has_no_hardcoded_model_names(self):
        base = ROOT / "src" / "server" / "domain" / "analytics" / "review_pipeline"
        pat = re.compile(r"claude|gemini|gpt-|opus|sonnet|haiku", re.I)
        for f in ("a_brief.py", "b_analysis.py", "runner.py", "structured.py", "trace.py", "facts.py", "prompts.py", "llm.py"):
            src = (base / f).read_text()
            self.assertIsNone(pat.search(src), f"{f} mentions a model name")

    def test_bad_numbers_fail_loudly(self):
        with self.assertRaises(config.ConfigError):
            config.PipelineConfig.from_env({"REVIEW_MAX_REPAIRS": "lots"})
        with self.assertRaises(config.ConfigError):
            config.PipelineConfig.from_env({"REVIEW_MAX_REPAIRS": "-1"})


if __name__ == "__main__":
    unittest.main()
