"""
Fixed produce/consume vocabulary for the card interaction graph (AN-1).

Every card is tagged with the RESOURCES it PRODUCES and the RESOURCES it
CONSUMES. An edge A -> B exists when A produces a resource that B consumes;
the edge's reasons are the shared resources and its weight is how many there
are.

The vocabulary is fixed in code on purpose: the LLM tagger is constrained to
these keys, and anything it returns outside them is dropped. Changing the
vocabulary (adding/removing a key or changing what a key means) must bump
VOCAB_VERSION so cached tags are recomputed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

# Bump when a resource is added/removed/redefined; cached tags carrying an
# older version are treated as unseen and retagged.
VOCAB_VERSION = 1


@dataclass(frozen=True)
class Resource:
    key: str
    label: str
    produce: str  # what "produces X" means, used in edge labels and the LLM prompt
    consume: str  # what "consumes X" means
    # Scryfall Tagger oracle tags (otag:...) implying each side.
    produce_otags: Tuple[str, ...] = ()
    consume_otags: Tuple[str, ...] = ()


RESOURCES: Tuple[Resource, ...] = (
    Resource(
        "tokens", "Tokens",
        "creates tokens",
        "cares about tokens (token payoffs, anthems for tokens, needs tokens as fodder)",
        produce_otags=(
            "repeatable-token-generator", "repeatable-creature-tokens",
            "repeatable-artifact-tokens", "repeatable-noncreature-tokens",
            "repeatable-treasures", "repeatable-clues", "repeatable-food",
            "token-doubler", "token-increaser",
        ),
        consume_otags=(
            "synergy-token", "synergy-token-creature", "affinity-for-tokens",
            "tokenfall", "sacrifice-outlet-token", "sac-outlet-token",
        ),
    ),
    Resource(
        "sacrifice", "Sacrifice",
        "is a sacrifice outlet (lets you sacrifice permanents on demand)",
        "pays off sacrificing (whenever you sacrifice ..., or needs permanents sacrificed)",
        produce_otags=(
            "sacrifice-outlet", "sacrifice-outlet-creature", "sacrifice-outlet-permanent",
            "sacrifice-outlet-artifact", "sacrifice-outlet-universal",
            "repeatable-sac-outlet", "repeatable-sacrifice-outlet",
        ),
        consume_otags=("synergy-sacrifice", "sacrifice-matters"),
    ),
    Resource(
        "death", "Creatures dying",
        "makes creatures die (sac outlets, wipes, fight/removal of your own)",
        "triggers when creatures die (aristocrats, death triggers)",
        produce_otags=("sacrifice-outlet-creature",),
        consume_otags=("death-trigger", "life-and-death-trigger-self"),
    ),
    Resource(
        "etb", "Enters the battlefield",
        "puts permanents onto the battlefield or re-triggers ETBs (blink, flicker, cheat into play, creature tokens)",
        "has an enters-the-battlefield trigger (its own or other permanents')",
        produce_otags=(
            "blink", "flicker", "flicker-creature", "flicker-permanent",
            "cheat-into-play", "reanimate-creature",
        ),
        consume_otags=("etb-other-creature", "synergy-leaves-creature"),
    ),
    Resource(
        "counters", "+1/+1 counters",
        "puts +1/+1 (or other) counters on permanents, or proliferates",
        "cares about counters (counter payoffs, proliferate, scales with counters)",
        produce_otags=(
            "counter-doubler", "counter-increaser", "repeatable-pp-counters",
            "repeatable-proliferate",
        ),
        consume_otags=("counters-matter", "synergy-proliferate"),
    ),
    Resource(
        "graveyard", "Graveyard",
        "fills your graveyard (self-mill, discard, loot, surveil)",
        "uses your graveyard (reanimation, recursion, flashback, graveyard fuel)",
        produce_otags=(
            "self-mill", "mill-self", "discard-outlet", "repeatable-loot",
            "repeatable-surveil", "loot",
        ),
        consume_otags=(
            "reanimate", "reanimate-creature", "recursion", "recursion-creature",
            "graveyard-fuel", "castable-from-graveyard", "synergy-mill",
        ),
    ),
    Resource(
        "mana", "Mana",
        "produces extra mana (ramp, rocks, dorks, treasures) beyond a normal land drop",
        "turns extra mana into value (mana sinks, X spells, expensive activations)",
        produce_otags=(
            "ramp", "mana-rock", "mana-dork", "mana-doubler", "mana-ramp",
            "repeatable-treasures",
        ),
        consume_otags=("mana-sink", "mana-dump", "infinite-mana-sink", "bottomless-mana-sink"),
    ),
    Resource(
        "cards", "Cards / draw",
        "draws cards",
        "pays off drawing cards or needs cards in hand (draw triggers, discard outlets)",
        produce_otags=("draw", "draw-engine", "repeatable-draw", "repeatable-card-advantage"),
        consume_otags=("draw-matters",),
    ),
    Resource(
        "life_gain", "Life gain",
        "gains you life",
        "pays off gaining life",
        produce_otags=("lifegain", "life-gain", "repeatable-lifegain"),
        consume_otags=("lifegain-matters", "synergy-lifegain", "lifegain-increaser"),
    ),
    Resource(
        "life_loss", "Opponent life loss",
        "makes opponents lose life (drain, burn to face)",
        "pays off opponents losing life",
        produce_otags=("drain-life", "life-drain"),
        consume_otags=("life-loss-matters",),
    ),
    Resource(
        "attacks", "Attacking",
        "enables or adds attacks (haste, extra combats, attack enablers)",
        "has attack triggers or rewards attacking",
        produce_otags=("extra-combat", "extra-combat-phase", "gives-haste", "extra-attack"),
        consume_otags=("attack-trigger", "attacking-matters"),
    ),
    Resource(
        "spells", "Instants & sorceries",
        "is (or copies) an instant/sorcery spell",
        "triggers on casting instants/sorceries/noncreature spells (magecraft, prowess)",
        produce_otags=(),
        consume_otags=("magecraft", "prowess-anthem", "synergy-instant", "synergy-sorcery"),
    ),
    Resource(
        "artifacts", "Artifacts",
        "is or creates artifacts (incl. Treasure/Clue/Food tokens)",
        "cares about artifacts (artifact count, artifacts entering)",
        produce_otags=(
            "repeatable-artifact-tokens", "repeatable-treasures", "repeatable-clues",
            "repeatable-food",
        ),
        consume_otags=("artifact-matters", "artifact-count-matters", "synergy-artifact", "artifactfall"),
    ),
    Resource(
        "enchantments", "Enchantments",
        "is or creates enchantments",
        "cares about enchantments (constellation, enchantress)",
        produce_otags=("repeatable-enchantment-tokens",),
        consume_otags=("synergy-enchantment",),
    ),
    Resource(
        "lands", "Lands entering",
        "puts extra lands onto the battlefield (extra land drops, land ramp, fetches)",
        "triggers on lands entering (landfall) or scales with lands",
        produce_otags=("extra-land", "land-ramp", "rampant-growth"),
        consume_otags=("landfall", "landfall-other", "lands-matter"),
    ),
)

RESOURCE_BY_KEY: Dict[str, Resource] = {res.key: res for res in RESOURCES}
RESOURCE_KEYS: Tuple[str, ...] = tuple(res.key for res in RESOURCES)


def normalize_resources(values: Iterable) -> List[str]:
    """Keep only known vocabulary keys, deduped, in vocabulary order.

    Accepts loose input (case, spaces/hyphens instead of underscores, labels),
    which is what an LLM tends to send back.
    """
    wanted = set()
    for v in values or []:
        if not isinstance(v, str):
            continue
        k = v.strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")
        if k in RESOURCE_BY_KEY:
            wanted.add(k)
            continue
        for res in RESOURCES:
            if res.label.lower().replace(" ", "_") == k:
                wanted.add(res.key)
    return [k for k in RESOURCE_KEYS if k in wanted]


def otag_queries() -> List[Tuple[str, str, Tuple[str, ...]]]:
    """(resource_key, side, otags) for every non-empty Tagger mapping."""
    out = []
    for res in RESOURCES:
        if res.produce_otags:
            out.append((res.key, "produces", res.produce_otags))
        if res.consume_otags:
            out.append((res.key, "consumes", res.consume_otags))
    return out


def vocab_payload() -> List[dict]:
    """JSON-friendly vocabulary for API responses."""
    return [
        {"key": r.key, "label": r.label, "produces": r.produce, "consumes": r.consume}
        for r in RESOURCES
    ]
