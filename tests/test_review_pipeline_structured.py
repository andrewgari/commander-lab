"""Review pipeline plumbing: structured output enforcement, bounded repair, loud failure.

Offline: a scripted fake client stands in for the model (no live LLM calls in tests).
"""
from __future__ import annotations

import unittest

from src.server.domain.analytics.review_pipeline import llm, structured

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "n"],
    "properties": {"answer": {"type": "string", "minLength": 1}, "n": {"type": "integer", "minimum": 1, "maximum": 10}},
}


class ScriptedClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return llm.LLMResponse(text=r, usage=llm.Usage(tokens_in=10, tokens_out=5), model="fake", latency_ms=3)


def req():
    return llm.LLMRequest(model="fake:m", system_blocks=("sys",), messages=(llm.Message("user", "q"),),
                          schema_name="t", schema=SCHEMA)


def validator(obj):
    return structured.validate_schema(obj, SCHEMA)


class StructuredTests(unittest.TestCase):
    def test_valid_first_try(self):
        c = ScriptedClient('{"answer": "five", "n": 5}')
        obj, attempts = structured.generate_structured(c, req(), validate=validator)
        self.assertEqual(obj, {"answer": "five", "n": 5})
        self.assertEqual([a.verdict for a in attempts], ["ok"])

    def test_repair_after_invalid_json_then_ok(self):
        c = ScriptedClient("not json at all", '{"answer": "five", "n": 5}')
        obj, attempts = structured.generate_structured(c, req(), validate=validator)
        self.assertEqual(obj["n"], 5)
        self.assertEqual([a.verdict for a in attempts], ["retry", "ok"])
        # the repair request carries the bad reply and the validation error back to the model
        second = c.requests[1]
        self.assertEqual([m.role for m in second.messages], ["user", "assistant", "user"])
        self.assertIn("not json at all", second.messages[1].content)
        self.assertIn("not valid JSON", second.messages[2].content)

    def test_repair_after_schema_violation(self):
        c = ScriptedClient('{"answer": "five", "n": 99}', '{"answer": "five", "n": 5}')
        obj, attempts = structured.generate_structured(c, req(), validate=validator)
        self.assertEqual(obj["n"], 5)
        self.assertIn("n", attempts[0].errors[0])

    def test_fails_loudly_after_bounded_repairs(self):
        c = ScriptedClient("bad", "still bad", "worse", '{"answer": "five", "n": 5}')
        with self.assertRaises(structured.StructuredOutputError) as cm:
            structured.generate_structured(c, req(), validate=validator, max_repairs=2)
        self.assertEqual(len(c.requests), 3)               # 1 call + 2 repairs, never a 4th
        self.assertEqual([a.verdict for a in cm.exception.attempts], ["retry", "retry", "failed"])
        self.assertIn("worse", cm.exception.attempts[-1].response.text)

    def test_zero_repairs_means_single_attempt(self):
        c = ScriptedClient("bad", '{"answer": "five", "n": 5}')
        with self.assertRaises(structured.StructuredOutputError):
            structured.generate_structured(c, req(), validate=validator, max_repairs=0)
        self.assertEqual(len(c.requests), 1)

    def test_semantic_validation_errors_trigger_repair(self):
        c = ScriptedClient('{"answer": "x", "n": 3}', '{"answer": "yes", "n": 3}')
        sem = lambda o: [] if o["answer"] == "yes" else ["answer must be 'yes'"]
        obj, attempts = structured.generate_structured(c, req(), validate=lambda o: validator(o) + sem(o))
        self.assertEqual(obj["answer"], "yes")
        self.assertEqual(attempts[0].errors, ["answer must be 'yes'"])

    def test_code_fence_wrapped_json_is_accepted(self):
        c = ScriptedClient('```json\n{"answer": "five", "n": 5}\n```')
        obj, _ = structured.generate_structured(c, req(), validate=validator)
        self.assertEqual(obj["n"], 5)

    def test_transport_error_is_recorded_and_raised_not_repaired(self):
        seen = []
        c = ScriptedClient(llm.LLMTransportError("HTTP 503"))
        with self.assertRaises(llm.LLMTransportError):
            structured.generate_structured(c, req(), validate=validator, on_attempt=seen.append)
        self.assertEqual(len(c.requests), 1)
        self.assertEqual(seen[0].verdict, "failed")
        self.assertIn("503", seen[0].error)

    def test_on_attempt_sees_every_attempt_including_final_failure(self):
        seen = []
        c = ScriptedClient("bad", "bad")
        with self.assertRaises(structured.StructuredOutputError):
            structured.generate_structured(c, req(), validate=validator, max_repairs=1, on_attempt=seen.append)
        self.assertEqual([a.verdict for a in seen], ["retry", "failed"])

    def test_empty_reply_uses_placeholder_assistant_turn(self):
        c = ScriptedClient("", '{"answer": "five", "n": 5}')
        structured.generate_structured(c, req(), validate=validator)
        self.assertTrue(c.requests[1].messages[1].content.strip())


class SchemaTests(unittest.TestCase):
    def test_portable_schema_strips_unsupported_keywords_but_keeps_shape(self):
        p = structured.portable_schema(SCHEMA)
        self.assertNotIn("minLength", p["properties"]["answer"])
        self.assertNotIn("minimum", p["properties"]["n"])
        self.assertEqual(p["required"], ["answer", "n"])
        self.assertIs(p["additionalProperties"], False)
        # constraint survives as human-readable guidance for the model
        self.assertIn("1", p["properties"]["n"].get("description", ""))
        # original untouched
        self.assertIn("minimum", SCHEMA["properties"]["n"])

    def test_input_hash_is_stable_and_sensitive(self):
        a, b = req(), req()
        self.assertEqual(a.input_hash, b.input_hash)
        c = llm.LLMRequest(model="fake:m", system_blocks=("sys2",), messages=a.messages, schema_name="t", schema=SCHEMA)
        self.assertNotEqual(a.input_hash, c.input_hash)
        d = llm.LLMRequest(model="fake:other", system_blocks=a.system_blocks, messages=a.messages, schema_name="t", schema=SCHEMA)
        self.assertNotEqual(a.input_hash, d.input_hash)


if __name__ == "__main__":
    unittest.main()
