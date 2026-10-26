"""Buy-on-TCGplayer button is wired into the buy surfaces."""
import unittest
from pathlib import Path

from src.server.app.main import templates

ROOT = Path(__file__).resolve().parent.parent


def _render_buy_total(cards):
    from src.server.domain.analytics import theory_view
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

    def test_reconstruct_new_to_buy_uses_tier3_cart(self):
        s = (ROOT / "templates/reconstruct.html").read_text()
        self.assertIn("else { t3Count++; t3Items.push({name: c.name, qty: c.qty || 1}); }", s)
        self.assertIn('window.TcgCart.button(t3Items, {size: "sm"})', s)
        self.assertLess(s.index("New to Buy: ${t3Count}"), s.index("TcgCart.button(t3Items"))

    def test_theory_decklist_buy_rows_only(self):
        from src.server.domain.analytics import theory_view
        tpl = templates.env.get_template("partials/theory/decklist.html")
        groups = [{"role": "Ramp", "cards": [
            {"name": "Sol Ring", "qty": 1, "source": "buy", "price": 1.0},
            {"name": "Arcane Signet", "qty": 1, "source": "owned", "price": 1.0},
            {"name": "Forest", "qty": 3, "source": "kept", "price": 0}]}]
        sec = type("S", (), {"data": {"groups": groups, "counts": {}}})()
        out = tpl.render(section=sec, tv=theory_view, card_preview_attrs=lambda n: "")
        self.assertEqual(out.count("hud-btn--buy"), 1)
        self.assertIn("1%20Sol%20Ring", out)

    def test_card_review_skips_owned(self):
        s = (ROOT / "static/js/card_review.js").read_text()
        self.assertIn('it.ownership !== "owned_free" && window.TcgCart', s)
        self.assertIn("primary + tcg", s)


if __name__ == "__main__":
    unittest.main()


class TestIdeasRow(unittest.TestCase):
    DOC = {"doc_id": "d", "sections": [{"type": "buy_total", "data": {"cards": [
        {"name": "Sol Ring", "qty": 1, "price": 1}, {"name": "Esper Sentinel", "qty": 2, "price": 2}]}}]}

    def test_summary_exposes_buy_cards(self):
        from src.server.domain.analytics import theory
        self.assertEqual(theory.summary(self.DOC)["buy_cards"],
                         [{"name": "Sol Ring", "qty": 1}, {"name": "Esper Sentinel", "qty": 2}])
        self.assertEqual(theory.summary({"doc_id": "x"})["buy_cards"], [])

    def test_ideas_template_wires_cart(self):
        s = (ROOT / "templates/deck_ideas.html").read_text()
        self.assertIn('hud.tcg_cart(d.buy_cards or [], size="sm")', s)
