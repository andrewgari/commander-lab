"""
Tests for PS-6: Oracle makes the final deck personality call.
Covers:
  - Payload builder (all required fields, cache-only, graceful fallbacks)
  - Schema validation (valid and invalid responses, bounds, required fields)
  - Cache-by-hash (hit on same deck+journal, miss on deck change, miss on journal change)
  - Offline fallback and error handling
  - API endpoints (POST /personality/read, GET /jobs/{job_id}, GET /personality/read)
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from analytics import personality_read
from analytics.oracle_client import OracleUnavailable
from app import app


class FakeRedis:
    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value, **kw):
        self._store[key] = value
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        return n

    def zcard(self, key):
        return 0


class FakeOracleClient:
    def __init__(self, reply_content: str = "", fail: bool = False, fail_detail: str = ""):
        self.reply_content = reply_content
        self.fail = fail
        self.fail_detail = fail_detail
        self.calls = []

    def models(self):
        if self.fail:
            raise OracleUnavailable(self.fail_detail)
        return ["oracle"]

    def chat(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise OracleUnavailable(self.fail_detail)
        return self.reply_content


VALID_ORACLE_JSON = {
    "schema_version": "1",
    "deck_id": "archidekt:417707",
    "scores": {
        "timmy": {"score": 75, "justification": "Casts massive demons and angels like Lord of the Void.", "cards": ["Lord of the Void"]},
        "johnny": {"score": 40, "justification": "Plays some synergy engines but avoids multi-card loops.", "cards": []},
        "spike": {"score": 85, "justification": "Tutors into fast mana and devastating attacks via Kaalia.", "cards": ["Demonic Tutor", "Sol Ring"]},
        "vorthos": {"score": 80, "justification": "High narrative resonance around righteous holy hellfire.", "cards": ["Avacyn, Angel of Hope"]},
        "melvin": {"score": 35, "justification": "Straightforward combat triggers and attack steps.", "cards": []}
    },
    "leans_toward": "spike",
    "runner_up": "vorthos",
    "summary": "While Kaalia cheat-into-play looks Timmy at a glance, the tutor density and fast mana make this an efficient Spike list with heavy Vorthos flavor.",
    "overrides": [
        {
            "axis": "spike",
            "rulebook_score": 50,
            "oracle_score": 85,
            "delta": 35,
            "reason": "Rulebook undercounted tutor density and consistent Kaalia combat acceleration as mere creature ramp."
        }
    ],
    "table_impact_flags": [
        {
            "card": "Master of Cruelties",
            "category": "combo",
            "severity": "high",
            "reason": "Instant one-shot elimination with Kaalia trigger."
        }
    ],
    "rule0_pitch": "Plays fast tuned Kaalia combat, with potential early lethals."
}


SAMPLE_DECK = {
    "registry_id": "archidekt:417707",
    "id": 417707,
    "name": "Holy Hellfire",
    "commanders": ["Kaalia of the Vast"],
    "color": "WBR",
    "cards": [
        {"name": "Kaalia of the Vast", "quantity": 1, "type_line": "Legendary Creature — Human Cleric"},
        {"name": "Lord of the Void", "quantity": 1, "type_line": "Creature — Demon"},
        {"name": "Avacyn, Angel of Hope", "quantity": 1, "type_line": "Legendary Creature — Angel"},
        {"name": "Demonic Tutor", "quantity": 1, "type_line": "Sorcery"},
        {"name": "Sol Ring", "quantity": 1, "type_line": "Artifact"},
        {"name": "Master of Cruelties", "quantity": 1, "type_line": "Creature — Demon"}
    ]
}

SAMPLE_JOURNAL = {
    "thesis": "Cheat enormous angels and demons into combat fast.",
    "pillars": [{"name": "Angel/Demon/Dragon payload", "description": "High mv bombs"}],
    "pet_cards": ["Lord of the Void"],
    "target_bracket": 3,
    "target_power_level": 8
}


class TestPayloadBuilder(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_build_payload_includes_all_required_evidence(self):
        payload = personality_read.build_payload(self.r, SAMPLE_DECK)
        self.assertEqual(payload["deck_id"], "archidekt:417707")
        self.assertEqual(payload["deck_name"], "Holy Hellfire")
        self.assertEqual(payload["commanders"], ["Kaalia of the Vast"])
        self.assertIn("decklist", payload)
        self.assertIn("rulebook_scores", payload)
        self.assertIn("commandersalt", payload)
        self.assertIn("edhrec_salt", payload)
        self.assertIn("combos", payload)
        self.assertIn("engines", payload)
        self.assertIn("journal", payload)
        self.assertIn("game_changers", payload)

        # Rulebook scores must have 5 axes
        rb = payload["rulebook_scores"]
        for axis in ("timmy", "johnny", "spike", "vorthos", "melvin"):
            self.assertIn(axis, rb)
            self.assertIn("score", rb[axis])
            self.assertIn("evidence", rb[axis])

    def test_build_payload_handles_cached_commandersalt(self):
        # Cache commandersalt data under bracket:eval
        eval_data = {
            "result": {
                "explanation": {
                    "signals": [
                        {"signal": "commandersalt_power", "value": 8.2},
                        {"signal": "commandersalt_bracket_rating", "value": 3.4},
                        {"signal": "commandersalt_salt", "value": 45.0}
                    ]
                }
            }
        }
        self.r.set("bracket:eval:archidekt:417707", json.dumps(eval_data))
        payload = personality_read.build_payload(self.r, SAMPLE_DECK)
        cs = payload["commandersalt"]
        self.assertIsNotNone(cs)
        self.assertEqual(cs.get("power"), 8.2)
        self.assertEqual(cs.get("salt"), 45.0)

    def test_request_prompt_formats_readable_json(self):
        payload = {"deck_name": "Test", "deck_id": "test:1"}
        prompt = personality_read.request_prompt(payload)
        self.assertIn("Deck Personality Read request for 'Test'", prompt)
        self.assertIn("```json", prompt)


class TestSchemaValidation(unittest.TestCase):
    def test_valid_doc_passes(self):
        doc = personality_read.validate_doc(VALID_ORACLE_JSON)
        self.assertEqual(doc.leans_toward, "spike")
        self.assertEqual(doc.scores.timmy.score, 75)
        self.assertEqual(doc.scores.spike.score, 85)
        self.assertEqual(len(doc.overrides), 1)
        self.assertEqual(doc.overrides[0].delta, 35)
        self.assertEqual(len(doc.table_impact_flags), 1)

    def test_parse_reply_extracts_fenced_json(self):
        prose = "Here is my analysis of Holy Hellfire:\nThis deck is tuned.\n```json\n" + json.dumps(VALID_ORACLE_JSON) + "\n```\n"
        data = personality_read.parse_reply(prose)
        self.assertEqual(data["leans_toward"], "spike")

    def test_missing_required_axis_raises(self):
        bad_data = json.loads(json.dumps(VALID_ORACLE_JSON))
        del bad_data["scores"]["melvin"]
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(bad_data)

    def test_score_out_of_bounds_raises(self):
        bad_data = json.loads(json.dumps(VALID_ORACLE_JSON))
        bad_data["scores"]["spike"]["score"] = 105
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(bad_data)

        bad_data["scores"]["spike"]["score"] = -5
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(bad_data)

    def test_invalid_lean_enum_raises(self):
        bad_data = json.loads(json.dumps(VALID_ORACLE_JSON))
        bad_data["leans_toward"] = "combo_god"
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(bad_data)

    def test_missing_justification_raises(self):
        bad_data = json.loads(json.dumps(VALID_ORACLE_JSON))
        bad_data["scores"]["spike"]["justification"] = ""
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(bad_data)


class TestCacheByHash(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_same_deck_and_journal_produces_identical_hash(self):
        h1 = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        h2 = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        self.assertEqual(h1, h2)

    def test_deck_card_change_produces_different_hash(self):
        base_h = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        modified_deck = json.loads(json.dumps(SAMPLE_DECK))
        modified_deck["cards"].append({"name": "Vampiric Tutor", "quantity": 1})
        mod_h = personality_read.compute_input_hash(modified_deck, SAMPLE_JOURNAL)
        self.assertNotEqual(base_h, mod_h)

    def test_deck_quantity_change_produces_different_hash(self):
        base_h = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        modified_deck = json.loads(json.dumps(SAMPLE_DECK))
        modified_deck["cards"][1]["quantity"] = 2
        mod_h = personality_read.compute_input_hash(modified_deck, SAMPLE_JOURNAL)
        self.assertNotEqual(base_h, mod_h)

    def test_journal_thesis_change_produces_different_hash(self):
        base_h = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        mod_journal = json.loads(json.dumps(SAMPLE_JOURNAL))
        mod_journal["thesis"] = "A completely different thesis."
        mod_h = personality_read.compute_input_hash(SAMPLE_DECK, mod_journal)
        self.assertNotEqual(base_h, mod_h)

    def test_journal_target_power_change_produces_different_hash(self):
        base_h = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        mod_journal = json.loads(json.dumps(SAMPLE_JOURNAL))
        mod_journal["target_power_level"] = 9
        mod_h = personality_read.compute_input_hash(SAMPLE_DECK, mod_journal)
        self.assertNotEqual(base_h, mod_h)

    def test_store_and_load_read(self):
        doc = personality_read.validate_doc(VALID_ORACLE_JSON)
        h = personality_read.compute_input_hash(SAMPLE_DECK, SAMPLE_JOURNAL)
        rulebook = {"timmy": 70, "spike": 50}
        personality_read.store_read(self.r, "archidekt:417707", h, doc, rulebook)

        loaded = personality_read.load_read(self.r, "archidekt:417707")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["input_hash"], h)
        self.assertEqual(loaded["read"]["leans_toward"], "spike")
        self.assertEqual(loaded["read"]["scores"]["spike"]["score"], 85)


class TestOfflineFallbackAndJob(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_job_fails_cleanly_on_oracle_offline(self):
        job = personality_read.new_job(self.r, SAMPLE_DECK)
        client = FakeOracleClient(fail=True, fail_detail="Connection refused")
        res = personality_read.run_job(self.r, job, SAMPLE_DECK, lambda: client)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error_kind"], "oracle_offline")
        self.assertIn("offline", res["error"])

    def test_job_fails_cleanly_on_invalid_json(self):
        job = personality_read.new_job(self.r, SAMPLE_DECK)
        client = FakeOracleClient(reply_content="Sorry, I cannot answer.\n```json\n{bad_json\n```")
        res = personality_read.run_job(self.r, job, SAMPLE_DECK, lambda: client)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["error_kind"], "PersonalityReadParseError")


class TestApiRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app._find_deck_any", return_value=SAMPLE_DECK)
    @patch("app.r", new_callable=FakeRedis)
    def test_post_personality_read_cached_returns_200(self, mock_r, mock_deck):
        h = personality_read.compute_input_hash(SAMPLE_DECK, None)
        doc = personality_read.validate_doc(VALID_ORACLE_JSON)
        personality_read.store_read(mock_r, "archidekt:417707", h, doc, {})

        resp = self.client.post("/api/decks/archidekt:417707/personality/read", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("cached"))
        self.assertEqual(data.get("status"), "done")
        read_obj = data.get("read") or {}
        self.assertEqual(read_obj.get("leans_toward"), "spike")

    @patch("app._find_deck_any", return_value=SAMPLE_DECK)
    @patch("app._oracle_preflight", return_value=None)
    @patch("app.r", new_callable=FakeRedis)
    def test_post_personality_read_starts_job(self, mock_r, mock_pre, mock_deck):
        reply = "```json\n" + json.dumps(VALID_ORACLE_JSON) + "\n```"
        fake_client = FakeOracleClient(reply_content=reply)
        with patch("app._oracle_client", return_value=fake_client):
            resp = self.client.post("/api/decks/archidekt:417707/personality/read", json={"force": True})
            self.assertEqual(resp.status_code, 202)
            data = resp.json()
            self.assertIn("job_id", data)
            self.assertIn("status_url", data)

            job_id = data["job_id"]
            # Poll job
            poll_resp = self.client.get(f"/api/decks/archidekt:417707/personality/read/jobs/{job_id}")
            self.assertEqual(poll_resp.status_code, 200)
            pdata = poll_resp.json()
            self.assertTrue(pdata.get("success"))

    @patch("app._find_deck_any", return_value=SAMPLE_DECK)
    @patch("app.r", new_callable=FakeRedis)
    def test_get_personality_read_endpoint(self, mock_r, mock_deck):
        # 404 before read
        resp = self.client.get("/api/decks/archidekt:417707/personality/read")
        self.assertEqual(resp.status_code, 404)

        # Store read
        h = personality_read.compute_input_hash(SAMPLE_DECK, None)
        doc = personality_read.validate_doc(VALID_ORACLE_JSON)
        personality_read.store_read(mock_r, "archidekt:417707", h, doc, {})

        resp = self.client.get("/api/decks/archidekt:417707/personality/read")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("read", {}).get("leans_toward"), "spike")
        self.assertFalse(data.get("stale"))


if __name__ == "__main__":
    unittest.main()
