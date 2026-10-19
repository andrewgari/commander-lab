"""Auto Tag engine — deterministic two-pass tagging for MTG decks.

Pass 1: regex rules (§3) → base evidence scores per tag per card.
Pass 1.5: theme support (§5) → active deck themes.
Pass 2: context rules (§4.2) → score bumps, theme gating (§6.1),
        selection (§6.3), filler cap (§6.2).

Deck-level tags (§7.1) and health hints (§7.2) follow.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Config (§9)
# ---------------------------------------------------------------------------

@dataclass
class AutoTagConfig:
    version: str = "autotag-v1"
    evidence_threshold: float = 2
    score_cap: int = 4
    max_tags_card: int = 3
    max_tags_land: int = 2
    filler_max_total: int = 2
    max_deck_tags: int = 5
    max_kindred_themes: int = 2
    theme_thresholds: dict = field(default_factory=lambda: {
        "general_pct": 0.12,
        "type_theme_pct": 0.20,
        "commander_pct": 0.06,
        "kindred_pct": 0.30,
        "kindred_named_by_commander_pct": 0.20,
        "general_min": 3,
        "commander_min": 2,
        "type_only_weight": 0.5,
    })
    mana_fixing_nonland_min_colors: int = 3
    health: dict = field(default_factory=lambda: {
        "min_deck_size": 90,
        "floors": {"ramp": 10, "card_advantage": 7, "interaction": 10, "board_wipe": 2, "finisher": 2},
        "big_commander_mv": 5,
        "big_commander_ramp_bonus": 2,
        "commander_does_job": -3,
        "tokens_wipe_floor": 0,
    })
    llm: dict = field(default_factory=lambda: {"enabled": False})

DEFAULT_CONFIG = AutoTagConfig()


# ---------------------------------------------------------------------------
# Tag taxonomy (§1)
# ---------------------------------------------------------------------------

FUNCTION_TAGS = [
    "ramp", "card_advantage", "removal", "board_wipe", "counterspell",
    "finisher", "tutor", "recursion", "protection", "mana_fixing",
]

THEME_TAGS = [
    "lands_matter", "tokens", "counters", "sacrifice", "graveyard",
    "spellslinger", "artifacts", "enchantments", "lifegain", "voltron", "blink",
]

ALL_TAGS = set(FUNCTION_TAGS) | set(THEME_TAGS)


# PRIORITY order (§6.3)
PRIORITY_ORDER: list[str] = (
    ["ramp", "card_advantage", "removal", "board_wipe", "counterspell",
     "finisher", "tutor", "protection", "recursion", "mana_fixing"]
    + ["kindred:*", "tokens", "sacrifice", "artifacts", "enchantments",
       "counters", "graveyard", "spellslinger", "lands_matter", "lifegain",
       "voltron", "blink"]
)

def _tag_priority(tag: str) -> int:
    if tag.startswith("kindred:"):
        return PRIORITY_ORDER.index("kindred:*")
    if tag not in PRIORITY_ORDER:
        return len(PRIORITY_ORDER) + 1
    return PRIORITY_ORDER.index(tag)


# Irregular plural map for kindred (§5.3)
_IRREGULAR_PLURAL: dict[str, str] = {
    "elf": "elves",
    "dwarf": "dwarves",
    "wolf": "wolves",
}

_IRREGULAR_PLURAL_REVERSE: dict[str, str] = {v: k for k, v in _IRREGULAR_PLURAL.items()}

def _plural_of(subtype: str) -> str:
    return _IRREGULAR_PLURAL.get(subtype, f"{subtype}s")

_CREATURE_SUBTYPES: set[str] = {
    "advisor", "aetherborn", "alien", "ally", "angel", "antelope", "ape", "archer",
    "archon", "armadillo", "army", "artificer", "assassin", "assembly-worker",
    "atog", "aurochs", "automaton", "avatar", "azra", "badger", "balloon", "barbarian",
    "bard", "basilisk", "bat", "bear", "beast", "beeble", "beholder", "berserker",
    "bird", "blinkmoth", "boar", "bringer", "brushwagg", "camarid", "camel", "caribou",
    "carrier", "cat", "centaur", "cephalid", "chameleon", "chimera", "citizen", "clamfolk",
    "cleric", "clown", "cockatrice", "construct", "coward", "coyote", "crab", "crocodile",
    "cyborg", "cyclops", "dauthi", "demigod", "demon", "deserter", "detective", "devil",
    "dinosaur", "djinn", "dog", "dragon", "drake", "dreadnought", "drone", "druid", "dryad",
    "dwarf", "efreet", "egg", "elder", "eldrazi", "elemental", "elephant", "elf", "elk",
    "eye", "faerie", "ferret", "fish", "flagbearer", "fox", "fractal", "frog", "fungus",
    "gargoyle", "germ", "giant", "gith", "glimmer", "gnoll", "gnome", "goat", "goblin",
    "god", "golem", "gorgon", "graveborn", "gremlin", "griffin", "guest", "hag", "halfling",
    "hamster", "harpy", "hellion", "hippo", "hippogriff", "homarid", "homunculus", "hornet",
    "horror", "horse", "human", "hydra", "hyena", "illusion", "imp", "incarnation", "inkling",
    "inquisitor", "insect", "jackal", "jellyfish", "juggernaut", "kavu", "kirin", "kithkin",
    "knight", "kobold", "kor", "kraken", "lamia", "lammasu", "leech", "leviathan", "lhurgoyf",
    "licid", "lizard", "manticore", "masticore", "mercenary", "merfolk", "metathran", "minion",
    "minotaur", "mite", "mole", "monger", "mongoose", "monk", "monkey", "moonfolk", "mount",
    "mouse", "mutant", "myr", "mystic", "naga", "nautilus", "necromite", "nephilim", "nightmare",
    "nightstalker", "ninja", "noble", "noggle", "nomad", "nymph", "octopus", "ogre", "ooze",
    "orb", "orc", "orgg", "otter", "ouph", "ox", "oyster", "pangolin", "peasant", "pegasus",
    "pentavite", "performer", "pest", "phelddagrif", "phoenix", "phyrexian", "pilot", "pincher",
    "pirate", "plant", "praetor", "primordial", "processor", "ranger", "rat", "rebel", "reflection",
    "rhino", "rigger", "robot", "rogue", "sable", "salamander", "samurai", "sand", "saproling",
    "satyr", "scarecrow", "scientist", "scion", "scorpion", "scout", "sculptor", "serf", "serpent",
    "servo", "shade", "shaman", "shapeshifter", "shark", "sheep", "siren", "skeleton", "slith",
    "sliver", "slug", "snake", "soldier", "soltari", "spawn", "specter", "spellshaper", "sphinx",
    "spider", "spike", "spirit", "splinter", "sponge", "squid", "squirrel", "starfish", "surrakar",
    "survivor", "tentacle", "tetravite", "thalakos", "thopter", "thrull", "tiefling", "time-lord",
    "treefolk", "trilobite", "triskelavite", "troll", "turtle", "tyranid", "unicorn", "vampire",
    "vampyre", "vedalken", "viashino", "villain", "volver", "vulpine", "wall", "walrus", "warlock",
    "warrior", "weird", "werewolf", "whale", "wizard", "wolf", "wolverine", "wombat", "worm",
    "wraith", "wurm", "yeti", "zombie", "zubera",
}


# ---------------------------------------------------------------------------
# Rule catalog (§3)
# ---------------------------------------------------------------------------

def _compile_rules() -> dict[str, list[dict]]:
    """Return {tag_name: [compiled_rule]}."""
    rules_raw = [
        # ramp
        {"tag": "ramp", "id": "RAMP-1", "w": 2, "scope": "nonland", "re": r"\{t\}(, [^:]*)?: add ", "not_re": r"\bloses all (other )?(card types and )?abilities"},
        {"tag": "ramp", "id": "RAMP-2", "w": 2, "scope": "nonland", "re": r"search your library for [^.]*\bland cards?\b[^.]*(onto|on) the battlefield"},
        {"tag": "ramp", "id": "RAMP-3", "w": 2, "scope": "nonland", "re": r"you may play (an )?additional lands?"},
        {"tag": "ramp", "id": "RAMP-4", "w": 1, "scope": "nonland", "re": r"\b(spells?|[a-z]+ spells) you cast costs? \{\d+\} less"},
        {"tag": "ramp", "id": "RAMP-5", "w": 2, "scope": "nonland", "re": r"\bcreate[^.]*\btreasure tokens?"},
        {"tag": "ramp", "id": "RAMP-6", "w": 1, "scope": "nonland", "re": r"\bsacrifice (a|an|another|two|three|x) [^:]*: add \{"},
        {"tag": "ramp", "id": "RAMP-7", "w": 1, "scope": "instant_or_sorcery", "re": r"\badd \{"},
        {"tag": "ramp", "id": "RAMP-8", "w": 2, "scope": "nonland", "kind": "REP", "re": r"^(whenever|at the beginning)[^.]*, add "},
        {"tag": "ramp", "id": "RAMP-9", "w": 2, "scope": "nonland", "re": r"\bwhenever enchanted [a-z]+ is tapped for mana[^.]*\badds? (an )?additional"},
        # card_advantage
        {"tag": "card_advantage", "id": "CA-1", "w": 2, "re": r"(^|[.,:] |\bthen |\byou (may )?)draw (two|three|four|five|six|seven|x|that many|\d+) cards"},
        {"tag": "card_advantage", "id": "CA-2", "w": 2, "kind": "REP", "re": r"(^|[.,:] |\bthen |\byou (may )?)draw a card"},
        {"tag": "card_advantage", "id": "CA-3", "w": 2, "re": r"\bdraw cards equal to"},
        {"tag": "card_advantage", "id": "CA-4", "w": 2, "all_of": [r"exile the top", r"you may (play|cast)"]},
        {"tag": "card_advantage", "id": "CA-5", "w": 1, "kind": "ONESHOT", "re": r"(^|[.,:] |\bthen |\byou (may )?)draw a card"},
        {"tag": "card_advantage", "id": "CA-7", "w": 1, "kind": "ONESHOT", "re": r"\blook at the top (two|three|four|five|six|seven|x|\d+) cards of your library"},
        {"tag": "card_advantage", "id": "CA-9", "w": 1, "kind": "ONESHOT", "re": r"^when ~ enters[^.]*, draw a card"},
        {"tag": "card_advantage", "id": "CA-6", "w": 1, "any_of": [r"\b(scry|surveil) \d"], "or_all_of": [r"(look at|reveal) the top", r"into your hand"]},
        # removal
        {"tag": "removal", "id": "RM-1", "w": 2, "re": r"\b(destroy|exile) (up to \w+ )?(another )?target [a-z ,-]*?(creature|artifact|enchantment|planeswalker|permanent|battle)s?\b(?! you (control|own))"},
        {"tag": "removal", "id": "RM-2", "w": 2, "re": r"\bdeals? (x|[2-9]|\d{2,}) damage to (any target|target creature|target [a-z ]*creature|target planeswalker)"},
        {"tag": "removal", "id": "RM-3", "w": 1, "re": r"\bdeals? 1 damage to (any target|(another )?target creature)"},
        {"tag": "removal", "id": "RM-9", "w": 2, "kind": "REP", "re": r"\bdeals? 1 damage to (any target|(another )?target creature)"},
        {"tag": "removal", "id": "RM-10", "w": 2, "re": r"\benchanted creature (loses all (abilities|other abilities)|is a [a-z ]*creature with base power and toughness|can't (attack|block))"},
        {"tag": "removal", "id": "RM-4", "w": 2, "re": r"\btarget creature [^.]*gets -(x|\d+)/-(x|\d+)"},
        {"tag": "removal", "id": "RM-5", "w": 2, "all_of": [r"\btarget\b", r"(shuffles? it into (its owner's|their) library|on the (top|bottom) of (its owner's|their) library)"]},
        {"tag": "removal", "id": "RM-6", "w": 2, "re": r"\b(target player|target opponent|each opponent) sacrifices (a|an|one|two)\b"},
        {"tag": "removal", "id": "RM-7", "w": 2, "re": r"\bfights? (target|another target|up to one target)|deals damage equal to its power to (target|another target|any target)"},
        {"tag": "removal", "id": "RM-9", "w": 2, "re": r"\benchanted (creature|permanent|land|artifact)\b[^.]*\bloses all (other )?(card types and )?abilities"},
        {"tag": "removal", "id": "RM-8", "w": 1, "re": r"\breturn (up to one )?target (creature|nonland permanent|permanent|artifact|enchantment)(?! you control)[^.]*to (its|their) owners?'?s?'?"},
        # board_wipe
        {"tag": "board_wipe", "id": "BW-1", "w": 2, "re": r"\b(destroy|exile) (all|each) (other )?(nonland |nontoken |non[a-z]+ )?(creatures?|artifacts?|enchantments?|permanents?|planeswalkers?)\b"},
        {"tag": "board_wipe", "id": "BW-2", "w": 2, "re": r"\bdeals? (x|\d+) damage to each (other )?creature"},
        {"tag": "board_wipe", "id": "BW-3", "w": 2, "re": r"\b(all|each) (other )?([a-z-]+ )?creatures?[^.]* gets? -(x|\d+)/-(x|\d+)"},
        {"tag": "board_wipe", "id": "BW-4", "w": 2, "re": r"\breturn all [^.]*to (their|its) owners?'?s?'?"},
        # counterspell
        {"tag": "counterspell", "id": "CS-1", "w": 2, "re": r"\bcounter target [a-z ]*?(spell|ability)"},
        # tutor
        {"tag": "tutor", "id": "TU-1", "w": 2, "re": r"\bsearch your library for (a|an|up to (one|two|three|x)) (?![^.]*\b(land|forest|island|swamp|mountain|plains)\b)[^.,]*?cards?\b"},
        # recursion
        {"tag": "recursion", "id": "RC-1", "w": 2, "all_of": [r"\bcards? (from|in) (your|a) graveyard", r"\b(return|put)\b", r"(to|onto) the battlefield|(to|into) your hand"]},
        {"tag": "recursion", "id": "RC-2", "w": 2, "re": r"\byou may (cast|play) [^.]*from your graveyard"},
        # protection
        {"tag": "protection", "id": "PR-1", "w": 2, "re": r"\b(equipped creature|enchanted creature|(other )?creatures you control|(other )?permanents you control|artifacts you control|target (creature|permanent) you control|your commander|you) (has|have|gains?|gets?) [^.]*\b(hexproof|shroud|indestructible|protection from)"},
        {"tag": "protection", "id": "PR-2", "w": 2, "re": r"\bprevent all combat damage|\b(target|each|all) [a-z ]*you control phases? out|\byou (have|gain) hexproof"},
        {"tag": "protection", "id": "PR-3", "w": 2, "re": r"\bcounter target [^.]*spell[^.]*that targets [^.]*you control"},
        # finisher
        {"tag": "finisher", "id": "FN-1", "w": 2, "re": r"\byou win the game|\b(each|target) opponents? loses? the game"},
        {"tag": "finisher", "id": "FN-2", "w": 2, "re": r"\badditional combat phase|\bextra combat"},
        {"tag": "finisher", "id": "FN-3", "w": 2, "keywords": ["infect", "toxic"], "or_re": r"\bpoison counters?"},
        {"tag": "finisher", "id": "FN-4", "w": 2, "re": r"\bcreatures you control [^.]*get \+(x|\d+)/\+(x|\d+)[^.]*until end of turn"},
        {"tag": "finisher", "id": "FN-5", "w": 2, "re": r"\bdeals? (x|[3-9]|\d{2,}) damage to each opponent|\beach opponent loses (x|[3-9]|\d{2,}) life"},
        {"tag": "finisher", "id": "FN-7", "w": 2, "keywords": ["storm"], "or_re": r"^storm\b"},
        {"tag": "finisher", "id": "FN-6", "w": 1, "kind": "REP", "re": r"\bdeals? (x|\d+) damage to (each opponent|any target|target player|target opponent)|\b(each opponent|target player|target opponent) loses (x|\d+) life"},
        # lands_matter
        {"tag": "lands_matter", "id": "LM-1", "w": 2, "re": r"^whenever (a|one or more) lands? (you control )?enters?", "or_keywords": ["landfall"]},
        {"tag": "lands_matter", "id": "LM-2", "w": 2, "re": r"\byou may play (an )?additional land|\bplay lands from your graveyard|\bland cards? from your graveyard|\bfor each land you control|\bsacrifice a land\b"},
        # tokens
        {"tag": "tokens", "id": "TK-1", "w": 2, "kind": "REP", "re": r"\bcreate [^.]*\bcreature tokens?"},
        {"tag": "tokens", "id": "TK-2", "w": 2, "re": r"^whenever (a|another|one or more) (nontoken )?([a-z]+ )?creatures? you control enters?\b(?![^.]*\byou gain \d+ life)|\bfor each creature you control|\b(creature )?tokens you control"},
        {"tag": "tokens", "id": "TK-5", "w": 2, "re": r"\bif an effect would create one or more tokens|\btwice that many (of those )?tokens?\b"},
        {"tag": "tokens", "id": "TK-6", "w": 2, "re": r"^when ~ dies, create [^.]*\bcreature tokens?"},
        {"tag": "tokens", "id": "TK-3", "w": 1, "kind": "ONESHOT", "re": r"\bcreate [^.]*\bcreature tokens?"},
        {"tag": "tokens", "id": "TK-4", "w": 1, "kind": "STATIC", "re": r"\b(?<!artifact )(?<!enchantment )(?<!nonartifact )(?<!legendary )(other )?(?!(artifact|enchantment|nonartifact|legendary) )([a-z]+ )?(?<!artifact )(?<!enchantment )creatures you control get \+\d+/\+\d+"},
        # counters
        {"tag": "counters", "id": "CT-1", "w": 2, "any_of": [r"\+1/\+1 counters? would be put", r"\bproliferate", r"\bfor each \+1/\+1 counter", r"\bwith (a|one or more) \+1/\+1 counters? on (it|them)"], "or_rep_re": r"\bput (a|one|two|three|x|that many|[a-z]+) \+1/\+1 counters? on"},
        {"tag": "counters", "id": "CT-2", "w": 1, "kind": "ONESHOT", "re": r"\bput (a|one|two|three|x|[a-z]+) \+1/\+1 counters? on|\benters with [^.]*\+1/\+1 counters?", "or_keywords": ["mentor", "evolve", "outlast", "bolster", "adapt", "modular", "graft", "training", "backup", "riot"]},
        # sacrifice
        {"tag": "sacrifice", "id": "SAC-1", "w": 2, "re": r"^[^:]*\bsacrifice (a|an|another|two|three|x|one or more) [^:]*:"},
        {"tag": "sacrifice", "id": "SAC-2", "w": 2, "re": r"\bwhenever [^.]*\b(a|another|one or more)( nontoken)?( [a-z]+)? creatures?( you control)? (dies|die|is put into a graveyard)|\bwhenever you sacrifice"},
        {"tag": "sacrifice", "id": "SAC-3", "w": 1, "scope": "creature", "re": r"\bsacrifice ~[^:]*:"},
        # graveyard
        {"tag": "graveyard", "id": "GY-1", "w": 2, "all_of": [r"your library", r"into your graveyard"], "or_re": r"\bmills? (a|one|two|three|four|five|x|\d+|that many) cards?(?![^.]*(target (player|opponent)|each opponent))", "or_keywords": ["surveil"]},
        {"tag": "graveyard", "id": "GY-2", "w": 2, "keywords": ["flashback", "escape", "unearth", "embalm", "eternalize", "disturb", "dredge", "delve", "jump-start", "retrace", "scavenge", "encore", "aftermath"]},
        {"tag": "graveyard", "id": "GY-3", "w": 2, "re": r"\bfor each [^.]*cards? in your graveyard|\b\w+ or more [^.]*cards? in your graveyard", "or_keywords": ["threshold", "delirium"]},
        # spellslinger
        {"tag": "spellslinger", "id": "SS-1", "w": 2, "re": r"\bwhenever you cast (an|a|your first) (instant|sorcery|noncreature)|\binstant (and|or) sorcery spells?\b|\bcopy target (instant|sorcery)", "or_keywords": ["magecraft"]},
        {"tag": "spellslinger", "id": "SS-T", "w": 1, "type_has": ["Instant", "Sorcery"]},
        # artifacts
        {"tag": "artifacts", "id": "AR-1", "w": 2, "re": r"\bartifacts? you control|\bartifact spells?|\bwhenever (an|another|one or more) (nontoken )?artifacts?\b|\bfor each artifact|\bsacrifice (a|an|another|two|three|x) (nontoken )?artifacts?|\buntapped artifacts?|\bartifact creatures? you control"},
        {"tag": "artifacts", "id": "AR-2", "w": 2, "re": r"\bcreate [^.]*\bartifact [^.]*tokens?"},
        {"tag": "artifacts", "id": "AR-T", "w": 1, "type_has": ["Artifact"]},
        {"tag": "artifacts", "id": "AR-X", "w": 0, "re": r"\bcreate [^.]*\b(treasure|clue|food|blood|map|gold|powerstone|incubator) tokens?|\binvestigate\b"},
        # enchantments
        {"tag": "enchantments", "id": "EN-1", "w": 2, "re": r"\benchantments? you control|\benchantment spells?|\bwhenever (an|another) (nontoken )?enchantment|\bfor each enchantment|\baura spells?", "or_keywords": ["constellation"]},
        {"tag": "enchantments", "id": "EN-T", "w": 1, "type_has": ["Enchantment"]},
        # lifegain
        {"tag": "lifegain", "id": "LG-1", "w": 2, "re": r"\bwhenever you gain life|\bif you would gain life|\byou gain life for the first time|\bfor each 1 life you gained|\bcreatures you control have lifelink", "or_rep_re": r"\byou gain (x|\d+) life"},
        {"tag": "lifegain", "id": "LG-2", "w": 1, "kind": "ONESHOT", "re": r"\byou gain (x|\d+) life", "or_keywords": ["lifelink"]},
        # voltron
        {"tag": "voltron", "id": "VO-1", "w": 2, "re": r"\b(equipped|enchanted) creature gets \+(x|\d+)/\+|\bequipment you control|\bwhenever an equipment|\bequip \{0\}|\bequip abilities|\bfor each (aura|equipment)|\battach (target|an?) equipment"},
        {"tag": "voltron", "id": "VO-T", "w": 1, "type_has": ["Equipment", "Aura"]},
        # blink
        {"tag": "blink", "id": "BK-1", "w": 2, "re": r"\bexile (up to (one|two|x) )?(another )?target [a-z ]*(creature|permanent|artifact)s? you (control|own)[^.]*,? then return (it|them|that card|those cards) to the battlefield"},
        {"tag": "blink", "id": "BK-2", "w": 1, "scope": "creature", "re": r"^when ~ enters"},
    ]

    compiled: dict[str, list[dict]] = {}
    for r in rules_raw:
        tag = r["tag"]
        entry = dict(r)
        if "re" in entry:
            entry["re_compiled"] = re.compile(entry.pop("re"), re.IGNORECASE)
        if "or_re" in entry:
            entry["or_re_compiled"] = re.compile(entry.pop("or_re"), re.IGNORECASE)
        if "or_rep_re" in entry:
            entry["or_rep_re_compiled"] = re.compile(entry.pop("or_rep_re"), re.IGNORECASE)
        compiled.setdefault(tag, []).append(entry)
    return compiled

_RULES = _compile_rules()


# ---------------------------------------------------------------------------
# Normalisation (§2.2)
# ---------------------------------------------------------------------------

def normalise(card: dict) -> tuple[str, list[str], str]:
    """Return (normalised_text, paragraphs, card_name) for scoring."""
    # N1: join all faces' oracle text
    faces = card.get("card_faces", [])
    if faces:
        oracle_parts = [f.get("oracle_text", "") or "" for f in faces]
        text = "\n".join(oracle_parts)
    else:
        text = card.get("oracle_text", "") or ""

    # N2: lowercase
    text = text.lower()

    # N3: strip reminder text (critical — prevents Treasure reminder from
    # faking Ramp/Sacrifice hits)
    text = re.sub(r'\([^)]*\)', '', text)

    # N4: replace self-references with ~
    name = card.get("name", "")
    short_name = name.split(",")[0]
    for repl in [name.lower(), short_name.lower(), "this creature", "this artifact",
                 "this enchantment", "this land", "this card", "this spell",
                 "this permanent", "this equipment", "this aura", "this vehicle",
                 "this token"]:
        if repl:
            text = text.replace(repl, "~")

    # N5: split into paragraphs; bullet paragraphs append to previous
    raw_paras = text.split("\n")
    paragraphs: list[str] = []
    for p in raw_paras:
        p = p.strip()
        if not p:
            continue
        if p.startswith("•"):
            if paragraphs:
                paragraphs[-1] += " " + p[1:].strip()
            continue
        paragraphs.append(p)

    # N6: strip leading ability word
    cleaned: list[str] = []
    for p in paragraphs:
        m = re.match(r"^[a-z' -]+ — ", p, re.IGNORECASE)
        if m:
            p = p[m.end():]
        cleaned.append(p)
    paragraphs = cleaned

    # N7: collapse whitespace
    paragraphs = [re.sub(r'\s+', ' ', p) for p in paragraphs]

    return text, paragraphs, name


# ---------------------------------------------------------------------------
# Paragraph kind (§2.3)
# ---------------------------------------------------------------------------

def _is_rep(paragraph: str) -> bool:
    p = paragraph.strip().lower()
    if p.startswith("whenever") or p.startswith("at the beginning"):
        return True
    if re.match(r'^[^:"]*:', p):
        before_colon = p.split(":")[0]
        if "{" in before_colon or any(w in before_colon for w in
            ("sacrifice", "tap", "untap", "discard", "exile", "pay", "remove")):
            return True
    return False

def _is_oneshot(paragraph: str) -> bool:
    return not _is_rep(paragraph)

def _is_static(paragraph: str, card: dict) -> bool:
    """A ONESHOT paragraph on a permanent with no 'when' and no 'until end of turn'."""
    p = paragraph.strip().lower()
    if _is_oneshot(paragraph) and not _is_land(card):
        if "when" not in p and "until end of turn" not in p:
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_land(card: dict) -> bool:
    tl = (card.get("type_line") or card.get("type") or "").lower()
    main_type = tl.split("—")[0] if "—" in tl else tl.split("-")[0]
    return "land" in main_type.split()

def _is_nonland(card: dict) -> bool:
    return not _is_land(card)

def _card_type(card: dict) -> str:
    return (card.get("type_line") or card.get("type") or "").lower()

def _has_keyword(card: dict, kw: str) -> bool:
    return kw.lower() in [k.lower() for k in card.get("keywords", [])]

def _matches_any(p: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if re.search(pat, p, re.IGNORECASE):
            return True
    return False

def _has_type(card: dict, typename: str) -> bool:
    tl = (card.get("type_line") or card.get("type") or "").lower()
    words = [w.lower() for w in re.findall(r'[a-zA-Z]+', tl)]
    return typename.lower() in words


# ---------------------------------------------------------------------------
# Pass 1: base evidence scoring (§3)
# ---------------------------------------------------------------------------

def _match_rule(rule: dict, paragraph: str, card: dict) -> bool:
    """Return True if rule fires on this paragraph (with card context)."""
    # scope check
    scope = rule.get("scope")
    if scope == "nonland" and not _is_nonland(card):
        return False
    if scope == "creature" and "creature" not in _card_type(card):
        return False
    if scope == "instant_or_sorcery":
        ct = _card_type(card)
        if "instant" not in ct and "sorcery" not in ct:
            return False

    # kind check
    kind = rule.get("kind")
    if kind == "REP" and not _is_rep(paragraph):
        return False
    if kind == "ONESHOT" and not _is_oneshot(paragraph):
        return False
    if kind == "STATIC" and not _is_static(paragraph, card):
        return False

    not_re = rule.get("not_re")
    if not_re and re.search(not_re, paragraph, re.IGNORECASE):
        return False

    primary_match = False
    if "all_of" in rule:
        primary_match = all(re.search(p, paragraph, re.IGNORECASE) for p in rule["all_of"])
    elif "any_of" in rule:
        primary_match = any(re.search(p, paragraph, re.IGNORECASE) for p in rule["any_of"])
    elif "keywords" in rule:
        primary_match = any(_has_keyword(card, k) for k in rule["keywords"])
    elif "type_has" in rule:
        primary_match = any(_has_type(card, t) for t in rule["type_has"])
    elif "re_compiled" in rule:
        primary_match = rule["re_compiled"].search(paragraph) is not None

    if primary_match:
        return True

    # Check or_* alternatives
    if "or_keywords" in rule and any(_has_keyword(card, k) for k in rule["or_keywords"]):
        return True
    if "or_re_compiled" in rule and rule["or_re_compiled"].search(paragraph):
        return True
    if "or_rep_re_compiled" in rule and _is_rep(paragraph) and rule["or_rep_re_compiled"].search(paragraph):
        return True
    if "or_all_of" in rule and all(re.search(p, paragraph, re.IGNORECASE) for p in rule["or_all_of"]):
        return True

    return False


def pass1_card_score(card: dict, config: AutoTagConfig | None = None) -> dict[str, dict]:
    """Pass 1: return {tag: {score, base_rules_fired}} for a single card."""
    _, paragraphs, _ = normalise(card)
    scores: dict[str, dict] = {}

    for tag, rules in _RULES.items():
        if tag == "mana_fixing":
            continue
        total = 0
        rules_fired: list[str] = []
        for rule in rules:
            w = rule["w"]
            if w == 0:
                continue
            for para in paragraphs:
                if _match_rule(rule, para, card):
                    total += w
                    if rule["id"] not in rules_fired:
                        rules_fired.append(rule["id"])
                    break
        total = min(total, config.score_cap if config else 4)
        if total > 0:
            scores[tag] = {"score": total, "rules_fired": rules_fired}
    # An Aura that neutralises a permanent is removal, not voltron support.
    if "RM-9" in scores.get("removal", {}).get("rules_fired", []):
        scores.pop("voltron", None)
    return scores


# ---------------------------------------------------------------------------
# Kindred Candidates (§5.3)
# ---------------------------------------------------------------------------

def _get_subtypes_of_card(card: dict) -> list[str]:
    tl = (card.get("type_line") or "").lower()
    if "—" in tl:
        subs = tl.split("—")[-1].strip().split()
    elif "-" in tl:
        subs = tl.split("-")[-1].strip().split()
    else:
        subs = []
    cleaned = []
    for s in subs:
        s = re.sub(r'[^a-z]', '', s)
        if s and s not in ("creature", "legendary", "snow", "basic", "artifact", "enchantment"):
            cleaned.append(s)
    return cleaned


def get_kindred_candidates(deck: dict, nonland_cards: list[tuple[str, dict]]) -> list[str]:
    """Find candidate creature subtypes for kindred themes per §5.3."""
    candidates: set[str] = set()
    commanders = deck.get("commanders", [])
    if isinstance(commanders, str):
        commanders = [{"name": commanders}]
    elif isinstance(commanders, list) and commanders and isinstance(commanders[0], str):
        commanders = [{"name": c} for c in commanders]

    # 1. creature subtypes on any commander
    for cc in commanders:
        if isinstance(cc, dict):
            for s in _get_subtypes_of_card(cc):
                if s in _CREATURE_SUBTYPES:
                    candidates.add(s)

    # 2. creature subtypes named in a commander's oracle text
    for cc in commanders:
        if isinstance(cc, dict):
            ot = (cc.get("oracle_text") or "").lower()
            for w in re.findall(r'\b[a-z]{3,}\b', ot):
                sing = _IRREGULAR_PLURAL_REVERSE.get(w, w)
                if sing:
                    if sing.endswith("s") and not sing.endswith("ss"):
                        sing = sing[:-1]
                    if sing in _CREATURE_SUBTYPES:
                        candidates.add(sing)

    # 3. any creature subtype carried by at least 30% of N
    N = len(nonland_cards)
    if N > 0:
        counts: dict[str, int] = {}
        for _, c in nonland_cards:
            for s in _get_subtypes_of_card(c):
                if s in _CREATURE_SUBTYPES:
                    counts[s] = counts.get(s, 0) + 1
        for s, cnt in counts.items():
            if cnt / N >= 0.30:
                candidates.add(s)

    return sorted(candidates)


# ---------------------------------------------------------------------------
# Pass 1.5: deck themes (§5)
# ---------------------------------------------------------------------------

@dataclass
class ThemeInfo:
    tag: str
    support: float
    threshold: float
    path: str  # "journal", "commander", "general"
    active: bool = False
    is_commander_centered: bool = False


def compute_deck_themes(deck: dict, card_scores: dict[str, dict], config: AutoTagConfig | None = None) -> dict[str, ThemeInfo]:
    """Compute active themes for the deck from card base scores."""
    cfg = config or DEFAULT_CONFIG
    tt = cfg.theme_thresholds

    commanders = deck.get("commanders", [])
    commander_names = set()
    if isinstance(commanders, list):
        for c in commanders:
            if isinstance(c, dict):
                commander_names.add(c.get("name", "").lower())
            elif isinstance(c, str):
                commander_names.add(c.lower())
    elif isinstance(commanders, str):
        commander_names.add(commanders.lower())

    cards = deck.get("cards", [])
    nonland_cards: list[tuple[str, dict]] = []
    for c in cards:
        cname = c.get("name", "").lower() if isinstance(c, dict) else str(c).lower()
        if cname not in commander_names and not _is_land(c):
            nonland_cards.append((cname, c))

    N = len(nonland_cards)
    commander_cards = [c for c in deck.get("commanders", []) if isinstance(c, dict)]

    # Theme support computation
    theme_support: dict[str, float] = {}

    # Non-type themes: count cards with base score >= 2
    for tag in ["tokens", "counters", "sacrifice", "graveyard", "lands_matter", "lifegain", "blink"]:
        count = sum(1 for cname, _ in nonland_cards if card_scores.get(cname, {}).get(tag, {}).get("score", 0) >= 2)
        if tag == "graveyard":
            count += sum(1 for cname, _ in nonland_cards if card_scores.get(cname, {}).get("recursion", {}).get("score", 0) >= 2)
        theme_support[tag] = float(count)

    # Type themes: text rules score >= 2 + 0.5 * type-only matches
    type_theme_text_rules = {
        "artifacts": ["AR-1", "AR-2"],
        "enchantments": ["EN-1"],
        "spellslinger": ["SS-1"],
        "voltron": ["VO-1"],
    }
    for tag, text_rule_ids in type_theme_text_rules.items():
        text_count = 0
        type_count = 0
        for cname, c in nonland_cards:
            cscores = card_scores.get(cname, {})
            tag_info = cscores.get(tag, {})
            rules_fired = tag_info.get("rules_fired", [])
            if any(rid in rules_fired for rid in text_rule_ids):
                text_count += 1
            elif any(_has_type(c, t) for t in {
                "artifacts": ["Artifact"],
                "enchantments": ["Enchantment"],
                "spellslinger": ["Instant", "Sorcery"],
                "voltron": ["Equipment", "Aura"],
            }[tag]):
                type_count += 1
        theme_support[tag] = text_count + tt["type_only_weight"] * type_count

    # Kindred candidates (§5.3)
    cand_subtypes = get_kindred_candidates(deck, nonland_cards)
    kindred_support: dict[str, float] = {}
    for subtype in cand_subtypes:
        kd_tag = f"kindred:{subtype}"
        count = sum(1 for cname, _ in nonland_cards if card_scores.get(cname, {}).get(kd_tag, {}).get("score", 0) > 0)
        kindred_support[kd_tag] = count

    themes: dict[str, ThemeInfo] = {}

    # Non-type themes
    for tag in ["tokens", "counters", "sacrifice", "graveyard", "lands_matter", "lifegain", "blink"]:
        sup = theme_support.get(tag, 0)
        gen_thr = max(tt["general_min"], math.ceil(tt["general_pct"] * N))
        cmdr_thr = max(tt["commander_min"], math.ceil(tt["commander_pct"] * N))
        is_cmdr = False
        for cc in commander_cards:
            cs = card_scores.get(cc.get("name", "").lower(), {})
            if cs.get(tag, {}).get("score", 0) >= 2:
                is_cmdr = True
                break
            if tag == "graveyard" and cs.get("recursion", {}).get("score", 0) >= 2:
                is_cmdr = True
                break
        active = False
        path = "general"
        if is_cmdr and sup >= cmdr_thr:
            active = True
            path = "commander"
        elif sup >= gen_thr:
            active = True
            path = "general"
        threshold = cmdr_thr if (is_cmdr and sup >= cmdr_thr) else gen_thr
        themes[tag] = ThemeInfo(tag=tag, support=sup, threshold=threshold,
                                path=path, active=active, is_commander_centered=is_cmdr)

    # Type themes
    for tag in ["artifacts", "enchantments", "spellslinger", "voltron"]:
        sup = theme_support.get(tag, 0)
        gen_thr = max(tt["general_min"], math.ceil(tt["type_theme_pct"] * N))
        cmdr_thr = max(tt["commander_min"], math.ceil(tt["commander_pct"] * N))
        is_cmdr = False
        for cc in commander_cards:
            cs = card_scores.get(cc.get("name", "").lower(), {})
            if cs.get(tag, {}).get("score", 0) >= 2:
                is_cmdr = True
                break
        active = False
        path = "general"
        if is_cmdr and sup >= cmdr_thr:
            active = True
            path = "commander"
        elif sup >= gen_thr:
            active = True
            path = "general"
        threshold = cmdr_thr if (is_cmdr and sup >= cmdr_thr) else gen_thr
        themes[tag] = ThemeInfo(tag=tag, support=sup, threshold=threshold,
                                path=path, active=active, is_commander_centered=is_cmdr)

    # Kindred themes: evaluate candidate subtypes
    active_kindred = []
    for kd_tag, sup in kindred_support.items():
        subtype = kd_tag.split(":", 1)[1]
        is_named = False
        for cc in commander_cards:
            ot = (cc.get("oracle_text") or "").lower()
            if re.search(r'\b(' + re.escape(subtype) + r'|' + re.escape(_plural_of(subtype)) + r')\b', ot):
                is_named = True
                break
        if is_named:
            threshold = max(tt["general_min"], math.ceil(tt["kindred_named_by_commander_pct"] * N))
        else:
            threshold = max(tt["general_min"], math.ceil(tt["kindred_pct"] * N))

        is_cmdr = False
        for cc in commander_cards:
            cs = card_scores.get(cc.get("name", "").lower(), {})
            if cs.get(kd_tag, {}).get("score", 0) >= 2:
                is_cmdr = True
                break
            if is_named:
                is_cmdr = True

        active = sup >= threshold
        path = "commander" if is_cmdr else "general"
        ti = ThemeInfo(tag=kd_tag, support=sup, threshold=threshold, path=path, active=active, is_commander_centered=is_cmdr)
        if active:
            active_kindred.append(ti)
        else:
            themes[kd_tag] = ti

    # At most max_kindred_themes active (§5.3)
    active_kindred.sort(key=lambda t: (-t.support, _tag_priority(t.tag)))
    for i, ti in enumerate(active_kindred):
        if i >= cfg.max_kindred_themes:
            ti.active = False
        themes[ti.tag] = ti

    return themes


# ---------------------------------------------------------------------------
# Pass 2: context rules (§4.2)
# ---------------------------------------------------------------------------

def _context_rules_for(card: dict, card_scores: dict, themes: dict[str, ThemeInfo],
                       paragraphs: list[str]) -> dict[str, tuple[int, list[str]]]:
    """Apply context rules. Returns {tag: (bonus, evidence_rules)}."""
    active_theme_names = {t.tag for t in themes.values() if t.active}
    result: dict[str, tuple[int, list[str]]] = {}

    # C-RM-1: RM-3 fired + SAC-1 in same paragraph + (tokens or sacrifice active)
    has_rm3 = False
    has_sac1_in_para = set()
    for i, para in enumerate(paragraphs):
        if re.search(r"\bdeals? 1 damage to (any target|target creature)", para, re.IGNORECASE):
            has_rm3 = True
        if re.search(r"^[^:]*\bsacrifice (a|an|another|two|three|x|one or more) [^:]*:", para, re.IGNORECASE):
            has_sac1_in_para.add(i)
    if has_rm3 and has_sac1_in_para and ("tokens" in active_theme_names or "sacrifice" in active_theme_names):
        result["removal"] = (1, ["RM-3", "C-RM-1"])

    # C-FN-1: FN-6 fired + (tokens, sacrifice, spellslinger, lifegain, lands_matter active)
    has_fn6 = False
    for para in paragraphs:
        if _is_rep(para):
            if re.search(r"\bdeals? (x|\d+) damage to (each opponent|any target|target player|target opponent)|\b(each opponent|target player|target opponent) loses (x|\d+) life", para, re.IGNORECASE):
                has_fn6 = True
    fn_context = {"tokens", "sacrifice", "spellslinger", "lifegain", "lands_matter"}
    if has_fn6 and (fn_context & active_theme_names):
        existing = result.get("finisher", (0, []))
        result["finisher"] = (existing[0] + 1, existing[1] + ["FN-6", "C-FN-1"])

    # C-SAC-1: SAC-3 (self-sacrifice: sacrifice ~[^:]*:) + sacrifice active
    has_sac3 = any(re.search(r"\bsacrifice ~[^:]*:", para, re.IGNORECASE) for para in paragraphs)
    if has_sac3 and "sacrifice" in active_theme_names:
        existing = result.get("sacrifice", (0, []))
        result["sacrifice"] = (existing[0] + 1, existing[1] + ["SAC-3", "C-SAC-1"])

    # C-RAMP-1: RAMP-6 (sac-for-mana) + (tokens or sacrifice active)
    has_ramp6 = any(re.search(r"\bsacrifice (a|an|another|two|three|x) [^:]*: add \{", para, re.IGNORECASE) for para in paragraphs)
    if has_ramp6 and ("tokens" in active_theme_names or "sacrifice" in active_theme_names):
        existing = result.get("ramp", (0, []))
        result["ramp"] = (existing[0] + 1, existing[1] + ["RAMP-6", "C-RAMP-1"])

    # C-TK-1: one-shot tokens/anthem (TK-3 or TK-4) + tokens active
    if "tokens" in active_theme_names:
        tk_fired = card_scores.get("tokens", {}).get("rules_fired", [])
        for rid in ["TK-4", "TK-3"]:
            if rid in tk_fired:
                existing = result.get("tokens", (0, []))
                result["tokens"] = (existing[0] + 1, existing[1] + [rid, "C-TK-1"])
                break

    # C-AR-1: is artifact (AR-T) + artifacts active -> +1 artifacts
    if "artifacts" in active_theme_names and _has_type(card, "Artifact"):
        existing = result.get("artifacts", (0, []))
        result["artifacts"] = (existing[0] + 1, existing[1] + ["AR-T", "C-AR-1"])

    # C-AR-2: treasure / clue / food / etc. maker (AR-X) + artifacts active -> +2 artifacts (filler)
    if "artifacts" in active_theme_names:
        has_ar_x = any(re.search(r'\bcreate [^.]*\b(treasure|clue|food|blood|map|gold|powerstone|incubator) tokens?|\binvestigate\b', p, re.IGNORECASE) for p in paragraphs)
        if has_ar_x:
            existing = result.get("artifacts", (0, []))
            result["artifacts"] = (existing[0] + 2, existing[1] + ["AR-X", "C-AR-2"])

    # C-EN-1: is enchantment + enchantments active -> +1 enchantments
    if "enchantments" in active_theme_names and _has_type(card, "Enchantment"):
        existing = result.get("enchantments", (0, []))
        result["enchantments"] = (existing[0] + 1, existing[1] + ["EN-T", "C-EN-1"])

    # C-SS-1: is instant/sorcery + spellslinger active -> +1 spellslinger
    if "spellslinger" in active_theme_names and (_has_type(card, "Instant") or _has_type(card, "Sorcery")):
        existing = result.get("spellslinger", (0, []))
        result["spellslinger"] = (existing[0] + 1, existing[1] + ["SS-T", "C-SS-1"])

    # C-VO-1: Equipment or Aura + voltron active -> +1 voltron
    if "voltron" in active_theme_names and (_has_type(card, "Equipment") or _has_type(card, "Aura")):
        existing = result.get("voltron", (0, []))
        result["voltron"] = (existing[0] + 1, existing[1] + ["VO-T", "C-VO-1"])

    # C-BK-1: ETB creature (BK-2) + blink active -> +1 blink
    if "blink" in active_theme_names:
        if "BK-2" in card_scores.get("blink", {}).get("rules_fired", []):
            existing = result.get("blink", (0, []))
            result["blink"] = (existing[0] + 1, existing[1] + ["BK-2", "C-BK-1"])

    # C-LM-1: land ramp (RAMP-2) + lands_matter active -> +1 lands_matter
    if "lands_matter" in active_theme_names:
        if "RAMP-2" in card_scores.get("ramp", {}).get("rules_fired", []):
            existing = result.get("lands_matter", (0, []))
            result["lands_matter"] = (existing[0] + 1, existing[1] + ["LM-3", "C-LM-1"])

    # C-LG-1: one-shot lifegain (LG-2) + lifegain active -> +1 lifegain
    if "lifegain" in active_theme_names:
        if "LG-2" in card_scores.get("lifegain", {}).get("rules_fired", []):
            existing = result.get("lifegain", (0, []))
            result["lifegain"] = (existing[0] + 1, existing[1] + ["LG-2", "C-LG-1"])

    # C-CT-1: counters (CT-2) + counters active -> +1 counters
    if "counters" in active_theme_names:
        if "CT-2" in card_scores.get("counters", {}).get("rules_fired", []):
            existing = result.get("counters", (0, []))
            result["counters"] = (existing[0] + 1, existing[1] + ["CT-2", "C-CT-1"])

    # C-KD-1: KD-T (has subtype T) + kindred:T active
    for kd_tag, theme_info in themes.items():
        if kd_tag.startswith("kindred:") and theme_info.active:
            subtype = kd_tag.split(":", 1)[1]
            subtypes = _get_subtypes_of_card(card)
            has_sub = subtype in subtypes or "changeling" in [k.lower() for k in card.get("keywords", [])]
            if has_sub:
                base_info = card_scores.get(kd_tag, {})
                if "KD-1" not in base_info.get("rules_fired", []):
                    existing = result.get(kd_tag, (0, []))
                    result[kd_tag] = (existing[0] + 1, existing[1] + ["KD-T", "C-KD-1"])

    return result


# ---------------------------------------------------------------------------
# Mana fixing (§3, L-2)
# ---------------------------------------------------------------------------

def _compute_mana_fixing(card: dict, card_scores: dict, ci_size: int) -> dict[str, dict]:
    """Compute mana_fixing score for a nonland card."""
    result: dict[str, dict] = {}
    if ci_size < 3:
        return result

    produced = set(card.get("produced_mana", [])) & set("WUBRG")
    if len(produced) < 2:
        return result

    cscores = card_scores
    if cscores.get("ramp", {}).get("score", 0) > 0:
        result["mana_fixing"] = {"score": 2, "rules_fired": ["MF-1"]}
    elif "RAMP-2" in cscores.get("ramp", {}).get("rules_fired", []):
        text = (card.get("oracle_text") or "").lower()
        if "basic land" in text:
            result["mana_fixing"] = {"score": 2, "rules_fired": ["MF-2"]}
    return result


# ---------------------------------------------------------------------------
# Selection (§6)
# ---------------------------------------------------------------------------

def _select_tags(card_scores: dict, context_bonuses: dict, themes: dict[str, ThemeInfo],
                 config: AutoTagConfig | None = None, is_commander: bool = False) -> list[dict]:
    """Select final tags for a card. Returns list of {tag, score, filler, evidence}."""
    cfg = config or DEFAULT_CONFIG
    evidence_threshold = cfg.evidence_threshold

    all_scores: dict[str, dict] = {}

    # Base scores
    for tag, info in card_scores.items():
        base = info["score"]
        all_scores[tag] = {
            "base": base,
            "score": base,
            "rules_fired": info.get("rules_fired", [])[:],
        }

    # Context bonuses
    for tag, (bonus, ev) in context_bonuses.items():
        if tag in all_scores:
            all_scores[tag]["score"] = min(all_scores[tag]["score"] + bonus, cfg.score_cap)
            for r in ev:
                if r not in all_scores[tag]["rules_fired"]:
                    all_scores[tag]["rules_fired"].append(r)
        else:
            all_scores[tag] = {"base": 0, "score": bonus, "rules_fired": ev[:]}

    # Theme gating (§6.1): drop theme tags whose theme is not active
    for tag in list(all_scores.keys()):
        if tag in THEME_TAGS or tag.startswith("kindred:"):
            theme_active = any(t.tag == tag and t.active for t in themes.values())
            if not theme_active:
                del all_scores[tag]

    # Filter to evidence threshold
    candidates = {tag: info for tag, info in all_scores.items() if info["score"] >= evidence_threshold}

    # Separate strong (base >= 2) and filler (base < 2)
    strong = {t: info for t, info in candidates.items() if info["base"] >= 2}
    filler = {t: info for t, info in candidates.items() if info["base"] < 2}

    def strong_sort_key(t):
        if is_commander:
            is_theme = 0 if (t in THEME_TAGS or t.startswith("kindred:")) else 1
            return (is_theme, -candidates[t]["score"], _tag_priority(t))
        return (-candidates[t]["score"], _tag_priority(t))

    sorted_strong = sorted(strong.keys(), key=strong_sort_key)
    sorted_filler = sorted(filler.keys(), key=lambda t: _tag_priority(t))

    selected: list[dict] = []
    for t in sorted_strong[:cfg.max_tags_card]:
        info = candidates[t]
        selected.append({
            "tag": t,
            "score": info["score"],
            "filler": False,
            "evidence": info["rules_fired"][:],
        })

    # Add filler tags (up to filler_max_total total, never into 3rd slot)
    for t in sorted_filler:
        if len(selected) < cfg.filler_max_total:
            selected.append({
                "tag": t,
                "score": candidates[t]["score"],
                "filler": True,
                "evidence": candidates[t]["rules_fired"][:],
            })

    return selected


# ---------------------------------------------------------------------------
# Land tagging (§4.4)
# ---------------------------------------------------------------------------

def _tag_land(card: dict, card_scores: dict, themes: dict[str, ThemeInfo],
              ci_size: int, deck_colors: list[str], config: AutoTagConfig | None = None) -> list[dict]:
    """Tag a land card per §4.4. Returns list of tag dicts."""
    cfg = config or DEFAULT_CONFIG
    tl = (card.get("type_line") or card.get("type") or "").lower()
    main_type = tl.split("—")[0] if "—" in tl else tl.split("-")[0]

    # L-1: basic lands get no tags
    if "basic" in main_type.split():
        return []

    produced = card.get("produced_mana", [])
    deck_color_set = set(deck_colors)

    # L-2: mana fixing
    has_mf = False
    if produced is not None and len(produced) > 0:
        colors_produced = set(produced) & deck_color_set
        if len(colors_produced) >= 2:
            has_mf = True
    else:
        # Fallback if produced_mana is missing
        oracle = (card.get("oracle_text") or "").lower()
        if len(deck_colors) >= 2:
            if "any color" in oracle:
                has_mf = True
            elif re.search(r'search your library for [^.]*basic land', oracle):
                has_mf = True
            else:
                add_matches = re.findall(r'add \{([wubrg])\}', oracle)
                if len(set(add_matches) & deck_color_set) >= 2:
                    has_mf = True

    tags: list[dict] = []
    if has_mf:
        tags.append({"tag": "mana_fixing", "score": 2, "filler": False, "evidence": ["L-2"]})

    # L-3 / L-4: At most ONE non-fixing tag
    non_fixing_tag = None

    # L-3: utility land
    _, paragraphs, _ = normalise(card)
    has_utility = False
    for para in paragraphs:
        if re.match(r'^\{t\}: add [^.]*\.$', para):
            continue
        if "as ~ enters" in para:
            continue
        if "enters tapped" in para or "enters the battlefield tapped" in para:
            continue
        has_utility = True
        break

    if has_utility:
        candidates = []
        for tag, info in card_scores.items():
            if tag in ("ramp", "mana_fixing"):
                continue
            if info.get("score", 0) >= 2:
                if tag in THEME_TAGS or tag.startswith("kindred:"):
                    if not any(t.tag == tag and t.active for t in themes.values()):
                        continue
                candidates.append((tag, info["score"], info.get("rules_fired", [])))
        if candidates:
            candidates.sort(key=lambda x: (-x[1], _tag_priority(x[0])))
            best_tag, best_score, best_ev = candidates[0]
            non_fixing_tag = {"tag": best_tag, "score": best_score, "filler": False, "evidence": best_ev}

    # L-4: typed land (if no utility tag was found)
    if not non_fixing_tag:
        words = [w.lower() for w in re.findall(r'[a-zA-Z]+', tl)]
        if "artifact" in words and "artifacts" in themes and themes["artifacts"].active:
            non_fixing_tag = {"tag": "artifacts", "score": 2, "filler": False, "evidence": ["L-4"]}
        elif "enchantment" in words and "enchantments" in themes and themes["enchantments"].active:
            non_fixing_tag = {"tag": "enchantments", "score": 2, "filler": False, "evidence": ["L-4"]}
        else:
            for t in themes.values():
                if t.tag.startswith("kindred:") and t.active:
                    sub = t.tag.split(":", 1)[1]
                    if sub in words:
                        non_fixing_tag = {"tag": t.tag, "score": 2, "filler": False, "evidence": ["L-4"]}
                        break

    if non_fixing_tag and len(tags) < cfg.max_tags_land:
        tags.append(non_fixing_tag)

    return tags


# ---------------------------------------------------------------------------
# Flags (§6.5)
# ---------------------------------------------------------------------------

def _compute_flags(card: dict) -> list[str]:
    flags: list[str] = []
    if card.get("game_changer"):
        flags.append("game_changer")
    text = (card.get("oracle_text") or "").lower()
    if re.search(r'\btakes? an extra turn|\bextra turn after this one', text):
        flags.append("extra_turns")
    if re.search(r'\bdestroy all lands|\beach player sacrifices [^.]*lands', text):
        flags.append("mass_land_denial")
    if re.search(r"(don't|doesn't) untap during|\b(each player|your opponents|players) can't|\bcosts? \{\d+\} more to cast", text):
        flags.append("stax")
    return flags


# ---------------------------------------------------------------------------
# Deck-level tags (§7.1)
# ---------------------------------------------------------------------------

def compute_deck_tags(themes: dict[str, ThemeInfo], config: AutoTagConfig | None = None) -> list[dict]:
    """Rank and emit deck-level tags. At most max_deck_tags."""
    cfg = config or DEFAULT_CONFIG
    active = [(t, t.support / max(t.threshold, 0.001)) for t in themes.values() if t.active]
    active.sort(key=lambda x: (
        {"journal": 0, "commander": 1, "general": 2}.get(x[0].path, 2),
        -x[1],
        _tag_priority(x[0].tag),
    ))

    result = []
    for t, _ in active[:cfg.max_deck_tags]:
        result.append({
            "tag": t.tag,
            "support": t.support,
            "threshold": t.threshold,
            "path": t.path,
        })
    return result


# ---------------------------------------------------------------------------
# Health hints (§7.2)
# ---------------------------------------------------------------------------

def compute_health(deck: dict, card_tag_results: dict[str, list[dict]], config: AutoTagConfig | None = None) -> list[dict]:
    """Compute role-count health hints. Only for 90+ card decks."""
    cfg = config or DEFAULT_CONFIG
    h_cfg = cfg.health
    min_deck = h_cfg.get("min_deck_size", 90)

    cards = deck.get("cards", [])
    commanders = deck.get("commanders", [])
    cmdr_count = len(commanders) if isinstance(commanders, list) else (1 if commanders else 0)
    total_cards = len(cards) + cmdr_count
    if total_cards < min_deck:
        return []

    # Count cards per tag
    tag_counts: dict[str, int] = {}
    for cname, tags in card_tag_results.items():
        for t in tags:
            tag_name = t["tag"]
            tag_counts[tag_name] = tag_counts.get(tag_name, 0) + 1

    # Themes
    raw_themes = deck.get("_themes", {})
    if isinstance(raw_themes, dict):
        themes = raw_themes
    elif isinstance(raw_themes, list):
        themes = {t.tag: t for t in raw_themes if hasattr(t, "active")}
    else:
        themes = {}
    tokens_active = "tokens" in themes and getattr(themes["tokens"], "active", False)

    # Commander tags
    commander_tags_set: set[str] = set()
    if isinstance(commanders, list):
        for cc in commanders:
            if isinstance(cc, dict):
                for t in cc.get("_final_tags", []):
                    commander_tags_set.add(t["tag"])

    # Commander MV
    cmdr_mvs = []
    if isinstance(commanders, list):
        for cc in commanders:
            if isinstance(cc, dict):
                cmdr_mvs.append(cc.get("cmc", cc.get("mana_value", 0)) or 0)
    mv = deck.get("commander_max_mv")
    if mv is None:
        mv = max(cmdr_mvs) if cmdr_mvs else 0
    big_mv = mv >= h_cfg.get("big_commander_mv", 5)

    results = []
    floors = h_cfg.get("floors", {})

    for key in ["ramp", "card_advantage", "interaction", "board_wipe", "finisher"]:
        count = tag_counts.get(key, 0)
        if key == "interaction":
            count = tag_counts.get("removal", 0) + tag_counts.get("counterspell", 0)

        floor = floors.get(key, 0)

        # Modifiers
        if big_mv and key == "ramp":
            floor += h_cfg.get("big_commander_ramp_bonus", 2)
        if key in commander_tags_set:
            floor += h_cfg.get("commander_does_job", -3)
        if key == "board_wipe" and tokens_active:
            floor = h_cfg.get("tokens_wipe_floor", 0)

        floor = max(0, floor)
        status = "low" if count < floor else "ok"

        results.append({
            "key": key,
            "count": count,
            "floor": floor,
            "status": status,
        })

    return results


# ---------------------------------------------------------------------------
# Main engine: run_auto_tag
# ---------------------------------------------------------------------------

def run_auto_tag(deck: dict, config: AutoTagConfig | None = None) -> dict:
    """Run the full auto-tag pipeline for a deck."""
    cfg = config or DEFAULT_CONFIG

    commanders = deck.get("commanders", [])
    if isinstance(commanders, str):
        commanders = [{"name": commanders}]
    elif isinstance(commanders, list) and commanders and isinstance(commanders[0], str):
        commanders = [{"name": c} for c in commanders]

    commander_names = {c.get("name", "").lower() for c in commanders if isinstance(c, dict)}
    cards = deck.get("cards", [])
    ci_size = deck.get("color_identity_size", len(deck.get("color_identity", [])))
    deck_colors = list(deck.get("color_identity", ["W", "U", "B", "R", "G"]))[:ci_size]

    # Nonland cards for kindred candidate discovery
    nonland_cards = []
    for c in cards:
        cname = c.get("name", "").lower() if isinstance(c, dict) else str(c).lower()
        if cname not in commander_names and not _is_land(c):
            nonland_cards.append((cname, c))

    cand_subtypes = get_kindred_candidates(deck, nonland_cards)

    # ── Pass 1 ──
    card_scores: dict[str, dict] = {}
    all_eval_cards = list(cards)
    for c in commanders:
        if isinstance(c, dict) and not any(oc.get("name", "").lower() == c.get("name", "").lower() for oc in all_eval_cards):
            all_eval_cards.append(c)

    for c in all_eval_cards:
        cname = c.get("name", "").lower()
        scores = pass1_card_score(c, cfg)

        # Score candidate kindred rules: KD-1 and KD-T
        text, _, _ = normalise(c)
        c_subtypes = _get_subtypes_of_card(c)
        c_keywords = [k.lower() for k in c.get("keywords", [])]

        for subtype in cand_subtypes:
            kd_1 = bool(re.search(r'\b(' + re.escape(subtype) + r'|' + re.escape(_plural_of(subtype)) + r')\b', text))
            kd_t = (subtype in c_subtypes) or ("changeling" in c_keywords)
            if kd_1 or kd_t:
                kd_score = (2 if kd_1 else 0) + (1 if kd_t else 0)
                kd_rules = []
                if kd_1:
                    kd_rules.append("KD-1")
                if kd_t:
                    kd_rules.append("KD-T")
                scores[f"kindred:{subtype}"] = {"score": min(kd_score, cfg.score_cap), "rules_fired": kd_rules}

        card_scores[cname] = scores

    # ── Pass 1.5 ──
    themes = compute_deck_themes(deck, card_scores, cfg)
    deck["_themes"] = themes

    # ── Pass 2: context + selection ──
    card_results: dict[str, list[dict]] = {}
    output_cards: list[dict] = []

    for c in cards:
        name = c.get("name", "").lower()
        is_commander = name in commander_names

        if _is_land(c):
            land_tags = _tag_land(c, card_scores.get(name, {}), themes, ci_size, deck_colors, cfg)
            flags = _compute_flags(c)
            card_results[name] = land_tags
            output_cards.append({
                "name": c.get("name", ""),
                "is_commander": False,
                "tags": land_tags,
                "flags": flags,
            })
            continue

        scores = card_scores.get(name, {})

        # Mana fixing
        mf = _compute_mana_fixing(c, scores, ci_size)
        for tag, info in mf.items():
            if tag in scores:
                scores[tag]["score"] = min(scores[tag]["score"] + info["score"], cfg.score_cap)
                scores[tag]["rules_fired"] = scores[tag].get("rules_fired", []) + info["rules_fired"]
            else:
                scores[tag] = {"score": info["score"], "rules_fired": info["rules_fired"][:]}

        _, paragraphs, _ = normalise(c)
        ctx = _context_rules_for(c, scores, themes, paragraphs)
        tags = _select_tags(scores, ctx, themes, cfg, is_commander=is_commander)

        card_results[name] = tags
        if is_commander:
            c["_final_tags"] = tags

        flags = _compute_flags(c)
        output_cards.append({
            "name": c.get("name", ""),
            "is_commander": is_commander,
            "tags": tags,
            "flags": flags,
        })

    # Process commanders not in cards list
    for c in commanders:
        if isinstance(c, dict):
            name = c.get("name", "").lower()
            if name in commander_names and not any(oc["name"].lower() == name for oc in output_cards):
                scores = card_scores.get(name, {})
                mf = _compute_mana_fixing(c, scores, ci_size)
                for tag, info in mf.items():
                    if tag in scores:
                        scores[tag]["score"] = min(scores[tag]["score"] + info["score"], cfg.score_cap)
                    else:
                        scores[tag] = {"score": info["score"], "rules_fired": info["rules_fired"][:]}

                _, paragraphs, _ = normalise(c)
                ctx = _context_rules_for(c, scores, themes, paragraphs)
                tags = _select_tags(scores, ctx, themes, cfg, is_commander=True)

                card_results[name] = tags
                c["_final_tags"] = tags
                flags = _compute_flags(c)
                output_cards.append({
                    "name": c.get("name", ""),
                    "is_commander": True,
                    "tags": tags,
                    "flags": flags,
                })

    deck_tags = compute_deck_tags(themes, cfg)
    health = compute_health(deck, card_results, cfg)

    return {
        "version": cfg.version,
        "cards": output_cards,
        "deck_tags": deck_tags,
        "health": health,
    }
