"""Seed the card catalog the Brew gate validates against (card_meta:{name})."""
import json
from unittest import mock

import src.server.domain.draft.brew_gate as brew_gate

# name -> (color identity, type, oracle_text)
CARDS = {
    "Atraxa, Praetors' Voice": ("WUBG", "Legendary Creature"),
    "Sol Ring": ("", "Artifact"),
    "Arcane Signet": ("", "Artifact"),
    "Doubling Season": ("G", "Enchantment"),
    "Demonic Tutor": ("B", "Sorcery"),
    "Cyclonic Rift": ("U", "Instant"),
    "Command Tower": ("", "Land"),
    "Lightning Greaves": ("", "Artifact"),
    "Mana Crypt": ("", "Artifact"),
    "Forest": ("G", "Basic Land"),
}


# Cards the donor/inventory fixtures already use; seeding more would put them
# in the Tier 3 pools and change what the builders pick.
GATE_CORE = ("Atraxa, Praetors' Voice", "Sol Ring", "Arcane Signet", "Doubling Season",
             "Demonic Tutor", "Lightning Greaves", "Mana Crypt", "Forest")


def seed_gate_catalog(r, extra=None, only=None):
    cards = {k: v for k, v in CARDS.items() if only is None or k in only}
    for name, (ident, type_) in {**cards, **(extra or {})}.items():
        r.set(f"card_meta:{name}", json.dumps({
            "name": name, "color": ident, "type": type_,
            "commander_legality": "banned" if name == "Mana Crypt" else "legal"}))


def pin_gate_fill(testcase, staples=()):
    """Keep the gate's top-up deterministic (basics only) so tests that count
    or add specific cards are unaffected by curated staples."""
    p = mock.patch.object(brew_gate, "STAPLES", tuple(staples))
    p.start()
    testcase.addCleanup(p.stop)
