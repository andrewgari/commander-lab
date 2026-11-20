"""
LOCK (t_e9ec412a): the Archidekt physical folder (ARCHIDEKT_PHYSICAL_FOLDER_ID,
default 588380) decides which decks are physical. Physical decks are locked;
every other deck is editable between digital, testing and retired.

Covers: folder -> physical derivation, the seed rule, both folder-move
transitions (instances preserved on the way in via auto_bind_physical,
pending_removal on the way out), the status API rejections, Moxfield
inheritance, sync.py storing parentFolderId/theorycrafted, and the
HUD.deckStatusControl builder (run in node).

All data is in-memory (FakeRedis); nothing touches a real deck.
Run: python -m unittest tests.test_physical_folder_lock
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ARCHIDEKT_USERNAME", "test_user")

import src.server.domain.inventory.instances as instance_store  # noqa: E402
import src.server.infra.providers.linked_accounts as linked_accounts  # noqa: E402
import src.server.domain.decks.registry as registry  # noqa: E402
from src.server.infra.providers import PROVIDERS  # noqa: E402
from tests.test_instances import FakeRedis  # noqa: E402

PHYS = 588380
TEST_FOLDER = 656395
ROOT_FOLDER = 45665
CARDS = [{"name": "Sol Ring", "quantity": 1}, {"name": "Arcane Signet", "quantity": 1}]


def arch(did, name="Deck", folder=PHYS, theorycrafted=False, commanders=None, cards=None):
    return {
        "source": "archidekt", "source_id": str(did), "name": name, "color": "W",
        "commanders": commanders or [f"Cmd {did}"], "commander_uids": [], "folder": "Imported",
        "description": "", "cards": list(cards or CARDS), "url": f"https://archidekt.com/decks/{did}",
        "parent_folder_id": folder, "theorycrafted": theorycrafted,
    }


def mox(pid, name, commanders):
    return {
        "source": "moxfield", "source_id": pid, "name": name, "color": "W",
        "commanders": commanders, "commander_uids": [], "folder": "Imported",
        "description": "", "cards": [{"name": "Other Card", "quantity": 1}],
        "url": f"https://moxfield.com/decks/{pid}",
    }


def bound(r, rid):
    deck = registry.find_deck(r, rid)
    return instance_store.list_deck_instances(r, registry.deck_instance_ids(deck), ownership_status="in_deck")


class TestDerivation(unittest.TestCase):
    def test_folder_id_from_env_with_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(registry.PHYSICAL_FOLDER_ENV, None)
            self.assertEqual(registry.physical_folder_id(), 588380)
        with mock.patch.dict(os.environ, {registry.PHYSICAL_FOLDER_ENV: "123"}):
            self.assertEqual(registry.physical_folder_id(), 123)
            self.assertTrue(registry.in_physical_folder(123))
            self.assertFalse(registry.in_physical_folder(PHYS))
        with mock.patch.dict(os.environ, {registry.PHYSICAL_FOLDER_ENV: "junk"}):
            self.assertEqual(registry.physical_folder_id(), 588380)

    def test_folder_id_shapes(self):
        self.assertTrue(registry.in_physical_folder(PHYS))
        self.assertTrue(registry.in_physical_folder(str(PHYS)))
        self.assertTrue(registry.in_physical_folder({"id": PHYS}))
        for v in (None, TEST_FOLDER, ROOT_FOLDER, "x", True):
            self.assertFalse(registry.in_physical_folder(v), v)

    def test_derive_status(self):
        self.assertEqual(registry.derive_status(None, True, True), "physical")
        self.assertEqual(registry.derive_status("retired", True, False), "physical")
        self.assertEqual(registry.derive_status(None, False, True), "testing")
        self.assertEqual(registry.derive_status(None, False, False), "digital")
        self.assertEqual(registry.derive_status("retired", False, True), "retired")
        self.assertEqual(registry.derive_status("digital", False, True), "digital")
        # Leaving the folder: saved "physical" is not a non-physical status -> seed.
        self.assertEqual(registry.derive_status("physical", False, True), "testing")
        self.assertEqual(registry.derive_status("physical", False, False), "digital")


class TestUpsertAppliesFolderRule(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_in_folder_is_physical_locked_and_bound(self):
        deck = registry.upsert_deck(self.r, arch(1, folder=PHYS))
        self.assertEqual(deck["status"], "physical")
        self.assertEqual(deck["status_origin"], "archidekt_folder")
        self.assertTrue(registry.is_locked(deck))
        self.assertEqual(self.r.get("deck_status:1"), "physical")
        self.assertEqual(sorted(i["card_name"] for i in bound(self.r, "archidekt:1")), ["Arcane Signet", "Sol Ring"])

    def test_seed_rule_outside_folder(self):
        self.assertEqual(registry.upsert_deck(self.r, arch(2, folder=TEST_FOLDER, theorycrafted=True))["status"], "testing")
        self.assertEqual(registry.upsert_deck(self.r, arch(3, folder=TEST_FOLDER, theorycrafted=False))["status"], "digital")
        self.assertEqual(registry.upsert_deck(self.r, arch(4, folder=ROOT_FOLDER))["status"], "digital")
        for rid in ("archidekt:2", "archidekt:3", "archidekt:4"):
            self.assertFalse(registry.is_locked(registry.find_deck(self.r, rid)))
        self.assertEqual(instance_store.list_instances(self.r), [], "non-physical decks have no instances")

    def test_saved_user_status_is_kept_on_resync(self):
        registry.upsert_deck(self.r, arch(5, folder=TEST_FOLDER, theorycrafted=True))
        registry.set_user_status(self.r, "archidekt:5", "retired")
        deck = registry.upsert_deck(self.r, arch(5, folder=TEST_FOLDER, theorycrafted=True))
        self.assertEqual(deck["status"], "retired")
        self.assertEqual(deck["status_origin"], "user")
        # A user-picked "testing" also survives (it is not the legacy default).
        registry.set_user_status(self.r, "archidekt:5", "testing")
        registry.upsert_deck(self.r, arch(5, folder=TEST_FOLDER, theorycrafted=False))
        self.assertEqual(registry.find_deck(self.r, "archidekt:5")["status"], "testing")

    def test_legacy_testing_default_is_reseeded_but_digital_retired_kept(self):
        # Pre-rule registry: everything outside the folder was auto-"testing".
        registry._save_decks(self.r, [
            {"id": 10, "name": "A", "status": "testing", "source": "archidekt", "source_id": "10", "registry_id": "archidekt:10"},
            {"id": 11, "name": "B", "status": "testing", "source": "archidekt", "source_id": "11", "registry_id": "archidekt:11"},
            {"id": 12, "name": "C", "status": "retired", "source": "archidekt", "source_id": "12", "registry_id": "archidekt:12"},
        ])
        for did in ("10", "11", "12"):
            self.r.set(f"deck_status:{did}", registry.find_deck(self.r, f"archidekt:{did}")["status"])
        registry.upsert_deck(self.r, arch(10, folder=TEST_FOLDER, theorycrafted=True))
        registry.upsert_deck(self.r, arch(11, folder=ROOT_FOLDER, theorycrafted=False))
        registry.upsert_deck(self.r, arch(12, folder=TEST_FOLDER, theorycrafted=True))
        self.assertEqual(registry.find_deck(self.r, "archidekt:10")["status"], "testing")
        self.assertEqual(registry.find_deck(self.r, "archidekt:11")["status"], "digital")
        self.assertEqual(registry.find_deck(self.r, "archidekt:12")["status"], "retired")

    def test_deck_without_folder_data_is_untouched(self):
        registry._save_decks(self.r, [{"id": 20, "name": "Old", "status": "physical", "source": "archidekt",
                                       "source_id": "20", "registry_id": "archidekt:20"}])
        self.assertIsNone(registry.apply_folder_rule(self.r, "archidekt:20"))
        self.assertTrue(registry.is_locked(registry.find_deck(self.r, "archidekt:20")))


class TestFolderMoves(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_leaving_folder_preserves_instances_and_flags_pending_removal(self):
        registry.upsert_deck(self.r, arch(30, folder=PHYS))
        before = {i["id"] for i in bound(self.r, "archidekt:30")}
        self.assertEqual(len(before), 2)

        deck = registry.upsert_deck(self.r, arch(30, folder=TEST_FOLDER, theorycrafted=True))
        self.assertEqual(deck["status"], "testing", "leaving the folder falls back to the seed rule")
        self.assertFalse(registry.is_locked(deck))
        after = bound(self.r, "archidekt:30")
        self.assertEqual({i["id"] for i in after}, before, "no instance deleted or unbound")
        for inst in after:
            self.assertEqual(inst["ownership_status"], "in_deck")
            self.assertEqual(inst["pending_removal"]["reason"], registry.LEFT_PHYSICAL_FOLDER)
        pending = {i["id"] for i in instance_store.list_pending_removal(self.r, "30")}
        self.assertEqual(pending, before)

        # Adjudication still works on a non-physical deck: keep -> in_collection.
        one = sorted(before)[0]
        instance_store.resolve_resync_removal(self.r, one, "keep", deck_id="30")
        self.assertEqual(instance_store.get_instance(self.r, one)["ownership_status"], "in_collection")

    def test_leaving_to_root_seeds_digital(self):
        registry.upsert_deck(self.r, arch(31, folder=PHYS))
        self.assertEqual(registry.upsert_deck(self.r, arch(31, folder=ROOT_FOLDER))["status"], "digital")

    def test_entering_folder_auto_binds(self):
        registry.upsert_deck(self.r, arch(32, folder=TEST_FOLDER, theorycrafted=True))
        self.assertEqual(bound(self.r, "archidekt:32"), [])
        # A copy already in the collection is reused, not duplicated.
        spare = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        deck = registry.upsert_deck(self.r, arch(32, folder=PHYS))
        self.assertEqual(deck["status"], "physical")
        ids = {i["id"] for i in bound(self.r, "archidekt:32")}
        self.assertIn(spare["id"], ids)
        self.assertEqual(len(ids), 2)

    def test_round_trip_clears_left_folder_flags_and_never_duplicates(self):
        registry.upsert_deck(self.r, arch(33, folder=PHYS))
        first = {i["id"] for i in bound(self.r, "archidekt:33")}
        registry.upsert_deck(self.r, arch(33, folder=TEST_FOLDER))
        registry.upsert_deck(self.r, arch(33, folder=PHYS))
        back = bound(self.r, "archidekt:33")
        self.assertEqual({i["id"] for i in back}, first)
        self.assertFalse(any(i.get("pending_removal") for i in back))
        self.assertEqual(len(instance_store.list_instances(self.r)), 2)

    def test_entering_folder_counts_copies_bound_under_registry_id(self):
        # The manual add-card path binds under the registry id ("archidekt:34");
        # auto_bind_physical binds under the raw id (34). Entering the folder
        # must count the existing copy, not add a duplicate (Copilot #88).
        registry.upsert_deck(self.r, arch(34, folder=TEST_FOLDER))
        inst = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        instance_store.transition_status(self.r, inst["id"], "in_deck", deck_id="archidekt:34", deck_name="Deck")
        registry.upsert_deck(self.r, arch(34, folder=PHYS))
        sol = [i for i in bound(self.r, "archidekt:34") if i["card_name"] == "Sol Ring"]
        self.assertEqual([i["id"] for i in sol], [inst["id"]])
        self.assertEqual(len(instance_store.list_instances(self.r, card_name="Sol Ring")), 1)


class TestUserStatus(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        registry.upsert_deck(self.r, arch(40, folder=PHYS))
        registry.upsert_deck(self.r, arch(41, folder=TEST_FOLDER, theorycrafted=True))

    def test_locked_deck_rejects_any_change(self):
        for s in ("digital", "testing", "retired"):
            with self.assertRaises(registry.StatusLockedError):
                registry.set_user_status(self.r, "archidekt:40", s)
        self.assertEqual(registry.find_deck(self.r, "archidekt:40")["status"], "physical")

    def test_physical_is_never_settable(self):
        for rid in ("archidekt:40", "archidekt:41"):
            with self.assertRaises(registry.StatusNotAllowedError):
                registry.set_user_status(self.r, rid, "physical")
        self.assertEqual(registry.find_deck(self.r, "archidekt:41")["status"], "testing")
        self.assertEqual(bound(self.r, "archidekt:41"), [])

    def test_unlocked_deck_cycles_editable_statuses(self):
        for s in ("digital", "retired", "testing"):
            registry.set_user_status(self.r, "archidekt:41", s)
            self.assertEqual(registry.find_deck(self.r, "archidekt:41")["status"], s)
            self.assertEqual(self.r.get("deck_status:41"), s)


class TestMoxfield(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self._orig = dict(PROVIDERS)

    def tearDown(self):
        PROVIDERS.clear()
        PROVIDERS.update(self._orig)

    def test_matched_moxfield_inherits_archidekt_lock_and_status(self):
        registry.upsert_deck(self.r, arch(50, "Holy Hellfire", folder=PHYS, commanders=["Kaalia"]))
        res = registry.attach_provider_deck(self.r, mox("M50", "Holy Hellfire", ["Kaalia"]))
        self.assertEqual(res["result"], "matched")
        deck = registry.find_deck(self.r, "archidekt:50")
        self.assertEqual(deck["status"], "physical")
        self.assertTrue(registry.is_locked(deck))
        self.assertEqual(deck["links"]["moxfield"]["deck_id"], "M50")

    def test_archidekt_wins_over_an_older_moxfield_only_entry(self):
        # An older /api/import registered the Moxfield deck on its own (digital).
        registry.upsert_deck(self.r, mox("M51", "Same Deck", ["Cmd X"]))
        self.assertEqual(registry.find_deck(self.r, "moxfield:M51")["status"], "digital")
        registry.upsert_deck(self.r, arch(51, "Same Deck", folder=PHYS, commanders=["Cmd X"]))
        res = registry.attach_provider_deck(self.r, mox("M51", "Same Deck", ["Cmd X"]))
        self.assertEqual(res["result"], "matched")
        self.assertEqual(registry.registry_id_of(res["deck"]), "archidekt:51")
        self.assertEqual(res["deck"]["status"], "physical")
        self.assertIsNotNone(registry.find_deck(self.r, "moxfield:M51"), "older entry is not deleted")

    def test_unmatched_moxfield_only_deck_is_digital_and_editable(self):
        deck = registry.upsert_deck(self.r, mox("M52", "Brew", ["Nobody"]), default_status="testing")
        self.assertEqual(deck["status"], "digital")
        self.assertFalse(registry.is_locked(deck))
        with self.assertRaises(registry.StatusNotAllowedError):
            registry.set_user_status(self.r, "moxfield:M52", "physical")
        registry.set_user_status(self.r, "moxfield:M52", "testing")
        registry.upsert_deck(self.r, mox("M52", "Brew", ["Nobody"]))
        self.assertEqual(registry.find_deck(self.r, "moxfield:M52")["status"], "testing")

    def test_legacy_physical_moxfield_only_deck_is_demoted_via_pending_removal(self):
        registry._save_decks(self.r, [{"id": "moxfield:M53", "name": "Old", "status": "physical", "source": "moxfield",
                                       "source_id": "M53", "registry_id": "moxfield:M53", "cards": CARDS}])
        instance_store.auto_bind_physical(self.r, "moxfield:M53", "Old", CARDS)
        registry.apply_folder_rules(self.r)
        deck = registry.find_deck(self.r, "moxfield:M53")
        self.assertEqual(deck["status"], "digital")
        insts = bound(self.r, "moxfield:M53")
        self.assertEqual(len(insts), 2)
        self.assertTrue(all(i["pending_removal"]["reason"] == registry.LEFT_PHYSICAL_FOLDER for i in insts))

    def test_linked_account_sync_applies_rule(self):
        class P:
            def __init__(self, decks): self.decks = {d["source_id"]: d for d in decks}
            def list_decks(self, u): return list(self.decks)
            def fetch_deck(self, i): return dict(self.decks[i])
        PROVIDERS["archidekt"] = P([arch(60, "P", folder=PHYS), arch(61, "T", folder=TEST_FOLDER, theorycrafted=True),
                                    arch(62, "D", folder=TEST_FOLDER), arch(63, "L", folder=ROOT_FOLDER)])
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")
        linked_accounts.sync_account(self.r, "archidekt:CovaDax")
        got = {d["source_id"]: d["status"] for d in registry.list_decks(self.r)}
        self.assertEqual(got, {"60": "physical", "61": "testing", "62": "digital", "63": "digital"})


class TestStatusApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import src.server.app.main as app_module
        self.r = FakeRedis()
        registry.upsert_deck(self.r, arch(70, "Locked", folder=PHYS))
        registry.upsert_deck(self.r, arch(71, "Free", folder=TEST_FOLDER, theorycrafted=True))
        self.patch = mock.patch.object(app_module, "r", self.r)
        self.patch.start()
        self.c = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    def post(self, did, body):
        return self.c.post(f"/api/decks/{did}/status", json=body)

    def test_locked_deck_change_is_409(self):
        res = self.post("archidekt:70", {"status": "retired"})
        self.assertEqual(res.status_code, 409)
        self.assertFalse(res.json()["success"])
        self.assertIn("Commander Decks folder", res.json()["error"])
        self.assertEqual(registry.find_deck(self.r, "archidekt:70")["status"], "physical")

    def test_physical_on_any_deck_is_400(self):
        for did in ("archidekt:70", "archidekt:71", "71"):
            res = self.post(did, {"status": "physical"})
            self.assertEqual(res.status_code, 400, did)
            self.assertIn("Physical can't be set here", res.json()["error"])
        self.assertEqual(registry.find_deck(self.r, "archidekt:71")["status"], "testing")

    def test_bad_values(self):
        self.assertEqual(self.post("archidekt:71", {"status": "virtual"}).status_code, 400)
        self.assertEqual(self.post("archidekt:71", {}).status_code, 400)
        self.assertEqual(self.c.post("/api/decks/archidekt:71/status", content="nope").status_code, 400)
        self.assertEqual(self.post("archidekt:999", {"status": "digital"}).status_code, 404)

    def test_editable_change_ok_by_raw_or_registry_id(self):
        res = self.post("71", {"status": "retired"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(registry.find_deck(self.r, "archidekt:71")["status"], "retired")

    def test_api_decks_reports_lock(self):
        decks = {d["source_id"]: d for d in self.c.get("/api/decks").json()["decks"]}
        self.assertTrue(decks["70"]["status_locked"])
        self.assertFalse(decks["71"]["status_locked"])


class TestSyncPy(unittest.TestCase):
    def test_sync_stores_folder_data_and_applies_rule(self):
        import src.server.workers.sync as sync
        r = FakeRedis()
        rows = [
            {"id": 101, "name": "Phys", "colors": {"W": 1}, "parentFolderId": PHYS, "theorycrafted": False},
            {"id": 102, "name": "Theory", "colors": {}, "parentFolderId": TEST_FOLDER, "theorycrafted": True},
            {"id": 103, "name": "Swag", "colors": {}, "parentFolderId": TEST_FOLDER, "theorycrafted": False},
            {"id": 104, "name": "Loose", "colors": {}, "parentFolderId": ROOT_FOLDER, "theorycrafted": False},
        ]

        def fake_get(url, *a, **k):
            resp = mock.Mock()
            resp.raise_for_status = lambda: None
            if "users" in url:
                resp.json = lambda: {"results": [{"id": 1}]}
            elif "v3" in url:
                resp.json = lambda: {"results": rows}
            else:
                resp.json = lambda: {"description": "", "cards": [
                    {"quantity": 1, "categories": [], "card": {"oracleCard": {"name": "Sol Ring"}}},
                    {"quantity": 2, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Maybe"}}},
                ]}
            return resp

        with mock.patch.object(sync, "r", r), mock.patch.object(sync.requests, "get", fake_get), \
                mock.patch.object(sync.time, "sleep", lambda s: None):
            sync.sync_inventory()
            by = {str(d["id"]): d for d in json.loads(r.get("decks"))}
            self.assertEqual({k: v["status"] for k, v in by.items()},
                             {"101": "physical", "102": "testing", "103": "digital", "104": "digital"})
            self.assertEqual(by["101"]["parent_folder_id"], PHYS)
            self.assertIs(by["102"]["theorycrafted"], True)
            # A new deck in the folder is bound from the synced mainboard (Copilot #88).
            self.assertEqual(by["101"]["cards"], [{"name": "Sol Ring", "quantity": 1}])
            self.assertEqual([i["card_name"] for i in bound(r, "archidekt:101")], ["Sol Ring"])

            # Deck moves out of the folder on Archidekt -> seed rule on the next sync.
            rows[0] = dict(rows[0], parentFolderId=ROOT_FOLDER)
            sync.sync_inventory()
            self.assertEqual(registry.find_deck(r, "archidekt:101")["status"], "digital")


class TestArchidektProvider(unittest.TestCase):
    def test_fetch_deck_carries_folder_and_theorycrafted(self):
        from src.server.infra.providers import archidekt
        resp = mock.Mock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"name": "X", "cards": [], "parentFolder": PHYS, "theorycrafted": True}
        with mock.patch.object(archidekt.requests, "get", lambda *a, **k: resp):
            d = archidekt.fetch_deck("417707")
        self.assertEqual(d["parent_folder_id"], PHYS)
        self.assertIs(d["theorycrafted"], True)

    def test_folder_name_from_list_rows_reaches_fetch_deck(self):
        from src.server.infra.providers import archidekt

        def get(url, *a, **k):
            resp = mock.Mock()
            resp.raise_for_status = lambda: None
            if "users" in url:
                resp.json = lambda: {"results": [{"id": 1}]}
            elif "v3" in url:
                resp.json = lambda: {"results": [{"id": 9, "parentFolderId": PHYS, "parentFolderName": "Commander Decks"}],
                                     "next": None}
            else:
                resp.json = lambda: {"name": "X", "cards": [], "parentFolder": PHYS, "theorycrafted": False}
            return resp
        with mock.patch.object(archidekt.requests, "get", get):
            archidekt.list_decks("CovaDax")
            d = archidekt.fetch_deck("9")
        self.assertEqual(d["folder"], "Commander Decks")


HUD_JS = Path(ROOT) / "src/web/static" / "js" / "hud.js"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestDeckStatusControl(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [], getElementById: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const H = window.HUD;
process.stdout.write(JSON.stringify({
  locked: H.deckStatusControl({status: 'physical', status_locked: true}, {id: 's1'}),
  free: H.deckStatusControl({status: 'testing', status_locked: false}, {id: 's2'}),
  note: H.DECK_LOCK_NOTE,
}));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_locked_select_is_disabled_with_note(self):
        h = self.out["locked"]
        self.assertIn("disabled", h)
        self.assertIn('aria-describedby="s1Note"', h)
        self.assertIn("Physical: managed by your Archidekt Commander Decks folder", h)
        self.assertEqual(h.count("<option"), 1)

    def test_unlocked_select_offers_virtual_wip_retired(self):
        h = self.out["free"]
        self.assertNotIn("disabled", h)
        self.assertNotIn('value="physical"', h)
        self.assertIn('<option value="digital">Virtual</option>', h)
        self.assertIn('<option value="testing" selected>WIP</option>', h)
        self.assertIn('<option value="retired">Retired</option>', h)


if __name__ == "__main__":
    unittest.main()
