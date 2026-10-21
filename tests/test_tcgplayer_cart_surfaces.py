"""Buy-on-TCGplayer button is wired into the buy surfaces."""
import unittest
from pathlib import Path

from app import templates

ROOT = Path(__file__).resolve().parent.parent


def _render_buy_total(cards):
    from analytics import theory_view
    tpl = templates.env.get_template("partials/theory/buy_total.html")
    sec = type("S", (), {"data": {"total_usd": 5, "count": len(cards), "cards": cards, "caps": {}}})()
    return tpl.render(section=sec, tv=theory_view, card_preview_attrs=lambda n: "")


class TestSurfaces(unittest.TestCase):
    def test_buy_total_has_multi_card_button(self):
        out = _render_buy_total([{"name": "Sol Ring", "qty": 1, "price": 1.0},
                                 {"name": "Esper Sentinel", "qty": 2, "price": 2.0}])
        self.assertIn("hud-btn--buy", out)
        self.assertIn("tcgplayer.com/massentry", out)
        self.assertIn("2%20Esper%20Sentinel", out)

    def test_buy_total_empty_has_no_button(self):
        self.assertNotIn("hud-btn--buy", _render_buy_total([]))

    def test_tune_buy_option_uses_cart(self):
        s = (ROOT / "templates/deck_tune.html").read_text()
        self.assertIn('o.source === "buy" && window.TcgCart', s)
        self.assertIn('size: "sm"', s)

    def test_card_review_skips_owned(self):
        s = (ROOT / "static/js/card_review.js").read_text()
        self.assertIn('it.ownership !== "owned_free" && window.TcgCart', s)
        self.assertIn("primary + tcg", s)


if __name__ == "__main__":
    unittest.main()
