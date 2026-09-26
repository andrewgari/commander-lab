"""Unit tests for providers/moxfield_writer.py — session loading, auth
refresh, folder/deck writes. No network: requests is monkeypatched.

Run: python tests/test_moxfield_writer.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.infra.providers import moxfield_writer as mw
from src.server.infra.providers.moxfield_writer import MoxfieldAuthError, MoxfieldWriteError


def write_state(cookies=True, user_id: object = "user-abc"):
    """Minimal Playwright storage_state file."""
    state = {"cookies": [], "origins": []}
    if cookies:
        state["cookies"] = [
            {"name": "sessionid", "value": "s3ss", "domain": ".moxfield.com"},
            {"name": "other", "value": "x", "domain": ".example.com"},
        ]
    if user_id:
        state["origins"] = [{
            "origin": "https://www.moxfield.com",
            "localStorage": [{"name": "user", "value": json.dumps({"userId": user_id})}],
        }]
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(state, handle)
    handle.close()
    return handle.name


def token_response(token="tok-1", status=200):
    res = MagicMock()
    res.status_code = status
    res.text = "" if status == 200 else "unauthorized"
    res.json.return_value = {"access_token": token, "user_id": "user-abc"}
    return res


class TestSessionLoading(unittest.TestCase):
    def test_missing_session_file_raises_with_setup_instructions(self):
        with patch.dict(os.environ, {"MOXFIELD_SESSION_FILE": "/nonexistent/state.json"}, clear=True):
            with self.assertRaises(MoxfieldAuthError) as ctx:
                mw._load_session_cookies()
        self.assertIn("setup_auth.py", str(ctx.exception))

    def test_only_moxfield_cookies_are_loaded(self):
        path = write_state()
        try:
            cookies = mw._load_session_cookies(path)
            self.assertEqual(cookies, {"sessionid": "s3ss"})
        finally:
            os.unlink(path)

    def test_state_without_moxfield_cookies_raises(self):
        path = write_state(cookies=False)
        try:
            with self.assertRaises(MoxfieldAuthError) as ctx:
                mw._load_session_cookies(path)
            self.assertIn("no moxfield.com cookies", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_user_id_is_read_from_local_storage(self):
        path = write_state(user_id="user-xyz")
        try:
            self.assertEqual(mw._user_id_from_state(path), "user-xyz")
        finally:
            os.unlink(path)

    def test_sidecar_moxfield_json_takes_precedence(self):
        """setup_auth.py writes .auth/moxfield.json from the live page, which is
        more reliable than Playwright's best-effort localStorage capture."""
        import tempfile as tf
        directory = tf.mkdtemp()
        state_path = os.path.join(directory, "state.json")
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump({
                "cookies": [{"name": "sessionid", "value": "s", "domain": ".moxfield.com"}],
                "origins": [{
                    "origin": "https://www.moxfield.com",
                    "localStorage": [{"name": "user", "value": json.dumps({"userId": "stale"})}],
                }],
            }, handle)
        with open(os.path.join(directory, "moxfield.json"), "w", encoding="utf-8") as handle:
            json.dump({"userId": "fresh"}, handle)

        self.assertEqual(mw._user_id_from_state(state_path), "fresh")

    def test_missing_sidecar_falls_back_to_storage_state(self):
        path = write_state(user_id="from-state")
        try:
            self.assertEqual(mw._user_id_from_state(path), "from-state")
        finally:
            os.unlink(path)


class ClientTestBase(unittest.TestCase):
    def setUp(self):
        self.path = write_state()
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def make_client(self):
        with patch.object(mw.requests, "post", return_value=token_response()):
            return mw.MoxfieldClient(session_path=self.path)


class TestAuth(ClientTestBase):
    def test_constructor_exchanges_cookies_for_a_bearer_token(self):
        with patch.object(mw.requests, "post", return_value=token_response("tok-9")) as mock_post:
            client = mw.MoxfieldClient(session_path=self.path)
        self.assertEqual(client.access_token, "tok-9")
        self.assertIn("/v1/account/token/refresh", mock_post.call_args.args[0])
        self.assertEqual(mock_post.call_args.kwargs["json"]["userId"], "user-abc")

    def test_expired_session_raises_with_refresh_instructions(self):
        with patch.object(mw.requests, "post", return_value=token_response(status=401)):
            with self.assertRaises(MoxfieldAuthError) as ctx:
                mw.MoxfieldClient(session_path=self.path)
        self.assertIn("setup_auth.py", str(ctx.exception))

    def test_missing_user_id_raises(self):
        path = write_state(user_id=None)
        try:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(MoxfieldAuthError) as ctx:
                    mw.MoxfieldClient(session_path=path)
            self.assertIn("userId", str(ctx.exception))
        finally:
            os.unlink(path)

    def test_headers_carry_bearer_and_session_user(self):
        client = self.make_client()
        headers = client._headers()
        self.assertEqual(headers["Authorization"], "Bearer tok-1")
        self.assertEqual(headers["X-Session-User"], "user-abc")

    def test_401_triggers_one_refresh_and_replay(self):
        client = self.make_client()
        unauthorized = MagicMock(status_code=401, text="nope")
        ok = MagicMock(status_code=200, text="{}")
        ok.json.return_value = {"ok": True}

        with patch.object(mw.requests, "request", side_effect=[unauthorized, ok]) as mock_req, \
             patch.object(mw.requests, "post", return_value=token_response("tok-2")):
            res = client._request("GET", "/v1/deck-folders")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(mock_req.call_count, 2)
        self.assertEqual(client.access_token, "tok-2")

    def test_repeated_401_does_not_loop_forever(self):
        client = self.make_client()
        unauthorized = MagicMock(status_code=401, text="nope")

        with patch.object(mw.requests, "request", return_value=unauthorized) as mock_req, \
             patch.object(mw.requests, "post", return_value=token_response("tok-2")):
            res = client._request("GET", "/v1/deck-folders")

        self.assertEqual(res.status_code, 401)
        self.assertEqual(mock_req.call_count, 2)  # original + one retry only


class TestFolders(ClientTestBase):
    def _resp(self, payload, status=200):
        res = MagicMock(status_code=status, text=json.dumps(payload))
        res.json.return_value = payload
        return res

    def test_list_folders_accepts_bare_list_and_page_wrapper(self):
        client = self.make_client()
        with patch.object(mw.requests, "request", return_value=self._resp([{"id": "f1", "name": "A"}])):
            self.assertEqual(client.list_folders()[0]["name"], "A")
        with patch.object(mw.requests, "request", return_value=self._resp({"data": [{"id": "f2", "name": "B"}]})):
            self.assertEqual(client.list_folders()[0]["name"], "B")

    def test_ensure_folder_reuses_existing_case_insensitively(self):
        client = self.make_client()
        existing = self._resp([{"id": "f1", "name": "Physical"}])
        with patch.object(mw.requests, "request", return_value=existing) as mock_req:
            folder = client.ensure_folder("physical")
        self.assertEqual(folder["id"], "f1")
        # only the GET happened; no folder was created
        self.assertEqual(mock_req.call_count, 1)
        self.assertEqual(mock_req.call_args.args[0], "GET")

    def test_ensure_folder_creates_when_absent(self):
        client = self.make_client()
        listing = self._resp([{"id": "f1", "name": "Other"}])
        created = self._resp({"id": "f2", "name": "Physical"}, status=201)
        with patch.object(mw.requests, "request", side_effect=[listing, created]) as mock_req:
            folder = client.ensure_folder("Physical")
        self.assertEqual(folder["id"], "f2")
        self.assertEqual(mock_req.call_args.args[0], "POST")
        self.assertEqual(mock_req.call_args.kwargs["json"], {"name": "Physical"})

    def test_invalid_folder_name_rejected_before_any_request(self):
        client = self.make_client()
        with patch.object(mw.requests, "request") as mock_req:
            with self.assertRaises(MoxfieldWriteError) as ctx:
                client.create_folder("Paper/Decks!")
        self.assertIn("letters, numbers, spaces and hyphens", str(ctx.exception))
        mock_req.assert_not_called()


class TestDeckCreation(ClientTestBase):
    def _resp(self, payload, status=200):
        res = MagicMock(status_code=status, text=json.dumps(payload))
        res.json.return_value = payload
        return res

    def test_create_deck_posts_import_text_visibility_and_folder(self):
        client = self.make_client()
        created = self._resp({"id": "mx1", "publicId": "pub1", "name": "Kaalia"}, status=201)
        with patch.object(mw.requests, "request", return_value=created) as mock_req:
            deck = client.create_deck(
                name="Kaalia", import_text="// Commander\n1 Kaalia of the Vast",
                visibility="private", folder_id="f1",
            )

        self.assertEqual(deck["publicId"], "pub1")
        body = mock_req.call_args.kwargs["json"]
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(body["deckFolderId"], "f1")
        self.assertEqual(body["format"], "commander")
        self.assertIn("Kaalia of the Vast", body["importText"])
        self.assertTrue(body["usePrefPrintings"])
        self.assertIn("/v3/decks", mock_req.call_args.args[1])

    def test_invalid_visibility_rejected(self):
        client = self.make_client()
        with self.assertRaises(MoxfieldWriteError):
            client.create_deck(name="X", import_text="1 Sol Ring", visibility="secret")

    def test_empty_import_text_rejected(self):
        client = self.make_client()
        with self.assertRaises(MoxfieldWriteError):
            client.create_deck(name="X", import_text="   ")

    def test_server_error_becomes_write_error_with_body(self):
        client = self.make_client()
        failed = MagicMock(status_code=500, text="boom")
        with patch.object(mw.requests, "request", return_value=failed):
            with self.assertRaises(MoxfieldWriteError) as ctx:
                client.create_deck(name="Kaalia", import_text="1 Sol Ring")
        self.assertIn("boom", str(ctx.exception))

    def test_description_put_sends_concurrency_headers(self):
        client = self.make_client()
        ok = MagicMock(status_code=200, text="{}")
        ok.json.return_value = {}
        deck = {"id": "mx1", "publicId": "pub1", "name": "Kaalia", "version": 3,
                "format": "commander", "visibility": "private"}

        with patch.object(mw.requests, "request", return_value=ok) as mock_req:
            client.update_deck_description(deck, "Commander: Kaalia of the Vast")

        self.assertEqual(mock_req.call_args.args[0], "PUT")
        self.assertIn("/v2/decks/mx1", mock_req.call_args.args[1])
        headers = mock_req.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Public-Deck-ID"], "pub1")
        self.assertEqual(headers["X-Deck-Version"], "3")
        self.assertEqual(mock_req.call_args.kwargs["json"]["description"],
                         "Commander: Kaalia of the Vast")

    def test_description_requires_id_and_public_id(self):
        client = self.make_client()
        with self.assertRaises(MoxfieldWriteError):
            client.update_deck_description({"name": "no ids"}, "desc")


if __name__ == "__main__":
    unittest.main()
