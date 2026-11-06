"""Update / Push controls replacing "Sync to External" (static/js/sync_controls.js).

Template checks: the old button and its no-op are gone, nothing sync-related
runs on a timer/poll, the confirm dialog + script are wired in.
Behaviour checks run the real module in node against a fake fetch/DOM:
- Update and Push each go click -> dry_run POST -> confirm dialog -> real POST;
- no write request is ever sent without the confirm click (cancel = no write);
- labels/dialogs name the provider and the deck that will be overwritten;
- unsupported push, no linked deck, loading, success, error, timestamps.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SC_JS = ROOT / "src/web/static" / "js" / "sync_controls.js"
DECK_HTML = (ROOT / "src/web/templates" / "deck.html").read_text()
DECKS_HTML = (ROOT / "src/web/templates" / "decks.html").read_text()


class TestTemplates(unittest.TestCase):
    def test_sync_to_external_button_is_gone(self):
        for html in (DECK_HTML, DECKS_HTML):
            self.assertNotIn("Sync to External", html)
            self.assertNotIn("syncDeck", html)
            self.assertNotIn("/sync'", html)
            self.assertNotRegex(html, r"fetch\([^)]*/sync`")

    def test_deck_page_hosts_controls_and_confirm_dialog(self):
        self.assertIn('id="syncControls"', DECK_HTML)
        self.assertIn('id="syncConfirmModal"', DECK_HTML)
        self.assertIn("data-sync-confirm-go", DECK_HTML)
        self.assertIn("/static/js/sync_controls.js", DECK_HTML)

    def test_no_automatic_trigger_in_page(self):
        # the only sync call site is initSyncControls -> refresh() (GET status)
        body = DECK_HTML[DECK_HTML.index("function initSyncControls"):]
        body = body[:body.index("DOMContentLoaded', initSyncControls);")]
        self.assertIn("sc.refresh()", body)
        for bad in ("request(", "confirm(", "setInterval", "setTimeout", "/sync/pull", "/sync/push"):
            self.assertNotIn(bad, body)

    def test_module_has_no_timers_except_button_flash(self):
        js = SC_JS.read_text()
        self.assertNotIn("setInterval", js)
        self.assertEqual(len(re.findall(r"setTimeout", js)), 1)  # 2s success/fail flash only

    def test_decks_modal_links_to_deck_page_instead_of_syncing(self):
        self.assertIn('id="modalSyncLink"', DECKS_HTML)


HARNESS = r"""
const fs = require('fs');
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const SC = window.SyncControls;

function el() {
  const handlers = {};
  return { innerHTML: '', textContent: '', className: '', handlers,
    addEventListener(t, f) { handlers[t] = f; },
    querySelector() { return this._qs; }, closest() { return null } };
}
function setup(statusBody, respond) {
  const calls = [];
  const fetch = async (url, init) => {
    init = init || {};
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url, method: init.method, body });
    if (url.endsWith('/sync/status')) return { ok: true, status: 200, json: async () => statusBody };
    return respond(url, body);
  };
  const root = el(), modal = el();
  const bodyEl = el(), go = el();
  modal._qs = null;
  modal.querySelector = (sel) => sel.includes('body') ? bodyEl : go;
  const toasts = [], opened = [], closed = [], changed = [];
  const c = SC.mount({ root, modal, deckName: 'Holy Hellfire', registryId: 'archidekt:42', fetch,
    flashMs: 1, onToast: (m, k) => toasts.push([m, k]), openModal: () => opened.push(1), closeModal: () => closed.push(1),
    onChanged: () => changed.push(1), now: () => Date.parse('2026-10-16T20:00:00Z') });
  return { c, calls, root, modal, bodyEl, go, toasts, opened, closed, changed };
}
const STATUS = { success: true, providers: [
  { provider: 'archidekt', linked: true, identifier: '42', can_update: true, can_push: true,
    last_updated_at: '2026-10-16T18:00:00Z', last_pushed_at: null },
  { provider: 'moxfield', linked: true, identifier: 'abc', can_update: true, can_push: false,
    push_unavailable_reason: 'moxfield push is not configured on this server', last_updated_at: null, last_pushed_at: '2026-10-16T19:59:50Z' },
  { provider: 'commandersalt', linked: true, identifier: 'x', can_update: false, can_push: false } ] };
const CHANGES = { added: [{ name: 'Rhystic Study', quantity: 1 }], removed: [{ name: 'Cancel', quantity: 1 }], changed: [{ name: 'Island', from: 10, to: 9 }] };
const ok = (op) => async (url, body) => ({ ok: true, status: 200, json: async () => ({ success: true, operation: op, dry_run: body.dry_run, changes: CHANGES }) });
const out = {};

(async () => {
  // --- view model / render ------------------------------------------------
  const vm = SC.viewModel(STATUS);
  out.providers = vm.providers.map(p => p.provider);
  out.none = SC.panelHtml(SC.viewModel({ providers: [{ provider: 'archidekt', linked: false }] }));
  out.panel = SC.panelHtml(vm, {}, Date.parse('2026-10-16T20:00:00Z'));
  out.busy = SC.buttonHtml('update', vm.providers[0], 'busy') + SC.buttonHtml('push', vm.providers[0], 'busy');
  out.done = SC.buttonHtml('update', vm.providers[0], 'done') + SC.buttonHtml('push', vm.providers[0], 'done');
  out.failed = SC.buttonHtml('update', vm.providers[0], 'failed') + SC.buttonHtml('push', vm.providers[0], 'failed');
  out.confirmUpdate = SC.confirmHtml('update', 'Archidekt', 'Holy Hellfire', CHANGES);
  out.confirmPush = SC.confirmHtml('push', 'Moxfield', 'Holy <Hellfire>', CHANGES);
  out.ago = [SC.ago(null), SC.ago('2026-10-16T19:59:50Z', Date.parse('2026-10-16T20:00:00Z')), SC.ago('2026-10-16T18:00:00Z', Date.parse('2026-10-16T20:00:00Z'))];

  // --- mount: only GET status happens on load -------------------------------
  let t = setup(STATUS, ok('update'));
  await t.c.refresh();
  out.loadCalls = t.calls.map(c => c.method + ' ' + c.url);

  // --- Update: click -> dry run -> dialog; nothing written yet --------------
  await t.c.request('update', 'archidekt');
  out.updateAfterClick = t.calls.slice(1).map(c => ({ url: c.url, body: c.body }));
  out.updateDialog = t.bodyEl.innerHTML; out.updateGo = t.go.textContent; out.opened = t.opened.length;
  // confirm -> real write
  await t.c.confirm();
  out.updateAfterConfirm = t.calls.slice(1).map(c => ({ url: c.url, body: c.body }));
  out.updateToast = t.toasts; out.updateChanged = t.changed.length;
  out.afterUpdateRoot = t.root.innerHTML;
  await new Promise(r => setTimeout(r, 20));

  // --- Cancel = no write -----------------------------------------------------
  t = setup(STATUS, ok('push'));
  await t.c.refresh();
  await t.c.request('push', 'archidekt');
  const before = t.calls.length;
  t.modal.handlers.click({ target: t.modal, });  // scrim click cancels
  await t.c.confirm();                            // stray confirm with nothing pending
  out.cancelWrites = t.calls.slice(1).filter(c => c.body && c.body.dry_run === false).length;
  out.cancelPending = t.c.state().pending;

  // --- Push: dry run, dialog, confirm ------------------------------------------
  t = setup(STATUS, ok('push'));
  await t.c.refresh();
  await t.c.request('push', 'archidekt');
  out.pushDialog = t.bodyEl.innerHTML; out.pushGo = t.go.textContent;
  out.pushPre = t.calls.slice(1).map(c => ({ url: c.url, body: c.body }));
  await t.c.confirm();
  out.pushPost = t.calls.slice(1).map(c => ({ url: c.url, body: c.body }));
  out.pushToast = t.toasts; out.pushChanged = t.changed.length;

  // --- Error from the real write (502) ---------------------------------------
  t = setup(STATUS, async (url, body) => body.dry_run
    ? { ok: true, status: 200, json: async () => ({ success: true, changes: CHANGES }) }
    : { ok: false, status: 502, json: async () => ({ success: false, code: 'provider_error', error: 'Archidekt unreachable' }) });
  await t.c.refresh();
  await t.c.request('push', 'archidekt');
  await t.c.confirm();
  out.errState = t.c.state().errorText; out.errToast = t.toasts; out.errRoot = t.root.innerHTML;
  out.errChanged = t.changed.length;

  // --- Error on the dry run (501 not configured): no dialog -------------------
  t = setup(STATUS, async () => ({ ok: false, status: 501, json: async () => ({ success: false, code: 'push_not_configured', error: 'archidekt push is not configured on this server' }) }));
  await t.c.refresh();
  await t.c.request('push', 'archidekt');
  out.dryErr = t.c.state().errorText; out.dryErrOpened = t.opened.length;

  // --- No linked deck ---------------------------------------------------------
  t = setup({ success: true, providers: [{ provider: 'archidekt', linked: false }] }, ok('update'));
  await t.c.refresh();
  out.noneRoot = t.root.innerHTML;

  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed: sync_controls.js tests need it")
class TestBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", HARNESS, str(SC_JS)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.o = json.loads(res.stdout)

    def test_only_capable_linked_providers_get_controls(self):
        self.assertEqual(self.o["providers"], ["archidekt", "moxfield"])

    def test_labels_name_provider_and_direction(self):
        p = self.o["panel"]
        self.assertIn("Update from Archidekt", p)
        self.assertIn("Push to Archidekt", p)
        self.assertIn("Update from Moxfield", p)
        self.assertIn("Push to Moxfield", p)
        self.assertIn("overwrites your local commander.lab deck", p)
        self.assertIn("overwrites the external deck", p)
        self.assertIn("sync-btn--update", p)
        self.assertIn("sync-btn--push", p)
        self.assertIn("<svg", p)  # direction icons

    def test_timestamps(self):
        p = self.o["panel"]
        self.assertIn("Last updated: 2 hours ago", p)
        self.assertIn("Last pushed: never", p)
        self.assertIn("Last pushed: just now", p)
        self.assertEqual(self.o["ago"], ["never", "just now", "2 hours ago"])

    def test_unsupported_push_is_disabled_with_reason(self):
        p = self.o["panel"]
        m = re.search(r'<button[^>]*data-sync-op="push" data-sync-provider="moxfield"[^>]*>', p)
        self.assertIn("disabled", m.group(0))
        self.assertIn("moxfield push is not configured on this server", m.group(0))
        a = re.search(r'<button[^>]*data-sync-op="push" data-sync-provider="archidekt"[^>]*>', p)
        self.assertNotIn("disabled", a.group(0))

    def test_no_linked_deck_state(self):
        self.assertIn("Link External Deck", self.o["none"])
        self.assertNotIn("data-sync-op", self.o["none"])
        self.assertIn("No external deck linked", self.o["noneRoot"])
        self.assertNotIn("data-sync-op", self.o["noneRoot"])

    def test_loading_success_error_button_states(self):
        self.assertIn("Updating", self.o["busy"])
        self.assertIn("Pushing", self.o["busy"])
        self.assertEqual(self.o["busy"].count("disabled"), 2)
        self.assertIn("Updated!", self.o["done"])
        self.assertIn("Pushed!", self.o["done"])
        self.assertIn("Update failed", self.o["failed"])
        self.assertIn("Push failed", self.o["failed"])

    def test_load_only_reads_status(self):
        self.assertEqual(self.o["loadCalls"], ["GET /api/decks/archidekt%3A42/sync/status"])

    # ---- Update flow -----------------------------------------------------------
    def test_update_click_only_dry_runs_then_confirm_writes(self):
        base = "/api/decks/archidekt%3A42/sync/pull"
        self.assertEqual(self.o["updateAfterClick"], [{"url": base, "body": {"provider": "archidekt", "dry_run": True}}])
        self.assertEqual(self.o["opened"], 1)
        self.assertEqual(self.o["updateAfterConfirm"][1], {"url": base, "body": {"provider": "archidekt", "dry_run": False}})
        self.assertEqual(self.o["updateToast"], [["Updated from Archidekt", "ok"]])
        self.assertEqual(self.o["updateChanged"], 1)  # page reloads the deck after an Update

    def test_update_dialog_states_local_deck_is_overwritten(self):
        d = self.o["updateDialog"]
        self.assertIn("Overwrite local deck?", d)
        self.assertIn("Archidekt", d)
        self.assertIn("<b>Holy Hellfire</b>", d)
        self.assertIn("Archidekt is not modified", d)
        self.assertIn("+1 Rhystic Study", d)
        self.assertIn("\u22121 Cancel", d)
        self.assertIn("Island 10 \u2192 9", d)
        self.assertEqual(self.o["updateGo"], "Update from Archidekt")

    # ---- Push flow -------------------------------------------------------------
    def test_push_click_only_dry_runs_then_confirm_writes(self):
        base = "/api/decks/archidekt%3A42/sync/push"
        self.assertEqual(self.o["pushPre"], [{"url": base, "body": {"provider": "archidekt", "dry_run": True}}])
        self.assertEqual(self.o["pushPost"][1], {"url": base, "body": {"provider": "archidekt", "dry_run": False}})
        self.assertEqual(self.o["pushToast"], [["Pushed to Archidekt", "ok"]])
        self.assertEqual(self.o["pushChanged"], 0)  # push does not change the commander.lab deck

    def test_push_dialog_states_external_deck_is_overwritten(self):
        d = self.o["pushDialog"]
        self.assertIn("Overwrite Archidekt deck?", d)
        self.assertIn("replaces the list on <b>Archidekt</b>", d)
        self.assertIn("commander.lab is not modified", d)
        self.assertEqual(self.o["pushGo"], "Push to Archidekt")
        self.assertIn("Holy &lt;Hellfire&gt;", self.o["confirmPush"])  # escaped
        self.assertIn("Overwrite Moxfield deck?", self.o["confirmPush"])

    # ---- nothing syncs without confirmation ---------------------------------------
    def test_cancel_sends_no_write(self):
        self.assertEqual(self.o["cancelWrites"], 0)
        self.assertIsNone(self.o["cancelPending"])

    # ---- errors ---------------------------------------------------------------------
    def test_real_write_error_is_shown_and_does_not_reload_deck(self):
        self.assertIn("Push to Archidekt failed: Archidekt unreachable", self.o["errState"])
        self.assertEqual(self.o["errToast"][-1][1], "error")
        self.assertEqual(self.o["errChanged"], 0)
        self.assertIn("data-sync-error", self.o["errRoot"])

    def test_dry_run_error_shows_message_and_no_dialog(self):
        self.assertIn("not configured", self.o["dryErr"])
        self.assertEqual(self.o["dryErrOpened"], 0)


if __name__ == "__main__":
    unittest.main()
