"""Commander roles derived from Scryfall data (commander_roles.py) and the
shared art group that draws them (hud.commanders macro / HUD.commanders).

Card texts below are real Scryfall oracle text / keywords / type lines.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import commander_roles as cr
from app import app, templates

ROOT = Path(__file__).resolve().parent.parent

FRODO = {"type_line": "Legendary Creature \u2014 Halfling Scout", "keywords": ["Vigilance", "Partner with", "Partner"],
         "oracle_text": "Partner with Sam, Loyal Attendant\nVigilance\nWhenever Frodo attacks, ..."}
SAM = {"type_line": "Legendary Creature \u2014 Halfling Peasant", "keywords": ["Food", "Partner with", "Partner"],
       "oracle_text": "Partner with Frodo, Adventurous Hobbit (When this creature enters, ...)"}
FRIENDS = {"type_line": "Legendary Creature \u2014 Human Wizard", "keywords": ["Friends forever"],
           "oracle_text": "Friends forever (You can have two commanders if both have friends forever.)"}
TENTH = {"type_line": "Legendary Creature \u2014 Time Lord Doctor", "keywords": ["Time Travel", "Allons-y!"],
         "oracle_text": "Allons-y! \u2014 Whenever you attack, ..."}
ROSE = {"type_line": "Legendary Creature \u2014 Human", "keywords": ["Doctor's companion", "Bad Wolf"],
        "oracle_text": "Rose Tyler gets +1/+1 ...\nDoctor's companion (You can have two commanders if the other is the Doctor.)"}
KERUGA = {"type_line": "Legendary Creature \u2014 Dinosaur Hippo", "keywords": ["Companion"],
          "oracle_text": "Companion \u2014 Your starting deck contains only cards with mana value 3 or greater and land cards. "
                         "(If this card is your chosen companion, ...)\nWhen Keruga enters, draw a card for each other permanent ..."}
HELGA = {"type_line": "Legendary Creature \u2014 Frog Druid", "keywords": [],
         "oracle_text": "Whenever you cast a creature spell with mana value 4 or greater, ..."}
MINSC = {"type_line": "Legendary Planeswalker \u2014 Minsc", "keywords": [],
         "oracle_text": "When Minsc & Boo enters ...\nMinsc & Boo, Timeless Heroes can be your commander."}
SHORIKAI = {"type_line": "Legendary Artifact \u2014 Vehicle", "keywords": ["Crew"],
            "oracle_text": "{1}, {T}: Draw two cards, then discard a card. ...\nCrew 8"}
KAALIA = {"type_line": "Legendary Creature \u2014 Human Cleric", "keywords": ["Flying"],
          "oracle_text": "Flying\nWhenever Kaalia of the Vast attacks an opponent, ..."}
# Not a companion: mentions the word inside rules text, no "Companion —" ability.
PET = {"type_line": "Legendary Creature \u2014 Cat", "keywords": [],
       "oracle_text": "Whenever another creature you control dies, this creature becomes your companion until end of turn."}


def roles(*cards):
    return [r for r, _ in cr.assign_roles(list(cards))]


class TestCardFlags(unittest.TestCase):
    def test_partner_signals(self):
        for card in (FRODO, SAM, FRIENDS):
            self.assertTrue(cr.card_flags(card)["partner"])
        # Text only (older payloads without keywords) still counts.
        self.assertTrue(cr.card_flags({"type_line": FRODO["type_line"], "oracle_text": FRODO["oracle_text"]})["partner"])
        self.assertTrue(cr.card_flags({"type_line": "Legendary Creature \u2014 Elf", "oracle_text": "Choose a Background"})["partner"])
        self.assertFalse(cr.card_flags(KAALIA)["partner"])

    def test_doctor_and_companion_signals(self):
        self.assertTrue(cr.card_flags(TENTH)["doctor"])
        self.assertFalse(cr.card_flags(ROSE)["doctor"])
        self.assertTrue(cr.card_flags(ROSE)["doctors_companion"])
        self.assertTrue(cr.card_flags(KERUGA)["companion"])
        self.assertTrue(cr.card_flags({"type_line": KERUGA["type_line"], "oracle_text": KERUGA["oracle_text"]})["companion"])
        self.assertFalse(cr.card_flags(ROSE)["companion"])  # "Doctor's companion" is not Companion
        self.assertFalse(cr.card_flags(PET)["companion"])

    def test_can_be_commander_is_generic(self):
        m = cr.card_flags(MINSC)
        self.assertTrue(m["can_be_commander"])
        self.assertEqual(m["type_label"], "Planeswalker")
        s = cr.card_flags(SHORIKAI)
        self.assertTrue(s["can_be_commander"])
        self.assertEqual(s["type_label"], "Vehicle")
        # A future card with the phrase works without any list.
        new = cr.card_flags({"type_line": "Legendary Enchantment \u2014 Saga", "oracle_text": "X can be your commander."})
        self.assertTrue(new["can_be_commander"])
        self.assertEqual(new["type_label"], "Enchantment")
        for card in (KAALIA, ROSE, {"type_line": "Legendary Enchantment \u2014 Background", "oracle_text": ""}):
            self.assertFalse(cr.card_flags(card)["can_be_commander"])

    def test_unknown_card_has_no_signal(self):
        f = cr.card_flags(None)
        self.assertFalse(any(v for k, v in f.items() if k != "type_label"))
        self.assertIsNone(f["type_label"])


class TestAssignRoles(unittest.TestCase):
    def test_partner_pair(self):
        self.assertEqual(roles(FRODO, SAM), ["partner", "partner"])
        self.assertEqual(roles(FRIENDS, FRIENDS), ["partner", "partner"])

    def test_doctors_companion(self):
        self.assertEqual(roles(TENTH, ROSE), ["doctor", "doctors_companion"])
        self.assertEqual(roles(ROSE, TENTH), ["doctors_companion", "doctor"])

    def test_true_companion(self):
        self.assertEqual(roles(KERUGA, HELGA), ["companion", "commander"])
        self.assertEqual(roles(HELGA, KERUGA), ["commander", "companion"])
        # Keruga alone is just the commander.
        self.assertEqual(roles(KERUGA), ["commander"])

    def test_can_be_commander_single(self):
        (role, flags), = cr.assign_roles([MINSC])
        self.assertEqual(role, "commander")
        self.assertTrue(flags["can_be_commander"])

    def test_plain(self):
        self.assertEqual(roles(KAALIA), ["commander"])
        self.assertEqual(roles(), [])
        # Two commanders with no data: still equal partners (no guessing).
        self.assertEqual(roles(None, None), ["partner", "partner"])

    def test_deck_commanders_orders_and_caps(self):
        deck = {"commanders": ["Keruga, the Macrosage", "Helga, Skittish Seer"], "commander_uids": ["k", "h"]}
        rows = cr.deck_commanders(deck, {"Keruga, the Macrosage": KERUGA, "Helga, Skittish Seer": HELGA})
        self.assertEqual([(r["name"], r["uid"], r["role"]) for r in rows],
                         [("Helga, Skittish Seer", "h", "commander"), ("Keruga, the Macrosage", "k", "companion")])
        deck = {"commanders": ["Rose Tyler", "The Tenth Doctor"], "commander_uids": ["r", "t"]}
        rows = cr.deck_commanders(deck, {"Rose Tyler": ROSE, "The Tenth Doctor": TENTH})
        self.assertEqual([r["role"] for r in rows], ["doctor", "doctors_companion"])
        self.assertEqual(rows[0]["uid"], "t")
        # One commander, two printings: one portrait.
        self.assertEqual(len(cr.deck_commanders({"commanders": ["A"], "commander_uids": ["x", "y"]}, {})), 1)
        self.assertEqual(len(cr.deck_commanders({"commander_uids": ["x", "y", "z"]}, {})), 2)
        self.assertEqual(cr.deck_commanders({}, {}), [])


class FakeRedis:
    def __init__(self, data):
        self.data = data

    def get(self, k):
        return self.data.get(k)

    def mget(self, ks):
        return [self.data.get(k) for k in ks]


class TestLoadCards(unittest.TestCase):
    def test_reads_card_meta_then_oracle_then_interactions(self):
        r = FakeRedis({
            "card_meta:Rose Tyler": json.dumps({"type": "Creature", "super_types": ["Legendary"], "sub_types": ["Human"],
                                                "keywords": ["Doctor's companion"], "oracle_text": ROSE["oracle_text"]}),
            "idx:card_name_to_oracle:The Tenth Doctor": "oid-10",
            "card_meta:oid-10": json.dumps({"type": "Creature", "super_types": ["Legendary"],
                                            "sub_types": ["Doctor", "Time Lord"], "oracle_text": ""}),
            # Keruga has no card_meta in prod; the tagger's Scryfall copy has it.
            "card_interactions:Keruga, the Macrosage": json.dumps({"card": {"type_line": KERUGA["type_line"],
                                                                            "oracle_text": KERUGA["oracle_text"]}}),
        })
        cards = cr.load_cards(r, ["Rose Tyler", "The Tenth Doctor", "Keruga, the Macrosage", "Nobody"])
        self.assertEqual(set(cards), {"Rose Tyler", "The Tenth Doctor", "Keruga, the Macrosage"})
        self.assertTrue(cr.card_flags(cards["Rose Tyler"])["doctors_companion"])
        self.assertTrue(cr.card_flags(cards["The Tenth Doctor"])["doctor"])
        self.assertTrue(cr.card_flags(cards["Keruga, the Macrosage"])["companion"])


UID_H, UID_K = "768ef947-85fc-4fb2-bda9-e0e446cd8886", "814cd870-a4da-4987-a69b-5ba6cee136c1"
KERUGA_DECK = {"id": 9100, "name": "The Gift", "color": "GU", "status": "physical", "registry_id": "archidekt:9100",
               "source": "archidekt", "source_id": "9100",
               "commanders": ["Keruga, the Macrosage", "Helga, Skittish Seer"], "commander_uids": [UID_K, UID_H],
               "cards": [{"name": "Helga, Skittish Seer", "quantity": 1}]}


class TestViewsUseRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _cards(self, r, names):
        return {"Keruga, the Macrosage": KERUGA, "Helga, Skittish Seer": HELGA}

    def test_api_decks_ships_roles(self):
        with patch("app.r") as r, patch("app.enrich_decks_with_repo_status", side_effect=lambda d: d), \
                patch("commander_roles.load_cards", side_effect=self._cards):
            r.get.return_value = json.dumps([dict(KERUGA_DECK)])
            res = self.client.get("/api/decks")
        self.assertEqual(res.status_code, 200)
        rows = res.json()["decks"][0]["commander_roles"]
        self.assertEqual([c["role"] for c in rows], ["commander", "companion"])

    def test_deck_header_renders_roles(self):
        with patch("app.registry.list_decks", return_value=[KERUGA_DECK]), \
                patch("app.inventory_view.build_inventory", return_value=[]), \
                patch("commander_roles.load_cards", side_effect=self._cards):
            for path in ("/deck/The%20Gift", "/deck/The%20Gift/stats"):
                with self.subTest(path=path):
                    html = self.client.get(path).text
                    head = html.split('<header class="hud-deckhead">', 1)[1].split("</header>", 1)[0]
                    self.assertIn('class="hud-cmdrs hud-cmdrs--many hud-deckhead-art"', head)  # one main -> not --multi
                    self.assertIn("hud-cmdr--companion", head)
                    self.assertIn("hud-cmdr--commander", head)
                    self.assertLess(head.index(UID_H), head.index(UID_K))  # commander first, companion after
                    self.assertIn("Commander deck · Helga, Skittish Seer · companion Keruga, the Macrosage", head)

    def test_macro_marks_can_be_commander(self):
        m = templates.env.get_template("partials/hud.html").module
        html = str(m.commanders([{"name": "Minsc & Boo", "uid": "0149b45d-b7d4-46bf-ba3a-5137e93ea607",
                                  "role": "commander", "can_be_commander": True, "type_label": "Planeswalker"}]))
        self.assertIn("is-cbc", html)
        self.assertIn('<span class="hud-cmdr-badge">Planeswalker</span>', html)
        self.assertIn("art_crop/front/0/1/", html)
        self.assertIn("hud-cmdr-art", str(m.commanders([])))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_mirror_matches_macro_classes(self):
        js = (ROOT / "static" / "js" / "hud.js").read_text()
        code = ("global.window = {}; global.document = {readyState: 'complete', addEventListener(){}, querySelectorAll(){return []}};\n"
                + js + "\nconst H = window.HUD;\n"
                "const out = {\n"
                " doc: H.commanders([{name:'The Tenth Doctor',uid:'abc',role:'doctor'},{name:'Rose Tyler',uid:'def',role:'doctors_companion'}]),\n"
                " minsc: H.commanders([{name:'Minsc',uid:'0149',role:'commander',can_be_commander:true,type_label:'Planeswalker'}]),\n"
                " legacy: H.commanderRoles({commanders:['A','B'],commander_uids:['x','y','z']}),\n"
                " one: H.commanderRoles({commanders:['A'],commander_uids:['x','y']}),\n"
                "};\nprocess.stdout.write(JSON.stringify(out));")
        res = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertIn('class="hud-cmdrs hud-cmdrs--multi hud-cmdrs--many"', out["doc"])
        self.assertIn("hud-cmdr--doctors-companion", out["doc"])
        self.assertIn('<span class="hud-cmdr-badge">Planeswalker</span>', out["minsc"])
        self.assertEqual([c["role"] for c in out["legacy"]], ["partner", "partner"])
        self.assertEqual([c["uid"] for c in out["one"]], ["x"])


class TestNoRoundCommanderArt(unittest.TestCase):
    def test_no_round_portraits(self):
        decks = (ROOT / "templates" / "decks.html").read_text()
        self.assertNotIn("commander-icon", decks)
        css = (ROOT / "static" / "css" / "hud.css").read_text()
        for rule in re.findall(r"\.hud-cmdr[^{]*\{[^}]*\}", css):
            self.assertNotIn("50%", rule)
        for page in ("decks.html", "deck.html", "partials/deck_head.html"):
            src = (ROOT / "templates" / page).read_text()
            for rule in re.findall(r"\.(?:deck-cmdr|deck-header|hud-cmdr)[^{]*\{[^}]*\}", src):
                self.assertNotIn("border-radius: 50%", rule)


if __name__ == "__main__":
    unittest.main()
