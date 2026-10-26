"""Unit tests for brew.py session lifecycle, donor-only pool constraint,
provenance derivation, the no-write invariant, and builder registry isolation.

Run: pytest tests/test_brew.py
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brew
import brew_builder
import instances
import reconstruct
import reconstruct_pools
import registry


class FakePipe:
    def __init__(self, r):
        self.r, self.ops, self.executed = r, [], False

    def __getattr__(self, name):
        def queue(*a):
            self.ops.append((name, a))
            return self
        return queue

    def execute(self):
        self.r.pipe_executions += 1
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}
        self.pipe_executions = 0

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def sadd(self, k, v):
        self.sets.setdefault(k, set()).add(v)

    def srem(self, k, v):
        self.sets.get(k, set()).discard(v)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sinter(self, *ks):
        out = None
        for k in ks:
            out = self.smembers(k) if out is None else out & self.smembers(k)
        return out or set()

    def keys(self, pat):
        pre = pat.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]

    def delete(self, k):
        self.store.pop(k, None)

    def pipeline(self):
        return FakePipe(self)


def make_deck(deck_id=42, name="Atraxa", status="physical", **kw):
    d = {
        "id": deck_id,
        "name": name,
        "registry_id": f"archidekt:{deck_id}",
        "source": "archidekt",
        "source_id": str(deck_id),
        "status": status,
        "tags": ["custom_tag"],
        "commanders": ["Atraxa, Praetors' Voice"],
        "cards": [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Arcane Signet", "quantity": 1},
            {"name": "Doubling Season", "quantity": 1},
        ],
    }
    d.update(kw)
    return d


def static_price_lookup(prices):
    def factory(r, refresh=True):
        def lookup(names):
            return {n: prices.get(n) for n in names if n}
        return lookup
    return factory


class TestBrewCore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck1 = make_deck(deck_id=42, name="Atraxa Superfriends", status="physical")
        self.deck2 = make_deck(
            deck_id=43, name="Urza Artifacts", status="testing",
            commanders=["Urza, Lord High Artificer"],
            cards=[{"name": "Mana Crypt", "quantity": 1}, {"name": "Mox Opal", "quantity": 1}],
        )
        # Unnamed deck: holds an exclusive card that must NEVER enter pools.
        self.deck3 = make_deck(
            deck_id=77, name="Unnamed Deck", status="digital",
            commanders=["Krenko, Mob Boss"],
            cards=[{"name": "Lotus Petal", "quantity": 1}],
        )
        self.retired = make_deck(deck_id=88, name="Old Deck", status="retired")
        self.r.set("decks", json.dumps([self.deck1, self.deck2, self.deck3, self.retired]))

        # Donor-bound instances
        instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck",
                                  deck_id="archidekt:42", deck_name="Atraxa Superfriends")
        # Unnamed-deck exclusive card, bound to the unnamed deck
        instances.create_instance(self.r, "Lotus Petal", ownership_status="in_deck",
                                  deck_id="archidekt:77", deck_name="Unnamed Deck")
        # Free inventory (Tier 2 candidate)
        instances.create_instance(self.r, "Demonic Tutor", ownership_status="in_collection")

        # Catalog + prices (deterministic; no network)
        self.r.set("card_meta:Lightning Greaves", json.dumps({"color": "", "type": "Artifact — Equipment"}))
        self._orig_lookup = reconstruct_pools.redis_price_lookup
        reconstruct_pools.redis_price_lookup = static_price_lookup({"Lightning Greaves": 3.50})

        brew_builder.set_builder(brew_builder.StubBrewBuilder())

    def tearDown(self):
        reconstruct_pools.redis_price_lookup = self._orig_lookup

    def _start(self, **kw):
        args = dict(
            commander="Atraxa, Praetors' Voice",
            donor_deck_ids=["archidekt:42", "archidekt:43"],
            build_mode="owned_first",
            power_level="3",
            deck_idea="proliferate",
            themes=["counters"],
            notes="no extra turns",
        )
        args.update(kw)
        return brew.start_brew(self.r, args.pop("commander"), args.pop("donor_deck_ids"),
                               args.pop("build_mode"), **args)

    # -- lifecycle ------------------------------------------------------

    def test_start_brew_snapshots_donors_without_mutating_them(self):
        before_decks = self.r.get("decks")
        session = self._start()

        self.assertEqual(session["status"], brew.STATUS_PENDING_PREFERENCES)
        self.assertEqual(session["donor_deck_ids"], ["archidekt:42", "archidekt:43"])
        self.assertEqual(len(session["donor_decks"]), 2)
        self.assertEqual(session["commanders"], ["Atraxa, Praetors' Voice"])
        self.assertEqual(len(session["preferences"]), 1)

        # Donors fully intact: identical decks blob, statuses, tags.
        self.assertEqual(self.r.get("decks"), before_decks)
        d1 = registry.find_deck(self.r, "archidekt:42")
        self.assertEqual(d1["status"], "physical")
        self.assertIn("custom_tag", d1.get("tags", []))

    def test_start_brew_validation(self):
        with self.assertRaises(ValueError):
            self._start(commander="")
        with self.assertRaises(ValueError):
            self._start(build_mode="turbo")
        with self.assertRaises(ValueError):
            self._start(donor_deck_ids=["archidekt:9999"])
        with self.assertRaises(ValueError):
            self._start(donor_deck_ids=["archidekt:88"])  # retired donor rejected

    def test_no_donors_is_valid(self):
        session = self._start(donor_deck_ids=[])
        self.assertEqual(session["donor_decks"], [])
        self.assertEqual(session["pool_summary"]["tier1_unique"], 0)

    # -- donor-only constraint ------------------------------------------

    def test_unnamed_deck_never_contributes_cards(self):
        session = self._start()  # donors: 42, 43 — deck 77 NOT named
        sid = session["session_id"]
        pools = brew.get_session_pools(self.r, sid)
        tier1_names = {c["name"] for c in pools["tier1"]}
        tier2_names = {c["name"] for c in pools["tier2"]}
        self.assertIn("Sol Ring", tier1_names)
        self.assertIn("Mana Crypt", tier1_names)
        self.assertIn("Demonic Tutor", tier2_names)
        # Lotus Petal exists ONLY in the unnamed deck: excluded everywhere.
        self.assertNotIn("Lotus Petal", tier1_names)
        self.assertNotIn("Lotus Petal", tier2_names)

    # -- build + provenance ---------------------------------------------

    def _built_session(self, **kw):
        session = self._start(**kw)
        sid = session["session_id"]
        return brew.trigger_build(self.r, sid, sync=True)

    def test_build_produces_one_draft_both_modes(self):
        for mode in ("owned_first", "best_build"):
            session = self._built_session(build_mode=mode)
            self.assertEqual(session["status"], brew.STATUS_DRAFTS_READY)
            self.assertEqual(len(session["drafts"]), 1)
            draft = session["drafts"][0]
            self.assertEqual(draft["commanders"], ["Atraxa, Praetors' Voice"])
            self.assertEqual(draft["build_mode"], mode)
            self.assertTrue(draft["cards"])

    def test_provenance_maps_tiers_to_sources(self):
        session = self._built_session()
        sid = session["session_id"]
        result = brew.accept_draft(self.r, sid)
        provenance = result["session"]["drafts"][0]["provenance"]
        by_name = {p["name"]: p["source"] for p in provenance}

        self.assertEqual(by_name.get("Sol Ring"), "archidekt:42")       # Tier 1 donor
        self.assertEqual(by_name.get("Demonic Tutor"), "free_inventory")  # Tier 2
        self.assertEqual(by_name.get("Lightning Greaves"), "buy")         # Tier 3
        self.assertEqual(result["session"]["parent_deck_ids"], ["archidekt:42", "archidekt:43"])

    # -- accept: no-write invariant --------------------------------------

    def _write_snapshot(self):
        return {
            "decks": json.loads(self.r.get("decks") or "[]"),
            "instances": {k: self.r.get(k) for k in self.r.keys("instance:*")},
            "instance_index": dict(self.r.sets),
        }

    def test_accept_writes_only_the_new_deck_and_provenance(self):
        session = self._built_session()
        sid = session["session_id"]
        before = self._write_snapshot()
        # Donor records via the same read path, before and after.
        donor_before = {did: registry.find_deck(self.r, did) for did in ("archidekt:42", "archidekt:43")}

        result = brew.accept_draft(self.r, sid)
        after = self._write_snapshot()

        # Instances untouched (nothing bound, moved, or transitioned).
        self.assertEqual(before["instances"], after["instances"])
        self.assertEqual(before["instance_index"], after["instance_index"])

        # Exactly one new deck record; donors semantically identical.
        new_ids = {d["registry_id"] for d in after["decks"]} - {d["registry_id"] for d in before["decks"]}
        self.assertEqual(len(new_ids), 1)
        for did, old in donor_before.items():
            current = registry.find_deck(self.r, did)
            self.assertEqual(current.get("status"), old.get("status"))
            self.assertEqual(current.get("tags"), old.get("tags"))
            self.assertEqual(current.get("cards"), old.get("cards"))
            self.assertEqual(current.get("commanders"), old.get("commanders"))

        new_deck = result["created_decks"][0]
        self.assertEqual(new_deck["status"], "testing")
        self.assertEqual(new_deck["source"], "brew")

        # Provenance persisted independent of the session.
        deck_id = registry.registry_id_of(new_deck)
        persisted = brew.get_provenance(self.r, deck_id)
        self.assertTrue(persisted)
        self.assertTrue(all(p["source"] for p in persisted))

    def test_accept_requires_drafts_ready(self):
        session = self._start()
        with self.assertRaises(ValueError):
            brew.accept_draft(self.r, session["session_id"])

    # -- availability -----------------------------------------------------

    def test_availability_report(self):
        session = self._built_session()
        sid = session["session_id"]
        rep = brew.get_availability(self.r, sid)

        owned_names = {o["name"] for o in rep["owned"]}
        buy_names = {b["name"] for b in rep["buy_list"]}
        self.assertIn("Sol Ring", owned_names)
        self.assertIn("Demonic Tutor", owned_names)
        self.assertIn("Lightning Greaves", buy_names)
        for b in rep["buy_list"]:
            self.assertTrue(b["needs_purchase"])
        greaves = next(b for b in rep["buy_list"] if b["name"] == "Lightning Greaves")
        self.assertTrue(greaves["price_known"])
        self.assertEqual(greaves["unit_price"], 3.50)
        self.assertEqual(rep["total_buy_cost"], 3.50)
        self.assertTrue(rep["is_complete"])
        # Donor projections present, projected only (donors unchanged)
        projs = {p["deck_id"]: p for p in rep["donor_projections"]}
        self.assertIn("archidekt:42", projs)

    # -- misc lifecycle ----------------------------------------------------

    def test_submit_preferences_single_only(self):
        session = self._start()
        sid = session["session_id"]
        with self.assertRaises(ValueError):
            brew.submit_preferences(self.r, sid, [{"commanders": ["A"]}, {"commanders": ["B"]}])
        updated = brew.submit_preferences(self.r, sid, {"commander": "Urza, Lord High Artificer", "themes": ["artifacts"]})
        self.assertEqual(updated["commanders"], ["Urza, Lord High Artificer"])

    def test_abandon(self):
        session = self._start()
        sid = session["session_id"]
        out = brew.abandon_brew(self.r, sid, reason="changed my mind")
        self.assertEqual(out["status"], brew.STATUS_ABANDONED)
        with self.assertRaises(ValueError):
            brew.trigger_build(self.r, sid)

    # -- builder registry isolation -----------------------------------------

    def test_brew_builder_registry_is_independent(self):
        before = reconstruct.get_builder()

        class Dummy(brew_builder.BaseBrewBuilder):
            def build(self, session, r):
                return []

        brew_builder.set_builder(Dummy())
        self.assertIs(reconstruct.get_builder(), before)
        with self.assertRaises(TypeError):
            brew_builder.set_builder(reconstruct.StubReconstructBuilder())


if __name__ == "__main__":
    unittest.main()
