"""Executes the /decks page script under Node: syntax check + jsdom behavioural flow.

Skipped when Node (or, for the behavioural test, jsdom) is unavailable. To run
the behavioural test locally: `npm i --prefix /tmp/jsd jsdom` and set
JSDOM_PATH=/tmp/jsd/node_modules.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

NODE = shutil.which("node")
FLOW = Path(__file__).parent / "js" / "bulk_decommission_flow.js"


def _node_env():
    env = dict(os.environ)
    if os.environ.get("JSDOM_PATH"):
        env["NODE_PATH"] = os.environ["JSDOM_PATH"] + os.pathsep + env.get("NODE_PATH", "")
    return env


def _has_jsdom():
    if not NODE:
        return False
    r = subprocess.run([NODE, "-e", "require('jsdom')"], env=_node_env(), capture_output=True)
    return r.returncode == 0


@unittest.skipUnless(NODE, "node not installed")
class TestDecksPageScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TestClient(app).get("/decks").text

    def test_page_script_parses(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(scripts[-1])
        try:
            r = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(f.name)

    @unittest.skipUnless(_has_jsdom(), "jsdom not available (set JSDOM_PATH)")
    def test_bulk_decommission_flow(self):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write(self.html)
        try:
            r = subprocess.run([NODE, str(FLOW), f.name], env=_node_env(),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
