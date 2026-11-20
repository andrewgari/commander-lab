"""Fixture data for the Brew verification matrix (docs/brew-test-matrix.md,
spec brew-test-matrix-v1). Identities / legalities were Scryfall-verified on
2026-11-06 by the oracle task; this file is the code-ready form of section 6."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from src.server.domain.draft.deck_validator import CardCatalog, CardInfo
from tests.support.brew_harness import build_fixture_catalog

L = ("Legendary",)


def _c(name, ident="", types=("Creature",), supers=(), text="", legality="legal"):
    return CardInfo(name=name, color_identity=frozenset(ident), types=tuple(types),
                    supertypes=tuple(supers), commander_legality=legality, oracle_text=text)


_CARDS: List[CardInfo] = [
    # --- commanders ---
    _c("Krenko, Mob Boss", "R", supers=L),
    _c("Meren of Clan Nel Toth", "BG", supers=L),
    _c("Korvold, Fae-Cursed King", "BRG", supers=L),
    _c("Kozilek, the Great Distortion", "", supers=L),
    _c("Thrasios, Triton Hero", "GU", supers=L, text="Partner"),
    _c("Tymna the Weaver", "WB", supers=L, text="Partner"),
    _c("Wort, the Raidmother", "RG", supers=L),
    _c("Alesha, Who Smiles at Death", "WBR", supers=L,
       text="Whenever Alesha attacks, you may pay {W/B}{W/B}."),
    # --- pair fixtures (section 3) ---
    _c("Kraum, Ludevic's Opus", "UR", supers=L, text="Partner"),
    _c("Pir, Imaginative Rascal", "G", supers=L, text="Partner with Toothy, Imaginary Friend"),
    _c("Toothy, Imaginary Friend", "U", supers=L, text="Partner with Pir, Imaginative Rascal"),
    _c("Wilson, Refined Grizzly", "G", supers=L, text="Choose a Background"),
    CardInfo(name="Raised by Giants", color_identity=frozenset("G"), types=("Enchantment",),
             supertypes=L, subtypes=("Background",), commander_legality="legal"),
    # --- GCs (all on the game_changers snapshot) ---
    _c("Gamble", "R", ("Sorcery",)), _c("Jeska's Will", "R", ("Sorcery",)),
    _c("Underworld Breach", "R", ("Enchantment",)), _c("Mana Vault", "", ("Artifact",)),
    _c("Grim Monolith", "", ("Artifact",)), _c("The One Ring", "", ("Artifact",), L),
    _c("Ancient Tomb", "", ("Land",)), _c("Survival of the Fittest", "G", ("Enchantment",)),
    _c("Worldly Tutor", "G", ("Instant",)), _c("Demonic Tutor", "B", ("Sorcery",)),
    _c("Necropotence", "B", ("Enchantment",)), _c("Gaea's Cradle", "", ("Land",), L),
    _c("Natural Order", "G", ("Sorcery",)), _c("Rhystic Study", "U", ("Enchantment",)),
    _c("Smothering Tithe", "W", ("Enchantment",)), _c("Cyclonic Rift", "U", ("Instant",)),
    _c("Teferi's Protection", "W", ("Instant",)), _c("Enlightened Tutor", "W", ("Instant",)),
    # --- MUST_EXCLUDE (off-identity somewhere) ---
    _c("Orcish Lumberjack", "RG"), _c("Etali, Primal Conqueror", "GR", supers=L),
    _c("Boggart Ram-Gang", "RG"), _c("Relentless Rats", "B",
       text="A deck can have any number of cards named Relentless Rats."),
    _c("Damn", "BW", ("Sorcery",)), _c("Murderous Redcap", "BR"), _c("Kitchen Finks", "GW"),
    _c("Spellskite", "U", ("Artifact", "Creature")), _c("Brain Freeze", "U", ("Sorcery",)),
    _c("Plains", "W", ("Land",), ("Basic",)),
    _c("Snow-Covered Plains", "W", ("Land",), ("Basic", "Snow")),
    _c("Eldrazi Displacer", "W"), _c("Iron Myr", "R", ("Artifact", "Creature")),
    _c("Haywire Mite", "G", ("Artifact", "Creature")), _c("Mind Over Matter", "U", ("Enchantment",)),
    _c("Manamorphose", "RG", ("Instant",)), _c("Gut Shot", "R", ("Instant",)),
    _c("Jegantha, the Wellspring", "RG", supers=L),
    _c("Figure of Destiny", "RW"), _c("Deathrite Shaman", "BG"),
    # --- MUST_ALLOW ---
    _c("Seven Dwarves", "R", text="A deck can have up to seven cards named Seven Dwarves."),
    _c("Dismember", "B", ("Instant",)), _c("Birthing Pod", "G", ("Artifact",)),
    _c("Elves of Deep Shadow", "BG"), _c("Rakdos Cackler", "BR"), _c("Mayhem Devil", "BR"),
    _c("Snow-Covered Wastes", "", ("Land",), ("Basic", "Snow")),
    _c("Thought-Knot Seer", ""), _c("Basalt Monolith", "", ("Artifact",)),
    _c("Thassa's Oracle", "U"), _c("Swords to Plowshares", "W", ("Instant",)),
    _c("Syndic of Tithes", "W", text="Extort ({W/B} reminder text)"),
    _c("Lightning Bolt", "R", ("Instant",)),
    # --- BANNED_TRAPS (in-identity for their rows; legality=banned) ---
    *[_c(n, i, t, legality="banned") for n, i, t in [
        ("Dockside Extortionist", "R", ("Creature",)), ("Falling Star", "R", ("Artifact",)),
        ("Mox Ruby", "", ("Artifact",)), ("Recurring Nightmare", "B", ("Enchantment",)),
        ("Sylvan Primordial", "G", ("Creature",)), ("Fastbond", "G", ("Enchantment",)),
        ("Primeval Titan", "G", ("Creature",)), ("Griselbrand", "B", ("Creature",)),
        ("Mana Crypt", "", ("Artifact",)), ("Sundering Titan", "", ("Artifact",)),
        ("Paradox Engine", "", ("Artifact",)), ("Leovold, Emissary of Trest", "BUG", ("Creature",)),
        ("Prophet of Kruphix", "GU", ("Creature",)), ("Nadu, Winged Wisdom", "GU", ("Creature",)),
        ("Rofellos, Llanowar Emissary", "G", ("Creature",)), ("Balance", "W", ("Sorcery",)),
        ("Iona, Shield of Emeria", "W", ("Creature",)),
    ]],
]


def matrix_catalog() -> CardCatalog:
    cat = build_fixture_catalog()
    for c in _CARDS:
        cat.add(c)
    return cat


@dataclass(frozen=True)
class Row:
    key: str
    commanders: Tuple[str, ...]
    kind: str
    identity: frozenset
    must_exclude: Tuple[str, ...]
    must_allow: Tuple[str, ...]
    banned_traps: Tuple[str, ...]
    gc_picks: Tuple[str, ...]            # 4 in-identity Game Changers, used for mid (2) / high (4)
    soft_exclude: Tuple[str, ...] = ()   # SHOULD NOT include (warn only)


ROWS: Dict[str, Row] = {r.key: r for r in [
    Row("krenko", ("Krenko, Mob Boss",), "mono", frozenset("R"),
        ("Orcish Lumberjack", "Etali, Primal Conqueror", "Boggart Ram-Gang", "Relentless Rats"),
        ("Gut Shot", "Iron Myr", "Seven Dwarves"),
        ("Dockside Extortionist", "Falling Star", "Mox Ruby"),
        ("Gamble", "Jeska's Will", "Underworld Breach", "Mana Vault")),
    Row("meren", ("Meren of Clan Nel Toth",), "2-color", frozenset("BG"),
        ("Damn", "Murderous Redcap", "Kitchen Finks", "Leovold, Emissary of Trest"),
        ("Deathrite Shaman", "Dismember", "Birthing Pod", "Elves of Deep Shadow"),
        ("Recurring Nightmare", "Sylvan Primordial", "Fastbond"),
        ("Survival of the Fittest", "Worldly Tutor", "Demonic Tutor", "Necropotence")),
    Row("korvold", ("Korvold, Fae-Cursed King",), "3-color", frozenset("BRG"),
        ("Kitchen Finks", "Spellskite", "Brain Freeze"),
        ("Rakdos Cackler", "Manamorphose", "Mayhem Devil"),
        ("Dockside Extortionist", "Primeval Titan", "Griselbrand"),
        ("Demonic Tutor", "Survival of the Fittest", "Necropotence", "Gaea's Cradle")),
    Row("kozilek", ("Kozilek, the Great Distortion",), "colorless", frozenset(),
        ("Plains", "Snow-Covered Plains", "Eldrazi Displacer", "Spellskite", "Iron Myr",
         "Haywire Mite", "Mind Over Matter"),
        ("Snow-Covered Wastes", "Thought-Knot Seer", "Basalt Monolith"),
        ("Mana Crypt", "Sundering Titan", "Paradox Engine"),
        ("The One Ring", "Ancient Tomb", "Mana Vault", "Grim Monolith"),
        soft_exclude=("Command Tower", "Arcane Signet")),
    Row("thrasios_tymna", ("Thrasios, Triton Hero", "Tymna the Weaver"), "partner pair",
        frozenset("WUBG"),
        ("Murderous Redcap", "Manamorphose", "Gut Shot", "Jegantha, the Wellspring"),
        ("Kitchen Finks", "Deathrite Shaman", "Spellskite", "Thassa's Oracle"),
        ("Leovold, Emissary of Trest", "Prophet of Kruphix", "Nadu, Winged Wisdom"),
        ("Rhystic Study", "Smothering Tithe", "Cyclonic Rift", "Demonic Tutor")),
    Row("wort", ("Wort, the Raidmother",), "hybrid cost", frozenset("RG"),
        ("Kitchen Finks", "Figure of Destiny", "Murderous Redcap", "Plains", "Brain Freeze"),
        ("Boggart Ram-Gang", "Manamorphose", "Birthing Pod", "Gut Shot"),
        ("Dockside Extortionist", "Primeval Titan", "Rofellos, Llanowar Emissary"),
        ("Jeska's Will", "Gamble", "Natural Order", "Gaea's Cradle")),
    Row("alesha", ("Alesha, Who Smiles at Death",), "hybrid rules text", frozenset("WBR"),
        ("Kitchen Finks", "Deathrite Shaman", "Manamorphose"),
        ("Swords to Plowshares", "Figure of Destiny", "Murderous Redcap", "Syndic of Tithes"),
        ("Dockside Extortionist", "Balance", "Iona, Shield of Emeria"),
        ("Smothering Tithe", "Teferi's Protection", "Enlightened Tutor", "Necropotence")),
]}
