"""PS-6 review-fix regressions: strict schema, input hash coverage, cache-only salt readers."""
from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from analytics import personality_read
from analytics.providers.edhrec import commander_slug
from tests.test_personality_read import (
    SAMPLE_DECK,
    SAMPLE_JOURNAL,
    VALID_ORACLE_JSON,
    FakeOracleClient,
    FakeRedis,
)


class TestStrictSchema(unittest.TestCase):
    def _bad(self, mutate):
        doc = copy.deepcopy(VALID_ORACLE_JSON)
        mutate(doc)
        with self.assertRaises(personality_read.PersonalityReadParseError):
            personality_read.validate_doc(doc)

    def test_wrong_schema_version_rejected(self):
        self._bad(lambda d: d.update(schema_version="2"))

    def test_missing_schema_version_rejected(self):
        self._bad(lambda d: d.pop("schema_version"))

    def test_invalid_runner_up_rejected(self):
        self._bad(lambda d: d.update(runner_up="banana"))

    def test_missing_overrides_rejected(self):
        self._bad(lambda d: d.pop("overrides"))

    def test_missing_table_impact_flags_rejected(self):
        self._bad(lambda d: d.pop("table_impact_flags"))

    def test_empty_lists_are_valid(self):
        doc = copy.deepcopy(VALID_ORACLE_JSON)
        doc["overrides"] = []
        doc["table_impact_flags"] = []
        personality_read.validate_doc(doc)


class TestHashCoversJournalInputs(unittest.TestCase):
    def test_deck_vision_change_misses(self):
        a = dict(SAMPLE_JOURNAL, deck_vision="one")
        b = dict(SAMPLE_JOURNAL, deck_vision="two")
        self.assertNotEqual(personality_read.compute_input_hash(SAMPLE_DECK, a),
                            personality_read.compute_input_hash(SAMPLE_DECK, b))

    def test_novelties_change_misses(self):
        a = dict(SAMPLE_JOURNAL, novelties="x")
        b = dict(SAMPLE_JOURNAL, novelties="y")
        self.assertNotEqual(personality_read.compute_input_hash(SAMPLE_DECK, a),
                            personality_read.compute_input_hash(SAMPLE_DECK, b))

    def test_current_input_hash_follows_resolved_journal(self):
        r = FakeRedis()
        with patch.object(personality_read, "resolve_journal", return_value=dict(SAMPLE_JOURNAL)):
            h1 = personality_read.current_input_hash(r, SAMPLE_DECK)
            h1b = personality_read.current_input_hash(r, SAMPLE_DECK)
        changed = dict(SAMPLE_JOURNAL, pillars=[{"name": "New pillar", "description": "d"}])
        with patch.object(personality_read, "resolve_journal", return_value=changed):
            h2 = personality_read.current_input_hash(r, SAMPLE_DECK)
        self.assertEqual(h1, h1b)
        self.assertNotEqual(h1, h2)


class TestCachedSaltReaders(unittest.TestCase):
    def test_commandersalt_empty_cache_is_none(self):
        self.assertIsNone(personality_read._extract_cached_commandersalt(FakeRedis(), "archidekt:417707", SAMPLE_DECK))

    def test_edhrec_salt_read_from_cache(self):
        r = FakeRedis()
        slug = commander_slug(SAMPLE_DECK["commanders"])
        r.set(f"edhrec:commander:{slug}", json.dumps({
            "commander_salt": 1.5,
            "cards": {"sol ring": {"name": "Sol Ring", "salt": 0.4}, "x": {"name": "X"}},
        }))
        out = personality_read._extract_cached_edhrec_salt(r, SAMPLE_DECK)
        self.assertEqual(out["commander_salt"], 1.5)
        self.assertEqual(out["cards_with_salt"], {"Sol Ring": 0.4})

    def test_edhrec_missing_cache_is_none(self):
        self.assertIsNone(personality_read._extract_cached_edhrec_salt(FakeRedis(), SAMPLE_DECK))

    def test_commandersalt_reads_registered_provider_cache(self):
        provider = SimpleNamespace(
            _cache={
                "https://archidekt.com/decks/417707": (
                    100.0,
                    {
                        "powerLevelRating": 8.1,
                        "bracketRating": 3.4,
                        "saltRating": 45.0,
                        "synergyRating": 0.62,
                        "threatRating": 0.31,
                        "archetypeLabel": "Reanimator",
                    },
                )
            },
            _now=lambda: 101.0,
            _cache_ttl_seconds=300.0,
        )
        with patch("analytics.registry.get_provider", return_value=provider) as get_provider:
            out = personality_read._extract_cached_commandersalt(
                FakeRedis(), "archidekt:417707", SAMPLE_DECK
            )

        get_provider.assert_called_once_with("commandersalt")
        self.assertEqual(out, {
            "power": 8.1,
            "bracket_rating": 3.4,
            "salt": 45.0,
            "synergy_rating": 0.62,
            "threat_rating": 0.31,
            "archetype_label": "Reanimator",
        })

    def test_commandersalt_ignores_expired_provider_cache(self):
        provider = SimpleNamespace(
            _cache={
                "https://archidekt.com/decks/417707": (
                    100.0,
                    {"synergyRating": 0.62},
                )
            },
            _now=lambda: 401.0,
            _cache_ttl_seconds=300.0,
        )
        with patch("analytics.registry.get_provider", return_value=provider):
            out = personality_read._extract_cached_commandersalt(
                FakeRedis(), "archidekt:417707", SAMPLE_DECK
            )

        self.assertIsNone(out)


class TestEdhrecParseKeepsSalt(unittest.TestCase):
    def test_parse_page_retains_salt(self):
        from analytics import edhrec_inclusion
        src = open(edhrec_inclusion.__file__).read()
        self.assertIn("commander_salt", src)
        self.assertIn('"salt"', src)


class TestJobRedisFailure(unittest.TestCase):
    def test_redis_failure_on_first_write_does_not_raise(self):
        class Boom(FakeRedis):
            def set(self, *a, **k):
                raise RuntimeError("redis down")

        job = {"job_id": "j1", "deck_id": "archidekt:417707", "status": "queued"}
        client = FakeOracleClient(reply_content="{}")
        try:
            personality_read.run_job(Boom(), job, SAMPLE_DECK, lambda: client)
        except Exception as e:  # noqa: BLE001
            self.fail(f"run_job leaked {e!r}")
        # The deck slot must be released even though persistence failed.
        personality_read.release("archidekt:417707", "j1")


if __name__ == "__main__":
    unittest.main()
