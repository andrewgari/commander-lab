"""
Regression tests for t_a2966d3f: every sync path must save deck source
links, Moxfield account sync must attach to the existing registry deck
(not create/overwrite one), and no sync may drop links or decks it
didn't fetch.

Run: python tests/test_sync_links.py
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("ARCHIDEKT_USERNAME", "test_user")

import instances as instance_store  # noqa: E402
import linked_accounts  # noqa: E402
import registry  # noqa: E402
from providers import PROVIDERS  # noqa: E402
from tests.test_instances import FakeRedis  # noqa: E402

import backfill_deck_links  # noqa: E402


def arch_deck(did, name, commanders, cards=None):
    return {
        "source": "archidekt", "source_id": str(did), "name": name, "color": "W",
        "commanders": commanders, "commander_uids": [], "folder": "Imported",
        "description": "", "cards": cards or [{"name": c, "quantity": 1} for c in commanders],
        "url": f"https://archidekt.com/decks/{did}",
    }


def mox_deck(pid, name, commanders, cards=None):
    return {
        "source": "moxfield", "source_id": pid, "name": name, "color": "W",
        "commanders": commanders, "commander_uids": [], "folder": "Imported",
        "description": "", "cards": cards or [{"name": "Totally Different Card", "quantity": 1}],
        "url": f"https://moxfield.com/decks/{pid}",
    }


class FakeProvider:
    def __init__(self, decks):
        self.decks = {d["source_id"]: d for d in decks}

    def list_decks(self, username):
        return list(self.decks)

    def fetch_deck(self, deck_id):
        return dict(self.decks[deck_id])


class Base(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self._orig = dict(PROVIDERS)

    def tearDown(self):
        PROVIDERS.clear()
        PROVIDERS.update(self._orig)

    def link_account(self, provider, decks):
        PROVIDERS[provider] = FakeProvider(decks)
        linked_accounts.add_account(self.r, provider, "CovaDax")
        return f"{provider}:CovaDax"


class TestAccountSyncWritesLinks(Base):
    def test_archidekt_account_sync_sets_links_archidekt(self):
        acct = self.link_account("archidekt", [arch_deck(101, "A", ["Cmd A"]), arch_deck(102, "B", ["Cmd B"])])
        linked_accounts.sync_account(self.r, acct)
        decks = registry.list_decks(self.r)
        self.assertEqual(len(decks), 2)
        for d in decks:
            self.assertEqual(d["links"]["archidekt"],
                             {"identifier": f"https://archidekt.com/decks/{d['source_id']}", "deck_id": d["source_id"]})

    def test_resync_keeps_other_providers_links(self):
        acct = self.link_account("archidekt", [arch_deck(101, "A", ["Cmd A"])])
        linked_accounts.sync_account(self.r, acct)
        decks = registry.list_decks(self.r)
        decks[0]["links"]["commandersalt"] = {"identifier": "https://commandersalt.com/x"}
        decks[0]["links"]["moxfield"] = {"identifier": "https://moxfield.com/decks/M1", "deck_id": "M1"}
        registry._save_decks(self.r, decks)

        linked_accounts.sync_account(self.r, acct)
        links = registry.list_decks(self.r)[0]["links"]
        self.assertEqual(set(links), {"archidekt", "moxfield", "commandersalt"})
        self.assertEqual(links["moxfield"]["deck_id"], "M1")


class TestMoxfieldAttachesToPhysicalDeck(Base):
    def setUp(self):
        super().setUp()
        cards = [{"name": "Aragorn, the Uniter", "quantity": 1}, {"name": "Sol Ring", "quantity": 1}]
        acct = self.link_account("archidekt", [
            arch_deck(5054882, "The Return of the King", ["Aragorn, the Uniter"], cards),
            arch_deck(200, "Dawnbringer", ["Mazzy, Truesword Paladin"]),
        ])
        linked_accounts.sync_account(self.r, acct)
        registry.set_status(self.r, "archidekt:5054882", "physical")
        self.r.set("deck_journal:5054882", json.dumps({"notes": "keep me"}))
        self.before = registry.find_deck(self.r, "archidekt:5054882")
        self.before_instances = sorted(
            json.dumps(i, sort_keys=True)
            for i in instance_store.list_deck_instances(self.r, registry.deck_instance_ids(self.before)))
        self.assertTrue(self.before_instances)

    def test_moxfield_sync_attaches_without_touching_status_cards_or_instances(self):
        acct = self.link_account("moxfield", [
            mox_deck("ROTK1", "The Return of the King", ["Aragorn, the Uniter"]),
            mox_deck("KYRA", "Kyra Fireball", ["Mazzy, Truesword Paladin"]),   # same cmdr, other name
            mox_deck("NOPE", "Unknown", ["Helga, Skittish Seer"]),              # no registry deck
        ])
        account = linked_accounts.sync_account(self.r, acct)

        decks = registry.list_decks(self.r)
        self.assertEqual(len(decks), 2, "moxfield sync must not create decks")
        self.assertFalse(any(d.get("source") == "moxfield" for d in decks))
        after = registry.find_deck(self.r, "archidekt:5054882")
        self.assertEqual(after["links"]["moxfield"],
                         {"identifier": "https://moxfield.com/decks/ROTK1", "deck_id": "ROTK1"})
        self.assertEqual(after["moxfield_id"], "ROTK1")
        self.assertEqual(after["links"]["archidekt"]["deck_id"], "5054882")
        for k in ("id", "status", "cards", "commanders", "name", "source", "source_id", "registry_id", "linked_account_id"):
            self.assertEqual(after[k], self.before[k], k)
        self.assertEqual(self.r.get("deck_status:5054882"), "physical")
        self.assertEqual(json.loads(self.r.get("deck_journal:5054882")), {"notes": "keep me"})
        after_instances = sorted(
            json.dumps(i, sort_keys=True)
            for i in instance_store.list_deck_instances(self.r, registry.deck_instance_ids(after)))
        self.assertEqual(after_instances, self.before_instances)
        self.assertFalse(any(k.startswith("deck_status:moxfield:") for k in self.r.store))

        res = account["last_sync_result"]
        self.assertEqual(res["decks_synced"], 1)
        self.assertEqual(sorted(u["deck_id"] for u in res["unmatched"]), ["KYRA", "NOPE"])
        self.assertIsNone(registry.find_deck(self.r, "archidekt:200").get("links", {}).get("moxfield"))

    def test_moxfield_resync_is_idempotent(self):
        acct = self.link_account("moxfield", [mox_deck("ROTK1", "The Return of the King", ["Aragorn, the Uniter"])])
        linked_accounts.sync_account(self.r, acct)
        first = registry.list_decks(self.r)
        account = linked_accounts.sync_account(self.r, acct)
        self.assertEqual(registry.list_decks(self.r), first)
        self.assertEqual(account["last_sync_result"]["decks_synced"], 1)

    def test_ambiguous_match_is_left_unmatched(self):
        decks = registry.list_decks(self.r)
        dup = dict(decks[0], id=999, source_id="999", registry_id="archidekt:999", links={})
        registry._save_decks(self.r, decks + [dup])
        idx, reason = registry.match_provider_deck(
            registry.list_decks(self.r), mox_deck("X", "The Return of the King", ["Aragorn, the Uniter"]))
        self.assertIsNone(idx)
        self.assertIn("ambiguous", reason)

    def test_name_normalization_and_mdfc_front_face(self):
        decks = registry.list_decks(self.r)
        idx, _ = registry.match_provider_deck(
            decks, mox_deck("X", "the return of the KING!", ["Aragorn, the Uniter // Back"]))
        self.assertEqual(decks[idx]["name"], "The Return of the King")


class TestFullSyncPreservesLinks(unittest.TestCase):
    def test_merge_full_sync_keeps_links_fields_and_unfetched_decks(self):
        existing = [
            {"id": 101, "name": "Old A", "status": "physical", "source": "archidekt", "source_id": "101",
             "registry_id": "archidekt:101", "cards": [{"name": "X", "quantity": 1}], "moxfield_id": "M1",
             "links": {"archidekt": {"identifier": "https://archidekt.com/decks/101", "deck_id": "101"},
                       "moxfield": {"identifier": "https://moxfield.com/decks/M1", "deck_id": "M1"}}},
            {"id": "moxfield:Z", "name": "Mox only", "source": "moxfield", "source_id": "Z",
             "registry_id": "moxfield:Z", "links": {"moxfield": {"identifier": "u", "deck_id": "Z"}}},
        ]
        fetched = [
            {"id": 101, "name": "A", "color": "W", "commanders": ["C"], "commander_uids": [],
             "folder": "Commander Decks", "status": "physical", "description": ""},
            {"id": 102, "name": "B", "color": "U", "commanders": [], "commander_uids": [],
             "folder": "Test Decks", "status": "testing", "description": ""},
        ]
        out = {str(d["id"]): d for d in registry.merge_full_sync(existing, fetched)}
        self.assertEqual(set(out), {"101", "102", "moxfield:Z"})
        a = out["101"]
        self.assertEqual(a["name"], "A")
        self.assertEqual(set(a["links"]), {"archidekt", "moxfield"})
        self.assertEqual(a["moxfield_id"], "M1")
        self.assertEqual(a["cards"], [{"name": "X", "quantity": 1}])
        self.assertEqual(a["registry_id"], "archidekt:101")
        self.assertEqual(out["102"]["links"]["archidekt"]["deck_id"], "102")
        self.assertEqual(out["moxfield:Z"]["links"]["moxfield"]["deck_id"], "Z")

    def test_sync_py_writes_merged_registry(self):
        import sync
        r = FakeRedis()
        r.set("decks", json.dumps([
            {"id": 101, "name": "A", "status": "physical", "source": "archidekt", "source_id": "101",
             "registry_id": "archidekt:101",
             "links": {"moxfield": {"identifier": "https://moxfield.com/decks/M1", "deck_id": "M1"}}},
        ]))

        def fake_get(url, *a, **k):
            resp = mock.Mock()
            resp.raise_for_status = lambda: None
            if "users" in url:
                resp.json = lambda: {"results": [{"id": 1}]}
            elif "v3" in url:
                resp.json = lambda: {"results": [{"id": 101, "name": "A", "colors": {"W": 1}, "parentFolderId": 588380}]}
            else:
                resp.json = lambda: {"description": "", "cards": []}
            return resp

        with mock.patch.object(sync, "r", r), mock.patch.object(sync.requests, "get", fake_get), \
                mock.patch.object(sync.time, "sleep", lambda s: None):
            sync.sync_inventory()
        d = json.loads(r.get("decks"))[0]
        self.assertEqual(d["links"]["moxfield"]["deck_id"], "M1")
        self.assertEqual(d["links"]["archidekt"]["deck_id"], "101")


class TestImportRoute(Base):
    def test_import_writes_links_and_attaches_moxfield(self):
        from fastapi.testclient import TestClient
        import app as app_module
        r = FakeRedis()
        PROVIDERS["archidekt"] = FakeProvider([arch_deck(101, "A", ["Cmd A"])])
        PROVIDERS["moxfield"] = FakeProvider([mox_deck("M1", "A", ["Cmd A"]), mox_deck("M2", "New", ["Cmd Z"])])
        with mock.patch.object(app_module, "r", r), \
                mock.patch.object(app_module.interactions_service, "start_rebuild", lambda *a, **k: None):
            c = TestClient(app_module.app)
            d = c.post("/api/import", json={"provider": "archidekt", "id": "101"}).json()["deck"]
            self.assertEqual(d["links"]["archidekt"]["deck_id"], "101")
            d = c.post("/api/import", json={"provider": "moxfield", "id": "M1"}).json()["deck"]
            self.assertEqual(d["registry_id"], "archidekt:101")
            self.assertEqual(set(d["links"]), {"archidekt", "moxfield"})
            d = c.post("/api/import", json={"provider": "moxfield", "id": "M2"}).json()["deck"]
            self.assertEqual(d["registry_id"], "moxfield:M2")
            self.assertEqual(d["links"]["moxfield"]["deck_id"], "M2")
        self.assertEqual(len(json.loads(r.get("decks"))), 2)


class TestBackfill(Base):
    def test_backfill_is_idempotent_and_matches_moxfield(self):
        r = self.r
        registry._save_decks(r, [
            {"id": 5054882, "name": "The Return of the King", "status": "physical", "source": "archidekt",
             "source_id": "5054882", "registry_id": "archidekt:5054882", "commanders": ["Aragorn, the Uniter"],
             "url": "https://archidekt.com/decks/5054882", "cards": [{"name": "Sol Ring", "quantity": 1}]},
            {"id": 7, "name": "Other", "status": "testing", "source": "archidekt", "source_id": "7",
             "registry_id": "archidekt:7", "commanders": ["Nobody"]},
        ])
        self.assertEqual(len(backfill_deck_links.backfill_archidekt(r, apply=False)), 2)
        self.assertFalse(any(d.get("links") for d in registry.list_decks(r)), "dry run must not write")
        self.assertEqual(len(backfill_deck_links.backfill_archidekt(r, apply=True)), 2)
        self.assertEqual(backfill_deck_links.backfill_archidekt(r, apply=True), [])
        self.assertEqual(registry.find_deck(r, "archidekt:7")["links"]["archidekt"]["identifier"],
                         "https://archidekt.com/decks/7")

        prov = FakeProvider([mox_deck("ROTK1", "The Return of the King", ["Aragorn, the Uniter"]),
                             mox_deck("NOPE", "Nope", ["Helga, Skittish Seer"])])
        rep = backfill_deck_links.backfill_moxfield(r, "CovaDax", apply=True, provider=prov)
        self.assertEqual(rep["matched"], [("The Return of the King", "The Return of the King")])
        self.assertEqual([u["deck_id"] for u in rep["unmatched"]], ["NOPE"])
        rep2 = backfill_deck_links.backfill_moxfield(r, "CovaDax", apply=True, provider=prov)
        self.assertEqual(rep2["matched"], [])
        self.assertEqual(rep2["already"], ["The Return of the King"])
        rotk = registry.find_deck(r, "archidekt:5054882")
        self.assertEqual(rotk["status"], "physical")
        self.assertEqual(rotk["cards"], [{"name": "Sol Ring", "quantity": 1}])
        self.assertEqual(rotk["moxfield_id"], "ROTK1")


if __name__ == "__main__":
    unittest.main()
