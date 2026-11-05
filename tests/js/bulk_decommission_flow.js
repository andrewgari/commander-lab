// Behavioural test for the bulk-decommission UI on /decks.
// Usage: node bulk_decommission_flow.js <rendered-decks.html>   (needs `jsdom` resolvable)
const fs = require('fs');
const { JSDOM } = require('jsdom');
const assert = require('assert');

const html = fs.readFileSync(process.argv[2], 'utf8');
const mk = (i, status = 'physical') => ({ id: `archidekt:${i}`, name: `Deck ${i}`, status, commanders: [`C${i}`], color: 'G' });
let decks = [], calls = [], failIds = new Set(), gate = null;

(async () => {
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'http://localhost/decks',
    beforeParse(w) {
      w.fetch = async (url, opts) => {
        const json = (o, status = 200) => ({ ok: status < 400, status, json: async () => o });
        if (url === '/api/decks') return json({ decks });
        if (url === '/api/decks/decommission') {
          const body = JSON.parse(opts.body);
          calls.push(body);
          if (gate) await gate;
          const ok = [], bad = [];
          body.deck_ids.forEach(id => failIds.has(id)
            ? bad.push({ deck_id: id, reason: 'boom', code: 'not_physical' })
            : ok.push({ deck_id: id, status: 'retired', unassigned_count: 3 }));
          decks = decks.map(d => ok.find(o => o.deck_id === d.id) ? { ...d, status: 'retired' } : d);
          return json({ success: true, requested: body.deck_ids.length, succeeded: ok, failed: bad });
        }
        throw new Error('unexpected fetch ' + url);
      };
    },
  });
  const w = dom.window, d = w.document;
  const tick = (ms = 20) => new Promise(r => setTimeout(r, ms));
  const $ = id => d.getElementById(id);
  // top-level let/const in a classic script are not window props; reach them via eval.
  const ev = code => { const v = w.eval(code); return (v && typeof v === 'object') ? JSON.parse(JSON.stringify(v)) : v; };
  const reset = async (list, fails = []) => {
    w.fetch = realFetch;
    decks = list; calls = []; failIds = new Set(fails); gate = null;
    ev("isDecommissionBusy = false;"); w.closeBulkDialog();
    if (ev('selectionMode')) w.toggleSelectMode(false);
    ev("selectedDeckIds.clear(); failedDeckIds.clear();");
    await w.loadDecks(); await tick();
  };
  const realFetch = w.fetch;
  const cards = () => [...d.querySelectorAll('.deck-card')];

  // --- selection logic
  await reset([mk(1), mk(2), mk(3), mk(9, 'digital')]);
  w.toggleSelectMode(true);
  assert.strictEqual($('bulkBar').hidden, false);
  w.toggleDeckSelection('archidekt:1'); w.toggleDeckSelection('archidekt:2');
  assert.strictEqual(ev('selectedDeckIds.size'), 2);
  assert.match($('bulkCount').textContent, /2 selected/);
  assert.strictEqual($('bulkGo').textContent, 'Decommission (2)');
  w.toggleDeckSelection('archidekt:2');
  assert.strictEqual(ev('selectedDeckIds.size'), 1);
  w.handleSelectAll(true);
  assert.deepStrictEqual(ev('[...selectedDeckIds].sort()'), ['archidekt:1', 'archidekt:2', 'archidekt:3']); // digital excluded
  w.clearSelection();
  assert.strictEqual(ev('selectedDeckIds.size'), 0);
  assert.strictEqual($('bulkGo').disabled, true);
  console.log('ok selection');

  // --- success: two decks, one confirm, with in-flight guard
  await reset([mk(1), mk(2), mk(3)]);
  w.toggleSelectMode(true); w.handleSelectAll(true);
  w.openBulkDecommissionDialog();
  assert.match($('bulkTitle').textContent, /Decommission 3 decks\?/);
  assert.ok(d.querySelector('.bulk-deck-list').textContent.includes('Deck 2')); // names listed
  assert.strictEqual($('dlgConfirm').disabled, false);
  let release; gate = new Promise(r => { release = r; });
  const p1 = w.submitBulkDecommission();
  const p2 = w.submitBulkDecommission(); // repeat submit while in flight
  await tick();
  assert.strictEqual($('dlgConfirm').disabled, true);
  assert.match($('dlgConfirm').textContent, /Decommissioning/);
  release(); await p1; await p2; await tick();
  assert.strictEqual(calls.length, 1, 'repeat submission must be ignored');
  assert.deepStrictEqual([...calls[0].deck_ids], ['archidekt:1', 'archidekt:2', 'archidekt:3']);
  assert.strictEqual($('bulkDecommissionModal').style.display, 'none');
  assert.match($('toasts').textContent, /3 decks decommissioned/);
  assert.match($('toasts').textContent, /9 cards returned/);
  assert.strictEqual(ev('selectedDeckIds.size'), 0);
  assert.strictEqual(cards().length, 0);
  console.log('ok bulk success');

  // --- partial failure keeps failed deck selected, shows reason; retry works
  await reset([mk(1), mk(2), mk(3)], ['archidekt:3']);
  w.toggleSelectMode(true); w.handleSelectAll(true);
  w.openBulkDecommissionDialog();
  await w.submitBulkDecommission(); await tick();
  assert.match($('bulkTitle').textContent, /2 of 3 decommissioned/);
  assert.ok($('bulkDesc').textContent.includes('Deck 3') && $('bulkDesc').textContent.includes('boom'));
  assert.deepStrictEqual(ev('[...selectedDeckIds]'), ['archidekt:3']);
  assert.deepStrictEqual(ev('[...failedDeckIds.keys()]'), ['archidekt:3']);
  assert.strictEqual($('bulkDecommissionModal').style.display, 'flex');
  assert.strictEqual(cards().length, 1);
  failIds.clear();
  await w.retryFailed(); await tick(30);
  assert.deepStrictEqual([...calls[1].deck_ids], ['archidekt:3']);
  assert.strictEqual($('bulkDecommissionModal').style.display, 'none');
  console.log('ok partial failure + retry');

  // --- network/HTTP failure: nothing removed, all stay selected
  await reset([mk(1), mk(2)]);
  w.fetch = async (url) => url === '/api/decks'
    ? { ok: true, status: 200, json: async () => ({ decks }) }
    : { ok: false, status: 500, json: async () => ({ success: false, error: 'server exploded' }) };
  w.toggleSelectMode(true); w.handleSelectAll(true);
  w.openBulkDecommissionDialog();
  await w.submitBulkDecommission(); await tick();
  assert.ok($('bulkDesc').textContent.includes('server exploded'));
  assert.strictEqual(ev('selectedDeckIds.size'), 2);
  assert.strictEqual(ev('isDecommissionBusy'), false);
  console.log('ok http failure');

  // --- type-to-confirm for 5+
  await reset([1, 2, 3, 4, 5].map(i => mk(i)));
  w.toggleSelectMode(true); w.handleSelectAll(true);
  w.openBulkDecommissionDialog();
  assert.strictEqual($('dlgConfirm').disabled, true);
  $('bulkType').value = '5'; $('bulkType').dispatchEvent(new w.Event('input'));
  assert.strictEqual($('dlgConfirm').disabled, false);
  console.log('ok type-to-confirm');

  // --- list refresh drops selected IDs for decks that no longer exist
  await reset([mk(1), mk(2), mk(3)]);
  w.toggleSelectMode(true); w.handleSelectAll(true);
  assert.strictEqual(ev('selectedDeckIds.size'), 3);
  decks = [mk(1), mk(3)];
  await w.loadDecks(); await tick();
  assert.deepStrictEqual(ev('[...selectedDeckIds].sort()'), ['archidekt:1', 'archidekt:3']);
  assert.match($('bulkCount').textContent, /2 selected/);
  console.log('ok selection pruned on refresh');

  // --- single-deck path reuses the dialog and the same endpoint
  await reset([mk(7)]);
  w.openDeckModal(0); assert.ok($('modalDecommissionBtn'));
  w.decommissionCurrentDeck();
  assert.match($('bulkTitle').textContent, /Decommission 1 deck\?/);
  await w.submitBulkDecommission(); await tick();
  assert.deepStrictEqual([...calls[0].deck_ids], ['archidekt:7']);
  console.log('ok single deck');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
