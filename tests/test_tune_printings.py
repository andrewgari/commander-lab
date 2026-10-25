"""Integration tests for the Deck Tuner printing picker (DT-3,
docs/architecture/deck-tuner-overlay-spec.md §3.1/§3.3): the
/api/decks/{id}/tuneup/printings endpoint and the printing/needs_purchase
fields on the paper draft, against an in-memory fake Redis (same pattern
as test_tune_overlay.py). Scryfall is stubbed via app._scryfall_printings.

Covers:
  - printings endpoint joins Scryfall prints with exact-printing ownership
    (owned only on the exact (set, collector_number, finish) instance)
  - printings are cached under card_prints:{name} (no second fetch)
  - POST /tuneup/draft preserves a card's printing and recomputes
    needs_purchase server-side: unowned printing -> True, owned -> False
    (a printing change is treated as a NEW card)
  - printing validation: bad shape, unknown finish
  - ownership statuses: not_owned does not count; in_collection /
    in_deck / in_mail do

Run: python -m unittest discover -s tests
"""
import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import instances as instance_store
import registry


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sinter(self, *keys):
        sets = [self.sets.get(k, set()) for k in keys]
        if not sets:
            return set()
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return result

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, parent):
        self.parent = parent
        self.ops = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.parent, name)(*args, **kwargs)
        self.ops = []


SCRYFALL_PRINTS = [
    {"set": "cmm", "set_name": "Commander Masters", "collector_number": "410",
     "finishes": ["nonfoil", "foil"], "image": "https://img/cmm410.jpg",
     "prices": {"nonfoil": "1.50", "foil": "3.00", "etched": None}},
    {"set": "40k", "set_name": "Warhammer 40,000", "collector_number": "129",
     "finishes": ["nonfoil"], "image": "",
     "prices": {"nonfoil": "8.00", "foil": None, "etched": None}},
]


class PrintingsTestCase(unittest.TestCase):
    def setUp(self):
        self.fake_r = FakeRedis()
        self._orig_r = app_module.r
        app_module.r = self.fake_r
        self.client = TestClient(app_module.app)
        self.fetches = []

        self._orig_fetch = app_module._scryfall_printings
        def stub(name, **kw):
            self.fetches.append(name)
            return [dict(p) for p in SCRYFALL_PRINTS]
        app_module._scryfall_printings = stub

        self.deck = registry.upsert_deck(
            self.fake_r,
            {"source": "archidekt", "source_id": "999", "name": "Atraxa",
             "color": "WUBG", "commanders": ["Atraxa, Praetors' Voice"],
             "commander_uids": [], "folder": "Imported", "description": "",
             "cards": [{"name": "Sol Ring", "quantity": 1}], "slots": {}, "url": ""},
            default_status="testing",
        )
        self.rid = registry.registry_id_of(self.deck)

    def tearDown(self):
        app_module.r = self._orig_r
        app_module._scryfall_printings = self._orig_fetch

    def _url(self, rid=None):
        return f"/api/decks/{rid or self.rid}/tuneup/printings"

    def _post_draft(self, card):
        return self.client.post(f"/api/decks/{self.rid}/tuneup/draft",
                                json={"cards": [card]})


class TestPrintingsEndpoint(PrintingsTestCase):
    def test_lists_printings_with_finishes(self):
        j = self.client.get(self._url(), params={"name": "Sol Ring"}).json()
        self.assertEqual(j["name"], "Sol Ring")
        self.assertEqual(len(j["printings"]), 2)
        cmm = j["printings"][0]
        self.assertEqual(cmm["set"], "cmm")
        self.assertEqual({f["finish"] for f in cmm["finishes"]}, {"nonfoil", "foil"})
        self.assertFalse(any(f["owned"] for f in cmm["finishes"]))

    def test_ownership_is_exact_printing_only(self):
        # Own the CMM nonfoil — only that exact row is owned.
        instance_store.create_instance(
            self.fake_r, card_name="Sol Ring", ownership_status="in_collection",
            set="cmm", collector_number="410", finish="nonfoil")
        j = self.client.get(self._url(), params={"name": "Sol Ring"}).json()
        owned = {(p["set"], f["finish"]): f["owned"]
                 for p in j["printings"] for f in p["finishes"]}
        self.assertTrue(owned[("cmm", "nonfoil")])
        self.assertFalse(owned[("cmm", "foil")])    # same print, different finish
        self.assertFalse(owned[("40k", "nonfoil")])  # different printing

    def test_not_owned_status_does_not_count(self):
        instance_store.create_instance(
            self.fake_r, card_name="Sol Ring", ownership_status="not_owned",
            set="cmm", collector_number="410", finish="nonfoil")
        j = self.client.get(self._url(), params={"name": "Sol Ring"}).json()
        self.assertFalse(any(f["owned"] for p in j["printings"] for f in p["finishes"]))

    def test_in_mail_and_in_deck_count_as_owned(self):
        for status in ("in_mail", "in_deck"):
            fake = FakeRedis()
            app_module.r = fake
            try:
                registry.upsert_deck(
                    fake, {"source": "archidekt", "source_id": "999", "name": "Atraxa",
                           "color": "", "commanders": [], "commander_uids": [],
                           "folder": "", "description": "",
                           "cards": [{"name": "Sol Ring", "quantity": 1}], "slots": {}, "url": ""},
                    default_status="testing")
                kwargs = dict(card_name="Sol Ring", ownership_status=status,
                              set="40k", collector_number="129", finish="nonfoil")
                if status == "in_deck":
                    kwargs.update(deck_id="999", deck_name="Atraxa")
                instance_store.create_instance(fake, **kwargs)
                j = self.client.get(self._url(), params={"name": "Sol Ring"}).json()
                owned = {(p["set"], f["finish"]): f["owned"]
                         for p in j["printings"] for f in p["finishes"]}
                self.assertTrue(owned[("40k", "nonfoil")], status)
            finally:
                app_module.r = self.fake_r

    def test_scryfall_result_is_cached(self):
        self.client.get(self._url(), params={"name": "Sol Ring"})
        self.client.get(self._url(), params={"name": "Sol Ring"})
        self.assertEqual(self.fetches, ["Sol Ring"])   # one fetch, second hit cache
        self.assertIsNotNone(self.fake_r.get("card_prints:Sol Ring"))

    def test_requires_name_and_deck(self):
        self.assertEqual(self.client.get(self._url()).status_code, 422)
        self.assertEqual(self.client.get(self._url("nope"),
                                         params={"name": "Sol Ring"}).status_code, 404)


class TestDraftPrinting(PrintingsTestCase):
    def test_unowned_printing_flags_needs_purchase(self):
        resp = self._post_draft({"name": "Sol Ring", "qty": 1, "printing": {
            "set": "cmm", "collector_number": "410", "finish": "foil"}})
        self.assertEqual(resp.status_code, 200)
        card = resp.json()["cards"][0]
        self.assertEqual(card["printing"],
                         {"set": "cmm", "collector_number": "410", "finish": "foil"})
        self.assertTrue(card["needs_purchase"])

    def test_owned_printing_needs_no_purchase(self):
        instance_store.create_instance(
            self.fake_r, card_name="Sol Ring", ownership_status="in_collection",
            set="cmm", collector_number="410", finish="foil")
        resp = self._post_draft({"name": "Sol Ring", "qty": 1, "printing": {
            "set": "cmm", "collector_number": "410", "finish": "foil"}})
        self.assertFalse(resp.json()["cards"][0]["needs_purchase"])

    def test_needs_purchase_recomputed_server_side(self):
        # Client claims no purchase is needed; server must not trust it.
        resp = self._post_draft({"name": "Sol Ring", "qty": 1, "needs_purchase": False,
                                 "printing": {"set": "cmm", "collector_number": "410",
                                              "finish": "nonfoil"}})
        self.assertTrue(resp.json()["cards"][0]["needs_purchase"])

    def test_draft_is_paper_only_with_printing(self):
        resp = self._post_draft({"name": "Sol Ring", "qty": 1, "printing": {
            "set": "cmm", "collector_number": "410", "finish": "foil"}})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.fake_r.keys("instance:*"))   # no instance writes

    def test_printing_survives_round_trip(self):
        self._post_draft({"name": "Sol Ring", "qty": 1, "printing": {
            "set": "cmm", "collector_number": "410", "finish": "foil"}})
        got = self.client.get(f"/api/decks/{self.rid}/tuneup/draft").json()
        self.assertEqual(got["cards"][0]["printing"]["finish"], "foil")
        self.assertTrue(got["cards"][0]["needs_purchase"])

    def test_printing_validation(self):
        self.assertEqual(self._post_draft(
            {"name": "Sol Ring", "qty": 1, "printing": "cmm"}).status_code, 400)
        self.assertEqual(self._post_draft(
            {"name": "Sol Ring", "qty": 1,
             "printing": {"set": "cmm", "collector_number": "410", "finish": "galaxy"}})
            .status_code, 400)

    def test_cards_without_printing_untouched(self):
        resp = self._post_draft({"name": "Sol Ring", "qty": 1})
        card = resp.json()["cards"][0]
        self.assertNotIn("printing", card)
        self.assertNotIn("needs_purchase", card)


if __name__ == "__main__":
    unittest.main()
