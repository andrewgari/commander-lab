"""SM-4F regressions: metrics_available flag, precon pool cleanup, tempo polarity / CSS guards."""
import re
import unittest
from pathlib import Path

from analytics import sim_diff, sim_service

ROOT = Path(__file__).resolve().parent.parent


class TestMetricsAvailable(unittest.TestCase):
    def test_random_quiet_zeros_not_available(self):
        tempo = {"land_drops": [{"sim_land_drop_rate": 0.0}], "sim": {"avg_mana_utilization_t2_t6": 0.0, "avg_first_commander_turn": None}}
        self.assertFalse(sim_diff.metrics_available({"params": {"opponent_mode": "random"}}, tempo))

    def test_verbose_false_flag(self):
        self.assertFalse(sim_diff.metrics_available({"params": {"verbose_log": False, "opponent_mode": "specific"}}, {}))

    def test_specific_available(self):
        self.assertTrue(sim_diff.metrics_available({"params": {"opponent_mode": "specific"}}, {"sim": {}}))

    def test_random_with_real_data_available(self):
        tempo = {"land_drops": [{"sim_land_drop_rate": 0.9}], "sim": {}}
        self.assertTrue(sim_diff.metrics_available({"params": {"opponent_mode": "random"}}, tempo))


class TestPreconRows(unittest.TestCase):
    def test_strips_set_year_and_user_ids(self):
        rows = sim_service._precon_rows([
            {"id": "sample_mind_seize", "name": "Mind Seize [MOC] [2023]"},
            "sample_power_hungry",
            "user_24011416",
            {"id": "user_417707", "name": "Holy Hellfire"},
        ])
        self.assertEqual([r["id"] for r in rows], ["sample_mind_seize", "sample_power_hungry"])
        self.assertEqual(rows[0]["name"], "Mind Seize")
        self.assertEqual((rows[0]["set"], rows[0]["year"]), ("MOC", "2023"))
        self.assertEqual(rows[1]["name"], "Power Hungry")


class TestFrontendGuards(unittest.TestCase):
    js = (ROOT / "static/js/deck_sim.js").read_text()
    css = (ROOT / "static/css/hud.css").read_text()

    def test_phone_row_height_override(self):
        self.assertRegex(self.css, r"@media \(max-width: 640px\) \{\s*\.sm-log td, \.sm-tempo td \{ height: auto; \}")

    def test_polarity_aware_reads(self):
        self.assertIn("readFor(sim.t5_mana_starvation_rate, st.t5_starvation_probability, true", self.js)
        self.assertNotIn('"signal", "worse"]', self.js)

    def test_unrec_panel_used(self):
        self.assertIn("Not recorded for random pods", self.js)
        self.assertIn("metrics_available", self.js)


if __name__ == "__main__":
    unittest.main()
