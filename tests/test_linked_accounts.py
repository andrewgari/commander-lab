"""
Unit tests for linked_accounts.py, using the same FakeRedis stand-in
pattern as tests/test_instances.py / tests/test_registry.py. Provider
network calls are stubbed by monkeypatching providers.PROVIDERS entries
with small fake modules -- no real HTTP.

Run: python tests/test_linked_accounts.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import linked_accounts
import registry
from providers import PROVIDERS


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls linked_accounts.py
    and registry.py make (get/set only -- no pipelines needed here since
    upsert_deck's instance-store side effects use a real FakeRedis pipe
    too, mirrored below)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def set(self, key, value):
        self.ops.append(("set", key, value))
        return self

    def sadd(self, key, *values):
        self.ops.append(("sadd", key, values))
        return self

    def srem(self, key, *values):
        self.ops.append(("srem", key, values))
        return self

    def execute(self):
        for op in self.ops:
            kind = op[0]
            if kind == "set":
                self.r.store[op[1]] = op[2]
            elif kind == "sadd":
                key = op[1]
                s = self.r.store.setdefault(key, set())
                if not isinstance(s, set):
                    s = set()
                for v in op[2]:
                    s.add(v)
                self.r.store[key] = s
            elif kind == "srem":
                key = op[1]
                s = self.r.store.get(key)
                if isinstance(s, set):
                    for v in op[2]:
                        s.discard(v)
        self.ops = []


class FakeArchidektProvider:
    """Stand-in for providers.archidekt used by add_account/sync_account
    tests. list_decks/fetch_deck return canned data per-username so tests
    can exercise both the happy path and the partial-failure path."""

    def __init__(self):
        self.decks_by_user = {}
        self.bad_deck_ids = set()

    def list_decks(self, username):
        if username not in self.decks_by_user:
            raise ValueError(f"no Archidekt user found for username: {username}")
        return list(self.decks_by_user[username])

    def fetch_deck(self, deck_id):
        if deck_id in self.bad_deck_ids:
            raise ValueError(f"failed to fetch deck {deck_id}")
        return {
            "source": "archidekt",
            "source_id": str(deck_id),
            "name": f"Deck {deck_id}",
            "color": "C",
            "commanders": [],
            "commander_uids": [],
            "folder": "Imported",
            "description": "",
            "cards": [],
            "url": f"https://archidekt.com/decks/{deck_id}",
        }


class LinkedAccountsTestBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.fake_provider = FakeArchidektProvider()
        self._orig_archidekt = PROVIDERS.get("archidekt")
        PROVIDERS["archidekt"] = self.fake_provider

    def tearDown(self):
        if self._orig_archidekt is not None:
            PROVIDERS["archidekt"] = self._orig_archidekt
        else:
            PROVIDERS.pop("archidekt", None)


class TestAddAccount(LinkedAccountsTestBase):
    def test_add_account_probes_and_persists_enabled(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1", "2"]
        account = linked_accounts.add_account(self.r, "archidekt", "CovaDax")

        self.assertEqual(account["id"], "archidekt:CovaDax")
        self.assertTrue(account["enabled"])
        self.assertIsNone(account["last_synced_at"])

        stored = linked_accounts.list_accounts(self.r)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], "archidekt:CovaDax")

    def test_add_account_dedups_on_provider_username(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")
        with self.assertRaises(linked_accounts.LinkedAccountError):
            linked_accounts.add_account(self.r, "archidekt", "CovaDax")

    def test_add_account_typo_username_fails_fast(self):
        # No entry seeded for "Typo0Name" -> fake provider raises, mirroring
        # a real ArchidektUserNotFound. Nothing should be persisted.
        with self.assertRaises(ValueError):
            linked_accounts.add_account(self.r, "archidekt", "Typo0Name")
        self.assertEqual(linked_accounts.list_accounts(self.r), [])

    def test_add_account_unknown_provider_raises(self):
        with self.assertRaises(linked_accounts.LinkedAccountError):
            linked_accounts.add_account(self.r, "nonexistent-provider", "someone")


class TestRemoveAccount(LinkedAccountsTestBase):
    def test_remove_account_removes_registry_entry_only(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")
        linked_accounts.sync_account(self.r, "archidekt:CovaDax")

        decks_before = registry.list_decks(self.r)
        self.assertEqual(len(decks_before), 1)
        self.assertEqual(decks_before[0].get("linked_account_id"), "archidekt:CovaDax")

        linked_accounts.remove_account(self.r, "archidekt:CovaDax")

        self.assertEqual(linked_accounts.list_accounts(self.r), [])
        # Deck stays, and its linked_account_id stamp is left untouched --
        # non-destructive/pull-only unlink.
        decks_after = registry.list_decks(self.r)
        self.assertEqual(len(decks_after), 1)
        self.assertEqual(decks_after[0].get("linked_account_id"), "archidekt:CovaDax")

    def test_remove_unknown_account_raises(self):
        with self.assertRaises(linked_accounts.LinkedAccountError):
            linked_accounts.remove_account(self.r, "archidekt:nobody")


class TestSyncAccount(LinkedAccountsTestBase):
    def test_sync_account_upserts_decks_and_stamps_linked_account_id(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["101", "102"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")

        account = linked_accounts.sync_account(self.r, "archidekt:CovaDax")

        self.assertIsNotNone(account["last_synced_at"])
        self.assertEqual(account["last_sync_result"]["decks_synced"], 2)
        self.assertEqual(account["last_sync_result"]["failures"], [])

        decks = registry.list_decks(self.r)
        self.assertEqual(len(decks), 2)
        for deck in decks:
            self.assertEqual(deck["linked_account_id"], "archidekt:CovaDax")

    def test_sync_account_partial_failure_does_not_block_the_rest(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["101", "102", "103"]
        self.fake_provider.bad_deck_ids.add("102")
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")

        account = linked_accounts.sync_account(self.r, "archidekt:CovaDax")

        self.assertEqual(account["last_sync_result"]["decks_synced"], 2)
        failures = account["last_sync_result"]["failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["deck_id"], "102")

        decks = registry.list_decks(self.r)
        synced_ids = {d["source_id"] for d in decks}
        self.assertEqual(synced_ids, {"101", "103"})

    def test_sync_unknown_account_raises(self):
        with self.assertRaises(linked_accounts.LinkedAccountError):
            linked_accounts.sync_account(self.r, "archidekt:nobody")


class TestSyncAll(LinkedAccountsTestBase):
    def test_sync_all_runs_every_enabled_account(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1"]
        self.fake_provider.decks_by_user["OtherUser"] = ["2", "3"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")
        linked_accounts.add_account(self.r, "archidekt", "OtherUser")

        results = linked_accounts.sync_all(self.r)

        self.assertEqual(len(results), 2)
        synced_counts = {r["id"]: r["last_sync_result"]["decks_synced"] for r in results}
        self.assertEqual(synced_counts["archidekt:CovaDax"], 1)
        self.assertEqual(synced_counts["archidekt:OtherUser"], 2)

    def test_sync_all_skips_disabled_accounts(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")

        accounts = json.loads(self.r.get(linked_accounts.LINKED_ACCOUNTS_KEY))
        accounts[0]["enabled"] = False
        self.r.set(linked_accounts.LINKED_ACCOUNTS_KEY, json.dumps(accounts))

        results = linked_accounts.sync_all(self.r)
        self.assertEqual(results, [])

    def test_sync_all_one_bad_account_does_not_block_the_rest(self):
        self.fake_provider.decks_by_user["CovaDax"] = ["1"]
        self.fake_provider.decks_by_user["OtherUser"] = ["2"]
        linked_accounts.add_account(self.r, "archidekt", "CovaDax")
        linked_accounts.add_account(self.r, "archidekt", "OtherUser")

        # Remove OtherUser's provider data after linking, so its
        # sync_account -> list_decks raises mid-sync_all.
        del self.fake_provider.decks_by_user["OtherUser"]

        results = linked_accounts.sync_all(self.r)
        self.assertEqual(len(results), 2)

        by_id = {r["id"]: r for r in results}
        # CovaDax synced fine.
        self.assertEqual(by_id["archidekt:CovaDax"]["last_sync_result"]["decks_synced"], 1)
        # OtherUser's list_decks failure is recorded as a per-deck failure,
        # not an aborted sync_all.
        self.assertEqual(by_id["archidekt:OtherUser"]["last_sync_result"]["decks_synced"], 0)
        self.assertEqual(len(by_id["archidekt:OtherUser"]["last_sync_result"]["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
