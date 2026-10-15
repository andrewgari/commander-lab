"""PS-2: pure deck personality scorer (analytics/personality.py).

Rules: docs/PERSONALITY_SPEC.md. Synthetic fixture decks check that each
psychographic comes out on top for the deck built for it; the real-deck
fixture (tests/fixtures/personality_real_decks.json, the same live-app
snapshot the spec's hand run used) checks the control deck and the Kaalia
journal example reproduce the spec's numbers exactly. No network, no Redis.

Run: python -m unittest tests.test_personality
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import personality as ps  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "personality_real_decks.json")
AXES = ("timmy", "johnny", "spike", "vorthos", "melvin")


def load_real(deck_id):
    with open(FIXTURE, encoding="utf-8") as f:
        return copy.deepcopy(json.load(f)["decks"][deck_id])


def score_real(deck_id, **override):
    d = load_real(deck_id)
    kw = dict(cards=d["cards"], composition=d["composition"], combos=d["combos"]["in_deck"], graph=d["graph"],
              engines=d["engines"]["engines"], edhrec=d["edhrec"], power=d["power"])
    kw.update(override)
    journal = kw.pop("journal", d["journal"])
    return ps.score_deck_personality(d["deck"], journal, **kw)


def scores(result):
    return {a: getattr(result, a).score for a in AXES}


# ---------------------------------------------------------------------------
# Synthetic decks
# ---------------------------------------------------------------------------

def card(name, type_="Creature", cmc=3, text="", sub=(), sup=(), kw=(), set_="TST"):
    return {"name": name, "type": type_, "super_types": list(sup), "sub_types": list(sub), "keywords": list(kw),
            "oracle_text": text, "cmc": cmc, "set": set_, "ownership_status": "in_deck", "instance_id": name}


def lands(n, prefix="Land"):
    return [card(f"{prefix} {i}", "Land", 0, "{T}: Add {G}.", set_=f"L{i}") for i in range(n)]


def build(commander, rows):
    rows = [commander] + rows
    deck = {"name": "fixture", "registry_id": "test:1", "commanders": [commander["name"]],
            "cards": [{"name": r["name"], "quantity": 1} for r in rows]}
    return deck, {r["name"]: r for r in rows}


def big_creature_pile():
    cmd = card("Ghalta, Primal Hunger", cmc=12, sub=["Dinosaur"], sup=["Legendary"], kw=["Trample"], set_="RIX",
               text="This spell costs {X} less to cast, where X is the total power of creatures you control.\nTrample")
    rows = []
    for i in range(14):
        rows.append(card(f"Colossal Beast {i}", cmc=6 + i % 4, sub=[["Beast", "Giant", "Wurm", "Elemental"][i % 4]],
                         kw=["Trample"], set_=f"S{i % 7}",
                         text="Trample\nWhenever this creature attacks, it gets +X/+X where X is its power."
                         if i % 3 == 0 else "Trample\nThis spell can't be countered."))
    for i in range(14):
        rows.append(card(f"Bear {i}", cmc=3, sub=[["Bear", "Elf", "Wolf"][i % 3]], set_=f"S{i % 9}"))
    for i in range(12):
        rows.append(card(f"Rampant Growth {i}", "Sorcery", 2, set_=f"R{i}",
                         text="Search your library for a basic land card, put it onto the battlefield tapped."))
    rows.append(card("Seize the Day", "Sorcery", 4, "Untap all creatures. After this phase, there is an additional "
                                                    "combat phase.", set_="S1"))
    rows.append(card("Overrun", "Sorcery", 5, "Creatures you control get +3/+3 and gain trample until end of turn.",
                     set_="S2"))
    rows.append(card("Fling", "Instant", 2, "As an additional cost, sacrifice a creature. Fling deals damage equal "
                                            "to the sacrificed creature's power to any target.", set_="S3"))
    return build(cmd, rows + lands(36))


def combo_deck():
    cmd = card("Thrasios, Triton Hero", cmc=2, sub=["Merfolk", "Wizard"], sup=["Legendary"], set_="C16",
               text="{4}: Scry 1, then reveal the top card of your library.")
    rows = [
        card("Demonic Tutor", "Sorcery", 2, "Search your library for a card, put that card into your hand.", set_="A"),
        card("Vampiric Tutor", "Instant", 1, "Search your library for a card, then shuffle and put that card on top.",
             set_="B"),
        card("Mystical Tutor", "Instant", 1, "Search your library for an instant or sorcery card.", set_="C"),
        card("Worldly Tutor", "Instant", 1, "Search your library for a creature card.", set_="D"),
        card("Enlightened Tutor", "Instant", 1, "Search your library for an artifact or enchantment card.", set_="E"),
        card("Mana Crypt", "Artifact", 0, "{T}: Add {C}{C}.", set_="F"),
        card("Sol Ring", "Artifact", 1, "{T}: Add {C}{C}.", set_="G"),
        card("Mana Vault", "Artifact", 1, "{T}: Add {C}{C}{C}.", set_="H"),
        card("Chrome Mox", "Artifact", 0, "Imprint - When this enters, you may exile a card. {T}: Add one mana.",
             set_="I"),
        card("Dramatic Reversal", "Instant", 2, "Untap all nonland permanents you control.", set_="J"),
        card("Isochron Scepter", "Artifact", 2, "Imprint - you may exile an instant card with mana value 2 or less.",
             set_="K"),
        card("Thassa's Oracle", cmc=2, sub=["Merfolk", "Wizard"], set_="L",
             text="When this enters, look at the top X cards of your library. If X is greater than or equal to the "
                  "number of cards in your library, you win the game."),
        card("Demonic Consultation", "Instant", 1, "Name a card. Exile the top six cards of your library.", set_="M"),
        card("Counterspell", "Instant", 2, "Counter target spell.", set_="N"),
        card("Swords to Plowshares", "Instant", 1, "Exile target creature.", set_="O"),
    ]
    for i in range(12):
        rows.append(card(f"Interaction {i}", "Instant", 1 + i % 2, "Counter target spell." if i % 2 else
                         "Destroy target artifact or creature.", set_=f"P{i}"))
    for i in range(10):
        rows.append(card(f"Cantrip {i}", "Instant", 1, "Draw a card.", set_=f"Q{i}"))
    deck, cards = build(cmd, rows + lands(30))
    combos = [{"id": "1", "card_names": ["Thassa's Oracle", "Demonic Consultation"]},
              {"id": "2", "card_names": ["Dramatic Reversal", "Isochron Scepter", "Sol Ring"]},
              {"id": "3", "card_names": ["Thrasios, Triton Hero", "Mana Vault", "Dramatic Reversal"]},
              {"id": "4", "card_names": ["Thassa's Oracle", "Mystical Tutor", "Demonic Consultation"]}]
    names = [r["name"] for r in rows if r["type"] != "Land"] + [cmd["name"]]
    edges = []
    for i, a in enumerate(names):
        for b in names[i + 1:i + 12]:
            edges.append({"source": a, "target": b})
    graph = {"counts": {"nodes": len(names), "edges": len(edges), "untagged": 0}, "edges": edges}
    engines = [{"kind": "chain", "signature": "chain:mana>untap", "cards": names},
               {"kind": "loop", "signature": "loop:untap", "cards": names[:4]}]
    edhrec = {"status": "ok", "per_card": [{"name": n, "inclusion": 0.05, "land": False} for n in names[:20]]}
    return deck, cards, dict(combos=combos, graph=graph, engines=engines, edhrec=edhrec, power=9.0)


def flavor_tribal_deck():
    cmd = card("Edgar Markov", cmc=6, sub=["Vampire", "Knight"], sup=["Legendary"], set_="C17",
               text="Eminence - Whenever you cast another Vampire spell, create a 1/1 black Vampire creature token.")
    rows = []
    for i in range(24):
        legendary = ["Legendary"] if i % 2 == 0 else []
        rows.append(card(f"Sorin's Vampire {i}" if i % 3 else f"Olivia, Bloodline {i}", cmc=2 + i % 3,
                         sub=["Vampire", ["Knight", "Noble", "Rogue"][i % 3]], sup=legendary, set_="VOW",
                         text="Other Vampires you control get +1/+1." if i % 4 == 0 else
                         "Whenever this attacks, Edgar's kin drain each opponent."))
    rows.append(card("The Eldest Reborn", "Enchantment", 5, "(As this Saga enters...) I - Each opponent sacrifices "
                                                            "a creature.", sub=["Saga"], set_="VOW"))
    rows.append(card("Blood Tribute Saga", "Enchantment", 4, "I - Create a Vampire token.", sub=["Saga"], set_="VOW"))
    for i in range(8):
        rows.append(card(f"Vampire Spell {i}", "Sorcery", 3, "Vampires you control gain lifelink until end of turn.",
                         set_="VOW"))
    for i in range(4):
        rows.append(card(f"Talisman {i}", "Artifact", 2, "{T}: Add {B}.", set_=f"T{i}"))
    return build(cmd, rows + lands(36))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ArchetypeFixtures(unittest.TestCase):
    def test_big_creature_pile_is_timmy(self):
        deck, cards = big_creature_pile()
        out = ps.score_deck_personality(deck, cards=cards)
        s = scores(out)
        self.assertEqual(max(s, key=s.get), "timmy", s)
        self.assertEqual(out.leans_toward, "timmy")
        t1 = next(e for e in out.timmy.evidence if e.signal == "T1")
        self.assertEqual(t1.value, 15)  # 14 beasts + Ghalta
        self.assertIn("Ghalta, Primal Hunger", t1.cards)
        self.assertEqual(t1.cards, sorted(t1.cards))

    def test_combo_deck_is_johnny_or_spike(self):
        deck, cards, ctx = combo_deck()
        out = ps.score_deck_personality(deck, cards=cards, **ctx)
        s = scores(out)
        top2 = sorted(AXES, key=lambda a: -s[a])[:2]
        self.assertEqual(set(top2), {"johnny", "spike"}, s)
        self.assertIn(out.leans_toward, ("johnny", "spike"))
        j1 = next(e for e in out.johnny.evidence if e.signal == "J1")
        self.assertEqual(j1.value, 4)
        s2 = next(e for e in out.spike.evidence if e.signal == "S2")
        self.assertEqual(s2.cards, sorted(["Demonic Tutor", "Vampiric Tutor", "Mystical Tutor", "Worldly Tutor",
                                           "Enlightened Tutor"]))
        s7 = next(e for e in out.spike.evidence if e.signal == "S7")
        self.assertIn("Chrome Mox", s7.cards)  # Game Changers from the BR-1 snapshot

    def test_flavor_tribal_deck_is_vorthos(self):
        deck, cards = flavor_tribal_deck()
        out = ps.score_deck_personality(deck, cards=cards)
        s = scores(out)
        self.assertEqual(max(s, key=s.get), "vorthos", s)
        self.assertEqual(out.leans_toward, "vorthos")
        v1 = next(e for e in out.vorthos.evidence if e.signal == "V1")
        self.assertEqual(v1.points, 35.0)
        v4 = next(e for e in out.vorthos.evidence if e.signal == "V4")
        self.assertIn("The Eldest Reborn", v4.cards)


class ControlDeck(unittest.TestCase):
    """The Tenth Doctor + Rose Tyler, PERSONALITY_SPEC section 5."""

    def test_reproduces_spec_scores(self):
        out = score_real("5620189")
        self.assertEqual(scores(out), {"timmy": 51, "johnny": 31, "spike": 39, "vorthos": 76, "melvin": 79})
        self.assertEqual(out.leans_toward, "melvin")
        self.assertEqual(out.runner_up, "vorthos")
        self.assertFalse(out.journal_used)
        self.assertIn("no journal: cards-only score", out.notes)
        self.assertEqual(out.version, ps.PERSONALITY_VERSION)

    def test_per_signal_evidence_matches_spec(self):
        out = score_real("5620189")
        ev = {e.signal: e for a in AXES for e in getattr(out, a).evidence if e.signal != "journal"}
        self.assertEqual(len(ev), 26)  # T1-5, J1-5, S1-7, V1-4, M1-5
        self.assertEqual(ev["T1"].cards, sorted(["Judoon Enforcers", "Star Whale", "Dinosaurs on a Spaceship",
                                                 "Atraxi Warden", "Absolute Virtue"]))
        self.assertEqual(ev["S3"].cards, ["Sol Ring", "Sol Talisman"])
        self.assertEqual(ev["S7"].cards, ["Farewell", "Mystical Tutor"])
        self.assertEqual(ev["S2"].cards, ["Mystical Tutor"])
        self.assertEqual(ev["J1"].value, 0)
        self.assertEqual(ev["M2"].value, "44/64")
        self.assertEqual(len(ev["M2"].cards), 44)
        self.assertEqual(len(ev["V4"].cards), 11)
        self.assertEqual(ev["M1"].value, 9)
        self.assertEqual(ev["M1"].fields, ["keyword=" + k for k in sorted(
            ["buyback", "doctor's companion", "escape", "skulk", "soulbond", "storm", "suspend", "time travel",
             "vanishing"])])
        self.assertIn("All of History, All at Once", ev["M1"].cards)
        self.assertAlmostEqual(ev["V1"].points, 31.18, places=2)
        self.assertAlmostEqual(out.timmy.card_score, 51.22, places=2)
        self.assertAlmostEqual(out.melvin.card_score, 78.54, places=2)
        # weights add to 100 per axis
        for a in AXES:
            sigs = [e for e in getattr(out, a).evidence if e.signal != "journal"]
            self.assertAlmostEqual(sum(e.points for e in sigs), getattr(out, a).card_score, places=1)

    def test_deterministic(self):
        a = score_real("5620189").model_dump()
        b = score_real("5620189").model_dump()
        self.assertEqual(a, b)

    def test_card_rows_as_list_pick_bound_row(self):
        d = load_real("5620189")
        rows = list(d["cards"].values())
        decoy = dict(d["cards"]["Sol Ring"], set="AAA", ownership_status="not_owned", instance_id="0")
        out = score_real("5620189", cards=rows + [decoy])
        self.assertEqual(scores(out)["vorthos"], 76)


class Journal(unittest.TestCase):
    def test_kaalia_journal_example(self):
        """Spec section 4 worked example: Timmy 65->73, Johnny 24->25, Spike 53->55."""
        out = score_real("417707")
        self.assertTrue(out.journal_used)
        self.assertEqual(scores(out), {"timmy": 73, "johnny": 25, "spike": 55, "vorthos": 33, "melvin": 8})
        self.assertEqual(out.timmy.journal_nudge, 8.0)
        self.assertEqual(out.johnny.journal_nudge, 0.5)
        self.assertEqual(out.spike.journal_nudge, 2.5)
        self.assertEqual(out.leans_toward, "timmy")
        fields = [f for e in out.timmy.evidence if e.signal == "journal" for f in e.fields]
        self.assertIn("preferred_win_conditions.primary=combat", fields)

    def test_no_journal_is_cards_only(self):
        with_j = score_real("417707")
        without = score_real("417707", journal=None)
        self.assertFalse(without.journal_used)
        self.assertIn("no journal: cards-only score", without.notes)
        for a in AXES:
            ax = getattr(without, a)
            self.assertEqual(ax.journal_nudge, 0)
            self.assertAlmostEqual(ax.card_score, getattr(with_j, a).card_score)
            rows = [e for e in ax.evidence if e.signal == "journal"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].points, 0)
        self.assertEqual(scores(without)["timmy"], 65)

    def test_unsaved_journal_api_shape_counts_as_none(self):
        """`exists: false` defaults (combat, power 7) must not nudge anything."""
        out = score_real("5620189", journal={"exists": False, "journal": {"preferred_win_conditions":
                                                                          {"primary": "combat"}}})
        self.assertFalse(out.journal_used)
        self.assertEqual(scores(out)["timmy"], 51)

    def test_nudge_can_move_lean(self):
        deck, cards = flavor_tribal_deck()
        base = ps.score_deck_personality(deck, cards=cards)
        j = {"target_power_level": {"scale": 10}, "target_bracket": 5,
             "preferred_win_conditions": {"primary": "lockout", "secondary": ["combo"]},
             "archetype_preferences": {"primary_archetype": "stax"},
             "freeform_notes": "Optimized, efficient, competitive, tuned, consistent cEDH list."}
        out = ps.score_deck_personality(deck, j, cards=cards)
        self.assertEqual(out.spike.journal_nudge, 10.0)  # capped
        self.assertEqual(out.spike.score, min(100, int(base.spike.card_score + 10 + 0.5)))
        self.assertEqual(out.vorthos.score, base.vorthos.score)

    def test_nudge_is_capped_both_ways(self):
        deck, cards = big_creature_pile()
        j = {"target_power_level": {"scale": 1}, "target_bracket": 1,
             "preferred_win_conditions": {"primary": "combat", "disliked_or_excluded": ["combo", "lockout"]},
             "archetype_preferences": {"primary_archetype": "aggro", "secondary_archetypes": ["voltron", "ramp"]},
             "deck_vision": {"description": "big huge fatty stompy smash swing giant massive"}}
        out = ps.score_deck_personality(deck, j, cards=cards)
        self.assertEqual(out.timmy.journal_nudge, 10.0)
        self.assertEqual(out.spike.journal_nudge, -10.0)
        self.assertTrue(out.journal_used)

    def test_lexicon_whole_words_and_per_axis_cap(self):
        deck, cards = big_creature_pile()
        j = {"preferred_win_conditions": {"primary": "lockout"},
             "freeform_notes": "bigger rulesy (no hits) but rules, mechanics, triggers, loops, layers, timing"}
        out = ps.score_deck_personality(deck, j, cards=cards)
        rows = [e for e in out.melvin.evidence if e.signal == "journal"]
        self.assertEqual(sum(e.points for e in rows), 4)
        self.assertEqual(out.timmy.journal_nudge, 0)  # "bigger" is not "big"


class Degraded(unittest.TestCase):
    def test_missing_sources_score_zero_with_notes(self):
        out = score_real("5620189", combos=None, graph=None, engines=None, edhrec=None, power=None)
        ev = {e.signal: e for a in AXES for e in getattr(out, a).evidence}
        for sig in ("J1", "S6", "J2", "J4", "J5", "M3", "J3", "S1"):
            self.assertEqual(ev[sig].points, 0, sig)
        for note in ("combos unavailable", "interaction graph missing or stale", "EDHREC unavailable",
                     "power estimate unavailable", "no journal: cards-only score"):
            self.assertIn(note, out.notes)
        self.assertEqual(scores(out)["vorthos"], 76)  # card-only axes unaffected

    def test_stale_graph(self):
        d = load_real("5620189")
        g = d["graph"]
        g["counts"]["untagged"] = int(g["counts"]["nodes"] * 0.3)
        out = score_real("5620189", graph=g)
        self.assertIn("interaction graph missing or stale", out.notes)
        self.assertEqual(next(e for e in out.johnny.evidence if e.signal == "J2").points, 0)

    def test_edhrec_not_ok(self):
        out = score_real("5620189", edhrec={"status": "unavailable", "per_card": []})
        self.assertIn("EDHREC unavailable", out.notes)

    def test_bracket_check_failure(self):
        out = score_real("5620189", game_changers=object())  # not iterable -> check raises
        self.assertIn("bracket check unavailable", out.notes)
        self.assertEqual(next(e for e in out.spike.evidence if e.signal == "S7").points, 0)

    def test_cards_without_metadata(self):
        d = load_real("5620189")
        cards = dict(d["cards"])
        for n in sorted(cards)[:6]:
            del cards[n]
        out = score_real("5620189", cards=cards)
        self.assertIn("6 cards without metadata", out.notes)

    def test_empty_deck_never_raises(self):
        out = ps.score_deck_personality({"name": "x", "commanders": [], "cards": []})
        self.assertEqual(scores(out)["spike"], 0)  # S5 undefined -> 0, not full points
        self.assertEqual(out.leans_toward, "balanced")
        self.assertIn("no journal: cards-only score", out.notes)

    def test_composition_computed_when_missing(self):
        out_given = score_real("5620189")
        out_computed = score_real("5620189", composition=None)
        # computed from the inventory rows; same ramp/interaction counts for this deck
        self.assertEqual(out_computed.timmy.score, out_given.timmy.score)


class LeansRule(unittest.TestCase):
    def test_rule(self):
        f = ps.leans_toward
        self.assertEqual(f({"timmy": 34, "johnny": 10, "spike": 0, "vorthos": 0, "melvin": 0}), ("balanced", None))
        self.assertEqual(f({"timmy": 50, "johnny": 48, "spike": 43, "vorthos": 0, "melvin": 0}), ("balanced", None))
        self.assertEqual(f({"timmy": 50, "johnny": 48, "spike": 42, "vorthos": 0, "melvin": 0}), ("timmy", "johnny"))
        self.assertEqual(f({"timmy": 50, "johnny": 44, "spike": 0, "vorthos": 0, "melvin": 0}), ("timmy", None))
        self.assertEqual(f({"timmy": 60, "johnny": 0, "spike": 0, "vorthos": 0, "melvin": 60}), ("timmy", "melvin"))

    def test_round_half_up(self):
        self.assertEqual(ps._round(50.5), 51)
        self.assertEqual(ps._round(51.5), 52)
        self.assertEqual(ps._round(-0.2), 0)
        self.assertEqual(ps._round(100.7), 100)


class CopilotRegressions(unittest.TestCase):
    def test_half_up_uses_exact_total(self):
        """63 nonland, 15 creatures (4 big), 6 splashy, 5 ramp, 7 interaction: Timmy is exactly 46.5 -> 47."""
        cmd = card("Cmd", "Enchantment", 2, sup=["Legendary"])
        rows = [card(f"Big {i}", cmc=7) for i in range(4)] + [card(f"Small {i}", cmc=2) for i in range(11)]
        rows += [card(f"Splash {i}", "Sorcery", 3, "Take an extra turn after this one.") for i in range(6)]
        rows += [card(f"Plain {i}", "Sorcery", 3) for i in range(63 - 1 - 15 - 6)]
        deck, cards = build(cmd, rows)
        comp = {"actual": {"ramp": 5, "targeted_disruption": 7, "mass_disruption": 0}, "cards_by_row": {}}
        out = ps.score_deck_personality(deck, cards=cards, composition=comp)
        exact = 30 * 3 / 7 + 15 * ramp_(15 / 63, 0.20, 0.45) + 0 + 25 * 5 / 7 + 15 * (1 - 0.1)
        self.assertAlmostEqual(exact, 46.5, places=9)
        self.assertEqual(out.timmy.score, 47)

    def test_full_type_line_wins_over_primary_type(self):
        row = card("Steel Golem", "Artifact", 7, sub=["Golem"])
        row["type_line"] = "Artifact Creature \u2014 Golem"
        deck, cards = build(card("Cmd", "Enchantment", 2, sup=["Legendary"]), [row])
        out = ps.score_deck_personality(deck, cards=cards)
        self.assertIn("Steel Golem", next(e for e in out.timmy.evidence if e.signal == "T1").cards)
        dryad = card("Dryad Arbor", "Land", 0, sub=["Forest", "Dryad"])
        dryad["type_line"] = "Land Creature \u2014 Forest Dryad"
        deck, cards = build(card("Cmd", "Enchantment", 2, sup=["Legendary"]), [dryad])
        out = ps.score_deck_personality(deck, cards=cards)
        self.assertEqual(next(e for e in out.timmy.evidence if e.signal == "T2").value, "1/2")

    def test_j3_uses_edhrec_rows_without_inventory(self):
        deck = {"name": "digital", "commanders": ["Cmd"], "cards": [{"name": f"C{i}", "quantity": 1}
                                                                     for i in range(10)]}
        ed = {"status": "ok", "per_card": [{"name": f"C{i}", "inclusion": 0.01 if i < 5 else 0.5, "land": False}
                                           for i in range(10)] + [{"name": "Cmd", "inclusion": 0.0, "land": False}]}
        out = ps.score_deck_personality(deck, edhrec=ed)
        j3 = next(e for e in out.johnny.evidence if e.signal == "J3")
        self.assertEqual(j3.value, "5/10")
        self.assertEqual(j3.points, 20.0)

    def test_j4_commander_without_metadata(self):
        rows = [card(f"N{i}") for i in range(4)]
        deck = {"name": "x", "commanders": ["Ghost Cmd"],
                "cards": [{"name": "Ghost Cmd", "quantity": 1}] + [{"name": r["name"], "quantity": 1} for r in rows]}
        graph = {"counts": {"nodes": 5, "edges": 2, "untagged": 0},
                 "edges": [{"source": "Ghost Cmd", "target": "N0"}, {"source": "N1", "target": "Ghost Cmd"}]}
        out = ps.score_deck_personality(deck, cards={r["name"]: r for r in rows}, graph=graph, engines=[])
        self.assertEqual(next(e for e in out.johnny.evidence if e.signal == "J4").cards, ["N0", "N1"])

    def test_empty_set_is_not_a_shared_set(self):
        rows = [card(f"N{i}", "Sorcery", 3, set_="") for i in range(10)]
        deck, cards = build(card("Cmd", "Enchantment", 2, sup=["Legendary"], set_=""), rows)
        out = ps.score_deck_personality(deck, cards=cards)
        self.assertEqual(next(e for e in out.vorthos.evidence if e.signal == "V1").points, 0)


def ramp_(x, lo, hi):
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


if __name__ == "__main__":
    unittest.main()
