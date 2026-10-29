"""Follow-up to the changelog truncation fix (t_83b56c62 / t_a947327b).

static/js/categories.js clips long category descriptions to 2 lines (1 in
dense/core mode) via CSS line-clamp in static/css/categories.css for layout
reasons (narrow registry rail). That's acceptable for a nav-list preview as
long as the full text stays reachable -- via a title tooltip here, since the
editor panel is the real place the full description is shown/edited. This
test pins the tooltip so the clipped prose never becomes silently
unreachable again.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class CategoryDescriptionTooltipTest(unittest.TestCase):
    def setUp(self):
        self.js = (REPO_ROOT / "src/web/static" / "js" / "categories.js").read_text(encoding="utf-8")

    def test_item_desc_has_title_tooltip_with_full_description(self):
        match = re.search(
            r'<p class="ct-item-desc"([^>]*)>\'\s*\+\s*esc\(c\.description\)',
            self.js,
        )
        self.assertIsNotNone(match, "ct-item-desc markup not found in categories.js")
        attrs = match.group(1)
        self.assertIn("title=", attrs)
        self.assertIn("esc(c.description)", attrs)


if __name__ == "__main__":
    unittest.main()
