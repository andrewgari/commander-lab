"""PS-3: player profile aggregation (analytics/personality_profile.py).

build_player_profile(all_scores) is pure and deterministic: averaged five
axes, dominant type, headline shape, lean counts and rule-based trend
callouts. Below 3 scored decks it flags low_data and returns no trends.

Run: python -m unittest tests.test_personality_profile
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import personality as ps  # noqa: E402
from analytics import personality_profile as pp  # noqa: E402

AXES = ("timmy", "johnny", "spike", "vorthos", "melvin")


def scores(t, j, s, v, m, journal=False, v1=None, notes=()):
    """A PersonalityScores dict with the given finals (and an optional V1 value string)."""
    vals = dict(zip(AXES, (t, j, s, v, m)))
    out = {}
    for a in AXES:
        ev = []
        if a == "vorthos" and v1:
            ev.append({"signal": "V1", "label": "Shared theme", "value": v1, "sub_score": 0.5, "points": 17.5,
                       "weight": 35, "cards": [], "fields": []})
        out[a] = {"score": vals[a], "card_score": float(vals[a]), "journal_nudge": 0.0, "evidence": ev}
    lean, runner = ps.leans_toward(vals)
    return {"version": 1, **out, "leans_toward": lean, "runner_up": runner, "journal_used": journal,
            "notes": list(notes)}


def entry(i, name, sc, status="physical"):
    return {"id": f"archidekt:{i}", "name": name, "commanders": [f"Commander {i}"], "commander_uids": [],
            "status": status, "scores": sc}


# Seven real rows from docs/PERSONALITY_SPEC.md 5.2
REAL = [
    entry(5620189, "The Laws of Time are Mine!!", scores(51, 31, 39, 76, 79, v1="type Alien 5/22; set WHO 41/55")),
    entry(417233, "My Big, Fat, Dead Wedding", scores(44, 24, 46, 79, 21, v1="type Vampire 25/31; set C17 9/40")),
    entry(1, "Everything is permitted.", scores(36, 6, 29, 96, 43, v1="type Assassin 20/25; set ACR 30/60")),
    entry(2, "Batteries Not Included", scores(37, 65, 30, 22, 10)),
    entry(3, "The world is slowly ebbing away...", scores(34, 45, 90, 4, 39)),
    entry(4, "The Word is 'Gamer'", scores(33, 49, 58, 10, 73)),
    entry(417707, "Holy Hellfire", scores(73, 25, 55, 33, 8, journal=True)),
]


class Empty(unittest.TestCase):
    def test_zero_decks(self):
        p = pp.build_player_profile([])
        self.assertEqual(p.decks_scored, 0)
        self.assertTrue(p.low_data)
        self.assertIsNone(p.average)
        self.assertIsNone(p.dominant)
        self.assertEqual(p.headline_kind, "none")
        self.assertEqual(p.trends, [])
        self.assertEqual(p.lean_counts, {**{a: 0 for a in AXES}, "balanced": 0})

    def test_failed_listed_but_not_counted(self):
        p = pp.build_player_profile([], failed=[{"id": "archidekt:9", "name": "Broken", "reason": "boom"}])
        self.assertEqual(p.decks_scored, 0)
        self.assertEqual(p.failed[0].reason, "boom")


class LowData(unittest.TestCase):
    def test_one_deck(self):
        p = pp.build_player_profile([REAL[0]])
        self.assertTrue(p.low_data)
        self.assertEqual(p.decks_scored, 1)
        self.assertEqual(p.average, {"timmy": 51, "johnny": 31, "spike": 39, "vorthos": 76, "melvin": 79})
        self.assertEqual(p.dominant, "melvin")
        self.assertEqual(p.dominant_runner_up, "vorthos")
        self.assertEqual(p.headline_kind, "close")
        self.assertEqual(p.trends, [])
        self.assertIn("Early read", p.headline)
        self.assertEqual(p.decks_to_unlock_trends, 2)

    def test_two_decks(self):
        p = pp.build_player_profile(REAL[:2])
        self.assertTrue(p.low_data)
        self.assertEqual(p.trends, [])
        self.assertEqual(p.decks_to_unlock_trends, 1)
        # (51+44)/2 = 47.5 -> 48 half-up; (76+79)/2 = 77.5 -> 78
        self.assertEqual(p.average["timmy"], 48)
        self.assertEqual(p.average["vorthos"], 78)

    def test_three_is_not_low(self):
        p = pp.build_player_profile(REAL[:3])
        self.assertFalse(p.low_data)
        self.assertEqual(p.decks_to_unlock_trends, 0)
        self.assertTrue(p.trends)


class Aggregation(unittest.TestCase):
    def setUp(self):
        self.p = pp.build_player_profile(REAL)

    def test_average_half_up(self):
        # timmy: 51+44+36+37+34+33+73 = 308 / 7 = 44.0
        self.assertEqual(self.p.average["timmy"], 44)
        # vorthos: 76+79+96+22+4+10+33 = 320 / 7 = 45.71 -> 46
        self.assertEqual(self.p.average["vorthos"], 46)
        self.assertEqual(self.p.average["spike"], 50)   # 347/7 = 49.57
        self.assertEqual(self.p.average["johnny"], 35)  # 245/7
        self.assertEqual(self.p.average["melvin"], 39)  # 273/7

    def test_lean_counts(self):
        self.assertEqual(self.p.lean_counts, {"timmy": 1, "johnny": 1, "spike": 1, "vorthos": 2, "melvin": 2,
                                              "balanced": 0})

    def test_dominant_and_headline(self):
        self.assertEqual(self.p.dominant, "spike")  # 50 vs 46 vorthos: close
        self.assertEqual(self.p.headline_kind, "close")
        self.assertEqual(self.p.dominant_runner_up, "vorthos")

    def test_journal_count(self):
        self.assertEqual(self.p.journal_count, 1)

    def test_deck_rows(self):
        self.assertEqual(len(self.p.decks), 7)
        row = next(d for d in self.p.decks if d.id == "archidekt:5620189")
        self.assertEqual(row.scores, {"timmy": 51, "johnny": 31, "spike": 39, "vorthos": 76, "melvin": 79})
        self.assertEqual(row.leans_toward, "melvin")
        self.assertEqual(row.runner_up, "vorthos")
        self.assertFalse(row.journal_used)

    def test_accepts_model_objects(self):
        e = dict(REAL[0], scores=ps.PersonalityScores.model_validate(REAL[0]["scores"]))
        self.assertEqual(pp.build_player_profile([e]).average["melvin"], 79)

    def test_deterministic_and_order_independent(self):
        a = pp.build_player_profile(REAL).model_dump()
        b = pp.build_player_profile(list(reversed(REAL))).model_dump()
        self.assertEqual(a, b)

    def test_decks_total_defaults_to_scored_plus_failed(self):
        p = pp.build_player_profile(REAL, failed=[{"id": "x", "name": "X", "reason": "r"}])
        self.assertEqual(p.decks_total, 8)
        self.assertEqual(pp.build_player_profile(REAL, decks_total=10).decks_total, 10)


class Headline(unittest.TestCase):
    def test_clear(self):
        p = pp.build_player_profile([entry(i, f"D{i}", scores(80, 30, 30, 30, 30)) for i in range(3)])
        self.assertEqual((p.dominant, p.headline_kind, p.dominant_runner_up), ("timmy", "clear", None))
        self.assertIn("<b>Timmy</b>", p.headline)

    def test_even(self):
        p = pp.build_player_profile([entry(i, f"D{i}", scores(50, 49, 48, 20, 20)) for i in range(3)])
        self.assertEqual((p.dominant, p.headline_kind), ("balanced", "even"))
        self.assertIn("a bit of everything", p.headline)


def kinds(p):
    return [t.kind for t in p.trends]


class Trends(unittest.TestCase):
    def setUp(self):
        self.p = pp.build_player_profile(REAL)

    def test_at_most_eight(self):
        self.assertLessEqual(len(self.p.trends), 8)

    def test_most_common_lean(self):
        t = self.p.trends[0]
        self.assertEqual(t.kind, "most_common_lean")
        # vorthos and melvin tie at 2 -> tie says so
        self.assertEqual((t.number, t.of), (2, 7))
        self.assertIn("<b>Vorthos</b>", t.text)
        self.assertIn("<b>Melvin</b>", t.text)
        self.assertEqual(t.action, {"type": "filter", "value": "vorthos"})

    def test_average_vs_count(self):
        t = next(t for t in self.p.trends if t.kind == "average_vs_count")
        self.assertEqual((t.axis, t.number, t.unit), ("spike", 50, "avg"))
        self.assertEqual(t.action, {"type": "sort", "value": "spike"})

    def test_tribal_group(self):
        """'your Vorthos is highest in tribal decks'"""
        t = next(t for t in self.p.trends if t.kind == "group")
        self.assertEqual(t.axis, "vorthos")
        self.assertEqual(t.group, "tribal")
        self.assertEqual(t.of, 2)  # Edgar (25/31 Vampire) and Ezio (20/25 Assassin); Doctor 5/22 isn't
        self.assertEqual(t.number, 88)  # (79+96)/2 = 87.5 -> 88
        self.assertIn("tribal", t.text)
        tribal = {d.id for d in self.p.decks if "tribal" in d.groups}
        self.assertEqual(tribal, {"archidekt:417233", "archidekt:1"})

    def test_outlier(self):
        outs = [t for t in self.p.trends if t.kind == "outlier"]
        self.assertTrue(outs)
        # biggest gap over the axis average: Ezio vorthos 96 vs 46 = 50, Celes spike 90 vs 50 = 40
        self.assertEqual((outs[0].axis, outs[0].number, outs[0].deck_id), ("vorthos", 96, "archidekt:1"))
        self.assertEqual(outs[0].action, {"type": "open", "value": "archidekt:1"})

    def test_no_deck_leans(self):
        p = pp.build_player_profile([entry(i, f"D{i}", scores(80, 30, 30, 30, 30)) for i in range(4)]
                                    + [entry(9, "S", scores(20, 30, 80, 20, 20))])
        t = next(t for t in p.trends if t.kind == "none")
        self.assertEqual((t.number, t.of), (0, 5))
        for a in ("Johnny", "Vorthos", "Melvin"):
            self.assertIn(f"<b>{a}</b>", t.text)

    def test_rarest_when_every_axis_has_a_deck(self):
        rows = [entry(i, f"T{i}", scores(80, 30, 30, 30, 30)) for i in range(3)]
        rows += [entry(10 + k, f"X{k}", scores(*[80 if x == k else 30 for x in range(5)])) for k in range(1, 5)]
        p = pp.build_player_profile(rows)
        self.assertNotIn("none", kinds(p))
        t = next(t for t in p.trends if t.kind == "rarest_lean")
        self.assertEqual((t.axis, t.number), ("johnny", 1))  # 1-1-1-1 tie -> axis order

    def test_x_of_y_lean(self):
        """'3 of 6 decks lean Spike'"""
        rows = [entry(i, f"S{i}", scores(30, 30, 80, 30, 30)) for i in range(3)]
        rows += [entry(10 + i, f"T{i}", scores(80, 30, 30, 30, 30)) for i in range(2)]
        rows += [entry(20, "J", scores(30, 80, 30, 30, 30))]
        t = pp.build_player_profile(rows).trends[0]
        self.assertEqual((t.kind, t.axis, t.number, t.of), ("most_common_lean", "spike", 3, 6))
        self.assertEqual(t.text, "decks lean <b>Spike</b>, your most common lean.")

    def test_balanced_count(self):
        rows = REAL + [entry(99, "Bird UP!!!!", scores(39, 70, 65, 63, 31))]
        t = next(t for t in pp.build_player_profile(rows).trends if t.kind == "balanced_count")
        self.assertEqual((t.number, t.of), (1, 8))
        self.assertEqual(t.action, {"type": "filter", "value": "balanced"})

    def test_journal_coverage(self):
        t = next(t for t in self.p.trends if t.kind == "journal_coverage")
        self.assertEqual((t.number, t.of), (1, 7))
        self.assertEqual(t.action, {"type": "filter", "value": "no_journal"})

    def test_spread(self):
        # Vorthos has the widest range (4-96) but Ezio/vorthos is already the
        # outlier callout, so the spread moves to the next widest: Melvin 8-79.
        t = next(t for t in self.p.trends if t.kind == "spread")
        self.assertEqual(t.axis, "melvin")
        self.assertEqual(t.number, "8–79")
        self.assertEqual(set(t.deck_ids), {"archidekt:417707", "archidekt:5620189"})

    def test_spread_widest_when_free(self):
        rows = [entry(i, f"D{i}", scores(40, 30, 30, 30 + 10 * i, 30)) for i in range(6)]  # vorthos 30..80
        p = pp.build_player_profile(rows)
        t = next(t for t in p.trends if t.kind == "spread")
        self.assertEqual((t.axis, t.number), ("vorthos", "30–80"))

    def test_trend_shape(self):
        for t in self.p.trends:
            self.assertTrue(t.text)
            self.assertNotIn("<script", t.text)
            if t.action:
                self.assertIn(t.action["type"], ("filter", "sort", "open"))

    def test_never_same_deck_and_axis_twice(self):
        seen = set()
        for t in self.p.trends:
            for d in t.deck_ids:
                key = (d, t.axis)
                self.assertNotIn(key, seen, t)
                seen.add(key)

    def test_all_same_no_spread_no_outlier(self):
        p = pp.build_player_profile([entry(i, f"D{i}", scores(60, 30, 30, 30, 30)) for i in range(5)])
        self.assertNotIn("spread", kinds(p))
        self.assertNotIn("outlier", kinds(p))
        self.assertNotIn("average_vs_count", kinds(p))


class Scope(unittest.TestCase):
    def test_scope_filter_physical(self):
        rows = REAL + [entry(77, "Draft", scores(10, 90, 10, 10, 10), status="testing")]
        p = pp.build_player_profile(rows, scope="physical")
        self.assertEqual(p.decks_scored, 7)
        self.assertEqual(p.scope, "physical")
        self.assertEqual(pp.build_player_profile(rows, scope="all").decks_scored, 8)


if __name__ == "__main__":
    unittest.main()
