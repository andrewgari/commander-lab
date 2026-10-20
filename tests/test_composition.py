"""
AN-3: composition vs the Command Zone 2025 template.

Cards are classified into Lands / Ramp / Card advantage / Targeted
disruption / Mass disruption from type line + oracle text (deterministic).
Plan = every card in none of those rows. A card can count in two rows.
Counts are by quantity (basics count 5x), commander included: the template
totals 100.

The Holy Hellfire fixture is the real deck from prod (87 unique names,
100 cards) with the card text cached by the AN-1 tagger.

Run: python -m pytest tests/test_composition.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import composition as comp
from tests.test_interactions import FakeRedis

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "holy_hellfire_cards.json")


def load_hh():
    with open(FIXTURE, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["deck"], data["tags"]


def card(type_line, text="", cost=""):
    return {"type_line": type_line, "oracle_text": text, "mana_cost": cost}


class TestTemplate(unittest.TestCase):
    def test_default_is_command_zone_2025(self):
        t = comp.DEFAULT_TEMPLATE
        self.assertEqual(
            [t[k] for k in comp.ROW_KEYS],
            [38, 10, 12, 12, 6, 22],
        )
        self.assertEqual(sum(t[k] for k in comp.ROW_KEYS), 100)
        self.assertIn("658", comp.TEMPLATE_SOURCE)

    def test_rows_are_in_spec_order(self):
        self.assertEqual(comp.ROW_KEYS, ("lands", "ramp", "card_advantage",
                                         "targeted_disruption", "mass_disruption", "plan"))

    def test_override_round_trip_and_reset(self):
        r = FakeRedis()
        deck = {"registry_id": "archidekt:1", "id": 1}
        self.assertFalse(comp.get_template(r, deck)["overridden"])
        saved = comp.set_template_override(r, deck, {"lands": 36, "ramp": 12})
        self.assertTrue(saved["overridden"])
        self.assertEqual(saved["lands"], 36)
        self.assertEqual(saved["ramp"], 12)
        self.assertEqual(saved["mass_disruption"], 6)  # untouched rows keep the default
        self.assertEqual(comp.get_template(r, deck)["lands"], 36)
        self.assertIn("composition:template:archidekt:1", r.store)
        comp.reset_template(r, deck)
        self.assertFalse(comp.get_template(r, deck)["overridden"])
        self.assertEqual(comp.get_template(r, deck)["lands"], 38)

    def test_override_rejects_bad_values(self):
        r = FakeRedis()
        deck = {"registry_id": "archidekt:1"}
        for bad in ({"lands": -1}, {"lands": "x"}, {"bogus": 3}, {"lands": 101}):
            with self.assertRaises(comp.CompositionError):
                comp.set_template_override(r, deck, bad)


class TestClassifier(unittest.TestCase):
    def rows(self, c, name="X"):
        return comp.classify_card(name, c)

    def test_lands(self):
        self.assertEqual(self.rows(card("Basic Land — Plains")), ["lands"])
        self.assertEqual(self.rows(card("Land", "{T}: Add {C}.")), ["lands"])  # a land is never also ramp

    def test_ramp(self):
        self.assertIn("ramp", self.rows(card("Artifact", "{T}: Add {C}{C}.")))
        self.assertIn("ramp", self.rows(card("Creature — Elf Druid", "{T}: Add {G}.")))
        self.assertIn("ramp", self.rows(card("Sorcery", "Search your library for up to two basic land cards, "
                                                         "put them onto the battlefield tapped, then shuffle.")))
        self.assertIn("ramp", self.rows(card("Artifact", 'Lands you control have "{T}: Add one mana of any color."')))
        # Path to Exile ramps the opponent, not you.
        self.assertNotIn("ramp", self.rows(card("Instant", "Exile target creature. Its controller may search their "
                                                            "library for a basic land card, put that card onto the "
                                                            "battlefield tapped, then shuffle.")))

    def test_card_advantage(self):
        self.assertIn("card_advantage", self.rows(card("Enchantment", "At the beginning of your upkeep, you draw a "
                                                                       "card and lose 1 life.")))
        self.assertIn("card_advantage", self.rows(card("Creature", "Whenever you lose life, draw that many cards.")))
        self.assertIn("card_advantage", self.rows(card("Enchantment", "Pay 1 life: Exile the top card of your library "
                                                                       "face down. Put that card into your hand at the "
                                                                       "beginning of your next end step.")))
        # Tutors are card selection, not card advantage (CZ 2025).
        self.assertNotIn("card_advantage", self.rows(card("Sorcery", "Search your library for a card, put that card "
                                                                      "into your hand, then shuffle.")))

    def test_targeted_disruption(self):
        for text in ("Destroy target creature. It can't be regenerated.",
                     "Exile target nonland permanent. You lose 3 life.",
                     "Counter target spell.",
                     "When this creature enters, you may exile up to three other target creatures from the battlefield.",
                     "It deals X damage to any target, where X is the number of Dragons you control.",
                     "Each opponent sacrifices a creature with the greatest power among creatures that player controls.",
                     "Return target nonland permanent to its owner's hand."):
            self.assertIn("targeted_disruption", self.rows(card("Instant", text)), text)
        self.assertNotIn("targeted_disruption", self.rows(card("Creature", "Flying, haste")))

    def test_mass_disruption(self):
        for text in ("Destroy all creatures. They can't be regenerated.",
                     "Choose one — • Exile all artifacts. • Exile all creatures.",
                     "Destroy each nonland permanent with mana value X whose controller was dealt combat damage.",
                     "At the beginning of each opponent's end step, that player chooses up to two creatures they "
                     "control, then sacrifices the rest.",
                     "Whenever this creature deals combat damage to a player, it deals that much damage to each "
                     "creature that player controls."):
            self.assertIn("mass_disruption", self.rows(card("Sorcery", text)), text)
        # Burn to each opponent's face is not mass disruption.
        self.assertNotIn("mass_disruption", self.rows(card("Instant", "It deals 2 damage to each opponent.")))

    def test_reminder_text_is_ignored(self):
        self.assertEqual(self.rows(card("Creature", "Flying (This creature can't be blocked except by creatures "
                                                     "with flying. Destroy all creatures.)")), [])


class TestHolyHellfire(unittest.TestCase):
    """Real deck: hand-checked expectations for every row."""

    @classmethod
    def setUpClass(cls):
        cls.deck, cls.tags = load_hh()
        cls.out = comp.compute_composition(cls.deck, {n: t["card"] for n, t in cls.tags.items()})

    def members(self, row):
        return set(self.out["cards_by_row"][row])

    def test_totals(self):
        self.assertEqual(self.out["total_cards"], 100)
        a = self.out["actual"]
        self.assertEqual(a["lands"], 35)
        self.assertEqual(a["ramp"], 6)
        self.assertEqual(a["card_advantage"], 5)
        self.assertEqual(a["targeted_disruption"], 9)
        self.assertEqual(a["mass_disruption"], 7)
        self.assertEqual(a["plan"], 39)

    def test_row_members(self):
        self.assertEqual(self.members("ramp"), {"Arcane Signet", "Boros Signet", "Chromatic Lantern",
                                                "Orzhov Signet", "Rakdos Signet", "Sol Ring"})
        self.assertEqual(self.members("card_advantage"), {"Bloodgift Demon", "Firemane Commando", "Necropotence",
                                                          "Phyrexian Arena", "Vilis, Broker of Blood"})
        self.assertEqual(self.members("mass_disruption"), {"Archfiend of Depravity", "Austere Command",
                                                           "Balefire Dragon", "Merciless Eviction",
                                                           "Ruinous Ultimatum", "Steel Hellkite", "Wrath of God"})
        self.assertEqual(self.members("targeted_disruption"), {
            "Angel of Despair", "Angel of Serenity", "Anguished Unmaking", "Crackling Doom", "Dragon Tempest",
            "Mortify", "Path to Exile", "Terminate", "Vilis, Broker of Blood"})
        self.assertIn("Kaalia of the Vast", self.members("plan"))
        self.assertIn("Demonic Tutor", self.members("plan"))
        self.assertIn("Temple of the False God", self.members("lands"))

    def test_by_card_and_double_counting(self):
        self.assertEqual(self.out["by_card"]["Vilis, Broker of Blood"], ["card_advantage", "targeted_disruption"])
        self.assertEqual(self.out["by_card"]["Mountain"], ["lands"])

    def test_rows_vs_template(self):
        rows = {row["key"]: row for row in self.out["rows"]}
        self.assertEqual(list(rows), list(comp.ROW_KEYS))
        self.assertEqual(rows["lands"]["target"], 38)
        self.assertEqual(rows["lands"]["delta"], -3)
        self.assertEqual(rows["lands"]["status"], "short")
        self.assertEqual(rows["mass_disruption"]["delta"], 1)
        self.assertEqual(rows["mass_disruption"]["status"], "ok")
        self.assertEqual(rows["plan"]["status"], "over")
        self.assertEqual(self.out["template"]["name"], "Default")

    def test_unknown_card_text_goes_to_plan_and_is_flagged(self):
        out = comp.compute_composition({"commanders": ["A"], "cards": [{"name": "A", "quantity": 1},
                                                                         {"name": "B", "quantity": 1}]},
                                       {"A": card("Legendary Creature")})
        self.assertEqual(out["unknown_cards"], ["B"])
        self.assertEqual(out["actual"]["plan"], 2)


class TestDeltaStatus(unittest.TestCase):
    def test_d8_rules(self):
        self.assertEqual(comp.delta_status(0), "ok")
        self.assertEqual(comp.delta_status(1), "ok")
        self.assertEqual(comp.delta_status(-1), "ok")
        self.assertEqual(comp.delta_status(-2), "short")
        self.assertEqual(comp.delta_status(2), "over")


class TestCompositionRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import app
        self.client = TestClient(app)
        self.deck, self.tags = load_hh()

    @patch("app.insights")
    @patch("app.registry")
    def test_get(self, mock_registry, mock_service):
        mock_registry.find_deck.return_value = self.deck
        mock_service.deck_composition.return_value = {"actual": {"lands": 35}}
        res = self.client.get("/api/decks/archidekt:417707/composition")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["composition"]["actual"]["lands"], 35)

    @patch("app.registry")
    def test_put_and_delete_template(self, mock_registry):
        import app as app_mod
        fake = FakeRedis()
        mock_registry.find_deck.return_value = self.deck
        with patch.object(app_mod, "r", fake):
            res = self.client.put("/api/decks/archidekt:417707/composition/template", json={"lands": 37})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["template"]["lands"], 37)
            bad = self.client.put("/api/decks/archidekt:417707/composition/template", json={"lands": -4})
            self.assertEqual(bad.status_code, 400)
            res = self.client.delete("/api/decks/archidekt:417707/composition/template")
            self.assertEqual(res.json()["template"]["lands"], 38)

    @patch("app.registry")
    def test_unknown_deck(self, mock_registry):
        mock_registry.find_deck.return_value = None
        mock_registry.list_decks.return_value = []
        self.assertEqual(self.client.get("/api/decks/nope/composition").status_code, 404)


if __name__ == "__main__":
    unittest.main()
