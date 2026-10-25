"""BR-6: bracket picker in the Journal + stored evaluation on the deck page.

Direction A (Inline) from the BR-D designer spec (draft PR #97,
docs/BRACKETS_UX_SPEC.md, reference only):
- Journal: "Power & bracket" section, an Off/1-5 hud-seg--scale beside the
  power scale, a live definition card, a below-the-floor advisory note,
  saving target_bracket through the BR-2 journal API.
- Deck page: a banner between the tabs and the hero built from the stored
  BR-5 result (GET /api/deck/{id}/bracket): estimated bracket + power vs the
  targets, hard vs advisory conflict rows (exact-mismatch and power-target
  advisories included), the "How this was decided" tray, flagged cards in the
  decklist, and the not-evaluated / re-evaluating / failed states.

The view-model + renderers live in static/js/brackets.js and run in node on
real BR-4 evaluate_bracket() output (fixture decks), so the UI is tested
against the exact shape the worker stores.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from analytics import bracket_eval as be
from analytics import brackets as br
from analytics import engine, journal
from tests.test_bracket_eval import evaluate, journal as jr, load, three_gc_deck
from tests.test_deck_journal_ui import SAVED, _JournalPage
from tests.test_deck_stats_ui import DECK, HUD_CSS, HUD_JS, INVENTORY, _classes
from tests.test_hud_shell_deck import _page_style

from fastapi.testclient import TestClient

import app as app_mod
from app import app

ROOT = Path(__file__).resolve().parent.parent
BR_JS = ROOT / "static" / "js" / "brackets.js"


def _api(ev, **over):
    """GET /api/deck/{id}/bracket 200 body around a BracketEvaluation."""
    body = {"success": True, "registry_id": "archidekt:417707", "deck_name": "Holy Hellfire", "pending": False,
            "status": "ok", "evaluated_at": "2026-10-15T10:00:05+00:00", "input_hash": "5484f11c70c7aa",
            "stale": False, "error": None, "error_at": None, "evaluation": ev.model_dump() if ev else None}
    body.update(over)
    return body


def _scenarios():
    gc3 = three_gc_deck()
    spicy, spicy_combos = load("spicy_only")
    return {
        # Target 1 on a deck with 3 Game Changers: the live acceptance case.
        "b1": _api(evaluate(gc3, jr(bracket=1, power=3), power_estimate=6.5)),
        "b1pet": _api(evaluate(gc3, jr(bracket=1, power=3, pets=["Rhystic Study"]), power_estimate=6.5)),
        "b3": _api(evaluate(gc3, jr(bracket=3, power=7), power_estimate=7.0)),
        "b4": _api(evaluate(gc3, jr(bracket=4, power=8), power_estimate=7.5)),
        "unset": _api(evaluate(gc3, jr(bracket=None, power=8), power_estimate=6.5)),
        "running": _api(evaluate(gc3, jr(bracket=1, power=3), power_estimate=6.5), stale=True),
        "failed": _api(evaluate(gc3, jr(bracket=3, power=7), power_estimate=7.0), status="error",
                       error="Commander Spellbook timed out", error_at="2026-10-15T10:05:00+00:00"),
        # Failed after a change: the API keeps the old result, stale=True, pending=False.
        "failed_stale": _api(evaluate(gc3, jr(bracket=3, power=7), power_estimate=7.0), status="error", stale=True,
                             error="boom", error_at="2026-10-15T10:05:00+00:00"),
        "failed_queued": _api(evaluate(gc3, jr(bracket=3, power=7), power_estimate=7.0), status="error", stale=True,
                              pending=True, error="boom", error_at="2026-10-15T10:05:00+00:00"),
        # 4 GCs at target 3: Upgraded allows 3, so "not allowed" would contradict the definition.
        "gc4_t3": _api(evaluate(load("gc_heavy")[0], jr(bracket=3, power=7), power_estimate=7.0)),
        # power_target threshold (engine.POWER_ALIGN_TOLERANCE = 1.5): 7.5 vs 6 inside, 7.6 vs 6 outside.
        "pw_in": _api(evaluate(spicy, jr(bracket=3, power=6), combos=spicy_combos, power_estimate=7.5)),
        "pw_out": _api(evaluate(spicy, jr(bracket=3, power=6), combos=spicy_combos, power_estimate=7.6)),
    }


NOT_EVALUATED = {"success": False, "registry_id": "archidekt:417707", "deck_name": "Holy Hellfire",
                 "pending": False, "status": "not_evaluated", "error": "Not evaluated yet.", "evaluation": None}
ERROR_ONLY = dict(NOT_EVALUATED, status="error", error="boom")


@unittest.skipUnless(shutil.which("node"), "node not installed: brackets.js tests need it")
class TestBracketsJs(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [],
                    getElementById: () => null, querySelector: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
eval(fs.readFileSync(process.argv[2], 'utf8'));
const B = window.Brackets;
const S = JSON.parse(process.argv[3]);
const opts = { deckUrl: '/deck/Holy%20Hellfire', tolerance: 1.5 };
const out = { scen: {} };
for (const k of Object.keys(S)) {
  const r = S[k], ev = r.evaluation;
  out.scen[k] = {
    state: B.state(r, 200),
    counts: B.counts(ev),
    titles: ev.conflicts.map(c => B.title(c)),
    offenders: B.offenders(ev),
    captions: Object.fromEntries(Object.entries(B.offenders(ev)).map(([n, o]) => [n, B.caption(o, ev)])),
    brDelta: B.bracketDelta(ev), pwDelta: B.powerDelta(ev, 1.5),
    banner: B.banner(r, 200, opts), chip: B.metaChip(r, 200),
    why: B.explanation(r),
  };
}
out.pending = { state: B.state(S.b1 && NOT, 404), banner: B.banner(NOT, 404, opts), chip: B.metaChip(NOT, 404) };
out.errOnly = { state: B.state(ERR, 404), banner: B.banner(ERR, 404, opts) };
out.unavailable = { state: B.state(null, 503), banner: B.banner(null, 503, opts) };
out.copy = [1, 2, 3, 4, 5].map(n => B.COPY[n]);
out.defs = [null, 1, 2, 3, 4, 5].map(n => B.definition(n));
out.warn = { b1: B.belowFloor(1, S.b1.evaluation), b3: B.belowFloor(3, S.b1.evaluation), off: B.belowFloor(null, S.b1.evaluation),
             noEval: B.belowFloor(1, null), gc4t3: B.belowFloor(3, S.gc4_t3.evaluation) };
out.xss = B.banner({ success: true, status: 'ok', stale: false, evaluation: Object.assign({}, S.b1.evaluation, {
  conflicts: [{ severity: 'hard', rule: 'game_changers', message: '<img src=x onerror=1>', cards: ['<b>x</b>'] }] }) }, 200, opts);
// BR-7: a count-led combo message must not repeat its title.
out.combo = B.banner({ success: true, status: 'ok', stale: false, evaluation: Object.assign({}, S.b1.evaluation, {
  conflicts: [{ severity: 'hard', rule: 'two_card_combos', message: '2 two-card combos (A + B; C + D); Bracket 1 (Exhibition) allows none.', cards: ['A', 'B'] }] }) }, 200, opts);
// Singular counts (Copilot on #109): bracket_eval._plural(1, ...) drops the "s".
out.combo1 = B.banner({ success: true, status: 'ok', stale: false, evaluation: Object.assign({}, S.b1.evaluation, {
  conflicts: [{ severity: 'hard', rule: 'two_card_combos', message: '1 two-card combo (A + B); Bracket 1 (Exhibition) allows none.', cards: ['A', 'B'] }] }) }, 200, opts);
out.early1 = B.banner({ success: true, status: 'ok', stale: false, evaluation: Object.assign({}, S.b1.evaluation, {
  conflicts: [{ severity: 'hard', rule: 'two_card_combos', message: '1 early two-card combo (Spellbook Ruthless/Banned: A + B); Bracket 3 (Upgraded) only allows late-game two-card combos.', cards: ['A', 'B'] }] }) }, 200, opts);
out.units = B.explanation({ evaluation: Object.assign({}, S.b1.evaluation, { explanation: { summary: 's', signals: [
  { signal: 'price', value: 599.1, effect: 'context only', source: 'Scryfall prices' },
  { signal: 'edhrec_overlap', value: 35, effect: 'context only', source: 'EDHREC' }] } }) });
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        cls.S = _scenarios()
        harness = cls.HARNESS.replace("const S = JSON.parse(process.argv[3]);",
                                      "const S = JSON.parse(process.argv[3]); const NOT = JSON.parse(process.argv[4]);"
                                      " const ERR = JSON.parse(process.argv[5]);")
        res = subprocess.run(["node", "-e", harness, str(HUD_JS), str(BR_JS), json.dumps(cls.S),
                              json.dumps(NOT_EVALUATED), json.dumps(ERROR_ONLY)],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)
        cls.sc = cls.out["scen"]

    # ---- the acceptance case: bracket 1 + Game Changers -------------------------
    def test_bracket_1_with_game_changers_shows_hard_and_advisory_conflicts(self):
        b1 = self.sc["b1"]
        rules = [c["rule"] for c in self.S["b1"]["evaluation"]["conflicts"]]
        self.assertEqual(rules, ["game_changers", "bracket_mismatch", "power_target"])
        self.assertEqual(b1["counts"], {"all": 3, "hard": 1, "adv": 2})
        self.assertEqual(b1["titles"], ["3 Game Changers", "Plays above target", "Plays above target power"])
        h = b1["banner"]
        self.assertIn("hud-bracket", h)
        self.assertIn("3 conflicts", h)
        self.assertIn("with Bracket 1", h)
        self.assertIn("1 hard rule broken", h)
        self.assertIn("2 advisories", h)
        self.assertIn("is-hard", h)  # worst severity drives the edge
        for card in ["Demonic Tutor", "Rhystic Study", "Smothering Tithe"]:
            self.assertIn('data-show="' + card + '"', h)
        # The message already opens with the title: bold it, don't repeat it.
        self.assertIn("<b>3 Game Changers</b> in the deck", h)
        self.assertNotIn("3 Game Changers.</b> 3 Game Changers", h)

    def test_hard_and_advisory_are_shape_plus_colour(self):
        h = self.sc["b1"]["banner"]
        self.assertIn("hud-bracket-sev--hard", h)
        self.assertIn("hud-bracket-sev--adv", h)
        self.assertIn("■", h)
        self.assertIn("▲", h)
        self.assertIn("Hard rule", h)
        self.assertIn("Advisory", h)

    def test_estimates_against_targets(self):
        h = self.sc["b1"]["banner"]
        self.assertIn("Estimated bracket", h)
        self.assertIn("Estimated power", h)
        self.assertIn("Upgraded", h)
        self.assertEqual(self.sc["b1"]["brDelta"], {"d": 2, "out": True, "text": "▲ +2 above target"})
        self.assertEqual(self.sc["b1"]["pwDelta"]["out"], True)
        self.assertEqual(self.sc["b1"]["pwDelta"]["text"], "▲ +3.5 · outside ±1.5")
        self.assertIn("6.5", h)
        self.assertIn("target <b>3</b>", h)
        self.assertIn("target <b>1</b>", h)

    def test_gc_row_offers_show_in_list_and_raise_target(self):
        h = self.sc["b1"]["banner"]
        self.assertIn("data-bracket-show-first", h)
        self.assertIn('href="/deck/Holy%20Hellfire/journal?bracket=3#j-power"', h)
        self.assertIn("Raise target to 3", h)

    def test_offending_cards_flagged_hard_wins(self):
        off = self.sc["b1pet"]["offenders"]
        self.assertEqual(sorted(off), ["Demonic Tutor", "Rhystic Study", "Smothering Tithe"])
        self.assertTrue(all(o["sev"] == "hard" for o in off.values()))
        self.assertTrue(off["Rhystic Study"]["pet"])
        cap = self.sc["b1pet"]["captions"]
        self.assertEqual(cap["Demonic Tutor"], "Game Changer · Over Bracket 1")
        self.assertEqual(cap["Rhystic Study"], "Game Changer · pet · Over Bracket 1")
        self.assertIn("Your pet card Rhystic Study", self.sc["b1pet"]["titles"])

    # ---- the other states ---------------------------------------------------------
    def test_clean_state(self):
        b3 = self.sc["b3"]
        self.assertEqual(b3["counts"]["all"], 0)
        self.assertIn("No conflicts with Bracket 3", b3["banner"])
        self.assertIn("is-ok", b3["banner"])
        self.assertEqual(self.sc["b3"]["offenders"], {})
        self.assertIn("No conflicts", b3["chip"])
        self.assertEqual(b3["brDelta"]["text"], "● on target")

    def test_plays_below_is_advisory_only(self):
        b4 = self.sc["b4"]
        self.assertEqual(b4["counts"], {"all": 1, "hard": 0, "adv": 1})
        self.assertEqual(b4["titles"], ["Plays below target"])
        self.assertIn("is-adv", b4["banner"])
        self.assertIn("▼ −1 below target", b4["banner"])

    def test_unset_shows_the_estimate_and_a_link_to_set_one(self):
        u = self.sc["unset"]
        self.assertIn("plays like", u["banner"])
        self.assertIn("Bracket 3", u["banner"])
        self.assertIn('href="/deck/Holy%20Hellfire/journal#j-power"', u["banner"])
        self.assertIn("Est. B3 · Upgraded", u["chip"])
        self.assertEqual(u["offenders"], {})
        self.assertIn("is-quiet", u["banner"])

    def test_not_evaluated_yet(self):
        p = self.out["pending"]
        self.assertEqual(p["state"], "pending")
        self.assertIn("Not evaluated yet", p["banner"])
        self.assertIn("data-bracket-evaluate", p["banner"])
        self.assertIn("Evaluate now", p["banner"])
        self.assertIn("not checked", p["chip"])

    def test_re_evaluating_keeps_the_last_result_and_marks_busy(self):
        r = self.sc["running"]
        self.assertEqual(r["state"], "running")
        self.assertIn('aria-busy="true"', r["banner"])
        self.assertIn("Re-evaluating", r["banner"])
        self.assertIn("3 conflicts", r["banner"])

    def test_failed_eval_falls_back_to_last_result(self):
        f = self.sc["failed"]
        self.assertIn("No conflicts with Bracket 3", f["banner"])
        self.assertIn("Last check failed", f["banner"])
        e = self.out["errOnly"]
        self.assertIn("Last check failed", e["banner"])
        self.assertIn("data-bracket-evaluate", e["banner"])
        self.assertIn("unavailable", self.out["unavailable"]["banner"].lower())

    def test_a_failure_after_a_change_is_not_shown_as_re_evaluating(self):
        # Copilot on PR #104: status=error + stale=true + pending=false is final
        # until the next change or a retry; "Re-evaluating" would never clear.
        s = self.sc["failed_stale"]
        self.assertEqual(s["state"], "ok")
        self.assertNotIn("aria-busy", s["banner"])
        self.assertNotIn("Re-evaluating", s["banner"])
        self.assertIn("Last check failed", s["banner"])
        self.assertIn("data-bracket-evaluate", s["banner"])
        self.assertEqual(self.sc["failed_queued"]["state"], "running")

    # ---- power advisory threshold (engine.POWER_ALIGN_TOLERANCE) ------------------
    def test_power_target_inside_threshold_is_quiet(self):
        s = self.sc["pw_in"]
        self.assertNotIn("Plays above target power", s["titles"])
        self.assertEqual(s["pwDelta"], {"d": 1.5, "out": False, "text": "▲ +1.5 · within ±1.5"})
        self.assertIn("is-in", s["banner"])

    def test_power_target_outside_threshold_is_an_advisory_row(self):
        s = self.sc["pw_out"]
        self.assertIn("Plays above target power", s["titles"])
        self.assertEqual(s["pwDelta"], {"d": 1.6, "out": True, "text": "▲ +1.6 · outside ±1.5"})
        self.assertIn("is-out", s["banner"])

    # ---- explanation tray ----------------------------------------------------------
    def test_how_this_was_decided(self):
        ev = self.S["b1"]["evaluation"]
        w = self.sc["b1"]["why"]
        self.assertIn("<details", w)
        self.assertIn("hud-tray", w)
        self.assertIn("How this was decided", w)
        self.assertIn("%d signals" % len(ev["explanation"]["signals"]), w)
        self.assertIn(ev["explanation"]["summary"].split(" -> ")[0].replace("'", "&#39;"), w)
        for col in ["Signal", "Value", "Effect", "Source"]:
            self.assertIn(">" + col + "<", w)
        self.assertNotIn(">Kind<", w)  # BR-4 signals carry no kind (designer open question)
        self.assertIn("Game Changers", w)  # humanised signal id
        self.assertIn("Hard signals set a floor", w)
        self.assertIn("5484f1", w)  # input hash stamp
        self.assertIn(w, self.sc["b1"]["banner"])

    def test_banner_escapes_api_text(self):
        x = self.out["xss"]
        self.assertNotIn("<img src=x", x)
        self.assertNotIn("<b>x</b>", x)
        self.assertIn("&lt;img src=x", x)

    # ---- BR-7 live-review fixes -------------------------------------------------------
    def test_unset_target_reads_no_target_not_a_dash(self):
        u = self.sc["unset"]["banner"]
        self.assertIn("no target</span>", u)
        self.assertNotIn("target <b>\u2014</b>", u)
        self.assertIn("no bracket rules are checked", u)  # power is still compared, so not "nothing"

    def test_count_led_message_bolds_once(self):
        c = self.out["combo"]
        self.assertIn("<b>2 two-card combos</b> (A + B; C + D)", c)
        self.assertNotIn("Two-card combos.</b>", c)
        self.assertIn("<b>1 two-card combo</b> (A + B)", self.out["combo1"])
        self.assertNotIn("combos.</b>", self.out["combo1"])
        self.assertIn("<b>1 early two-card combo</b> (Spellbook", self.out["early1"])
        self.assertNotIn("combos.</b>", self.out["early1"])

    def test_tray_does_not_repeat_the_reason_and_shows_units(self):
        ev = self.S["b1"]["evaluation"]
        self.assertNotIn(ev["estimated_bracket_reason"].replace("'", "&#39;"), self.sc["b1"]["why"])
        self.assertIn(">$599.10<", self.out["units"])
        self.assertIn(">35%<", self.out["units"])

    # ---- Journal helpers -------------------------------------------------------------
    def test_plain_language_copy_for_each_bracket(self):
        names = [c["name"] for c in self.out["copy"]]
        self.assertEqual(names, [b.name for b in br.BRACKETS])
        self.assertIn("Theme over power", self.out["copy"][0]["line"])
        self.assertIn("Up to 3 Game Changers", self.out["copy"][2]["rules"])

    def test_definition_card(self):
        self.assertIn("Unset", self.out["defs"][0])
        self.assertIn("no bracket checks", self.out["defs"][0])
        self.assertIn("Exhibition", self.out["defs"][1])
        self.assertIn("cEDH", self.out["defs"][5])

    def test_below_floor_note(self):
        w = self.out["warn"]
        self.assertIn("Bracket 3 today", w["b1"])
        self.assertIn("Game Changers", w["b1"])
        self.assertIn("Exhibition", w["b1"])
        self.assertIn("Saving will flag it on the deck page", w["b1"])
        self.assertNotIn("aren't allowed", w["b1"])
        self.assertIn("break the Exhibition rules", w["b1"])
        # Upgraded allows 3 Game Changers: describe the rule break, not a ban.
        self.assertIn("break the Upgraded rules", w["gc4t3"])
        self.assertNotIn("allowed", w["gc4t3"])
        self.assertEqual(w["b3"], "")
        self.assertEqual(w["off"], "")
        self.assertEqual(w["noEval"], "")


class TestDeckPageWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = TestClient(app)
        with patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            cls.html = client.get("/deck/Holy%20Hellfire").text
        cls.body = cls.html.split("<body", 1)[1]
        cls.css = HUD_CSS.read_text()

    def test_loads_brackets_js(self):
        self.assertRegex(self.html, r'<script src="/static/js/brackets\.js\?v=[^"]+"')

    def test_banner_sits_between_tabs_and_content(self):
        order = [self.body.find(m) for m in ('class="hud-tabs hud-deck-tabs"', 'id="bracketBanner"', 'class="ov-split"')]
        self.assertTrue(all(i > 0 for i in order), order)
        self.assertEqual(order, sorted(order))
        self.assertRegex(self.body, r'id="bracketBanner"[^>]*aria-live="polite"')

    def test_header_meta_has_a_bracket_slot(self):
        meta = self.body.split('class="hud-deckhead-meta"', 1)[1].split("</div>", 1)[0]
        self.assertRegex(meta, r'<span id="deckBracket"[^>]*hidden')

    def test_reads_the_stored_result_and_queues(self):
        self.assertIn("/bracket", self.body)
        self.assertIn("archidekt:417707", self.body)
        self.assertIn("Brackets.banner(", self.body)
        self.assertIn("Brackets.offenders(", self.body)
        self.assertIn("'POST'", self.body)

    def test_tolerance_comes_from_the_engine_constant(self):
        self.assertRegex(self.body, r"POWER_TOL = 1\.5\b")
        with patch.object(engine, "POWER_ALIGN_TOLERANCE", 2.25), \
                patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            html = TestClient(app).get("/deck/Holy%20Hellfire").text
        self.assertIn("POWER_TOL = 2.25", html)

    def test_flags_reach_visual_and_list_views(self):
        self.assertIn("hud-bracket-flag", self.body)
        self.assertIn("hud-bracket-flag-why", self.body)
        self.assertIn("hud-bracket-flag--adv", self.body)
        self.assertIn("data-card-name", self.body)
        self.assertIn("flagged ↓", self.body)

    def test_new_hud_classes_are_defined(self):
        js = BR_JS.read_text()
        used = set(re.findall(r"(?<![-\w])(hud-[a-z0-9-]+)", self.body + js))
        defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])

    def test_reduced_motion_fallback_for_the_pulse(self):
        rm = self.css.split("prefers-reduced-motion: reduce")
        self.assertTrue(any("hud-bracket-pulse" in part.split("}\n}", 1)[0] or ".is-pulse" in part.split("}\n}", 1)[0]
                            for part in rm[1:]))

    def test_journal_query_preselect_is_documented_in_links(self):
        self.assertIn("/journal", BR_JS.read_text())


class TestJournalBracketPicker(_JournalPage, unittest.TestCase):
    SAVED = dict(SAVED, target_bracket=2)

    def test_section_is_power_and_bracket(self):
        self.assertRegex(self.body, r'<section class="hud-form-section" id="j-power">\s*<header><h2>Power &amp; bracket</h2>')
        nav = self.body.split('aria-label="Journal sections"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('href="#j-power">Power &amp; bracket</a>', nav)

    def test_seg_has_off_and_1_to_5_after_power(self):
        values = re.findall(r'type="radio" name="bracket" value="([^"]+)"', self.body)
        self.assertEqual(values, ["off", "1", "2", "3", "4", "5"])
        power = self.body.find('name="power"')
        self.assertLess(power, self.body.find('name="bracket"'))
        seg = self.body.split('name="bracket"', 1)[0].rsplit("<span", 1)[1]
        self.assertIn("hud-seg--scale", seg)
        self.assertIn('aria-labelledby="bkL"', seg)
        self.assertIn('aria-describedby="bkDef"', self.body)

    def test_prefilled_from_the_stored_bracket(self):
        self.assertRegex(self.body, r'name="bracket" value="2"[^>]* checked')
        self.assertNotRegex(self.body, r'name="bracket" value="off"[^>]* checked')
        self.assertIn("2 · Core", self.body)

    def test_definition_card_is_live(self):
        self.assertRegex(self.body, r'id="bkDef"[^>]*aria-live="polite"')
        self.assertIn("<b>2 · Core</b>", self.body)  # server-rendered; brackets.js adds the line + rules
        self.assertIn('$("bkDef").innerHTML = Br.definition(', self.body)

    def test_saves_through_the_journal_api(self):
        script = self.body.split("<script>", 1)[1]
        self.assertIn('"target_bracket"', script.split("var KEYS = [", 1)[1].split("]", 1)[0])
        self.assertIn("target_bracket:", script.split("function collect()", 1)[1].split("function setRadio", 1)[0])
        self.assertIn('bracket: ["j-power", ["target_bracket"]]', script)

    def test_reads_the_estimate_and_preselects_from_query(self):
        self.assertIn("/bracket", self.body)
        self.assertIn('params.get("bracket")', self.body)
        self.assertIn("Br.belowFloor(", self.body)
        self.assertRegex(self.body, r"POWER_TOL = 1\.5\b")
        self.assertRegex(self.html, r'<script src="/static/js/brackets\.js\?v=[^"]+"')

    def test_bracket_checkin_row_now_has_an_inline_change(self):
        rows = self.body.split("var ROWS = {", 1)[1].split("};", 1)[0]
        self.assertIn("bracket:", rows)

    def test_anchor_nav_and_deep_links_reach_display_contents_sections(self):
        # BR-7: .hud-form-section is display:contents, so neither #j-power nor
        # an IntersectionObserver on the section works; both go via its header.
        self.assertIn(".hud-form-section { display: contents; }", HUD_CSS.read_text())
        self.assertIn("function goToSection(", self.body)
        self.assertIn('querySelectorAll(".hud-form-section > header").forEach(function (h) { io.observe(h); })', self.body)
        self.assertIn("goToSection(location.hash, false)", self.body)
        self.assertIn("marked <span class=\"hud-bracket-estkey\"", self.body)
        # Copilot on #109: focus moves to the section; modified clicks stay native.
        go = self.body.split("function goToSection(", 1)[1].split("anchors.forEach", 1)[0]
        self.assertIn('head.setAttribute("tabindex", "-1")', go)
        self.assertIn("head.focus({ preventScroll: true })", go)
        self.assertIn("e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;", self.body)
        self.assertIn(".hud-form-section > header:focus-visible", self.html)

    def test_shared_section_shows_only_the_opened_rows_field(self):
        # Copilot on PR #104: Power and Bracket rows both open j-power, but
        # Save change / Cancel only cover that row's keys.
        self.assertRegex(self.body, r'<div class="hud-field" data-row="power">')
        self.assertRegex(self.body, r'<div class="hud-field" data-row="bracket">')
        script = self.body.split("function openChange", 1)[1].split("function saveChange", 1)[0]
        self.assertIn('x.hidden = x.getAttribute("data-row") !== field', script)
        self.assertIn("x.hidden = false", script.split("function closeChange", 1)[1])
        self.assertIn(".hud-field[hidden] { display: none; }", HUD_CSS.read_text())


class TestDeckPagePolling(unittest.TestCase):
    """Copilot on PR #104: polling must follow an explicit request to completion
    and must not rebuild unchanged content (open tray, focus)."""

    @classmethod
    def setUpClass(cls):
        with patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            cls.body = TestClient(app).get("/deck/Holy%20Hellfire").text.split("<body", 1)[1]
        cls.script = cls.body.split("// ---- Bracket evaluation", 1)[1].split("async function loadDeckData", 1)[0]

    def test_request_tracks_completion_by_stamp(self):
        self.assertIn("awaitFrom = stampOf(bracketRes)", self.script)
        self.assertIn("stampOf(bracketRes) !== awaitFrom", self.script)
        self.assertIn("awaitFrom !== null) && bracketPoll < 30", self.script)

    def test_unchanged_results_do_not_rebuild(self):
        self.assertIn("if (key === bracketKey) return;", self.script)
        self.assertIn("if (fk === flagsKey) return;", self.script)
        self.assertIn(".hud-bracket-why[open]", self.script)
        self.assertIn("focus({ preventScroll: true })", self.script)


class TestJournalUnsetBracket(_JournalPage, unittest.TestCase):
    SAVED = SAVED  # no target_bracket

    def test_off_is_checked(self):
        self.assertRegex(self.body, r'name="bracket" value="off"[^>]* checked')
        self.assertIn("No target, so no bracket checks", self.body)


if __name__ == "__main__":
    unittest.main()
