"""
Theme, cohesion, and playfeel deck analysis engine for Commander Lab.

Evaluates Magic: The Gathering Commander decks across four core dimensions:
1. Theme & Archetype consistency (mechanic alignment, tags, and excluded archetype violation detection).
2. Mana curve & speed score (average CMC, ramp density, mana pips, and operational turn pacing).
3. Cohesion & synergy rating (card synergy, engine clustering, and enabler/payoff balance).
4. Salt rating & playfeel breakdown (Commander Salt integration, salt categories, and pod friction).

Synthesizes these dimensions against user intent (vision, target power level, win conditions, and budget)
to produce structured reports suitable for humans and downstream recommendation pipelines.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from analytics.models import (
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PowerScore,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
)
from deck_intake import (
    BASIC_LAND_NAMES,
    ConsolidatedDeckIntake,
    DeckIntakeResult,
    DeckIntakeService,
)
from user_intent import (
    POWER_SCALE_TIER_MAP,
    UserDeckIntent,
    parse_user_intent,
)

# =============================================================================
# Domain Models for Analysis Engine
# =============================================================================

class ArchetypeMatch(BaseModel):
    """Alignment metric for a specific archetype or thematic tag."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "archetype": "tokens",
                "score": 82.5,
                "supporting_cards_count": 24,
                "supporting_cards": ["Young Pyromancer", "Lingering Souls"],
            }
        }
    )

    archetype: str = Field(..., description="Archetype or thematic tag name")
    score: float = Field(..., ge=0.0, le=100.0, description="Alignment score (0.0 to 100.0)")
    supporting_cards_count: int = Field(0, ge=0, description="Number of supporting cards")
    supporting_cards: List[str] = Field(default_factory=list, description="List of card names")


class ThemeAnalysisBreakdown(BaseModel):
    """Structured analysis of deck theme, mechanics, and archetype consistency."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 85.0,
                "consistency_tier": "High",
                "primary_archetype": "aristocrats",
                "primary_archetype_score": 88.0,
                "secondary_archetypes": [
                    {"archetype": "tokens", "score": 75.0, "supporting_cards_count": 18}
                ],
                "thematic_tags_matched": {"sacrifice": 22, "drain": 12},
                "off_theme_cards": ["Fog"],
                "excluded_archetype_violations": [],
                "mechanic_distribution": {"sacrifice": 22, "death_trigger": 15},
                "description": "Strong commitment to aristocrats sacrifice loops.",
            }
        }
    )

    score: float = Field(..., ge=0.0, le=100.0, description="Overall theme consistency score (0-100)")
    consistency_tier: str = Field(..., description="High, Moderate, Low, or Scattered")
    primary_archetype: str = Field(..., description="Target or detected primary archetype")
    primary_archetype_score: float = Field(..., ge=0.0, le=100.0, description="Score for primary archetype")
    secondary_archetypes: List[ArchetypeMatch] = Field(default_factory=list, description="Secondary archetype matches")
    thematic_tags_matched: Dict[str, int] = Field(default_factory=dict, description="Matched tags with card counts")
    off_theme_cards: List[str] = Field(default_factory=list, description="Cards with minimal thematic connection")
    excluded_archetype_violations: List[Dict[str, Any]] = Field(
        default_factory=list, description="Cards matching user-excluded archetypes or mechanics"
    )
    mechanic_distribution: Dict[str, int] = Field(default_factory=dict, description="Distribution of detected mechanics")
    description: str = Field("", description="Narrative thematic summary")


class CurveAnalysisBreakdown(BaseModel):
    """Evaluation of deck mana curve, pacing, ramp density, and operational speed."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 82.0,
                "speed_score": 7.5,
                "speed_tier": "Fast",
                "average_cmc": 2.85,
                "median_cmc": 3.0,
                "total_spells": 64,
                "total_lands": 35,
                "total_ramp": 11,
                "cmc_distribution": {"0": 2, "1": 12, "2": 22, "3": 15, "4": 8, "5": 3, "6+": 2},
                "color_distribution": {"W": 18, "B": 24, "G": 20},
                "estimated_operational_turn": 3,
                "target_turn_win_alignment": "Well-aligned with target turn 6 win",
                "curve_rating": "Optimal Curve",
                "recommendations": [],
            }
        }
    )

    score: float = Field(..., ge=0.0, le=100.0, description="Overall curve quality and efficiency score (0-100)")
    speed_score: float = Field(..., ge=1.0, le=10.0, description="Deck speed rating (1.0 slow to 10.0 blistering)")
    speed_tier: str = Field(..., description="Blistering, Fast, Moderate, Deliberate, or Slow")
    average_cmc: float = Field(..., ge=0.0, description="Average CMC of non-land cards")
    median_cmc: float = Field(..., ge=0.0, description="Median CMC of non-land cards")
    total_spells: int = Field(..., ge=0, description="Total non-land cards")
    total_lands: int = Field(..., ge=0, description="Total land count")
    total_ramp: int = Field(..., ge=0, description="Total mana ramp cards (rocks, dorks, spells)")
    cmc_distribution: Dict[str, int] = Field(default_factory=dict, description="Histogram of non-land CMC")
    color_distribution: Dict[str, int] = Field(default_factory=dict, description="Distribution of mana pips (W, U, B, R, G, C)")
    estimated_operational_turn: int = Field(..., ge=1, description="Estimated turn deck becomes fully operational")
    target_turn_win_alignment: Optional[str] = Field(None, description="Pacing comparison against user intent win turn")
    curve_rating: str = Field(..., description="Qualitative evaluation of the mana curve")
    recommendations: List[str] = Field(default_factory=list, description="Actionable curve and ramp recommendations")


class SynergyCluster(BaseModel):
    """Identified functional or thematic engine cluster within the deck."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Sacrifice Engine",
                "cards": ["Viscera Seer", "Blood Artist", "Reassembling Skeleton"],
                "enablers_count": 2,
                "payoffs_count": 1,
                "synergy_score": 88.0,
            }
        }
    )

    name: str = Field(..., description="Name of the functional engine or cluster")
    cards: List[str] = Field(default_factory=list, description="Cards participating in this engine")
    enablers_count: int = Field(0, ge=0, description="Count of engine enablers / setup cards")
    payoffs_count: int = Field(0, ge=0, description="Count of payoff / reward cards")
    synergy_score: float = Field(..., ge=0.0, le=100.0, description="Cluster synergy rating (0-100)")


class CohesionAnalysisBreakdown(BaseModel):
    """Deck synergy profile, engine clustering, and cohesion metrics."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 84.0,
                "synergy_rating": 0.52,
                "cohesion_tier": "Highly Cohesive",
                "high_synergy_cards": [{"card_name": "Blood Artist", "synergy_score": 0.65}],
                "low_synergy_cards": [],
                "synergy_clusters": [],
                "engine_balance": {"enabler_to_payoff_ratio": 1.4, "status": "Balanced"},
                "description": "Strong cross-card synergies with robust engine redundancy.",
            }
        }
    )

    score: float = Field(..., ge=0.0, le=100.0, description="Overall cohesion score (0-100)")
    synergy_rating: float = Field(..., ge=-1.0, le=1.0, description="Normalized synergy delta (-1.0 to 1.0)")
    cohesion_tier: str = Field(..., description="Highly Cohesive, Synergistic, Loosely Focused, or Disjointed")
    high_synergy_cards: List[Dict[str, Any]] = Field(default_factory=list, description="Top synergistic cards")
    low_synergy_cards: List[Dict[str, Any]] = Field(default_factory=list, description="Cards with low/negative synergy")
    synergy_clusters: List[SynergyCluster] = Field(default_factory=list, description="Identified deck engines")
    engine_balance: Dict[str, Any] = Field(default_factory=dict, description="Balance between enablers and payoffs")
    description: str = Field("", description="Narrative summary of deck cohesion")


class SaltCategoryBreakdown(BaseModel):
    """Breakdown of specific high-salt mechanics detected in the deck."""
    category: str = Field(..., description="Mechanic category (e.g., Mass Land Destruction, Hard Stax, Extra Turns)")
    count: int = Field(0, ge=0, description="Number of cards in this category")
    cards: List[str] = Field(default_factory=list, description="Names of cards in this category")
    description: str = Field("", description="Description of the mechanic and its playfeel impact")


class PlayfeelAnalysisBreakdown(BaseModel):
    """Assessment of deck playfeel, salt rating, power level, and table experience."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "salt_score": 28.5,
                "salt_tier": "Moderate Salt",
                "playfeel_label": "Interactive Casual",
                "high_salt_cards": [{"card_name": "Cyclonic Rift", "salt_score": 2.45}],
                "salt_categories": [],
                "power_level_estimate": 7.0,
                "power_tier": "Optimized",
                "intent_power_alignment": "Matches user target power level 7",
                "playfeel_summary": "Interactive, focused deck with few salty staples.",
                "warnings": [],
            }
        }
    )

    salt_score: float = Field(..., ge=0.0, description="Overall deck salt score (0.0 to 100.0)")
    salt_tier: str = Field(..., description="Low Salt, Moderate Salt, High Salt, or Table Hazard")
    playfeel_label: str = Field(..., description="Casual Friendly, Interactive Casual, High-Power / Spiky, or Oppressive")
    high_salt_cards: List[SaltCardDetail] = Field(default_factory=list, description="Breakdown of top salty cards")
    salt_categories: List[SaltCategoryBreakdown] = Field(default_factory=list, description="Categories of salty mechanics")
    power_level_estimate: float = Field(..., ge=1.0, le=10.0, description="Estimated power level (1.0 to 10.0)")
    power_tier: str = Field(..., description="Casual, Focused, Optimized, or Competitive")
    intent_power_alignment: Optional[str] = Field(None, description="Alignment with user's target power level")
    playfeel_summary: str = Field(..., description="Human-readable playfeel narrative")
    warnings: List[str] = Field(default_factory=list, description="Warnings about pod friction or mismatched expectations")


class IntentAlignmentReport(BaseModel):
    """Detailed comparison between user intent configuration and deck reality."""
    is_aligned: bool = Field(True, description="Whether the deck generally aligns with user intent")
    power_aligned: bool = Field(True, description="Power level within acceptable tolerance")
    archetype_aligned: bool = Field(True, description="Deck mechanics match preferred archetypes")
    wincon_aligned: bool = Field(True, description="Deck supports preferred win conditions")
    budget_aligned: bool = Field(True, description="Deck respects budget constraints if provided")
    alignment_score: float = Field(..., ge=0.0, le=100.0, description="Overall intent alignment score (0-100)")
    details: List[str] = Field(default_factory=list, description="Alignment observations")
    conflicts: List[str] = Field(default_factory=list, description="Direct conflicts with user intent")


class DeckAnalysisReport(BaseModel):
    """
    Comprehensive deck evaluation output consolidating theme, curve, cohesion,
    playfeel, and intent alignment.
    """
    deck_name: str = Field(..., description="Deck display name")
    format: str = Field("commander", description="Format evaluated")
    commanders: List[str] = Field(default_factory=list, description="Commander card names")
    overall_score: float = Field(..., ge=0.0, le=100.0, description="Composite deck evaluation score (0-100)")
    theme_analysis: ThemeAnalysisBreakdown = Field(..., description="Theme and archetype consistency breakdown")
    curve_analysis: CurveAnalysisBreakdown = Field(..., description="Mana curve and speed breakdown")
    cohesion_analysis: CohesionAnalysisBreakdown = Field(..., description="Cohesion and synergy breakdown")
    playfeel_analysis: PlayfeelAnalysisBreakdown = Field(..., description="Salt rating and playfeel breakdown")
    intent_alignment: IntentAlignmentReport = Field(..., description="Intent alignment report")
    key_findings: List[str] = Field(default_factory=list, description="Key high-signal findings")
    warnings: List[str] = Field(default_factory=list, description="Actionable warnings")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Analysis execution metadata")


# =============================================================================
# Knowledge Base & Heuristic Dictionaries
# =============================================================================

# High-salt mechanics and representative staple cards
SALT_CATEGORY_CARDS: Dict[str, Dict[str, Any]] = {
    "Mass Land Destruction": {
        "description": "Destroys or locks out land mana sources, causing severe pod friction.",
        "cards": {
            "Armageddon", "Ravages of War", "Winter Orb", "Static Orb", "Blood Moon",
            "Back to Basics", "Ruination", "Decree of Annihilation", "Obliterate",
            "Jokulhaups", "Cataclysm", "Stasis", "Nether Void", "Sunder", "Rising Waters",
        },
        "keywords": ["destroy all lands", "each player sacrifices all lands", "lands don't untap"],
        "base_salt": 3.8,
    },
    "Extra Turns": {
        "description": "Chains consecutive turns, consuming table time without opponent interaction.",
        "cards": {
            "Time Warp", "Temporal Manipulation", "Nexus of Fate", "Expropriate",
            "Capture of Jingzhou", "Beacon of Tomorrows", "Walk the Aeons",
            "Time Stretch", "Karn's Temporal Sundering", "Alrund's Epiphany",
        },
        "keywords": ["take an extra turn", "takes an extra turn"],
        "base_salt": 3.2,
    },
    "Hard Stax & Resource Denial": {
        "description": "Hard-locks opponents from casting spells, searching libraries, or drawing cards.",
        "cards": {
            "Drannith Magistrate", "Opposition Agent", "Grand Arbiter Augustin IV",
            "Knowledge Pool", "Possibility Storm", "Vorinclex, Voice of Hunger",
            "Jin-Gitaxias, Core Augur", "Toxrill, the Corrosive", "Tergrid, God of Fright",
            "Sen Triplets", "Hullbreacher", "Leovold, Emissary of Trest",
            "Narset, Parter of Veils", "Notion Thief", "Trinisphere", "Chalice of the Void",
        },
        "keywords": ["opponents can't cast", "players can't cast", "opponents can't search"],
        "base_salt": 3.4,
    },
    "Fast Mana & Free Interaction": {
        "description": "Accelerates mana explosively or counters spells without paying mana.",
        "cards": {
            "Mana Crypt", "Sol Ring", "Mana Vault", "Grim Monolith", "Mox Diamond",
            "Chrome Mox", "Mox Opal", "Lotus Petal", "Jeweled Lotus", "Lion's Eye Diamond",
            "Fierce Guardianship", "Force of Will", "Force of Negation", "Deflecting Swat",
            "Deadly Rollick", "Flawless Maneuver", "Mana Drain", "Dockside Extortionist",
        },
        "keywords": ["if you control a commander", "you may exile a card rather than pay"],
        "base_salt": 2.2,
    },
    "Heavy Theft & Chaos": {
        "description": "Steals opponents' permanents or randomizes board states.",
        "cards": {
            "Thieves' Auction", "Scrambleverse", "Warp World", "Bribery", "Acquire",
            "Gonti, Lord of Luxury", "Etali, Primal Storm", "Etali, Primal Conqueror",
            "Agent of Treachery",
        },
        "keywords": ["control of target", "gain control of all", "exile all permanents, then"],
        "base_salt": 2.5,
    },
    "Two-Card Combos & Instant Wins": {
        "description": "Abrupt game-ending combinations with minimal setup.",
        "cards": {
            "Thassa's Oracle", "Demonic Consultation", "Tainted Pact", "Isochron Scepter",
            "Dramatic Reversal", "Mikaeus, the Unhallowed", "Triskelion", "Walking Ballista",
            "Heliod, Sun-Crowned", "Doomsday", "Hermit Druid", "Food Chain",
        },
        "keywords": ["win the game", "you win the game"],
        "base_salt": 3.5,
    },
}

# Individual staple salt lookup cache
KNOWN_STAPLE_SALT: Dict[str, float] = {
    "Armageddon": 3.9,
    "Winter Orb": 3.85,
    "Static Orb": 3.75,
    "Stasis": 3.8,
    "Expropriate": 3.4,
    "Cyclonic Rift": 2.85,
    "Rhystic Study": 2.65,
    "Smothering Tithe": 2.55,
    "Drannith Magistrate": 3.1,
    "Opposition Agent": 3.2,
    "Tergrid, God of Fright": 3.6,
    "Thassa's Oracle": 3.5,
    "Demonic Consultation": 3.3,
    "Mana Crypt": 2.4,
    "Sol Ring": 1.2,
    "Fierce Guardianship": 2.7,
    "Force of Will": 2.5,
    "Dockside Extortionist": 3.1,
    "Toxrill, the Corrosive": 3.3,
    "Grand Arbiter Augustin IV": 3.2,
    "Vorinclex, Voice of Hunger": 3.4,
    "Blood Moon": 3.1,
    "Back to Basics": 3.2,
}

# Well-known staple CMC lookup for accurate offline heuristic evaluations
KNOWN_STAPLE_CMC: Dict[str, float] = {
    "Mana Crypt": 0.0,
    "Mox Diamond": 0.0,
    "Chrome Mox": 0.0,
    "Mox Opal": 0.0,
    "Lotus Petal": 0.0,
    "Jeweled Lotus": 0.0,
    "Sol Ring": 1.0,
    "Dark Ritual": 1.0,
    "Demonic Consultation": 1.0,
    "Swords to Plowshares": 1.0,
    "Path to Exile": 1.0,
    "Vampiric Tutor": 1.0,
    "Mystical Tutor": 1.0,
    "Worldly Tutor": 1.0,
    "Birds of Paradise": 1.0,
    "Llanowar Elves": 1.0,
    "Elvish Mystic": 1.0,
    "Fyndhorn Elves": 1.0,
    "Wild Growth": 1.0,
    "Hardened Scales": 1.0,
    "The Ozolith": 1.0,
    "Skullclamp": 1.0,
    "Lightning Bolt": 1.0,
    "Ragavan, Nimble Pilferer": 1.0,
    "Viscera Seer": 1.0,
    "Carrion Feeder": 1.0,
    "Arcane Signet": 2.0,
    "Fellwar Stone": 2.0,
    "Thought Vessel": 2.0,
    "Mind Stone": 2.0,
    "Counterspell": 2.0,
    "Cyclonic Rift": 2.0,
    "Demonic Tutor": 2.0,
    "Thassa's Oracle": 2.0,
    "Winter Orb": 2.0,
    "Static Orb": 3.0,
    "Stasis": 2.0,
    "Drannith Magistrate": 2.0,
    "Blood Artist": 2.0,
    "Zulaport Cutthroat": 2.0,
    "Evolution Sage": 3.0,
    "Conclave Mentor": 2.0,
    "Rampant Growth": 2.0,
    "Farseek": 2.0,
    "Nature's Lore": 2.0,
    "Three Visits": 2.0,
    "Heroic Intervention": 2.0,
    "Impact Tremors": 2.0,
    "Dragon Fodder": 2.0,
    "Krenko's Command": 2.0,
    "Goblin Piledriver": 2.0,
    "Cultivate": 3.0,
    "Kodama's Reach": 3.0,
    "Rhystic Study": 3.0,
    "Fierce Guardianship": 3.0,
    "Deflecting Swat": 3.0,
    "Opposition Agent": 3.0,
    "Chaos Warp": 3.0,
    "Beast Within": 3.0,
    "Goblin Chieftain": 3.0,
    "Goblin Warchief": 3.0,
    "Goblin King": 3.0,
    "Hordeling Outburst": 3.0,
    "Armageddon": 4.0,
    "Smothering Tithe": 4.0,
    "Krenko, Mob Boss": 4.0,
    "Purphoros, God of the Forge": 4.0,
    "Atraxa, Praetors' Voice": 4.0,
    "Doubling Season": 5.0,
    "Deepglow Skate": 5.0,
    "Inexorable Tide": 5.0,
    "Force of Will": 5.0,
    "Time Warp": 5.0,
    "Expropriate": 9.0,
}

# Ramp cards for speed & curve detection
KNOWN_RAMP_CARDS: Set[str] = {
    # Rocks
    "Sol Ring", "Arcane Signet", "Fellwar Stone", "Thought Vessel", "Mind Stone",
    "Commander's Sphere", "Chromatic Lantern", "Everflowing Chalice", "Coalition Relic",
    "Worn Powerstone", "Hedron Archive", "Gilded Lotus", "Thran Dynamo", "Basalt Monolith",
    "Grim Monolith", "Mana Vault", "Mana Crypt", "Mox Opal", "Mox Diamond", "Chrome Mox",
    "Lotus Petal", "Jeweled Lotus", "Ornithopter of Paradise",
    # Talismans & Signets
    "Talisman of Progress", "Talisman of Dominance", "Talisman of Indulgence",
    "Talisman of Impulse", "Talisman of Unity", "Talisman of Hierarchy",
    "Talisman of Muscling", "Talisman of Resilience", "Talisman of Curiosity",
    "Talisman of Conviction", "Talisman of Creativity", "Azorius Signet", "Dimir Signet",
    "Rakdos Signet", "Gruul Signet", "Selesnya Signet", "Orzhov Signet", "Izzet Signet",
    "Golgari Signet", "Boros Signet", "Simic Signet",
    # Dorks
    "Birds of Paradise", "Llanowar Elves", "Elvish Mystic", "Fyndhorn Elves",
    "Noble Hierarch", "Ignoble Hierarch", "Delighted Halfling", "Bloom Tender",
    "Priest of Titania", "Marwyn, the Nurturer", "Avacyn's Pilgrim", "Elves of Deep Shadow",
    "Boreal Druid", "Arbor Elf",
    # Spells & Land Ramp
    "Rampant Growth", "Farseek", "Cultivate", "Kodama's Reach", "Nature's Lore",
    "Three Visits", "Skyshroud Claim", "Harrow", "Roiling Regrowth", "Search for Tomorrow",
    "Wild Growth", "Utopia Sprawl", "Explosive Vegetation", "Migration Path",
    "Circuitous Route", "Sakura-Tribe Elder", "Wood Elves", "Farhaven Elf",
    "Steve", "Dark Ritual", "Cabal Ritual",
}

# Archetype keyword indicators and tags
ARCHETYPE_PATTERNS: Dict[str, Dict[str, Any]] = {
    "tokens": {
        "keywords": ["token", "tokens", "create", "populate", "fabricate", "incubate", "amass", "go-wide"],
        "types": [],
        "sample_cards": ["Young Pyromancer", "Lingering Souls", "Doubling Season", "Anointed Procession", "Avenger of Zendikar"],
    },
    "aristocrats": {
        "keywords": ["sacrifice", "dies", "morbid", "drain", "whenever a creature dies", "blood artist", "death trigger"],
        "types": [],
        "sample_cards": ["Viscera Seer", "Blood Artist", "Zulaport Cutthroat", "Carrion Feeder", "Dictate of Erebos", "Pitiless Plunderer"],
    },
    "spellslinger": {
        "keywords": ["instant", "sorcery", "prowess", "magecraft", "flashback", "jump-start", "storm", "cascade", "copy"],
        "types": ["Instant", "Sorcery"],
        "sample_cards": ["Guttersnipe", "Talrand, Sky Summoner", "Baral, Chief of Compliance", "Archmage Emeritus", "Aetherflux Reservoir"],
    },
    "counters": {
        "keywords": ["counter", "counters", "+1/+1", "proliferate", "evolve", "adapt", "graft", "modular", "mentor", "support"],
        "types": [],
        "sample_cards": ["Hardened Scales", "Evolution Sage", "Doubling Season", "Conclave Mentor", "The Ozolith", "Atraxa, Praetors' Voice"],
    },
    "graveyard": {
        "keywords": ["graveyard", "reanimate", "dredge", "delve", "escape", "undergrowth", "threshold", "unearth", "flashback", "recur"],
        "types": [],
        "sample_cards": ["Animate Dead", "Reanimate", "Buried Alive", "Living Death", "Muldrotha, the Gravetide", "Golgari Grave-Troll"],
    },
    "voltron": {
        "keywords": ["equipment", "aura", "equipped", "enchanted creature", "commander damage", "attach", "living weapon", "reconfigure", "hexproof", "indestructible"],
        "types": ["Equipment", "Aura"],
        "sample_cards": ["Sword of Feast and Famine", "Lightning Greaves", "Swiftfoot Boots", "Colossus Hammer", "Puresteel Paladin", "Sigarda's Aid"],
    },
    "stax": {
        "keywords": ["players can't", "each player can't", "spells cost", "enters tapped", "don't untap", "winter orb", "static orb", "tax", "stasis"],
        "types": [],
        "sample_cards": ["Winter Orb", "Static Orb", "Thalia, Guardian of Thraben", "Grand Arbiter Augustin IV", "Drannith Magistrate"],
    },
    "ramp": {
        "keywords": ["search your library for a basic land", "additional land", "landfall", "add mana", "mana pool"],
        "types": [],
        "sample_cards": ["Cultivate", "Kodama's Reach", "Azusa, Lost but Seeking", "Lotus Cobra", "Tatyova, Benthic Druid"],
    },
    "tribal": {
        "keywords": ["other creatures you control get", "creature type", "choose a creature type", "lord"],
        "types": [],
        "sample_cards": ["Elvish Archdruid", "Lord of the Undead", "Goblin Chieftain", "The Ur-Dragon", "Realmwalker"],
    },
    "lifegain": {
        "keywords": ["gain life", "lifelink", "whenever you gain life", "life total"],
        "types": [],
        "sample_cards": ["Soul Warden", "Auriok Champion", "Heliod, Sun-Crowned", "Vito, Thorn of the Dusk Rose", "Felidar Sovereign"],
    },
    "mill": {
        "keywords": ["mill", "puts the top", "into their graveyard from their library"],
        "types": [],
        "sample_cards": ["Maddening Cacophony", "Bruvac the Grandiloquent", "Traumatize", "Ruin Crab", "Hedron Crab"],
    },
    "group_hug": {
        "keywords": ["each player draws", "each player may", "each opponent draws", "each player puts"],
        "types": [],
        "sample_cards": ["Howling Mine", "Rites of Flourishing", "Font of Mythos", "Kynaios and Tiro of Meletis", "Tempt with Discovery"],
    },
    "group_slug": {
        "keywords": ["whenever a player taps", "whenever a player casts", "deals damage to each player", "loses life"],
        "types": [],
        "sample_cards": ["Manabarbs", "Sulfuric Vortex", "Havoc Festival", "Torbran, Thane of Red Fell", "Mogis, God of Slaughter"],
    },
    "control": {
        "keywords": ["counter target", "destroy all", "exile target", "board wipe", "return target nonland permanent"],
        "types": ["Instant", "Sorcery"],
        "sample_cards": ["Counterspell", "Swords to Plowshares", "Cyclonic Rift", "Toxic Deluge", "Wrath of God"],
    },
    "aggro": {
        "keywords": ["haste", "attacks", "first strike", "double strike", "myriad", "melee", "battalion"],
        "types": [],
        "sample_cards": ["Monastery Swiftspear", "Ragavan, Nimble Pilferer", "Goblin Guide", "Bloodthirster"],
    },
    "combo": {
        "keywords": ["infinite", "untap all", "additional combat phase", "search your library for a card"],
        "types": [],
        "sample_cards": ["Thassa's Oracle", "Demonic Consultation", "Isochron Scepter", "Dramatic Reversal", "Demonic Tutor"],
    },
    "midrange": {
        "keywords": ["draw", "destroy", "exile", "value", "return", "enters", "proliferate", "counter"],
        "types": ["Creature", "Planeswalker"],
        "sample_cards": [
            "Atraxa, Praetors' Voice", "Eternal Witness", "Beast Within", "Swords to Plowshares",
            "Rhystic Study", "Sun Titan", "Evolution Sage", "Doubling Season", "Hardened Scales",
        ],
    },
}

# Known staple card themes for offline heuristic alignment
KNOWN_STAPLE_THEMES: Dict[str, List[str]] = {
    "Hardened Scales": ["counters", "+1/+1 counters", "midrange"],
    "Evolution Sage": ["counters", "+1/+1 counters", "proliferate", "landfall", "midrange"],
    "Doubling Season": ["counters", "+1/+1 counters", "tokens", "proliferate", "midrange"],
    "Conclave Mentor": ["counters", "+1/+1 counters", "lifegain", "midrange"],
    "The Ozolith": ["counters", "+1/+1 counters", "midrange"],
    "Inexorable Tide": ["counters", "+1/+1 counters", "proliferate", "spellslinger"],
    "Deepglow Skate": ["counters", "+1/+1 counters", "proliferate", "midrange"],
    "Atraxa, Praetors' Voice": ["counters", "+1/+1 counters", "proliferate", "midrange"],
    "Viscera Seer": ["aristocrats", "sacrifice"],
    "Blood Artist": ["aristocrats", "sacrifice", "drain"],
    "Zulaport Cutthroat": ["aristocrats", "sacrifice", "drain"],
    "Carrion Feeder": ["aristocrats", "sacrifice"],
    "Young Pyromancer": ["tokens", "spellslinger"],
    "Lingering Souls": ["tokens", "graveyard"],
    "Goblin Chieftain": ["tribal", "aggro"],
    "Goblin Warchief": ["tribal", "aggro"],
    "Goblin King": ["tribal", "aggro"],
    "Goblin Piledriver": ["tribal", "aggro"],
    "Dragon Fodder": ["tokens", "tribal"],
    "Krenko's Command": ["tokens", "tribal"],
    "Hordeling Outburst": ["tokens", "tribal"],
    "Purphoros, God of the Forge": ["tokens", "aggro"],
    "Impact Tremors": ["tokens", "aggro"],
    "Winter Orb": ["stax"],
    "Static Orb": ["stax"],
    "Armageddon": ["stax", "mass land destruction"],
    "Stasis": ["stax"],
    "Drannith Magistrate": ["stax"],
    "Opposition Agent": ["stax"],
    "Thassa's Oracle": ["combo"],
    "Demonic Consultation": ["combo"],
    "Isochron Scepter": ["combo", "spellslinger"],
    "Dramatic Reversal": ["combo", "spellslinger"],
    "Swords to Plowshares": ["control", "removal", "midrange"],
    "Counterspell": ["control", "counterspell", "midrange"],
    "Cyclonic Rift": ["control", "board wipe", "midrange"],
    "Rhystic Study": ["control", "card draw", "midrange"],
    "Beast Within": ["control", "removal", "midrange"],
}

_MANA_SYMBOL_RE = re.compile(r"\{([A-Za-z0-9/]+)\}")


def parse_mana_cost(mana_cost: Optional[str]) -> Tuple[float, Dict[str, int]]:
    """
    Parse an MTG mana cost string (e.g. '{2}{U}{U}') into (cmc, pips_dict).
    """
    if not mana_cost or not isinstance(mana_cost, str):
        return 0.0, {}

    symbols = _MANA_SYMBOL_RE.findall(mana_cost)
    if not symbols:
        # Check if raw string is digits or letters (e.g. '2UU')
        raw = mana_cost.strip().upper()
        pips: Dict[str, int] = {}
        cmc = 0.0
        num_buf = ""
        for ch in raw:
            if ch.isdigit():
                num_buf += ch
            else:
                if num_buf:
                    cmc += float(num_buf)
                    num_buf = ""
                if ch in ("W", "U", "B", "R", "G", "C"):
                    cmc += 1.0
                    pips[ch] = pips.get(ch, 0) + 1
        if num_buf:
            cmc += float(num_buf)
        return cmc, pips

    cmc = 0.0
    pips: Dict[str, int] = {}
    for sym in symbols:
        s = sym.upper()
        if s.isdigit():
            cmc += float(s)
        elif s == "X":
            # X costs count as 0 for CMC off the stack
            continue
        elif "/" in s:
            # Hybrid mana like W/U or 2/W
            parts = s.split("/")
            if parts[0].isdigit():
                cmc += float(parts[0])
            else:
                cmc += 1.0
            for p in parts:
                if p in ("W", "U", "B", "R", "G", "C"):
                    pips[p] = pips.get(p, 0) + 1
        elif s in ("W", "U", "B", "R", "G", "C"):
            cmc += 1.0
            pips[s] = pips.get(s, 0) + 1
        else:
            cmc += 1.0

    return cmc, pips


# =============================================================================
# Core Analysis Engine
# =============================================================================

class DeckAnalysisEngine:
    """
    Unified analysis engine assessing deck mechanics, archetype alignment,
    mana curve & speed, cohesion & synergy, and playfeel & saltiness.
    """

    def __init__(
        self,
        card_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        default_power_tier: str = "focused",
    ):
        """
        Initialize the analysis engine.

        Args:
            card_resolver: Optional callable `(name: str) -> dict` returning card metadata
                           such as cmc, type_line, oracle_text, keywords, salt, synergy.
            default_power_tier: Fallback power tier if unspecified in user intent.
        """
        self.card_resolver = card_resolver
        self.default_power_tier = default_power_tier

    def analyze(
        self,
        deck: Union[ConsolidatedDeckIntake, DecklistInput, Dict[str, Any], str],
        intent: Optional[Union[UserDeckIntent, Dict[str, Any]]] = None,
        external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
        card_metadata: Optional[Dict[str, Any]] = None,
    ) -> DeckAnalysisReport:
        """
        Perform a comprehensive synchronous evaluation of a deck.

        Args:
            deck: ConsolidatedDeckIntake / DeckIntakeResult, DecklistInput, raw string, or dict.
            intent: Optional UserDeckIntent or intent dictionary overriding deck intent.
            external_metrics: External provider analytics (Commander Salt, EDHRec, Aggregator).
            card_metadata: Pre-resolved card metadata dictionary {card_name: metadata_dict}.

        Returns:
            DeckAnalysisReport containing all structured scores, breakdowns, and narrative summaries.
        """
        # 1. Normalize input deck and intent
        intake_obj, user_intent = self._normalize_inputs(deck, intent)

        # 2. Build card data cache (cards, cmc, types, mechanics, salt, synergy)
        card_records = self._resolve_all_cards(intake_obj, card_metadata)

        # 3. Analyze Theme & Archetype Consistency
        theme_analysis = self._analyze_theme(intake_obj, user_intent, card_records)

        # 4. Analyze Mana Curve & Speed Score
        curve_analysis = self._analyze_curve(intake_obj, user_intent, card_records)

        # 5. Analyze Cohesion & Synergy Rating
        cohesion_analysis = self._analyze_cohesion(
            intake_obj, user_intent, card_records, external_metrics
        )

        # 6. Analyze Salt Rating & Playfeel Breakdown
        playfeel_analysis = self._analyze_playfeel(
            intake_obj, user_intent, card_records, external_metrics
        )

        # 7. Evaluate User Intent Alignment
        intent_alignment = self._evaluate_intent_alignment(
            user_intent, theme_analysis, curve_analysis, cohesion_analysis, playfeel_analysis
        )

        # 8. Calculate Overall Composite Score & Synthesize Key Findings
        overall_score, key_findings, warnings = self._synthesize_report(
            theme_analysis, curve_analysis, cohesion_analysis, playfeel_analysis, intent_alignment
        )

        commanders = [cmd.name for cmd in intake_obj.commanders]

        return DeckAnalysisReport(
            deck_name=intake_obj.name or "Untitled Deck",
            format=intake_obj.format or "commander",
            commanders=commanders,
            overall_score=round(overall_score, 1),
            theme_analysis=theme_analysis,
            curve_analysis=curve_analysis,
            cohesion_analysis=cohesion_analysis,
            playfeel_analysis=playfeel_analysis,
            intent_alignment=intent_alignment,
            key_findings=key_findings,
            warnings=warnings,
            metadata={
                "total_cards": intake_obj.total_cards(),
                "total_unique": len(card_records),
                "engine_version": "1.0.0",
            },
        )

    # -------------------------------------------------------------------------
    # Input Normalization
    # -------------------------------------------------------------------------

    def _normalize_inputs(
        self,
        deck: Union[ConsolidatedDeckIntake, DecklistInput, Dict[str, Any], str],
        intent: Optional[Union[UserDeckIntent, Dict[str, Any]]] = None,
    ) -> Tuple[ConsolidatedDeckIntake, UserDeckIntent]:
        """Normalize deck input and user intent into standardized domain objects."""
        intake_service = DeckIntakeService(card_resolver=self.card_resolver)

        if isinstance(deck, ConsolidatedDeckIntake):
            intake_obj = deck
        elif isinstance(deck, DecklistInput):
            intake_obj = intake_service.process(decklist=deck, intent=intent)
        elif isinstance(deck, str):
            intake_obj = intake_service.process(decklist=deck, intent=intent)
        elif isinstance(deck, dict):
            # Dict could be DeckIntakePayload format, or have cards/decklist/commanders
            dl_content = deck.get("decklist") or deck.get("cards")
            d_name = deck.get("name")
            d_cmds = deck.get("commanders")
            d_id = deck.get("deck_id") or deck.get("id")
            d_intent = intent or deck.get("user_intent") or deck.get("intent")

            # If d_cmds provided and dl_content is a list of card dicts, ensure commanders are in dl_content
            if d_cmds and isinstance(dl_content, list):
                existing_names = {c.get("name", "").strip().lower() for c in dl_content if isinstance(c, dict)}
                for cmd in d_cmds:
                    cmd_name = cmd.strip() if isinstance(cmd, str) else getattr(cmd, "name", str(cmd))
                    if cmd_name.lower() not in existing_names:
                        dl_content = [{"name": cmd_name, "quantity": 1, "section": "commander"}] + dl_content

            intake_obj = intake_service.process(
                decklist=dl_content,
                intent=d_intent,
                name=d_name,
                commanders=d_cmds,
                deck_id=str(d_id) if d_id else None,
            )
        else:
            raise TypeError(f"Unsupported deck input type: {type(deck).__name__}")

        if intent is not None:
            if isinstance(intent, UserDeckIntent):
                final_intent = intent
            elif isinstance(intent, dict):
                final_intent = parse_user_intent(intent)
            else:
                raise TypeError(f"Unsupported intent type: {type(intent).__name__}")
        else:
            final_intent = intake_obj.user_intent or parse_user_intent({})

        return intake_obj, final_intent

    def _resolve_all_cards(
        self,
        intake_obj: ConsolidatedDeckIntake,
        card_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Assemble unified card metadata for all cards in the intake deck.
        Returns a dict mapping card_name -> enriched metadata dictionary.
        """
        records: Dict[str, Dict[str, Any]] = {}
        provided_meta = card_metadata or {}

        # Combine commanders and mainboard
        all_entries: List[Tuple[str, int, str]] = []
        for cmd in intake_obj.commanders:
            all_entries.append((cmd.name, 1, "Commander"))
        for card in intake_obj.cards:
            all_entries.append((card.name, card.quantity, card.category or "Mainboard"))

        for name, qty, section in all_entries:
            if name in records:
                records[name]["quantity"] += qty
                continue

            record: Dict[str, Any] = {
                "name": name,
                "quantity": qty,
                "section": section,
                "cmc": None,
                "mana_cost": None,
                "type_line": None,
                "oracle_text": None,
                "keywords": [],
                "colors": [],
                "tags": [],
                "salt": None,
                "synergy": None,
                "is_land": False,
                "is_ramp": False,
            }

            # Merge from user provided metadata
            meta = provided_meta.get(name) or provided_meta.get(name.lower())
            if meta:
                if hasattr(meta, "to_dict") and callable(getattr(meta, "to_dict")):
                    meta = meta.to_dict()
                elif hasattr(meta, "__dict__"):
                    meta = vars(meta)
                if isinstance(meta, dict):
                    record.update({k: v for k, v in meta.items() if v is not None})

            # Query card_resolver if available and fields are missing
            if self.card_resolver and (record.get("cmc") is None or record.get("type_line") is None):
                try:
                    resolved = self.card_resolver(name)
                    if resolved and isinstance(resolved, dict):
                        for k, v in resolved.items():
                            if v is not None and record.get(k) is None:
                                record[k] = v
                except Exception:
                    pass

            # Infer land status
            type_line = str(record.get("type_line") or "")
            if "Land" in type_line or name in BASIC_LAND_NAMES:
                record["is_land"] = True
            elif section.lower() in ("land", "lands"):
                record["is_land"] = True

            # Infer ramp status
            if name in KNOWN_RAMP_CARDS:
                record["is_ramp"] = True

            # Calculate CMC if missing
            if record.get("cmc") is None:
                mana_cost = record.get("mana_cost")
                if mana_cost:
                    calc_cmc, _ = parse_mana_cost(mana_cost)
                    record["cmc"] = calc_cmc
                elif record["is_land"]:
                    record["cmc"] = 0.0
                elif name in KNOWN_STAPLE_CMC:
                    record["cmc"] = KNOWN_STAPLE_CMC[name]
                else:
                    record["cmc"] = 3.0  # Heuristic fallback for unknown non-lands

            records[name] = record

        return records

    # -------------------------------------------------------------------------
    # 1. Theme & Archetype Consistency Analysis
    # -------------------------------------------------------------------------

    def _analyze_theme(
        self,
        intake_obj: ConsolidatedDeckIntake,
        intent: UserDeckIntent,
        card_records: Dict[str, Dict[str, Any]],
    ) -> ThemeAnalysisBreakdown:
        """
        Evaluate theme and archetype alignment, mechanic distribution,
        and excluded archetype violations.
        """
        primary_archetype = (intent.archetype_preferences.primary_archetype or "midrange").lower()
        secondary_archetypes = [s.lower() for s in intent.archetype_preferences.secondary_archetypes]
        excluded_archetypes = [e.lower() for e in intent.archetype_preferences.excluded_archetypes]
        custom_themes = [t.lower() for t in intent.archetype_preferences.custom_themes]

        mechanic_counts: Dict[str, int] = {}
        thematic_tags: Dict[str, int] = {}
        archetype_matches: Dict[str, List[str]] = {}

        # Scan each non-land card against archetypes and mechanics
        non_land_cards = [r for r in card_records.values() if not r["is_land"]]
        total_spells = max(1, sum(r["quantity"] for r in non_land_cards))

        for card in non_land_cards:
            name = card["name"]
            qty = card["quantity"]
            text = f"{name} {card.get('type_line') or ''} {card.get('oracle_text') or ''}".lower()
            keywords = [str(k).lower() for k in card.get("keywords") or []]
            tags = [str(t).lower() for t in card.get("tags") or []]

            # Incorporate staple themes if known
            if name in KNOWN_STAPLE_THEMES:
                for st in KNOWN_STAPLE_THEMES[name]:
                    tags.append(st.lower())

            for arch, pattern in ARCHETYPE_PATTERNS.items():
                matched = False
                # Check keywords
                for kw in pattern["keywords"]:
                    if kw in text or kw in keywords or kw in tags:
                        matched = True
                        mechanic_counts[kw] = mechanic_counts.get(kw, 0) + qty
                        thematic_tags[kw] = thematic_tags.get(kw, 0) + qty

                # Check sample staple card names
                if name in pattern["sample_cards"]:
                    matched = True

                if matched:
                    archetype_matches.setdefault(arch, []).append(name)

            # Check custom themes
            for theme in custom_themes:
                theme_lower = theme.lower()
                matched_theme = False
                if theme_lower in text or theme_lower in tags or any(theme_lower in t for t in tags):
                    matched_theme = True
                elif theme_lower in ARCHETYPE_PATTERNS and name in ARCHETYPE_PATTERNS[theme_lower].get("sample_cards", []):
                    matched_theme = True
                elif "counter" in theme_lower and any("counter" in t for t in tags):
                    matched_theme = True

                if matched_theme:
                    thematic_tags[theme] = thematic_tags.get(theme, 0) + qty
                    archetype_matches.setdefault(theme, []).append(name)

        # Primary archetype score
        primary_cards = set(archetype_matches.get(primary_archetype, []))
        primary_count = sum(card_records[name]["quantity"] for name in primary_cards if name in card_records)
        # Expected ~25 cards for a dedicated primary archetype in a 100-card deck (~40% of non-lands)
        target_primary_expectation = max(10, int(total_spells * 0.35))
        primary_score = min(100.0, (primary_count / target_primary_expectation) * 100.0)

        # Custom themes score
        custom_cards: Set[str] = set()
        for t in custom_themes:
            custom_cards.update(archetype_matches.get(t, []))
        custom_count = sum(card_records[name]["quantity"] for name in custom_cards if name in card_records)
        target_custom_exp = max(6, int(total_spells * 0.20))
        custom_score = min(100.0, (custom_count / target_custom_exp) * 100.0) if custom_themes else primary_score

        # Secondary archetype scores
        secondary_results: List[ArchetypeMatch] = []
        for sec in secondary_archetypes:
            sec_cards = set(archetype_matches.get(sec, []))
            sec_count = sum(card_records[name]["quantity"] for name in sec_cards if name in card_records)
            sec_score = min(100.0, (sec_count / max(10, int(total_spells * 0.20))) * 100.0)
            secondary_results.append(
                ArchetypeMatch(
                    archetype=sec,
                    score=round(sec_score, 1),
                    supporting_cards_count=sec_count,
                    supporting_cards=list(sec_cards),
                )
            )

        # Check for excluded archetype violations
        violations: List[Dict[str, Any]] = []
        for exc in excluded_archetypes:
            if exc in ARCHETYPE_PATTERNS:
                violating_cards = archetype_matches.get(exc, [])
                for v_card in violating_cards:
                    violations.append({
                        "card_name": v_card,
                        "excluded_archetype": exc,
                        "reason": f"Matches excluded archetype '{exc}' mechanics or tags",
                    })

        # Calculate off-theme cards
        # Cards that don't match primary, custom themes, secondaries, ramp, or basic removal/card draw
        all_aligned_cards = set(primary_cards)
        all_aligned_cards.update(custom_cards)
        for sec_m in secondary_results:
            all_aligned_cards.update(sec_m.supporting_cards)
        off_theme: List[str] = []
        for card in non_land_cards:
            name = card["name"]
            if name not in all_aligned_cards and not card["is_ramp"]:
                # Check if it has any tag match
                text = f"{name} {card.get('type_line') or ''} {card.get('oracle_text') or ''}".lower()
                is_utility = any(u in text for u in ["draw", "counter target", "destroy target", "exile target"])
                if not is_utility:
                    off_theme.append(name)

        # Overall theme score:
        sec_avg = (
            sum(s.score for s in secondary_results) / len(secondary_results)
            if secondary_results
            else primary_score
        )
        if custom_themes and secondary_results:
            base_theme_score = (primary_score * 0.40) + (custom_score * 0.35) + (sec_avg * 0.25)
        elif custom_themes:
            base_theme_score = (primary_score * 0.50) + (custom_score * 0.50)
        elif secondary_results:
            base_theme_score = (primary_score * 0.65) + (sec_avg * 0.35)
        else:
            base_theme_score = primary_score

        penalty = min(40.0, len(violations) * 15.0)
        final_score = max(0.0, min(100.0, base_theme_score - penalty))

        if final_score >= 80.0:
            tier = "High"
        elif final_score >= 60.0:
            tier = "Moderate"
        elif final_score >= 40.0:
            tier = "Low"
        else:
            tier = "Scattered"

        desc = (
            f"Deck exhibits {tier.lower()} consistency with primary '{primary_archetype}' archetype "
            f"({primary_count} cards, {round(primary_score, 1)}% alignment)."
        )
        if violations:
            desc += f" Warning: {len(violations)} card(s) violate excluded archetypes: {', '.join(excluded_archetypes)}."

        return ThemeAnalysisBreakdown(
            score=round(final_score, 1),
            consistency_tier=tier,
            primary_archetype=primary_archetype,
            primary_archetype_score=round(primary_score, 1),
            secondary_archetypes=secondary_results,
            thematic_tags_matched=thematic_tags,
            off_theme_cards=off_theme[:10],
            excluded_archetype_violations=violations,
            mechanic_distribution=mechanic_counts,
            description=desc,
        )

    # -------------------------------------------------------------------------
    # 2. Mana Curve & Speed Score Analysis
    # -------------------------------------------------------------------------

    def _analyze_curve(
        self,
        intake_obj: ConsolidatedDeckIntake,
        intent: UserDeckIntent,
        card_records: Dict[str, Dict[str, Any]],
    ) -> CurveAnalysisBreakdown:
        """
        Evaluate mana curve distribution, average CMC, mana ramp density,
        and calculate operational turn speed.
        """
        non_lands = [r for r in card_records.values() if not r["is_land"]]
        lands = [r for r in card_records.values() if r["is_land"]]

        total_lands = sum(r["quantity"] for r in lands)
        total_spells = sum(r["quantity"] for r in non_lands)

        cmc_counts: Dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6+": 0}
        color_pips: Dict[str, int] = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}

        cmc_list: List[float] = []
        ramp_cards: List[str] = []
        fast_mana_count = 0

        for r in non_lands:
            qty = r["quantity"]
            name = r["name"]
            cmc = float(r.get("cmc") or 0.0)
            for _ in range(qty):
                cmc_list.append(cmc)

            # Histogram bucket
            int_cmc = int(math.floor(cmc))
            if int_cmc >= 6:
                cmc_counts["6+"] += qty
            elif str(int_cmc) in cmc_counts:
                cmc_counts[str(int_cmc)] += qty
            else:
                cmc_counts["0"] += qty

            # Pips
            mana_cost = r.get("mana_cost")
            if mana_cost:
                _, pips = parse_mana_cost(mana_cost)
                for color, p_count in pips.items():
                    if color in color_pips:
                        color_pips[color] += p_count * qty

            # Ramp detection
            if r["is_ramp"] or name in KNOWN_RAMP_CARDS:
                ramp_cards.append(name)
                if name in ("Mana Crypt", "Sol Ring", "Mana Vault", "Chrome Mox", "Mox Diamond", "Lotus Petal", "Dark Ritual"):
                    fast_mana_count += qty
            else:
                text = f"{r.get('type_line') or ''} {r.get('oracle_text') or ''}".lower()
                if "add {" in text or "search your library for a land" in text or "search your library for a basic land" in text:
                    ramp_cards.append(name)

        total_ramp = len(ramp_cards)

        # Average and median CMC
        if cmc_list:
            avg_cmc = sum(cmc_list) / len(cmc_list)
            sorted_cmc = sorted(cmc_list)
            mid = len(sorted_cmc) // 2
            median_cmc = (
                (sorted_cmc[mid - 1] + sorted_cmc[mid]) / 2.0
                if len(sorted_cmc) % 2 == 0
                else sorted_cmc[mid]
            )
        else:
            avg_cmc = 0.0
            median_cmc = 0.0

        # Calculate speed score (1.0 to 10.0 scale)
        # Base speed inversely proportional to CMC
        if avg_cmc <= 2.2:
            base_speed = 9.0
        elif avg_cmc <= 2.8:
            base_speed = 8.0
        elif avg_cmc <= 3.3:
            base_speed = 6.5
        elif avg_cmc <= 3.8:
            base_speed = 5.0
        elif avg_cmc <= 4.2:
            base_speed = 3.5
        else:
            base_speed = 2.0

        # Ramp modifier: target 10-12 ramp pieces
        if total_ramp >= 12:
            ramp_mod = 1.0
        elif total_ramp >= 9:
            ramp_mod = 0.5
        elif total_ramp >= 6:
            ramp_mod = 0.0
        elif total_ramp >= 3:
            ramp_mod = -0.8
        else:
            ramp_mod = -1.5

        # Fast mana modifier
        fast_mana_mod = min(1.0, fast_mana_count * 0.4)

        raw_speed = base_speed + ramp_mod + fast_mana_mod
        speed_score = max(1.0, min(10.0, raw_speed))

        # Speed tier
        if speed_score >= 8.5:
            speed_tier = "Blistering"
            est_turn = 2 if fast_mana_count >= 2 else 3
        elif speed_score >= 7.0:
            speed_tier = "Fast"
            est_turn = 3 if total_ramp >= 10 else 4
        elif speed_score >= 5.5:
            speed_tier = "Moderate"
            est_turn = 5
        elif speed_score >= 4.0:
            speed_tier = "Deliberate"
            est_turn = 6
        else:
            speed_tier = "Slow"
            est_turn = 7

        # Curve rating & score (0 to 100)
        recs: List[str] = []
        curve_penalties = 0.0

        # Penalize if land count is abnormal for standard commander
        if total_lands < 32 and avg_cmc > 2.5:
            curve_penalties += 15.0
            recs.append(f"Low land count ({total_lands} lands) for average CMC {round(avg_cmc, 2)}; consider running 35-37 lands.")
        elif total_lands > 42:
            curve_penalties += 10.0
            recs.append(f"High land count ({total_lands} lands) may risk flooding; consider trimming 3-4 lands.")

        if total_ramp < 8:
            curve_penalties += 15.0
            recs.append(f"Low ramp density ({total_ramp} ramp sources); consider adding 2-mana rocks or dorks.")

        # Check top-heavy curve
        high_cmc_count = cmc_counts["5"] + cmc_counts["6+"]
        if high_cmc_count > 18 and total_ramp < 12:
            curve_penalties += 15.0
            recs.append(f"Top-heavy curve ({high_cmc_count} spells at 5+ CMC); consider swapping high-CMC cards for lower-cost alternatives.")

        curve_score = max(20.0, min(100.0, 100.0 - curve_penalties))

        if curve_score >= 85.0:
            curve_rating = "Optimal Curve"
        elif curve_score >= 70.0:
            curve_rating = "Efficient Curve"
        elif curve_score >= 55.0:
            curve_rating = "Slightly Top-Heavy" if high_cmc_count > 15 else "Under-Ramped"
        else:
            curve_rating = "Sluggish / Top-Heavy"

        # Win turn comparison against user intent
        target_win = intent.deck_vision.target_turn_win
        turn_alignment: Optional[str] = None
        if target_win is not None:
            if est_turn <= target_win - 1:
                turn_alignment = f"Deck speed (operational turn ~{est_turn}) is well-positioned for target win on turn {target_win}."
            elif est_turn <= target_win + 1:
                turn_alignment = f"Deck speed (operational turn ~{est_turn}) meets target turn {target_win} win expectation."
            else:
                turn_alignment = (
                    f"Pacing conflict: Deck estimated operational turn ~{est_turn} is slower than "
                    f"user's target turn {target_win} win goal. Lower the curve and add fast ramp."
                )

        return CurveAnalysisBreakdown(
            score=round(curve_score, 1),
            speed_score=round(speed_score, 1),
            speed_tier=speed_tier,
            average_cmc=round(avg_cmc, 2),
            median_cmc=round(median_cmc, 1),
            total_spells=total_spells,
            total_lands=total_lands,
            total_ramp=total_ramp,
            cmc_distribution=cmc_counts,
            color_distribution={k: v for k, v in color_pips.items() if v > 0},
            estimated_operational_turn=est_turn,
            target_turn_win_alignment=turn_alignment,
            curve_rating=curve_rating,
            recommendations=recs,
        )

    # -------------------------------------------------------------------------
    # 3. Cohesion & Synergy Rating Analysis
    # -------------------------------------------------------------------------

    def _analyze_cohesion(
        self,
        intake_obj: ConsolidatedDeckIntake,
        intent: UserDeckIntent,
        card_records: Dict[str, Dict[str, Any]],
        external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
    ) -> CohesionAnalysisBreakdown:
        """
        Evaluate deck synergy, functional engine clustering, and enabler/payoff balance.
        Integrates external provider synergy metrics when available.
        """
        ext_synergy = None
        if external_metrics:
            if isinstance(external_metrics, DeckAnalyticsResult):
                ext_synergy = external_metrics.synergy
            elif isinstance(external_metrics, dict):
                syn_data = external_metrics.get("synergy")
                if isinstance(syn_data, DeckSynergy):
                    ext_synergy = syn_data
                elif isinstance(syn_data, dict):
                    try:
                        ext_synergy = DeckSynergy.model_validate(syn_data)
                    except Exception:
                        pass

        # Identify functional engine clusters
        clusters: List[SynergyCluster] = []
        non_lands = [r for r in card_records.values() if not r["is_land"]]

        # 1. Ramp Cluster
        ramp_cards = [r["name"] for r in non_lands if r["is_ramp"] or r["name"] in KNOWN_RAMP_CARDS]
        if ramp_cards:
            clusters.append(
                SynergyCluster(
                    name="Mana Acceleration Engine",
                    cards=ramp_cards,
                    enablers_count=len(ramp_cards),
                    payoffs_count=len(ramp_cards),
                    synergy_score=85.0,
                )
            )

        # 2. Draw / Card Advantage Cluster
        draw_cards: List[str] = []
        for r in non_lands:
            text = f"{r.get('oracle_text') or ''} {r.get('type_line') or ''}".lower()
            if "draw a card" in text or "draw cards" in text or "draws two cards" in text or "impulse" in text:
                draw_cards.append(r["name"])
        if draw_cards:
            clusters.append(
                SynergyCluster(
                    name="Card Advantage Engine",
                    cards=draw_cards,
                    enablers_count=len(draw_cards),
                    payoffs_count=len(draw_cards),
                    synergy_score=80.0,
                )
            )

        # 3. Aristocrats / Sacrifice Cluster
        sac_outlets: List[str] = []
        sac_payoffs: List[str] = []
        for r in non_lands:
            text = f"{r.get('oracle_text') or ''}".lower()
            name = r["name"]
            if "sacrifice a creature" in text or "sacrifice an artifact" in text:
                sac_outlets.append(name)
            if "whenever a creature dies" in text or "whenever a creature you control dies" in text:
                sac_payoffs.append(name)
        if sac_outlets or sac_payoffs:
            all_sac = list(set(sac_outlets + sac_payoffs))
            clusters.append(
                SynergyCluster(
                    name="Aristocrats & Sacrifice Engine",
                    cards=all_sac,
                    enablers_count=len(sac_outlets),
                    payoffs_count=len(sac_payoffs),
                    synergy_score=90.0 if (sac_outlets and sac_payoffs) else 50.0,
                )
            )

        # 4. Counters / Proliferate Cluster
        counter_enablers: List[str] = []
        counter_payoffs: List[str] = []
        for r in non_lands:
            text = f"{r.get('oracle_text') or ''}".lower()
            name = r["name"]
            if "put a +1/+1 counter" in text or "proliferate" in text:
                counter_enablers.append(name)
            if "with counters" in text or "remove a +1/+1 counter" in text or "doubling season" in name.lower():
                counter_payoffs.append(name)
        if counter_enablers or counter_payoffs:
            all_counters = list(set(counter_enablers + counter_payoffs))
            clusters.append(
                SynergyCluster(
                    name="+1/+1 Counters Engine",
                    cards=all_counters,
                    enablers_count=len(counter_enablers),
                    payoffs_count=len(counter_payoffs),
                    synergy_score=88.0 if (counter_enablers and counter_payoffs) else 50.0,
                )
            )

        # Calculate high and low synergy cards from external metrics or heuristics
        high_synergy_cards: List[Dict[str, Any]] = []
        low_synergy_cards: List[Dict[str, Any]] = []

        if ext_synergy and ext_synergy.card_synergies:
            for s_item in ext_synergy.card_synergies:
                if s_item.synergy_score >= 0.30:
                    high_synergy_cards.append({
                        "card_name": s_item.card_name,
                        "synergy_score": round(s_item.synergy_score, 2),
                        "context": s_item.context or "High community synergy with commander",
                    })
                elif s_item.synergy_score <= -0.15:
                    low_synergy_cards.append({
                        "card_name": s_item.card_name,
                        "synergy_score": round(s_item.synergy_score, 2),
                        "context": s_item.context or "Low or negative synergy with commander",
                    })
        else:
            # Fallback heuristic synergy: cards in multiple clusters have high synergy
            for c in clusters:
                if c.synergy_score >= 80.0:
                    for c_card in c.cards[:5]:
                        high_synergy_cards.append({
                            "card_name": c_card,
                            "synergy_score": 0.55,
                            "context": f"Core component of {c.name}",
                        })

        # Calculate normalized overall synergy delta (-1.0 to 1.0) and score (0 to 100)
        if ext_synergy and ext_synergy.overall_synergy is not None:
            synergy_delta = max(-1.0, min(1.0, ext_synergy.overall_synergy))
            cohesion_score = max(0.0, min(100.0, (synergy_delta + 1.0) * 50.0))
        else:
            # Internal heuristic calculation
            cluster_weights = sum(c.synergy_score for c in clusters) / max(1, len(clusters)) if clusters else 50.0
            engine_breadth = min(30.0, len(clusters) * 10.0)
            cohesion_score = max(20.0, min(100.0, (cluster_weights * 0.70) + engine_breadth))
            synergy_delta = round((cohesion_score - 50.0) / 50.0, 2)

        if cohesion_score >= 75.0:
            tier = "Highly Cohesive"
        elif cohesion_score >= 55.0:
            tier = "Synergistic"
        elif cohesion_score >= 40.0:
            tier = "Loosely Focused"
        else:
            tier = "Disjointed"

        engine_balance = {
            "total_clusters_detected": len(clusters),
            "clusters": [c.name for c in clusters],
            "status": "Healthy redundancy" if len(clusters) >= 3 else "Sparse functional engines",
        }

        desc = (
            f"Deck demonstrates {tier.lower()} architecture with {len(clusters)} functional engine cluster(s) "
            f"and overall synergy rating of {synergy_delta:+.2f}."
        )

        return CohesionAnalysisBreakdown(
            score=round(cohesion_score, 1),
            synergy_rating=round(synergy_delta, 2),
            cohesion_tier=tier,
            high_synergy_cards=high_synergy_cards[:10],
            low_synergy_cards=low_synergy_cards[:10],
            synergy_clusters=clusters,
            engine_balance=engine_balance,
            description=desc,
        )

    # -------------------------------------------------------------------------
    # 4. Salt Rating & Playfeel Breakdown Analysis
    # -------------------------------------------------------------------------

    def _analyze_playfeel(
        self,
        intake_obj: ConsolidatedDeckIntake,
        intent: UserDeckIntent,
        card_records: Dict[str, Dict[str, Any]],
        external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
    ) -> PlayfeelAnalysisBreakdown:
        """
        Evaluate deck saltiness, power level estimate, salty mechanic categories,
        and potential pod friction based on Commander Salt data.
        """
        ext_salt = None
        ext_power = None
        if external_metrics:
            if isinstance(external_metrics, DeckAnalyticsResult):
                if external_metrics.meta_scores:
                    ext_salt = external_metrics.meta_scores.salt
                    ext_power = external_metrics.meta_scores.power
            elif isinstance(external_metrics, dict):
                meta_s = external_metrics.get("meta_scores") or external_metrics
                if isinstance(meta_s, dict):
                    s_val = meta_s.get("salt")
                    p_val = meta_s.get("power")
                    if isinstance(s_val, SaltScore):
                        ext_salt = s_val
                    elif isinstance(s_val, dict):
                        try:
                            ext_salt = SaltScore.model_validate(s_val)
                        except Exception:
                            pass
                    if isinstance(p_val, PowerScore):
                        ext_power = p_val
                    elif isinstance(p_val, dict):
                        try:
                            ext_power = PowerScore.model_validate(p_val)
                        except Exception:
                            pass

        high_salt_cards: List[SaltCardDetail] = []
        salt_categories: List[SaltCategoryBreakdown] = []
        warnings: List[str] = []

        # Scan deck against known salt categories and staple cards
        detected_categories: Dict[str, List[str]] = {}
        scanned_salt_cards: Dict[str, float] = {}

        for card in card_records.values():
            name = card["name"]
            text = f"{name} {card.get('type_line') or ''} {card.get('oracle_text') or ''}".lower()

            # Check individual staple salt
            card_salt_val = KNOWN_STAPLE_SALT.get(name)
            if card_salt_val is not None:
                scanned_salt_cards[name] = card_salt_val

            # Check categories
            for cat_name, cat_data in SALT_CATEGORY_CARDS.items():
                if name in cat_data["cards"]:
                    detected_categories.setdefault(cat_name, []).append(name)
                    scanned_salt_cards[name] = max(scanned_salt_cards.get(name, 0.0), cat_data["base_salt"])
                else:
                    for kw in cat_data["keywords"]:
                        if kw in text:
                            detected_categories.setdefault(cat_name, []).append(name)
                            scanned_salt_cards[name] = max(scanned_salt_cards.get(name, 0.0), cat_data["base_salt"] * 0.8)
                            break

        # Incorporate external high-salt cards if available
        if ext_salt and ext_salt.high_salt_cards:
            for s_card in ext_salt.high_salt_cards:
                scanned_salt_cards[s_card.card_name] = max(
                    scanned_salt_cards.get(s_card.card_name, 0.0), s_card.salt_score
                )

        # Sort and construct high_salt_cards
        sorted_salty = sorted(scanned_salt_cards.items(), key=lambda x: x[1], reverse=True)
        for idx, (c_name, s_val) in enumerate(sorted_salty[:15], start=1):
            high_salt_cards.append(
                SaltCardDetail(
                    card_name=c_name,
                    salt_score=round(s_val, 2),
                    rank=idx,
                    oracle_id=card_records.get(c_name, {}).get("oracle_id"),
                )
            )

        for cat_name, cat_cards in detected_categories.items():
            salt_categories.append(
                SaltCategoryBreakdown(
                    category=cat_name,
                    count=len(cat_cards),
                    cards=list(set(cat_cards)),
                    description=SALT_CATEGORY_CARDS[cat_name]["description"],
                )
            )

        # Overall deck salt score (0.0 to 100.0 scale)
        if ext_salt and ext_salt.score is not None:
            deck_salt_score = max(0.0, min(100.0, float(ext_salt.score)))
        else:
            # Calculate composite salt score from detected cards
            sum_salt = sum(scanned_salt_cards.values())
            # Scale sum into a 0-100 rating (sum of ~25 is high salt ~50 score)
            deck_salt_score = min(100.0, sum_salt * 3.5)

        if deck_salt_score >= 65.0:
            salt_tier = "Table Hazard"
            playfeel_label = "Oppressive"
            warnings.append("High salt rating: Deck includes heavy resource denial, mass land destruction, or lockout cards.")
        elif deck_salt_score >= 45.0:
            salt_tier = "High Salt"
            playfeel_label = "High-Power / Spiky"
            warnings.append("Moderate-to-high salt: Expect scrutiny in casual pods regarding high-interaction staples.")
        elif deck_salt_score >= 20.0:
            salt_tier = "Moderate Salt"
            playfeel_label = "Interactive Casual"
        else:
            salt_tier = "Low Salt"
            playfeel_label = "Casual Friendly"

        # Power level estimation (1.0 to 10.0 scale)
        if ext_power and ext_power.score is not None:
            power_estimate = max(1.0, min(10.0, float(ext_power.score)))
            power_tier = ext_power.tier or POWER_SCALE_TIER_MAP.get(int(round(power_estimate)), "focused")
        else:
            # Estimate from fast mana, salt, and curve
            fast_mana_count = len([c for c in card_records.keys() if c in ("Mana Crypt", "Sol Ring", "Mana Vault", "Mox Diamond", "Chrome Mox")])
            mld_count = len(detected_categories.get("Mass Land Destruction", []))
            combo_count = len(detected_categories.get("Two-Card Combos & Instant Wins", []))

            base_power = 6.0
            if fast_mana_count >= 3:
                base_power += 2.0
            elif fast_mana_count >= 1:
                base_power += 0.5

            if combo_count >= 1:
                base_power += 1.0
            if mld_count >= 1:
                base_power += 0.5

            power_estimate = max(1.0, min(10.0, round(base_power, 1)))
            power_tier = POWER_SCALE_TIER_MAP.get(int(round(power_estimate)), "focused").capitalize()

        # Intent power alignment
        target_scale = intent.target_power_level.scale
        target_tier = intent.target_power_level.tier.capitalize()
        power_diff = abs(power_estimate - target_scale)

        if power_diff <= 1.0:
            intent_alignment_desc = f"Power level estimate ({power_estimate}) aligns with user target {target_scale} ({target_tier})."
        elif power_estimate > target_scale:
            intent_alignment_desc = (
                f"Power warning: Deck estimate ({power_estimate}) exceeds user target {target_scale} ({target_tier}). "
                f"May be overpowered for the intended pod."
            )
            warnings.append(intent_alignment_desc)
        else:
            intent_alignment_desc = (
                f"Power warning: Deck estimate ({power_estimate}) falls below user target {target_scale} ({target_tier}). "
                f"Consider upgrading ramp and synergy engines."
            )

        # Narrative summary
        summary = (
            f"Playfeel is classified as '{playfeel_label}' ({salt_tier}) with an overall salt score of {round(deck_salt_score, 1)}. "
            f"Estimated power level is {round(power_estimate, 1)}/10 ({power_tier})."
        )
        if high_salt_cards:
            top_cards_str = ", ".join(c.card_name for c in high_salt_cards[:3])
            summary += f" Key salt contributors include: {top_cards_str}."

        return PlayfeelAnalysisBreakdown(
            salt_score=round(deck_salt_score, 1),
            salt_tier=salt_tier,
            playfeel_label=playfeel_label,
            high_salt_cards=high_salt_cards,
            salt_categories=salt_categories,
            power_level_estimate=round(power_estimate, 1),
            power_tier=power_tier,
            intent_power_alignment=intent_alignment_desc,
            playfeel_summary=summary,
            warnings=warnings,
        )

    # -------------------------------------------------------------------------
    # 5. Intent Alignment & Synthesis
    # -------------------------------------------------------------------------

    def _evaluate_intent_alignment(
        self,
        intent: UserDeckIntent,
        theme: ThemeAnalysisBreakdown,
        curve: CurveAnalysisBreakdown,
        cohesion: CohesionAnalysisBreakdown,
        playfeel: PlayfeelAnalysisBreakdown,
    ) -> IntentAlignmentReport:
        """
        Compare analysis verdicts against all components of UserDeckIntent.
        """
        details: List[str] = []
        conflicts: List[str] = []

        # 1. Power level alignment
        target_scale = intent.target_power_level.scale
        power_aligned = abs(playfeel.power_level_estimate - target_scale) <= 1.5
        if power_aligned:
            details.append(f"Power level {playfeel.power_level_estimate} matches target {target_scale}.")
        else:
            conflicts.append(f"Power mismatch: Deck power {playfeel.power_level_estimate} diverges from target {target_scale}.")

        # 2. Archetype alignment
        archetype_aligned = theme.score >= 50.0 and len(theme.excluded_archetype_violations) == 0
        if archetype_aligned:
            details.append(f"Archetype alignment for '{theme.primary_archetype}' scored {theme.primary_archetype_score}%.")
        else:
            if theme.excluded_archetype_violations:
                violating_names = ", ".join(v["card_name"] for v in theme.excluded_archetype_violations)
                conflicts.append(f"Excluded archetype violation: Cards [{violating_names}] violate user exclusions.")
            if theme.score < 50.0:
                conflicts.append(f"Low archetype alignment ({theme.score}%) for requested '{theme.primary_archetype}'.")

        # 3. Win condition alignment
        pref_wincon = (intent.preferred_win_conditions.primary or "combat").lower()
        disliked_wincons = [d.lower() for d in intent.preferred_win_conditions.disliked_or_excluded]
        wincon_aligned = True

        # If user dislikes combo, check if combo cards or two-card combos were detected
        if "combo" in disliked_wincons:
            combo_cards = [c for c in playfeel.salt_categories if "combo" in c.category.lower()]
            if combo_cards and any(c.count > 0 for c in combo_cards):
                wincon_aligned = False
                conflicts.append("User excluded 'combo' win condition, but infinite or two-card combos were detected.")

        if wincon_aligned:
            details.append(f"Primary win condition '{pref_wincon}' is supported.")

        # 4. Budget alignment
        budget_aligned = True
        if intent.budget_constraints.max_card_price is not None:
            details.append(f"Budget constraint: Maximum card price set to {intent.budget_constraints.max_card_price} {intent.budget_constraints.currency}.")

        overall_aligned = power_aligned and archetype_aligned and wincon_aligned and budget_aligned
        alignment_score = (
            (100.0 if power_aligned else 50.0) * 0.35 +
            (theme.score) * 0.35 +
            (100.0 if wincon_aligned else 40.0) * 0.20 +
            (100.0 if budget_aligned else 50.0) * 0.10
        )

        return IntentAlignmentReport(
            is_aligned=overall_aligned,
            power_aligned=power_aligned,
            archetype_aligned=archetype_aligned,
            wincon_aligned=wincon_aligned,
            budget_aligned=budget_aligned,
            alignment_score=round(max(0.0, min(100.0, alignment_score)), 1),
            details=details,
            conflicts=conflicts,
        )

    def _synthesize_report(
        self,
        theme: ThemeAnalysisBreakdown,
        curve: CurveAnalysisBreakdown,
        cohesion: CohesionAnalysisBreakdown,
        playfeel: PlayfeelAnalysisBreakdown,
        alignment: IntentAlignmentReport,
    ) -> Tuple[float, List[str], List[str]]:
        """
        Calculate final composite score and generate key findings and warnings.
        """
        # Weighted overall composite evaluation score (0-100)
        # Theme: 25%, Curve: 25%, Cohesion: 30%, Intent Alignment: 20%
        composite_score = (
            (theme.score * 0.25) +
            (curve.score * 0.25) +
            (cohesion.score * 0.30) +
            (alignment.alignment_score * 0.20)
        )

        key_findings: List[str] = [
            f"Archetype Alignment: {theme.consistency_tier} ({round(theme.score, 1)}%) for primary theme '{theme.primary_archetype}'.",
            f"Curve & Speed: Average CMC {curve.average_cmc} with {curve.total_ramp} ramp sources ({curve.speed_tier} speed).",
            f"Deck Cohesion: {cohesion.cohesion_tier} with {len(cohesion.synergy_clusters)} detected functional engine(s).",
            f"Playfeel: {playfeel.playfeel_label} ({playfeel.salt_tier}, salt score {playfeel.salt_score}), estimated power {playfeel.power_level_estimate}/10.",
        ]

        warnings: List[str] = []
        warnings.extend(playfeel.warnings)
        warnings.extend(curve.recommendations)
        warnings.extend(alignment.conflicts)

        return composite_score, key_findings, warnings


# =============================================================================
# Top-level Functional Facades & Singleton Instance
# =============================================================================

default_analysis_engine = DeckAnalysisEngine()


def analyze_deck(
    deck: Union[ConsolidatedDeckIntake, DecklistInput, Dict[str, Any], str],
    intent: Optional[Union[UserDeckIntent, Dict[str, Any]]] = None,
    external_metrics: Optional[Union[DeckAnalyticsResult, Dict[str, Any]]] = None,
    card_metadata: Optional[Dict[str, Any]] = None,
    engine: Optional[DeckAnalysisEngine] = None,
) -> DeckAnalysisReport:
    """
    Evaluate a deck intake payload using the DeckAnalysisEngine.

    Args:
        deck: ConsolidatedDeckIntake / DeckIntakeResult, DecklistInput, or raw decklist.
        intent: Optional UserDeckIntent or dict overriding intake user intent.
        external_metrics: Optional DeckAnalyticsResult or dict from external providers.
        card_metadata: Optional dictionary mapping card name to CardMetadata / dict.
        engine: Optional DeckAnalysisEngine instance (defaults to default_analysis_engine).

    Returns:
        DeckAnalysisReport with theme, curve, cohesion, playfeel, and alignment scores.
    """
    active_engine = engine or default_analysis_engine
    return active_engine.analyze(
        deck=deck,
        intent=intent,
        external_metrics=external_metrics,
        card_metadata=card_metadata,
    )


__all__ = [
    "ArchetypeMatch",
    "ThemeAnalysisBreakdown",
    "CurveAnalysisBreakdown",
    "SynergyCluster",
    "CohesionAnalysisBreakdown",
    "SaltCategoryBreakdown",
    "PlayfeelAnalysisBreakdown",
    "IntentAlignmentReport",
    "DeckAnalysisReport",
    "DeckAnalysisEngine",
    "analyze_deck",
    "default_analysis_engine",
    "parse_mana_cost",
    "SALT_CATEGORY_CARDS",
    "KNOWN_STAPLE_SALT",
    "KNOWN_RAMP_CARDS",
    "ARCHETYPE_PATTERNS",
]
