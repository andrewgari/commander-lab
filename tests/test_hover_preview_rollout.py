"""Hover preview rollout: every audited render site (docs/CARD_RENDER_AUDIT.md
section 3) carries the markup CardHoverPreview reads. See docs/scryfall-hover-preview.md."""
import unittest
from pathlib import Path

from jinja2 import Environment

import src.server.domain.decks.card_refs as card_refs

ROOT = Path(__file__).resolve().parent.parent

# audit site -> (file, marker that must be present)
SITES = {
    "6/7 deck_stats": ("templates/deck_stats.html", "card: true"),
    "8 strong_cards": ("templates/partials/review/strong_cards.html", "card_preview_attrs(c.name)"),
    "8 stragglers": ("templates/partials/review/stragglers.html", "card_preview_attrs(c.name)"),
    "9 curve": ("templates/partials/review/curve_tempo.html", "card_preview_attrs(card.name)"),
    "9 combos": ("templates/partials/review/combos_engines.html", "card_preview_attrs(n)"),
    "9 focus": ("templates/partials/review/focus.html", "card_preview_attrs(n)"),
    "9 composition": ("templates/partials/review/composition.html", "card_preview_attrs(n)"),
    "10 theory decklist": ("templates/partials/theory/decklist.html", "card_preview_attrs(card.name)"),
    "10 theory cuts": ("templates/partials/theory/cuts.html", "card_preview_attrs(card.name)"),
    "10 theory buy": ("templates/partials/theory/buy_total.html", "card_preview_attrs(card.name)"),
    "11 deck_tune": ("templates/deck_tune.html", "H.cardName("),
    "12 deck_journal": ("templates/deck_journal.html", "cardRefAttrString"),
    "13 deck_manage": ("templates/deck_manage.html", "data-card-preview"),
    "14 inventory": ("templates/inventory.html", "data-card-preview"),
    "15 instances": ("templates/instances.html", "data-card-preview"),
    "16 tags": ("templates/tags.html", "data-card-preview"),
    "17 decks grid": ("templates/decks.html", "data-card-preview"),
    "19 hud.commanders macro": ("templates/partials/hud.html", "card_ref_attrs(c.name"),
    "20 personality thumb": ("static/js/personality.js", "data-card-preview"),
    "21 hud.commanders JS": ("static/js/hud.js", "cardRefAttrString(cardRef({ name: c.name"),
}


class TestRollout(unittest.TestCase):
    def test_every_site_is_marked(self):
        for site, (path, marker) in SITES.items():
            with self.subTest(site=site):
                self.assertIn(marker, (ROOT / path).read_text())

    def test_hand_rolled_api_image_urls_replaced(self):
        self.assertNotIn("cards/${card.uid}?format=image", (ROOT / "templates/inventory.html").read_text())
        self.assertNotIn("cards/${uid}?format=image", (ROOT / "templates/decks.html").read_text())

    def test_preview_attrs_uses_page_printing_map(self):
        env = Environment(autoescape=True)
        env.globals["card_preview_attrs"] = card_refs.preview_attrs
        tpl = env.from_string("<b {{ card_preview_attrs('Sol Ring') }}>x</b>")
        sid = "4cbc6901-6a4a-4d0a-83ea-7eefa3b35021"
        ref = card_refs.make_ref("Sol Ring", scryfall_id=sid, set_code="c21", collector_number="263")
        with_map = tpl.render(head={"card_refs": {"sol ring": card_refs.compact(ref)}})
        self.assertIn("data-card-preview", with_map)
        self.assertIn(sid, with_map)
        # No head (partial rendered stand-alone): name-only, never an error.
        bare = tpl.render()
        self.assertIn("Sol Ring", bare)
        self.assertNotIn(sid, bare)


if __name__ == "__main__":
    unittest.main()
