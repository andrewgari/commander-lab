import math
import unittest

from src.server.domain.analytics.merit import signals as s
from src.server.domain.analytics.merit.signals import BracketPage as P


class SignalRange(unittest.TestCase):
    def test_in_unit_interval(self):
        for v in (-5, 0, 0.3, 0.6, 9):
            self.assertTrue(0 <= s.inclusion(v) <= 1)
            self.assertTrue(0 <= s.synergy(v) <= 1)
        for r in (1, 30, 300, 25000, 10**7):
            self.assertTrue(0 <= s.global_rank(r) <= 1)
        self.assertEqual(s.global_rank(1), 1.0)
        self.assertEqual(s.global_rank(25000), 0.0)

    def test_missing(self):
        self.assertIsNone(s.inclusion(None))
        self.assertIsNone(s.synergy(None))
        self.assertIsNone(s.global_rank(None))
        self.assertIsNone(s.global_rank(0))


class Lift(unittest.TestCase):
    def test_neutral_and_direction(self):
        same = s.bracket_lift([P(100, 1000)], [P(100, 1000)], 0.1)
        self.assertAlmostEqual(same.value, 0.5)
        up = s.bracket_lift([P(800, 1000)], [P(100, 1000)], 0.3)
        down = s.bracket_lift([P(100, 1000)], [P(800, 1000)], 0.3)
        self.assertGreater(up.value, 0.5)
        self.assertLess(down.value, 0.5)
        self.assertFalse(up.censored)
        for v in (up.value, down.value):
            self.assertTrue(0 <= v <= 1)

    def test_censored_side_imputed_and_flagged(self):
        r = s.bracket_lift([P(500, 1000)], [P(None, 1000, 0.05)], 0.2)
        self.assertTrue(r.censored)
        n_lo = 0.5 * 0.05 * 1000
        p_hi = (500 + 50 * 0.2) / 1050
        p_lo = (n_lo + 50 * 0.2) / 1050
        expected = 0.5 + (s._logit(p_hi) - s._logit(p_lo)) / 4
        self.assertAlmostEqual(r.value, min(1, max(0, expected)))

    def test_both_censored_is_missing(self):
        r = s.bracket_lift([P(None, 100, 0.1)], [P(None, 100, 0.1)], 0.2)
        self.assertIsNone(r.value)

    def test_unimputable_or_no_prior_is_missing(self):
        self.assertIsNone(s.bracket_lift([P(None, 100)], [P(5, 100)], 0.2).value)
        self.assertIsNone(s.bracket_lift([P(5, 100)], [P(5, 100)], None).value)
        self.assertIsNone(s.bracket_lift([], [P(5, 100)], 0.2).value)

    def test_multiple_pages_pooled(self):
        r = s.bracket_lift([P(10, 100), P(30, 100)], [P(5, 100), P(5, 100)], 0.1)
        self.assertGreater(r.value, 0.5)


class Combo(unittest.TestCase):
    V = [({"A", "B", "Cmdr"}, "E", 100), ({"A", "X"}, "C", 1000)]

    def test_complete_combo_scores(self):
        v = s.combo("A", {"A", "B", "Cmdr"}, self.V, 1000)
        self.assertAlmostEqual(v, math.log1p(100) / math.log1p(1000))

    def test_one_away_adds_nothing(self):
        self.assertEqual(s.combo("A", {"A", "Cmdr"}, self.V, 1000), 0.0)

    def test_card_not_in_combo(self):
        self.assertEqual(s.combo("Z", {"A", "B", "Cmdr", "Z"}, self.V, 1000), 0.0)

    def test_banned_tag_zero_and_max_taken(self):
        v = [({"A", "B"}, "B", 1000), ({"A", "C"}, "P", 10)]
        r = s.combo("A", {"A", "B", "C"}, v, 1000)
        self.assertAlmostEqual(r, math.log1p(10) / math.log1p(1000))

    def test_no_data_is_missing_and_capped(self):
        self.assertIsNone(s.combo("A", {"A"}, None, 1000))
        self.assertEqual(s.combo("A", {"A", "B"}, [({"A", "B"}, "E", 5000)], 1000), 1.0)

    def test_zero_p99_is_missing(self):
        self.assertIsNone(s.combo("A", {"A", "B"}, [({"A", "B"}, "E", 50)], 0))

    def test_custom_weights(self):
        r = s.combo("A", {"A", "B"}, [({"A", "B"}, "E", 1000)], 1000, {"E": 0.5})
        self.assertAlmostEqual(r, 0.5)


if __name__ == "__main__":
    unittest.main()
