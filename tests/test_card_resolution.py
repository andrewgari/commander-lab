"""Focused tests for card_resolution + the brew builder's use of it."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.reconstruct_pools as pools
from src.server.domain.draft.card_resolution import resolve_all, resolve_card
from tests.test_brew import FakeRedis

GOOD = "11111111-2222-3333-4444-555555555555"
BAD = "99999999-8888-7777-6666-555555555555"


class TestResolveCard(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.r.set(f"card:{GOOD}", json.dumps({"name": "Sol Ring"}))

    def test_name_passes_through(self):
        self.assertEqual(resolve_card("Sol Ring", self.r).name, "Sol Ring")

    def test_id_resolves_via_catalog(self):
        rc = resolve_card(GOOD, self.r)
        self.assertEqual((rc.name, rc.oracle_id), ("Sol Ring", GOOD))

    def test_unknown_id_is_none(self):
        self.assertIsNone(resolve_card(BAD, self.r))

    def test_id_without_redis_is_none(self):
        self.assertIsNone(resolve_card(GOOD, None))

    def test_record_whose_name_is_a_uuid_is_none(self):
        self.r.set(f"card:{BAD}", json.dumps({"name": GOOD}))
        self.assertIsNone(resolve_card(BAD, self.r))

    def test_blank_and_non_string_are_none(self):
        for v in ("", "  ", None, 5):
            self.assertIsNone(resolve_card(v, self.r))

    def test_resolve_all_reports_dropped_and_missing_slots(self):
        out, report = resolve_all(["Sol Ring", GOOD, BAD], self.r)
        self.assertEqual([c.name for c in out], ["Sol Ring", "Sol Ring"])
        self.assertEqual(report.dropped, [BAD])
        self.assertEqual(report.missing_slots, 1)


class TestIdentityFallback(unittest.TestCase):
    def test_unknown_commander_yields_none_not_colourless(self):
        cat = pools.InMemoryCatalog([pools.CatalogCard("Known", frozenset("G"))])
        self.assertIsNone(pools.identity_of_cards(["Nope"], cat))
        self.assertIsNone(pools.identity_of_cards(["Known", "Nope"], cat))
        self.assertEqual(pools.identity_of_cards(["Known"], cat), frozenset("G"))


class TestBuilder(unittest.TestCase):
    def test_unresolvable_donor_dropped_and_never_emitted_and_deck_fills(self):
        r = FakeRedis()
        donors = [{"name": "Donor", "cards": [{"name": BAD}] + [{"name": f"Card {i}"} for i in range(60)],
                   "commanders": []}]
        session = {"commanders": ["Cmdr"], "donor_decks": donors, "build_mode": "owned_first"}
        draft = brew_builder.StubBrewBuilder().build(session, r)[0]
        names = [c["name"] for c in draft["cards"]]
        self.assertNotIn(BAD, names)
        self.assertEqual(draft["dropped_ids"], [BAD])
        self.assertEqual(len(names), 61)  # not capped at 25+commander
        self.assertEqual(draft["missing_slots"], 39)

    def test_unresolvable_commander_raises(self):
        with self.assertRaises(ValueError):
            brew_builder.StubBrewBuilder().build({"commanders": [BAD]}, FakeRedis())

    def test_uuid_commander_resolved_before_pools_and_donor_attributed(self):
        r = FakeRedis()
        r.set(f"card:{GOOD}", json.dumps({"name": "Sol Ring"}))
        r.set("card:aaaaaaaa-0000-0000-0000-000000000001", json.dumps({"name": "Cmdr", "color_identity": "G"}))
        session = {
            "commanders": ["aaaaaaaa-0000-0000-0000-000000000001"],
            "donor_decks": [{"name": "D", "cards": [{"name": GOOD, "quantity": 1}], "commanders": []}],
            "source_decks": [{"name": "D", "cards": [{"name": GOOD, "quantity": 1}], "commanders": []}],
        }
        draft = brew_builder.StubBrewBuilder().build(session, r)[0]
        self.assertEqual(draft["commanders"], ["Cmdr"])
        sol = next(c for c in draft["cards"] if c["name"] == "Sol Ring")
        self.assertEqual(sol["tier"], 1)  # donor attribution kept

    def test_dropped_owned_id_reaches_draft_report(self):
        r = FakeRedis()
        import src.server.domain.inventory.instances as inst
        inst.create_instance(r, BAD, ownership_status="in_collection")
        draft = brew_builder.StubBrewBuilder().build({"commanders": ["Cmdr"]}, r)[0]
        self.assertIn(BAD, draft["dropped_ids"])
        self.assertNotIn(BAD, [c["name"] for c in draft["cards"]])

    def test_recompute_refreshes_missing_slots_by_quantity(self):
        import src.server.domain.draft.brew as brew
        r = FakeRedis()
        draft = {"cards": [{"name": "Forest", "quantity": 5}, {"name": "Sol Ring", "quantity": 1}],
                 "missing_slots": 99}
        brew._recompute_draft(r, {"commanders": ["Cmdr"]}, draft)
        self.assertEqual(draft["missing_slots"], 94)


if __name__ == "__main__":
    unittest.main()
