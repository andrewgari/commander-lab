"""PS-4: ability-score chart on the deck page + the /profile player review.

Structure and behaviour (same rules as the HUD / AN-4 tests):
- routes: /profile renders (both scopes, bad scope falls back), the nav has a
  Player Profile item that lights up on /profile, the deck page carries the
  Ability scores panel straight under the stat strip and loads personality.js;
- hud.css section 14 exists, uses only defined tokens, no gradients, and every
  hud-ps-* class the JS emits is defined;
- static/js/personality.js runs in node on REAL payloads (tests/fixtures/ps4/,
  snapshot of prod v0.4.41 2026-10-15: Doctor/Rose 51/31/39/76/79 leans
  melvin runner-up vorthos, Holy Hellfire with journal nudges, and the
  39-deck physical profile) and on synthetic edge cases for every state in
  UX_SPEC §4 / §5.3 (clear / close / balanced / nothing-stands-out, loading,
  not scored, queued, failed, empty, low data, partial failure, API error).
"""
import copy
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parent.parent
HUD_CSS = ROOT / "static" / "css" / "hud.css"
PS_JS = ROOT / "static" / "js" / "personality.js"
FIX = ROOT / "tests" / "fixtures" / "ps4"

DOCTOR = json.loads((FIX / "doctor_rose.json").read_text())
KAALIA = json.loads((FIX / "holy_hellfire.json").read_text())
PROFILE = json.loads((FIX / "profile_physical.json").read_text())["profile"]

DECK = {"id": 5620189, "name": "The Laws of Time are Mine!!", "color": "WUR", "commanders": ["The Tenth Doctor", "Rose Tyler"],
        "commander_uids": [], "status": "physical", "registry_id": "archidekt:5620189", "source": "archidekt",
        "source_id": "5620189", "cards": [{"name": "The Tenth Doctor", "quantity": 1}]}


def _classes(html):
    out = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        out.update(attr.split())
    return out


def _nav(html):
    return html.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


class TestRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_profile_page_renders_shell(self):
        res = self.client.get("/profile")
        self.assertEqual(res.status_code, 200)
        html = res.text
        self.assertIn('id="psProf"', html)
        self.assertIn('data-scope="physical"', html)
        self.assertRegex(html, r'<script src="/static/js/personality\.js(\?[^"]*)?" defer>')
        self.assertIn("/api/profile/personality?scope=", html)
        self.assertIn('class="hud-crumbs"', html)  # opt-in topbar

    def test_profile_scope_param(self):
        self.assertIn('data-scope="all"', self.client.get("/profile?scope=all").text)
        # Unknown scope: page still renders with the default instead of a 4xx.
        res = self.client.get("/profile?scope=<script>")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-scope="physical"', res.text)
        self.assertNotIn("<script>\"", res.text)

    def test_nav_has_player_profile_after_decks(self):
        nav = _nav(self.client.get("/").text)
        labels = re.findall(r'<span class="nav-label">([^<]+)</span>', nav)
        self.assertIn("Player Profile", labels)
        self.assertEqual(labels.index("Player Profile"), labels.index("Commander Decks") + 1)
        self.assertIn('href="/profile"', nav)
        active = re.findall(r'class="nav-item active"[^>]*>.*?<span class="nav-label">([^<]+)</span>', _nav(self.client.get("/profile").text), re.S)
        self.assertEqual(active, ["Player Profile"])

    @patch("app.registry.list_decks", return_value=[DECK])
    def test_deck_page_has_ability_panel_under_stat_strip(self, _):
        body = self.client.get("/deck/The%20Laws%20of%20Time%20are%20Mine!!").text.split("<body", 1)[1]
        order = [body.find(m) for m in ('id="deckStats"', 'id="psPanel"', 'id="deckCurve"')]
        self.assertTrue(all(i > 0 for i in order), order)
        self.assertEqual(order, sorted(order))
        self.assertIn('aria-labelledby="psH"', body)
        self.assertIn(">Ability scores<", body)
        self.assertRegex(body, r'<script src="/static/js/personality\.js(\?[^"]*)?" defer>')
        # The panel asks the PS-3 API by registry id, and gets "your average" from the physical profile.
        self.assertIn('const psDeckId = "archidekt:5620189"', body)
        self.assertIn("/personality'", body)
        self.assertIn("/api/profile/personality?scope=physical", body)
        for hook in ("PSUI.deckPanelBody(", "PSUI.deckPanelFoot(", "PSUI.bindEvidence(", "PSUI.deckModel(", "method: 'POST'"):
            with self.subTest(hook=hook):
                self.assertIn(hook, body)

    def test_unknown_deck_panel_falls_back_to_name(self):
        body = self.client.get("/deck/No%20Such%20Deck").text
        self.assertIn('const psDeckId = "No Such Deck"', body)

    def test_personality_js_served(self):
        res = self.client.get("/static/js/personality.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("window.PSUI", res.text)

    def test_profile_page_guards(self):
        # Copilot #106: a late response for an old scope never renders; a failed rescore POST stays retryable.
        html = self.client.get("/profile").text
        self.assertIn("const seq = ++psSeq;", html)
        self.assertIn("if (seq !== psSeq) return;", html)
        self.assertIn("b.disabled = ok;", html)
        js = PS_JS.read_text()
        self.assertIn("H.sortTable(root.querySelector(\"#psTable\"), th); syncCards();", js)
        self.assertIn('if (ev.target.closest(".hud-sort")) syncCards();', js)


class TestStylesheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = HUD_CSS.read_text()
        cls.section = cls.css.split("10. ABILITY SCORES (PS-4)", 1)[1]
        cls.js = PS_JS.read_text()

    def test_section_and_tokens(self):
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        for tok in ("--ps-axis", "--ps-lean", "--ps-lean-dim", "--ps-ref", "--ps-journal", "--ps-journal-neg",
                    "--ps-meter-h", "--ps-score", "--ps-score-sm", "--ps-mono", "--ps-dot", "--ps-strip-h", "--ps-lean-delay"):
            with self.subTest(tok=tok):
                self.assertIn(tok, defined)
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.section))
        self.assertEqual(sorted(used - defined), [])

    def test_no_raw_colours_or_gradients(self):
        self.assertNotRegex(self.section, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(self.section, r"rgba?\(")
        self.assertNotIn("gradient(", self.section)
        for shadow in re.findall(r"box-shadow:\s*([^;]+);", self.section):
            with self.subTest(shadow=shadow):
                self.assertRegex(shadow, r"^inset (0|var\(--indicator\)) (0|var\(--indicator\)) 0 var\(--[a-z0-9-]+\)$")

    def test_no_per_axis_colours(self):
        # UX_SPEC §0.3: colour = score (data) or lean (signal), never per-axis hues or mana colours.
        self.assertNotRegex(self.section, r"--mana-")
        rules = re.sub(r"/\*.*?\*/", "", self.section, flags=re.S).lower()
        for axis in ("timmy", "johnny", "spike", "vorthos", "melvin"):
            self.assertNotIn(axis, rules)

    def test_reduced_motion_and_phone_blocks(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.section)
        self.assertIn("@media (max-width: 640px)", self.section)

    def test_every_hud_ps_class_in_js_is_defined(self):
        used = set(re.findall(r"(?<![-\w])(hud-ps-[a-z0-9-]+)", self.js)) - {"hud-ps-"}
        defined = set(re.findall(r"\.(hud-ps-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])
        kit = set(re.findall(r"(?<![-\w])(hud-(?!ps-)[a-z0-9-]*[a-z0-9])(?![-\w])", self.js))
        kit_defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(kit - kit_defined), [])


HARNESS = r"""
const fs = require('fs');
global.window = {};
global.document = { addEventListener() {}, querySelectorAll: () => [], body: { contains: () => true } };
eval(fs.readFileSync(process.argv[1], 'utf8'));
const P = window.PSUI;
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
for (const [k, c] of Object.entries(cases)) {
  try {
    if (c.fn === 'deck') {
      const d = P.deckModel(c.p);
      out[k] = { d, lean: P.leanSentence(d), info: P.leanInfo(d), sr: P.srSummary(d),
                 body: P.deckPanelBody('ok', { d, ref: c.ref || null, refN: 39 }),
                 foot: P.deckPanelFoot({ d, ref: c.ref || null, refN: 39, computed_at: c.computed_at, stale: c.stale, error: c.error }),
                 ev: Object.fromEntries(P.AX.map(a => [a, P.evidence(d, a)])) };
    } else if (c.fn === 'state') {
      out[k] = P.deckPanelBody(c.state, c.info || {});
    } else if (c.fn === 'profile') {
      out[k] = P.profileHtml(c.state, c.p, c.info || {});
    } else if (c.fn === 'misc') {
      out[k] = { fmt: Object.fromEntries(Object.entries(c.fmt).map(([s, v]) => [s, P.FMT[s](v)])),
                 fields: c.fields.map(P.fieldCopy), onlyB: P.onlyB(c.html),
                 ago: c.ago.map(([iso, now]) => P.ago(iso, now)), levels: c.levels.map(P.level) };
    }
  } catch (e) { out[k] = { error: String(e && e.stack || e) }; }
}
process.stdout.write(JSON.stringify(out));
"""


def _scores(t, j, s, v, m, leans, runner=None, journal=False):
    p = {"version": 1, "leans_toward": leans, "runner_up": runner, "journal_used": journal,
         "notes": [] if journal else ["no journal: cards-only score"]}
    for a, sc in zip(("timmy", "johnny", "spike", "vorthos", "melvin"), (t, j, s, v, m)):
        p[a] = {"score": sc, "card_score": sc, "journal_nudge": 0.0,
                "evidence": [{"signal": "journal", "label": "Deck Journal", "value": "no journal", "sub_score": 0.0,
                              "points": 0.0, "weight": None, "cards": [], "fields": []}]}
    return p


def _profile_variant(**kw):
    p = copy.deepcopy(PROFILE)
    p.update(kw)
    return p


@unittest.skipUnless(shutil.which("node"), "node not installed: personality.js tests need it")
class TestPersonalityJs(unittest.TestCase):
    """Run static/js/personality.js in node and check the markup for every state."""

    @classmethod
    def setUpClass(cls):
        low = _profile_variant(decks_scored=2, low_data=True, decks_to_unlock_trends=1, trends=[],
                               decks=PROFILE["decks"][:2], headline="Early read: <b>Johnny</b>, so far.",
                               why="That's your average over 2 physical decks. Trends need at least 3 scored decks: 1 more to go.")
        empty = _profile_variant(decks_scored=0, low_data=True, decks_to_unlock_trends=3, average=None, dominant=None,
                                 headline_kind="none", headline="No decks scored yet.", trends=[], decks=[], journal_count=0)
        partial = _profile_variant(failed=[{"id": "archidekt:1", "name": "Knights <b>&", "reason": "No commander on the decklist", "status": "physical"}])
        only_failed = copy.deepcopy(empty)
        only_failed["failed"] = [{"id": "archidekt:1", "name": "Knights", "reason": "No commander on the decklist", "status": "physical"}]
        cases = {
            "doctor": {"fn": "deck", "p": DOCTOR["personality"], "ref": PROFILE["average"], "computed_at": DOCTOR["computed_at"]},
            "kaalia": {"fn": "deck", "p": KAALIA["personality"], "computed_at": KAALIA["computed_at"], "stale": True},
            "clear": {"fn": "deck", "p": _scores(34, 45, 90, 4, 39, "spike")},
            "bunched": {"fn": "deck", "p": _scores(39, 70, 65, 63, 31, "balanced")},
            "low": {"fn": "deck", "p": _scores(20, 34, 30, 12, 5, "balanced")},
            "degraded": {"fn": "deck", "p": dict(_scores(50, 40, 30, 20, 10, "timmy"), notes=["39 cards without metadata", "EDHREC unavailable", "no journal: cards-only score"]),
                         "error": "boom"},
            "s_loading": {"fn": "state", "state": "loading"},
            "s_not_scored": {"fn": "state", "state": "not_scored"},
            "s_not_found": {"fn": "state", "state": "not_scored", "info": {"can_score": False}},
            "s_queued": {"fn": "state", "state": "queued"},
            "s_error": {"fn": "state", "state": "error", "info": {"status_line": "Scoring failed: <img src=x>.", "can_score": True}},
            "p_full": {"fn": "profile", "state": "ok", "p": PROFILE},
            "p_low": {"fn": "profile", "state": "ok", "p": low},
            "p_empty": {"fn": "profile", "state": "ok", "p": empty},
            "p_partial": {"fn": "profile", "state": "ok", "p": partial},
            "p_only_failed": {"fn": "profile", "state": "ok", "p": only_failed},
            "p_error": {"fn": "profile", "state": "error", "p": None, "info": {"status_line": "GET /api/profile/personality returned 503."}},
            "p_loading": {"fn": "profile", "state": "loading", "p": None},
            "misc": {"fn": "misc",
                     "fmt": {"V1": "type Alien 5/22; set WHO 41/55", "J4": 0.44, "S2": 1, "M3": 4, "T5": 13, "S5": 3.29},
                     "fields": ["preferred_win_conditions.primary=combat", "text: fatty, swarm, swing", "novelties (non-empty)", "target_power_level.scale=8"],
                     "html": 'You are mostly <b>Spike</b><script>alert(1)</script><img src=x onerror=1>.',
                     "ago": [["2026-10-15T10:35:31+00:00", 1792060000000], ["", 0], ["2026-10-15T10:00:00+00:00", 1792058430000]],
                     "levels": [0, 19, 20, 35, 50, 65, 80, 100]},
        }
        res = subprocess.run(["node", "-e", HARNESS, str(PS_JS)], input=json.dumps(cases), capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)
        for k, v in cls.out.items():
            if isinstance(v, dict) and "error" in v:
                raise AssertionError(f"{k}: {v['error']}")

    # ---- deck panel: the control deck --------------------------------------
    def test_doctor_rose_scores_and_close_lean(self):
        o = self.out["doctor"]
        self.assertEqual(o["d"]["scores"], {"timmy": 51, "johnny": 31, "spike": 39, "vorthos": 76, "melvin": 79})
        self.assertEqual(o["info"]["kind"], "close")
        self.assertEqual(o["lean"]["head"], "Leans <em>Melvin</em>")
        self.assertEqual(o["lean"]["why"], "Close second <b>Vorthos</b>, 3 points behind.")
        self.assertEqual(o["sr"], "Timmy 51, Johnny 31, Spike 39, Vorthos 76, Melvin 79. Leans Melvin.")
        body = o["body"]
        self.assertIn('class="hud-ps-b is-close"', body)
        tiles = re.findall(r'<button type="button" aria-expanded="false" aria-controls="psEv" class="([^"]+)" data-axis="(\w+)" aria-label="([^"]+)"', body)
        self.assertEqual([t[1] for t in tiles], ["timmy", "johnny", "spike", "vorthos", "melvin"])
        self.assertEqual([t[2] for t in tiles][3:], ["Vorthos 76, close second", "Melvin 79, lean"])
        self.assertIn("is-lean", tiles[4][0])
        self.assertIn("is-runner", tiles[3][0])
        self.assertEqual(body.count('class="hud-ps-2nd"'), 1)
        self.assertIn('id="psEv" role="region" hidden', body)
        # "Your average" ticks at the physical profile average.
        self.assertEqual(re.findall(r'hud-ps-meter-ref" style="left:(\d+)%', body), ["43", "45", "48", "35", "38"])
        self.assertEqual(re.findall(r'hud-ps-meter-fill" style="width:(\d+)%', body), ["51", "31", "39", "76", "79"])
        # No journal: no nudges on tiles, foot says cards only.
        self.assertNotIn("hud-ps-nudge", body)
        self.assertIn("Cards only · no journal", o["foot"])
        self.assertIn("Your average (39 physical decks)", o["foot"])
        self.assertIn("· v1", o["foot"])
        self.assertIn('href="/profile"', o["foot"])

    def test_doctor_rose_evidence_drawer(self):
        ev = self.out["doctor"]["ev"]["melvin"]
        self.assertIn("Melvin · 79", ev)
        # Header sum = the shown rows added up (16.7 + 30 + 10 + 1.9 + 20 = 78.6), not the unrounded card_score.
        self.assertIn("cards <b>78.6</b> ≈ <b>79</b>", ev)
        codes = re.findall(r'data-signal="(\w+)"', ev)
        self.assertEqual(codes, ["M1", "M2", "M3", "M4", "M5"])
        self.assertIn("16.7 <small>/ 25</small>", ev)
        self.assertIn("No journal", ev)
        self.assertIn("0.0 <small>/ ±10</small>", ev)
        # M1 keywords and M3 loops show as tags; card lists cap at 6 then "+N more".
        self.assertIn("doctor&#39;s companion", ev)
        self.assertIn("attacks+counters", ev)
        self.assertIn('aria-label="Show 38 more cards">+38 more</button>', ev)
        m2 = ev.split('data-signal="M2"', 1)[1].split("</li>", 1)[0]
        self.assertEqual(m2.count('class="hud-cardlink"'), 6)
        self.assertIn("Points = weight × how far the signal got along its band", ev)
        # Every signal is listed, zeros included and dimmed (J1 = 0 combos).
        jo = self.out["doctor"]["ev"]["johnny"]
        self.assertRegex(jo, r'class="hud-ps-ev-row is-zero" data-signal="J1"')
        self.assertIn("0 combos in deck", jo)

    def test_journal_deck_nudges(self):
        o = self.out["kaalia"]
        self.assertTrue(o["d"]["journal"])
        self.assertEqual(o["info"]["kind"], "clear")
        self.assertEqual(o["lean"]["why"], "Big, splashy moments. 18 points clear of Spike.")
        body = o["body"]
        # Clear lean: solid tile, no "2nd".
        self.assertIn('class="hud-ps-b"', body)
        self.assertNotIn("hud-ps-2nd", body)
        nudges = re.findall(r'class="hud-ps-nudge ([^"]+)"[^>]*>([^<]+)', body)
        self.assertEqual(nudges, [("is-up", "+8"), ("is-up", "+1"), ("is-up", "+3"), ("is-none", "±0"), ("is-none", "±0")])
        ev = o["ev"]["timmy"]
        self.assertIn("cards <b>64.6</b> + journal <b>8.0</b> ≈ <b>73</b>", ev)  # 30 + 15 + 1.7 + 17.9 + 0
        self.assertIn("Primary win con: combat", ev)
        self.assertIn("Words in your notes: “fatty”, “swarm”, “swing”", ev)
        self.assertIn("+4.0 <small>/ ±10</small>", ev)
        self.assertIn("is-neg", o["ev"]["johnny"])  # excluded combo: −1.5
        self.assertIn("−1.5", o["ev"]["johnny"])
        self.assertIn("Your journal doesn&#39;t move this axis.", o["ev"]["melvin"])
        self.assertNotIn("Cards only", o["foot"])
        self.assertIn("Inputs changed · rescoring", o["foot"])
        self.assertNotIn("Your average", o["foot"])  # no ref given

    def test_balanced_and_nothing_stands_out(self):
        b = self.out["bunched"]
        self.assertEqual(b["lean"]["head"], "Balanced")
        self.assertEqual(b["lean"]["why"], "<b>Johnny, Spike and Vorthos</b> are within 7 points.")
        self.assertIn('class="hud-ps-b is-balanced"', b["body"])
        self.assertNotIn("is-lean", b["body"])
        lo = self.out["low"]
        self.assertEqual(lo["lean"]["head"], "Nothing stands out")
        self.assertEqual(lo["lean"]["why"], "Top score is <b>Johnny 34</b>, below 35 on every axis.")
        self.assertNotIn("is-lean", lo["body"])
        c = self.out["clear"]
        self.assertEqual(c["lean"]["why"], "Plays to win. 45 points clear of Johnny.")

    def test_degraded_notes_and_failed_rescore(self):
        foot = self.out["degraded"]["foot"]
        self.assertIn("▲ 39 cards without metadata · low confidence", foot)
        self.assertIn("▲ EDHREC unavailable", foot)
        self.assertIn("Cards only · no journal", foot)
        self.assertIn("Last rescore failed · showing earlier scores", foot)
        # Scores are still shown; notes never touch the tiles.
        self.assertEqual(len(re.findall(r'class="hud-ps-tile', self.out["degraded"]["body"])), 5)

    # ---- deck panel: non-ok states -----------------------------------------
    def test_deck_states(self):
        o = self.out
        self.assertIn('aria-busy="true"', o["s_loading"])
        self.assertIn("hud-ps-skel", o["s_loading"])
        self.assertNotIn("aria-controls", o["s_loading"])  # skeleton tiles aren't buttons
        self.assertIn("Not scored yet", o["s_not_scored"])
        self.assertIn("data-ps-score", o["s_not_scored"])
        self.assertNotIn("data-ps-score", o["s_not_found"])
        self.assertIn("Scoring…", o["s_queued"])
        err = o["s_error"]
        self.assertIn('class="hud-ps-state is-error" role="alert"', err)
        self.assertIn("Couldn&#39;t load ability scores", err)
        self.assertIn("&lt;img src=x&gt;", err)
        self.assertNotIn("<img src=x>", err)
        self.assertIn("data-ps-retry", err)
        self.assertIn("data-ps-score", err)

    # ---- /profile ------------------------------------------------------------
    def test_profile_full(self):
        h = self.out["p_full"]
        self.assertIn('<blockquote id="psHeadline">You are mostly <b>Spike</b>, with a lot of Johnny.</blockquote>', h)
        self.assertIn("By count, Johnny wins: 9 decks lean that way", h)
        self.assertIn("Physical · 39</label>", h)
        self.assertIn("All · 57</label>", h)
        self.assertRegex(h, r'id="psScope-physical" value="physical" checked')
        self.assertIn("3 of 39 have a journal", h)
        # 01 average tiles: static (no drawer), close lean on spike with johnny 2nd.
        avg = h.split('id="psSecAvg"', 1)[1].split("</section>", 1)[0]
        self.assertIn("hud-ps-b is-close is-static", avg)
        self.assertNotIn("aria-controls", avg)
        self.assertRegex(avg, r'class="hud-ps-tile is-lean is-top" data-axis="spike"')
        self.assertRegex(avg, r'class="hud-ps-tile is-runner" data-axis="johnny"')
        self.assertEqual(re.findall(r'class="hud-ps-num">(\d+)<', avg), ["43", "45", "48", "35", "38"])
        # Strip plot: 39 decks x 5 axes; lean dots match lean_counts.
        self.assertEqual(h.count('class="hud-ps-dot'), 195)
        self.assertEqual(h.count('class="hud-ps-dot is-lean"'), 35)
        # 02 trends: all 8 from PS-3, number first, action wired to the table.
        callouts = re.findall(r'<li class="hud-ps-callout[^"]*"[^>]*data-kind="([^"]+)"', h)
        self.assertEqual(len(callouts), 8)
        self.assertEqual(h.count("hud-ps-callout is-lead"), 1)
        acts = re.findall(r'data-act="(\w+)" data-val="([^"]+)">([^<]+)', h)
        self.assertIn(("filter", "johnny", "Show them →"), acts)
        self.assertIn(("sort", "spike", "Sort by Spike →"), acts)
        self.assertIn(("open", "archidekt:7291350", "Open deck →"), acts)
        self.assertIn(("filter", "no_journal", "Cards-only decks →"), acts)
        self.assertIn(("filter", "tribal", "Show them →"), acts)
        self.assertIn('67<small> avg</small>', h)          # group callout: avg, not "/ 11"
        self.assertIn('9<small> / 39</small>', h)
        # 03 table: one row per deck with test hooks; links to the deck page; heat levels.
        rows = re.findall(r'<tr data-id="([^"]+)" data-lean="(\w+)" data-journal="([01])"', h)
        self.assertEqual(len(rows), 39)
        self.assertEqual(sum(1 for r in rows if r[2] == "1"), 3)
        self.assertIn('href="/deck/The%20Laws%20of%20Time%20are%20Mine!!"', h)
        doc = h.split('data-id="archidekt:5620189"', 1)[1].split("</tr>", 1)[0]
        self.assertIn('class="hud-ps-h is-lean" data-level="4" data-value="79"', doc)
        self.assertIn("Melvin · V", doc)
        # PS-5: the runner-up cell is marked (neutral dashed ring) so cells agree with the chip.
        self.assertIn('class="hud-ps-h is-runner" data-level="4" data-value="76"', doc)
        self.assertEqual(h.count('<td class="hud-ps-h is-runner"'), 5)  # 5 physical decks have a runner_up
        self.assertIn('title="All decks · key 0"', h)
        # Filters: lean seg + group options present because some decks are tribal.
        for v in ("all", "timmy", "balanced", "no_journal", "tribal"):
            self.assertIn(f'id="psLean-{v}"', h)
        self.assertIn('data-hud-sortable id="psTable"', h)
        self.assertEqual(h.count('<li class="hud-ps-card"'), 39)  # phone card list

    def test_profile_low_data(self):
        h = self.out["p_low"]
        self.assertIn("Early read: <b>Johnny</b>, so far.", h)
        self.assertNotIn("hud-ps-spread", h)
        self.assertIn("1 more deck to unlock trends", h)
        self.assertNotIn("hud-ps-callout", h)
        self.assertEqual(len(re.findall(r'<tr data-id=', h)), 2)

    def test_profile_empty(self):
        h = self.out["p_empty"]
        self.assertIn("No decks scored yet", h)
        self.assertIn('href="/decks">Sync decks</a>', h)
        self.assertIn("What are these?", h)
        self.assertIn('id="psWhat" hidden', h)
        self.assertIn('href="/profile?scope=all"', h)
        self.assertNotIn("psTable", h)

    def test_profile_partial_failure(self):
        h = self.out["p_partial"]
        self.assertIn('<span class="is-danger">1 failed</span>', h)
        failed = h.split('class="is-failed"', 1)[1].split("</tr>", 1)[0]
        self.assertIn("Knights &lt;b&gt;&amp;", failed)
        self.assertIn("No commander on the decklist", failed)
        self.assertIn('data-ps-rescore="archidekt:1"', failed)
        self.assertIn("Couldn&#39;t score", failed)
        # Failed decks come after the scored ones and never count in the average.
        self.assertGreater(h.index('class="is-failed"'), h.index('data-id="archidekt:5620189"'))
        self.assertEqual(re.findall(r'class="hud-ps-num">(\d+)<', h)[:5], ["43", "45", "48", "35", "38"])
        self.assertIn("40 decks", h)
        # Copilot #106: failed rows live in their own tbody, outside the sortable first tbody.
        table = h.split('id="psTable"', 1)[1].split("</table>", 1)[0]
        bodies = re.findall(r"<tbody[^>]*>(.*?)</tbody>", table, re.S)
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("is-failed", bodies[0])
        self.assertIn('<tbody class="hud-ps-failed">', table)
        # Copilot #106: the phone card list has the same Retry.
        cards = h.split('id="psCards"', 1)[1].split("</ul>", 1)[0]
        card = cards.split('class="hud-ps-card is-failed"', 1)[1].split("</li>", 1)[0]
        self.assertIn('data-ps-rescore="archidekt:1"', card)

    def test_profile_only_failed_decks_lists_them(self):
        # Copilot #106: 0 scored + N failed still shows each failure with its reason and Retry.
        h = self.out["p_only_failed"]
        self.assertIn("No decks scored yet", h)
        self.assertIn("1 deck failed to score.", h)
        self.assertIn("Decks that failed to score", h)
        self.assertIn("No commander on the decklist", h)
        self.assertEqual(h.count('data-ps-rescore="archidekt:1"'), 2)  # table + phone list

    def test_profile_error_and_loading(self):
        e = self.out["p_error"]
        self.assertIn('role="alert"', e)
        self.assertIn("Couldn&#39;t build your profile", e)
        self.assertIn("returned 503", e)
        self.assertIn("data-ps-retry", e)
        lo = self.out["p_loading"]
        self.assertIn("Reading your decks…", lo)
        self.assertIn('aria-busy="true"', lo)

    # ---- copy helpers ----------------------------------------------------------
    def test_copy_helpers(self):
        m = self.out["misc"]
        self.assertEqual(m["fmt"]["V1"], "Alien\u00a05/22 creatures · set\u00a0WHO\u00a041/55")
        self.assertEqual(m["fmt"]["J4"], "44% touch a commander")
        self.assertEqual(m["fmt"]["S2"], "1 tutor")
        self.assertEqual(m["fmt"]["M3"], "4 loops")
        self.assertEqual(m["fmt"]["T5"], "13 interaction · fewer scores higher")
        self.assertEqual(m["fmt"]["S5"], "avg mv 3.29 · lower scores higher")
        self.assertEqual(m["fields"], ["Primary win con: combat", "Words in your notes: “fatty”, “swarm”, “swing”",
                                       "You listed novelties", "Target power: 8"])
        # Server copy keeps <b> only.
        self.assertEqual(m["onlyB"], "You are mostly <b>Spike</b>alert(1).")
        self.assertEqual(m["ago"][1], "")
        self.assertEqual(m["levels"], [0, 0, 1, 2, 3, 4, 5, 5])


if __name__ == "__main__":
    unittest.main()
