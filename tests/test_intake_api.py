"""
API integration tests for /api/intake and /api/intake/validate endpoints.
"""

import os
import sys
import unittest
from fastapi.testclient import TestClient

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


class TestIntakeAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_post_intake_valid_payload_returns_200(self):
        payload = {
            "name": "Intake API Test Deck",
            "format": "commander",
            "decklist": "Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n1 Sol Ring\n1 Arcane Signet\n",
            "user_intent": {
                "deck_vision": {"intent": "tune_up", "description": "High power pod"},
                "target_power_level": {"scale": 8, "tier": "optimized"},
            },
        }
        response = self.client.post("/api/intake", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        intake = data.get("intake", {})
        self.assertEqual(intake.get("name"), "Intake API Test Deck")
        self.assertEqual(len(intake.get("commanders", [])), 1)
        self.assertEqual(intake["commanders"][0]["name"], "Atraxa, Praetors' Voice")
        self.assertEqual(intake["summary"]["mainboard_count"], 2)

    def test_post_intake_validation_failure_returns_422(self):
        # Missing commander in commander format + invalid power tier
        payload = {
            "format": "commander",
            "decklist": "1 Sol Ring\n1 Arcane Signet\n",
            "user_intent": {
                "target_power_level": {"tier": "godlike"}
            },
        }
        response = self.client.post("/api/intake", json=payload)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("errors", data)
        self.assertGreaterEqual(len(data["errors"]), 2)

    def test_post_intake_validate_endpoint_returns_verdict(self):
        # Valid payload
        valid_payload = {
            "format": "commander",
            "decklist": "Commander\n1 Edgar Markov\n\nDeck\n1 Blood Artist\n",
        }
        resp = self.client.post("/api/intake/validate", json=valid_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data["validation"]["is_valid"])
        self.assertEqual(len(data["validation"]["errors"]), 0)

        # Invalid payload
        invalid_payload = {
            "format": "commander",
            "decklist": "1 Blood Artist\n",
        }
        resp2 = self.client.post("/api/intake/validate", json=invalid_payload)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2.get("success"))
        self.assertFalse(data2["validation"]["is_valid"])
        self.assertGreater(len(data2["validation"]["errors"]), 0)


if __name__ == "__main__":
    unittest.main()
