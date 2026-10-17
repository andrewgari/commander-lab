"""Integration tests for the /api/linked-accounts REST endpoints in app.py.

These hit the routes via FastAPI's TestClient (real HTTP calls, real
request/response cycle) and mock only app.linked_accounts, mirroring the
@patch("app.registry")/@patch("app.instance_store") pattern already used in
tests/test_routes.py. The endpoints are thin wrappers -- these tests assert
they call through to linked_accounts.py correctly and translate
LinkedAccountError into the right HTTP status/response shape, not that
linked_accounts.py's own logic is correct (that's tests/test_linked_accounts.py).
"""
from fastapi.testclient import TestClient
import unittest
from unittest.mock import patch

from app import app
from linked_accounts import LinkedAccountError


class TestListLinkedAccounts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.linked_accounts")
    def test_list_returns_accounts_and_count(self, mock_la):
        accounts = [
            {"id": "archidekt:foo", "provider": "archidekt", "username": "foo",
             "enabled": True, "created_at": "t0", "last_synced_at": None, "last_sync_result": None},
        ]
        mock_la.list_accounts.return_value = accounts
        response = self.client.get("/api/linked-accounts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accounts"], accounts)
        self.assertEqual(data["count"], 1)
        mock_la.list_accounts.assert_called_once()

    @patch("app.linked_accounts")
    def test_list_empty(self, mock_la):
        mock_la.list_accounts.return_value = []
        response = self.client.get("/api/linked-accounts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["accounts"], [])
        self.assertEqual(data["count"], 0)


class TestAddLinkedAccount(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.linked_accounts")
    def test_add_account_validates_adds_and_syncs(self, mock_la):
        added = {"id": "archidekt:foo", "provider": "archidekt", "username": "foo",
                  "enabled": True, "created_at": "t0", "last_synced_at": None, "last_sync_result": None}
        synced = {**added, "last_synced_at": "t1", "last_sync_result": {"decks_synced": 2, "failures": []}}
        mock_la.add_account.return_value = added
        mock_la.sync_account.return_value = synced

        response = self.client.post("/api/linked-accounts", json={"provider": "archidekt", "username": "foo"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["account"], synced)
        add_args = mock_la.add_account.call_args[0]
        self.assertEqual(add_args[1:], ("archidekt", "foo"))
        sync_args = mock_la.sync_account.call_args[0]
        self.assertEqual(sync_args[1:], ("archidekt:foo",))

    @patch("app.linked_accounts")
    def test_add_account_rejects_duplicate(self, mock_la):
        mock_la.add_account.side_effect = LinkedAccountError("account already linked: archidekt:foo")

        response = self.client.post("/api/linked-accounts", json={"provider": "archidekt", "username": "foo"})

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("already linked", data["error"])
        mock_la.sync_account.assert_not_called()

    @patch("app.linked_accounts")
    def test_add_account_rejects_unknown_provider_or_bad_username(self, mock_la):
        mock_la.add_account.side_effect = LinkedAccountError("unknown provider: bogus")

        response = self.client.post("/api/linked-accounts", json={"provider": "bogus", "username": "foo"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])


class TestRemoveLinkedAccount(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.linked_accounts")
    def test_remove_account_success(self, mock_la):
        mock_la.remove_account.return_value = True
        response = self.client.delete("/api/linked-accounts/archidekt:foo")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        remove_args = mock_la.remove_account.call_args[0]
        self.assertEqual(remove_args[1:], ("archidekt:foo",))

    @patch("app.linked_accounts")
    def test_remove_account_not_found(self, mock_la):
        mock_la.remove_account.side_effect = LinkedAccountError("linked account not found: archidekt:foo")
        response = self.client.delete("/api/linked-accounts/archidekt:foo")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])


class TestSyncOneLinkedAccount(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.linked_accounts")
    def test_sync_one_success(self, mock_la):
        account = {"id": "archidekt:foo", "last_synced_at": "t1",
                   "last_sync_result": {"decks_synced": 3, "failures": []}}
        mock_la.sync_account.return_value = account
        response = self.client.post("/api/linked-accounts/archidekt:foo/sync")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["account"], account)

    @patch("app.linked_accounts")
    def test_sync_one_not_found(self, mock_la):
        mock_la.sync_account.side_effect = LinkedAccountError("linked account not found: archidekt:foo")
        response = self.client.post("/api/linked-accounts/archidekt:foo/sync")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])


class TestSyncAllLinkedAccounts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.linked_accounts")
    def test_sync_all_returns_every_account(self, mock_la):
        accounts = [
            {"id": "archidekt:foo", "last_synced_at": "t1", "last_sync_result": {"decks_synced": 1, "failures": []}},
            {"id": "moxfield:bar", "last_synced_at": "t1", "last_sync_result": {"decks_synced": 0, "failures": [{"deck_id": None, "error": "boom"}]}},
        ]
        mock_la.sync_all.return_value = accounts
        response = self.client.post("/api/linked-accounts/sync-all")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["accounts"], accounts)
        self.assertEqual(data["count"], 2)


if __name__ == "__main__":
    unittest.main()
