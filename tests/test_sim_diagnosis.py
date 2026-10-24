import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analytics import sim_diagnosis as sd

TID = "user_1"


def tel(n, miss3=0.4, cards=None, with_curve=True):
    games = []
    for i in range(n):
        curve = [{"turn": t, "land_played": not (t == 3 and i < n * miss3)} for t in (1, 2, 3, 4)] if with_curve else []
        games.append({"user_won": i % 2 == 0, "total_turns": 9, "curves": {TID: curve} if with_curve else {}})
    return {"deck_id": TID, "games": games,
            "aggregate": {"games": n, "win_rate": {"n": n, "value": 0.5}, "game_length": {"n": n, "mean": 9.0, "median": 9},
                          "cards": {TID: cards or {}}}}


REVIEW = {"composition": {"rows": [{"key": "ramp", "actual": 5, "target": 10}, {"key": "card_advantage", "actual": 10, "target": 8}]}}


class T(unittest.TestCase):
    def test_insufficient_sample_no_claims(self):
        d = sd.build_findings(tel(4), {}, REVIEW)
        self.assertEqual(d["status"], "insufficient_sample")
        self.assertFalse([f for f in d["findings"] if f["sim_validated"] and f["status"] == "supported"])
        self.assertEqual(d["suggested_cuts"], [])

    def test_land_drop_cited(self):
        d = sd.build_findings(tel(20), {}, REVIEW)
        f = next(f for f in d["findings"] if f["id"] == "land_t3")
        self.assertEqual(f["category"], "not_working")
        self.assertIn("40%", f["claim"])
        self.assertEqual(f["evidence"]["n"], 20)

    def test_no_curve_is_unavailable_not_zero(self):
        d = sd.build_findings(tel(20, with_curve=False), {}, REVIEW)
        f = next(f for f in d["findings"] if f["id"] == "land_t3")
        self.assertEqual((f["category"], f["evidence"]["value"]), ("unavailable", None))
        self.assertTrue(any(f["id"] == "stragglers_unavailable" for f in d["findings"]))

    def test_pillars_from_review_labelled(self):
        d = sd.build_findings(tel(20), {}, REVIEW)
        ramp = next(f for f in d["findings"] if f["id"] == "pillar:ramp")
        self.assertEqual(ramp["category"], "missing_pillar")
        self.assertFalse(ramp["sim_validated"])
        self.assertTrue(any(a["pillar"] == "ramp" for a in d["suggested_adds"]))
        self.assertEqual(next(f for f in d["findings"] if f["id"] == "pillar:plan")["category"], "unavailable")

    def test_stragglers_need_sample(self):
        c = {"Dud": {"cast": {"n": 8, "count": 1}}, "Few": {"cast": {"n": 2, "count": 0}}}
        d = sd.build_findings(tel(20, cards=c), {}, REVIEW)
        ids = [f["id"] for f in d["findings"] if f["category"] == "straggler"]
        self.assertEqual(ids, ["straggler:Dud"])
        self.assertEqual(d["suggested_cuts"][0]["card"], "Dud")

    def test_oracle_failure_falls_back(self):
        def boom():
            raise RuntimeError("down")
        d = sd.diagnose(tel(20), {}, REVIEW, boom)
        self.assertEqual(d["summary_source"], "deterministic")
        self.assertTrue(d["summary"])

    def test_oracle_invented_numbers_stripped(self):
        class O:
            def chat(self, *a, **k):
                return '```json\n{"summary": "Land drops slipped in 40% of 20 games. It also won 99% of the time."}\n```'
        d = sd.diagnose(tel(20), {}, REVIEW, lambda: O())
        self.assertEqual(d["summary_source"], "oracle")
        self.assertNotIn("99", d["summary"])


if __name__ == "__main__":
    unittest.main()
