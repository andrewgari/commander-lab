"""AN-5: Journal tab + review check-in UI (UX_SPEC.md §3.3, §4, §5).

Structure and behaviour only, same rules as the AN-4 page tests:
- /deck/{name}/journal renders the shared deck header with the Journal tab
  (key 4) active; Tune (key 5) is still hidden until AN-6 ships it;
- the form is server-rendered from the kit macros (hud-form / hud-field /
  hud-seg / hud-money / hud-picker / hud-savebar), pre-filled from the
  stored journal, with the schema's enums;
- page CSS is layout-only and every hud-* class is defined in hud.css;
- the Overview "Review" button goes to /journal?review=1 (D4);
- the check-in view-model helpers in deck_analytics.js run in node.
"""
import json
import redis
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.server.app.main as app_mod
from src.server.domain.analytics import journal
from src.server.app.main import app
from tests.test_deck_stats_ui import DECK, DECK_JS, HUD_CSS, HUD_JS, INVENTORY, _DeckPage, _classes, _page_style
from tests.test_interactions import FakeRedis

SAVED = {
    "deck_vision": {"intent": "overhaul", "description": "Kaalia <b>cheats</b> fatties.", "target_turn_win": 8},
    "target_power_level": {"scale": 8},
    "budget_constraints": {"total_budget": 500, "hard_cap": 40},
    "preferred_win_conditions": {"primary": "burn", "secondary": ["commander_damage"], "disliked_or_excluded": ["combo"]},
    "pet_cards": ["Kaalia of the Vast"],
    "novelties": "Swarm once.",
}


class _JournalPage(_DeckPage):
    PATH = "/deck/Holy%20Hellfire/journal"
    ACTIVE = "Journal"
    SAVED = SAVED

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeRedis()
        if cls.SAVED is not None:
            journal.save_journal(cls.fake, DECK, cls.SAVED)
        with patch.object(app_mod, "r", cls.fake):
            super().setUpClass()

    def test_tabs_have_keys_and_mark_the_active_tab(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a class="hud-tab[^"]*" href="([^"]+)"[^>]*data-key="(\d)"', nav)
        self.assertEqual([k for _, k in tabs], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
        self.assertEqual(tabs[6][0], "/deck/Holy%20Hellfire/journal")
        active = re.findall(r'<a class="hud-tab active"[^>]*aria-current="page"[^>]*><span class="hud-kbd"[^>]*>[^<]*</span>([^<]+)', nav)
        self.assertEqual([a.strip() for a in active], [self.ACTIVE])
        self.assertEqual(tabs[2][0], "/deck/Holy%20Hellfire/tune")  # AN-6


class TestJournalPage(_JournalPage, unittest.TestCase):
    def test_form_structure(self):
        for cls_ in ["hud-form", "hud-form-section", "hud-field", "hud-field-row", "hud-seg", "hud-seg--multi",
                     "hud-seg--exclude", "hud-seg--scale", "hud-scale-tiers", "hud-money", "hud-picker",
                     "hud-toggle", "hud-savebar", "hud-checkin-head", "hud-checklist", "hud-picker-list"]:
            with self.subTest(cls=cls_):
                self.assertIn(cls_, _classes(self.html))

    def test_sections_and_anchor_nav_in_spec_order(self):
        ids = ["j-vision", "j-genre", "j-archetype", "j-win", "j-power", "j-budget", "j-pets", "j-pillars"]  # GN: Genre under Vision
        idx = [self.body.find(f'<section class="hud-form-section" id="{i}"') for i in ids]
        self.assertTrue(all(i > 0 for i in idx), idx)
        self.assertEqual(idx, sorted(idx))
        nav = self.body.split('aria-label="Journal sections"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(re.findall(r'href="#([^"]+)"', nav), ids)

    def test_enums_come_from_the_schema(self):
        for v in ["new_build", "tune_up", "overhaul"]:
            self.assertRegex(self.body, rf'name="intent" value="{v}"')
        for v in ["combat", "combo", "attrition", "commander_damage", "alternate_wincon", "burn", "lockout", "mill"]:
            with self.subTest(v=v):
                self.assertRegex(self.body, rf'type="radio" name="wc_primary" value="{v}"')
                self.assertRegex(self.body, rf'type="checkbox" name="wc_secondary" value="{v}"')
                self.assertRegex(self.body, rf'type="checkbox" name="wc_excluded" value="{v}"')
        self.assertEqual(len(re.findall(r'type="radio" name="power" value="\d+"', self.body)), 10)

    def test_prefilled_from_the_stored_journal(self):
        self.assertRegex(self.body, r'name="intent" value="overhaul"[^>]* checked')
        self.assertRegex(self.body, r'name="wc_primary" value="burn"[^>]* checked')
        self.assertRegex(self.body, r'name="wc_excluded" value="combo"[^>]* checked')
        self.assertRegex(self.body, r'name="power" value="8"[^>]* checked')
        self.assertIn("Kaalia &lt;b&gt;cheats&lt;/b&gt; fatties.", self.body)
        self.assertNotIn("<b>cheats</b>", self.body)
        self.assertRegex(self.body, r'id="hardCap"[^>]* value="40"')
        self.assertIn("Global default $30 · overridden", self.body)
        self.assertIn("Using global default", self.body)  # soft cap not set
        self.assertIn("Owned cards cost $0 against the budget.", self.body)
        # Burn is past the first 4 primaries: its "More…" group starts expanded.
        self.assertRegex(self.body, r'id="wcPrimaryMore" class="hud-seg-rest">')

    def test_pet_picker_is_an_accessible_combobox_over_this_deck(self):
        self.assertRegex(self.body, r'<input id="petInput"[^>]*role="combobox"[^>]*aria-controls="petList"')
        self.assertRegex(self.body, r'<ul class="hud-picker-list" id="petList" role="listbox"')
        self.assertIn('"Plains"', self.body)  # the deck's card list is embedded for autocomplete
        self.assertIn("hud-chip-remove", self.body)
        self.assertIn("Autocomplete from this deck's list only.", self.body)

    def test_pillars_link(self):
        self.assertIn('href="/deck/Holy%20Hellfire/identity"', self.body)

    def test_uses_the_journal_and_checkin_endpoints(self):
        for frag in ['"/journal"', '"/checkin"', '"/checkin/rows/"', '"/checkin/finish"', "PUT", "PATCH",
                     "beforeunload", "aria-pressed"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.body)

    def test_keys(self):
        for key in ['e.key === "j"', 'e.key === "k"', 'e.key === "y"', 'e.key === "c"', '"Enter"']:
            with self.subTest(key=key):
                self.assertIn(key, self.body)
        self.assertIn("inTextField", self.body)
        self.assertRegex(self.body, r'class="hud-progress"[^>]*aria-live="polite"')

    def test_rows_without_a_form_section_get_no_change_button(self):
        # A check-in row with no form section must not offer an inline Change
        # (BR-2's bracket row until BR-6 added the picker; any future row too).
        self.assertIn("!ROWS[r.field] ?", self.body)

    def test_review_button_in_header(self):
        actions = self.body.split('class="hud-deckhead-actions"', 1)[1].split("</div>", 1)[0]
        self.assertIn("?review=1", actions)

    def test_inline_change_reads_fields_outside_the_form(self):
        # "Change" moves a form section under its check-in row (outside <form>):
        # reads must be document-scoped or the edit is lost (live bug, AN-5).
        script = self.body.split("<script>", 1)[1]
        self.assertNotIn("form.querySelector", script)
        self.assertNotIn("form.addEventListener", script)


class TestJournalPageEmpty(_JournalPage, unittest.TestCase):
    SAVED = None

    def test_defaults_and_first_time_copy(self):
        self.assertRegex(self.body, r'name="intent" value="tune_up"[^>]* checked')
        self.assertRegex(self.body, r'name="power" value="7"[^>]* checked')
        self.assertIn("No journal yet", self.body)
        self.assertEqual(self.body.split("<script>", 1)[0].count("Using global default"), 2)
        self.assertRegex(self.body, r'id="wcPrimaryMore" class="hud-seg-rest" hidden>')


class TestReviewEntryPoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_unknown_deck_404(self):
        with patch("src.server.app.main.registry.list_decks", return_value=[]):
            self.assertEqual(self.client.get("/deck/Nope/journal").status_code, 404)

    def test_overview_review_goes_to_the_checkin(self):
        with patch("src.server.app.main.registry.list_decks", return_value=[DECK]), \
                patch("src.server.app.main.inventory_view.build_inventory", return_value=INVENTORY):
            body = self.client.get("/deck/Holy%20Hellfire").text
        self.assertIn("reviewDeck()", body)
        self.assertIn("/journal?review=1", body)
        self.assertNotIn('id="reviewModal"', body)

    def test_journal_page_survives_redis_down(self):
        class Down(FakeRedis):
            def get(self, key):
                raise redis.exceptions.ConnectionError("down")
        with patch.object(app_mod, "r", Down()), patch("src.server.app.main.registry.list_decks", return_value=[DECK]), \
                patch("src.server.app.main.inventory_view.build_inventory", return_value=INVENTORY):
            self.assertEqual(self.client.get("/deck/Holy%20Hellfire/journal").status_code, 200)


class TestKitDocs(unittest.TestCase):
    def test_picker_list_is_in_the_kit(self):
        css = HUD_CSS.read_text()
        self.assertIn(".hud-picker-list", css)
        self.assertIn('.hud-picker-list [role="option"][aria-selected="true"]', css)


@unittest.skipUnless(shutil.which("node"), "node not installed: deck_analytics.js tests need it")
class TestCheckinHelpers(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [],
                    getElementById: () => null, querySelector: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
eval(fs.readFileSync(process.argv[2], 'utf8'));
const A = window.DeckAnalytics;
const out = {
  tiers: [1, 4, 5, 6, 7, 8, 9, 10].map(A.tierOf),
  since: A.sinceItems({ added: ['A', 'B'], removed: ['C'], power: [6.5, 7.2], price: [412, 468], on_plan: [64, 60] }),
  sinceNone: A.sinceItems({ added: [], removed: [], power: [null, 6.5], price: [null, null], on_plan: [null, 71] }),
  segs: A.progressSegments([{ answer: 'true' }, { answer: 'changed' }, { answer: null }]),
  label: A.progressLabel({ total: 8, answered: 3, changed: 1 }),
  money: [A.money(40), A.money(12.5), A.money(null)],
};
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS), str(DECK_JS)], capture_output=True, text=True,
                             timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_tier_is_derived(self):
        self.assertEqual(self.out["tiers"], ["casual", "casual", "focused", "focused", "optimized", "optimized",
                                             "competitive", "competitive"])

    def test_since_row_ups_ok_downs_signal_never_red(self):
        s = {i["label"]: i for i in self.out["since"]}
        self.assertEqual(list(s), ["Cards", "Power est.", "Price", "On-plan"])
        self.assertIn('<span class="up">+2</span>', s["Cards"]["html"])
        self.assertIn('<span class="down">−1</span>', s["Cards"]["html"])
        self.assertIn('6.5 → <span class="up">7.2</span>', s["Power est."]["html"])
        self.assertIn("$412 → $468", s["Price"]["html"])
        self.assertIn('64% → <span class="down">60%</span>', s["On-plan"]["html"])
        self.assertNotIn("danger", json.dumps(self.out["since"]))

    def test_since_row_without_history(self):
        s = {i["label"]: i["html"] for i in self.out["sinceNone"]}
        self.assertEqual(s["Cards"], "0 / 0")
        self.assertEqual(s["Power est."], "6.5")
        self.assertEqual(s["Price"], "—")
        self.assertEqual(s["On-plan"], "71%")

    def test_progress(self):
        self.assertEqual(self.out["segs"], ["is-done", "is-changed", ""])
        self.assertEqual(self.out["label"], "3 of 8 answered · 1 changed")
        self.assertEqual(self.out["money"], ["$40", "$12.50", "—"])


if __name__ == "__main__":
    unittest.main()
