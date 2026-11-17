"""ToolRegistry dispatch order, failure tracking and result capping (spec §2.3/§4)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.oracle import Tool, ToolError, ToolRegistry  # noqa: E402
from src.server.domain.oracle.registry import serialize  # noqa: E402
from tests.oracle_fakes import bad_call, call  # noqa: E402

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
          "additionalProperties": False}


def tool(handler, name="t_one", **kw):
    return Tool(name=name, description="d", parameters=SCHEMA, handler=handler, **kw)


class RegistryTests(unittest.TestCase):
    def test_specs_and_names_in_order(self):
        r = ToolRegistry([tool(lambda a, c: {}, "bb"), tool(lambda a, c: {}, "aa")])
        self.assertEqual([s["function"]["name"] for s in r.specs()], ["bb", "aa"])
        self.assertEqual(r.specs()[0]["type"], "function")
        self.assertEqual(r.names(), frozenset({"aa", "bb"}))

    def test_duplicate_and_bad_name(self):
        with self.assertRaises(ValueError):
            ToolRegistry([tool(lambda a, c: {}), tool(lambda a, c: {})])
        with self.assertRaises(ValueError):
            tool(lambda a, c: {}, name="Bad-Name")

    def test_unknown_tool_lists_valid_names(self):
        o = ToolRegistry([tool(lambda a, c: {})]).dispatch(call("nope", {"n": 1}), None)
        self.assertFalse(o.ok)
        self.assertEqual(o.result["error"]["kind"], "unknown_tool")
        self.assertIn("t_one", o.result["error"]["message"])

    def test_parse_error_and_schema_errors(self):
        r = ToolRegistry([tool(lambda a, c: {})])
        self.assertEqual(r.dispatch(bad_call("t_one"), None).result["error"]["kind"], "bad_arguments")
        o = r.dispatch(call("t_one", {"n": "x"}), None)
        self.assertEqual(o.result["error"]["kind"], "bad_arguments")
        o = r.dispatch(call("t_one", {"n": 1, "extra": 2}), None)
        self.assertEqual(o.result["error"]["kind"], "bad_arguments")

    def test_success_envelope(self):
        o = ToolRegistry([tool(lambda a, c: {"v": a["n"] * 2})]).dispatch(call("t_one", {"n": 4}), None)
        self.assertTrue(o.ok)
        self.assertEqual(o.result, {"ok": True, "data": {"v": 8}})
        self.assertFalse(o.truncated)

    def test_tool_error_kind_and_message_shown(self):
        def h(a, c):
            raise ToolError("not_allowed", "nope")
        o = ToolRegistry([tool(h)]).dispatch(call("t_one", {"n": 1}), None)
        self.assertEqual(o.result["error"], {"kind": "not_allowed", "message": "nope"})

    def test_handler_exception_leaks_only_type_name(self):
        def h(a, c):
            raise RuntimeError("https://x?key=SECRET")
        with self.assertLogs("src.server.domain.oracle.registry", level="ERROR"):
            o = ToolRegistry([tool(h)]).dispatch(call("t_one", {"n": 1}), None)
        self.assertEqual(o.result["error"], {"kind": "tool_failed", "message": "RuntimeError"})
        self.assertNotIn("SECRET", json.dumps(o.result))

    def test_three_failures_disable_and_success_resets(self):
        state = {"fail": True}

        def h(a, c):
            if state["fail"]:
                raise ToolError("boom", "x")
            return {}
        r = ToolRegistry([tool(h)])
        kinds = [r.dispatch(call("t_one", {"n": 1}), None).result.get("error", {}).get("kind") for _ in range(4)]
        self.assertEqual(kinds, ["boom", "boom", "boom", "disabled"])
        r2 = ToolRegistry([tool(h)])
        r2.dispatch(call("t_one", {"n": 1}), None)
        r2.dispatch(call("t_one", {"n": 1}), None)
        state["fail"] = False
        self.assertTrue(r2.dispatch(call("t_one", {"n": 1}), None).ok)
        state["fail"] = True
        for _ in range(2):
            self.assertEqual(r2.dispatch(call("t_one", {"n": 1}), None).result["error"]["kind"], "boom")
        self.assertEqual(r2.dispatch(call("t_one", {"n": 1}), None).result["error"]["kind"], "boom")
        self.assertEqual(r2.dispatch(call("t_one", {"n": 1}), None).result["error"]["kind"], "disabled")

    def test_max_calls(self):
        r = ToolRegistry([tool(lambda a, c: {}, max_calls=2)])
        res = [r.dispatch(call("t_one", {"n": 1}), None) for _ in range(3)]
        self.assertEqual([o.ok for o in res], [True, True, False])
        self.assertEqual(res[2].result["error"]["kind"], "call_limit")

    def test_result_over_cap_is_truncated_and_under_cap(self):
        rows = [{"name": "card %d" % i, "text": "x" * 40} for i in range(500)]
        r = ToolRegistry([tool(lambda a, c: {"rows": rows, "total": 500}, max_result_chars=2000)])
        o = r.dispatch(call("t_one", {"n": 1}), None)
        self.assertTrue(o.ok and o.truncated)
        self.assertTrue(o.result["truncated"])
        self.assertLessEqual(len(serialize(o.result)), 2000)
        self.assertEqual(o.result["data"]["total"], 500)
        self.assertGreater(len(o.result["data"]["rows"]), 0)
        self.assertEqual(o.result["data"]["rows"][0], rows[0])  # cut from the end

    def test_unshrinkable_result_never_exceeds_cap(self):
        r = ToolRegistry([tool(lambda a, c: {"blob": "y" * 5000}, max_result_chars=300)])
        o = r.dispatch(call("t_one", {"n": 1}), None)
        self.assertTrue(o.truncated)
        self.assertLessEqual(len(serialize(o.result)), 300)

    def test_tiny_cap_never_raises_or_exceeds(self):
        for cap in (10, 11, 30, 45):
            r = ToolRegistry([tool(lambda a, c: {"blob": "y" * 500}, max_result_chars=cap)])
            o = r.dispatch(call("t_one", {"n": 1}), None)
            self.assertTrue(o.truncated, cap)
            self.assertLessEqual(len(serialize(o.result)), cap, cap)

    def test_custom_shrink_used(self):
        r = ToolRegistry([tool(lambda a, c: {"big": "z" * 5000}, max_result_chars=500,
                               shrink=lambda d: {"big": d["big"][:10]})])
        o = r.dispatch(call("t_one", {"n": 1}), None)
        self.assertEqual(o.result["data"], {"big": "z" * 10})
        self.assertTrue(o.truncated)

    def test_tiny_cap_returns_outcome_within_cap(self):
        r = ToolRegistry([tool(lambda a, c: {"x": "y" * 500}, max_result_chars=10)])
        out = r.dispatch(call("t_one", {"n": 1}), None)
        self.assertFalse(out.ok)
        self.assertTrue(out.truncated)
        self.assertEqual(out.error_kind, "result_too_large")
        self.assertLessEqual(len(serialize(out.result)), 10)


if __name__ == "__main__":
    unittest.main()
