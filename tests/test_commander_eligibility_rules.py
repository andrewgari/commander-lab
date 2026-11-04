"""
One test per commander-eligibility rule (oracle ruling t_6ccb9548, contract t_ea6b8413),
driven by named card fixtures shaped like Scryfall card objects.

Every fixture goes through the production adapters:
  * search_query.facts_from_scryfall -> search_query.card_info (Scryfall path)
  * CardInfo.from_record on the sync-shaped record (cache path)
so the rules are tested on the data shapes the server actually sees, not on
hand-built CardInfo objects.

NOTE: fixtures model Scryfall's documented card-object shape (type_line, power,
toughness, oracle_text, layout, card_faces, legalities). They were written
from the card names and rules, not from a live pull: the Scryfall API was
rate-limiting this host (429) during this run. Re-verify against a live pull
before quoting these as ground truth.

Run: python tests/test_commander_eligibility_rules.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.cards import search_query as sq
from src.server.domain.draft import deck_validator as dv
from src.server.domain.draft.deck_validator import (
    CardCatalog, CardInfo, DraftCard, DraftDeck, RuleCode, validate_deck,
)

ORACLE_CHARIOT = "Crew 4 (Tap any number of other creatures you control with total power 4 or more: This Vehicle becomes an artifact creature until end of turn.)"


def scry(name, type_line, *, oracle="", power=None, toughness=None, layout="normal",
         card_faces=None, all_parts=None, oid=None, legal="legal"):
    """A Scryfall card object (only the fields the rules read)."""
    obj = {
        "object": "card", "oracle_id": oid or name, "name": name, "layout": layout,
        "type_line": type_line, "oracle_text": oracle, "color_identity": [],
        "legalities": {"commander": legal}, "keywords": [],
    }
    if power is not None:
        obj["power"], obj["toughness"] = power, toughness
    if card_faces is not None:
        obj["card_faces"] = card_faces
    if all_parts is not None:
        obj["all_parts"] = all_parts
    return obj


def via_scryfall(obj):
    """Production Scryfall path: facts -> CardInfo used by the validator."""
    return sq.card_info(sq.facts_from_scryfall(obj))


def via_record(obj):
    """Production cache path: a sync-shaped record (Archidekt keys)."""
    name = obj["name"]
    front = (obj.get("type_line") or "").split(" // ")[0]
    left, _, right = front.partition("\u2014")
    supers = [w for w in left.split() if w == "Legendary"]
    types = [w for w in left.split() if w != "Legendary"]
    return CardInfo.from_record({
        "name": name, "types": types, "super_types": supers, "sub_types": right.split(),
        "oracle_text": obj.get("oracle_text") or "", "commander_legality": "legal",
        "power": obj.get("power"), "toughness": obj.get("toughness"),
        "layout": obj.get("layout") or "",
    })


def both(obj):
    """(scryfall_path, record_path) verdicts. Both must agree and be correct."""
    return via_scryfall(obj).can_be_commander, via_record(obj).can_be_commander


# ---------------------------------------------------------------------------
# Fixtures (Scryfall-shaped)
# ---------------------------------------------------------------------------

ESIKAS_CHARIOT = scry("Esika's Chariot", "Legendary Artifact \u2014 Vehicle",
                      oracle=ORACLE_CHARIOT, power="4", toughness="4")
SHORIKAI = scry("Shorikai, Genesis Engine", "Legendary Artifact \u2014 Vehicle",
                oracle="Whenever you draw your second card each turn, create a 1/1 colorless Thopter artifact creature token with flying.",
                power="4", toughness="4")
HEARTHHULL = scry("Hearthhull, the Worldseed", "Legendary Artifact \u2014 Spacecraft",
                  oracle="Station 4 ...", power="3", toughness="4")
ETERNITY_ELEVATOR = scry("The Eternity Elevator", "Legendary Artifact \u2014 Spacecraft",
                         oracle="Station ...")  # no printed P/T

BRISELA_MELD_RESULT = scry(
    "Brisela, Voice of Nightmares", "Legendary Creature \u2014 Eldrazi Angel",
    oracle="Flying\nIf you would begin your draw step, ...", power="9", toughness="9",
    layout="meld",
    all_parts=[{"name": "Brisela, Voice of Nightmares", "component": "meld_result"},
               {"name": "Bruna, the Fading Light", "component": "meld_part"},
               {"name": "Gisela, the Broken Blade", "component": "meld_part"}])

AKKI_TOK_TOK = scry(
    "Akki Lavarunner // Tok-Tok, Volcano Born",
    "Creature \u2014 Goblin Warrior // Legendary Creature \u2014 Goblin Shaman",
    layout="transform",
    card_faces=[
        {"name": "Akki Lavarunner", "type_line": "Creature \u2014 Goblin Warrior",
         "oracle_text": "Haste\nWhenever you ...", "power": "2", "toughness": "2"},
        {"name": "Tok-Tok, Volcano Born", "type_line": "Legendary Creature \u2014 Goblin Shaman",
         "oracle_text": "Trample\nWhenever ...", "power": "1", "toughness": "1"},
    ])

# A planeswalker: no creature type, legendary, with the explicit commander text.
WILL_KENRITH = scry("Will Kenrith", "Legendary Planeswalker \u2014 Will",
                    oracle="Will Kenrith can be your commander.")

# Grist, the Hunger Tide: planeswalker, no "can be your commander" text. It is
# eligible because its front face is a creature in every zone except the battlefield.
GRIST = scry("Grist, the Hunger Tide", "Legendary Planeswalker \u2014 Grist",
             oracle=("As long as Grist isn't on the battlefield, it's a 1/1 Insect "
                     "creature in addition to its other types."))

# Background (modeled): legendary enchantment, Background subtype.
DWARVEN_RELIEF = scry("Dwarven Reinforcements", "Legendary Enchantment \u2014 Background",
                      oracle="Commander creatures you own have ...")
# Choose a Background creature (modeled).
BRUENOR = scry("Bruenor Battlehammer", "Legendary Creature \u2014 Dwarf Barbarian",
               oracle="Choose a Background (You can have a Background as a second commander.)",
               power="6", toughness="5")

FIXTURES = [ESIKAS_CHARIOT, SHORIKAI, HEARTHHULL, ETERNITY_ELEVATOR, BRISELA_MELD_RESULT,
            AKKI_TOK_TOK, WILL_KENRITH, GRIST, DWARVEN_RELIEF, BRUENOR]


def _catalog():
    cards = []
    for obj in FIXTURES:
        cards.append(via_scryfall(obj))
    return CardCatalog(cards)


def _deck(commanders):
    return DraftDeck(commanders=list(commanders),
                     cards=[DraftCard("Plains", 99)], name="rules")


class TestRule1VehicleSpacecraftPT(unittest.TestCase):
    """Legendary Vehicle/Spacecraft WITH printed P/T is eligible; without P/T is not."""

    def test_esikas_chariot_shorikai_hearthhull_eligible(self):
        for obj in (ESIKAS_CHARIOT, SHORIKAI, HEARTHHULL):
            self.assertEqual(both(obj), (True, True), obj["name"])

    def test_eternity_elevator_no_pt_not_eligible(self):
        self.assertEqual(both(ETERNITY_ELEVATOR), (False, False))


class TestRule2FrontFaceOnly(unittest.TestCase):
    """Meld results and back-face-only legendary DFCs are not eligible."""

    def test_brisela_meld_result_not_eligible(self):
        self.assertEqual(both(BRISELA_MELD_RESULT), (False, False))

    def test_akki_tok_tok_back_face_legend_not_eligible(self):
        self.assertEqual(both(AKKI_TOK_TOK), (False, False))

    def test_front_face_legend_dfc_is_eligible(self):
        # Control for rule 2: a legendary FRONT face still counts (Grist // Plague Swarm shape).
        grist_dfc = scry("Grist, Voracious Larva // Grist, the Plague Swarm",
                         "Legendary Creature \u2014 Phyrexian Insect // Legendary Planeswalker \u2014 Grist",
                         layout="transform",
                         card_faces=[{"name": "Grist, Voracious Larva",
                                      "type_line": "Legendary Creature \u2014 Phyrexian Insect",
                                      "oracle_text": "", "power": "3", "toughness": "3"},
                                     {"name": "Grist, the Plague Swarm",
                                      "type_line": "Legendary Planeswalker \u2014 Grist",
                                      "oracle_text": ""}])
        self.assertTrue(via_scryfall(grist_dfc).can_be_commander)


class TestRule3CanBeYourCommanderText(unittest.TestCase):
    def test_planeswalker_with_commander_text_eligible(self):
        self.assertEqual(both(WILL_KENRITH), (True, True))


class TestRule4Grist(unittest.TestCase):
    """Grist, the Hunger Tide: eligible with no commander text, via its creature face.

    Modeled on the Scryfall shape (Legendary Planeswalker — Grist, no commander text).
    Fails on the current code: the planeswalker path has no creature check, and the
    hand-built fixture in the existing suite only passes because it adds types=[Creature].
    """

    def test_grist_eligible_on_scryfall_shape(self):
        self.assertTrue(via_scryfall(GRIST).can_be_commander)

    def test_grist_eligible_on_sync_record_shape(self):
        self.assertTrue(via_record(GRIST).can_be_commander)


class TestRule5Background(unittest.TestCase):
    """Backgrounds are never a solo/first commander; only second beside Choose a Background."""

    def test_background_alone_not_eligible(self):
        self.assertEqual(both(DWARVEN_RELIEF), (False, False))

    def test_background_alone_rejected_by_validator(self):
        cat = _catalog()
        result = validate_deck(_deck([DWARVEN_RELIEF["name"]]), cat)
        self.assertIn(RuleCode.COMMANDER_ILLEGAL, result.codes())

    def test_background_as_second_beside_chooser_passes(self):
        cat = _catalog()
        result = validate_deck(_deck([BRUENOR["name"], DWARVEN_RELIEF["name"]]), cat)
        self.assertNotIn(RuleCode.COMMANDER_ILLEGAL, result.codes())
        self.assertNotIn(RuleCode.PARTNER_INVALID, result.codes())

    def test_background_as_first_of_pair_is_not_treated_as_second(self):
        # Order must not matter: the Background-only pass is keyed on the pair, not the slot.
        cat = _catalog()
        result = validate_deck(_deck([DWARVEN_RELIEF["name"], BRUENOR["name"]]), cat)
        self.assertNotIn(RuleCode.COMMANDER_ILLEGAL, result.codes())
        self.assertNotIn(RuleCode.PARTNER_INVALID, result.codes())


class TestReconstructJsPredicate(unittest.TestCase):
    """Runs the real commanderEligibilityFromCard from reconstruct.html under node
    on the same Scryfall-shaped fixtures. Skipped when node is unavailable."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.node = shutil.which("node")

    def _run(self, cases):
        import json
        import re
        import subprocess
        import tempfile
        if not self.node:
            self.skipTest("node not installed")
        html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "src", "web", "templates", "reconstruct.html"), encoding="utf-8").read()
        m = re.search(r"function commanderEligibilityFromCard\(card\) \{.*?\n    \}\n", html, re.S)
        if m is None:
            self.fail("commanderEligibilityFromCard not found in reconstruct.html")
        script = m.group(0) + "\nconst T = " + json.dumps(cases) + ";\n" + \
            "console.log(JSON.stringify(Object.fromEntries(Object.keys(T).map(k => [k, commanderEligibilityFromCard(T[k])]))));"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
        try:
            out = subprocess.run([self.node, fh.name], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(fh.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        return {k: bool(v.get("ok")) for k, v in json.loads(out.stdout).items()}

    def test_js_predicate_matches_python_rules(self):
        cases = {
            "chariot": ESIKAS_CHARIOT, "shorikai": SHORIKAI, "hearthhull": HEARTHHULL,
            "elevator": ETERNITY_ELEVATOR, "brisela": BRISELA_MELD_RESULT,
            "akki": AKKI_TOK_TOK, "will": WILL_KENRITH, "grist": GRIST,
        }
        got = self._run(cases)
        self.assertEqual(got, {
            "chariot": True, "shorikai": True, "hearthhull": True, "elevator": False,
            "brisela": False, "akki": False, "will": True, "grist": True,
        })

    def test_js_predicate_flags_background_for_slot_gate(self):
        got = self._run({"bg": DWARVEN_RELIEF})
        self.assertEqual(got, {"bg": True})  # Background ok, slot gating lives in checkCommanderLegal


if __name__ == "__main__":
    unittest.main()
