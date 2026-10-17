"""
Deck recommendation and card swap generation pipeline for Commander Lab.

Synthesizes deck analysis (theme, curve, cohesion, playfeel, intent alignment)
with user deck-intake goals to generate:
1. Targeted card additions emphasizing theme, unique synergies, and recent sets.
2. Candidate card cuts flagging off-theme, clunky, or high-friction cards.
3. Direct 1-to-1 card swap suggestions with concrete upgrade rationales.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from analytics.engine import (
    ARCHETYPE_PATTERNS,
    KNOWN_RAMP_CARDS,
    KNOWN_STAPLE_CMC,
    KNOWN_STAPLE_SALT,
    DeckAnalysisReport,
)
from analytics.models import (
    CardCutRecommendation,
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckCardEntry,
    DecklistInput,
)
from user_intent import UserDeckIntent

logger = logging.getLogger(__name__)

# Recent sets (2022-2025) emphasizing fresh mechanics and modern designs
RECENT_SETS: Dict[str, Dict[str, Any]] = {
    "FDN": {"name": "Foundations", "year": 2024},
    "DSK": {"name": "Duskmourn: House of Horror", "year": 2024},
    "BLB": {"name": "Bloomburrow", "year": 2024},
    "MH3": {"name": "Modern Horizons 3", "year": 2024},
    "OTJ": {"name": "Outlaws of Thunder Junction", "year": 2024},
    "MKM": {"name": "Murders at Karlov Manor", "year": 2024},
    "LCI": {"name": "The Lost Caverns of Ixalan", "year": 2023},
    "WOE": {"name": "Wilds of Eldraine", "year": 2023},
    "CMM": {"name": "Commander Masters", "year": 2023},
    "LTR": {"name": "The Lord of the Rings: Tales of Middle-earth", "year": 2023},
    "MOM": {"name": "March of the Machine", "year": 2023},
    "ONE": {"name": "Phyrexia: All Will Be One", "year": 2023},
    "BRO": {"name": "The Brothers' War", "year": 2022},
    "DMU": {"name": "Dominaria United", "year": 2022},
    "SNC": {"name": "Streets of New Capenna", "year": 2022},
    "NEO": {"name": "Kamigawa: Neon Dynasty", "year": 2022},
}


# =============================================================================
# Domain Models: Swaps and Recommendation Collections
# =============================================================================

class CardSwapSuggestion(BaseModel):
    """
    Direct 1-to-1 card swap suggestion pairing a recommended addition with a candidate cut.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_in": {"card_name": "Innkeeper's Talent", "score": 0.92},
                "card_out": {"card_name": "Diabolic Tutor", "synergy": -0.2},
                "swap_rationale": "Replaces a high-mana generic tutor with a recursive counters engine from Bloomburrow.",
                "category": "Theme Synergy",
                "net_cmc_change": -2.0,
                "synergy_gain": 0.85,
                "theme_fit": "counters",
            }
        }
    )

    card_in: CardRecommendation = Field(..., description="Recommended card to add")
    card_out: CardCutRecommendation = Field(..., description="Recommended card to remove")
    swap_rationale: str = Field(..., description="Explanation of why this swap improves the deck")
    category: str = Field(
        "Theme Synergy",
        description="Category: 'Theme Synergy', 'Recent Set Upgrade', 'Curve Optimization', 'Playfeel Balancing', 'Wincon Alignment'",
    )
    net_cmc_change: Optional[float] = Field(None, description="Change in CMC (card_in - card_out)")
    synergy_gain: Optional[float] = Field(None, description="Estimated improvement in synergy score")
    theme_fit: Optional[str] = Field(None, description="Primary theme or archetype reinforced")
    tags: List[str] = Field(default_factory=list, description="Categorization tags")


class RecommendationSet(BaseModel):
    """
    Consolidated collection of additions, cuts, and direct swap suggestions.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    additions: List[CardRecommendation] = Field(
        default_factory=list, description="Ranked list of card additions"
    )
    cuts: List[CardCutRecommendation] = Field(
        default_factory=list, description="Ranked list of suggested cuts"
    )
    swaps: List[CardSwapSuggestion] = Field(
        default_factory=list, description="Paired 1-to-1 card swap suggestions"
    )
    recent_set_cards: List[CardRecommendation] = Field(
        default_factory=list, description="Recommendations from recent sets (2022+)"
    )
    unique_synergies: List[CardRecommendation] = Field(
        default_factory=list, description="High-synergy, non-generic cards matching deck identity"
    )
    intent_aligned_upgrades: List[CardRecommendation] = Field(
        default_factory=list, description="Cards directly advancing user's stated intake goals"
    )
    category_counts: Dict[str, int] = Field(
        default_factory=dict, description="Count of recommendations per category"
    )
    summary: str = Field("", description="Executive summary of recommendation rationale")


# =============================================================================
# Curated Knowledge Base: Archetypes, Recent Sets, and Unique Synergies
# =============================================================================

@dataclass
class CuratedCard:
    name: str
    cmc: float
    types: List[str]
    archetypes: List[str]
    keywords: List[str]
    description: str
    set_code: Optional[str] = None
    release_year: Optional[int] = None
    is_recent: bool = False
    is_unique_synergy: bool = True
    base_salt: float = 1.0
    approx_price_usd: float = 2.0
    wincon_types: Optional[List[str]] = None

    def __post_init__(self):
        if self.wincon_types is None:
            self.wincon_types = []
        if self.set_code and self.set_code in RECENT_SETS:
            self.is_recent = True
            if not self.release_year:
                self.release_year = RECENT_SETS[self.set_code]["year"]


# Comprehensive database of archetype enablers, payoffs, recent set gems, and unique synergies
CURATED_CARD_CATALOG: List[CuratedCard] = [
    # -------------------------------------------------------------------------
    # Counters (+1/+1, Proliferate, Modular, Evolve)
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Innkeeper's Talent",
        cmc=2.0,
        types=["Enchantment"],
        archetypes=["counters", "midrange"],
        keywords=["counters", "+1/+1", "ward", "double"],
        description="Distributes +1/+1 counters each combat and Class levels up to double all counter placements.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=15.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Bristly Bill, Spine Sower",
        cmc=2.0,
        types=["Creature"],
        archetypes=["counters", "ramp"],
        keywords=["landfall", "+1/+1", "counters", "double"],
        description="Places +1/+1 counters on landfall and can double all +1/+1 counters on your team.",
        set_code="OTJ",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=22.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Evolution Sage",
        cmc=3.0,
        types=["Creature"],
        archetypes=["counters", "proliferate"],
        keywords=["landfall", "proliferate", "counters"],
        description="Proliferates whenever a land enters under your control, multiplying all counter types.",
        set_code="WAR",
        release_year=2019,
        is_unique_synergy=True,
        approx_price_usd=1.5,
    ),
    CuratedCard(
        name="The Ozolith",
        cmc=1.0,
        types=["Artifact"],
        archetypes=["counters"],
        keywords=["counters", "+1/+1", "modular", "retain"],
        description="Stores all counters from dying creatures and moves them onto your combatants each turn.",
        set_code="IKO",
        release_year=2020,
        is_unique_synergy=True,
        approx_price_usd=12.0,
    ),
    CuratedCard(
        name="Ozolith, the Shattered Spire",
        cmc=2.0,
        types=["Artifact"],
        archetypes=["counters"],
        keywords=["counters", "+1/+1", "hardened scales", "incubate"],
        description="Increases +1/+1 counter placement by one and can actively put counters on demand.",
        set_code="MOM",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=4.0,
    ),
    CuratedCard(
        name="Kami of Whispered Hopes",
        cmc=3.0,
        types=["Creature"],
        archetypes=["counters", "ramp"],
        keywords=["counters", "+1/+1", "mana dork", "amplifier"],
        description="Amplifies +1/+1 counters while tapping for mana equal to its power.",
        set_code="MOM",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=2.5,
    ),
    CuratedCard(
        name="Tarrian's Soulcleaver",
        cmc=1.0,
        types=["Artifact"],
        archetypes=["counters", "voltron", "aristocrats"],
        keywords=["equipment", "+1/+1", "counters", "vigilance"],
        description="Grows equipped creature with +1/+1 counters whenever any creature or artifact goes to graveyard.",
        set_code="LCI",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=3.5,
        wincon_types=["commander_damage", "combat"],
    ),
    CuratedCard(
        name="Hardened Scales",
        cmc=1.0,
        types=["Enchantment"],
        archetypes=["counters"],
        keywords=["counters", "+1/+1", "enabler"],
        description="Premier 1-mana efficiency enabler that adds extra +1/+1 counters on every trigger.",
        set_code="KTK",
        release_year=2014,
        is_unique_synergy=True,
        approx_price_usd=2.0,
    ),
    CuratedCard(
        name="Conclave Mentor",
        cmc=2.0,
        types=["Creature"],
        archetypes=["counters"],
        keywords=["counters", "+1/+1", "lifegain"],
        description="Doubles counter value on a 2-drop body and gains life when dying.",
        set_code="M21",
        release_year=2020,
        is_unique_synergy=True,
        approx_price_usd=0.5,
    ),
    CuratedCard(
        name="Tribute to the World Tree",
        cmc=3.0,
        types=["Enchantment"],
        archetypes=["counters", "midrange"],
        keywords=["draw", "counters", "+1/+1", "engine"],
        description="Draws cards on large creature drops and buffers smaller creatures with +1/+1 counters.",
        set_code="MOM",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=9.0,
    ),
    CuratedCard(
        name="Agatha's Soul Cauldron",
        cmc=2.0,
        types=["Artifact"],
        archetypes=["counters", "combo"],
        keywords=["counters", "+1/+1", "activated abilities", "graveyard exile"],
        description="Exiles creatures to bestow their activated abilities onto all creatures you control with counters.",
        set_code="WOE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=40.0,
        wincon_types=["combo"],
    ),
    CuratedCard(
        name="Norn's Choirmaster",
        cmc=5.0,
        types=["Creature"],
        archetypes=["counters", "proliferate"],
        keywords=["proliferate", "commander", "flying"],
        description="Proliferates twice whenever your commander enters or attacks.",
        set_code="ONE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=4.0,
    ),

    # -------------------------------------------------------------------------
    # Tokens & Go-Wide
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Caretaker's Talent",
        cmc=3.0,
        types=["Enchantment"],
        archetypes=["tokens", "midrange"],
        keywords=["token", "draw", "copy", "buff"],
        description="Draws a card each turn you make a token, copies tokens, and pumps your token army.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=14.0,
    ),
    CuratedCard(
        name="Warren Warleader",
        cmc=4.0,
        types=["Creature"],
        archetypes=["tokens", "aggro"],
        keywords=["token", "offspring", "anthem", "card advantage"],
        description="Modal attacker creating tokens, buffing the team, or digging for card advantage with offspring.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=8.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Ocelot Pride",
        cmc=1.0,
        types=["Creature"],
        archetypes=["tokens", "lifegain", "aggro"],
        keywords=["token", "city's blessing", "duplicate", "first strike"],
        description="Hyper-efficient 1-drop that duplicates every token created this turn once you have the blessing.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=38.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Mondrak, Glory Dominus",
        cmc=4.0,
        types=["Creature"],
        archetypes=["tokens"],
        keywords=["token", "double", "indestructible"],
        description="Doubles token creation on an indestructible, highly resilient legendary body.",
        set_code="ONE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=28.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Warleader's Call",
        cmc=3.0,
        types=["Enchantment"],
        archetypes=["tokens", "aggro"],
        keywords=["anthem", "burn", "impact tremors", "token"],
        description="Combines an anthem buff with Impact Tremors burn on every creature entry.",
        set_code="MKM",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=7.0,
        wincon_types=["burn", "combat"],
    ),
    CuratedCard(
        name="Ojer Taq, Deepest Foundation",
        cmc=6.0,
        types=["Creature"],
        archetypes=["tokens"],
        keywords=["token", "triple", "recursion"],
        description="Triples all creature tokens created and flips into a land upon death.",
        set_code="LCI",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=25.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Moonshaker Cavalry",
        cmc=8.0,
        types=["Creature"],
        archetypes=["tokens", "aggro"],
        keywords=["craterhoof", "flying", "anthem", "overrun"],
        description="White Craterhoof Behemoth granting flying and massive power to end games on the spot.",
        set_code="WOE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=18.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Enduring Innocence",
        cmc=3.0,
        types=["Enchantment", "Creature"],
        archetypes=["tokens", "midrange"],
        keywords=["token", "draw", "weenie", "recursion"],
        description="Draws cards on small creature/token entries and returns as an enchantment upon death.",
        set_code="DSK",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=6.0,
    ),

    # -------------------------------------------------------------------------
    # Aristocrats & Sacrifice
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Marionette Apprentice",
        cmc=2.0,
        types=["Creature"],
        archetypes=["aristocrats", "tokens"],
        keywords=["sacrifice", "dies", "drain", "fabricate"],
        description="Drains opponents whenever a creature OR artifact you control dies, coming with a token.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=3.0,
        wincon_types=["attrition"],
    ),
    CuratedCard(
        name="Rottenmouth Viper",
        cmc=6.0,
        types=["Creature"],
        archetypes=["aristocrats", "midrange"],
        keywords=["sacrifice", "drain", "discard", "cost reduction"],
        description="Cost-reduced by sacrificing nonland permanents; compounds card disadvantage and drain each turn.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=10.0,
        wincon_types=["attrition"],
    ),
    CuratedCard(
        name="Vein Ripper",
        cmc=6.0,
        types=["Creature"],
        archetypes=["aristocrats", "control"],
        keywords=["drain", "sacrifice", "ward", "flying"],
        description="Devastating 2-damage drain whenever any creature dies, backed by heavy ward sacrifice protection.",
        set_code="MKM",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=16.0,
        wincon_types=["attrition"],
    ),
    CuratedCard(
        name="Bloodletter of Aclazotz",
        cmc=4.0,
        types=["Creature"],
        archetypes=["aristocrats", "aggro"],
        keywords=["life loss", "double", "flying", "demon"],
        description="Doubles all life loss opponents suffer on your turn, supercharging aristocrats drains and combat.",
        set_code="LCI",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=18.0,
        wincon_types=["attrition", "burn"],
    ),
    CuratedCard(
        name="Drivnod, Carnage Dominus",
        cmc=5.0,
        types=["Creature"],
        archetypes=["aristocrats"],
        keywords=["death trigger", "double", "indestructible"],
        description="Doubles all death triggers from your creatures, doubling drain, draw, and token generation.",
        set_code="ONE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=11.0,
        wincon_types=["attrition"],
    ),
    CuratedCard(
        name="Pitiless Plunderer",
        cmc=4.0,
        types=["Creature"],
        archetypes=["aristocrats", "ramp"],
        keywords=["treasure", "sacrifice", "dies", "mana"],
        description="Generates Treasure tokens on every creature death, fueling infinite combos and explosive turns.",
        set_code="RIX",
        release_year=2018,
        is_unique_synergy=True,
        approx_price_usd=6.0,
        wincon_types=["combo"],
    ),
    CuratedCard(
        name="Blood Artist",
        cmc=2.0,
        types=["Creature"],
        archetypes=["aristocrats"],
        keywords=["drain", "dies", "life gain"],
        description="Classic staple drain piece that triggers on any creature dying anywhere at the table.",
        set_code="EMA",
        release_year=2016,
        is_unique_synergy=False,
        approx_price_usd=2.5,
        wincon_types=["attrition"],
    ),
    CuratedCard(
        name="Zulaport Cutthroat",
        cmc=2.0,
        types=["Creature"],
        archetypes=["aristocrats"],
        keywords=["drain", "dies", "each opponent"],
        description="Drains each opponent simultaneously on your creature deaths, closing games fast.",
        set_code="BFZ",
        release_year=2015,
        is_unique_synergy=False,
        approx_price_usd=1.5,
        wincon_types=["attrition"],
    ),

    # -------------------------------------------------------------------------
    # Spellslinger & Storm
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Bria, Riptide Rogue",
        cmc=4.0,
        types=["Creature"],
        archetypes=["spellslinger", "tokens"],
        keywords=["prowess", "unblockable", "otter"],
        description="Grants prowess to all your creatures and renders an attacker unblockable on every noncreature cast.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=12.0,
        wincon_types=["combat"],
    ),
    CuratedCard(
        name="Valley Floodcaller",
        cmc=3.0,
        types=["Creature"],
        archetypes=["spellslinger", "control"],
        keywords=["flash", "untap", "noncreature spells"],
        description="Allows casting noncreature spells as though they had flash and untaps kindred on cast.",
        set_code="BLB",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=5.0,
    ),
    CuratedCard(
        name="Stella Lee, Wild Card",
        cmc=3.0,
        types=["Creature"],
        archetypes=["spellslinger", "combo"],
        keywords=["copy", "instant", "sorcery", "storm"],
        description="Exiles cards for advantage and copies your third spell each turn, enabling instant-win lines.",
        set_code="OTJ",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=4.0,
        wincon_types=["combo"],
    ),
    CuratedCard(
        name="Third Path Iconoclast",
        cmc=2.0,
        types=["Creature"],
        archetypes=["spellslinger", "tokens", "artifacts"],
        keywords=["token", "noncreature", "artifact soldier"],
        description="Generates an artifact creature token on EVERY noncreature spell, outclassing Young Pyromancer.",
        set_code="BRO",
        release_year=2022,
        is_unique_synergy=True,
        approx_price_usd=1.0,
    ),
    CuratedCard(
        name="Archmage Emeritus",
        cmc=4.0,
        types=["Creature"],
        archetypes=["spellslinger"],
        keywords=["magecraft", "draw", "instant", "sorcery"],
        description="Premier magecraft draw engine drawing a card on every cast or copy of an instant or sorcery.",
        set_code="STX",
        release_year=2021,
        is_unique_synergy=True,
        approx_price_usd=3.5,
    ),
    CuratedCard(
        name="Flare of Duplication",
        cmc=3.0,
        types=["Instant"],
        archetypes=["spellslinger"],
        keywords=["free spell", "copy", "sacrifice nontoken"],
        description="Free spell-copying reaction spell cast by sacrificing a nontoken red creature.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=4.0,
    ),

    # -------------------------------------------------------------------------
    # Graveyard, Reanimation, and Dredge
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Six",
        cmc=3.0,
        types=["Creature"],
        archetypes=["graveyard", "ramp"],
        keywords=["retrace", "mill", "lands", "recursion"],
        description="Mills lands for ramp on attack and grants retrace to all nonland permanents in your graveyard.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=8.0,
    ),
    CuratedCard(
        name="Wight of the Reliquary",
        cmc=2.0,
        types=["Creature"],
        archetypes=["graveyard", "aristocrats", "ramp"],
        keywords=["sacrifice", "tutor land", "graveyard size"],
        description="Grows from creatures in all graveyards and sacrifices creatures to tutor any land directly into play.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=3.5,
    ),
    CuratedCard(
        name="Conduit of Worlds",
        cmc=4.0,
        types=["Artifact"],
        archetypes=["graveyard", "ramp"],
        keywords=["crucible of worlds", "play lands from graveyard", "recursion"],
        description="Plays lands from your graveyard and casts nonland permanents from graveyard once per turn.",
        set_code="ONE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=7.0,
    ),
    CuratedCard(
        name="Overlord of the Balemurk",
        cmc=5.0,
        types=["Enchantment", "Creature"],
        archetypes=["graveyard", "midrange"],
        keywords=["impending", "mill", "recursion", "creature"],
        description="Mills 4 and recurs a creature or planeswalker immediately via cheap impending cost.",
        set_code="DSK",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=12.0,
    ),
    CuratedCard(
        name="Virtue of Persistence",
        cmc=7.0,
        types=["Enchantment"],
        archetypes=["graveyard", "control"],
        keywords=["adventure", "removal", "reanimate each upkeep"],
        description="Early 2-mana removal adventure that later reanimates a creature from ANY graveyard each upkeep.",
        set_code="WOE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=13.0,
    ),

    # -------------------------------------------------------------------------
    # Value, Ramp & Smooth Pacing
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Overlord of the Hauntwoods",
        cmc=5.0,
        types=["Enchantment", "Creature"],
        archetypes=["ramp", "tokens", "midrange"],
        keywords=["everywhere land", "impending", "domain", "mana fixing"],
        description="Creates an 'Everywhere' land token on entry/attack, fixing all 5 colors with cheap impending.",
        set_code="DSK",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=19.0,
    ),
    CuratedCard(
        name="Birthing Ritual",
        cmc=2.0,
        types=["Enchantment"],
        archetypes=["midrange", "aristocrats"],
        keywords=["pod", "birthing pod", "end step", "creature tutor"],
        description="End-step mini-Birthing Pod that upgrades your lowest value creature into gas straight from library.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=11.0,
    ),
    CuratedCard(
        name="Up the Beanstalk",
        cmc=2.0,
        types=["Enchantment"],
        archetypes=["ramp", "control", "midrange"],
        keywords=["draw", "5 cmc", "cantrip", "delve"],
        description="Cantrips on entry and draws a card on every 5+ CMC spell (including cost-reduced spells).",
        set_code="WOE",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=2.5,
    ),
    CuratedCard(
        name="Flare of Cultivation",
        cmc=3.0,
        types=["Sorcery"],
        archetypes=["ramp"],
        keywords=["free ramp", "cultivate", "sacrifice creature"],
        description="Free Cultivate cast by sacrificing a nontoken green creature, accelerating turns early.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=2.0,
    ),
    CuratedCard(
        name="Delighted Halfling",
        cmc=1.0,
        types=["Creature"],
        archetypes=["ramp"],
        keywords=["mana dork", "uncounterable", "legendary"],
        description="Premier 1-mana dork making your commander and all legendary spells completely uncounterable.",
        set_code="LTR",
        release_year=2023,
        is_unique_synergy=True,
        approx_price_usd=14.0,
    ),

    # -------------------------------------------------------------------------
    # Interaction, Protection & Utility
    # -------------------------------------------------------------------------
    CuratedCard(
        name="Delney, Streetwise Lookout",
        cmc=3.0,
        types=["Creature"],
        archetypes=["tokens", "aristocrats", "counters"],
        keywords=["double trigger", "power 2 or less", "evasion"],
        description="Doubles all triggered abilities of creatures with power 2 or less and grants them combat evasion.",
        set_code="MKM",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=18.0,
    ),
    CuratedCard(
        name="Trouble in Pairs",
        cmc=4.0,
        types=["Enchantment"],
        archetypes=["control", "midrange"],
        keywords=["card draw", "no extra turns", "punishment"],
        description="Draws cards whenever opponents attack, draw extra cards, or cast multiple spells; shuts off extra turns.",
        set_code="MKM",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=22.0,
    ),
    CuratedCard(
        name="Flare of Denial",
        cmc=3.0,
        types=["Instant"],
        archetypes=["control", "spellslinger"],
        keywords=["free counterspell", "sacrifice nontoken blue"],
        description="Free counterspell castable by sacrificing a nontoken blue creature to protect commanders.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=16.0,
    ),
    CuratedCard(
        name="Flare of Fortitude",
        cmc=4.0,
        types=["Instant"],
        archetypes=["voltron", "tokens", "aggro"],
        keywords=["free protection", "heroic intervention", "teferi's protection"],
        description="Free board protection giving all permanents hexproof and indestructible, plus life loss shield.",
        set_code="MH3",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=5.0,
    ),
    CuratedCard(
        name="Final Showdown",
        cmc=1.0,
        types=["Instant"],
        archetypes=["control"],
        keywords=["spree", "instant board wipe", "silence", "indestructible"],
        description="Instant-speed modal spree spell functioning as protection, silence, or an instant-speed board wipe.",
        set_code="OTJ",
        release_year=2024,
        is_unique_synergy=True,
        approx_price_usd=8.0,
    ),
]


# =============================================================================
# Recommendation Generator Engine
# =============================================================================

class RecommendationGenerator:
    """
    Synthesizes deck analysis, card metrics, and user intent to produce
    prioritized card additions, candidate cuts, and direct 1-to-1 swaps.
    """

    def __init__(
        self,
        catalog: Optional[List[CuratedCard]] = None,
        max_recommendations: int = 15,
        max_cuts: int = 10,
        max_swaps: int = 8,
        recent_bonus: float = 0.25,
    ):
        self.catalog = list(catalog or CURATED_CARD_CATALOG)
        self.max_recommendations = max_recommendations
        self.max_cuts = max_cuts
        self.max_swaps = max_swaps
        self.recent_bonus = recent_bonus

    def generate(
        self,
        deck: Union[DecklistInput, Any],
        analysis: DeckAnalysisReport,
        intent: Optional[UserDeckIntent] = None,
        external_recommendations: Optional[CardRecommendations] = None,
    ) -> RecommendationSet:
        """
        Generate a comprehensive set of additions, cuts, and 1-to-1 card swaps.
        """
        # 1. Extract deck card names & commanders
        deck_cards: List[DeckCardEntry] = getattr(deck, "cards", [])
        commanders: List[str] = [c for c in analysis.commanders]
        existing_names: Set[str] = {c.name.strip().lower() for c in deck_cards}
        for cmd in commanders:
            existing_names.add(cmd.strip().lower())

        intent = intent or UserDeckIntent()

        # 2. Extract context from analysis & intent
        primary_archetype = analysis.theme_analysis.primary_archetype.lower()
        secondary_archetypes = [
            a.archetype.lower() if hasattr(a, "archetype") else str(a).lower()
            for a in analysis.theme_analysis.secondary_archetypes
        ]
        preferred_archetypes = [
            intent.archetype_preferences.primary_archetype.lower()
        ] + [a.lower() for a in intent.archetype_preferences.secondary_archetypes]
        excluded_archetypes = {a.lower() for a in intent.archetype_preferences.excluded_archetypes}
        excluded_wincons = {w.lower() for w in intent.preferred_win_conditions.disliked_or_excluded}
        preferred_wincons = {intent.preferred_win_conditions.primary.lower()} | {
            w.lower() for w in intent.preferred_win_conditions.secondary
        }
        power_scale = intent.target_power_level.scale
        power_tier = intent.target_power_level.tier.lower()
        deck_vision_intent = intent.deck_vision.intent.lower()  # tune_up, new_build, overhaul
        max_card_price = intent.budget_constraints.max_card_price

        # 3. Score and curate candidate additions
        additions = self._evaluate_candidate_additions(
            existing_names=existing_names,
            primary_archetype=primary_archetype,
            secondary_archetypes=secondary_archetypes,
            preferred_archetypes=preferred_archetypes,
            excluded_archetypes=excluded_archetypes,
            preferred_wincons=preferred_wincons,
            excluded_wincons=excluded_wincons,
            power_scale=power_scale,
            power_tier=power_tier,
            deck_vision_intent=deck_vision_intent,
            max_card_price=max_card_price,
            external_recommendations=external_recommendations,
            analysis=analysis,
        )

        # 4. Score and curate candidate cuts
        cuts = self._evaluate_candidate_cuts(
            deck_cards=deck_cards,
            commanders=commanders,
            primary_archetype=primary_archetype,
            excluded_archetypes=excluded_archetypes,
            excluded_wincons=excluded_wincons,
            power_scale=power_scale,
            power_tier=power_tier,
            deck_vision_intent=deck_vision_intent,
            analysis=analysis,
        )

        # 5. Formulate direct 1-to-1 card swaps
        swaps = self._formulate_card_swaps(
            additions=additions,
            cuts=cuts,
            primary_archetype=primary_archetype,
            deck_vision_intent=deck_vision_intent,
            analysis=analysis,
        )

        # 6. Categorize subsets for report presentation
        recent_set_cards = [
            card for card in additions
            if any(cat.startswith("[") or "Recent Set" in cat for cat in card.categories)
        ]
        unique_synergies = [
            card for card in additions
            if "Unique Synergy" in card.categories or (card.synergy and card.synergy > 0.6)
        ]
        intent_aligned = [
            card for card in additions
            if "Intent Alignment" in card.categories or "Vision Focus" in card.categories
        ]

        category_counts: Dict[str, int] = {}
        for card in additions:
            for cat in card.categories:
                category_counts[cat] = category_counts.get(cat, 0) + 1

        summary = self._synthesize_recommendation_summary(
            additions=additions,
            cuts=cuts,
            swaps=swaps,
            intent=intent,
            analysis=analysis,
        )

        return RecommendationSet(
            additions=additions[:self.max_recommendations],
            cuts=cuts[:self.max_cuts],
            swaps=swaps[:self.max_swaps],
            recent_set_cards=recent_set_cards[:6],
            unique_synergies=unique_synergies[:6],
            intent_aligned_upgrades=intent_aligned[:6],
            category_counts=category_counts,
            summary=summary,
        )

    # -------------------------------------------------------------------------
    # Helper: Evaluate Candidate Additions
    # -------------------------------------------------------------------------

    def _evaluate_candidate_additions(
        self,
        existing_names: Set[str],
        primary_archetype: str,
        secondary_archetypes: List[str],
        preferred_archetypes: List[str],
        excluded_archetypes: Set[str],
        preferred_wincons: Set[str],
        excluded_wincons: Set[str],
        power_scale: int,
        power_tier: str,
        deck_vision_intent: str,
        max_card_price: Optional[float],
        external_recommendations: Optional[CardRecommendations],
        analysis: DeckAnalysisReport,
    ) -> List[CardRecommendation]:
        scored_cards: List[Tuple[float, CardRecommendation]] = []

        # A. Incorporate external provider recommendations if provided
        if external_recommendations and external_recommendations.items:
            for ext in external_recommendations.items:
                if ext.card_name.strip().lower() in existing_names:
                    continue
                score = min(1.0, (ext.score or 0.85) + 0.10)
                synergy = ext.synergy or 0.5
                reason = ext.reason or f"Synergistic addition recommended by external provider data."
                cats = list(ext.categories or ["External Analytics"])
                rec = CardRecommendation(
                    card_name=ext.card_name,
                    oracle_id=ext.oracle_id,
                    score=round(score, 2),
                    synergy=round(synergy, 2),
                    inclusion_rate=ext.inclusion_rate,
                    reason=reason,
                    categories=cats,
                    sources=list(ext.sources or ["external"]),
                )
                scored_cards.append((score, rec))

        # B. Evaluate internal curated knowledge base
        for card in self.catalog:
            if card.name.strip().lower() in existing_names:
                continue

            # Respect budget limit if defined
            if max_card_price is not None and card.approx_price_usd > max_card_price:
                continue

            # Check archetype exclusions
            card_archetypes = {a.lower() for a in card.archetypes}
            if card_archetypes and card_archetypes.issubset(excluded_archetypes):
                continue

            # Check wincon exclusions
            if card.wincon_types and any(w in excluded_wincons for w in card.wincon_types):
                continue

            # Calculate base alignment score
            base_score = 0.50
            synergy_score = 0.40
            reasons: List[str] = []
            categories: List[str] = []

            # 1. Archetype and Theme alignment
            is_primary_theme = primary_archetype in card_archetypes
            is_secondary_theme = any(a in card_archetypes for a in secondary_archetypes)
            is_preferred_theme = any(a in card_archetypes for a in preferred_archetypes)

            if is_primary_theme:
                base_score += 0.25
                synergy_score += 0.35
                reasons.append(f"Direct enabler for primary '{primary_archetype}' theme ({card.description})")
                categories.append("Theme Synergy")
            elif is_secondary_theme or is_preferred_theme:
                base_score += 0.15
                synergy_score += 0.20
                matched_theme = next((a for a in card_archetypes if a in preferred_archetypes or a in secondary_archetypes), "deck archetype")
                reasons.append(f"Supports secondary '{matched_theme}' archetype")
                categories.append("Archetype Support")

            # 2. Recent Sets Bonus (2022+)
            if card.is_recent and card.set_code:
                base_score += self.recent_bonus
                set_info = RECENT_SETS.get(card.set_code, {})
                set_name = set_info.get("name", card.set_code)
                set_year = set_info.get("year", card.release_year)
                reasons.append(f"[{card.set_code} {set_year}] Recent set release from {set_name}")
                categories.append(f"[{card.set_code}] Recent Set")

            # 3. Unique Synergy & Non-generic design
            if card.is_unique_synergy:
                base_score += 0.10
                synergy_score += 0.15
                categories.append("Unique Synergy")

            # 4. User Intent Goals Alignment
            if deck_vision_intent == "tune_up":
                # Tune-up prioritizes lowering curve and tightening synergy
                if card.cmc <= 3.0:
                    base_score += 0.10
                    reasons.append("Tuning pick: low mana value enhances deck velocity")
                    categories.append("Curve Optimization")
            elif deck_vision_intent == "new_build":
                # New build prioritizes foundational enablers and core payoffs
                base_score += 0.08
                reasons.append("Foundational engine piece for initial deck construction")
                categories.append("Vision Focus")
            elif deck_vision_intent == "overhaul":
                # Overhaul prioritizes strong thematic pivot cards
                if is_primary_theme or is_preferred_theme:
                    base_score += 0.15
                    reasons.append("Key pivot card driving the deck overhaul")
                    categories.append("Overhaul Engine")

            # 5. Win condition alignment
            if card.wincon_types and any(w in preferred_wincons for w in card.wincon_types):
                matched_win = next(w for w in card.wincon_types if w in preferred_wincons)
                base_score += 0.12
                reasons.append(f"Directly advances preferred '{matched_win}' win condition")
                categories.append("Wincon Enabler")

            # 6. Power tier modulation
            if power_scale >= 8 or power_tier in ("optimized", "competitive"):
                if card.cmc <= 2.0 or "free" in card.keywords:
                    base_score += 0.10
                    reasons.append(f"High efficiency suited for {power_tier} power tier")
            elif power_scale <= 5 or power_tier == "casual":
                # In casual, penalize high salt cards
                if card.base_salt > 2.5:
                    base_score -= 0.30

            # 7. Check cohesion gaps from analysis report
            if analysis.curve_analysis.average_cmc > 3.6 and card.cmc <= 2.0:
                base_score += 0.08
                reasons.append("Smooths curve to address elevated average CMC")
            total_cards_count = max(1, analysis.curve_analysis.total_spells + analysis.curve_analysis.total_lands)
            ramp_density = analysis.curve_analysis.total_ramp / total_cards_count
            if ramp_density < 0.10 and ("ramp" in card_archetypes or "mana" in card.keywords):
                base_score += 0.10
                reasons.append("Increases ramp density to cure early-game mana bottleneck")
                categories.append("Mana & Ramp")

            final_score = min(0.99, max(0.10, base_score))
            final_synergy = min(1.0, max(-1.0, synergy_score))

            if not reasons:
                reasons.append(card.description)

            compiled_reason = "; ".join(reasons)
            rec_model = CardRecommendation(
                card_name=card.name,
                oracle_id=None,
                score=round(final_score, 2),
                synergy=round(final_synergy, 2),
                inclusion_rate=round(min(0.95, final_score * 0.9), 2),
                reason=compiled_reason,
                categories=list(dict.fromkeys(categories)),
                sources=["curated_knowledge_base"],
            )
            scored_cards.append((final_score, rec_model))

        # Sort by score descending and deduplicate by card name
        scored_cards.sort(key=lambda x: x[0], reverse=True)
        unique_recs: List[CardRecommendation] = []
        seen: Set[str] = set()
        for _, rec in scored_cards:
            k = rec.card_name.lower().strip()
            if k not in seen:
                seen.add(k)
                unique_recs.append(rec)

        return unique_recs

    # -------------------------------------------------------------------------
    # Helper: Evaluate Candidate Cuts
    # -------------------------------------------------------------------------

    def _evaluate_candidate_cuts(
        self,
        deck_cards: List[DeckCardEntry],
        commanders: List[str],
        primary_archetype: str,
        excluded_archetypes: Set[str],
        excluded_wincons: Set[str],
        power_scale: int,
        power_tier: str,
        deck_vision_intent: str,
        analysis: DeckAnalysisReport,
    ) -> List[CardCutRecommendation]:
        scored_cuts: List[Tuple[float, CardCutRecommendation]] = []
        commander_names = {c.strip().lower() for c in commanders}

        # Cards known to be generic, high-cost, or common off-theme fillers
        GENERIC_OUTCLASSED = {
            "Diabolic Tutor": ("High mana cost (4 CMC) sorcery tutor outclassed by efficient card draw or theme enablers", 4.0),
            "Cancel": ("Strictly outclassed 3 CMC counterspell with no additional upside", 3.0),
            "Murder": ("Inefficient 3 CMC single-target removal outpaced by 1-2 CMC versatile answers", 3.0),
            "Manalith": ("3 CMC mana rock that adds no functional utility", 3.0),
            "Divination": ("3 CMC sorcery drawing only 2 cards without thematic synergy", 3.0),
            "Mind Rot": ("Low-impact discard spell creating negligible card advantage in 4-player pods", 3.0),
            "Pacifism": ("Fragile creature-only aura outclassed by instant removal or versatile wipes", 2.0),
            "Naturalize": ("Narrow 2 CMC artifact/enchantment removal outclassed by modal options like Nature's Claim", 2.0),
            "Disenchant": ("Narrow 2 CMC removal outclassed by modal white removal like Stroke of Midnight", 2.0),
            "Elixir of Immortality": ("Low-impact life gain and shuffle effect consuming card equity", 1.0),
        }

        high_salt_cards = {c.card_name for c in analysis.playfeel_analysis.high_salt_cards}

        for card in deck_cards:
            name = card.name.strip()
            name_lower = name.lower()

            # Never cut commanders
            if name_lower in commander_names:
                continue

            cut_score = 0.20
            reasons: List[str] = []
            sources: List[str] = []

            # 1. Outclassed or generic filler cards
            if name in GENERIC_OUTCLASSED:
                desc, _ = GENERIC_OUTCLASSED[name]
                cut_score += 0.50
                reasons.append(desc)
                sources.append("efficiency_analysis")

            # 2. High-salt cards in casual or focused decks
            if name in high_salt_cards and (power_scale <= 6 or power_tier in ("casual", "focused")):
                cut_score += 0.45
                reasons.append(f"High salt rating creates pod friction in a {power_tier} deck")
                sources.append("playfeel_analysis")

            # 3. Curve bottlenecks: 5+ CMC cards in high-curve decks
            cmc = KNOWN_STAPLE_CMC.get(name)
            if cmc is not None and cmc >= 5.0 and analysis.curve_analysis.average_cmc > 3.5:
                cut_score += 0.25
                reasons.append(f"Heavy mana cost ({int(cmc)} CMC) exacerbates high average curve ({analysis.curve_analysis.average_cmc:.2f})")
                sources.append("curve_bottleneck")

            # 4. Off-theme check: Card does not fit primary archetype patterns
            pattern_info = ARCHETYPE_PATTERNS.get(primary_archetype, {})
            keywords = pattern_info.get("keywords", [])
            types = pattern_info.get("types", [])
            # If name is not a recognized ramp staple and lacks keywords
            is_ramp = name in KNOWN_RAMP_CARDS
            is_thematic = False
            for kw in keywords:
                if kw in name_lower:
                    is_thematic = True
                    break

            if not is_ramp and not is_thematic and name not in GENERIC_OUTCLASSED:
                # Modest penalty for off-theme cards
                if deck_vision_intent in ("tune_up", "overhaul"):
                    cut_score += 0.15
                    reasons.append(f"Low mechanic alignment with primary '{primary_archetype}' gameplan")
                    sources.append("theme_alignment")

            # 5. Check if card matches excluded wincons (e.g. combo pieces)
            salt_score = KNOWN_STAPLE_SALT.get(name, 0.0)
            if salt_score >= 3.0 and "combo" in excluded_wincons:
                cut_score += 0.35
                reasons.append("Card associated with combo win conditions excluded by user intent")
                sources.append("intent_conflict")

            if cut_score > 0.35:
                synergy_val = round(-1.0 * min(0.9, cut_score), 2)
                reason_str = "; ".join(reasons) if reasons else "Candidate for upgrade to higher-synergy alternative."
                cut_rec = CardCutRecommendation(
                    card_name=name,
                    oracle_id=card.oracle_id,
                    synergy=synergy_val,
                    reason=reason_str,
                    sources=sources or ["heuristics"],
                )
                scored_cuts.append((cut_score, cut_rec))

        # Sort cuts by cut_score descending
        scored_cuts.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored_cuts]

    # -------------------------------------------------------------------------
    # Helper: Formulate Direct Card Swaps
    # -------------------------------------------------------------------------

    def _formulate_card_swaps(
        self,
        additions: List[CardRecommendation],
        cuts: List[CardCutRecommendation],
        primary_archetype: str,
        deck_vision_intent: str,
        analysis: DeckAnalysisReport,
    ) -> List[CardSwapSuggestion]:
        swaps: List[CardSwapSuggestion] = []
        used_adds: Set[str] = set()
        used_cuts: Set[str] = set()

        for cut in cuts:
            if cut.card_name in used_cuts:
                continue

            cut_cmc = KNOWN_STAPLE_CMC.get(cut.card_name, 4.0)

            # Find best addition matching or upgrading this cut
            best_add: Optional[CardRecommendation] = None
            best_swap_score = -999.0
            best_category = "Theme Synergy"

            for add in additions:
                if add.card_name in used_adds:
                    continue

                add_cmc = KNOWN_STAPLE_CMC.get(add.card_name, 3.0)
                swap_score = (add.score or 0.5)

                # Prioritize recent set cards for freshness
                is_recent = any("Recent Set" in c or c.startswith("[") for c in add.categories)
                if is_recent:
                    swap_score += 0.20

                # Prioritize mana curve reduction
                cmc_diff = add_cmc - cut_cmc
                if cmc_diff < 0:
                    swap_score += 0.15

                if swap_score > best_swap_score:
                    best_swap_score = swap_score
                    best_add = add
                    if is_recent:
                        best_category = "Recent Set Upgrade"
                    elif cmc_diff <= -1.0:
                        best_category = "Curve Optimization"
                    elif "Wincon Enabler" in add.categories:
                        best_category = "Wincon Alignment"
                    elif "Playfeel" in add.categories or "salt" in (cut.reason or "").lower():
                        best_category = "Playfeel Balancing"
                    else:
                        best_category = "Theme Synergy"

            if best_add:
                used_adds.add(best_add.card_name)
                used_cuts.add(cut.card_name)

                add_cmc = KNOWN_STAPLE_CMC.get(best_add.card_name, 3.0)
                net_cmc = round(add_cmc - cut_cmc, 1)
                syn_in = best_add.synergy or 0.6
                syn_out = cut.synergy or -0.3
                net_synergy = round(syn_in - syn_out, 2)
                best_add_recent = any("Recent Set" in c or c.startswith("[") for c in best_add.categories)

                # Craft comprehensive upgrade story
                rationale_parts = [
                    f"Cut '{cut.card_name}' ({cut.reason or 'low synergy'}) in favor of '{best_add.card_name}'."
                ]
                if net_cmc < 0:
                    rationale_parts.append(f"Lowers curve by {abs(net_cmc):.1f} mana to improve operational speed.")
                elif net_cmc > 0:
                    rationale_parts.append(f"Invests +{net_cmc:.1f} mana for significantly greater payoff potential.")

                if best_add_recent:
                    recent_badge = next((c for c in best_add.categories if c.startswith("[")), "Recent set")
                    rationale_parts.append(f"Injects modern design equity from {recent_badge}.")

                rationale_parts.append(f"Aligns with {deck_vision_intent} goal by strengthening '{primary_archetype}' cohesion.")

                swap = CardSwapSuggestion(
                    card_in=best_add,
                    card_out=cut,
                    swap_rationale=" ".join(rationale_parts),
                    category=best_category,
                    net_cmc_change=net_cmc,
                    synergy_gain=net_synergy,
                    theme_fit=primary_archetype,
                    tags=[best_category, primary_archetype],
                )
                swaps.append(swap)

                if len(swaps) >= self.max_swaps:
                    break

        return swaps

    # -------------------------------------------------------------------------
    # Helper: Synthesize Summary
    # -------------------------------------------------------------------------

    def _synthesize_recommendation_summary(
        self,
        additions: List[CardRecommendation],
        cuts: List[CardCutRecommendation],
        swaps: List[CardSwapSuggestion],
        intent: UserDeckIntent,
        analysis: DeckAnalysisReport,
    ) -> str:
        intent_type = intent.deck_vision.intent
        power_desc = f"{intent.target_power_level.tier.title()} (scale {intent.target_power_level.scale}/10)"
        archetype = analysis.theme_analysis.primary_archetype.title()

        add_count = len(additions)
        swap_count = len(swaps)
        cut_count = len(cuts)

        recent_count = sum(1 for a in additions if any("[" in c for c in a.categories))

        return (
            f"Generated {add_count} card additions, {cut_count} candidate cuts, and {swap_count} direct 1-to-1 swaps "
            f"tailored for a '{intent_type}' review targeting {power_desc} in the {archetype} archetype. "
            f"Highlighting {recent_count} recent set inclusions (2022-2024) to boost deck uniqueness while smoothing mana pacing."
        )


# Global default recommendation generator instance
default_recommendation_generator = RecommendationGenerator()
