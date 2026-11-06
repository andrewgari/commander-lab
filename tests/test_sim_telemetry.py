import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.server.domain.analytics import sim_telemetry as st, sim_store as ss
from tests.test_sim_store_and_diff import FakeRedis, make_deck

OWNED = {"user_417707", "user_2"}


def game(n, cast_a, won=True):
    return {"game": n, "winner": "x", "user_won": won, "total_turns": 10 + n,
            "opponents": [{"seat": 2, "deck": "user_2", "ai": "Default"}, {"seat": 3, "deck": "Cavalry Charge"}],
            "players": [
                {"deck": "user_417707", "curve": [{"turn": 1, "land_played": "Plains"}],
                 "cards": {"Sol Ring": {"drawn": True, "cast_turn": cast_a, "stuck": cast_a is None}}},
                {"deck": "user_2", "cards": {"Foo": {"drawn": True, "cast_turn": 3, "stuck": False}}},
                {"deck": "Cavalry Charge", "curve": [{"turn": 1}], "cards": {"Bar": {"drawn": True, "cast_turn": 2, "stuck": False}}},
            ]}


class T(unittest.TestCase):
    def build(self, games):
        return st.build_telemetry(make_deck(), {"opponent_mode": "specific", "games": len(games), "notes": "n"},
                                  {"games_log": games}, OWNED)

    def test_owned_only(self):
        t = self.build([game(1, 2)])
        cm = t["games"][0]["card_metrics"]
        self.assertEqual(set(cm), {"user_417707", "user_2"})
        self.assertNotIn("Cavalry Charge", t["aggregate"]["cards"])
        self.assertIn("Cavalry Charge", t["games"][0]["curves"])  # curve kept for everyone
        self.assertEqual([p["owned"] for p in t["games"][0]["pod"]], [True, True, False])

    def test_aggregate_samples(self):
        t = self.build([game(1, 2), game(2, None), game(3, 4, won=False)])
        a = t["aggregate"]
        self.assertEqual(a["win_rate"], {"n": 3, "value": 0.6667})
        c = a["cards"]["user_417707"]["Sol Ring"]
        self.assertEqual(c["first_cast_turn"], {"n": 2, "mean": 3.0, "median": 3.0})
        self.assertEqual(c["cast"], {"n": 3, "count": 2})
        self.assertEqual(c["stuck_in_hand"], {"n": 3, "count": 1})

    def test_unavailable_stays_null(self):
        g = game(1, 2); g["players"] = []
        t = self.build([g])
        self.assertEqual(t["aggregate"]["cards"], {})
        self.assertIsNone(self.build([])["aggregate"]["win_rate"]["value"])

    def test_persist_replace_and_failed_keeps(self):
        r, deck = FakeRedis(), make_deck()
        for tag, games in (("a", [game(1, 2)]), ("b", [game(1, 3), game(2, 3)])):
            rep = {"summary": {"games": len(games)}, "telemetry": self.build(games)}
            ss.save_completed(r, deck, {"job_id": tag, "status": "done", "report": rep})
        doc = ss.load_latest(r, deck)
        self.assertEqual(doc["report"]["telemetry"]["aggregate"]["games"], 2)
        ss.save_completed(r, deck, {"job_id": "c", "status": "failed"})
        self.assertEqual(ss.load_latest(r, deck)["report"]["telemetry"]["aggregate"]["games"], 2)


if __name__ == "__main__":
    unittest.main()
