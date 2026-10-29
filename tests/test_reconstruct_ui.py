"""
Unit and template-level tests for Reconstruct UI screens and end-to-end backend wiring.
"""
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.server.app.main import app
import src.server.domain.draft.reconstruct as reconstruct
import src.server.domain.decks.registry as registry
import src.server.domain.inventory.instances as instance_store


class TestDeckPageReconstructEntry(unittest.TestCase):
    """Checks for Reconstruct entry point on templates/deck.html."""

    def setUp(self):
        self.client = TestClient(app)
        self.html = self.client.get("/deck/Lathril%20Elves").text

    def test_reconstruct_button_present_on_deck_page(self):
        self.assertIn('id="reconstructBtn"', self.html)
        self.assertIn("Reconstruct", self.html)
        btn_match = re.search(r'<button[^>]*id="reconstructBtn"[^>]*>', self.html)
        self.assertIsNotNone(btn_match)
        assert btn_match is not None
        self.assertIn("hidden", btn_match.group(0))

    def test_reconstruct_modal_and_dialog_elements(self):
        self.assertIn('id="reconstructModal"', self.html)
        self.assertIn('id="reconstructTitle"', self.html)
        self.assertIn('id="reconstructDeckName"', self.html)
        self.assertIn('id="reconstructSecondDeckSelect"', self.html)
        self.assertIn('id="reconstructConfirmBtn"', self.html)
        self.assertIn('id="reconstructCancelBtn"', self.html)
        self.assertIn('id="reconstructError"', self.html)

    def test_decommission_warning_and_card_pooling_text(self):
        self.assertIn("Warning", self.html)
        self.assertIn("retired", self.html)
        self.assertIn("cards will be returned to your inventory", self.html)

    def test_link_between_decommission_and_reconstruct(self):
        self.assertIn('id="decommissionToReconstructLink"', self.html)
        self.assertIn("openReconstructDialog()", self.html)

    def test_js_handlers_and_api_call(self):
        self.assertIn("function openReconstructDialog()", self.html)
        self.assertIn("function closeReconstructDialog()", self.html)
        self.assertIn("function confirmReconstruct()", self.html)
        self.assertIn("/api/reconstruct/start", self.html)
        self.assertIn("/reconstruct/", self.html)


class TestReconstructDraftNotices(unittest.TestCase):
    """New draft fields (is_complete / unpriced_cards) and COMMANDER_MISMATCH surface in the UI."""

    def setUp(self):
        self.html = TestClient(app).get("/reconstruct/recon_abc").text

    def test_incomplete_pricing_notice_hooks(self):
        self.assertIn("function draftCompletenessNotice", self.html)
        self.assertIn("draft.is_complete", self.html)
        self.assertIn("draft.unpriced_cards", self.html)
        self.assertIn("draft-incomplete-notice", self.html)
        self.assertIn("${draftCompletenessNotice(draft)}", self.html)

    def test_commander_mismatch_and_failed_state_hooks(self):
        self.assertIn("COMMANDER_MISMATCH", self.html)
        self.assertIn("function describeBuildError", self.html)
        self.assertIn("currentSession.status === 'failed'", self.html)

    def test_helpers_behave_in_node(self):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        script = re.findall(r"<script>(.*?)</script>", self.html, re.S)[-1]
        fn = lambda name: re.search(r"function %s\(.*?\n    \}\n" % name, script, re.S).group(0)
        harness = (
            "function esc(v){return String(v);}\n" + fn("describeBuildError") + fn("draftCompletenessNotice") +
            "const a=draftCompletenessNotice({is_complete:true,unpriced_cards:[]});\n"
            "const b=draftCompletenessNotice({is_complete:false,unpriced_cards:[]});\n"
            "const c=draftCompletenessNotice({is_complete:true,unpriced_cards:['Foo','Bar']});\n"
            "const d=describeBuildError('Draft validation failed after 3 retries: [COMMANDER_MISMATCH] x');\n"
            "const e=describeBuildError('boom');\n"
            "console.log(JSON.stringify([a==='',b.includes('lower bound'),c.includes('Foo, Bar')&&c.includes('2 cards'),d.code,e.code,e.text]));"
        )
        res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), [True, True, True, "COMMANDER_MISMATCH", "", "boom"])


class TestReconstructPageTemplate(unittest.TestCase):
    """Checks for templates/reconstruct.html and all 5 Reconstruct screens."""

    def setUp(self):
        self.client = TestClient(app)

    def test_reconstruct_landing_route(self):
        resp = self.client.get("/reconstruct")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text
        self.assertIn('id="screenLanding"', html)
        self.assertIn('id="landingDeck1Select"', html)
        self.assertIn('id="landingDeck2Select"', html)
        self.assertIn('id="btnLandingStart"', html)
        self.assertIn('id="recentSessionsContainer"', html)

    def test_reconstruct_session_route(self):
        resp = self.client.get("/reconstruct/recon_test123")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text
        self.assertIn('recon_test123', html)

    def test_stepper_and_lifecycle_indicators(self):
        html = self.client.get("/reconstruct").text
        self.assertIn('id="reconstructStepper"', html)
        self.assertIn('id="step1"', html)
        self.assertIn('id="step2"', html)
        self.assertIn('id="step3"', html)
        self.assertIn('id="step4"', html)
        self.assertIn('id="step5"', html)

    def test_screen_2_resource_review_pool_elements(self):
        html = self.client.get("/reconstruct").text
        self.assertIn('id="screenPool"', html)
        self.assertIn('id="poolHeaderTitle"', html)
        self.assertIn('id="poolHeaderDecks"', html)
        self.assertIn('id="poolTotalCards"', html)
        self.assertIn('id="poolUniqueCards"', html)
        self.assertIn('id="poolSourceDecksCount"', html)
        self.assertIn('id="poolCombinedColors"', html)
        self.assertIn('id="poolSourceDecksDetail"', html)
        self.assertIn('id="poolCardSearch"', html)
        self.assertIn('id="poolCardsTable"', html)
        self.assertIn('id="btnProceedToPrefs"', html)

    def test_screen_3_preferences_form_elements(self):
        html = self.client.get("/reconstruct").text
        self.assertIn('id="screenPreferences"', html)
        self.assertIn('id="buildTwoDecksToggle"', html)

        # Deck 1 fields
        self.assertIn('id="deckPrefsSection_0"', html)
        self.assertIn('id="pref_name_0"', html)
        self.assertIn('id="pref_cmdr_0"', html)
        self.assertIn('id="suggestedCmdrs_0"', html)
        self.assertIn('id="btnTogglePartner_0"', html)
        self.assertIn('id="partnerContainer_0"', html)
        self.assertIn('id="pref_partner_cmdr_0"', html)
        self.assertIn('id="pref_themes_0"', html)
        self.assertIn('id="pref_strategy_0"', html)
        self.assertIn('id="pref_art_fullart_0"', html)
        self.assertIn('id="pref_art_oldborder_0"', html)
        self.assertIn('id="pref_art_matchframes_0"', html)
        self.assertIn('id="pref_art_showcase_0"', html)
        self.assertIn('id="pref_notes_0"', html)

        # Deck 2 fields
        self.assertIn('id="deckPrefsSection_1"', html)
        self.assertIn('id="pref_name_1"', html)
        self.assertIn('id="pref_cmdr_1"', html)
        self.assertIn('id="partnerContainer_1"', html)
        self.assertIn('id="pref_partner_cmdr_1"', html)
        self.assertIn('id="pref_themes_1"', html)
        self.assertIn('id="pref_strategy_1"', html)
        self.assertIn('id="pref_notes_1"', html)

        # Actions
        self.assertIn('id="btnDraftWithAgent"', html)
        self.assertIn('id="prefsError"', html)

    def test_screen_4_building_progress_elements(self):
        html = self.client.get("/reconstruct").text
        self.assertIn('id="screenBuilding"', html)
        self.assertIn('id="buildingTitle"', html)
        self.assertIn('id="buildingLog_1"', html)
        self.assertIn('id="buildingLog_2"', html)
        self.assertIn('id="buildingLog_3"', html)
        self.assertIn('id="buildingLog_4"', html)
        self.assertIn('id="buildingLog_5"', html)
        self.assertIn('id="buildingPollStatus"', html)
        self.assertIn('id="buildingError"', html)

    def test_screen_5_draft_results_elements(self):
        html = self.client.get("/reconstruct").text
        self.assertIn('id="screenResults"', html)
        self.assertIn('id="draftsListContainer"', html)
        self.assertIn('id="draftsActionBar"', html)
        self.assertIn('id="resultsGrandTotalCost"', html)
        self.assertIn('id="btnTweakPrefs"', html)
        self.assertIn('id="btnRegenerateDraft"', html)
        self.assertIn('id="btnAcceptSave"', html)
        self.assertIn('id="btnAbandonDraft"', html)
        self.assertIn('id="abandonModal"', html)
        self.assertIn('id="acceptModal"', html)

    def test_tier_badge_classes_defined(self):
        html = self.client.get("/reconstruct").text
        self.assertIn("badge-source-pool", html)
        self.assertIn("badge-source-owned", html)
        self.assertIn("badge-source-new", html)

    def test_commander_fields_use_the_shared_autocomplete_component(self):
        # The per-page browser-direct Scryfall implementation (searchCardNames/
        # setupAutocomplete, which swallowed errors into "no matches") is gone;
        # the 4 commander/partner inputs attach the shared component with the
        # retired pool merged in as extraSource.
        html = self.client.get("/reconstruct").text
        self.assertNotIn("searchCardNames", html)
        self.assertNotIn("setupAutocomplete", html)
        self.assertNotIn("api.scryfall.com/cards/autocomplete", html)
        self.assertNotIn('class="autocomplete-dropdown"', html)
        self.assertIn("CardAutocomplete.attach(el", html)
        self.assertIn("extraSource: poolCommanderSource", html)
        for fid in ("pref_cmdr_0", "pref_partner_cmdr_0", "pref_cmdr_1", "pref_partner_cmdr_1"):
            self.assertIn(f"'{fid}'", html)
        self.assertIn("From Pool", html)  # pool badge preserved


    def test_form_validation_and_error_state_hooks(self):
        html = self.client.get("/reconstruct").text
        self.assertIn("function validatePreferenceFields", html)
        self.assertIn("function checkCommanderLegal", html)
        self.assertIn("Invalid commander selected.", html)
        self.assertIn("Draft completely random decks from the pool?", html)
        self.assertIn("Could not generate a legal deck with these preferences", html)
        self.assertIn("function esc(", html)

    def test_inline_script_is_valid_javascript(self):
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        html = self.client.get("/reconstruct/recon_abc").text
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(scripts[-1])
        res = subprocess.run([node, "--check", fh.name], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_validation_logic_runs_in_node(self):
        """Execute validatePreferenceFields against a minimal DOM stub."""
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        html = self.client.get("/reconstruct").text
        script = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
        m = re.search(r"(function validatePreferenceFields\(count\) \{.*?\n    \}\n)", script, re.S)
        self.assertIsNotNone(m)
        harness = """
const vals = {pref_name_0:'', pref_notes_0:'', pref_cmdr_0:'Tymna the Weaver', pref_partner_cmdr_0:'Tymna the Weaver',
              pref_name_1:'', pref_notes_1:'', pref_cmdr_1:'', pref_partner_cmdr_1:''};
const display = {partnerContainer_0:'block', partnerContainer_1:'none'};
global.document = {getElementById: id => ({value: vals[id], style: {display: display[id]}})};
%s
const errs = validatePreferenceFields(2);
console.log(JSON.stringify(errs.map(e => e.id)));
""" % m.group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
        res = subprocess.run([node, fh.name], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout.strip()), ["pref_partner_cmdr_0"])


class TestReconstructDryRunUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.deck_html = self.client.get("/deck/Lathril%20Elves").text
        self.recon_html = self.client.get("/reconstruct/recon_abc").text

    def test_deck_modal_has_dry_run_toggle(self):
        self.assertIn('id="reconstructDryRunToggle"', self.deck_html)
        self.assertIn("onDeckReconstructDryRunChange(this.checked)", self.deck_html)
        self.assertIn("Start Dry Run", self.deck_html)
        self.assertIn("dry_run: Boolean(document.getElementById('reconstructDryRunToggle')", self.deck_html)
        self.assertIn("reconstructDryRunToggle').checked = false", self.deck_html)

    def test_reconstruct_page_dry_run_elements(self):
        for el in ("landingDryRunToggle", "dryRunSessionBadge", "dryRunNoticeBanner", "poolOwnedCollectionCards"):
            self.assertIn(f'id="{el}"', self.recon_html)
        for text in ("onLandingDryRunChange", "1. Snapshot (Dry Run)", "Dry run source decks (not decommissioned)",
                     "Close Dry Run", "DRY RUN", "dry_run: Boolean(document.getElementById('landingDryRunToggle')"):
            self.assertIn(text, self.recon_html)

    def test_dry_run_inline_scripts_are_valid_javascript(self):
        import shutil
        import subprocess
        import tempfile

        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        for html in (self.deck_html, self.recon_html):
            scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
            self.assertTrue(scripts)
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                fh.write(scripts[-1])
            res = subprocess.run([node, "--check", fh.name], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)



class TestReconstructUserFlowEndToEnd(unittest.TestCase):
    """Tests the full backend flow that the UI drives:
    Start -> Preferences -> Build -> Drafts -> Accept -> Dedicated cards.
    """

    def setUp(self):
        self.client = TestClient(app)
        # Use an in-memory or mocked Redis store for predictable unit testing
        self.fake_redis = FakeRedisStore()
        self.patcher_app_r = patch("src.server.app.main.r", self.fake_redis)
        self.patcher_recon_r = patch("src.server.domain.draft.reconstruct.r", self.fake_redis)
        self.patcher_app_r.start()
        # Deterministic builder: the real one calls an LLM agent.
        self._prev_builder = reconstruct.get_builder()
        reconstruct.set_builder(reconstruct.StubReconstructBuilder())

        # Seed two physical source decks
        self.deck1 = {
            "id": "archidekt:101",
            "registry_id": "archidekt:101",
            "name": "Lathril Elves",
            "commanders": ["Lathril, Blade of the Elves"],
            "color": "BG",
            "status": "physical",
            "cards": [
                {"name": "Lathril, Blade of the Elves", "quantity": 1},
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Elvish Archdruid", "quantity": 1},
                {"name": "Overgrowth", "quantity": 1},
            ],
        }
        self.deck2 = {
            "id": "archidekt:102",
            "registry_id": "archidekt:102",
            "name": "Edgar Markov Vampires",
            "commanders": ["Edgar Markov"],
            "color": "BRW",
            "status": "physical",
            "cards": [
                {"name": "Edgar Markov", "quantity": 1},
                {"name": "Blood Artist", "quantity": 1},
                {"name": "Demonic Tutor", "quantity": 1},
            ],
        }
        self.fake_redis.decks_db["archidekt:101"] = self.deck1
        self.fake_redis.decks_db["archidekt:102"] = self.deck2
        self.fake_redis.status_db["deck_status:archidekt:101"] = "physical"
        self.fake_redis.status_db["deck_status:archidekt:102"] = "physical"

    def tearDown(self):
        reconstruct.set_builder(self._prev_builder)
        self.patcher_app_r.stop()

    def test_single_deck_reconstruct_to_accepted_new_deck(self):
        # 1. Start Reconstruct on single deck
        start_res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:101"]})
        self.assertEqual(start_res.status_code, 201)
        start_data = start_res.json()
        self.assertTrue(start_data["success"])
        session_id = start_data["session_id"]
        session = start_data["session"]
        self.assertEqual(session["status"], reconstruct.STATUS_PENDING_PREFERENCES)
        self.assertEqual(len(session["source_decks"]), 1)

        # Verify source deck is decommissioned/retired
        self.assertEqual(self.fake_redis.status_db.get("deck_status:archidekt:101"), "retired")

        # 2. Submit Preferences for 1 target deck
        pref_payload = {
            "preferences": [
                {
                    "name": "Golgari Swarm Draft",
                    "commanders": ["Chatterfang, Squirrel General"],
                    "themes": ["Tokens", "Aristocrats"],
                    "strategies": ["midrange"],
                    "notes": "Low curve",
                }
            ]
        }
        pref_res = self.client.post(f"/api/reconstruct/{session_id}/preferences", json=pref_payload)
        self.assertEqual(pref_res.status_code, 200)
        self.assertTrue(pref_res.json()["success"])

        # 3. Trigger build (synchronous)
        build_res = self.client.post(f"/api/reconstruct/{session_id}/build?sync=true")
        self.assertEqual(build_res.status_code, 200)
        build_data = build_res.json()
        self.assertEqual(build_data["status"], reconstruct.STATUS_DRAFTS_READY)

        # 4. Fetch Drafts
        drafts_res = self.client.get(f"/api/reconstruct/{session_id}/drafts")
        self.assertEqual(drafts_res.status_code, 200)
        drafts_data = drafts_res.json()
        self.assertTrue(drafts_data["success"])
        drafts = drafts_data["drafts"]
        self.assertEqual(len(drafts), 1)

        draft = drafts[0]
        self.assertIn("Chatterfang, Squirrel General", draft["commanders"])
        self.assertTrue(any(c["tier"] == 1 for c in draft["cards"]))
        self.assertTrue(all(c["cost"] == 0.0 for c in draft["cards"] if c["tier"] in (1, 2)))

        # 5. Accept Draft
        accept_res = self.client.post(f"/api/reconstruct/{session_id}/accept", json={"status": "physical"})
        self.assertEqual(accept_res.status_code, 200)
        accept_data = accept_res.json()
        self.assertTrue(accept_data["success"])
        self.assertEqual(accept_data["session"]["status"], reconstruct.STATUS_ACCEPTED)
        self.assertEqual(len(accept_data["created_decks"]), 1)

    def test_two_deck_reconstruct_with_partner_commanders(self):
        # 1. Start Reconstruct on 2 source decks
        start_res = self.client.post(
            "/api/reconstruct/start", json={"deck_ids": ["archidekt:101", "archidekt:102"]}
        )
        self.assertEqual(start_res.status_code, 201)
        session_id = start_res.json()["session_id"]

        # 2. Submit preferences for 2 target decks with partner commanders
        pref_payload = {
            "preferences": [
                {
                    "name": "Kraum & Tymna Spells",
                    "commanders": ["Kraum, Ludevic's Opus", "Tymna the Weaver"],
                    "themes": ["Spellslinger"],
                },
                {
                    "name": "Thrasios Landfall",
                    "commanders": ["Thrasios, Triton Hero"],
                    "themes": ["Landfall"],
                },
            ]
        }
        pref_res = self.client.post(f"/api/reconstruct/{session_id}/preferences", json=pref_payload)
        self.assertEqual(pref_res.status_code, 200)

        # 3. Build inline
        build_res = self.client.post(f"/api/reconstruct/{session_id}/build?sync=true")
        self.assertEqual(build_res.status_code, 200)
        drafts = build_res.json()["session"]["drafts"]
        self.assertEqual(len(drafts), 2)
        self.assertEqual(len(drafts[0]["commanders"]), 2)

        # 4. Accept drafts
        accept_res = self.client.post(f"/api/reconstruct/{session_id}/accept", json={"status": "physical"})
        self.assertEqual(accept_res.status_code, 200)
        self.assertEqual(len(accept_res.json()["created_decks"]), 2)

    def test_abandon_flow(self):
        start_res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:101"]})
        session_id = start_res.json()["session_id"]

        abandon_res = self.client.post(
            f"/api/reconstruct/{session_id}/abandon", json={"reason": "User changed mind"}
        )
        self.assertEqual(abandon_res.status_code, 200)
        self.assertEqual(abandon_res.json()["session"]["status"], reconstruct.STATUS_ABANDONED)


class FakeRedisStore:
    """Mock Redis client for Reconstruct integration tests."""

    def __init__(self):
        self.data = {}
        self.sets = {}
        self.decks_db = {}
        self.status_db = {}
        self.instances = []

    def get(self, key):
        if key == "decks":
            return json.dumps(list(self.decks_db.values()))
        if key.startswith("deck_status:"):
            return self.status_db.get(key)
        return self.data.get(key)

    def set(self, key, value):
        if key.startswith("deck_status:"):
            self.status_db[key] = value
        else:
            self.data[key] = value

    def sadd(self, key, *members):
        if key not in self.sets:
            self.sets[key] = set()
        for m in members:
            self.sets[key].add(m)

    def smembers(self, key):
        return self.sets.get(key, set())

    def srem(self, key, *members):
        if key in self.sets:
            for m in members:
                self.sets[key].discard(m)

    def sinter(self, *keys):
        out = None
        for k in keys:
            m = set(self.sets.get(k, set()))
            out = m if out is None else out & m
        return out or set()

    def keys(self, pattern):
        pre = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(pre)]

    def delete(self, key):
        self.data.pop(key, None)

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def get(self, key):
        self.ops.append(("get", key))
        return self

    def set(self, key, value):
        self.ops.append(("set", key, value))
        return self

    def sadd(self, key, *members):
        self.ops.append(("sadd", key, members))
        return self

    def srem(self, key, *members):
        self.ops.append(("srem", key, members))
        return self

    def delete(self, key):
        self.ops.append(("delete", key))
        return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "get":
                results.append(self.store.get(op[1]))
            elif op[0] == "set":
                self.store.set(op[1], op[2])
                results.append(True)
            elif op[0] == "sadd":
                self.store.sadd(op[1], *op[2])
                results.append(len(op[2]))
            elif op[0] == "srem":
                self.store.srem(op[1], *op[2])
                results.append(len(op[2]))
            elif op[0] == "delete":
                self.store.delete(op[1])
                results.append(1)
        self.ops.clear()
        return results


if __name__ == "__main__":
    unittest.main()
