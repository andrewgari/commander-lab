"""
Deck Genre (GN): the journal's archetype / theme / kindred tags.

Rules: docs/DECK_GENRE_TAXONOMY.md, UI: docs/DECK_GENRE_UX_SPEC.md.

The journal's archetype/theme vocabulary (ARCHETYPES/GENRES below) is a Redis-backed,
user-editable registry -- its own thing, distinct from analytics/archetypes.py and
analytics/genres.py (the Deep Review role-coverage registries vocab PR2/PR3 added). Those
score a deck's cards against required card roles; this is a flat, closed-ish pick-list of
labels a user types or clicks onto a deck in the Journal, with no card-role scoring at all.
The names rhyme but the concepts don't -- see the parent card for the PR3 decomposition.

Storage (journal:deck:{registry_id}, see analytics/journal.py):
    archetype_preferences.primary_archetype     0..1, nullable, from list_archetypes()
    archetype_preferences.secondary_archetypes  0..3, from list_archetypes(), never the primary
    deck_genre.genres                           0..5, an archetype id (see list_genres()) or custom text (<= 32 chars)
    deck_genre.kindred                          0..5, catalog spelling ("Elf") or custom text (<= 32 chars)

The UI labels deck_genre.genres "Themes" (/genres is the Deep Review registry).

Redis (JSON list, each item seeded once then freely editable/deletable; a deleted seed
never comes back -- "first read creates the key" never re-seeds an existing, now-empty key):
    deck_genre:archetypes   the Style / archetype pick-list (default: 30 seed items)
    deck_genre:genres       the Themes pick-list (default: 33 seed items)

Every list/label lookup takes an optional Redis client `r`. With r=None (no Redis in this
call's context -- e.g. a pure validator with no app plumbed through) they fall back to the
bundled seed values, same as creature_types(r=None) below. Pass `r` wherever you have it so
edits made through the (future) admin UI actually take effect.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

TAXONOMY_VERSION = 1
MAX_SECONDARY = 3
MAX_ARCHETYPES = 1 + MAX_SECONDARY
MAX_GENRES = 5
MAX_KINDRED = 5
CUSTOM_MAX_LEN = 32
MAX_LABEL = 60
MAX_DESC = 400
# Redis override for the creature-type list (a JSON array of strings). Unset = the snapshot.
CATALOG_KEY = "catalog:creature_types"
CATALOG_URL = "https://api.scryfall.com/catalog/creature-types"
CATALOG_TTL = 7 * 24 * 3600

# Redis keys for the editable archetype/genre pick-lists (see module docstring).
ARCHETYPES_KEY = "deck_genre:archetypes"
GENRES_KEY = "deck_genre:genres"

# Serialise writers so two concurrent edits to the same list can't drop each other (the app
# runs as one process; this lock is process-wide, same pattern as analytics/genres.py).
_WRITE_LOCK = threading.RLock()


class DeckGenreError(ValueError):
    """Invalid archetype/genre item payload (HTTP 400)."""


class DeckGenreNotFound(KeyError):
    """No archetype/genre item with that id (HTTP 404)."""


def _locked(fn):
    def wrapper(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return wrapper


# --- Groups (structural buckets for the UI, not user-editable vocabulary) -------------------
ARCHETYPE_GROUPS = (
    {'id': 'gameplan', 'label': 'Game plan'},
    {'id': 'engine', 'label': 'Engine / mechanic'},
    {'id': 'table', 'label': 'Table dynamics'},
)
GENRE_GROUPS = (
    {'id': 'flavor', 'label': 'Flavor & setting'},
    {'id': 'vibe', 'label': 'Play vibe'},
    {'id': 'identity', 'label': 'Build identity'},
)
_ARCHETYPE_GROUP_IDS = {g["id"] for g in ARCHETYPE_GROUPS}
_GENRE_GROUP_IDS = {g["id"] for g in GENRE_GROUPS}

# --- Seed data (default content of the Redis-backed registries on first read) ---------------
SEED_ARCHETYPES: Tuple[dict, ...] = (
    {'id': 'aggro', 'group': 'gameplan', 'label': 'Aggro', 'desc': 'Gets on board fast and wins through early, relentless combat pressure.'},
    {'id': 'midrange', 'group': 'gameplan', 'label': 'Midrange', 'desc': 'Efficient threats and answers; adapts to the table and grinds out value.'},
    {'id': 'control', 'group': 'gameplan', 'label': 'Control', 'desc': 'Answers everything, controls the pace, and wins late with a few finishers.'},
    {'id': 'combo', 'group': 'gameplan', 'label': 'Combo', 'desc': 'Assembles a specific card combination that wins the game or locks it up.'},
    {'id': 'stax', 'group': 'gameplan', 'label': 'Stax', 'desc': "Taxes and restricts resources so opponents can't function while it pulls ahead."},
    {'id': 'ramp', 'group': 'gameplan', 'label': 'Ramp / Big Mana', 'desc': 'Accelerates mana early to cast huge spells and threats ahead of the curve.'},
    {'id': 'pillowfort', 'group': 'gameplan', 'label': 'Pillowfort', 'desc': 'Makes itself annoying to attack, then wins quietly from behind its walls.'},
    {'id': 'tokens', 'group': 'engine', 'label': 'Tokens / Go-Wide', 'desc': 'Floods the board with creature tokens and wins with an anthem or mass pump.'},
    {'id': 'aristocrats', 'group': 'engine', 'label': 'Aristocrats', 'desc': 'Sacrifices its own creatures for value and drains opponents on every death.'},
    {'id': 'counters', 'group': 'engine', 'label': '+1/+1 Counters', 'desc': 'Piles +1/+1 counters onto creatures and multiplies or moves them around.'},
    {'id': 'voltron', 'group': 'engine', 'label': 'Voltron', 'desc': 'Suits up one creature, usually the commander, with equipment and auras for commander-damage kills.'},
    {'id': 'tribal', 'group': 'engine', 'label': 'Kindred (type matters)', 'desc': 'Rewards playing many creatures of one type through lords and synergies. Pick the types in the Kindred field.'},
    {'id': 'spellslinger', 'group': 'engine', 'label': 'Spellslinger', 'desc': 'Chains instants and sorceries, rewarded by every spell cast or copied.'},
    {'id': 'storm', 'group': 'engine', 'label': 'Storm', 'desc': 'Casts a flurry of spells in one turn and wins off the spell count.'},
    {'id': 'graveyard', 'group': 'engine', 'label': 'Graveyard Value', 'desc': 'Uses its own graveyard as a resource: self-mill, flashback, recursion.'},
    {'id': 'reanimator', 'group': 'engine', 'label': 'Reanimator', 'desc': 'Dumps big creatures in the graveyard and brings them back cheaply.'},
    {'id': 'mill', 'group': 'engine', 'label': 'Mill', 'desc': "Wins by emptying opponents' libraries. Self-mill belongs under Graveyard Value."},
    {'id': 'landfall', 'group': 'engine', 'label': 'Landfall / Lands Matter', 'desc': 'Plays extra lands and recurs them, triggering payoffs on every land drop.'},
    {'id': 'blink', 'group': 'engine', 'label': 'Blink / Flicker', 'desc': 'Exiles and returns its own permanents to retrigger enter-the-battlefield effects.'},
    {'id': 'artifacts', 'group': 'engine', 'label': 'Artifacts', 'desc': 'Built around artifact synergies: treasures, constructs, equipment, or cost reducers.'},
    {'id': 'enchantress', 'group': 'engine', 'label': 'Enchantress', 'desc': 'Draws cards off every enchantment cast and wins through an enchantment-heavy board.'},
    {'id': 'superfriends', 'group': 'engine', 'label': 'Superfriends', 'desc': 'Runs many planeswalkers, protecting and proliferating them toward ultimates.'},
    {'id': 'lifegain', 'group': 'engine', 'label': 'Lifegain', 'desc': 'Gains life repeatedly and turns each gain into counters, cards, or drain.'},
    {'id': 'poison', 'group': 'engine', 'label': 'Infect / Poison', 'desc': 'Wins by giving opponents ten poison counters through infect, toxic, or proliferate.'},
    {'id': 'theft', 'group': 'engine', 'label': 'Theft', 'desc': "Steals, copies, and casts opponents' cards to beat them with their own stuff."},
    {'id': 'wheels', 'group': 'engine', 'label': 'Wheels', 'desc': 'Makes everyone discard and redraw hands, punishing each card drawn.'},
    {'id': 'cheat', 'group': 'engine', 'label': 'Cheat Into Play', 'desc': 'Puts huge threats onto the battlefield without paying their mana costs.'},
    {'id': 'extra_combats', 'group': 'engine', 'label': 'Extra Combats', 'desc': 'Takes additional combat phases to multiply attack triggers and damage.'},
    {'id': 'group_hug', 'group': 'table', 'label': 'Group Hug', 'desc': 'Gives every player resources and wins by steering the table it fed.'},
    {'id': 'group_slug', 'group': 'table', 'label': 'Group Slug', 'desc': 'Punishes the whole table at once with symmetric damage and taxes.'},
)
SEED_GENRES: Tuple[dict, ...] = (
    {'id': 'horror', 'group': 'flavor', 'label': 'Horror / Gothic', 'desc': 'Haunted, undead, monstrous: the Innistrad and graveyard-at-midnight feeling.'},
    {'id': 'eldritch', 'group': 'flavor', 'label': 'Eldritch / Cosmic Horror', 'desc': 'Unknowable things from beyond: Eldrazi, tentacles, madness.'},
    {'id': 'nautical', 'group': 'flavor', 'label': 'Pirates / Nautical', 'desc': 'Ships, sea monsters, treasure, and life on the waves.'},
    {'id': 'high_fantasy', 'group': 'flavor', 'label': 'High Fantasy / Adventure', 'desc': 'Classic sword-and-sorcery: dungeons, quests, a D&D adventuring party.'},
    {'id': 'heroic', 'group': 'flavor', 'label': 'Heroes / Chivalry', 'desc': 'Knights, paladins, and the good guys riding to the rescue.'},
    {'id': 'villainous', 'group': 'flavor', 'label': 'Villains / Dark Lord', 'desc': 'Playing the evil overlord and everything that comes with it.'},
    {'id': 'mythology', 'group': 'flavor', 'label': 'Mythology / Gods', 'desc': 'Gods, heroes of legend, and the Theros-style mythic world.'},
    {'id': 'nature', 'group': 'flavor', 'label': 'Nature / The Wilds', 'desc': 'Forests, beasts, and the untamed natural world.'},
    {'id': 'cute', 'group': 'flavor', 'label': 'Cute / Cozy', 'desc': 'Critters, snacks, and wholesome vibes: squirrels, otters, food.'},
    {'id': 'sci_fi', 'group': 'flavor', 'label': 'Sci-Fi / Tech', 'desc': 'Robots, aliens, spaceships, and future tech: neon Kamigawa and beyond.'},
    {'id': 'steampunk', 'group': 'flavor', 'label': 'Artifice / Steampunk', 'desc': 'Inventors, gears, and contraptions: Kaladesh-style clockwork.'},
    {'id': 'crime', 'group': 'flavor', 'label': 'Crime / Heist / Noir', 'desc': 'Mobsters, detectives, heists, and back-alley deals.'},
    {'id': 'western', 'group': 'flavor', 'label': 'Western / Outlaws', 'desc': 'Desperadoes, bounties, and frontier showdowns.'},
    {'id': 'war', 'group': 'flavor', 'label': 'War / Army', 'desc': 'Marching battalions, sieges, and the battlefield as a war.'},
    {'id': 'apocalypse', 'group': 'flavor', 'label': 'Apocalypse / Doom', 'desc': 'The end of the world: destruction, wastelands, last stands.'},
    {'id': 'crossover', 'group': 'flavor', 'label': 'Crossover / Universes Beyond', 'desc': 'Built around an outside franchise (Lord of the Rings, Warhammer, Doctor Who, ...).'},
    {'id': 'lore', 'group': 'flavor', 'label': 'Lore / Story Deck', 'desc': "Tells a specific story or character's arc from MTG lore."},
    {'id': 'battlecruiser', 'group': 'vibe', 'label': 'Battlecruiser', 'desc': 'Slow and splashy: big spells, big turns, little interaction.'},
    {'id': 'chaos', 'group': 'vibe', 'label': 'Chaos', 'desc': 'Coin flips, random effects, and nobody knowing what happens next.'},
    {'id': 'political', 'group': 'vibe', 'label': 'Political', 'desc': 'Deals, votes, and table talk are part of the plan.'},
    {'id': 'archenemy', 'group': 'vibe', 'label': 'Archenemy', 'desc': "Built to be the table's villain. Everyone vs. you, on purpose."},
    {'id': 'puzzle', 'group': 'vibe', 'label': 'Puzzle / Engine Builder', 'desc': 'The joy is assembling and running a clever machine.'},
    {'id': 'durdle', 'group': 'vibe', 'label': 'Durdle / Slow Burn', 'desc': 'Gets there eventually; enjoys the long game.'},
    {'id': 'meme', 'group': 'vibe', 'label': 'Meme / Jank', 'desc': 'Built for the laugh or the bit, not the win rate.'},
    {'id': 'precon_upgrade', 'group': 'identity', 'label': 'Precon Upgrade', 'desc': 'Started life as a preconstructed deck and grew from there.'},
    {'id': 'budget', 'group': 'identity', 'label': 'Budget', 'desc': 'Built deliberately cheap as a constraint and a point of pride.'},
    {'id': 'pauper_ish', 'group': 'identity', 'label': 'Pauper-ish', 'desc': 'Mostly or all commons (and uncommons), by choice.'},
    {'id': 'flavor_first', 'group': 'identity', 'label': 'Flavor Over Function', 'desc': 'Card choices follow the theme even when a stronger card exists.'},
    {'id': 'challenge', 'group': 'identity', 'label': 'Self-Imposed Challenge', 'desc': 'A deliberate restriction: one set, no tutors, no rares, one artist.'},
    {'id': 'off_meta', 'group': 'identity', 'label': 'Off-Meta Commander', 'desc': 'An unusual or rarely played commander, chosen because nobody else plays it.'},
    {'id': 'old_school', 'group': 'identity', 'label': 'Old School', 'desc': 'Old-border cards, classic sets, nostalgia.'},
    {'id': 'bling', 'group': 'identity', 'label': 'Bling / Pimped', 'desc': 'Foils, alt-arts, showcase frames: the deck as a collection piece.'},
    {'id': 'pet_deck', 'group': 'identity', 'label': 'Pet Deck', 'desc': "Your favorite; you'll never take it apart."},
)
KINDRED_SUGGESTIONS = (
    'Elf', 'Goblin', 'Dragon', 'Zombie', 'Vampire', 'Sliver', 'Merfolk', 'Angel', 'Wizard', 'Dinosaur', 'Cat',
    'Spirit', 'Human', 'Knight', 'Soldier', 'Pirate', 'Elemental', 'Demon', 'Faerie', 'Rat', 'Squirrel',
    'Dog', 'Bird', 'Werewolf', 'Hydra', 'Eldrazi', 'Ninja', 'Dwarf', 'Myr', 'Snake', 'Treefolk', 'Insect',
    'Beast', 'Phyrexian', 'Shapeshifter',
)
KINDRED_GROUPS = (
    {'id': 'party', 'label': 'Party', 'members': ['Cleric', 'Rogue', 'Warrior', 'Wizard']},
    {'id': 'outlaws', 'label': 'Outlaws', 'members': ['Assassin', 'Mercenary', 'Pirate', 'Rogue', 'Warlock']},
)
KINDRED_ALIASES = {
    'Elves': 'Elf',
    'Dwarves': 'Dwarf',
    'Wolves': 'Wolf',
    'Werewolves': 'Werewolf',
    'Mice': 'Mouse',
    'Fungi': 'Fungus',
    'Octopi': 'Octopus',
    'Fae': 'Faerie',
    'Dinos': 'Dinosaur',
}

# Scryfall /catalog/creature-types, 2026-10-16 (351 types). Offline fallback for the
# Redis catalog (CATALOG_KEY); refresh with scripts/refresh_creature_types.py.
# NOT in scope for the Redis-registry move above: this is live reference data cached with a
# TTL, not a hardcoded vocabulary list a user edits.
CREATURE_TYPES_SNAPSHOT = (
    'Advisor', 'Aetherborn', 'Alien', 'Ally', 'Andorian', 'Angel', 'Antelope', 'Ape', 'Archer', 'Archon',
    'Armadillo', 'Army', 'Artificer', 'Assassin', 'Assembly-Worker', 'Astartes', 'Atog', 'Aurochs',
    'Automaton', 'Avatar', 'Azra', 'Badger', 'Balloon', 'Barbarian', 'Bard', 'Basilisk', 'Bat', 'Bear',
    'Beast', 'Beaver', 'Beeble', 'Beholder', 'Berserker', 'Bird', 'Bison', 'Blinkmoth', 'Boar', 'Borg',
    'Brainiac', 'Bringer', 'Brushwagg', "C'tan", 'Caitian', 'Camarid', 'Camel', 'Capybara', 'Caribou',
    'Carrier', 'Cat', 'Centaur', 'Chicken', 'Child', 'Chimera', 'Citizen', 'Cleric', 'Clown', 'Cockatrice',
    'Construct', 'Coward', 'Coyote', 'Crab', 'Crocodile', 'Custodes', 'Cyberman', 'Cyclops', 'Dalek',
    'Dauthi', 'Demigod', 'Demon', 'Deserter', 'Detective', 'Devil', 'Dinosaur', 'Djinn', 'Doctor', 'Dog',
    'Dragon', 'Drake', 'Dreadnought', 'Drix', 'Drone', 'Druid', 'Dryad', 'Dwarf', 'Echidna', 'Efreet', 'Egg',
    'Elder', 'Eldrazi', 'Elemental', 'Elephant', 'Elf', 'Elk', 'Employee', 'Eternal', 'Eye', 'Faerie',
    'Ferret', 'Fish', 'Flagbearer', 'Fox', 'Fractal', 'Frog', 'Fungus', 'Gamer', 'Gamma', 'Gargoyle', 'Germ',
    'Giant', 'Giraffe', 'Gith', 'Glimmer', 'Gnoll', 'Gnome', 'Goat', 'Goblin', 'God', 'Golem', 'Gorgon',
    'Gorn', 'Graveborn', 'Gremlin', 'Griffin', 'Guest', 'Hag', 'Halfling', 'Hamster', 'Harpy', 'Head',
    'Hedgehog', 'Hellion', 'Hero', 'Hippo', 'Hippogriff', 'Homarid', 'Homunculus', 'Horror', 'Horse', 'Human',
    'Hydra', 'Hyena', 'Illusion', 'Imp', 'Incarnation', 'Inhuman', 'Inkling', 'Inquisitor', 'Insect',
    'Jackal', 'Jellyfish', 'Juggernaut', 'Kangaroo', 'Kavu', 'Kelpien', 'Kirin', 'Kithkin', 'Klingon',
    'Knight', 'Kobold', 'Kor', 'Kraken', 'Kree', 'Lamia', 'Lammasu', 'Lanthanite', 'Leech', 'Lemur',
    'Leviathan', 'Lhurgoyf', 'Licid', 'Lizard', 'Llama', 'Lobster', 'Manticore', 'Masticore', 'Mercenary',
    'Merfolk', 'Metathran', 'Minion', 'Minotaur', 'Missing', 'Mite', 'Mole', 'Monger', 'Mongoose', 'Monk',
    'Monkey', 'Moogle', 'Moonfolk', 'Mount', 'Mouse', 'Mutant', 'Myr', 'Mystic', 'Naga', 'Nautilus', 'Necron',
    'Nephilim', 'Nightmare', 'Nightstalker', 'Ninja', 'Noble', 'Noggle', 'Nomad', 'Nymph', 'Octopus',
    'Officer', 'Ogre', 'Ooze', 'Orb', 'Orc', 'Orgg', 'Orion', 'Otter', 'Ouphe', 'Ox', 'Oyster', 'Pangolin',
    'Peasant', 'Pegasus', 'Pentavite', 'Performer', 'Pest', 'Phelddagrif', 'Phoenix', 'Phyrexian', 'Pilot',
    'Pincher', 'Pirate', 'Plant', 'Platypus', 'Porcupine', 'Possum', 'Praetor', 'Primarch', 'Prism',
    'Processor', 'Q', 'Qu', 'Rabbit', 'Raccoon', 'Ranger', 'Rat', 'Rebel', 'Reflection', 'Reveler', 'Rhino',
    'Rigger', 'Robot', 'Rogue', 'Rukh', 'Sable', 'Salamander', 'Samurai', 'Sand', 'Saproling', 'Satyr',
    'Scarecrow', 'Scientist', 'Scion', 'Scorpion', 'Scout', 'Sculpture', 'Seal', 'Serf', 'Serpent', 'Servo',
    'Shade', 'Shaman', 'Shapeshifter', 'Shark', 'Sheep', "Shi'ar", 'Siren', 'Skeleton', 'Skrull', 'Skunk',
    'Slith', 'Sliver', 'Sloth', 'Slug', 'Snail', 'Snake', 'Soldier', 'Soltari', 'Sorcerer', 'Spawn',
    'Specter', 'Spellshaper', 'Sphinx', 'Spider', 'Spike', 'Spirit', 'Splinter', 'Sponge', 'Spy', 'Squid',
    'Squirrel', 'Starfish', 'Surrakar', 'Survivor', 'Symbiote', 'Synth', 'Talosian', 'Teddy', 'Tellarite',
    'Tentacle', 'Tetravite', 'Thalakos', 'Tholian', 'Thopter', 'Thrull', 'Tiefling', 'Time Lord', 'Tosk',
    'Toy', 'Treefolk', 'Trilobite', 'Triskelavite', 'Troll', 'Turtle', 'Tyranid', 'Unicorn', 'Urzan', 'Utrom',
    'Vampire', 'Varmint', 'Vedalken', 'Villain', 'Volver', 'Vorta', 'Vulcan', 'Wall', 'Walrus', 'Warlock',
    'Warrior', 'Weasel', 'Weird', 'Werewolf', 'Whale', 'Wizard', 'Wolf', 'Wolverine', 'Wombat', 'Worm',
    'Wraith', 'Wurm', 'Xindi', 'Yeti', 'Zombie', 'Zubera',
)

_ALIAS_LOW = {k.lower(): v for k, v in KINDRED_ALIASES.items()}
_SNAPSHOT_LOW = {t.lower(): t for t in CREATURE_TYPES_SNAPSHOT}

# Header strip / field labels (UX spec §3.4: title="Archetype: Tokens / Go-Wide").
FIELD_LABELS = {"archetype": "Style", "theme": "Themes", "kindred": "Kindred"}
STRIP_DESKTOP = 4
STRIP_PHONE = 2


# --- Redis-backed archetype/genre registries -------------------------------------------------

def _load_registry(r, key: str, seed: Sequence[dict]) -> List[dict]:
    """The editable list from Redis, seeding it (NX) from `seed` on first read. A key that
    exists but is empty (everything deleted) stays empty -- never re-seeded. r=None (no Redis
    in this call's context) returns the seed as-is, never touching storage."""
    if r is None:
        return [dict(x) for x in seed]
    raw = r.get(key)
    if raw is None:
        if r.set(key, json.dumps(list(seed), ensure_ascii=False), nx=True):
            return [dict(x) for x in seed]
        raw = r.get(key)
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [dict(x) for x in data if isinstance(x, dict) and x.get("id")]


def _save_registry(r, key: str, items: List[dict]) -> None:
    r.set(key, json.dumps(items, ensure_ascii=False))


def list_archetypes(r=None) -> List[dict]:
    """The Style/archetype pick-list: [{id, group, label, desc}, ...]."""
    return _load_registry(r, ARCHETYPES_KEY, SEED_ARCHETYPES)


def list_genres(r=None) -> List[dict]:
    """The Themes pick-list: [{id, group, label, desc}, ...]."""
    return _load_registry(r, GENRES_KEY, SEED_GENRES)


def archetype_ids(r=None) -> Tuple[str, ...]:
    return tuple(a["id"] for a in list_archetypes(r))


def archetype_labels(r=None) -> Dict[str, str]:
    return {a["id"]: a["label"] for a in list_archetypes(r)}


def genre_ids(r=None) -> Tuple[str, ...]:
    return tuple(g["id"] for g in list_genres(r))


def genre_labels(r=None) -> Dict[str, str]:
    return {g["id"]: g["label"] for g in list_genres(r)}


def get_archetype(r, item_id: str) -> dict:
    for a in list_archetypes(r):
        if a["id"] == item_id:
            return a
    raise DeckGenreNotFound(item_id)


def get_genre_item(r, item_id: str) -> dict:
    for g in list_genres(r):
        if g["id"] == item_id:
            return g
    raise DeckGenreNotFound(item_id)


def _slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return s or "item"


def _unique_id(label: str, taken: Iterable[str]) -> str:
    taken, base = set(taken), _slugify(label)
    item_id, n = base, 2
    while item_id in taken:
        item_id, n = f"{base}_{n}", n + 1
    return item_id


def _clean_item_fields(body: Any, group_ids: Iterable[str], *, partial_from: Optional[dict] = None) -> dict:
    """Validated {group, label, desc}. With partial_from, missing keys keep their value."""
    if not isinstance(body, Mapping):
        raise DeckGenreError("Body must be a JSON object.")
    base = partial_from or {}

    def pick(k, default):
        return body[k] if k in body else base.get(k, default)

    label = pick("label", "")
    if not isinstance(label, str) or not label.strip():
        raise DeckGenreError("A label is required.")
    label = " ".join(label.split())
    if len(label) > MAX_LABEL:
        raise DeckGenreError(f"Label is longer than {MAX_LABEL} characters.")
    desc = pick("desc", "")
    if not isinstance(desc, str):
        raise DeckGenreError("desc must be text.")
    desc = desc.strip()
    if len(desc) > MAX_DESC:
        raise DeckGenreError(f"Description is longer than {MAX_DESC} characters.")
    group = pick("group", None)
    group_ids = set(group_ids)
    if group is not None and group not in group_ids:
        raise DeckGenreError(f"group must be one of {sorted(group_ids)} or null.")
    return {"group": group, "label": label, "desc": desc}


def _name_taken(items: List[dict], label: str, skip_id: Optional[str] = None) -> bool:
    return any(x["id"] != skip_id and x.get("label", "").lower() == label.lower() for x in items)


@_locked
def _create_item(r, key: str, seed: Sequence[dict], group_ids: Iterable[str], body: Any) -> dict:
    fields = _clean_item_fields(body, group_ids)
    items = _load_registry(r, key, seed)
    if _name_taken(items, fields["label"]):
        raise DeckGenreError(f"{fields['label']!r} already exists.")
    item = {"id": _unique_id(fields["label"], (x["id"] for x in items)), **fields}
    items.append(item)
    _save_registry(r, key, items)
    return item


@_locked
def _update_item(r, key: str, seed: Sequence[dict], group_ids: Iterable[str], item_id: str, body: Any) -> dict:
    items = _load_registry(r, key, seed)
    for i, x in enumerate(items):
        if x["id"] == item_id:
            fields = _clean_item_fields(body, group_ids, partial_from=x)
            if _name_taken(items, fields["label"], skip_id=item_id):
                raise DeckGenreError(f"{fields['label']!r} already exists.")
            items[i] = {"id": item_id, **fields}
            _save_registry(r, key, items)
            return items[i]
    raise DeckGenreNotFound(item_id)


@_locked
def _delete_item(r, key: str, seed: Sequence[dict], item_id: str) -> None:
    items = _load_registry(r, key, seed)
    kept = [x for x in items if x["id"] != item_id]
    if len(kept) == len(items):
        raise DeckGenreNotFound(item_id)
    _save_registry(r, key, kept)


def create_archetype(r, body: Any) -> dict:
    return _create_item(r, ARCHETYPES_KEY, SEED_ARCHETYPES, _ARCHETYPE_GROUP_IDS, body)


def update_archetype(r, item_id: str, body: Any) -> dict:
    return _update_item(r, ARCHETYPES_KEY, SEED_ARCHETYPES, _ARCHETYPE_GROUP_IDS, item_id, body)


def delete_archetype(r, item_id: str) -> None:
    _delete_item(r, ARCHETYPES_KEY, SEED_ARCHETYPES, item_id)


def create_genre_item(r, body: Any) -> dict:
    return _create_item(r, GENRES_KEY, SEED_GENRES, _GENRE_GROUP_IDS, body)


def update_genre_item(r, item_id: str, body: Any) -> dict:
    return _update_item(r, GENRES_KEY, SEED_GENRES, _GENRE_GROUP_IDS, item_id, body)


def delete_genre_item(r, item_id: str) -> None:
    _delete_item(r, GENRES_KEY, SEED_GENRES, item_id)


def clean_text(value: Any) -> str:
    """Trim and collapse inner whitespace to single spaces."""
    return " ".join(str(value).split())


def _type_map(types: Optional[Iterable[str]]) -> Dict[str, str]:
    if types is None:
        return _SNAPSHOT_LOW
    return {t.lower(): t for t in types}


def canon_kindred(value: Any, types: Optional[Iterable[str]] = None) -> str:
    """Taxonomy §3: trim, case-insensitive catalog match, alias map (Elves -> Elf),
    then strip a trailing "es"/"s" and re-match. No match = the cleaned text (custom)."""
    text = clean_text(value)
    low = text.lower()
    tmap = _type_map(types)
    if low in tmap:
        return tmap[low]
    if low in _ALIAS_LOW:
        return _ALIAS_LOW[low]
    if low.endswith("es") and low[:-2] in tmap:
        return tmap[low[:-2]]
    if low.endswith("s") and low[:-1] in tmap:
        return tmap[low[:-1]]
    return text


def canon_genre(value: Any, r=None) -> str:
    """A seed id or label (case-insensitive) -> the seed id; anything else is custom text."""
    text = clean_text(value)
    low = text.lower()
    labels = genre_labels(r)
    if low.replace(" ", "_") in labels:
        return low.replace(" ", "_")
    for gid, label in labels.items():
        if label.lower() == low:
            return gid
    return text


def is_custom_genre(value: str, r=None) -> bool:
    return value not in genre_labels(r)


def is_custom_kindred(value: str, types: Optional[Iterable[str]] = None) -> bool:
    return str(value).lower() not in _type_map(types)


def _dedupe(values: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for v in values:
        k = v.lower()
        if v and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def normalize_deck_genre(dg: Any, types: Optional[Iterable[str]] = None, r=None) -> Any:
    """Canonicalize + dedupe on save. Never truncates: over-limit lists stay over the
    limit so validation rejects them (400) instead of silently dropping a tag.
    Anything malformed is returned untouched for validate_deck_genre to explain."""
    if not isinstance(dg, Mapping):
        return dg
    out = dict(dg)
    for key, canon in (("genres", lambda v: canon_genre(v, r)), ("kindred", lambda v: canon_kindred(v, types))):
        vals = out.get(key)
        if isinstance(vals, list) and all(isinstance(v, str) for v in vals):
            out[key] = _dedupe([canon(v) for v in vals if v.strip()])
    return out


def normalize_archetypes(ap: Any) -> Any:
    """Archetype rules (UX spec §4): "" means unset; the primary is never also a
    secondary; secondaries are deduped; with no primary, the first secondary is
    promoted (the same rule as removing the primary in the UI)."""
    if not isinstance(ap, Mapping):
        return ap
    out = dict(ap)
    prim = out.get("primary_archetype")
    if isinstance(prim, str) and not prim.strip():
        prim = None
        out["primary_archetype"] = None
    sec = out.get("secondary_archetypes")
    if isinstance(sec, list) and all(isinstance(s, str) for s in sec):
        sec = _dedupe([s.strip() for s in sec if s.strip()])
        if prim is None and sec:
            prim, sec = sec[0], sec[1:]
            out["primary_archetype"] = prim
        out["secondary_archetypes"] = [s for s in sec if s != prim]
    return out


def validate_deck_genre(dg: Any) -> List[str]:
    errors: List[str] = []
    if dg is None:
        return errors
    if not isinstance(dg, Mapping):
        return ["deck_genre must be an object."]
    extra = sorted(set(dg) - {"genres", "kindred"})
    if extra:
        errors.append(f"deck_genre has unknown keys {extra}. Allowed: ['genres', 'kindred']")
    for key, cap in (("genres", MAX_GENRES), ("kindred", MAX_KINDRED)):
        vals = dg.get(key)
        if vals is None:
            continue
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            errors.append(f"deck_genre.{key} must be a list of strings.")
            continue
        if len(vals) > cap:
            errors.append(f"deck_genre.{key} allows at most {cap} values, got {len(vals)}.")
        for v in vals:
            if not v.strip():
                errors.append(f"deck_genre.{key} values cannot be empty.")
            elif len(v) > CUSTOM_MAX_LEN:
                errors.append(f"deck_genre.{key} value '{v[:40]}' is longer than {CUSTOM_MAX_LEN} characters.")
        lows = [v.lower() for v in vals]
        if len(set(lows)) != len(lows):
            errors.append(f"deck_genre.{key} contains duplicates.")
    return errors


def validate_archetype_cardinality(ap: Mapping) -> List[str]:
    """Counts only; enum membership is user_intent.validate_intent's job."""
    errors: List[str] = []
    prim = ap.get("primary_archetype")
    sec = ap.get("secondary_archetypes")
    if not isinstance(sec, list):
        return errors
    if len(sec) > MAX_SECONDARY:
        errors.append(f"archetype_preferences.secondary_archetypes allows at most {MAX_SECONDARY}, got {len(sec)}.")
    if prim is not None and prim in sec:
        errors.append(f"archetype_preferences: '{prim}' cannot be both primary and secondary.")
    if prim is None and sec:
        errors.append("archetype_preferences.secondary_archetypes needs a primary_archetype first.")
    return errors


# --- Creature-type catalog -------------------------------------------------------------------------

def creature_types(r=None) -> List[str]:
    """The Redis catalog when it holds a sane list, else the bundled snapshot. Never raises:
    the journal must not break when Scryfall or Redis is unreachable."""
    if r is not None:
        try:
            raw = r.get(CATALOG_KEY)
            data = json.loads(raw) if raw else None
            if isinstance(data, list) and len(data) >= 100 and all(isinstance(t, str) for t in data):
                return sorted(set(data))
        except Exception:  # noqa: BLE001
            pass
    return list(CREATURE_TYPES_SNAPSHOT)


def refresh_creature_types(r, fetch=None) -> int:
    """Pull Scryfall's catalog into Redis (CATALOG_TTL). `fetch(url) -> dict` is injectable
    for tests; the default uses urllib. Returns the number of types stored."""
    if fetch is None:
        import urllib.request

        def fetch(url):
            req = urllib.request.Request(url, headers={"User-Agent": "commander-lab", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https URL
                return json.loads(resp.read().decode("utf-8"))
    data = (fetch(CATALOG_URL) or {}).get("data")
    if not isinstance(data, list) or len(data) < 100:
        raise ValueError("Scryfall catalog/creature-types returned no usable list.")
    types = sorted({str(t) for t in data if str(t).strip()})
    r.set(CATALOG_KEY, json.dumps(types), ex=CATALOG_TTL)
    return len(types)


# --- View helpers -----------------------------------------------------------------------------------

def page_options(r=None) -> dict:
    """Everything the Journal's TagFields need, rendered into the page with | tojson."""
    return {
        "archetype_groups": list(ARCHETYPE_GROUPS), "archetypes": list_archetypes(r),
        "genre_groups": list(GENRE_GROUPS), "genres": list_genres(r),
        "kindred": {"suggestions": list(KINDRED_SUGGESTIONS), "groups": list(KINDRED_GROUPS),
                    "aliases": dict(KINDRED_ALIASES)},
        "limits": {"archetype": MAX_ARCHETYPES, "theme": MAX_GENRES, "kindred": MAX_KINDRED,
                   "custom_max_len": CUSTOM_MAX_LEN},
    }


def values_of(journal: Optional[Mapping]) -> dict:
    """{archetype: [primary, *secondaries], theme: [...], kindred: [...]} from a public journal."""
    j = journal or {}
    ap = j.get("archetype_preferences") or {}
    dg = j.get("deck_genre") or {}
    prim = ap.get("primary_archetype")
    arch = ([prim] if prim else []) + [s for s in ap.get("secondary_archetypes") or [] if s != prim]
    return {"archetype": arch, "theme": list(dg.get("genres") or []), "kindred": list(dg.get("kindred") or [])}


def label_of(kind: str, value: str, r=None) -> str:
    if kind == "archetype":
        return archetype_labels(r).get(value, value)
    if kind == "theme":
        return genre_labels(r).get(value, value)
    return value


def strip_items(values: Mapping, types: Optional[Iterable[str]] = None, r=None) -> List[dict]:
    """Header order (UX spec §3.4): primary archetype, first kindred, first theme, then the
    remaining secondaries, kindred and themes."""
    a, k, t = list(values.get("archetype") or []), list(values.get("kindred") or []), list(values.get("theme") or [])
    order = []
    if a:
        order.append(("archetype", a[0]))
    if k:
        order.append(("kindred", k[0]))
    if t:
        order.append(("theme", t[0]))
    order += [("archetype", x) for x in a[1:]] + [("kindred", x) for x in k[1:]] + [("theme", x) for x in t[1:]]
    tmap = _type_map(types)
    out = []
    for kind, v in order:
        custom = (kind == "theme" and is_custom_genre(v, r)) or (kind == "kindred" and v.lower() not in tmap)
        lab = label_of(kind, v, r)
        out.append({"kind": kind, "value": v, "label": lab, "custom": custom,
                    "title": f"{FIELD_LABELS[kind]}: {lab}"})
    return out


def header_strip(journal: Optional[Mapping], types: Optional[Iterable[str]] = None, r=None) -> Optional[dict]:
    """The deck-header chip strip (view mode), or None when nothing is set (the strip hides)."""
    items = strip_items(values_of(journal), types, r)
    if not items:
        return None
    for i, it in enumerate(items[:STRIP_DESKTOP]):
        it["rank"] = i + 1

    def more(start):
        rest = [it["label"] for it in items[start:]]
        return {"n": len(rest), "labels": ", ".join(rest)}
    return {"items": items[:STRIP_DESKTOP], "more_desktop": more(STRIP_DESKTOP), "more_phone": more(STRIP_PHONE),
            "count": len(items)}


def migrate_phantom_midrange(rec: Optional[dict]) -> Optional[dict]:
    """Taxonomy §0: journals saved before GN stored the resolver's default "midrange", which no
    UI could pick. A legacy record (no _journal.genre_v) holding midrange with nothing else in
    archetype_preferences is "never set" -> null. Records saved after GN carry genre_v, so a
    deliberate Midrange is never touched. Returns a copy when it changes anything."""
    if not isinstance(rec, dict) or (rec.get("_journal") or {}).get("genre_v"):
        return rec
    ap = rec.get("archetype_preferences")
    if (isinstance(ap, dict) and ap.get("primary_archetype") == "midrange"
            and not ap.get("secondary_archetypes") and not ap.get("excluded_archetypes")
            and not ap.get("custom_themes")):
        rec = dict(rec)
        rec["archetype_preferences"] = dict(ap, primary_archetype=None)
    return rec
