"""PS-2: the Redis wrapper (analytics.personality.load_inputs / score_deck).

Cache-only: Spellbook and EDHREC records are read at any age and never
fetched; a missing source degrades to a note. Network calls are trapped.

Run: python -m unittest tests.test_personality_inputs
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import personality as ps  # noqa: E402
from analytics.interactions import combos as combos_mod  # noqa: E402
from analytics import edhrec_inclusion  # noqa: E402
from analytics.providers.edhrec import commander_slug  # noqa: E402


class FakeRedis:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = []

    def get(self, k):
        return self.data.get(k)

    def mget(self, ks):
        return [self.data.get(k) for k in ks]

    def set(self, k, v, *a, **kw):
        self.writes.append(k)
        self.data[k] = v

    def keys(self, pattern="*"):
        pre = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(pre)]


DECK = {"id": 7, "name": "Tiny", "registry_id": "archidekt:7", "commanders": ["Hero, the Bold"],
        "cards": [{"name": "Hero, the Bold", "quantity": 1}, {"name": "Sol Ring", "quantity": 1},
                  {"name": "Demonic Tutor", "quantity": 1}, {"name": "Forest", "quantity": 1}]}
META = {
    "Hero, the Bold": {"type": "Creature", "super_types": ["Legendary"], "sub_types": ["Human", "Knight"],
                       "keywords": [], "oracle_text": "Whenever Hero attacks, draw a card.", "cmc": 3},
    "Sol Ring": {"type": "Artifact", "super_types": [], "sub_types": [], "keywords": [],
                 "oracle_text": "{T}: Add {C}{C}.", "cmc": 1},
    "Demonic Tutor": {"type": "Sorcery", "super_types": [], "sub_types": [], "keywords": [],
                      "oracle_text": "Search your library for a card, put that card into your hand, then shuffle.",
                      "cmc": 2},
    "Forest": {"type": "Land", "super_types": ["Basic"], "sub_types": ["Forest"], "keywords": [],
               "oracle_text": "({T}: Add {G}.)", "cmc": 0},
}


def redis_with(combos=True, edhrec=True, journal=None):
    data = {"decks": json.dumps([DECK])}
    for i, (n, m) in enumerate(META.items()):
        data[f"card_meta:{n}"] = json.dumps(m)
        data[f"instance:{i}"] = json.dumps({"id": f"i{i}", "card_name": n, "ownership_status": "in_deck",
                                            "deck_id": "archidekt:7", "set": "TST"})
    if combos:
        key = combos_mod.decklist_key(DECK["commanders"], combos_mod._deck_names(DECK))
        data[key] = json.dumps({"included": [{"id": "x", "card_names": ["Sol Ring", "Demonic Tutor"]}],
                                "fetched_at": "2020-01-01T00:00:00+00:00"})  # very stale: still used
    if edhrec:
        data[edhrec_inclusion.cache_key(commander_slug(DECK["commanders"]))] = json.dumps(
            {"slug": "hero-the-bold", "fetched_at": "2020-01-01T00:00:00+00:00", "decks_sampled": 10,
             "cards": {"sol ring": {"inclusion": 0.9}}})
    if journal is not None:
        data["journal:deck:archidekt:7"] = json.dumps(journal)
    return FakeRedis(data)


def no_network():
    boom = mock.Mock(side_effect=AssertionError("network call during personality scoring"))
    return [mock.patch("requests.post", boom), mock.patch("requests.get", boom),
            mock.patch("httpx.get", boom), mock.patch("httpx.post", boom)]


class LoadInputs(unittest.TestCase):
    def setUp(self):
        self.patches = no_network()
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_reads_stale_caches_without_fetching(self):
        r = redis_with()
        out = ps.score_deck(r, DECK)
        self.assertEqual(r.writes, [])  # nothing refetched or rewritten
        j1 = next(e for e in out.johnny.evidence if e.signal == "J1")
        self.assertEqual(j1.value, 1)
        self.assertNotIn("combos unavailable", out.notes)
        self.assertNotIn("EDHREC unavailable", out.notes)
        s2 = next(e for e in out.spike.evidence if e.signal == "S2")
        self.assertEqual(s2.cards, ["Demonic Tutor"])
        s7 = next(e for e in out.spike.evidence if e.signal == "S7")
        self.assertEqual(s7.cards, ["Demonic Tutor"])  # BR-1 snapshot (no Redis copy)
        self.assertIn("no journal: cards-only score", out.notes)

    def test_missing_caches_degrade(self):
        r = redis_with(combos=False, edhrec=False)
        out = ps.score_deck(r, DECK)
        self.assertIn("combos unavailable", out.notes)
        self.assertIn("EDHREC unavailable", out.notes)
        self.assertEqual(r.writes, [])

    def test_journal_loaded(self):
        r = redis_with(journal={"preferred_win_conditions": {"primary": "combo"}})
        out = ps.score_deck(r, DECK)
        self.assertTrue(out.journal_used)
        self.assertEqual(out.johnny.journal_nudge, 3.0)

    def test_source_failure_is_isolated(self):
        r = redis_with()
        with mock.patch("deck_review.review_deck", side_effect=RuntimeError("boom")):
            out = ps.score_deck(r, DECK)
        self.assertIn("power estimate unavailable", out.notes)
        self.assertEqual(next(e for e in out.spike.evidence if e.signal == "S2").value, 1)


if __name__ == "__main__":
    unittest.main()
