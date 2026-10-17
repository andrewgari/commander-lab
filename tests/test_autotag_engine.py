"""Tests for the Auto Tag engine (§8 fixtures and invariants)."""

import unittest
from autotag.engine import run_auto_tag, compute_health, AutoTagConfig, ALL_TAGS


# ---------------------------------------------------------------------------
# Fixture Decks (§8)
# ---------------------------------------------------------------------------

FIXTURE_A_KRENKO = {
    "name": "Krenko, Mob Boss",
    "id": "fixture_krenko",
    "commanders": [{
        "name": "Krenko, Mob Boss",
        "type_line": "Legendary Creature — Goblin Warrior",
        "oracle_text": "{T}: Create X 1/1 red Goblin creature tokens, where X is the number of Goblins you control.",
        "cmc": 4,
    }],
    "color_identity": ["R"],
    "color_identity_size": 1,
    "cards": [
        {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1},
        {"name": "Skirk Prospector", "type_line": "Creature — Goblin", "oracle_text": "Sacrifice a Goblin: Add {R}.", "cmc": 1},
        {"name": "Goblin Bombardment", "type_line": "Enchantment", "oracle_text": "Sacrifice a creature: This enchantment deals 1 damage to any target.", "cmc": 2},
        {"name": "Impact Tremors", "type_line": "Enchantment", "oracle_text": "Whenever a creature you control enters, this enchantment deals 1 damage to each opponent.", "cmc": 2},
        {"name": "Goblin Chieftain", "type_line": "Creature — Goblin", "oracle_text": "Haste (This creature can attack and {T} as soon as it comes under your control.)\nOther Goblin creatures you control get +1/+1 and have haste.", "cmc": 3},
        {"name": "Goblin Matron", "type_line": "Creature — Goblin", "oracle_text": "When this creature enters, you may search your library for a Goblin card, reveal that card, put it into your hand, then shuffle.", "cmc": 3},
        {"name": "Legion Warboss", "type_line": "Creature — Goblin Soldier", "oracle_text": "Mentor (Whenever this creature attacks, put a +1/+1 counter on target attacking creature with lesser power.)\nAt the beginning of combat on your turn, create a 1/1 red Goblin creature token. That token gains haste until end of turn and attacks this combat if able.", "cmc": 3},
        {"name": "Chaos Warp", "type_line": "Instant", "oracle_text": "The owner of target permanent shuffles it into their library, then reveals the top card of their library. If it's a permanent card, they put it onto the battlefield.", "cmc": 3},
        {"name": "Blasphemous Act", "type_line": "Sorcery", "oracle_text": "This spell costs {1} less to cast for each creature on the battlefield.\nBlasphemous Act deals 13 damage to each creature.", "cmc": 9},
        {"name": "Skullclamp", "type_line": "Artifact — Equipment", "oracle_text": "Equipped creature gets +1/-1.\nWhenever equipped creature dies, draw two cards.\nEquip {1}", "cmc": 1},
        {"name": "Lightning Greaves", "type_line": "Artifact — Equipment", "oracle_text": "Equipped creature has haste and shroud. (It can't be the target of spells or abilities.)\nEquip {0}", "cmc": 2},
        {"name": "Mountain", "type_line": "Basic Land — Mountain", "oracle_text": "({T}: Add {R}.)", "cmc": 0},
    ],
}

FIXTURE_B_MEREN = {
    "name": "Meren of Clan Nel Toth",
    "id": "fixture_meren",
    "commanders": [{
        "name": "Meren of Clan Nel Toth",
        "type_line": "Legendary Creature — Human Shaman",
        "oracle_text": "Whenever another creature you control dies, you get an experience counter.\nAt the beginning of your end step, choose target creature card in your graveyard. If that card's mana value is less than or equal to the number of experience counters you have, return it to the battlefield.\nOtherwise, put it into your hand.",
        "cmc": 4,
    }],
    "color_identity": ["B", "G"],
    "color_identity_size": 2,
    "cards": [
        {"name": "Sakura-Tribe Elder", "type_line": "Creature — Snake Shaman", "oracle_text": "Sacrifice this creature: Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle.", "cmc": 2},
        {"name": "Viscera Seer", "type_line": "Creature — Vampire Wizard", "oracle_text": "Sacrifice a creature: Scry 1. (Look at the top card of your library. You may put that card on the bottom.)", "cmc": 1},
        {"name": "Blood Artist", "type_line": "Creature — Vampire", "oracle_text": "Whenever this creature or another creature dies, target player loses 1 life and you gain 1 life.", "cmc": 2},
        {"name": "Pitiless Plunderer", "type_line": "Creature — Human Pirate", "oracle_text": "Whenever another creature you control dies, create a Treasure token. (It's an artifact with \"{T}, Sacrifice this token: Add one mana of any color.\")", "cmc": 4},
        {"name": "Eternal Witness", "type_line": "Creature — Human Shaman", "oracle_text": "When this creature enters, you may return target card from your graveyard to your hand.", "cmc": 3},
        {"name": "Spore Frog", "type_line": "Creature — Frog", "oracle_text": "Sacrifice this creature: Prevent all combat damage that would be dealt this turn.", "cmc": 1},
        {"name": "Grisly Salvage", "type_line": "Instant", "oracle_text": "Reveal the top five cards of your library. You may put a creature or land card from among them into your hand. Put the rest into your graveyard.", "cmc": 2},
        {"name": "Ravenous Chupacabra", "type_line": "Creature — Beast Horror", "oracle_text": "When this creature enters, destroy target creature an opponent controls.", "cmc": 4},
        {"name": "Pernicious Deed", "type_line": "Enchantment", "oracle_text": "{X}, Sacrifice this enchantment: Destroy each artifact, creature, and enchantment with mana value X or less.", "cmc": 3},
        {"name": "Demonic Tutor", "type_line": "Sorcery", "oracle_text": "Search your library for a card, put that card into your hand, then shuffle.", "cmc": 2, "game_changer": True},
        {"name": "Phyrexian Tower", "type_line": "Legendary Land", "oracle_text": "{T}: Add {C}.\n{T}, Sacrifice a creature: Add {B}{B}.", "produced_mana": ["B", "C"], "cmc": 0},
        {"name": "Command Tower", "type_line": "Land", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "produced_mana": ["B", "G", "R", "U", "W"], "cmc": 0},
        {"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)", "cmc": 0},
    ],
}

FIXTURE_C_BREYA = {
    "name": "Breya, Etherium Shaper",
    "id": "fixture_breya",
    "commanders": [{
        "name": "Breya, Etherium Shaper",
        "type_line": "Legendary Artifact Creature — Human",
        "oracle_text": "When Breya enters, create two 1/1 blue Thopter artifact creature tokens with flying.\n{2}, Sacrifice two artifacts: Choose one —\n• Breya deals 3 damage to target player or planeswalker.\n• Target creature gets -4/-4 until end of turn.\n• You gain 5 life.",
        "cmc": 4,
    }],
    "color_identity": ["W", "U", "B", "R"],
    "color_identity_size": 4,
    "cards": [
        {"name": "Smothering Tithe", "type_line": "Enchantment", "oracle_text": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token. (It's an artifact with \"{T}, Sacrifice this token: Add one mana of any color.\")", "cmc": 4, "game_changer": True},
        {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1},
        {"name": "Arcane Signet", "type_line": "Artifact", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "produced_mana": ["B", "G", "R", "U", "W"], "cmc": 2},
        {"name": "Etherium Sculptor", "type_line": "Artifact Creature — Vedalken Artificer", "oracle_text": "Artifact spells you cast cost {1} less to cast.", "cmc": 2},
        {"name": "Thopter Foundry", "type_line": "Artifact", "oracle_text": "{1}, Sacrifice a nontoken artifact: Create a 1/1 blue Thopter artifact creature token with flying. You gain 1 life.", "cmc": 2},
        {"name": "Whirler Rogue", "type_line": "Creature — Human Rogue Artificer", "oracle_text": "When this creature enters, create two 1/1 colorless Thopter artifact creature tokens with flying.\nTap two untapped artifacts you control: Target creature can't be blocked this turn.", "cmc": 4},
        {"name": "Swords to Plowshares", "type_line": "Instant", "oracle_text": "Exile target creature. Its controller gains life equal to its power.", "cmc": 1},
        {"name": "Counterspell", "type_line": "Instant", "oracle_text": "Counter target spell.", "cmc": 2},
        {"name": "Darksteel Forge", "type_line": "Artifact", "oracle_text": "Artifacts you control have indestructible. (Effects that say \"destroy\" don't destroy them. Artifact creatures with indestructible can't be destroyed by damage.)", "cmc": 9},
        {"name": "Ashnod's Altar", "type_line": "Artifact", "oracle_text": "Sacrifice a creature: Add {C}{C}.", "cmc": 3},
        {"name": "Seat of the Synod", "type_line": "Artifact Land", "oracle_text": "{T}: Add {U}.", "produced_mana": ["U"], "cmc": 0},
        {"name": "Command Tower", "type_line": "Land", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "produced_mana": ["B", "G", "R", "U", "W"], "cmc": 0},
        {"name": "Island", "type_line": "Basic Land — Island", "oracle_text": "({T}: Add {U}.)", "cmc": 0},
    ],
}


class TestAutoTagFixtures(unittest.TestCase):
    """Test the three worked-example deck fixtures from spec §8."""

    def test_fixture_a_krenko(self):
        res = run_auto_tag(FIXTURE_A_KRENKO)
        card_tags = {c["name"]: {t["tag"] for t in c["tags"]} for c in res["cards"]}

        expected = {
            "Krenko, Mob Boss": {"kindred:goblin", "tokens"},
            "Sol Ring": {"ramp"},
            "Skirk Prospector": {"kindred:goblin", "ramp"},
            "Goblin Bombardment": {"removal", "finisher"},
            "Impact Tremors": {"tokens", "finisher"},
            "Goblin Chieftain": {"kindred:goblin", "tokens"},
            "Goblin Matron": {"kindred:goblin", "tutor"},
            "Legion Warboss": {"kindred:goblin", "tokens"},
            "Chaos Warp": {"removal"},
            "Blasphemous Act": {"board_wipe"},
            "Skullclamp": {"card_advantage"},
            "Lightning Greaves": {"protection"},
            "Mountain": set(),
        }

        for card_name, exp_tags in expected.items():
            self.assertEqual(
                card_tags.get(card_name, set()),
                exp_tags,
                f"Mismatch for {card_name}: got {card_tags.get(card_name)}, expected {exp_tags}",
            )

        # Deck tags: kindred:goblin, tokens
        deck_tag_names = [dt["tag"] for dt in res["deck_tags"]]
        self.assertEqual(deck_tag_names, ["kindred:goblin", "tokens"])

    def test_fixture_b_meren(self):
        res = run_auto_tag(FIXTURE_B_MEREN)
        card_tags = {c["name"]: {t["tag"] for t in c["tags"]} for c in res["cards"]}
        card_flags = {c["name"]: c.get("flags", []) for c in res["cards"]}

        expected = {
            "Meren of Clan Nel Toth": {"sacrifice", "recursion"},
            "Sakura-Tribe Elder": {"ramp", "sacrifice"},
            "Viscera Seer": {"sacrifice"},
            "Blood Artist": {"sacrifice", "finisher"},
            "Pitiless Plunderer": {"sacrifice", "ramp"},
            "Eternal Witness": {"recursion"},
            "Spore Frog": {"protection", "sacrifice"},
            "Grisly Salvage": {"graveyard"},
            "Ravenous Chupacabra": {"removal"},
            "Pernicious Deed": {"board_wipe"},
            "Demonic Tutor": {"tutor"},
            "Phyrexian Tower": {"sacrifice"},
            "Command Tower": {"mana_fixing"},
            "Forest": set(),
        }

        for card_name, exp_tags in expected.items():
            self.assertEqual(
                card_tags.get(card_name, set()),
                exp_tags,
                f"Mismatch for {card_name}: got {card_tags.get(card_name)}, expected {exp_tags}",
            )

        deck_tag_names = [dt["tag"] for dt in res["deck_tags"]]
        self.assertEqual(deck_tag_names, ["sacrifice", "graveyard"])

        # Flags: Demonic Tutor -> game_changer
        self.assertIn("game_changer", card_flags.get("Demonic Tutor", []))

    def test_fixture_c_breya(self):
        res = run_auto_tag(FIXTURE_C_BREYA)
        card_tags = {c["name"]: {t["tag"] for t in c["tags"]} for c in res["cards"]}
        card_flags = {c["name"]: c.get("flags", []) for c in res["cards"]}

        expected = {
            "Breya, Etherium Shaper": {"artifacts", "sacrifice", "removal"},
            "Smothering Tithe": {"ramp", "artifacts"},
            "Sol Ring": {"ramp", "artifacts"},
            "Arcane Signet": {"ramp", "mana_fixing"},
            "Etherium Sculptor": {"artifacts", "ramp"},
            "Thopter Foundry": {"artifacts", "sacrifice"},
            "Whirler Rogue": {"artifacts"},
            "Swords to Plowshares": {"removal"},
            "Counterspell": {"counterspell"},
            "Darksteel Forge": {"artifacts", "protection"},
            "Ashnod's Altar": {"sacrifice", "ramp"},
            "Seat of the Synod": {"artifacts"},
            "Command Tower": {"mana_fixing"},
            "Island": set(),
        }

        for card_name, exp_tags in expected.items():
            self.assertEqual(
                card_tags.get(card_name, set()),
                exp_tags,
                f"Mismatch for {card_name}: got {card_tags.get(card_name)}, expected {exp_tags}",
            )

        deck_tag_names = [dt["tag"] for dt in res["deck_tags"]]
        self.assertEqual(deck_tag_names, ["artifacts", "sacrifice"])

        # Flags: Smothering Tithe -> game_changer
        self.assertIn("game_changer", card_flags.get("Smothering Tithe", []))


class TestHealthHintsUnit(unittest.TestCase):
    """Test health hint unit cases H1-H6 from §8.4."""

    def test_h1_ramp_low(self):
        # Commander MV 6; ramp = 9 -> floor 12 (10 + 2), status low
        deck = {"commander_max_mv": 6, "cards": [{"name": f"Card{i}"} for i in range(99)]}
        card_tag_results = {f"Card{i}": [{"tag": "ramp"}] for i in range(9)}
        hints = compute_health(deck, card_tag_results)
        hint_ramp = next(h for h in hints if h["key"] == "ramp")
        self.assertEqual(hint_ramp["count"], 9)
        self.assertEqual(hint_ramp["floor"], 12)
        self.assertEqual(hint_ramp["status"], "low")

    def test_h2_tokens_board_wipe_ok(self):
        # tokens active; board_wipe = 0 -> floor 0, status ok
        deck = {
            "cards": [{"name": f"Card{i}"} for i in range(99)],
            "_themes": {"tokens": type("Theme", (), {"active": True})()},
        }
        card_tag_results = {}
        hints = compute_health(deck, card_tag_results)
        hint_bw = next(h for h in hints if h["key"] == "board_wipe")
        self.assertEqual(hint_bw["count"], 0)
        self.assertEqual(hint_bw["floor"], 0)
        self.assertEqual(hint_bw["status"], "ok")

    def test_h3_commander_card_advantage_ok(self):
        # Commander's final tags include card_advantage; count = 5 -> floor 4 (7 - 3), status ok
        deck = {
            "commanders": [{"name": "Cmdr", "_final_tags": [{"tag": "card_advantage"}]}],
            "cards": [{"name": f"Card{i}"} for i in range(99)],
        }
        card_tag_results = {f"Card{i}": [{"tag": "card_advantage"}] for i in range(5)}
        hints = compute_health(deck, card_tag_results)
        hint_ca = next(h for h in hints if h["key"] == "card_advantage")
        self.assertEqual(hint_ca["count"], 5)
        self.assertEqual(hint_ca["floor"], 4)
        self.assertEqual(hint_ca["status"], "ok")

    def test_h4_interaction_low(self):
        # removal = 6, counterspell = 3 -> interaction = 9, floor 10, status low
        deck = {"cards": [{"name": f"Card{i}"} for i in range(99)]}
        card_tag_results = {}
        for i in range(6):
            card_tag_results[f"Rem{i}"] = [{"tag": "removal"}]
        for i in range(3):
            card_tag_results[f"Cs{i}"] = [{"tag": "counterspell"}]
        hints = compute_health(deck, card_tag_results)
        hint_int = next(h for h in hints if h["key"] == "interaction")
        self.assertEqual(hint_int["count"], 9)
        self.assertEqual(hint_int["floor"], 10)
        self.assertEqual(hint_int["status"], "low")

    def test_h5_finisher_low(self):
        # finisher = 1 -> floor 2, status low
        deck = {"cards": [{"name": f"Card{i}"} for i in range(99)]}
        card_tag_results = {"Fin0": [{"tag": "finisher"}]}
        hints = compute_health(deck, card_tag_results)
        hint_fn = next(h for h in hints if h["key"] == "finisher")
        self.assertEqual(hint_fn["count"], 1)
        self.assertEqual(hint_fn["floor"], 2)
        self.assertEqual(hint_fn["status"], "low")

    def test_h6_under_90_cards_emits_empty(self):
        # Main deck < 90 cards -> []
        deck = {"cards": [{"name": f"Card{i}"} for i in range(14)]}
        card_tag_results = {"Card0": [{"tag": "ramp"}]}
        hints = compute_health(deck, card_tag_results)
        self.assertEqual(hints, [])


class TestInvariants(unittest.TestCase):
    """Test conservatism and consistency invariants (§8.5)."""

    def test_conservatism_caps(self):
        for fixture in [FIXTURE_A_KRENKO, FIXTURE_B_MEREN, FIXTURE_C_BREYA]:
            res = run_auto_tag(fixture)
            for c in res["cards"]:
                name = c["name"]
                tags = c["tags"]
                is_land = "land" in (c.get("type_line", "").lower()) or name in ("Mountain", "Forest", "Island", "Seat of the Synod", "Phyrexian Tower", "Command Tower")
                if is_land:
                    self.assertLessEqual(len(tags), 2, f"Land {name} has more than 2 tags: {tags}")
                    tag_names = {t["tag"] for t in tags}
                    self.assertNotIn("ramp", tag_names, f"Land {name} received ramp tag")
                else:
                    self.assertLessEqual(len(tags), 3, f"Card {name} has more than 3 tags: {tags}")

    def test_all_tags_in_taxonomy(self):
        for fixture in [FIXTURE_A_KRENKO, FIXTURE_B_MEREN, FIXTURE_C_BREYA]:
            res = run_auto_tag(fixture)
            for c in res["cards"]:
                for t in c["tags"]:
                    tag = t["tag"]
                    if tag.startswith("kindred:"):
                        continue
                    self.assertIn(tag, ALL_TAGS, f"Tag {tag} not in taxonomy")

    def test_max_five_deck_tags(self):
        for fixture in [FIXTURE_A_KRENKO, FIXTURE_B_MEREN, FIXTURE_C_BREYA]:
            res = run_auto_tag(fixture)
            self.assertLessEqual(len(res["deck_tags"]), 5)

    def test_determinism(self):
        res1 = run_auto_tag(FIXTURE_A_KRENKO)
        res2 = run_auto_tag(FIXTURE_A_KRENKO)
        self.assertEqual(res1, res2)

    def test_cross_deck_sol_ring(self):
        res_a = run_auto_tag(FIXTURE_A_KRENKO)
        res_c = run_auto_tag(FIXTURE_C_BREYA)

        sol_a = next(c for c in res_a["cards"] if c["name"] == "Sol Ring")
        sol_c = next(c for c in res_c["cards"] if c["name"] == "Sol Ring")

        tags_a = {t["tag"] for t in sol_a["tags"]}
        tags_c = {t["tag"] for t in sol_c["tags"]}

        self.assertEqual(tags_a, {"ramp"})
        self.assertEqual(tags_c, {"ramp", "artifacts"})

    def test_command_tower_mono_vs_multi(self):
        mono_deck = {
            "name": "Mono-Red",
            "color_identity": ["R"],
            "color_identity_size": 1,
            "commanders": [{"name": "Torbran, Thane of Red Fell", "type_line": "Legendary Creature — Dwarf Noble"}],
            "cards": [
                {"name": "Command Tower", "type_line": "Land", "oracle_text": "{T}: Add one mana of any color in your commander's color identity.", "produced_mana": ["B", "G", "R", "U", "W"]}
            ],
        }
        res_mono = run_auto_tag(mono_deck)
        ct_mono = next(c for c in res_mono["cards"] if c["name"] == "Command Tower")
        self.assertEqual(ct_mono["tags"], [])

        res_b = run_auto_tag(FIXTURE_B_MEREN)
        ct_b = next(c for c in res_b["cards"] if c["name"] == "Command Tower")
        self.assertEqual({t["tag"] for t in ct_b["tags"]}, {"mana_fixing"})

    def test_reminder_text_stripping(self):
        # Without reminder text stripping, Pitiless Plunderer would pick up RAMP-1 and SAC-1 from Treasure reminder
        # With reminder text stripping, it must only get SAC-2 and RAMP-5
        res = run_auto_tag(FIXTURE_B_MEREN)
        pp = next(c for c in res["cards"] if c["name"] == "Pitiless Plunderer")
        pp_tags = {t["tag"] for t in pp["tags"]}
        self.assertEqual(pp_tags, {"sacrifice", "ramp"})
        evidence = {t["tag"]: t["evidence"] for t in pp["tags"]}
        self.assertNotIn("RAMP-1", evidence.get("ramp", []))
        self.assertNotIn("SAC-1", evidence.get("sacrifice", []))


# ---------------------------------------------------------------------------
# Dogfood t_61b734d5 regressions (missed roles / tokens theme).
# Oracle text is verbatim from Scryfall.
# ---------------------------------------------------------------------------

DOGFOOD_CARDS = {
    'Anointed Procession': {'name': 'Anointed Procession', 'type_line': 'Enchantment', 'oracle_text': 'If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.', 'keywords': []},
    'Mavren Fein, Dusk Apostle': {'name': 'Mavren Fein, Dusk Apostle', 'type_line': 'Legendary Creature — Vampire Cleric', 'oracle_text': 'Whenever one or more nontoken Vampires you control attack, create a 1/1 white Vampire creature token with lifelink.', 'keywords': []},
    'Elenda, the Dusk Rose': {'name': 'Elenda, the Dusk Rose', 'type_line': 'Legendary Creature — Vampire Knight', 'oracle_text': "Lifelink\nWhenever another creature dies, put a +1/+1 counter on Elenda.\nWhen Elenda dies, create X 1/1 white Vampire creature tokens with lifelink, where X is Elenda's power.", 'keywords': ['Lifelink']},
    "Olivia's Wrath": {'name': "Olivia's Wrath", 'type_line': 'Sorcery', 'oracle_text': 'Each non-Vampire creature gets -X/-X until end of turn, where X is the number of Vampires you control.', 'keywords': []},
    'Olivia Voldaren': {'name': 'Olivia Voldaren', 'type_line': 'Legendary Creature — Vampire', 'oracle_text': 'Flying\n{1}{R}: Olivia Voldaren deals 1 damage to another target creature. That creature becomes a Vampire in addition to its other types. Put a +1/+1 counter on Olivia Voldaren.\n{3}{B}{B}: Gain control of target Vampire for as long as you control Olivia Voldaren.', 'keywords': ['Flying']},
    'Utopia Sprawl': {'name': 'Utopia Sprawl', 'type_line': 'Enchantment — Aura', 'oracle_text': 'Enchant Forest\nAs this Aura enters, choose a color.\nWhenever enchanted Forest is tapped for mana, its controller adds an additional one mana of the chosen color.', 'keywords': ['Enchant']},
    'Abundant Growth': {'name': 'Abundant Growth', 'type_line': 'Enchantment — Aura', 'oracle_text': 'Enchant land\nWhen this Aura enters, draw a card.\nEnchanted land has "{T}: Add one mana of any color."', 'keywords': ['Enchant']},
    "Kenrith's Transformation": {'name': "Kenrith's Transformation", 'type_line': 'Enchantment — Aura', 'oracle_text': 'Enchant creature\nWhen this Aura enters, draw a card.\nEnchanted creature loses all abilities and is a green Elk creature with base power and toughness 3/3. (It loses all other card types and creature types.)', 'keywords': ['Enchant']},
    'Ponder': {'name': 'Ponder', 'type_line': 'Sorcery', 'oracle_text': 'Look at the top three cards of your library, then put them back in any order. You may shuffle.\nDraw a card.', 'keywords': []},
    'Preordain': {'name': 'Preordain', 'type_line': 'Sorcery', 'oracle_text': 'Scry 2, then draw a card. (To scry 2, look at the top two cards of your library, then put any number of them on the bottom and the rest on top in any order.)', 'keywords': ['Scry']},
    'Expressive Iteration': {'name': 'Expressive Iteration', 'type_line': 'Sorcery', 'oracle_text': 'Look at the top three cards of your library. Put one of them into your hand, put one of them on the bottom of your library, and exile one of them. You may play the exiled card this turn.', 'keywords': []},
    'Grapeshot': {'name': 'Grapeshot', 'type_line': 'Sorcery', 'oracle_text': 'Grapeshot deals 1 damage to any target.\nStorm (When you cast this spell, copy it for each spell cast before it this turn. You may choose new targets for the copies.)', 'keywords': ['Storm']},
}

_EDGAR = {
    "name": "Edgar Markov", "type_line": "Legendary Creature — Vampire Knight",
    "oracle_text": "Eminence — Whenever you cast another Vampire spell, if Edgar Markov is in the command zone or on the battlefield, create a 1/1 black Vampire creature token.\nFirst strike, haste\nWhenever Edgar Markov attacks, put a +1/+1 counter on each Vampire you control.",
    "cmc": 6, "keywords": ["First strike", "Haste"],
}
_GALEA = {"name": "Galea, Kindler of Hope", "type_line": "Legendary Creature — Elf Knight",
          "oracle_text": "Vigilance", "cmc": 3, "keywords": ["Vigilance"]}
_VIVI = {"name": "Vivi Ornitier", "type_line": "Legendary Creature — Wizard",
         "oracle_text": "Whenever you cast a noncreature spell, put a +1/+1 counter on Vivi Ornitier.", "cmc": 2}


def _dogfood_deck(commander, names):
    return {
        "name": "dogfood", "id": "dogfood",
        "commanders": [commander],
        "color_identity": ["W", "B", "R"], "color_identity_size": 3,
        "cards": [dict(DOGFOOD_CARDS[n]) for n in names],
    }


def _tags_of(res, name):
    card = next(c for c in res["cards"] if c["name"] == name)
    return {t["tag"] for t in card["tags"]}


_EDGAR_NAMES = ["Anointed Procession", "Mavren Fein, Dusk Apostle", "Elenda, the Dusk Rose",
                "Olivia's Wrath", "Olivia Voldaren"]
_EDGAR_FILLER = [{"name": f"Vampire {i}", "type_line": "Creature — Vampire", "oracle_text": "", "cmc": 2}
                 for i in range(3)]


class TestDogfoodMissedRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = _dogfood_deck(_EDGAR, _EDGAR_NAMES)
        d["cards"] += [dict(c) for c in _EDGAR_FILLER]
        cls.edgar = run_auto_tag(d)
        cls.galea = run_auto_tag(_dogfood_deck(_GALEA, ["Utopia Sprawl", "Abundant Growth", "Kenrith's Transformation"]))
        cls.vivi = run_auto_tag(_dogfood_deck(_VIVI, ["Ponder", "Preordain", "Expressive Iteration", "Grapeshot"]))

    def test_m1_anointed_procession_tokens(self):
        self.assertIn("tokens", _tags_of(self.edgar, "Anointed Procession"))

    def test_m2_mavren_fein_tokens(self):
        self.assertIn("tokens", _tags_of(self.edgar, "Mavren Fein, Dusk Apostle"))

    def test_m3_elenda_tokens(self):
        self.assertIn("tokens", _tags_of(self.edgar, "Elenda, the Dusk Rose"))

    def test_m4_edgar_deck_tags_include_tokens(self):
        deck_tags = {t["tag"] for t in self.edgar["deck_tags"]}
        self.assertIn("tokens", deck_tags)
        self.assertIn("kindred:vampire", deck_tags)

    def test_m5_olivias_wrath_board_wipe(self):
        self.assertIn("board_wipe", _tags_of(self.edgar, "Olivia's Wrath"))

    def test_m11_olivia_voldaren_removal(self):
        self.assertIn("removal", _tags_of(self.edgar, "Olivia Voldaren"))

    def test_m6_utopia_sprawl_ramp(self):
        self.assertIn("ramp", _tags_of(self.galea, "Utopia Sprawl"))

    def test_m6_abundant_growth_still_ramp(self):
        self.assertIn("ramp", _tags_of(self.galea, "Abundant Growth"))

    def test_m7_kenriths_transformation_removal_and_card_advantage(self):
        tags = _tags_of(self.galea, "Kenrith's Transformation")
        self.assertIn("removal", tags)
        self.assertIn("card_advantage", tags)

    def test_m8_ponder_card_advantage_like_preordain(self):
        self.assertIn("card_advantage", _tags_of(self.vivi, "Preordain"))
        self.assertIn("card_advantage", _tags_of(self.vivi, "Ponder"))

    def test_m9_expressive_iteration_card_advantage(self):
        self.assertIn("card_advantage", _tags_of(self.vivi, "Expressive Iteration"))

    def test_m10_grapeshot_finisher(self):
        self.assertIn("finisher", _tags_of(self.vivi, "Grapeshot"))


if __name__ == "__main__":
    unittest.main()
