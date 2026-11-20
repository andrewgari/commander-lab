"""Run the shared autocomplete's dependency-free browser behavior tests under Node."""
import shutil
import subprocess
import unittest
from pathlib import Path


NODE = shutil.which("node")
TEST = Path(__file__).parent / "js" / "card_autocomplete.test.js"


@unittest.skipUnless(NODE, "node is not installed")
class TestCardAutocompleteJavaScript(unittest.TestCase):
    def test_behavior(self):
        result = subprocess.run(
            [NODE, "--test", str(TEST)], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
