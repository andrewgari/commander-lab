"""DT-4: TCGplayer cart panel on the Tune page (templates/deck_tune.html).

The change-list dock carries a "TCGplayer cart" panel listing every swap the
user accepted as "buy", each with a checkbox. Unchecking a card drops it from
the cart; the button (the shared TcgCart component) opens a Mass Entry URL
with exactly the checked cards, pinning the printing when a set code is
carried (`1 Lightning Bolt [SLD]`).

Structure/wiring assertions only (Python-only suite): the cart behaviour
itself is covered by tests/test_tcgplayer.py (URL lines) and
tests/test_tcgplayer_cart_button.py (the button component).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "src/web/templates" / "deck_tune.html"


class TestCartPanelMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = TEMPLATE.read_text()

    def test_panel_lives_in_the_change_list_dock_hidden_until_needed(self):
        dock = self.s.split('data-hud-dock="tune"', 1)[1]
        self.assertIn('<section class="hud-panel" style="margin-top:var(--sp-4)" id="cartPanel" hidden>', dock)

    def test_panel_structure(self):
        self.assertIn('<h3 class="hud-panel-title">TCGplayer cart</h3><span class="hud-panel-meta" id="cartMeta"></span>', self.s)
        self.assertIn('<ul class="tn-cart" id="cartItems"></ul>', self.s)
        self.assertIn('<span id="cartBtn"></span>', self.s)
        self.assertIn("exactly the checked cards", self.s)


class TestCartPanelWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = TEMPLATE.read_text()

    def test_cart_is_rebuilt_with_the_change_list(self):
        body = self.s.split("function renderChanges()", 1)[1]
        self.assertIn("renderCart();", body.split("\n    }", 1)[0])

    def test_only_accepted_buys_enter_the_cart(self):
        body = self.s.split("function cartRaw()", 1)[1]
        self.assertIn('if (s.decision !== "buy") return;', body)
        # The accepted card stays the truth even after a recompute reranks.
        self.assertIn("s.decision_add", body)

    def test_checkboxes_default_checked_and_gate_the_cart(self):
        self.assertIn('data-cart-i="', self.s)
        self.assertIn('cartSkip[cartKey(it)] = true;', self.s)      # uncheck records the skip
        self.assertIn('delete cartSkip[cartKey(it)];', self.s)       # re-check clears it
        m = re.search(r"function mountCartBtn\(list\) \{(.*?)\n    \}", self.s, re.S)
        self.assertIsNotNone(m)
        self.assertIn("list.filter(function (it) { return !cartSkip[cartKey(it)]; })", m.group(1))
        self.assertIn('TcgCart.mount($("cartBtn")', m.group(1))

    def test_set_code_is_carried_into_the_cart(self):
        m = re.search(r"function cartRaw\(\) \{(.*?)\n    \}", self.s, re.S)
        self.assertIn("if (o.set) it.set = o.set;", m.group(1))
        self.assertIn("if (it.set) c.set = it.set;", self.s)

    def test_panel_stays_hidden_with_nothing_to_buy(self):
        m = re.search(r"function renderCart\(\) \{(.*?)\n    \}", self.s, re.S)
        self.assertIn("panel.hidden = !list.length;", m.group(1))


if __name__ == "__main__":
    unittest.main()
