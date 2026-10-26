"""CR-F: Single Card Review UI on the deck page (docs/design/card-review/UX_SPEC.md).

Component tests, same split as the AN/PS page tests:
- the deck page ships the review dialog shell, the Review entry points and the script;
- every hud-cr-* class the view emits is defined in hud.css (no design drift);
- card_review.js runs in node against REAL API payloads (tests/fixtures/card_review_job_*.json were produced
  by the card-review backend with a scripted LLM): the adapter, the view states, and the session
  (start / resume / poll / errors / pick / undo) with a fake fetch.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.server.app.main import app

ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = ROOT / "src/web/templates" / "base.html"
HUD_JS = ROOT / "src/web/static" / "js" / "hud.js"
CR_JS = ROOT / "src/web/static" / "js" / "card_review.js"
HUD_CSS = ROOT / "src/web/static" / "css" / "hud.css"
FIX = ROOT / "tests" / "fixtures"


class TestDeckPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TestClient(app).get("/deck/Holy%20Hellfire").text
        cls.css = HUD_CSS.read_text()
        cls.js = CR_JS.read_text()

    def test_dialog_shell(self):
        self.assertIn('id="cardReviewModal"', self.html)
        for frag in ['role="dialog"', 'aria-modal="true"', "data-cr-title", "data-cr-live", "data-cr-body",
                     'data-cr="back"', "data-hud-close"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.html)
        self.assertRegex(self.html, r'<p class="hud-cr-sr" role="status" aria-live="polite" data-cr-live>')

    def test_script_and_entry_points(self):
        self.assertRegex(self.html, r'<script src="/static/js/card_review\.js\?v=[^"]*" defer>')
        for frag in ["reviewActionRow(card, index)", "data-review-card", "CardReview.mount(", "openReview("]:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.html)
        # Review card is the one primary action in the detail modal, and R is its key.
        self.assertIn('hud-btn hud-btn--primary" data-review-card', self.html)
        self.assertIn("ev.key !== 'r' && ev.key !== 'R'", self.html)

    def test_existing_deck_view_is_intact(self):
        for frag in ['id="cardModal"', "function openCardModal(index)", 'id="decklistContainer"', 'value="visual"',
                     'value="list"', "data-card-index", "loadBracket()", "loadPersonality"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.html)

    def test_every_hud_cr_class_the_view_emits_is_defined(self):
        used = set(re.findall(r"hud-cr-[a-z0-9-]+", self.js))
        defined = set(re.findall(r"\.(hud-cr-[a-z0-9-]+)", self.css))
        # hud-cr-modal is a modifier set on the shell; hud-cr-mark/more/cell are the optional grid markers.
        self.assertEqual(sorted(used - defined), [])

    def test_stylesheet_has_the_design_tokens_and_reduced_motion(self):
        for tok in ["--cr-dialog-w", "--cr-subject-w", "--cr-thumb-w", "--cr-act-w", "--cr-touch", "--cr-keep", "--cr-remove"]:
            with self.subTest(tok=tok):
                self.assertIn(tok + ":", self.css)
        tail = self.css.split("Card review (CR-D", 1)[1].split("*/", 1)[1]
        tail = re.sub(r"/\*.*?\*/", "", tail, flags=re.S)  # comments may mention what the kit bans
        self.assertIn("prefers-reduced-motion: reduce", tail)
        self.assertNotRegex(tail, r"#[0-9a-fA-F]{3,6}\b")        # tokens only: no new colours
        self.assertNotRegex(tail, r"gradient|blur\(|box-shadow: [^;]*\d+px [^;]*\d+px [^;]*\d+px")  # no gradients / glow
        self.assertNotIn(".mock-", tail)

    def test_no_dependency_on_the_mockup(self):
        self.assertNotIn("MOCK", self.js)
        self.assertNotIn("mock.js", self.js)
        self.assertNotRegex(self.js, r"(src|href)=[\"']?[^\"']*docs/design")

    def test_base_html_script_ordering(self):
        base_html = BASE_HTML.read_text()
        hud_pos = base_html.find('/static/js/hud.js')
        extra_js_pos = base_html.find('{% block extra_js %}')
        self.assertNotEqual(hud_pos, -1, "hud.js must be present in base.html")
        self.assertNotEqual(extra_js_pos, -1, "extra_js block must be present in base.html")
        self.assertLess(hud_pos, extra_js_pos, "hud.js must be loaded before extra_js in base.html")

        # In rendered deck HTML, hud.js must appear before card_review.js
        hud_idx = self.html.find('/static/js/hud.js')
        cr_idx = self.html.find('/static/js/card_review.js')
        self.assertNotEqual(hud_idx, -1, "hud.js must appear in rendered deck page")
        self.assertNotEqual(cr_idx, -1, "card_review.js must appear in rendered deck page")
        self.assertLess(hud_idx, cr_idx, "hud.js must precede card_review.js in rendered HTML")


HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [], getElementById: () => null, querySelector: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
eval(fs.readFileSync(process.argv[2], 'utf8'));
const CR = window.CardReview;
const fx = (n) => JSON.parse(fs.readFileSync(process.argv[3] + '/' + n + '.json', 'utf8'));
const removeJob = fx('card_review_job_remove').job, keepJob = fx('card_review_job_keep').job;
const CARD = { name: 'Blasphemous Act', uid: '', cmc: 9, colors: 'R', type: 'Sorcery', slot: 'Sorceries', index: 0 };
const base = (kind) => ({ deck: { kind, name: 'Holy Hellfire' }, card: CARD, tuneUrl: '/deck/Holy%20Hellfire/tune' });
const NOW = Date.parse(removeJob.finished_at) + 5000;
const res = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

// Scripted fetch + a timer queue; the 1s clock tick is ignored, only polls are pumped.
function rig(script, kind, extra) {
  const calls = [], states = [], q = [];
  const f = async (url, opt) => {
    calls.push({ url, method: (opt && opt.method) || 'GET', body: opt && opt.body ? JSON.parse(opt.body) : null });
    const next = script.shift();
    if (!next) throw new Error('unscripted call ' + url);
    if (next instanceof Error) throw next;
    return typeof next === 'function' ? next(url, opt) : next;
  };
  const store = {}, storage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
  Object.assign(store, (extra && extra.store) || {});
  const clock = { t: NOW };
  const s = CR.createSession({ deckId: 'archidekt:1', deckName: 'Holy Hellfire', deckKind: kind, card: CARD, tuneUrl: '/deck/Holy%20Hellfire/tune',
    fetch: f, storage, pollMs: 10, now: () => clock.t,
    setTimeout: (fn, ms) => { const h = { fn, ms }; q.push(h); return h; }, clearTimeout: (h) => { const i = q.indexOf(h); if (i >= 0) q.splice(i, 1); },
    onState: (st) => states.push(JSON.parse(JSON.stringify(st))) });
  const pump = async () => { for (let i = 0; i < 50; i++) { await new Promise(r => setImmediate(r)); const h = q.find(x => x.ms !== 1000); if (!h) break; q.splice(q.indexOf(h), 1); clock.t += 3000; await h.fn(); } await new Promise(r => setImmediate(r)); };
  return { s, calls, states, store, pump, clock, last: () => states[states.length - 1] };
}
const partial = (extra) => Object.assign({}, removeJob, { status: 'running', stage: 'turn', result: undefined, transcript: removeJob.transcript.slice(0, 2), finished_at: undefined, created_at: new Date(NOW - 4000).toISOString() }, extra || {});

(async () => {
  const out = {};
  // ---- adapter ----
  const done = CR.fromJob(removeJob, base('digital'), NOW), doneP = CR.fromJob(removeJob, base('physical'), NOW), keep = CR.fromJob(keepJob, base('digital'), NOW);
  out.done = { phase: done.phase, ruling: done.verdict.ruling, conf: done.verdict.confidence, strength: done.verdict.strength, median: done.verdict.deck_median,
    line: done.verdict.judge_line, role: done.verdict.role, signals: done.verdict.signals, notes: done.verdict.notes.map(n => n.text), reviewed: done.verdict.reviewed,
    remReasons: done.verdict.reasons.remove, keepReasons: done.verdict.reasons.keep,
    repl: done.repl.items.map(i => ({ n: i.name, k: i.kind, o: i.ownership, uid: i.uid })), replP: doneP.repl.items.map(i => i.ownership) };
  out.keep = { ruling: keep.verdict.ruling, repl: keep.repl.state };
  const run1 = CR.fromJob(partial({ stage: 'round_start', transcript: [] }), base('digital'), NOW), run2 = CR.fromJob(partial(), base('digital'), NOW);
  const run3 = CR.fromJob(partial({ stage: 'judging', transcript: removeJob.transcript }), base('digital'), NOW);
  out.running = [run1, run2, run3].map(r => ({ phase: r.phase, step: r.run.step, args: r.args.length, sides: r.args.map(a => a.side + ':' + a.round), ev: r.evidence.map(e => e.label + '=' + (e.main || '')), elapsed: r.run.elapsed }));
  const fails = {
    offline: CR.fromJob({ status: 'failed', error_kind: 'OracleUnavailable', error: 'x', transcript: [], events: [] }, base('digital'), NOW),
    internal: CR.fromJob({ status: 'failed', error_kind: 'internal', error: 'boom', transcript: [], events: [] }, base('digital'), NOW),
    gone: CR.fromJob({ status: 'failed', error_kind: 'UnknownCardError', error: 'x', transcript: [], events: [] }, base('digital'), NOW),
    notAllowed: CR.fromJob({ status: 'failed', error_kind: 'not_allowed', error: 'x', transcript: [], events: [] }, base('digital'), NOW),
  };
  out.fails = Object.fromEntries(Object.entries(fails).map(([k, v]) => [k, [v.phase, v.error && v.error.code]]));
  const emptyRes = JSON.parse(JSON.stringify(removeJob)); emptyRes.result.replacements = []; emptyRes.result.replacement_meta = { dropped: [{ card: 'X', reason: 'y' }], pool_exhausted: true };
  out.empty = CR.fromJob(emptyRes, base('digital'), NOW).repl;
  const prov = JSON.parse(JSON.stringify(removeJob)); prov.result.provisional = true; prov.result.flags = ['edhrec_data_missing', 'journal_unconfirmed']; prov.result.fallback = true;
  out.notes = CR.fromJob(prov, base('digital'), NOW).verdict.notes.map(n => n.text);
  const pet = JSON.parse(JSON.stringify(keepJob)); pet.result.gate = 'pet_card'; out.pet = CR.fromJob(pet, base('digital'), NOW).verdict.notes.map(n => n.text);

  // ---- view ----
  const html = (s) => CR.render(s);
  const evil = JSON.parse(JSON.stringify(removeJob)); evil.result.key_reasons = ['<img src=x onerror=alert(1)> [S1]']; evil.result.replacements[0].card = '"><script>x</script>';
  const evilHtml = html(CR.fromJob(evil, base('digital'), NOW));
  out.view = {
    verdictD: html(done), verdictP: html(doneP), keep: html(keep), run: html(run2), run3: html(run3),
    slow: html(CR.fromJob(partial({ created_at: new Date(NOW - 130000).toISOString() }), base('digital'), NOW)),
    empty: html(Object.assign({}, done, { repl: CR.fromJob(emptyRes, base('digital'), NOW).repl })),
    err: Object.fromEntries(['offline', 'internal', 'gone'].map(k => [k, html(fails[k])])),
    network: html(Object.assign({}, run2, { phase: 'error', error: { code: 'network' } })),
    blocked: html(Object.assign({}, run2, { phase: 'blocked', blocked: { h: 'No quick look for commanders', p: 'p' } })),
    chosenD: html(Object.assign({}, done, { repl: Object.assign({}, done.repl, { chosen: done.repl.items[0].name }) })),
    chosenP: html(Object.assign({}, doneP, { repl: Object.assign({}, doneP.repl, { chosen: doneP.repl.items[0].name }) })),
    actionErr: html(Object.assign({}, done, { repl: Object.assign({}, done.repl, { actionError: 'nope' }) })),
    evil: evilHtml,
    labels: [CR.actionLabel('digital', { ownership: 'not_owned' }), CR.actionLabel('physical', { ownership: 'not_owned' }), CR.actionLabel('physical', { ownership: 'owned_free' })],
    decisions: [CR.decisionFor('digital', { ownership: 'not_owned' }), CR.decisionFor('physical', { ownership: 'not_owned' }), CR.decisionFor('physical', { ownership: 'owned_free' })],
  };
  out.unrev = [CR.unreviewable({ is_commander: true }), CR.unreviewable({ categories: ['Commander'] }), CR.unreviewable({ type: 'Basic Land — Mountain' }),
    CR.unreviewable({ categories: ['Maybeboard'] }), CR.unreviewable({ type: 'Sorcery', categories: ['Removal'] })].map(x => x && x.h);

  // ---- session ----
  const SU = '/api/decks/archidekt%3A1/cards/Blasphemous%20Act/review';
  {   // happy path, digital: POST 202 -> running -> running -> done; pick; undo
    const t = rig([res(202, { job_id: 'abc123def456' }), res(200, { job: partial({ transcript: [] }) }), res(200, { job: partial() }), res(200, { job: removeJob }),
                   res(200, { success: true, state: {} }), res(200, { success: true, state: {} })], 'digital');
    await t.s.start(false); await t.pump();
    const fin = t.last();
    await t.s.handle('pick', fin.repl.items[1].name); const picked = t.last().repl.chosen;
    await t.s.handle('undo'); const undone = t.last().repl.chosen;
    out.happy = { calls: t.calls, phases: t.states.map(s => s.phase + (s.run ? ':' + s.run.step : '')), saved: t.store['cr:job:archidekt:1:blasphemous act'], picked, undone, live: fin.live };
  }
  {   // physical: owned -> own, not owned -> buy
    const ownedJob = JSON.parse(JSON.stringify(removeJob)); ownedJob.result.replacements[0].tier = 'owned';
    const t = rig([res(202, { job_id: 'p1p1p1p1' }), res(200, { job: ownedJob }), res(200, {}), res(200, {})], 'physical');
    await t.s.start(false); await t.pump();
    const items = t.last().repl.items;
    await t.s.handle('pick', items[1].name); await t.s.handle('pick', items[0].name);
    out.phys = { puts: t.calls.filter(c => c.method === 'PUT').map(c => [c.url, c.body]) , kind: t.last().deck.kind, chosen: t.last().repl.chosen };
  }
  {   // pick failure keeps nothing chosen and shows the reason
    const t = rig([res(202, { job_id: 'f1f1f1f1' }), res(200, { job: removeJob }), res(400, { success: false, error: 'accepting a swap needs the add card' })], 'digital');
    await t.s.start(false); await t.pump(); await t.s.handle('pick', t.last().repl.items[0].name);
    out.pickFail = { chosen: t.last().repl.chosen, err: t.last().repl.actionError };
  }
  {   // skip / restore are local only
    const t = rig([res(202, { job_id: 'k1k1k1k1' }), res(200, { job: removeJob })], 'digital');
    await t.s.start(false); await t.pump(); const n = t.last().repl.items[0].name;
    t.s.handle('skip', n); const a = t.last().repl.skipped.slice(); t.s.handle('unskip', n);
    out.skip = { a, b: t.last().repl.skipped, calls: t.calls.length };
  }
  {   // 409 "already running" resumes the existing job
    const t = rig([res(409, { success: false, error: 'a review of this card is already running (job 0123456789ab)', error_kind: 'busy' }), res(200, { job: removeJob })], 'digital');
    await t.s.start(false); await t.pump();
    out.resume = { urls: t.calls.map(c => c.method + ' ' + c.url), phase: t.last().phase, saved: t.store['cr:job:archidekt:1:blasphemous act'] };
  }
  {   // reopen: a saved job id is polled instead of POSTing again
    const t = rig([res(200, { job: removeJob })], 'digital', { store: { 'cr:job:archidekt:1:blasphemous act': 'deadbeef00' } });
    await t.s.start(false); await t.pump();
    out.reopen = { urls: t.calls.map(c => c.method + ' ' + c.url), phase: t.last().phase };
  }
  {   // saved job expired (404) -> a fresh POST
    const t = rig([res(404, { success: false }), res(202, { job_id: 'new0new0new0' }), res(200, { job: removeJob })], 'digital', { store: { 'cr:job:archidekt:1:blasphemous act': 'old' } });
    await t.s.start(false); await t.pump();
    out.expired = { urls: t.calls.map(c => c.method + ' ' + c.url.split('/review')[1]), phase: t.last().phase, saved: t.store['cr:job:archidekt:1:blasphemous act'] };
  }
  {   // rerun ignores the saved job
    const t = rig([res(202, { job_id: 'again00000' }), res(200, { job: removeJob })], 'digital', { store: { 'cr:job:archidekt:1:blasphemous act': 'old' } });
    t.s.handle('retry'); await t.pump();
    out.rerun = { first: t.calls[0].method };
  }
  const starts = {};
  for (const [k, r] of Object.entries({ offline: res(503, { error: 'x' }), gone: res(404, { error: 'x' }), busy429: res(429, { error: 'x' }), busy409: res(409, { error: 'x' }),
                                         blocked: res(400, { error: 'commander' }), bad: res(422, { error: 'rounds must be 2 or 3' }), net: new Error('down') })) {
    const t = rig([r], 'digital'); await t.s.start(false); const l = t.last();
    starts[k] = [l.phase, l.error && l.error.code, l.blocked && l.blocked.h];
  }
  out.starts = starts;
  {   // network drop mid-poll, then Reconnect resumes the same job
    const t = rig([res(202, { job_id: 'net1net1net1' }), res(200, { job: partial() }), new Error('down'), res(200, { job: removeJob })], 'digital');
    await t.s.start(false); await t.pump(); const drop = t.last().error && t.last().error.code;
    await t.s.handle('reconnect'); await t.pump();
    out.reconnect = { drop, urls: t.calls.slice(2).map(c => c.url.split('/jobs/')[1]), phase: t.last().phase };
  }
  {   // server reports the job failed
    const t = rig([res(202, { job_id: 'fail1fail1' }), res(200, { job: { status: 'failed', error_kind: 'internal', error: 'boom', transcript: removeJob.transcript, events: [], rounds: 2, created_at: new Date(NOW).toISOString() } })], 'digital');
    await t.s.start(false); await t.pump();
    out.jobFailed = { phase: t.last().phase, code: t.last().error.code, msg: t.last().error.message, args: t.last().args.length, html: CR.render(t.last()) };
  }
  {   // gives up after 10 minutes of "running"
    const t = rig([res(202, { job_id: 'slowslowslow' })].concat(Array.from({ length: 400 }, () => res(200, { job: partial({ created_at: new Date(NOW).toISOString() }) }))), 'digital');
    await t.s.start(true);
    for (let i = 0; i < 300 && t.last().phase === 'running'; i++) await t.pump();
    out.giveUp = t.last().phase + ':' + (t.last().error && t.last().error.code);
  }
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is required for the component tests")
class TestCardReviewComponent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", HARNESS, str(HUD_JS), str(CR_JS), str(FIX)],
                             capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.o = json.loads(res.stdout)
        cls.v = cls.o["view"]

    # ---- adapter ------------------------------------------------------------------------------
    def test_done_remove_maps_the_api_result_to_the_verdict(self):
        d = self.o["done"]
        self.assertEqual((d["phase"], d["ruling"]), ("verdict", "remove"))
        self.assertAlmostEqual(d["conf"], 0.89)
        self.assertEqual(d["strength"], 34)
        self.assertIsNone(d["median"])  # the API has no deck median; the view omits it rather than invent one
        self.assertEqual(d["line"], "Remove. It fights the goblin pillar.")  # [IDs] move to the cite chip
        self.assertEqual(d["role"]["label"], "Board wipe")
        self.assertEqual(d["remReasons"][0], {"text": "It fights the goblin pillar.", "cite": "J2, S1"})
        self.assertTrue(d["keepReasons"])  # the losing side still shows what it argued
        self.assertEqual([s["label"] for s in d["signals"]], ["EDHREC inclusion", "Synergy", "Role need"])
        self.assertTrue(all(0 <= s["v"] <= 1 for s in d["signals"]))
        self.assertEqual(d["reviewed"], "just now")

    def test_replacements_map_kind_and_ownership(self):
        d = self.o["done"]
        self.assertEqual([r["n"] for r in d["repl"]], ["Anger of the Gods", "Chaos Warp", "Gempalm Incinerator"])
        self.assertEqual([r["k"] for r in d["repl"]], ["like_for_like", "function_change", "function_change"])
        self.assertEqual(d["replP"], ["not_owned"] * 3)  # tier 'buy'

    def test_keep_has_no_replacements(self):
        self.assertEqual(self.o["keep"], {"ruling": "keep", "repl": "hidden"})
        self.assertIn("Keep Blasphemous Act. No replacement needed.", self.v["keep"])
        self.assertNotIn("data-cr=\"pick\"", self.v["keep"])

    def test_running_steps_follow_the_job_stage(self):
        r1, r2, r3 = self.o["running"]
        self.assertEqual((r1["step"], r1["args"]), (0, 0))
        self.assertEqual(r2["step"], 1)  # round 1 turns only
        self.assertEqual(r2["sides"], ["remove:opening", "keep:opening"])
        self.assertEqual(r3["step"], 3)
        self.assertEqual(r3["args"], 4)
        self.assertIn("remove:rebuttal", r3["sides"])
        self.assertTrue(any(e.startswith("Strength=34") for e in r2["ev"]))
        self.assertEqual(r2["elapsed"], 4)

    def test_failures_map_to_the_designed_error_states(self):
        self.assertEqual(self.o["fails"], {"offline": ["error", "offline"], "internal": ["error", "failed"],
                                           "gone": ["error", "gone"], "notAllowed": ["blocked", None]})

    def test_empty_replacements_and_notes(self):
        self.assertEqual(self.o["empty"]["state"], "empty")
        self.assertIn("1 candidates were considered", self.o["empty"]["reason"])
        joined = " | ".join(self.o["notes"])
        for frag in ["No EDHREC data", "Provisional", "leans on the strength score"]:
            self.assertIn(frag, joined)
        self.assertTrue(any("pet" in n for n in self.o["pet"]))

    def test_unreviewable_cards(self):
        h = self.o["unrev"]
        self.assertEqual(h[:2], ["No quick look for commanders"] * 2)
        self.assertEqual(h[2:4], ["No quick look for basic lands", "Not in the 99"])
        self.assertIsNone(h[4])

    # ---- view ---------------------------------------------------------------------------------
    def test_verdict_view_matches_the_design(self):
        h = self.v["verdictD"]
        for frag in ['class="hud-cr-verdict"', 'data-ruling="remove"', "<span>Remove</span><small>High confidence</small>", "89<small>%</small>",
                     "34<small> / 100 · Weak</small>", 'role="img" aria-label="Strength 34 of 100"', "Case to keep", "Case to remove", "Ruled for", "Overruled",
                     "Replace Blasphemous Act · 3", "<b>1</b> like-for-like · <b>2</b> function change", "= Like-for-like", "↻ Function change",
                     "Swap into deck", 'id="crList"', "Digital deck. Suggestions queue onto the Tune change list"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, h)
        self.assertNotIn("Deck median", h)                    # no invented median
        self.assertEqual(h.count('data-cr="pick"'), 3)
        self.assertNotIn("Show more", h)                      # no widen/more: the API has no such call
        self.assertEqual(h.count("is-current"), 1)            # one amber action at a time

    def test_physical_deck_uses_want_buy_labels_and_ownership(self):
        h = self.v["verdictP"]
        self.assertIn("Add to want/buy list", h)
        self.assertNotIn("Swap into deck", h)
        self.assertIn("○ Not owned", h)
        self.assertIn("Physical deck. Suggestions go to your want/buy list", h)
        self.assertEqual(self.v["labels"], ["Swap into deck", "Add to want/buy list", "Pull from collection"])

    def test_tune_decision_per_deck_kind(self):
        # Tune's own/buy split: digital -> own; physical owned -> own; physical not owned -> buy.
        self.assertEqual(self.v["decisions"], ["own", "buy", "own"])

    def test_chosen_row_confirms_and_links_to_tune(self):
        d, p = self.v["chosenD"], self.v["chosenP"]
        self.assertIn("queued on the change list.", d)
        self.assertIn("added to your want/buy list.", p)
        for h in (d, p):
            self.assertIn('href="/deck/Holy%20Hellfire/tune"', h)
            self.assertIn('data-cr="undo"', h)
            self.assertIn("Use this instead", h)
            self.assertNotIn("is-current", h)
        self.assertIn("Queued", d)
        self.assertIn("On list", p)

    def test_running_view_has_stepper_clock_evidence_and_both_sides(self):
        h = self.v["run"]
        for frag in ['class="hud-job"', 'aria-label="Quick look progress"', 'aria-current="step"', "hud-job-clock", "00:04", "Quick look: Blasphemous Act",
                     'data-side="keep"', 'data-side="remove"', "Opening", "Closing this window does not cancel the quick look.", "hud-cr-evidence", "Waiting"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, h)
        self.assertNotIn("Cancel review", h)  # the API has no cancel
        self.assertNotIn("Taking longer than usual", h)
        self.assertIn("Taking longer than usual", self.v["slow"])
        self.assertIn("Deliberating", self.v["run3"])

    def test_empty_state(self):
        h = self.v["empty"]
        self.assertIn("No better options found", h)
        self.assertIn("1 candidates were considered", h)
        self.assertIn('data-cr="retry"', h)
        self.assertNotIn('data-cr="pick"', h)
        self.assertIn("hud-cr-verdict", h)  # the ruling stays

    def test_error_states_are_inline_alerts_with_a_retry(self):
        e = self.v["err"]
        self.assertIn("Quick look is offline", e["offline"])
        self.assertIn("Nothing was evaluated and nothing was changed", e["offline"])
        self.assertIn('role="alert"', e["offline"])
        self.assertIn('data-cr="retry"', e["offline"])
        self.assertIn("The quick look failed", e["internal"])
        self.assertIn("This card is no longer in the deck", e["gone"])
        self.assertIn('data-cr="close"', e["gone"])
        self.assertIn("Connection lost", self.v["network"])
        self.assertIn('data-cr="reconnect"', self.v["network"])
        self.assertIn("hud-cr-subject", e["offline"])  # the card never disappears

    def test_blocked_state(self):
        self.assertIn("No quick look for commanders", self.v["blocked"])
        self.assertIn("Back to the deck", self.v["blocked"])

    def test_action_error_is_inline_and_leaves_the_list(self):
        self.assertIn('role="alert"><span>nope</span>', self.v["actionErr"])
        self.assertIn('data-cr="pick"', self.v["actionErr"])

    def test_model_text_is_escaped(self):
        h = self.v["evil"]
        self.assertNotIn("<img src=x", h)
        self.assertNotIn("<script>x</script>", h)
        self.assertIn("&lt;img src=x", h)

    # ---- session ------------------------------------------------------------------------------
    def test_happy_path_polls_to_a_verdict_and_queues_on_tune(self):
        h = self.o["happy"]
        calls = [(c["method"], c["url"]) for c in h["calls"]]
        base = "/api/decks/archidekt%3A1/cards/Blasphemous%20Act/review"
        self.assertEqual(calls[0], ("POST", base))
        self.assertEqual(calls[1], ("GET", base + "/jobs/abc123def456"))
        self.assertEqual(h["phases"][-1], "verdict")
        self.assertIn("running:0", h["phases"])
        self.assertEqual(h["saved"], "abc123def456")
        self.assertIn("Verdict: remove, 89 percent confidence. 3 replacement options.", h["live"])
        put, undo = h["calls"][-2], h["calls"][-1]
        self.assertEqual((put["method"], put["url"]), ("PUT", "/api/decks/archidekt%3A1/tune/decisions"))
        self.assertEqual(put["body"], {"cut": "Blasphemous Act", "decision": "own", "add": "Chaos Warp"})
        self.assertEqual(undo["body"]["decision"], None)
        self.assertEqual(undo["body"]["cut"], "Blasphemous Act")
        self.assertEqual((h["picked"], h["undone"]), ("Chaos Warp", None))

    def test_physical_pick_uses_own_for_owned_and_buy_for_the_rest(self):
        p = self.o["phys"]
        self.assertEqual([b["decision"] for _, b in p["puts"]], ["buy", "own"])
        self.assertEqual([b["add"] for _, b in p["puts"]], ["Chaos Warp", "Anger of the Gods"])
        self.assertEqual(p["kind"], "physical")

    def test_failed_pick_shows_the_reason_and_does_not_mark_chosen(self):
        self.assertEqual(self.o["pickFail"], {"chosen": None, "err": "accepting a swap needs the add card"})

    def test_skip_is_local(self):
        s = self.o["skip"]
        self.assertEqual((s["a"], s["b"]), (["Anger of the Gods"], []))
        self.assertEqual(s["calls"], 2)  # POST + one poll, no write

    def test_already_running_resumes_that_job(self):
        r = self.o["resume"]
        self.assertEqual(r["urls"][1].split("/jobs/")[1], "0123456789ab")
        self.assertEqual((r["phase"], r["saved"]), ("verdict", "0123456789ab"))

    def test_reopening_polls_the_saved_job_without_a_new_post(self):
        r = self.o["reopen"]
        self.assertEqual(len(r["urls"]), 1)
        self.assertTrue(r["urls"][0].startswith("GET ") and r["urls"][0].endswith("/jobs/deadbeef00"))
        self.assertEqual(r["phase"], "verdict")

    def test_expired_saved_job_starts_fresh(self):
        e = self.o["expired"]
        self.assertEqual(e["urls"][0], "GET /jobs/old")
        self.assertEqual(e["urls"][1], "POST ")
        self.assertEqual((e["phase"], e["saved"]), ("verdict", "new0new0new0"))

    def test_rerun_always_posts(self):
        self.assertEqual(self.o["rerun"]["first"], "POST")

    def test_start_errors_map_to_states(self):
        self.assertEqual(self.o["starts"], {
            "offline": ["error", "offline", None], "gone": ["error", "gone", None], "busy429": ["error", "busy", None],
            "busy409": ["error", "busy", None], "blocked": ["blocked", None, "No quick look for commanders"],
            "bad": ["error", "failed", None], "net": ["error", "network", None]})

    def test_connection_drop_then_reconnect_resumes_the_same_job(self):
        r = self.o["reconnect"]
        self.assertEqual(r["drop"], "network")
        self.assertEqual(r["urls"], ["net1net1net1", "net1net1net1"])
        self.assertEqual(r["phase"], "verdict")

    def test_server_side_failure_keeps_the_debate_on_screen(self):
        j = self.o["jobFailed"]
        self.assertEqual((j["phase"], j["code"], j["msg"], j["args"]), ("error", "failed", "boom", 4))
        self.assertIn("The quick look failed", j["html"])
        self.assertIn("Cuts against the pillar.", j["html"])
        self.assertIn("No ruling", j["html"])

    def test_gives_up_after_ten_minutes(self):
        self.assertEqual(self.o["giveUp"], "error:timeout")

    def test_card_review_evaluated_before_hud_attached(self):
        """Simulate script evaluation order where card_review.js is evaluated
        before window.HUD is attached, verifying open() successfully invokes
        window.HUD.openModal without throwing TypeError."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        eval_script = r"""
const fs = require('fs');
const calls = [];
const modalElement = {
    id: 'cardReviewModal',
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    classList: { contains: () => false, add: () => {}, remove: () => {} },
    setAttribute: () => {},
    getAttribute: () => null
};
global.document = {
    readyState: 'complete',
    addEventListener: () => {},
    querySelectorAll: () => [],
    getElementById: (id) => (id === 'cardReviewModal' ? modalElement : null),
    querySelector: () => null,
    contains: () => false,
    activeElement: null
};
global.window = {
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: async () => ({ ok: true, status: 202, json: async () => ({ job_id: 'test_job' }) })
};

// 1. Ensure window.HUD is not yet attached when card_review.js is evaluated
delete global.window.HUD;

// 2. Evaluate card_review.js first
eval(fs.readFileSync(process.argv[1], 'utf8'));

// 3. Attach window.HUD afterwards
global.window.HUD = {
    openModal: function (id) {
        calls.push({ method: 'openModal', arg: id });
    },
    closeModal: function (id) {
        calls.push({ method: 'closeModal', arg: id });
    },
    chip: function (t) { return '<span>' + t + '</span>'; },
    pips: function () { return ''; },
    scryfall: function () { return { src: '', fallback: '' }; },
    art: function () { return ''; },
    statusChip: function () { return ''; },
    esc: function (s) { return String(s); }
};

// 4. Mount CardReview and invoke open()
const cr = global.window.CardReview.mount({
    modalId: 'cardReviewModal',
    deckId: 'archidekt:1',
    deckName: 'Holy Hellfire',
    deckKind: 'digital'
});

try {
    cr.open({ name: 'Blasphemous Act', uid: '123', cmc: 9, type: 'Sorcery' }, 0);
    process.stdout.write(JSON.stringify({ error: null, calls: calls }));
} catch (err) {
    process.stdout.write(JSON.stringify({ error: { name: err.name, message: err.message, stack: err.stack }, calls: calls }));
}
"""
        res = subprocess.run([node, "-e", eval_script, str(CR_JS)],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"node execution failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsNone(data["error"], f"open() threw an error: {data['error']}")
        open_modal_calls = [c for c in data["calls"] if c["method"] == "openModal"]
        self.assertEqual(len(open_modal_calls), 1)
        self.assertEqual(open_modal_calls[0]["arg"], "cardReviewModal")

    def test_card_review_evaluated_before_real_hud_js(self):
        """Simulate evaluating card_review.js first, then hud.js, verifying
        window.HUD.openModal opens the modal without TypeError."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        eval_script = r"""
const fs = require('fs');
const classes = new Set();
const modalElement = {
    id: 'cardReviewModal',
    addEventListener: () => {},
    removeEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    classList: {
        contains: (c) => classes.has(c),
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c)
    },
    setAttribute: () => {},
    getAttribute: () => null
};
global.document = {
    readyState: 'complete',
    addEventListener: () => {},
    querySelectorAll: () => [],
    getElementById: (id) => (id === 'cardReviewModal' ? modalElement : null),
    querySelector: () => null,
    contains: () => false,
    activeElement: null
};
global.window = {
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: async () => ({ ok: true, status: 202, json: async () => ({ job_id: 'test_job' }) })
};

// 1. Evaluate card_review.js BEFORE hud.js
eval(fs.readFileSync(process.argv[1], 'utf8'));

// 2. Evaluate hud.js
eval(fs.readFileSync(process.argv[2], 'utf8'));

const opened = [];
const origOpen = global.window.HUD.openModal;
global.window.HUD.openModal = function (id) {
    opened.push(id);
    return origOpen(id);
};

const cr = global.window.CardReview.mount({
    modalId: 'cardReviewModal',
    deckId: 'archidekt:1',
    deckName: 'Holy Hellfire',
    deckKind: 'digital'
});

try {
    cr.open({ name: 'Blasphemous Act', uid: '123', cmc: 9, type: 'Sorcery' }, 0);
    process.stdout.write(JSON.stringify({
        error: null,
        opened: opened,
        isOpen: classes.has('is-open')
    }));
} catch (err) {
    process.stdout.write(JSON.stringify({
        error: { name: err.name, message: err.message },
        opened: opened
    }));
}
"""
        res = subprocess.run([node, "-e", eval_script, str(CR_JS), str(HUD_JS)],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"node execution failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsNone(data["error"], f"open() threw an error: {data['error']}")
        self.assertEqual(data["opened"], ["cardReviewModal"])
        self.assertTrue(data["isOpen"])


if __name__ == "__main__":
    unittest.main()
