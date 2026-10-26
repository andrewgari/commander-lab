"""Unit tests for deck_export + provider create_deck (no network)."""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.deck_export as deck_export
from src.server.domain.decks.deck_export import ExportError
import src.server.infra.providers as providers
from src.server.infra.providers import PushError, PushNotConfigured, moxfield, archidekt


def _resp(status=200, body=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.json.return_value = body if body is not None else {}
    m.text = json.dumps(body or {})
    return m


class TestNormalize(unittest.TestCase):
    def test_merge_and_commander_added(self):
        d = deck_export.normalize_decklist(" X ", [{"name": "Sol Ring"}, {"name": "Sol Ring", "quantity": 1},
                                                 {"name": "Forest", "quantity": 0}], ["Atraxa"])
        q = {c["name"]: c["quantity"] for c in d["cards"]}
        self.assertEqual(q, {"Sol Ring": 2, "Atraxa": 1})
        self.assertEqual(d["name"], "X")
        self.assertEqual(d["commanders"], ["Atraxa"])

    def test_invalid(self):
        for args in [("", [{"name": "a"}]), ("n", []), ("n", [{"name": "a", "quantity": "x"}]),
                     ("n", [{"name": "a", "quantity": 100}]), ("n", [{"name": "a", "quantity": -1}])]:
            with self.assertRaises(ExportError):
                deck_export.normalize_decklist(*args)


class TestExportDecklist(unittest.TestCase):
    cards = [{"name": "Sol Ring", "quantity": 1}]

    def test_unknown_provider(self):
        with self.assertRaises(ExportError):
            deck_export.export_decklist("n", self.cards, providers_requested=["foo"])
        with self.assertRaises(ExportError):
            deck_export.export_decklist("n", self.cards, providers_requested=[])

    def test_dry_run_no_network(self):
        with patch.object(providers, "create_deck") as cd, patch.object(providers, "push_configured", return_value=True):
            out = deck_export.export_decklist("n", self.cards, dry_run=True)
        cd.assert_not_called()
        self.assertTrue(out["success"])
        self.assertEqual(out["results"]["moxfield"]["status"], "dry_run")

    def test_isolated_failures(self):
        def fake(provider, *a, **k):
            if provider == "moxfield":
                raise PushError("boom")
            return {"url": "https://archidekt.com/decks/1", "source_id": "1"}
        with patch.object(providers, "create_deck", side_effect=fake), \
             patch.object(providers, "push_configured", return_value=True):
            out = deck_export.export_decklist("n", self.cards)
        self.assertFalse(out["success"])
        self.assertEqual(out["results"]["moxfield"]["status"], "failed")
        self.assertEqual(out["results"]["archidekt"]["status"], "created")

    def test_not_configured(self):
        with patch.object(providers, "push_configured", return_value=False):
            out = deck_export.export_decklist("n", self.cards, providers_requested=["archidekt"])
        self.assertEqual(out["results"]["archidekt"]["status"], "not_configured")
        self.assertFalse(out["success"])

    def test_unexpected_exception_contained(self):
        with patch.object(providers, "create_deck", side_effect=RuntimeError("net")), \
             patch.object(providers, "push_configured", return_value=True):
            out = deck_export.export_decklist("n", self.cards, providers_requested=["archidekt"])
        self.assertEqual(out["results"]["archidekt"]["status"], "failed")


class TestExportDraft(unittest.TestCase):
    def test_draft(self):
        drafts = [{"name": "D", "cards": [{"name": "Sol Ring", "quantity": 1}], "commanders": ["Atraxa"]}]
        with patch("src.server.domain.draft.reconstruct.get_drafts", return_value=drafts):
            out = deck_export.export_draft(None, "s", 0, dry_run=True)
            self.assertEqual(out["deck"]["name"], "D")
            with self.assertRaises(ExportError):
                deck_export.export_draft(None, "s", 5)
        with patch("src.server.domain.draft.reconstruct.get_drafts", side_effect=ValueError("session not found")):
            with self.assertRaises(ExportError):
                deck_export.export_draft(None, "s", 0)


class TestProviderCreate(unittest.TestCase):
    def test_dispatcher_unknown(self):
        with self.assertRaises(providers.ProviderError):
            providers.create_deck("nope", "n", [])

    def test_moxfield_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PushNotConfigured):
                moxfield.create_deck("n", [])

    def test_moxfield_success_and_fill_failure(self):
        env = {moxfield.PUSH_TOKEN_ENV: "t"}
        with patch.dict(os.environ, env), patch.object(moxfield.requests, "post", return_value=_resp(200, {"publicId": "abc"})), \
             patch.object(moxfield, "push_deck", return_value={}) as pd:
            out = moxfield.create_deck("n", [{"name": "a", "quantity": 1}])
        self.assertEqual(out["source_id"], "abc")
        pd.assert_called_once()
        with patch.dict(os.environ, env), patch.object(moxfield.requests, "post", return_value=_resp(200, {"publicId": "abc"})), \
             patch.object(moxfield, "push_deck", side_effect=PushError("x")):
            with self.assertRaises(PushError) as cm:
                moxfield.create_deck("n", [])
        self.assertIn("moxfield.com/decks/abc", str(cm.exception))

    def test_moxfield_http_errors(self):
        with patch.dict(os.environ, {moxfield.PUSH_TOKEN_ENV: "t"}):
            for status in (401, 500):
                with patch.object(moxfield.requests, "post", return_value=_resp(status)):
                    with self.assertRaises(PushError):
                        moxfield.create_deck("n", [])
            with patch.object(moxfield.requests, "post", return_value=_resp(200, {})):
                with self.assertRaises(PushError):
                    moxfield.create_deck("n", [])

    def test_archidekt_create_is_import_only(self):
        # Slice 1 contract: archidekt provider is import-only; deck creation deleted.
        self.assertFalse(hasattr(archidekt, "create_deck"))
        with self.assertRaises(PushNotConfigured):
            providers.create_deck("archidekt", "n", [])


if __name__ == "__main__":
    unittest.main()
