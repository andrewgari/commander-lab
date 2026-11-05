import unittest

from src.server.domain.analytics.merit import combine as c


class MeritE(unittest.TestCase):
    def test_weighted_mean(self):
        sig = dict(I=1.0, S=0.0, L=0.5, C=0.0, R=1.0)
        w = c.DEFAULT_WEIGHTS
        exp = (w["I"] + 0.5 * w["L"] + w["R"]) / sum(w.values())
        self.assertAlmostEqual(c.merit_e(sig), exp)

    def test_missing_dropped_and_renormalised(self):
        full = c.merit_e(dict(I=0.8, S=0.8, L=0.8, C=0.8, R=0.8))
        part = c.merit_e(dict(I=0.8, S=None, L=None, C=0.8, R=0.8))
        self.assertAlmostEqual(full, 0.8)
        self.assertAlmostEqual(part, 0.8)
        only = c.merit_e({"I": 0.4})
        self.assertAlmostEqual(only, 0.4)

    def test_none_left(self):
        self.assertIsNone(c.merit_e({}))
        self.assertIsNone(c.merit_e(dict(I=None, S=None)))

    def test_custom_weights_replace_defaults(self):
        r = c.merit_e(dict(I=1.0, S=0.0), {"I": 3, "S": 1})
        self.assertAlmostEqual(r, 0.75)
        self.assertAlmostEqual(c.merit_e(dict(I=1.0, S=0.0), {"I": 0, "S": 1}), 0.0)

    def test_censored_flag_irrelevant_value_stays_unit(self):
        self.assertLessEqual(c.merit_e(dict(I=5, S=5)), 1.0)


class Strength(unittest.TestCase):
    def test_default_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(c.DEFAULT_WEIGHTS.values()), 1.0)
        self.assertAlmostEqual(c.E_SHARE + c.ROLE_CONFIDENCE_W + c.ROLE_NEED_W, 1.0)

    def test_e_takes_065(self):
        self.assertEqual(c.strength(1.0, 0.0, 0.0), 65.0)
        self.assertEqual(c.strength(0.0, 1.0, 1.0), 35.0)

    def test_missing_renormalised(self):
        self.assertEqual(c.strength(None, 1.0, 1.0), 100.0)
        self.assertEqual(c.strength(0.5), 50.0)
        self.assertEqual(c.strength(1.0, None, 0.0), round(100 * 0.65 / 0.80, 1))

    def test_nothing_is_neutral(self):
        self.assertEqual(c.strength(None), 50.0)


if __name__ == "__main__":
    unittest.main()
