"""
Unit tests for deck_validator.py and deck_repair.py (pure; no Redis/network).

Run: python tests/test_deck_validator.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.deck_repair as deck_repair
import src.server.domain.draft.deck_validator as dv
from src.server.domain.draft.deck_validator import (
    CardCatalog, CardInfo, DraftCard, DraftDeck, RuleCode, validate_deck, validate_decks,
)


def card(name, ci="", types=("Artifact",), supers=(), subs=(), text="", legality="legal",  # str | None
         keywords=()):
    return CardInfo(
        name=name, color_identity=dv.parse_color_identity(ci), types=tuple(types),
        supertypes=tuple(supers), subtypes=tuple(subs), keywords=tuple(keywords),
        oracle_text=text, commander_legality=legality,
    )


def legend(name, ci, text="", subs=("Human",), keywords=(), legality="legal"):
    return card(name, ci, ("Creature",), ("Legendary",), subs, text, legality=legality,
                keywords=keywords)


CARDS = [
    # commanders
    legend("Atraxa, Praetors' Voice", "WUBG", "Flying, vigilance, deathtouch, lifelink"),
    legend("Mono Red Guy", "R"),
    legend("Rogue Partner A", "UB", "Partner", keywords=("Partner",)),
    legend("Rogue Partner B", "RG", "Partner", keywords=("Partner",)),
    legend("Pir, Imaginative Rascal", "G", "Partner with Toothy, Imaginative Friend\n"
           "(When this creature enters, target player may put Toothy into their hand.)"),
    legend("Toothy, Imaginative Friend", "U", "Partner with Pir, Imaginative Rascal"),
    legend("Pir Impostor", "G", "Partner with Somebody Else"),
    legend("Friend One", "W", "Friends forever"),
    legend("Friend Two", "B", "Friends forever (You can have two commanders if both have friends forever.)"),
    legend("Bg Chooser", "R", "Choose a Background"),
    card("Cult Background", "B", ("Enchantment",), ("Legendary",), ("Background",),
         "Commander creatures you own have 'foo'.\n"),
    legend("Doctor Fan", "U", "Doctor's companion", subs=("Time Lord",)),
    legend("The Doc", "U", "", subs=("Time Lord", "Doctor")),
    legend("Survivor A", "R", "Partner\u2014Survivors"),
    legend("Survivor B", "G", "Partner\u2014Survivors"),
    legend("Survivor Other", "W", "Partner\u2014Father & Son"),
    card("Grist Planeswalker", "BG", ("Planeswalker",), ("Legendary",), (),
         "Grist, the Hunger Tide can be your commander."),
    card("Plain Elf", "G", ("Creature",), (), ("Elf",)),
    legend("Colorless Legend", "", subs=("Golem",)),
    # staples / spells
    card("Sol Ring"),
    card("Arcane Signet"),
    card("Lightning Bolt", "R", ("Instant",)),
    card("Counterspell", "U", ("Instant",)),
    card("Swords to Plowshares", "W", ("Instant",)),
    card("Cultivate", "G", ("Sorcery",)),
    card("Mana Crypt", legality="banned"),
    card("Dockside Extortionist", "R", ("Creature",), (), ("Goblin", "Pirate"), legality="banned"),
    card("Silver Border Thing", "G", ("Creature",), legality="not_legal"),
    # singleton exceptions (oracle text)
    card("Relentless Rats", "B", ("Creature",), (), ("Rat",),
         "Relentless Rats gets +1/+1 for each other creature you control named Relentless Rats.\n"
         "A deck can have any number of cards named Relentless Rats."),
    card("Seven Dwarves", "R", ("Creature",), (), ("Dwarf",),
         "Seven Dwarves gets +1/+1 for each other creature you control named Seven Dwarves.\n"
         "A deck can have up to seven cards named Seven Dwarves."),
    card("Nazgûl", "B", ("Creature",), (), ("Wraith", "Knight"),
         "Deathtouch\nA deck can have up to nine cards named Nazgûl."),
    # exception cards with NO oracle text in the catalog -> fallback list
    card("Persistent Petitioners", "U", ("Creature",), (), ("Advisor",), ""),
    card("Shadowborn Apostle", "B", ("Creature",), (), ("Human", "Cleric"), ""),
    card("Dragon's Approach", "R", ("Sorcery",), (), (), ""),
    # lands
    card("Command Tower", "", ("Land",)),
    card("Snow-Covered Island", "U", ("Land",), ("Basic", "Snow"), ("Island",)),
    # variant-slot type classes (planar / scheme / attraction decks)
    legend("Missy", "UB", subs=("Time Lord",)),
    card("The Abyssal Plane", "", ("Plane",), legality="legal"),
    card("Chaotic Aether", "", ("Phenomenon",), legality="legal"),
    card("A Planar Scheme", "", ("Scheme",), legality="legal"),
    # Real Scryfall layout: type_line "Artifact — Attraction" (Attraction is a
    # SUBTYPE, unlike Plane/Phenomenon/Scheme which are types).
    card("Balloon Stand", "", ("Artifact",), (), ("Attraction",), legality="legal"),
    card("Mystery Card", "", (), legality="legal"),
]
CATALOG = CardCatalog(CARDS)

FILLER_POOL = [f"Filler {i}" for i in range(200)]
CATALOG_FILLER = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL])


def mk_deck(commanders, n_filler=None, extra=(), basics=None, name="d", filler_start=0):
    """Build a deck of exactly 100 (or n_filler-adjusted) cards with unique
    colorless filler so only the requested extras trigger rules."""
    cards = [DraftCard(n, q) for n, q in extra]
    have = len(commanders) + sum(q for _, q in extra)
    fill = (100 - have) if n_filler is None else n_filler
    cards += [DraftCard(FILLER_POOL[filler_start + i], 1) for i in range(fill)]
    return DraftDeck(commanders=list(commanders), cards=cards, name=name)


def codes(result):
    return result.codes()


class Base(unittest.TestCase):
    catalog = CATALOG_FILLER

    def check(self, deck, t1=None, t2=None, **kw):
        return validate_deck(deck, self.catalog, t1, t2, **kw)


class TestBaselineValid(Base):
    def test_valid_100_card_deck(self):
        res = self.check(mk_deck(["Mono Red Guy"], extra=[("Lightning Bolt", 1), ("Sol Ring", 1)]))
        self.assertTrue(res.valid, res.errors)
        self.assertEqual(res.to_dict(), {"valid": True, "errors": []})

    def test_deterministic(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Counterspell", 2), ("Mana Crypt", 1)])
        a, b = self.check(d).to_json(), self.check(d).to_json()
        self.assertEqual(a, b)


class TestDeckSize(Base):
    def test_99_cards_fails(self):
        d = mk_deck(["Mono Red Guy"], n_filler=98)
        res = self.check(d)
        self.assertEqual(codes(res), ["DECK_SIZE"])
        e = res.errors[0]
        self.assertEqual(e.details, {"expected": 100, "actual": 99, "delta": -1})
        self.assertIn("Add 1", e.message)

    def test_101_cards_fails(self):
        d = mk_deck(["Mono Red Guy"], n_filler=100)
        res = self.check(d)
        self.assertEqual(codes(res), ["DECK_SIZE"])
        self.assertEqual(res.errors[0].details["delta"], 1)
        self.assertIn("Remove 1", res.errors[0].message)

    def test_exactly_100_passes_and_counts_quantities(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Mountain", 30)])
        self.assertTrue(self.check(d).valid)

    def test_two_commanders_count_toward_100(self):
        ok = mk_deck(["Rogue Partner A", "Rogue Partner B"])  # 2 + 98
        self.assertTrue(self.check(ok).valid, self.check(ok).errors)
        bad = mk_deck(["Rogue Partner A", "Rogue Partner B"], n_filler=99)  # 101
        self.assertEqual(codes(self.check(bad)), ["DECK_SIZE"])

    def test_invalid_quantity_reported(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Lightning Bolt", 0)], n_filler=99)
        res = self.check(d)
        self.assertIn("INVALID_QUANTITY", codes(res))
        d2 = mk_deck(["Mono Red Guy"], extra=[("Lightning Bolt", -2)], n_filler=99)
        self.assertIn("INVALID_QUANTITY", codes(self.check(d2)))


class TestColorIdentity(Base):
    def test_in_identity_passes(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Lightning Bolt", 1), ("Mountain", 5), ("Sol Ring", 1)])
        self.assertTrue(self.check(d).valid)

    def test_colorless_always_ok(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Command Tower", 1)])
        self.assertTrue(self.check(d).valid)

    def test_off_identity_fails(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Counterspell", 1), ("Island", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["COLOR_IDENTITY"])
        e = res.errors[0]
        self.assertEqual(sorted(e.cards), ["Counterspell", "Island"])
        self.assertEqual(e.details["allowed_identity"], "R")
        self.assertEqual(e.details["off_colors"]["Counterspell"], "U")

    def test_commander_identity_is_union_for_partners(self):
        # Partner A = UB, B = RG -> combined UBRG; Cultivate (G), Counterspell (U) legal
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"],
                    extra=[("Cultivate", 1), ("Counterspell", 1), ("Lightning Bolt", 1)])
        self.assertTrue(self.check(d).valid)
        w = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=[("Swords to Plowshares", 1)])
        res = self.check(w)
        self.assertEqual(codes(res), ["COLOR_IDENTITY"])
        self.assertEqual(res.errors[0].cards, ["Swords to Plowshares"])

    def test_commander_itself_checked_against_union(self):
        # Colorless commander: any colored card is off identity
        d = mk_deck(["Colorless Legend"], extra=[("Lightning Bolt", 1)])
        self.assertEqual(codes(self.check(d)), ["COLOR_IDENTITY"])

    def test_basic_land_off_identity(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Swamp", 3)])
        res = self.check(d)
        self.assertEqual(res.errors[0].cards, ["Swamp"])

    def test_wastes_always_fits(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Wastes", 4)])
        self.assertTrue(self.check(d).valid)

    def test_parse_color_identity_formats(self):
        P = dv.parse_color_identity
        self.assertEqual(P("WUB"), frozenset("WUB"))
        self.assertEqual(P("C"), frozenset())
        self.assertEqual(P(["White", "Blue"]), frozenset("WU"))
        self.assertEqual(P(None), frozenset())
        self.assertEqual(P("Red"), frozenset("R"))


class TestSingleton(Base):
    def test_duplicate_nonbasic_fails(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 2), ("Lightning Bolt", 3)])
        res = self.check(d)
        self.assertEqual(codes(res), ["SINGLETON"])
        self.assertEqual(sorted(res.errors[0].cards), ["Lightning Bolt", "Sol Ring"])
        self.assertEqual(res.errors[0].details["copies"]["Lightning Bolt"], {"count": 3, "max": 1})

    def test_split_entries_are_merged(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 1), ("sol ring", 1)], n_filler=97)
        self.assertEqual(codes(self.check(d)), ["SINGLETON"])

    def test_commander_also_in_mainboard_is_duplicate(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Mono Red Guy", 1)])
        self.assertEqual(codes(self.check(d)), ["SINGLETON"])

    def test_basics_exempt(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Mountain", 40)])
        self.assertTrue(self.check(d).valid)

    def test_snow_basics_and_wastes_exempt(self):
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [
            card("Snow-Covered Mountain", "R", ("Land",), ("Basic", "Snow"), ("Mountain",))])
        d = mk_deck(["Mono Red Guy"], extra=[("Snow-Covered Mountain", 10), ("Wastes", 5)])
        self.assertTrue(validate_deck(d, c).valid)

    def test_snow_basic_exempt_even_if_missing_from_catalog(self):
        # Built-in basic knowledge: no catalog entry needed.
        d = mk_deck(["Colorless Legend"], extra=[("Wastes", 5), ("Snow-Covered Wastes", 5)])
        self.assertTrue(self.check(d).valid)

    def test_any_number_from_oracle_text(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=[("Relentless Rats", 30)])
        self.assertTrue(self.check(d).valid, self.check(d).errors)

    def test_up_to_seven_dwarves(self):
        ok = mk_deck(["Mono Red Guy"], extra=[("Seven Dwarves", 7)])
        self.assertTrue(self.check(ok).valid)
        bad = mk_deck(["Mono Red Guy"], extra=[("Seven Dwarves", 8)])
        res = self.check(bad)
        self.assertEqual(codes(res), ["SINGLETON"])
        self.assertEqual(res.errors[0].details["copies"]["Seven Dwarves"], {"count": 8, "max": 7})

    def test_up_to_nine_nazgul_diacritics(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=[("Nazgûl", 9)])
        self.assertTrue(self.check(d).valid, self.check(d).errors)
        # ASCII spelling resolves to the same card
        d2 = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=[("Nazgul", 10)])
        res = self.check(d2)
        self.assertEqual(codes(res), ["SINGLETON"])
        self.assertEqual(res.errors[0].details["copies"]["Nazgûl"]["max"], 9)

    def test_fallback_list_when_text_missing(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"],
                    extra=[("Persistent Petitioners", 20), ("Shadowborn Apostle", 10),
                           ("Dragon's Approach", 8)], n_filler=98 - 38)
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)

    def test_fallback_limits_are_data_driven(self):
        self.assertEqual(dv.SINGLETON_EXCEPTIONS["Seven Dwarves"], 7)
        self.assertEqual(dv.SINGLETON_EXCEPTIONS["Nazgûl"], 9)
        for n in ("Relentless Rats", "Shadowborn Apostle", "Persistent Petitioners",
                  "Dragon's Approach"):
            self.assertIsNone(dv.SINGLETON_EXCEPTIONS[n])

    def test_singleton_text_parser(self):
        f = dv.singleton_limit_from_text
        self.assertEqual(f("A deck can have any number of cards named Foo."), (True, None))
        self.assertEqual(f("A deck can have up to seven cards named Foo."), (True, 7))
        self.assertEqual(f("A deck can have up to 12 cards named Foo."), (True, 12))
        self.assertEqual(f("Flying"), (False, None))

    def test_exception_card_unaffected_other_rules_still_apply(self):
        # Relentless Rats is black; Mono Red commander -> identity error but no singleton error
        d = mk_deck(["Mono Red Guy"], extra=[("Relentless Rats", 5)])
        self.assertEqual(codes(self.check(d)), ["COLOR_IDENTITY"])


class TestBanlist(Base):
    def test_banned_via_legality_data(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Mana Crypt", 1), ("Dockside Extortionist", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["BANNED"])
        self.assertEqual(sorted(res.errors[0].cards), ["Dockside Extortionist", "Mana Crypt"])

    def test_not_legal_card(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=[("Silver Border Thing", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])

    def test_banned_commander(self):
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [
            legend("Banned Legend", "R", legality="banned")])
        d = mk_deck(["Banned Legend"])
        self.assertEqual(codes(validate_deck(d, c)), ["BANNED"])

    def test_legal_cards_pass(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 1), ("Arcane Signet", 1)])
        self.assertTrue(self.check(d).valid)

    def test_fallback_snapshot_when_legality_missing(self):
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [
            card("Black Lotus", "", legality=None), card("Unlisted Rock", "", legality=None)])
        d = mk_deck(["Mono Red Guy"], extra=[("Black Lotus", 1), ("Unlisted Rock", 1)])
        res = validate_deck(d, c)
        # Snapshot fallback still catches the banned card; the other unknown-
        # legality card fails closed (UNKNOWN_LEGALITY), never silently legal.
        self.assertEqual(codes(res), ["BANNED", "UNKNOWN_LEGALITY"])
        self.assertEqual(res.errors[0].cards, ["Black Lotus"])
        self.assertEqual(res.errors[1].cards, ["Unlisted Rock"])

    def test_unknown_legality_fails_closed(self):
        # Regression: a planar card whose record has no synced legality
        # (commander_legality=None) must NOT pass mainboard validation. It is
        # flagged UNKNOWN_LEGALITY (needs re-sync) AND, since the slot gates
        # landed, NOT_LEGAL for being a plane in the mainboard slot.
        plane = card("The Matrix of Time", "", types=("Plane",), legality=None)
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [plane])
        d = mk_deck(["Mono Red Guy"], extra=[("The Matrix of Time", 1)])
        res = validate_deck(d, c)
        self.assertFalse(res.valid)
        self.assertEqual(codes(res), ["UNKNOWN_LEGALITY", "NOT_LEGAL"])
        self.assertEqual(res.errors[0].cards, ["The Matrix of Time"])
        self.assertIn("re-sync", res.errors[0].message)
        self.assertEqual(res.errors[1].cards, ["The Matrix of Time"])
        self.assertIn("plane not legal in mainboard slot", res.errors[1].message)

    def test_unknown_legality_dropped_by_repair(self):
        plane = card("The Matrix of Time", "", types=("Plane",), legality=None)
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [plane])
        d = mk_deck(["Mono Red Guy"], n_filler=98, extra=[("The Matrix of Time", 1)])
        fixed, actions = deck_repair.drop_invalid_cards(d, c)
        self.assertNotIn("The Matrix of Time", {x.name for x in fixed.cards})
        self.assertTrue(any("legality unknown" in a for a in actions), actions)

    def test_synced_legal_overrides_snapshot(self):
        c = CardCatalog(CARDS + [card(n, "") for n in FILLER_POOL] + [
            card("Black Lotus", "", legality="legal")])
        d = mk_deck(["Mono Red Guy"], extra=[("Black Lotus", 1)])
        self.assertTrue(validate_deck(d, c).valid)


def slotted_deck(commanders=("Missy",), slots=None, n_filler=None, extra=(),
                 name="d"):
    """A 100-card draft carrying named non-main slots (planar/scheme/
    attraction/maybeboard/considering)."""
    d = mk_deck(list(commanders), n_filler=n_filler, extra=extra, name=name)
    d.slots = {slot: [DraftCard(n, q) for n, q in entries]
               for slot, entries in (slots or {}).items()}
    return d


class TestSlotGates(Base):
    """Deterministic per-slot type-class gates (Plane/Phenomenon/Scheme/
    Attraction). Violations are NOT_LEGAL and carry a reason string."""

    # --- mainboard / commander slots reject variant type classes
    def test_plane_in_mainboard_not_legal(self):
        d = mk_deck(["Mono Red Guy"], extra=[("The Abyssal Plane", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        e = res.errors[0]
        self.assertEqual(e.cards, ["The Abyssal Plane"])
        self.assertIn("plane not legal in mainboard slot", e.message)
        self.assertEqual(
            e.details["slot_reasons"]["The Abyssal Plane"],
            {"slot": "mainboard",
             "reason": "plane not legal in mainboard slot (belongs in planar_deck)"})

    def test_phenomenon_scheme_attraction_in_mainboard_not_legal(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"],
                    extra=[("Chaotic Aether", 1), ("A Planar Scheme", 1),
                           ("Balloon Stand", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        e = res.errors[0]
        self.assertEqual(sorted(e.cards),
                         ["A Planar Scheme", "Balloon Stand", "Chaotic Aether"])
        for name, cls in (("Chaotic Aether", "phenomenon"),
                          ("A Planar Scheme", "scheme"),
                          ("Balloon Stand", "attraction")):
            self.assertIn(f"{cls} not legal in mainboard slot",
                          e.details["slot_reasons"][name]["reason"])

    def test_subtype_position_attraction_in_mainboard_not_legal(self):
        # Regression (t_cf694113): a real Attraction is stored as
        # types=("Artifact",), subtypes=("Attraction",) — the gate must scan
        # the subtype position too, or it passes the mainboard slot.
        d = mk_deck(["Mono Red Guy"], extra=[("Balloon Stand", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        e = res.errors[0]
        self.assertEqual(e.cards, ["Balloon Stand"])
        self.assertEqual(
            e.details["slot_reasons"]["Balloon Stand"],
            {"slot": "mainboard",
             "reason": "attraction not legal in mainboard slot "
                       "(belongs in attraction_deck)"})

    def test_subtype_position_attraction_gate_function(self):
        # slot_gate itself classifies a subtype-position Attraction.
        g = dv.slot_gate(self.catalog.get("Balloon Stand"), "mainboard")
        self.assertFalse(g.legal)
        self.assertEqual(g.type_class, "attraction")
        ok = dv.slot_gate(self.catalog.get("Balloon Stand"), "attraction_deck")
        self.assertTrue(ok.legal)
        self.assertEqual(ok.type_class, "attraction")

    def test_ordinary_artifact_still_passes_mainboard(self):
        # An artifact WITHOUT the Attraction subtype is unaffected.
        g = dv.slot_gate(self.catalog.get("Sol Ring"), "mainboard")
        self.assertTrue(g.legal)
        self.assertIsNone(g.type_class)
        d = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 1)])
        self.assertTrue(self.check(d).valid)

    def test_plane_as_commander_not_legal(self):
        d = mk_deck(["The Abyssal Plane"])
        res = self.check(d)
        self.assertEqual(codes(res), ["COMMANDER_ILLEGAL", "NOT_LEGAL"])
        self.assertIn("plane not legal in commander slot", res.errors[1].message)

    # --- variant slots accept only their own type class
    def test_missys_planar_deck_validates(self):
        # Acceptance: Missy's planar deck (planes + phenomenons in the planar
        # slot) validates clean; the slot cards don't touch the 100.
        d = slotted_deck(slots={"planar_deck": [("The Abyssal Plane", 1),
                                                ("Chaotic Aether", 1)]})
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)

    def test_missys_planar_card_in_mainboard_flagged(self):
        # Acceptance: the same planar card placed in the mainboard is flagged
        # NOT_LEGAL with an attachable reason.
        d = slotted_deck(extra=[("The Abyssal Plane", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        self.assertIn("plane not legal in mainboard slot", res.errors[0].message)

    def test_attraction_in_attraction_deck_legal(self):
        d = slotted_deck(slots={"attraction_deck": [("Balloon Stand", 2)]})
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)

    def test_scheme_in_scheme_deck_legal(self):
        d = slotted_deck(slots={"scheme_deck": [("A Planar Scheme", 1)]})
        self.assertTrue(self.check(d).valid)

    def test_scheme_in_planar_deck_not_legal(self):
        d = slotted_deck(slots={"planar_deck": [("A Planar Scheme", 1)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        e = res.errors[0]
        self.assertEqual(e.cards, ["A Planar Scheme"])
        self.assertIn("scheme not legal in planar_deck slot "
                      "(planes and phenomenons only)", e.message)
        self.assertEqual(e.details["slot_reasons"]["A Planar Scheme"]["slot"],
                         "planar_deck")

    def test_plane_in_attraction_deck_not_legal(self):
        d = slotted_deck(slots={"attraction_deck": [("The Abyssal Plane", 1)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        self.assertIn("plane not legal in attraction_deck slot "
                      "(attractions only)", res.errors[0].message)

    def test_ordinary_card_in_planar_deck_not_legal(self):
        d = slotted_deck(slots={"planar_deck": [("Sol Ring", 1)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        self.assertIn("artifact not legal in planar_deck slot", res.errors[0].message)

    def test_unknown_type_card_in_variant_slot_flagged(self):
        # Fail closed: a catalog card with no type data cannot be verified for
        # a variant slot.
        d = slotted_deck(slots={"scheme_deck": [("Mystery Card", 1)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["NOT_LEGAL"])
        e = res.errors[0]
        self.assertEqual(e.cards, ["Mystery Card"])
        self.assertIn("card type unknown; cannot verify scheme_deck slot "
                      "legality", e.message)

    def test_unknown_card_in_variant_slot(self):
        d = slotted_deck(slots={"planar_deck": [("Made Up Plane", 1)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["UNKNOWN_CARD"])
        self.assertEqual(res.errors[0].cards, ["Made Up Plane"])

    def test_invalid_quantity_in_variant_slot(self):
        d = slotted_deck(slots={"planar_deck": [("The Abyssal Plane", 0)]})
        res = self.check(d)
        self.assertEqual(codes(res), ["INVALID_QUANTITY"])
        self.assertIn("named-slot", res.errors[0].message)

    # --- ungated slots / isolation from the main-deck rules
    def test_maybeboard_and_considering_anything_goes(self):
        d = slotted_deck(slots={
            "maybeboard": [("The Abyssal Plane", 1), ("A Planar Scheme", 1)],
            "considering": [("Balloon Stand", 1), ("Fake Card", 1)],
        })
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)

    def test_variant_slots_do_not_count_toward_deck_size(self):
        d = slotted_deck(slots={"planar_deck": [("The Abyssal Plane", 10),
                                                ("Chaotic Aether", 1)]})
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)  # still exactly 100 in the 100

    def test_variant_slots_exempt_from_singleton_and_color_identity(self):
        # 2 copies of one plane in the planar deck of a colorless-identity
        # deck: no SINGLETON / COLOR_IDENTITY noise from variant slots.
        d = slotted_deck(commanders=("Colorless Legend",),
                         slots={"planar_deck": [("The Abyssal Plane", 2)]})
        res = self.check(d)
        self.assertTrue(res.valid, res.errors)

    # --- the gate function itself: pass/fail + REASON strings
    def test_slot_gate_result_reasons(self):
        cat = self.catalog
        ok = dv.slot_gate(cat.get("The Abyssal Plane"), "planar_deck")
        self.assertTrue(ok.legal)
        self.assertEqual(ok.reason, "plane is legal in the planar_deck slot")
        self.assertEqual(ok.type_class, "plane")
        bad = dv.slot_gate(cat.get("The Abyssal Plane"), "mainboard")
        self.assertFalse(bad.legal)
        self.assertEqual(bad.reason,
                         "plane not legal in mainboard slot (belongs in planar_deck)")
        free = dv.slot_gate(cat.get("The Abyssal Plane"), "maybeboard")
        self.assertTrue(free.legal)
        self.assertIn("no type gate", free.reason)
        missing = dv.slot_gate(cat.get("Made Up Plane"), "planar_deck",
                               card_name="Made Up Plane")
        self.assertFalse(missing.legal)
        self.assertIn("not in the catalog", missing.reason)
        unknown = dv.slot_gate(cat.get("Mystery Card"), "planar_deck")
        self.assertFalse(unknown.legal)
        self.assertIn("card type unknown", unknown.reason)
        self.assertEqual(ok.to_dict()["reason"], ok.reason)

    def test_gate_deck_slots_covers_every_occupied_slot(self):
        d = slotted_deck(slots={"planar_deck": [("The Abyssal Plane", 1),
                                                ("A Planar Scheme", 1)],
                                "maybeboard": [("Sol Ring", 1)]})
        results = dv.gate_deck_slots(d, self.catalog)
        by_card = {g.card: g for g in results}
        self.assertIn("Missy", by_card)             # commander slot
        self.assertIn("Filler 0", by_card)          # mainboard slot
        self.assertFalse(by_card["A Planar Scheme"].legal)
        self.assertTrue(by_card["The Abyssal Plane"].legal)
        self.assertTrue(by_card["Sol Ring"].legal)  # maybeboard ungated
        self.assertEqual(by_card["Missy"].slot, "commander")
        self.assertEqual(by_card["Filler 0"].slot, "mainboard")
        for g in results:
            self.assertTrue(g.reason)  # reason always populated

    def test_from_dict_slots_adapter(self):
        d = DraftDeck.from_dict({
            "name": "Missy",
            "commanders": ["Missy"],
            "cards": {f"Filler {i}": 1 for i in range(99)},
            "slots": {"Planar Deck": [{"name": "The Abyssal Plane", "quantity": 1}],
                      "Sideboard": [{"name": "Sol Ring", "quantity": 1}]},
        })
        self.assertEqual([c.name for c in d.slots["planar_deck"]],
                         ["The Abyssal Plane"])
        # Removed Sideboard concept folds into maybeboard.
        self.assertEqual([c.name for c in d.slots["maybeboard"]], ["Sol Ring"])
        self.assertTrue(self.check(d).valid)

    def test_from_dict_slot_form_commander_and_mainboard(self):
        d = DraftDeck.from_dict({
            "slots": {"commander": [{"name": "Missy"}],
                      "mainboard": [{"name": f"Filler {i}"} for i in range(99)],
                      "planar_deck": [{"name": "Chaotic Aether"}]},
        })
        self.assertEqual(d.commanders, ["Missy"])
        self.assertEqual(len(d.cards), 99)
        self.assertTrue(self.check(d).valid, self.check(d).errors)

    def test_repair_drops_slot_violation_and_preserves_slots(self):
        # A synced-'legal' plane sitting in the mainboard is dropped by repair
        # with the slot-gate reason; named slots survive auto_repair.
        d = slotted_deck(extra=[("The Abyssal Plane", 1)],
                         slots={"planar_deck": [("Chaotic Aether", 1)]})
        fixed, actions = deck_repair.drop_invalid_cards(d, self.catalog)
        self.assertNotIn("The Abyssal Plane", {c.name for c in fixed.cards})
        self.assertTrue(any("not legal in mainboard slot" in a for a in actions),
                        actions)
        fixed2, _ = deck_repair.auto_repair(d, self.catalog)
        self.assertEqual([c.name for c in fixed2.slots["planar_deck"]],
                         ["Chaotic Aether"])
        self.assertTrue(self.check(fixed2).valid, self.check(fixed2).errors)


class TestCommanderAndPartners(Base):
    def test_single_legendary_creature_ok(self):
        self.assertTrue(self.check(mk_deck(["Mono Red Guy"])).valid)

    def test_can_be_your_commander_planeswalker_ok(self):
        self.assertTrue(self.check(mk_deck(["Grist Planeswalker"])).valid)

    def test_nonlegendary_creature_illegal_commander(self):
        res = self.check(mk_deck(["Plain Elf"]))
        self.assertEqual(codes(res), ["COMMANDER_ILLEGAL"])
        self.assertEqual(res.errors[0].cards, ["Plain Elf"])

    def test_noncreature_illegal_commander(self):
        res = self.check(mk_deck(["Sol Ring"]))
        self.assertEqual(codes(res), ["COMMANDER_ILLEGAL"])

    def test_no_commander_fails(self):
        d = mk_deck([], n_filler=100)
        res = self.check(d)
        self.assertEqual(codes(res), ["COMMANDER_COUNT"])

    def test_three_commanders_fail(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B", "Mono Red Guy"])
        self.assertIn("COMMANDER_COUNT", codes(self.check(d)))

    def test_unknown_commander_reported(self):
        res = self.check(mk_deck(["Totally Made Up Legend"]))
        self.assertIn("UNKNOWN_CARD", codes(res))

    # --- legal pairs
    def pair(self, a, b):
        return self.check(mk_deck([a, b]))

    def test_partner_pair_legal(self):
        self.assertTrue(self.pair("Rogue Partner A", "Rogue Partner B").valid)

    def test_partner_with_legal(self):
        res = self.pair("Pir, Imaginative Rascal", "Toothy, Imaginative Friend")
        self.assertTrue(res.valid, res.errors)

    def test_partner_with_wrong_partner_illegal(self):
        res = self.pair("Pir, Imaginative Rascal", "Rogue Partner A")
        self.assertEqual(codes(res), ["PARTNER_INVALID"])
        res = self.pair("Pir Impostor", "Rogue Partner A")
        self.assertEqual(codes(res), ["PARTNER_INVALID"])

    def test_partner_with_not_reciprocal_illegal(self):
        # Pir Impostor names "Somebody Else"; Toothy names Pir, not Impostor
        res = self.pair("Pir Impostor", "Toothy, Imaginative Friend")
        self.assertEqual(codes(res), ["PARTNER_INVALID"])

    def test_friends_forever_legal(self):
        self.assertTrue(self.pair("Friend One", "Friend Two").valid)

    def test_friends_forever_with_plain_partner_illegal(self):
        self.assertEqual(codes(self.pair("Friend One", "Rogue Partner A")), ["PARTNER_INVALID"])

    def test_choose_a_background_legal(self):
        res = self.pair("Bg Chooser", "Cult Background")
        self.assertTrue(res.valid, res.errors)
        # order independent
        self.assertTrue(self.pair("Cult Background", "Bg Chooser").valid)

    def test_background_without_chooser_illegal(self):
        res = self.pair("Mono Red Guy", "Cult Background")
        self.assertEqual(codes(res), ["PARTNER_INVALID"])

    def test_two_chooser_cards_illegal(self):
        self.assertEqual(codes(self.pair("Bg Chooser", "Rogue Partner A")), ["PARTNER_INVALID"])

    def test_doctors_companion_legal(self):
        res = self.pair("Doctor Fan", "The Doc")
        self.assertTrue(res.valid, res.errors)

    def test_doctors_companion_without_doctor_illegal(self):
        self.assertEqual(codes(self.pair("Doctor Fan", "Mono Red Guy")), ["PARTNER_INVALID"])

    def test_partner_variant_groups(self):
        self.assertTrue(self.pair("Survivor A", "Survivor B").valid)
        self.assertEqual(codes(self.pair("Survivor A", "Survivor Other")), ["PARTNER_INVALID"])
        self.assertEqual(codes(self.pair("Survivor A", "Rogue Partner A")), ["PARTNER_INVALID"])

    def test_two_unrelated_commanders_illegal(self):
        res = self.pair("Mono Red Guy", "Atraxa, Praetors' Voice")
        self.assertEqual(codes(res), ["PARTNER_INVALID"])
        self.assertEqual(sorted(res.errors[0].cards), ["Atraxa, Praetors' Voice", "Mono Red Guy"])

    def test_same_commander_twice_illegal(self):
        res = self.check(mk_deck(["Rogue Partner A", "Rogue Partner A"]))
        self.assertIn("PARTNER_INVALID", codes(res))

    def test_partner_keyword_only_detected(self):
        c = CardCatalog([
            legend("Kw One", "R", "", keywords=("Partner",)),
            legend("Kw Two", "G", "", keywords=("Partner",)),
        ] + [card(n, "") for n in FILLER_POOL])
        self.assertTrue(validate_deck(mk_deck(["Kw One", "Kw Two"]), c).valid)

    def test_reminder_text_does_not_grant_partner(self):
        c = CardCatalog([
            legend("Rem One", "R", "Haste\n(Partner is a keyword some other cards have.)"),
            legend("Rem Two", "G", "Partner"),
        ] + [card(n, "") for n in FILLER_POOL])
        self.assertEqual(codes(validate_deck(mk_deck(["Rem One", "Rem Two"]), c)), ["PARTNER_INVALID"])


class TestUnknownCards(Base):
    def test_unknown_mainboard_card(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Lightning Bolte", 1)])
        res = self.check(d)
        self.assertEqual(codes(res), ["UNKNOWN_CARD"])
        self.assertEqual(res.errors[0].cards, ["Lightning Bolte"])


class TestSharedPool(Base):
    def two(self, a_extra, b_extra, t1=None, t2=None, **kw):
        a = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=a_extra, name="a")
        b = mk_deck(["Rogue Partner A", "Rogue Partner B"], extra=b_extra, name="b",
                    filler_start=100)
        return validate_decks([a, b], self.catalog, t1, t2, **kw), a, b

    def test_within_quantity_passes(self):
        res, *_ = self.two([("Sol Ring", 1)], [], t1={"Sol Ring": 1})
        self.assertTrue(res.valid, res.errors)

    def test_single_card_in_both_decks_with_one_copy_conflicts(self):
        # Commanders are also shared: each draft uses both partners.
        res, *_ = self.two([("Sol Ring", 1)], [("Cultivate", 1), ("Sol Ring", 1)],
                           t1={"Sol Ring": 1, "Rogue Partner A": 2, "Rogue Partner B": 2})
        self.assertEqual(codes(res), ["SHARED_POOL_CONFLICT"])
        e = res.errors[0]
        self.assertEqual(e.cards, ["Sol Ring"])
        self.assertIsNone(e.deck_index)
        self.assertEqual(e.details, {"requested": 2, "available": 1,
                                     "per_deck": {"deck_0": 1, "deck_1": 1}})

    def test_two_copies_available_allows_both(self):
        res, *_ = self.two([("Sol Ring", 1)], [("Sol Ring", 1)], t1={"Sol Ring": 2})
        self.assertTrue(res.valid, res.errors)

    def test_tiers_are_summed(self):
        res, *_ = self.two([("Sol Ring", 1)], [("Sol Ring", 1)],
                           t1={"Sol Ring": 1}, t2={"Sol Ring": 1})
        self.assertTrue(res.valid, res.errors)
        res, *_ = self.two([("Sol Ring", 1)], [("Sol Ring", 1)],
                           t1={"Sol Ring": 1}, t2={"Cultivate": 1})
        self.assertEqual(codes(res), ["SHARED_POOL_CONFLICT"])

    def test_tier2_only_conflict(self):
        res, *_ = self.two([("Cultivate", 1)], [("Cultivate", 1)], t2={"Cultivate": 1})
        self.assertEqual(res.errors[0].cards, ["Cultivate"])

    def test_commanders_shared_across_decks_conflict(self):
        res, *_ = self.two([], [], t1={"Rogue Partner A": 1, "Rogue Partner B": 1})
        self.assertEqual(sorted(e.cards[0] for e in res.errors),
                         ["Rogue Partner A", "Rogue Partner B"])

    def test_single_deck_over_quantity_conflict(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Lightning Bolt", 2)])
        res = validate_decks([d], self.catalog, {"Lightning Bolt": 1})
        self.assertIn("SHARED_POOL_CONFLICT", codes(res))

    def test_cards_not_in_pool_unconstrained(self):
        res, *_ = self.two([("Sol Ring", 1)], [("Sol Ring", 1)], t1={"Cultivate": 1})
        self.assertTrue(res.valid)

    def test_basics_exempt_by_default_but_can_be_enforced(self):
        pool = {"Mountain": 10}
        res, *_ = self.two([("Mountain", 8)], [("Mountain", 8)], t1=pool)
        self.assertTrue(res.valid, res.errors)
        res, *_ = self.two([("Mountain", 8)], [("Mountain", 8)], t1=pool,
                           exempt_basics_from_pool=False)
        self.assertEqual(codes(res), ["SHARED_POOL_CONFLICT"])
        self.assertEqual(res.errors[0].details["requested"], 16)

    def test_name_matching_is_normalized(self):
        res, *_ = self.two([("Nazgûl", 5)], [("Nazgul", 5)], t1={"nazgûl": 9})
        self.assertEqual(codes(res), ["SHARED_POOL_CONFLICT"])
        self.assertEqual(res.errors[0].details["available"], 9)

    def test_conflict_reported_alongside_per_deck_errors(self):
        a = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 1)], n_filler=97)  # 99 cards
        b = mk_deck(["Mono Red Guy"], extra=[("Sol Ring", 1)], filler_start=100)
        res = validate_decks([a, b], self.catalog, {"Sol Ring": 1, "Mono Red Guy": 2})
        self.assertEqual(codes(res), ["DECK_SIZE", "SHARED_POOL_CONFLICT"])
        self.assertEqual(res.errors[0].deck_index, 0)
        self.assertEqual(len(res.errors_for(0)), 1)
        self.assertEqual(len(res.errors_for(None)), 1)


class TestDeckCount(Base):
    def test_zero_or_three_decks(self):
        self.assertEqual(codes(validate_decks([], self.catalog)), ["DECK_COUNT"])
        d = mk_deck(["Mono Red Guy"])
        self.assertEqual(codes(validate_decks([d, d, d], self.catalog)), ["DECK_COUNT"])

    def test_error_ordering_by_deck(self):
        a = mk_deck(["Plain Elf"], n_filler=98)
        b = mk_deck(["Mono Red Guy"], n_filler=98, filler_start=100)
        res = validate_decks([a, b], self.catalog)
        self.assertEqual([e.deck_index for e in res.errors], [0, 0, 1])


class TestStructuredOutput(Base):
    def test_error_dict_is_json_serialisable(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Counterspell", 2), ("Mana Crypt", 1)])
        res = self.check(d)
        payload = json.loads(res.to_json())
        self.assertFalse(payload["valid"])
        for e in payload["errors"]:
            self.assertEqual(set(e), {"code", "cards", "message", "deck_index", "details"})
            self.assertIsInstance(e["code"], str)
            self.assertIsInstance(e["cards"], list)
            self.assertTrue(e["message"])
        self.assertEqual({e["code"] for e in payload["errors"]},
                         {"COLOR_IDENTITY", "SINGLETON", "BANNED"})

    def test_prompt_formatter(self):
        d = mk_deck(["Mono Red Guy"], n_filler=98)
        text = dv.format_errors_for_prompt(self.check(d))
        self.assertIn("[DECK_SIZE] (deck 0)", text)
        self.assertEqual(dv.format_errors_for_prompt(self.check(mk_deck(["Mono Red Guy"]))), "VALID")


class TestAdapters(Base):
    def test_from_builder_draft_strips_commander_entries(self):
        draft = {
            "id": "draft_0", "name": "X", "commanders": ["Rogue Partner A", "Rogue Partner B"],
            "cards": [{"name": "Rogue Partner A", "quantity": 1, "tier": 1},
                      {"name": "Rogue Partner B", "quantity": 1, "tier": 1}]
                     + [{"name": n, "quantity": 1, "tier": 3} for n in FILLER_POOL[:98]],
        }
        deck = DraftDeck.from_builder_draft(draft)
        self.assertEqual(len(deck.cards), 98)
        self.assertTrue(self.check(deck).valid)

    def test_from_dict_mapping_cards(self):
        deck = DraftDeck.from_dict({"commanders": ["Mono Red Guy"],
                                    "cards": {"Mountain": 99}})
        self.assertTrue(self.check(deck).valid)

    def test_card_info_from_sync_record(self):
        rec = {"oracle_id": "x", "name": "Foo", "type": "Legendary Creature", "color": "WU",
               "super_types": ["Legendary"], "sub_types": ["Human"], "keywords": [],
               "oracle_text": "", "commander_legality": "banned"}
        info = CardInfo.from_record(rec)
        self.assertEqual(info.color_identity, frozenset("WU"))
        self.assertTrue(info.is_legendary and info.is_creature)
        self.assertEqual(info.commander_legality, "banned")
        rec2 = {"name": "Bar", "color": "C", "types": ["Creature"], "super_types": ["Legendary"],
                "legalities": {"commander": "Legal"}}
        info2 = CardInfo.from_record(rec2)
        self.assertEqual(info2.color_identity, frozenset())
        self.assertEqual(info2.commander_legality, "legal")
        self.assertTrue(info2.can_be_commander)

    def test_catalog_from_redis_with_fakes(self):
        class R:
            def __init__(self):
                self.d = {
                    "idx:card_name_to_oracle:Sol Ring": "oid1",
                    "card:oid1": json.dumps({"oracle_id": "oid1", "name": "Sol Ring", "color": "C",
                                             "type": "Artifact", "commander_legality": "legal"}),
                    "card_meta:Old Card": json.dumps({"type": "Instant", "color": "R"}),
                }

            def get(self, k):
                return self.d.get(k)

        cat = dv.catalog_from_redis(R(), ["Sol Ring", "Old Card", "Missing"])
        self.assertIn("Sol Ring", cat)
        self.assertEqual(cat.get("Old Card").color_identity, frozenset("R"))  # type: ignore[union-attr]
        self.assertNotIn("Missing", cat)
        self.assertIn("Island", cat)  # basics always resolvable

    def test_catalog_from_redis_fallback_normalized_and_dfc(self):
        class R:
            def __init__(self):
                self.d = {
                    "idx:card_name_to_oracle:Gríma, Saruman's Footman": "o1",
                    "card:o1": json.dumps({"oracle_id": "o1", "name": "Gríma, Saruman's Footman",
                                           "color": "B", "type_line": "Legendary Creature",
                                           "commander_legality": "legal"}),
                    "idx:card_name_to_oracle:Delver of Secrets // Insectile Aberration": "o2",
                    "card:o2": json.dumps({"oracle_id": "o2",
                                           "name": "Delver of Secrets // Insectile Aberration",
                                           "color": "U", "type": "Creature",
                                           "commander_legality": "legal"}),
                    "card_meta:Sol Ring": json.dumps({"type": "Artifact", "color": ""}),
                }

            def get(self, k):
                return self.d.get(k)

            def scan_iter(self, match="*", count=None):
                pre = match.rstrip("*")
                return iter([k for k in self.d if k.startswith(pre)])

        cat = dv.catalog_from_redis(R(), ["Grima, Saruman's Footman", "delver of secrets", "SOL RING", "Nope"])
        self.assertEqual(cat.get("Grima, Saruman's Footman").name, "Gríma, Saruman's Footman")  # type: ignore[union-attr]
        self.assertIn("Delver of Secrets", cat)
        self.assertIn("Sol Ring", cat)
        self.assertNotIn("Nope", cat)


class TestRepair(Base):
    def test_does_not_mutate_input(self):
        d = mk_deck(["Mono Red Guy"], n_filler=98)
        before = [(c.name, c.quantity) for c in d.cards]
        deck_repair.auto_repair(d, self.catalog)
        self.assertEqual(before, [(c.name, c.quantity) for c in d.cards])

    def test_pad_with_basics_split_by_identity(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"], n_filler=90)  # 92 total
        fixed, actions = deck_repair.fit_to_size(d, self.catalog)
        self.assertTrue(self.check(fixed).valid, self.check(fixed).errors)
        added = {c.name: c.quantity for c in fixed.cards if c.name in dv.BASIC_LAND_NAMES}
        self.assertEqual(sum(added.values()), 8)
        self.assertEqual(sorted(added), ["Forest", "Island", "Mountain", "Swamp"])
        self.assertEqual(len(actions), 4)

    def test_pad_colorless_uses_wastes(self):
        d = mk_deck(["Colorless Legend"], n_filler=90)
        fixed, _ = deck_repair.fit_to_size(d, self.catalog)
        self.assertTrue(self.check(fixed).valid)
        self.assertEqual([c.name for c in fixed.cards if c.name == "Wastes"], ["Wastes"])

    def test_trim_removes_basics_first(self):
        d = mk_deck(["Mono Red Guy"], extra=[("Mountain", 30)], n_filler=72)  # 103
        fixed, actions = deck_repair.fit_to_size(d, self.catalog)
        self.assertTrue(self.check(fixed).valid)
        self.assertEqual({c.name: c.quantity for c in fixed.cards}["Mountain"], 27)

    def test_trim_nonbasics_when_no_basics(self):
        d = mk_deck(["Mono Red Guy"], n_filler=101)  # 102
        fixed, _ = deck_repair.fit_to_size(d, self.catalog)
        self.assertTrue(self.check(fixed).valid)
        self.assertEqual(sum(c.quantity for c in fixed.cards), 99)

    def test_dedupe_singleton_keeps_exceptions(self):
        d = mk_deck(["Rogue Partner A", "Rogue Partner B"],
                    extra=[("Sol Ring", 3), ("Seven Dwarves", 9), ("Relentless Rats", 5),
                           ("Mountain", 4)], n_filler=80)
        fixed, actions = deck_repair.dedupe_singleton(d, self.catalog)
        q = {c.name: c.quantity for c in fixed.cards}
        self.assertEqual((q["Sol Ring"], q["Seven Dwarves"], q["Relentless Rats"], q["Mountain"]),
                         (1, 7, 5, 4))
        self.assertEqual(len(actions), 2)

    def test_dedupe_merges_split_entries_and_commander_dupe(self):
        d = mk_deck(["Mono Red Guy"],
                    extra=[("Sol Ring", 1), ("Sol Ring", 1), ("Mono Red Guy", 1)], n_filler=10)
        fixed, _ = deck_repair.dedupe_singleton(d, self.catalog)
        names = [c.name for c in fixed.cards]
        self.assertEqual(names.count("Sol Ring"), 1)
        self.assertNotIn("Mono Red Guy", names)

    def test_drop_invalid_cards(self):
        d = mk_deck(["Mono Red Guy"], n_filler=90,
                    extra=[("Counterspell", 1), ("Mana Crypt", 1), ("Fake Card", 1),
                           ("Silver Border Thing", 1), ("Lightning Bolt", 1)])
        fixed, actions = deck_repair.drop_invalid_cards(d, self.catalog)
        names = {c.name for c in fixed.cards}
        self.assertEqual(names & {"Counterspell", "Mana Crypt", "Fake Card", "Silver Border Thing"},
                         set())
        self.assertIn("Lightning Bolt", names)
        self.assertEqual(len(actions), 4)

    def test_auto_repair_end_to_end_and_idempotent(self):
        d = mk_deck(["Mono Red Guy"], n_filler=80,
                    extra=[("Counterspell", 1), ("Mana Crypt", 1), ("Sol Ring", 3),
                           ("Lightning Bolt", 1)])
        self.assertFalse(self.check(d).valid)
        fixed, actions = deck_repair.auto_repair(d, self.catalog)
        res = self.check(fixed)
        self.assertTrue(res.valid, res.errors)
        self.assertTrue(actions)
        again, actions2 = deck_repair.auto_repair(fixed, self.catalog)
        self.assertEqual(actions2, [])
        self.assertEqual([(c.name, c.quantity) for c in again.cards],
                         [(c.name, c.quantity) for c in fixed.cards])

    def test_auto_repair_leaves_commander_problems(self):
        d = mk_deck(["Plain Elf"], n_filler=95)
        fixed, _ = deck_repair.auto_repair(d, self.catalog)
        self.assertEqual(codes(self.check(fixed)), ["COMMANDER_ILLEGAL"])


if __name__ == "__main__":
    unittest.main()
