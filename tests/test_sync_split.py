"""
Explicit Update / Push deck sync (docs/SYNC_UPDATE_PUSH.md).

- Update  = external -> commander.lab   (deck_sync.update_deck, POST .../sync/pull)
- Push    = commander.lab -> external   (deck_sync.push_deck,   POST .../sync/push)

Provider HTTP is never hit: provider modules are swapped for fakes, and the
provider writers are tested against a stubbed `requests`. TestNoAutomaticSync
guards that nothing (startup, page loads, GETs, linking, background workers)
syncs a deck without an explicit user action.

Run: python -m unittest tests/test_sync_split.py
"""
import ast
import json
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import deck_sync  # noqa: E402
import linked_accounts  # noqa: E402
import providers  # noqa: E402
from deck_sync import DeckNotFound, DeckSyncError, ProviderFailure, PushUnsupported  # noqa: E402
from providers import archidekt, moxfield  # noqa: E402
from providers.errors import PushError  # noqa: E402

class FakeRedis:
    """In-repo stand-in (CI has no fakeredis package). Dict-backed for the
    calls the sync path makes; anything else is a harmless no-op so page
    loads in the no-auto-sync test can run."""

    def __init__(self):
        self.store, self.sets = {}, {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, *a, **kw):
        self.store[key] = value

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def exists(self, key):
        return int(key in self.store)

    def keys(self, pattern="*"):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def scan_iter(self, match="*", **kw):
        return iter(self.keys(match))

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def mget(self, keys, *more):
        return [self.store.get(k) for k in (list(keys) + list(more))]

    def __getattr__(self, name):
        return lambda *a, **kw: None


LOCAL_CARDS = [
    {"name": "Atraxa, Praetors' Voice", "quantity": 1},
    {"name": "Sol Ring", "quantity": 1},
    {"name": "Island", "quantity": 10},
    {"name": "Cancel", "quantity": 1},
]


def _seed(r, links=None):
    deck = {
        "id": 42, "name": "Atraxa", "registry_id": "archidekt:42", "source": "archidekt",
        "source_id": "42", "status": "testing", "commanders": ["Atraxa, Praetors' Voice"],
        "cards": [dict(c) for c in LOCAL_CARDS],
        "links": links if links is not None else {
            "archidekt": {"identifier": "42", "deck_id": "42"},
            "moxfield": {"identifier": "abc123", "deck_id": "abc123"},
        },
    }
    r.set("decks", json.dumps([deck]))
    return deck


def _remote(source, cards):
    return {"source": source, "source_id": "x", "name": "Atraxa", "color": "WUBG",
            "commanders": ["Atraxa, Praetors' Voice"], "commander_uids": [], "folder": "Imported",
            "description": "", "cards": cards, "url": f"https://{source}.example/deck"}


REMOTE_CARDS = [
    {"name": "Atraxa, Praetors' Voice", "quantity": 1},
    {"name": "Sol Ring", "quantity": 1},
    {"name": "Island", "quantity": 9},
    {"name": "Rhystic Study", "quantity": 1},
]


class FakeProvider:
    """Stands in for providers.archidekt / providers.moxfield."""

    def __init__(self, deck=None, error=None, push_error=None, configured=True):
        self.deck, self.error, self.push_error, self.configured = deck, error, push_error, configured
        self.fetch_calls, self.push_calls = [], []

    def fetch_deck(self, identifier):
        self.fetch_calls.append(identifier)
        if self.error:
            raise self.error
        return self.deck

    def push_configured(self):
        return self.configured

    def push_deck(self, identifier, cards, commanders=None):
        self.push_calls.append((identifier, cards, commanders))
        if self.push_error:
            raise self.push_error
        return {"url": "https://remote.example/deck", "operations": 3}


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed(self.r)
        self.arch = FakeProvider(_remote("archidekt", REMOTE_CARDS))
        self.mox = FakeProvider(_remote("moxfield", REMOTE_CARDS))
        p = patch.dict(providers.PROVIDERS, {"archidekt": self.arch, "moxfield": self.mox})
        p.start()
        self.addCleanup(p.stop)

    def deck(self):
        return json.loads(self.r.get("decks"))[0]


class TestUpdate(ServiceCase):
    def test_update_from_one_provider_overwrites_local_and_reports_changes(self):
        res = deck_sync.update_deck(self.r, "archidekt:42", provider="archidekt")

        self.assertTrue(res["success"])
        self.assertEqual((res["operation"], res["direction"]), ("update", "external_to_local"))
        self.assertEqual(res["providers"], ["archidekt"])
        self.assertEqual(self.mox.fetch_calls, [])  # only the chosen provider is read
        self.assertEqual(res["changes"]["added"], [{"name": "Rhystic Study", "quantity": 1}])
        self.assertEqual(res["changes"]["removed"], [{"name": "Cancel", "quantity": 1}])
        self.assertEqual(res["changes"]["changed"], [{"name": "Island", "from": 10, "to": 9}])
        self.assertEqual(res["changes"]["counts"], {"added": 1, "removed": 1, "changed": 1})
        deck = self.deck()
        self.assertEqual({c["name"]: c["quantity"] for c in deck["cards"]},
                         {c["name"]: c["quantity"] for c in REMOTE_CARDS})
        self.assertEqual(deck["last_updated_at"], res["timestamp"])
        self.assertEqual(deck["sync_state"]["archidekt"]["last_updated_at"], res["timestamp"])
        self.assertNotIn("moxfield", deck["sync_state"])
        self.assertEqual(self.arch.push_calls, [])  # Update never writes externally

    def test_update_from_moxfield(self):
        res = deck_sync.update_deck(self.r, "archidekt:42", provider="moxfield")
        self.assertEqual(res["providers"], ["moxfield"])
        self.assertEqual(self.arch.fetch_calls, [])
        self.assertIn("moxfield", self.deck()["sync_state"])

    def test_update_all_links_merges(self):
        self.mox.deck = _remote("moxfield", REMOTE_CARDS + [{"name": "Smothering Tithe", "quantity": 1}])
        res = deck_sync.update_deck(self.r, "archidekt:42")
        self.assertEqual(sorted(res["providers"]), ["archidekt", "moxfield"])
        self.assertIn("Smothering Tithe", {c["name"] for c in self.deck()["cards"]})

    def test_dry_run_writes_nothing(self):
        before = self.r.get("decks")
        res = deck_sync.update_deck(self.r, "archidekt:42", provider="archidekt", dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["changes"]["counts"]["added"], 1)
        self.assertEqual(self.r.get("decks"), before)

    def test_provider_failure_leaves_deck_untouched(self):
        self.arch.error = RuntimeError("archidekt down")
        before = self.r.get("decks")
        with self.assertRaises(ProviderFailure):
            deck_sync.update_deck(self.r, "archidekt:42", provider="archidekt")
        self.assertEqual(self.r.get("decks"), before)

    def test_unlinked_and_unknown(self):
        _seed(self.r, links={"archidekt": {"identifier": "42", "deck_id": "42"}})
        with self.assertRaises(DeckSyncError):
            deck_sync.update_deck(self.r, "archidekt:42", provider="moxfield")
        with self.assertRaises(DeckSyncError):
            deck_sync.update_deck(self.r, "archidekt:42", provider="commandersalt")
        with self.assertRaises(DeckNotFound):
            deck_sync.update_deck(self.r, "nope:1")


class TestPush(ServiceCase):
    def test_push_sends_local_cards_and_records_timestamp(self):
        for name, prov in (("archidekt", self.arch), ("moxfield", self.mox)):
            res = deck_sync.push_deck(self.r, "archidekt:42", name)
            self.assertTrue(res["success"])
            self.assertEqual((res["operation"], res["direction"]), ("push", "local_to_external"))
            ident, cards, commanders = prov.push_calls[-1]
            self.assertEqual(cards, LOCAL_CARDS)
            self.assertEqual(commanders, ["Atraxa, Praetors' Voice"])
            # changes describe the remote: remote before -> commander.lab
            self.assertEqual(res["changes"]["added"], [{"name": "Cancel", "quantity": 1}])
            self.assertEqual(res["changes"]["removed"], [{"name": "Rhystic Study", "quantity": 1}])
            deck = self.deck()
            self.assertEqual(deck["sync_state"][name]["last_pushed_at"], res["timestamp"])
            self.assertEqual(deck["cards"], LOCAL_CARDS)  # Push never changes the local deck
        self.assertEqual(self.arch.push_calls[0][0], "42")
        self.assertEqual(self.mox.push_calls[0][0], "abc123")

    def test_push_not_configured_is_explicit_error(self):
        self.mox.configured = False
        with self.assertRaises(PushUnsupported) as ctx:
            deck_sync.push_deck(self.r, "archidekt:42", "moxfield")
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("push_not_configured", 501))
        self.assertEqual(self.mox.push_calls, [])
        self.assertNotIn("last_pushed_at", self.deck())

    def test_push_dry_run_needs_no_credentials_and_writes_nothing(self):
        self.arch.configured = False
        res = deck_sync.push_deck(self.r, "archidekt:42", "archidekt", dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["changes"]["counts"], {"added": 1, "removed": 1, "changed": 1})
        self.assertEqual(self.arch.push_calls, [])
        self.assertNotIn("last_pushed_at", self.deck())

    def test_push_provider_rejection(self):
        self.arch.push_error = PushError("token expired")
        with self.assertRaises(ProviderFailure):
            deck_sync.push_deck(self.r, "archidekt:42", "archidekt")
        self.assertNotIn("last_pushed_at", self.deck())

    def test_push_requires_provider_and_link(self):
        with self.assertRaises(DeckSyncError):
            deck_sync.push_deck(self.r, "archidekt:42", None)
        with self.assertRaises(DeckSyncError):
            deck_sync.push_deck(self.r, "archidekt:42", "commandersalt")

    def test_unsupported_provider_reports_push_unsupported(self):
        with patch.object(deck_sync, "PUSH_PROVIDERS", {"archidekt"}):
            with self.assertRaises(PushUnsupported) as ctx:
                deck_sync.push_deck(self.r, "archidekt:42", "moxfield")
        self.assertEqual(ctx.exception.code, "push_unsupported")

    def test_status_reports_capabilities_without_network(self):
        self.mox.configured = False
        deck_sync.update_deck(self.r, "archidekt:42", provider="archidekt")
        self.arch.fetch_calls.clear()
        st = deck_sync.sync_status(self.r, "archidekt:42")
        rows = {p["provider"]: p for p in st["providers"]}
        self.assertTrue(rows["archidekt"]["can_push"])
        self.assertFalse(rows["moxfield"]["can_push"])
        self.assertIn("not configured", rows["moxfield"]["push_unavailable_reason"])
        self.assertIsNotNone(rows["archidekt"]["last_updated_at"])
        self.assertEqual(self.arch.fetch_calls + self.mox.fetch_calls, [])


class RouteCase(ServiceCase):
    def setUp(self):
        super().setUp()
        self._orig_r = app_module.r
        app_module.r = self.r
        self.addCleanup(setattr, app_module, "r", self._orig_r)
        self.client = TestClient(app_module.app)


class TestRoutes(RouteCase):
    def test_pull_route(self):
        res = self.client.post("/api/decks/archidekt:42/sync/pull", json={"provider": "archidekt"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual((body["operation"], body["provider"]), ("update", "archidekt"))
        self.assertIn("timestamp", body)

    def test_pull_route_without_body_pulls_all(self):
        res = self.client.post("/api/decks/archidekt:42/sync/pull")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(sorted(res.json()["providers"]), ["archidekt", "moxfield"])

    def test_push_route_and_errors(self):
        ok = self.client.post("/api/decks/archidekt:42/sync/push", json={"provider": "moxfield"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["direction"], "local_to_external")

        self.arch.configured = False
        res = self.client.post("/api/decks/archidekt:42/sync/push", json={"provider": "archidekt"})
        self.assertEqual(res.status_code, 501)
        self.assertEqual(res.json()["code"], "push_not_configured")
        self.assertFalse(res.json()["success"])

        self.assertEqual(self.client.post("/api/decks/archidekt:42/sync/push", json={}).status_code, 400)
        missing = self.client.post("/api/decks/nope:1/sync/push", json={"provider": "archidekt"})
        self.assertEqual((missing.status_code, missing.json()["code"]), (404, "deck_not_found"))

        self.mox.error = RuntimeError("moxfield down")
        bad = self.client.post("/api/decks/archidekt:42/sync/push", json={"provider": "moxfield"})
        self.assertEqual((bad.status_code, bad.json()["code"]), (502, "provider_error"))

    def test_status_route(self):
        res = self.client.get("/api/decks/archidekt:42/sync/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual({p["provider"] for p in res.json()["providers"]}, {"archidekt", "moxfield"})


class TestNoAutomaticSync(RouteCase):
    """No code path syncs a deck without an explicit user action."""

    SYNC_FUNCS = {"update_deck", "push_deck", "sync_deck", "sync_account", "sync_all"}

    def test_startup_pages_and_reads_never_sync(self):
        spies = {
            "update": patch.object(deck_sync, "update_deck", wraps=deck_sync.update_deck),
            "push": patch.object(deck_sync, "push_deck", wraps=deck_sync.push_deck),
            "acct": patch.object(linked_accounts, "sync_account", wraps=linked_accounts.sync_account),
            "all": patch.object(linked_accounts, "sync_all", wraps=linked_accounts.sync_all),
        }
        mocks = {k: p.start() for k, p in spies.items()}
        for p in spies.values():
            self.addCleanup(p.stop)

        with TestClient(app_module.app, raise_server_exceptions=False) as client:  # runs startup handlers
            for url in ("/", "/decks", "/settings", "/deck/Atraxa", "/api/decks",
                        "/api/decks/archidekt:42/links", "/api/decks/archidekt:42/sync/status",
                        "/api/linked-accounts"):
                client.get(url)

        for name, m in mocks.items():
            self.assertFalse(m.called, f"{name} ran without a user action")
        self.assertEqual(self.arch.fetch_calls + self.mox.fetch_calls, [])
        self.assertEqual(self.arch.push_calls + self.mox.push_calls, [])

    def test_linking_an_account_does_not_sync(self):
        with patch.object(linked_accounts, "add_account", return_value={"id": "archidekt:foo"}), \
             patch.object(linked_accounts, "sync_account") as sync_account:
            res = self.client.post("/api/linked-accounts", json={"provider": "archidekt", "username": "foo"})
        self.assertEqual(res.status_code, 200)
        sync_account.assert_not_called()

    def test_saving_a_deck_link_does_not_sync(self):
        res = self.client.post("/api/decks/archidekt:42/links", json={"provider": "moxfield", "identifier": "zzz"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.arch.fetch_calls + self.mox.fetch_calls, [])

    def test_sync_functions_only_called_from_post_routes(self):
        """Static guard: every call to a sync entry point outside the sync
        modules themselves must sit inside an @app.post route handler -- no
        scheduler, thread, startup hook, worker, or GET may call one."""
        allowed_modules = {"deck_sync.py", "linked_accounts.py"}
        offenders = []
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if rel.parts[0] in {"tests", ".venv", "venv", "docs"} or ".worktrees" in rel.parts \
                    or str(rel) in allowed_modules or rel.parts[0] == "scripts":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                is_post_route = any(
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "post"
                    for d in fn.decorator_list
                )
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call):
                        name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                        target = call.func.value.id if isinstance(call.func, ast.Attribute) \
                            and isinstance(call.func.value, ast.Name) else None
                        if name in self.SYNC_FUNCS and target in {"deck_sync", "linked_accounts", None} \
                                and not is_post_route:
                            offenders.append(f"{rel}:{call.lineno} {fn.name} -> {name}")
            # module-level calls (import-time side effects)
            for node in tree.body:
                for call in ast.walk(node) if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else []:
                    if isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", None)) in self.SYNC_FUNCS:
                        offenders.append(f"{rel}:{call.lineno} <module> -> sync call")
        self.assertEqual(offenders, [])

    def test_background_workers_do_not_import_sync(self):
        for rel in ("analytics/bracket_worker.py", "analytics/interactions/service.py", "github_sync.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for mod in ("deck_sync", "linked_accounts", "providers"):
                self.assertNotIn(f"import {mod}", src, f"{rel} imports {mod}")
                self.assertNotIn(f"from {mod} ", src, f"{rel} imports from {mod}")


class TestProviderWriters(unittest.TestCase):
    """The real provider writers, with `requests` stubbed."""

    def test_archidekt_plan_ops(self):
        remote = [
            {"id": 1, "quantity": 10, "categories": [], "card": {"id": 100, "oracleCard": {"name": "Island"}}},
            {"id": 2, "quantity": 1, "categories": [], "card": {"id": 200, "oracleCard": {"name": "Rhystic Study"}}},
            {"id": 3, "quantity": 1, "categories": ["Maybeboard"], "card": {"id": 300, "oracleCard": {"name": "Cancel"}}},
            {"id": 4, "quantity": 1, "categories": ["Commander"], "card": {"id": 400, "oracleCard": {"name": "Atraxa, Praetors' Voice"}}},
        ]
        desired = {"Island": 9, "Atraxa, Praetors' Voice": 1, "Sol Ring": 1, "Mystery Card": 1}
        ops, unresolved = archidekt.build_modify_ops(
            remote, desired, ["Atraxa, Praetors' Voice"], lambda n: 500 if n == "Sol Ring" else None)
        by = {(o["action"], o["cardid"]): o for o in ops}
        self.assertEqual(by[("modify", 100)]["modifications"], {"quantity": 9})
        self.assertEqual(by[("modify", 100)]["deckRelationId"], 1)
        self.assertIn(("remove", 200), by)
        self.assertIn(("add", 500), by)
        self.assertNotIn(300, {o["cardid"] for o in ops})  # maybeboard untouched
        self.assertNotIn(400, {o["cardid"] for o in ops})  # unchanged commander untouched
        self.assertEqual(unresolved, ["Mystery Card"])

    def test_archidekt_push_requires_token(self):
        with patch.dict(os.environ, {"ARCHIDEKT_TOKEN": ""}), patch.object(archidekt, "requests") as req:
            with self.assertRaises(providers.PushNotConfigured):
                archidekt.push_deck("42", LOCAL_CARDS)
            req.get.assert_not_called()

    def test_archidekt_push_aborts_before_write_on_unresolved(self):
        deck_resp = MagicMock(json=lambda: {"cards": []})
        with patch.dict(os.environ, {"ARCHIDEKT_TOKEN": "t"}), patch.object(archidekt, "requests") as req, \
             patch.object(archidekt, "lookup_card_id", return_value=None):
            req.get.return_value = deck_resp
            with self.assertRaises(PushError):
                archidekt.push_deck("42", [{"name": "Nonexistent", "quantity": 1}])
            req.patch.assert_not_called()

    def test_archidekt_push_patches_with_jwt(self):
        with patch.dict(os.environ, {"ARCHIDEKT_TOKEN": "tok"}), patch.object(archidekt, "requests") as req, \
             patch.object(archidekt, "lookup_card_id", return_value=7):
            req.get.return_value = MagicMock(json=lambda: {"cards": []})
            req.patch.return_value = MagicMock(status_code=200, ok=True)
            out = archidekt.push_deck("42", [{"name": "Sol Ring", "quantity": 1}])
        url = req.patch.call_args[0][0]
        kwargs = req.patch.call_args[1]
        self.assertEqual(url, "https://archidekt.com/api/decks/42/modifyCards/")
        self.assertEqual(kwargs["headers"]["Authorization"], "JWT tok")
        self.assertEqual(kwargs["json"]["cards"][0]["action"], "add")
        self.assertEqual(out["operations"], 1)

    def test_moxfield_push_requires_token(self):
        with patch.dict(os.environ, {"MOXFIELD_TOKEN": ""}), patch.object(moxfield, "requests") as req:
            with self.assertRaises(providers.PushNotConfigured):
                moxfield.push_deck("abc", LOCAL_CARDS)
            req.get.assert_not_called()

    def test_moxfield_push_imports_and_preserves_side_boards(self):
        remote = {"id": "MBV4O6", "version": 56, "publicUrl": "https://moxfield.com/decks/abc",
                  "boards": {"maybeboard": {"cards": {"k": {"quantity": 1, "card": {"name": "Cancel"}}}}}}
        with patch.dict(os.environ, {"MOXFIELD_TOKEN": "tok"}), patch.object(moxfield, "requests") as req:
            req.get.return_value = MagicMock(json=lambda: remote)
            req.post.return_value = MagicMock(status_code=200, ok=True, content=b"{}",
                                              json=lambda: {"isSuccessful": True})
            moxfield.push_deck("abc", LOCAL_CARDS, ["Atraxa, Praetors' Voice"])
        url = req.post.call_args[0][0]
        kwargs = req.post.call_args[1]
        self.assertEqual(url, "https://api2.moxfield.com/v3/decks/MBV4O6/import")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["headers"]["X-Deck-Version"], "56")
        self.assertTrue(kwargs["json"]["replaceAll"])
        text = kwargs["json"]["importText"]
        self.assertIn("1 Atraxa, Praetors' Voice *CMDR*", text)
        self.assertIn("10 Island", text)
        self.assertIn("Maybeboard\n1 Cancel", text)

    def test_moxfield_push_401_is_reported(self):
        with patch.dict(os.environ, {"MOXFIELD_TOKEN": "tok"}), patch.object(moxfield, "requests") as req:
            req.get.return_value = MagicMock(json=lambda: {"id": "X", "version": 1, "boards": {}})
            req.post.return_value = MagicMock(status_code=401, ok=False)
            with self.assertRaises(PushError):
                moxfield.push_deck("abc", LOCAL_CARDS)


if __name__ == "__main__":
    unittest.main()
