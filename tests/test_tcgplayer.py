"""Unit tests for tcgplayer.build_tcgplayer_url."""
import sys
import os
import unittest
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tcgplayer import build_tcgplayer_url, cart_items, BASE_URL  # noqa: E402


def decoded_c(url: str) -> str:
    """Pull out and URL-decode the `c` query param for easy assertions."""
    assert url.startswith(BASE_URL + "?c=")
    tail = url[len(BASE_URL) + 3 :]
    c_param = tail.split("&productline=Magic")[0]
    return unquote(c_param)


class TestBuildTcgplayerUrl(unittest.TestCase):
    def test_single_card(self):
        url = build_tcgplayer_url([{"name": "Sol Ring", "qty": 1}])
        self.assertEqual(decoded_c(url), "1 Sol Ring")
        self.assertIn("productline=Magic", url)

    def test_many_cards_preserve_order(self):
        cards = [
            {"name": "Sol Ring", "qty": 1},
            {"name": "Arcane Signet", "qty": 1},
            {"name": "Command Tower", "qty": 1},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(
            decoded_c(url), "1 Sol Ring||1 Arcane Signet||1 Command Tower"
        )

    def test_quantity_greater_than_one(self):
        url = build_tcgplayer_url([{"name": "Island", "qty": 5}])
        self.assertEqual(decoded_c(url), "5 Island")

    def test_duplicate_names_are_merged(self):
        cards = [
            {"name": "Island", "qty": 2},
            {"name": "Mountain", "qty": 1},
            {"name": "Island", "qty": 3},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(decoded_c(url), "5 Island||1 Mountain")

    def test_special_characters_are_encoded(self):
        url = build_tcgplayer_url([{"name": "Lim-Dûl's Vault", "qty": 1}])
        self.assertEqual(decoded_c(url), "1 Lim-Dûl's Vault")
        # Space and apostrophe must not appear raw in the actual URL.
        self.assertNotIn(" ", url.split("?c=")[1].split("&")[0])

    def test_double_faced_card_uses_front_face(self):
        url = build_tcgplayer_url(
            [{"name": "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", "qty": 2}]
        )
        self.assertEqual(decoded_c(url), "2 Fable of the Mirror-Breaker")

    def test_double_faced_duplicate_merges_with_front_face_name(self):
        cards = [
            {"name": "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki", "qty": 1},
            {"name": "Fable of the Mirror-Breaker", "qty": 1},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(decoded_c(url), "2 Fable of the Mirror-Breaker")

    def test_empty_list_returns_bare_base_url(self):
        url = build_tcgplayer_url([])
        self.assertEqual(url, BASE_URL)

    def test_zero_or_negative_qty_dropped(self):
        cards = [
            {"name": "Sol Ring", "qty": 0},
            {"name": "Arcane Signet", "qty": -1},
            {"name": "Command Tower", "qty": 1},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(decoded_c(url), "1 Command Tower")

    def test_all_zero_qty_returns_bare_base_url(self):
        url = build_tcgplayer_url([{"name": "Sol Ring", "qty": 0}])
        self.assertEqual(url, BASE_URL)

    def test_missing_qty_defaults_to_one(self):
        url = build_tcgplayer_url([{"name": "Sol Ring"}])
        self.assertEqual(decoded_c(url), "1 Sol Ring")

    def test_blank_name_skipped(self):
        cards = [{"name": "", "qty": 1}, {"name": "Sol Ring", "qty": 1}]
        url = build_tcgplayer_url(cards)
        self.assertEqual(decoded_c(url), "1 Sol Ring")


class TestSetPinnedPrintings(unittest.TestCase):
    """A carried set code pins the printing: `1 Lightning Bolt [SLD]`."""

    def test_set_pins_the_printing(self):
        url = build_tcgplayer_url([{"name": "Lightning Bolt", "qty": 1, "set": "sld"}])
        self.assertEqual(decoded_c(url), "1 Lightning Bolt [SLD]")

    def test_set_code_is_normalized(self):
        url = build_tcgplayer_url([{"name": "Lightning Bolt", "qty": 1, "set": " sld "}])
        self.assertEqual(decoded_c(url), "1 Lightning Bolt [SLD]")

    def test_same_name_same_set_merges(self):
        cards = [
            {"name": "Lightning Bolt", "qty": 1, "set": "sld"},
            {"name": "Lightning Bolt", "qty": 2, "set": "SLD"},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(decoded_c(url), "3 Lightning Bolt [SLD]")

    def test_same_name_different_sets_stay_separate(self):
        cards = [
            {"name": "Lightning Bolt", "qty": 1, "set": "sld"},
            {"name": "Lightning Bolt", "qty": 1, "set": "2x2"},
            {"name": "Lightning Bolt", "qty": 1},
        ]
        url = build_tcgplayer_url(cards)
        self.assertEqual(
            decoded_c(url),
            "1 Lightning Bolt [SLD]||1 Lightning Bolt [2X2]||1 Lightning Bolt",
        )

    def test_blank_set_falls_back_to_any_printing(self):
        for raw in ("", "   ", None):
            with self.subTest(set=raw):
                url = build_tcgplayer_url([{"name": "Sol Ring", "qty": 1, "set": raw}])
                self.assertEqual(decoded_c(url), "1 Sol Ring")

    def test_cart_items_carry_set_only_when_present(self):
        items = cart_items([{"name": "Lightning Bolt", "qty": 1, "set": "sld"},
                            {"name": "Sol Ring", "qty": 1}])
        self.assertEqual(items[0], {"name": "Lightning Bolt", "qty": 1, "set": "SLD"})
        self.assertEqual(items[1], {"name": "Sol Ring", "qty": 1})


if __name__ == "__main__":
    unittest.main()
