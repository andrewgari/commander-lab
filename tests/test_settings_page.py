"""/settings page: the Linked Accounts UI lives in its Sync section, not on /decks.

Structure only. The /api/linked-accounts endpoints themselves are covered by
tests/test_linked_accounts_routes.py; the browser flows by
tests/e2e/test_linked_accounts_e2e.py (which now targets /settings).

Run: python -m unittest tests.test_settings_page
"""
import re
import unittest

from fastapi.testclient import TestClient

from src.server.app.main import app


def _nav(html):
    return html.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


def _content(html):
    """Everything inside <main> (page body + page scripts), sans sidebar."""
    return html.split('<main class="main-content" id="main">', 1)[1]


class TestSettingsPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = TestClient(app)
        cls.res = client.get("/settings")
        cls.html = cls.res.text
        cls.decks = client.get("/decks").text

    def test_settings_renders(self):
        self.assertEqual(self.res.status_code, 200)
        self.assertIn("<title>Settings | Commander Lab</title>", self.html)
        self.assertIn('class="hud-page-head"', self.html)

    def test_sync_section_holds_linked_accounts_ui(self):
        body = _content(self.html)
        sync = body.index('id="sync"')
        section = body.index('<section class="linked-accounts-section">')
        self.assertLess(sync, section, "Linked Accounts must sit under the Sync heading")
        self.assertIn("<h2>Sync</h2>", body)
        for needle in [
            "Linked Accounts",
            'id="linkedAccountsList"',
            'id="syncAllBtn"',
            "Sync All",
            "Link Account",
            'id="addAccountForm"',
            'id="newAccountProvider"',
            'id="newAccountUsername"',
            'id="addAccountStatus"',
        ]:
            with self.subTest(needle=needle):
                self.assertIn(needle, body)

    def test_settings_js_drives_the_accounts_api(self):
        body = _content(self.html)
        for fn in ["loadLinkedAccounts", "renderLinkedAccounts", "syncAllAccounts", "syncAccount",
                   "removeAccount", "toggleAddAccountForm", "submitAddAccount", "showRemoveConfirm"]:
            with self.subTest(fn=fn):
                self.assertRegex(body, r"function " + fn + r"\(")
        for url in ["'/api/linked-accounts'", "'/api/linked-accounts/sync-all'",
                    "`/api/linked-accounts/${encodeURIComponent(accountId)}/sync`",
                    "`/api/linked-accounts/${encodeURIComponent(accountId)}`"]:
            with self.subTest(url=url):
                self.assertIn(url, body)
        # The page boots the list itself, and doesn't call into the deck-list code it left behind.
        self.assertRegex(body, r"\n\s*loadLinkedAccounts\(\);\s*</script>")
        self.assertNotIn("loadDecks", body)

    def test_settings_styles_the_moved_ui(self):
        head = self.html.split("</head>", 1)[0]
        for cls in ["linked-accounts-section", "account-card", "add-account-form", "remove-confirm", "btn-icon", "btn"]:
            with self.subTest(cls=cls):
                self.assertRegex(head, r"\." + re.escape(cls) + r"\s*\{")


class TestDecksPageWithoutAccounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TestClient(app).get("/decks").text

    def test_linked_accounts_panel_removed(self):
        for needle in ["linked-accounts-section", "linkedAccountsList", "addAccountForm",
                       "syncAllBtn", "loadLinkedAccounts", "/api/linked-accounts"]:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.html)

    def test_per_deck_sync_controls_stay(self):
        for needle in ["Sync Sources", 'id="modalArchidektUrl"', 'id="modalMoxfieldUrl"',
                       "saveDeckLink('archidekt')", 'id="modalSyncLink"', "Import Deck"]:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # Startup load still runs (after deferred hud.js, which renderDecks needs).
        self.assertRegex(self.html, r"\n\s*document\.addEventListener\('DOMContentLoaded', loadDecks\);\s*</script>")


class TestSettingsNav(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_settings_nav_item_on_every_page(self):
        for path in ["/", "/decks", "/inventory", "/settings"]:
            with self.subTest(path=path):
                nav = _nav(self.client.get(path).text)
                m = re.search(r'<a href="/settings" class="nav-item[^"]*"[^>]*title="Settings"[^>]*>(.*?)</a>', nav, re.S)
                self.assertIsNotNone(m)
                # icon-only rail at <=900px relies on the svg + title attribute
                self.assertIn('class="nav-icon"', m.group(1))
                self.assertIn('<span class="nav-label">Settings</span>', m.group(1))

    def test_settings_is_active_only_on_settings(self):
        nav = _nav(self.client.get("/settings").text)
        self.assertRegex(nav, r'<a href="/settings" class="nav-item active" title="Settings" aria-current="page">')
        nav = _nav(self.client.get("/decks").text)
        self.assertNotRegex(nav, r'<a href="/settings" class="nav-item active"')


if __name__ == "__main__":
    unittest.main()
