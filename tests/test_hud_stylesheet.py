"""Shared HUD stylesheet: served by FastAPI and linked from every page.

Asserts structure (the asset exists, is served as CSS, every page links it,
the documented token groups are defined) rather than specific values, so the
palette can be tuned without editing tests.
"""
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app

STYLESHEET_PATH = "/static/css/hud.css"

# Every HTML page that extends base.html.
PAGES = [
    "/",
    "/decks",
    "/inventory",
    "/tags",
    "/instances",
    "/changelog",
    "/deck/TestDeck",
    "/styleguide",
]

# One representative token per documented group (docs/DESIGN_TOKENS.md).
REQUIRED_TOKENS = [
    # colour: surfaces, lines, text, signal, data
    "--surface-0", "--surface-1", "--line", "--text-1", "--text-3",
    "--signal", "--data", "--data-5",
    # mana palette
    "--mana-w", "--mana-u", "--mana-b", "--mana-r", "--mana-g", "--mana-c", "--mana-m",
    # type
    "--font-sans", "--font-mono", "--fs-micro", "--fs-base", "--fs-stat",
    # spacing
    "--sp-1", "--sp-4", "--sp-8",
    # borders
    "--hairline", "--stroke", "--radius-1",
    # legacy aliases still used by page templates
    "--bg-color", "--nav-bg", "--text-main", "--text-muted", "--text-heading",
    "--border", "--accent", "--accent-hover", "--row-hover",
]


def _defined_tokens(css):
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", css))


class TestHudStylesheet(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_stylesheet_is_served_as_css(self):
        response = self.client.get(STYLESHEET_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["content-type"])

    def test_stylesheet_defines_documented_tokens(self):
        css = self.client.get(STYLESHEET_PATH).text
        missing = [t for t in REQUIRED_TOKENS if t not in _defined_tokens(css)]
        self.assertEqual(missing, [], f"tokens missing from hud.css: {missing}")

    def test_every_var_reference_in_stylesheet_is_defined(self):
        css = self.client.get(STYLESHEET_PATH).text
        defined = _defined_tokens(css)
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        self.assertEqual(sorted(used - defined), [])

    def test_stylesheet_has_no_pure_black_background(self):
        css = self.client.get(STYLESHEET_PATH).text.lower()
        self.assertNotRegex(css, r"#000(000)?\b")

    @patch("app.registry")
    def test_every_page_links_stylesheet(self, mock_registry):
        mock_registry.list_decks.return_value = [{"name": "TestDeck", "id": "archidekt:1"}]
        mock_registry.registry_id_of.return_value = "archidekt:1"
        for path in PAGES + ["/deck/TestDeck/manage"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertRegex(
                    response.text,
                    r'<link rel="stylesheet" href="' + re.escape(STYLESHEET_PATH) + r'(\?[^"]*)?"',
                )

    def test_page_styles_are_not_nested_inside_base_style(self):
        # Page templates pass a full <style> element via {% block extra_css %};
        # base.html must not wrap it in another <style> (which made the first
        # page rule unparseable).
        html = self.client.get("/changelog").text
        head = html.split("</head>", 1)[0]
        self.assertNotRegex(head, r"(?s)<style>(?:(?!</style>).)*<style>", "nested <style> in <head>")


if __name__ == "__main__":
    unittest.main()
