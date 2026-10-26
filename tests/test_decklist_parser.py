"""Unit tests for multi-format Commander decklist parser (decklist_parser.py).

Verifies MTG Arena, MTGO, and plain-text formats, commander identification,
sideboard separation, split card handling, set code/collector annotations,
and malformed or edge case handling.

Run:
    python -m unittest tests.test_decklist_parser
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.intake.decklist_parser import (
    DeckSection,
    ParsedCard,
    ParsedDeck,
    parse_deck,
    parse_decklist,
)


class TestDecklistParserArena(unittest.TestCase):
    def test_arena_format_full(self):
        text = """
Commander
1 Atraxa, Praetors' Voice (CM2) 1

Deck
1 Sol Ring (NEO) 123
4 Island (BRO) 271
1 Boseiju, Who Endures (NEO) 266

Sideboard
1 Heroic Intervention (M21) 164
1 Swords to Plowshares (STA) 10
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 6)

        # Commander section
        self.assertEqual(len(deck.commander), 1)
        cmdr = deck.commander[0]
        self.assertEqual(cmdr["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(cmdr["quantity"], 1)
        self.assertEqual(cmdr["section"], "commander")
        self.assertEqual(cmdr["set_code"], "CM2")
        self.assertEqual(cmdr["collector_number"], "1")

        # Mainboard section
        self.assertEqual(len(deck.mainboard), 3)
        self.assertEqual(deck.mainboard[0]["name"], "Sol Ring")
        self.assertEqual(deck.mainboard[0]["quantity"], 1)
        self.assertEqual(deck.mainboard[0]["set_code"], "NEO")
        self.assertEqual(deck.mainboard[0]["collector_number"], "123")
        self.assertEqual(deck.mainboard[0]["section"], "mainboard")

        self.assertEqual(deck.mainboard[1]["name"], "Island")
        self.assertEqual(deck.mainboard[1]["quantity"], 4)
        self.assertEqual(deck.mainboard[1]["set_code"], "BRO")
        self.assertEqual(deck.mainboard[1]["collector_number"], "271")

        self.assertEqual(deck.mainboard[2]["name"], "Boseiju, Who Endures")
        self.assertEqual(deck.mainboard[2]["quantity"], 1)

        # Sideboard section: remapped to Maybeboard (removed concept), flagged
        self.assertEqual(len(deck.maybeboard), 2)
        self.assertEqual(deck.maybeboard[0]["name"], "Heroic Intervention")
        self.assertEqual(deck.maybeboard[0]["section"], "maybeboard")
        self.assertEqual(deck.maybeboard[0]["remapped_from"], "sideboard")
        self.assertEqual(deck.maybeboard[0]["set_code"], "M21")
        self.assertEqual(deck.maybeboard[0]["collector_number"], "164")

        self.assertEqual(deck.maybeboard[1]["name"], "Swords to Plowshares")
        self.assertEqual(deck.maybeboard[1]["section"], "maybeboard")
        self.assertEqual(deck.maybeboard[1]["set_code"], "STA")
        self.assertEqual(deck.maybeboard[1]["collector_number"], "10")

        self.assertIn("sideboard_mapped_to_maybeboard", deck.parse_flags)
        # Deprecated alias still resolves to the maybeboard
        self.assertEqual(deck.sideboard, deck.maybeboard)

    def test_arena_with_foil_and_collector_variations(self):
        text = """
Deck
1 Sol Ring (NEO) 123 *F*
1 Mana Crypt (MPS) 17 [Foil]
1 Teferi, Hero of Dominaria (DAR) 207a
1 Island (JMP) 046
1 Mountain (SLD) #1234
1 Plains (M21) 380★
1 Forest (NEO)
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 7)
        self.assertEqual(deck[0]["name"], "Sol Ring")
        self.assertEqual(deck[0]["set_code"], "NEO")
        self.assertEqual(deck[0]["collector_number"], "123")
        self.assertTrue(deck[0]["foil"])

        self.assertEqual(deck[1]["name"], "Mana Crypt")
        self.assertEqual(deck[1]["set_code"], "MPS")
        self.assertEqual(deck[1]["collector_number"], "17")
        self.assertTrue(deck[1]["foil"])

        self.assertEqual(deck[2]["name"], "Teferi, Hero of Dominaria")
        self.assertEqual(deck[2]["collector_number"], "207a")

        self.assertEqual(deck[3]["name"], "Island")
        self.assertEqual(deck[3]["collector_number"], "046")

        self.assertEqual(deck[4]["name"], "Mountain")
        self.assertEqual(deck[4]["collector_number"], "1234")

        self.assertEqual(deck[5]["name"], "Plains")
        self.assertEqual(deck[5]["collector_number"], "380★")

        self.assertEqual(deck[6]["name"], "Forest")
        self.assertEqual(deck[6]["set_code"], "NEO")
        self.assertIsNone(deck[6]["collector_number"])

    def test_trailing_f_foil_marker(self):
        text = "1 Sol Ring (NEO) 123 F"
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 1)
        self.assertEqual(deck[0]["name"], "Sol Ring")
        self.assertEqual(deck[0]["set_code"], "NEO")
        self.assertEqual(deck[0]["collector_number"], "123")
        self.assertTrue(deck[0]["foil"])

    def test_category_suffix_combined_with_set_annotation(self):
        text = "1 Sol Ring (NEO) 123 [Ramp]"
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 1)
        self.assertEqual(deck[0]["name"], "Sol Ring")
        self.assertEqual(deck[0]["set_code"], "NEO")
        self.assertEqual(deck[0]["collector_number"], "123")


class TestDecklistParserMTGO(unittest.TestCase):
    def test_mtgo_format_with_slash_headers(self):
        text = """
// Commander
1 Atraxa, Praetors' Voice

// Main
1 Sol Ring
4 Forest
1 Arcane Signet

// Sideboard
1 Swords to Plowshares
1 Disenchant
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 6)
        self.assertEqual(len(deck.commander), 1)
        self.assertEqual(deck.commander[0]["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(deck.commander[0]["section"], "commander")

        self.assertEqual(len(deck.mainboard), 3)
        self.assertEqual([c["name"] for c in deck.mainboard], ["Sol Ring", "Forest", "Arcane Signet"])
        self.assertEqual(deck.mainboard[1]["quantity"], 4)

        self.assertEqual(len(deck.sideboard), 2)
        self.assertEqual([c["name"] for c in deck.sideboard], ["Swords to Plowshares", "Disenchant"])

    def test_mtgo_format_with_count_headers_and_sb_prefix(self):
        text = """
// 1 Commander
1 Thrasios, Triton Hero
1 Tymna the Weaver

// 99 Maindeck
1 Sol Ring
1 Demonic Tutor

// 15 Sideboard
SB: 1 Red Elemental Blast
SB: 1 Pyroblast
1 Veil of Summer
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 2)
        self.assertEqual([c["name"] for c in deck.commander], ["Thrasios, Triton Hero", "Tymna the Weaver"])

        self.assertEqual(len(deck.mainboard), 2)
        self.assertEqual([c["name"] for c in deck.mainboard], ["Sol Ring", "Demonic Tutor"])

        self.assertEqual(len(deck.maybeboard), 3)
        self.assertEqual([c["name"] for c in deck.maybeboard], ["Red Elemental Blast", "Pyroblast", "Veil of Summer"])
        for c in deck.maybeboard:
            self.assertEqual(c["section"], "maybeboard")
            self.assertEqual(c["remapped_from"], "sideboard")
        self.assertIn("sideboard_mapped_to_maybeboard", deck.parse_flags)

    def test_mtgo_sb_prefix_in_unsectioned_list(self):
        text = """
1 Sol Ring
SB: 1 Disenchant
1 Demonic Tutor
SB: 2 Flusterstorm
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.mainboard), 2)
        self.assertEqual(len(deck.sideboard), 2)
        self.assertEqual([c["name"] for c in deck.mainboard], ["Sol Ring", "Demonic Tutor"])
        self.assertEqual(deck.sideboard[0]["name"], "Disenchant")
        self.assertEqual(deck.sideboard[0]["quantity"], 1)
        self.assertEqual(deck.sideboard[1]["name"], "Flusterstorm")
        self.assertEqual(deck.sideboard[1]["quantity"], 2)


class TestDecklistParserPlainText(unittest.TestCase):
    def test_simple_plain_text(self):
        text = """
1 Sol Ring
1 Arcane Signet
10 Island
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 3)
        for card in deck:
            self.assertEqual(card["section"], "mainboard")
        self.assertEqual(deck[2]["name"], "Island")
        self.assertEqual(deck[2]["quantity"], 10)

    def test_bare_card_names_no_quantity(self):
        text = """
Sol Ring
Arcane Signet
Command Tower
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 3)
        for card in deck:
            self.assertEqual(card["quantity"], 1)
            self.assertEqual(card["section"], "mainboard")
        self.assertEqual([c["name"] for c in deck], ["Sol Ring", "Arcane Signet", "Command Tower"])

    def test_quantity_variants(self):
        text = """
1x Sol Ring
2X Arcane Signet
3. Command Tower
4 - Forest
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 4)
        self.assertEqual(deck[0]["quantity"], 1)
        self.assertEqual(deck[0]["name"], "Sol Ring")
        self.assertEqual(deck[1]["quantity"], 2)
        self.assertEqual(deck[1]["name"], "Arcane Signet")
        self.assertEqual(deck[2]["quantity"], 3)
        self.assertEqual(deck[2]["name"], "Command Tower")
        self.assertEqual(deck[3]["quantity"], 4)
        self.assertEqual(deck[3]["name"], "Forest")

    def test_bracketed_categories_stripped(self):
        text = """
1 Sol Ring [Ramp]
1 Rhystic Study [Draw]
1 Toxic Deluge [Board Wipe]
"""
        deck = parse_decklist(text)
        self.assertEqual([c["name"] for c in deck], ["Sol Ring", "Rhystic Study", "Toxic Deluge"])
        self.assertEqual([c["quantity"] for c in deck], [1, 1, 1])


class TestCommanderIdentification(unittest.TestCase):
    def test_commander_header_with_colon(self):
        text = """
Commander:
1 Atraxa, Praetors' Voice

Mainboard:
1 Sol Ring
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 1)
        self.assertEqual(deck.commander[0]["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(deck.mainboard[0]["name"], "Sol Ring")

    def test_blank_line_does_not_reset_section(self):
        # A blank line between partner commanders (no new header in between)
        # must not misclassify the second commander as mainboard.
        text = """
Commander
1 Thrasios, Triton Hero

1 Tymna the Weaver

Deck
1 Sol Ring
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 2)
        self.assertEqual(
            [c["name"] for c in deck.commander],
            ["Thrasios, Triton Hero", "Tymna the Weaver"],
        )
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(deck.mainboard[0]["name"], "Sol Ring")

    def test_commander_inline_prefix(self):
        text = """
Commander: 1 Atraxa, Praetors' Voice
1 Sol Ring
1 Arcane Signet
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 1)
        self.assertEqual(deck.commander[0]["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(len(deck.mainboard), 2)
        self.assertEqual([c["name"] for c in deck.mainboard], ["Sol Ring", "Arcane Signet"])

    def test_cmdr_inline_prefix(self):
        text = """
CMDR: 1 The Ur-Dragon
1 Sol Ring
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 1)
        self.assertEqual(deck.commander[0]["name"], "The Ur-Dragon")
        self.assertEqual(deck.commander[0]["section"], "commander")
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(deck.mainboard[0]["name"], "Sol Ring")

    def test_cmdr_tags_inline(self):
        text = """
1 Atraxa, Praetors' Voice *CMDR*
1 The Ur-Dragon [CMDR]
1 Korvold, Fae-Cursed King (CMDR)
1 Miirym, Sentinel Wyrm CMDR
1 Sol Ring
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 4)
        expected_cmdrs = [
            "Atraxa, Praetors' Voice",
            "The Ur-Dragon",
            "Korvold, Fae-Cursed King",
            "Miirym, Sentinel Wyrm",
        ]
        self.assertEqual([c["name"] for c in deck.commander], expected_cmdrs)
        for c in deck.commander:
            self.assertEqual(c["section"], "commander")

        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(deck.mainboard[0]["name"], "Sol Ring")

    def test_commander_tag_with_set_code(self):
        text = """
1 Atraxa, Praetors' Voice (CM2) 1 *CMDR*
1 Sol Ring (NEO) 123
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.commander), 1)
        cmdr = deck.commander[0]
        self.assertEqual(cmdr["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(cmdr["set_code"], "CM2")
        self.assertEqual(cmdr["collector_number"], "1")
        self.assertEqual(cmdr["section"], "commander")


class TestSideboardRemap(unittest.TestCase):
    """The Sideboard concept is removed: Sideboard input remaps to the
    Maybeboard slot and is flagged on the parse result (confirmable default)."""

    def test_sideboard_header_and_cards(self):
        text = """
Deck
1 Sol Ring

Sideboard
1 Flusterstorm
1 Red Elemental Blast
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(len(deck.maybeboard), 2)
        self.assertEqual([c["name"] for c in deck.maybeboard], ["Flusterstorm", "Red Elemental Blast"])
        for c in deck.maybeboard:
            self.assertEqual(c["section"], "maybeboard")
            self.assertEqual(c["remapped_from"], "sideboard")
        self.assertIn("sideboard_mapped_to_maybeboard", deck.parse_flags)

    def test_sideboard_tags_inline(self):
        text = """
1 Sol Ring
1 Flusterstorm *SB*
1 Red Elemental Blast [SB]
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(len(deck.maybeboard), 2)
        self.assertEqual([c["name"] for c in deck.maybeboard], ["Flusterstorm", "Red Elemental Blast"])
        self.assertIn("sideboard_mapped_to_maybeboard", deck.parse_flags)

    def test_explicit_maybeboard_section_is_not_flagged(self):
        text = """
Deck
1 Sol Ring

Maybeboard
1 Flusterstorm
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.maybeboard), 1)
        self.assertEqual(deck.maybeboard[0]["section"], "maybeboard")
        self.assertNotIn("remapped_from", deck.maybeboard[0])
        self.assertEqual(deck.parse_flags, [])


class TestVariantDeckSlots(unittest.TestCase):
    """Planar / scheme / attraction sections map onto their named slots."""

    def test_planar_deck_section(self):
        text = """
Commander
1 Missy

Deck
1 Sol Ring

Planar Deck
1 The Abyssal Plane
1 Stairs to Infinity
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(len(deck.planar_deck), 2)
        self.assertEqual(
            [c["name"] for c in deck.planar_deck],
            ["The Abyssal Plane", "Stairs to Infinity"],
        )
        for c in deck.planar_deck:
            self.assertEqual(c["section"], "planar_deck")
        self.assertEqual(deck.parse_flags, [])

    def test_planechase_header_alias(self):
        deck = parse_decklist("// Planechase\n1 The Abyssal Plane\n")
        self.assertEqual(len(deck.planar_deck), 1)
        self.assertEqual(deck.planar_deck[0]["name"], "The Abyssal Plane")

    def test_scheme_deck_section(self):
        text = """
Scheme Deck
1 Plots That Span Centuries
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.scheme_deck), 1)
        self.assertEqual(deck.scheme_deck[0]["section"], "scheme_deck")

    def test_archenemy_header_alias(self):
        deck = parse_decklist("Archenemy\n1 Plots That Span Centuries\n")
        self.assertEqual(len(deck.scheme_deck), 1)

    def test_attraction_deck_section(self):
        text = """
Commander
1 The Most Dangerous Gamer

Deck
1 Sol Ring

Attraction Deck
1 Balloon Stand
1 Guess Your Fate
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.attraction_deck), 2)
        self.assertEqual(
            [c["name"] for c in deck.attraction_deck],
            ["Balloon Stand", "Guess Your Fate"],
        )
        for c in deck.attraction_deck:
            self.assertEqual(c["section"], "attraction_deck")

    def test_attractions_header_alias(self):
        deck = parse_decklist("Attractions\n1 Balloon Stand\n")
        self.assertEqual(len(deck.attraction_deck), 1)

    def test_considering_section(self):
        deck = parse_decklist("Considering\n1 Rhystic Study\n")
        self.assertEqual(len(deck.considering), 1)
        self.assertEqual(deck.considering[0]["section"], "considering")

    def test_inline_variant_prefixes(self):
        text = """
1 Sol Ring
Planar Deck: 1 The Abyssal Plane
Schemes: 1 Plots That Span Centuries
Attractions: 1 Balloon Stand
Maybeboard: 1 Flusterstorm
Considering: 1 Rhystic Study
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck.mainboard), 1)
        self.assertEqual(deck.planar_deck[0]["name"], "The Abyssal Plane")
        self.assertEqual(deck.scheme_deck[0]["name"], "Plots That Span Centuries")
        self.assertEqual(deck.attraction_deck[0]["name"], "Balloon Stand")
        self.assertEqual(deck.maybeboard[0]["name"], "Flusterstorm")
        self.assertEqual(deck.considering[0]["name"], "Rhystic Study")
        self.assertEqual(deck.parse_flags, [])

    def test_slots_in_by_section_and_dict_access(self):
        text = """
Deck
1 Sol Ring

Planar Deck
1 The Abyssal Plane
"""
        deck = parse_decklist(text)
        self.assertIn("planar_deck", deck)
        self.assertEqual(len(deck["planar_deck"]), 1)
        self.assertEqual(deck.by_section["planar_deck"][0]["name"], "The Abyssal Plane")
        self.assertEqual(deck.to_dict()["planar_deck"][0]["name"], "The Abyssal Plane")


class TestSplitCards(unittest.TestCase):
    def test_split_card_standard(self):
        text = """
1 Fire // Ice
1 Wear // Tear
1 Commit // Memory
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 3)
        self.assertEqual([c["name"] for c in deck], ["Fire // Ice", "Wear // Tear", "Commit // Memory"])

    def test_split_card_with_set_code(self):
        text = """
1 Fire // Ice (MH2) 290
1 Wear // Tear (DGM) 135
"""
        deck = parse_decklist(text)
        self.assertEqual(deck[0]["name"], "Fire // Ice")
        self.assertEqual(deck[0]["set_code"], "MH2")
        self.assertEqual(deck[0]["collector_number"], "290")

        self.assertEqual(deck[1]["name"], "Wear // Tear")
        self.assertEqual(deck[1]["set_code"], "DGM")
        self.assertEqual(deck[1]["collector_number"], "135")

    def test_split_card_no_space_normalized(self):
        text = """
1 Fire//Ice
1 Wear//Tear
"""
        deck = parse_decklist(text)
        self.assertEqual(deck[0]["name"], "Fire // Ice")
        self.assertEqual(deck[1]["name"], "Wear // Tear")


class TestMalformedAndEdgeCases(unittest.TestCase):
    def test_empty_string(self):
        deck = parse_decklist("")
        self.assertEqual(len(deck), 0)
        self.assertEqual(deck.commander, [])
        self.assertEqual(deck.mainboard, [])
        self.assertEqual(deck.sideboard, [])

    def test_whitespace_only(self):
        deck = parse_decklist("   \n\n\t   \r\n   ")
        self.assertEqual(len(deck), 0)

    def test_none_input(self):
        deck = parse_decklist(None)  # type: ignore
        self.assertEqual(len(deck), 0)

    def test_comments_and_blank_lines(self):
        text = """
# This is a deck comment
// Another random comment
// Lands:

1 Sol Ring
# In-between comment

1 Arcane Signet
// Ending comment
"""
        deck = parse_decklist(text)
        self.assertEqual(len(deck), 2)
        self.assertEqual([c["name"] for c in deck], ["Sol Ring", "Arcane Signet"])

    def test_invalid_formatting_and_garbage(self):
        text = """
---------------------------------
=== Decklist ===
123456
0 Sol Ring
-1 Mana Vault
<html><body>Not a card</body></html>
* * *
1 Sol Ring
"""
        deck = parse_decklist(text)
        # Should only parse the valid "1 Sol Ring"
        self.assertEqual(len(deck), 1)
        self.assertEqual(deck[0]["name"], "Sol Ring")
        self.assertEqual(deck[0]["quantity"], 1)

    def test_parse_deck_dict_helper(self):
        text = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring

Sideboard
1 Swords to Plowshares
"""
        by_sec = parse_deck(text)
        self.assertIn("commander", by_sec)
        self.assertIn("mainboard", by_sec)
        self.assertIn("maybeboard", by_sec)
        self.assertIn("planar_deck", by_sec)
        self.assertIn("scheme_deck", by_sec)
        self.assertIn("attraction_deck", by_sec)
        self.assertNotIn("sideboard", by_sec)
        self.assertEqual(len(by_sec["commander"]), 1)
        self.assertEqual(by_sec["commander"][0]["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(len(by_sec["mainboard"]), 1)
        self.assertEqual(by_sec["mainboard"][0]["name"], "Sol Ring")
        # Sideboard input remapped to the maybeboard slot
        self.assertEqual(len(by_sec["maybeboard"]), 1)
        self.assertEqual(by_sec["maybeboard"][0]["name"], "Swords to Plowshares")

    def test_parsed_deck_dict_like_access(self):
        text = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
"""
        deck = parse_decklist(text)
        self.assertIn("commander", deck)
        self.assertIn("mainboard", deck)
        self.assertEqual(len(deck["commander"]), 1)
        self.assertEqual(deck["commander"][0].name, "Atraxa, Praetors' Voice")
        self.assertEqual(deck["mainboard"][0].quantity, 1)
        self.assertEqual(deck.to_dict()["commander"][0]["name"], "Atraxa, Praetors' Voice")


if __name__ == "__main__":
    unittest.main()
