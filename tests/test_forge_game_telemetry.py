"""Forge verbose-log parser (forge_sim.forge_game_telemetry) + e2e through sim_service/sim_store."""
import os, sys, unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.forge import forge_game_telemetry as fgt
from src.server.domain.analytics import sim_service as svc, sim_store as ss
from tests.test_sim_service_and_api import FakeSim, Base, user_deck, put_decks, run_sync, FakeOracle  # noqa: F401
from tests.test_sim_store_and_diff import make_report

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "forge", "verbose_game1.txt")
TEXT = open(FIX, encoding="utf-8").read()
SEATS = {1: "user_417707", 2: "user_901", 3: "Explorers of the Deep", 4: "Endless Punishment"}


class TestParser(unittest.TestCase):
    def players(self):
        blocks = fgt.split_game_blocks(TEXT)
        self.assertEqual(len(blocks), 1)
        return {p["deck"]: p for p in fgt.parse_game(blocks[0], SEATS)}

    def test_cards_and_curve(self):
        p = self.players()
        me = p["user_417707"]
        self.assertEqual(me["cards"]["Arcane Signet"], {"drawn": None, "cast_turn": 2, "stuck": None})
        self.assertEqual(me["curve"][0], {"turn": 1, "land_played": "Rugged Prairie", "mana_spent": None, "spells_cast": 0})
        self.assertEqual([c["turn"] for c in me["curve"]], list(range(1, len(me["curve"]) + 1)))
        # own-turn numbering: seat 2's first turn is global turn 2 but its turn 1
        self.assertEqual(p["user_901"]["cards"]["Sol Ring"]["cast_turn"], 1)

    def test_targeting_suffix_stripped_and_first_cast_kept(self):
        lines = ["Turn: Turn 1 (Ai(1)-x)", "Add To Stack: Ai(1)-x cast Bolt targeting [Foo (3)]",
                 "Turn: Turn 2 (Ai(2)-y)", "Turn: Turn 3 (Ai(1)-x)", "Add To Stack: Ai(1)-x cast Bolt"]
        pl = fgt.parse_game(lines, {1: "a", 2: "b"})[0]
        self.assertEqual(pl["cards"], {"Bolt": {"drawn": None, "cast_turn": 1, "stuck": None}})
        self.assertEqual(pl["curve"][1]["turn"], 2)

    def test_malformed_and_noise_tolerated(self):
        pl = fgt.parse_game(["garbage", "Turn: Turn x (Ai(1)", "Add To Stack: Ai(9)-z cast Foo", "Land: Ai(1)-a played"], {1: "a"})
        self.assertEqual(pl[0]["cards"], {})
        self.assertEqual(fgt.parse_game([], {})[:], [])

    def test_enrich_skips_games_without_verbose_block(self):
        gl = [{"game": 1, "opponents": [{"seat": 2, "deck": "user_901"}]}, {"game": 2, "opponents": []}]
        fgt.enrich_games_log(TEXT, gl, "user_417707")
        self.assertTrue(gl[0]["players"][0]["curve"])
        self.assertNotIn("players", gl[1])


class TestE2E(Base):
    def test_owned_cards_have_samples_nonowned_have_none(self):
        gl = [{"game": 1, "winner": "x", "user_won": False, "total_turns": 19, "win_reason": "r",
               "opponents": [{"seat": 2, "deck": "user_901", "ai": "Reckless"},
                             {"seat": 3, "deck": "Explorers of the Deep", "ai": "Default"},
                             {"seat": 4, "deck": "Endless Punishment", "ai": "Cautious"}]}]
        fgt.enrich_games_log(TEXT, gl, "user_417707")
        rep = make_report(); rep["games_log"] = gl
        run_sync(self.r, self.deck, {"opponent_mode": "specific", "opponents": ["user_901"]}, FakeSim(report=rep), FakeOracle())
        tel = ss.load_latest(self.r, self.deck)["report"]["telemetry"]
        cards = tel["aggregate"]["cards"]
        self.assertEqual(set(cards), {"user_417707", "user_901"})
        sig = cards["user_417707"]["Arcane Signet"]
        self.assertGreater(sig["first_cast_turn"]["n"], 0)
        self.assertGreater(sig["cast"]["n"], 0)
        self.assertEqual(cards["user_901"]["Sol Ring"]["first_cast_turn"]["median"], 1)
        g = tel["games"][0]
        self.assertEqual(set(g["card_metrics"]), {"user_417707", "user_901"})
        self.assertEqual(set(g["curves"]), set(SEATS.values()))  # curves kept for all seats
        self.assertTrue(all(g["curves"].values()))


if __name__ == "__main__":
    unittest.main()
