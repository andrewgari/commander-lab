"""PD-UI: pillar description assist button on the Identity page (static/js/pillar_describe.js).

Page wiring is checked against the rendered template; the pure helpers (mode, clamp, error
classification, markup escaping) run in node.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "static" / "js" / "pillar_describe.js"


def node(expr):
    out = subprocess.run(["node", "-e", f"const P=require({json.dumps(str(JS))});console.log(JSON.stringify({expr}))"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestIdentityPageWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl = (ROOT / "templates" / "deck_identity.html").read_text()

    def test_script_loaded_and_mounted(self):
        self.assertRegex(self.tpl, r'<script src="/static/js/pillar_describe\.js\?v=')
        for frag in ["PillarDescribe.fieldHtml(", "PillarDescribe.mount(", "PillarDescribe.unmount()",
                     "PillarDescribe.handleKey(e)", '"/pillars/describe"']:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.tpl)

    def test_key_classes_are_styled(self):
        for cls in ["pd-gen", "pd-sugg", "pd-skel", "pd-undo", "pd-count"]:
            with self.subTest(cls=cls):
                self.assertIn(f".{cls}", self.tpl)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestHelpers(unittest.TestCase):
    def test_mode(self):
        self.assertEqual(node('[P.modeOf(""),P.modeOf("  "),P.modeOf("x")]'), ["draft", "draft", "improve"])

    def test_can_generate_needs_name_or_text(self):
        self.assertEqual(node('[P.canGen("",""),P.canGen("a",""),P.canGen("","b")]'), [False, True, True])

    def test_clamp(self):
        long = "Sentence one. " * 60
        out = node(f"P.clamp({json.dumps(long)})")
        self.assertLessEqual(len(out), 400)
        self.assertTrue(out.endswith("."))

    def test_classify(self):
        self.assertEqual(node("P.classify(422,'draft',false)")["kind"], "hint")
        self.assertEqual(node("P.classify(503,'draft',false)")["kind"], "na")
        self.assertFalse(node("P.classify(503,'draft',false)")["retry"])
        self.assertTrue(node("P.classify(502,'draft',false)")["retry"])
        self.assertIn("too long", node("P.classify(0,'improve',true)")["msg"])

    def test_server_text_is_escaped_in_markup(self):
        html = node("""P.fieldHtml({id:'a"<b>',description:'<script>x</script>'})""")
        self.assertNotIn("<script>", html)
        self.assertIn("data-pd-gen", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
