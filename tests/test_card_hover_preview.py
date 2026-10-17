"""Card hover-preview popover component (static/js/card_hover_preview.js).

The controller is vanilla JS with its DOM, clock, image loader and resolver
injectable, so it runs in node against a small fake DOM and a fake clock
(same node-subprocess pattern as tests/test_brackets_ui.py and
tests/test_scryfall_image.py). URL resolution goes through the real
static/js/scryfall_image.js.

Covers (SPEC: docs/design/hover-preview/SPEC.md): cold/warm open and close
timing, the no-flicker sweep (warm swap in place, intermediate rows skipped),
keyboard focus open + Esc, ARIA, loading skeleton / error / timeout states,
name-only marking, DFC side-by-side and flip fallback, placement and flipping,
touch long-press / tap / slop / sheet, preload + caching, the remote lookup
upgrade, and the markup helpers.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHP_JS = ROOT / "static" / "js" / "card_hover_preview.js"
SI_JS = ROOT / "static" / "js" / "scryfall_image.js"
CHP_CSS = ROOT / "static" / "css" / "card_hover_preview.css"

SID = "0c3e2a5b-4f4f-4a53-9c3f-5d1a6c1d9e10"
SID2 = "11111111-2222-3333-4444-555555555555"
SID3 = "99999999-8888-7777-6666-555555555555"
CDN = "https://cards.scryfall.io"
API = "https://api.scryfall.com"
DELVER = {
    "id": SID2, "name": "Delver of Secrets // Insectile Aberration", "layout": "transform",
    "set": "isd", "collector_number": "51",
    "card_faces": [
        {"name": "Delver of Secrets", "image_uris": {"normal": CDN + "/normal/front/d/e/delver.jpg",
                                                      "large": CDN + "/large/front/d/e/delver.jpg"}},
        {"name": "Insectile Aberration", "image_uris": {"normal": CDN + "/normal/back/d/e/delver.jpg",
                                                         "large": CDN + "/large/back/d/e/delver.jpg"}},
    ],
}

HARNESS = r"""
const CHP_PATH = process.argv[1], SI_PATH = process.argv[2];
const K = JSON.parse(process.argv[3]);
const { SID, SID2, SID3, CDN, API, DELVER } = K;

// ---- fake DOM ---------------------------------------------------------------
function mkEvent(type, props) {
  const e = Object.assign({ type, target: null, relatedTarget: null, pointerType: 'mouse', clientX: 0, clientY: 0,
                            defaultPrevented: false, propagationStopped: false }, props);
  e.preventDefault = () => { e.defaultPrevented = true; };
  e.stopPropagation = () => { e.propagationStopped = true; };
  e.stopImmediatePropagation = () => { e.propagationStopped = true; };
  return e;
}
class Target {
  constructor() { this._ls = {}; }
  addEventListener(t, f) { (this._ls[t] = this._ls[t] || []).push(f); }
  removeEventListener(t, f) { this._ls[t] = (this._ls[t] || []).filter(x => x !== f); }
  fire(t, props) { const e = mkEvent(t, Object.assign({ target: this }, props)); (this._ls[t] || []).slice().forEach(f => f(e)); return e; }
  listenerCount() { return Object.values(this._ls).reduce((n, a) => n + a.length, 0); }
}
class El extends Target {
  constructor(tag, doc) {
    super(); this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = []; this.parentNode = null;
    this._attrs = {}; this._cls = []; this.style = {}; this.hidden = false; this._text = ''; this._rect = null; this._rects = null; this._fv = false;
    const self = this;
    this.classList = {
      add: (...c) => c.forEach(x => { if (!self._cls.includes(x)) self._cls.push(x); }),
      remove: (...c) => { self._cls = self._cls.filter(x => !c.includes(x)); },
      contains: c => self._cls.includes(c),
      toggle: (c, f) => { const want = f === undefined ? !self._cls.includes(c) : !!f; if (want) self.classList.add(c); else self.classList.remove(c); return want; },
    };
  }
  get className() { return this._cls.join(' '); } set className(v) { this._cls = String(v).split(/\s+/).filter(Boolean); }
  get id() { return this._attrs.id || ''; } set id(v) { this._attrs.id = String(v); }
  get firstChild() { return this.children[0] || null; }
  get isConnected() { let n = this; while (n) { if (n === this.ownerDocument.body) return true; n = n.parentNode; } return false; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; }
  hasAttribute(k) { return this.getAttribute(k) !== null; }
  removeAttribute(k) { delete this._attrs[k]; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._text = String(v); }
  getBoundingClientRect() { return this._rect || { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  getClientRects() { return this._rects || [this.getBoundingClientRect()]; }
  matches(s) { return s === ':focus-visible' ? this._fv : false; }
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  focus() { this.ownerDocument.activeElement = this; }
  all(pred) { const out = []; const walk = n => n.children.forEach(c => { if (pred(c)) out.push(c); walk(c); }); walk(this); return out; }
  byClass(c) { return this.all(n => n._cls.includes(c)); }
}
function rect(left, top, w, h) { return { left, top, right: left + w, bottom: top + h, width: w, height: h }; }

// ---- fake clock -------------------------------------------------------------
function Clock() {
  let t = 0, seq = 0, q = [];
  return {
    now: () => t,
    setTimeout: (fn, ms) => { const id = ++seq; q.push({ id, at: t + Math.max(0, ms || 0), fn }); return id; },
    clearTimeout: id => { q = q.filter(x => x.id !== id); },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        q.sort((a, b) => a.at - b.at || a.id - b.id);
        const n = q[0];
        if (!n || n.at > end) break;
        q.shift(); t = n.at; n.fn();
      }
      t = end;
    },
    pending: () => q.length,
  };
}

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };

function setup(opts) {
  opts = opts || {};
  for (const p of [CHP_PATH, SI_PATH]) delete require.cache[require.resolve(p)];
  const SI = require(SI_PATH);
  SI.config.warn = false;
  const CHP = require(CHP_PATH);
  const doc = new Target();
  doc.createElement = tag => new El(tag, doc);
  doc.body = new El('body', doc);
  doc.activeElement = doc.body;
  doc.documentElement = { clientWidth: 1400, clientHeight: 900 };
  const win = new Target();
  Object.assign(win, { innerWidth: opts.vw || 1400, innerHeight: opts.vh || 900, devicePixelRatio: opts.dpr || 1, navigator: {} });
  const clock = Clock();
  const loads = [];               // every image request the component made
  const pending = {};             // url -> {resolve, reject} when mode === 'manual'
  let mode = opts.imageMode || 'ok';
  const loadImage = url => {
    loads.push(url);
    if (mode === 'ok') return Promise.resolve(url);
    if (mode === 'fail') return Promise.reject(new Error('404'));
    return new Promise((res, rej) => { pending[url] = { resolve: () => res(url), reject: () => rej(new Error('x')) }; });
  };
  const fetches = [];
  const fetch = opts.fetch || ((url) => { fetches.push(url); return Promise.reject(new Error('offline')); });
  const c = CHP.create(Object.assign({ lookup: false }, opts.config || {},
                                     { env: { document: doc, window: win, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
                                              now: clock.now, loadImage, fetch, resolver: SI } })).listen();
  const events = [];
  ['intent', 'open', 'close', 'render', 'lookup'].forEach(n => c.on(n, d => events.push({ n, t: clock.now(), d })));
  function trig(a, r, tag) {
    const el = new El(tag || 'span', doc);
    el.setAttribute('data-card-preview', '');
    if (a.id) el.setAttribute('data-cp-id', a.id);
    if (a.set) el.setAttribute('data-cp-set', a.set);
    if (a.cn) el.setAttribute('data-cp-cn', a.cn);
    if (a.name) el.setAttribute('data-cp-name', a.name);
    if (a.faces) el.setAttribute('data-cp-faces', String(a.faces));
    if (a.layout) el.setAttribute('data-cp-layout', a.layout);
    el._rect = r || rect(100, 100, 120, 20);
    (a.parent || doc.body).appendChild(el);
    return el;
  }
  const at = el => ({ clientX: el._rect.left + 5, clientY: el._rect.top + 5 });
  const over = (el, from) => doc.fire('pointerover', Object.assign({ target: el, relatedTarget: from || null, pointerType: 'mouse' }, at(el)));
  const out = (el, to) => doc.fire('pointerout', { target: el, relatedTarget: to || null, pointerType: 'mouse' });
  const move = (from, to) => { out(from, to); over(to, from); };
  const pop = () => c.element();
  const faces = () => pop() ? pop().byClass('hud-cp-face') : [];
  const img = f => f.children.find(n => n.tagName === 'IMG');
  const state = () => faces().map(f => ({ state: f.getAttribute('data-state'), face: f.getAttribute('data-face'),
                                          alt: img(f).alt, src: img(f).src || '', ratio: f.style.aspectRatio }));
  const cap = () => { const p = pop(); const k = cl => p.byClass(cl)[0]; return {
    text: k('hud-cp-cap').textContent, status: k('hud-cp-cap-status').textContent,
    chip: k('hud-chip').hidden ? '' : k('hud-chip').textContent, flip: !k('hud-cp-flip').hidden }; };
  return { SI, CHP, c, doc, win, clock, loads, pending, fetches, events, trig, over, out, move, pop, faces, state, cap,
           setMode: m => { mode = m; }, flush, rect };
}

const out = {};
async function scen(name, fn) {
  try { out[name] = await fn(); } catch (e) { out[name] = { error: String(e && e.stack || e) }; }
}

(async () => {
  // ---- timing ---------------------------------------------------------------
  await scen('cold_open_and_close', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'Sol Ring', set: 'cmm', cn: '410' });
    a.setAttribute('aria-describedby', 'note-1');
    T.over(a);
    T.clock.advance(299); const at299 = T.c.isOpen();
    T.clock.advance(1); const at300 = T.c.isOpen();
    const el = T.pop();
    const r = { at299, at300, role: el.getAttribute('role'), id: el.id, described: a.getAttribute('aria-describedby'),
                openClass: a.classList.contains('is-cp-open'), tabindex: el.getAttribute('tabindex'),
                intentDelay: T.events.find(e => e.n === 'intent').d.delay };
    T.out(a);
    T.clock.advance(119); r.at119 = T.c.isOpen();
    T.clock.advance(1); r.at120 = T.c.isOpen();
    r.describedAfter = a.getAttribute('aria-describedby');
    r.hiddenRightAway = el.hidden;
    T.clock.advance(100); r.hiddenAfterFade = el.hidden;
    return r;
  });

  await scen('reenter_within_grace_keeps_open', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    T.over(a); T.clock.advance(300);
    T.out(a); T.clock.advance(80); T.over(a); T.clock.advance(500);
    return { open: T.c.isOpen(), closes: T.events.filter(e => e.n === 'close').length, opens: T.events.filter(e => e.n === 'open').length };
  });

  await scen('warm_window', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'A' }), b = T.trig({ id: SID2, name: 'B' }, T.rect(100, 130, 120, 20)),
          c = T.trig({ id: SID3, name: 'C' }, T.rect(100, 160, 120, 20));
    T.over(a); T.clock.advance(300); T.out(a); T.clock.advance(120);       // closed now
    T.clock.advance(100);                                                    // 100 ms < 250 ms warm window
    T.over(b); T.clock.advance(59); const b59 = T.c.isOpen(); T.clock.advance(1); const b60 = T.c.isOpen();
    T.out(b); T.clock.advance(120); T.clock.advance(300);                   // beyond the warm window
    T.over(c); T.clock.advance(299); const c299 = T.c.isOpen(); T.clock.advance(1); const c300 = T.c.isOpen();
    return { b59, b60, c299, c300, delays: T.events.filter(e => e.n === 'intent').map(e => e.d.delay) };
  });

  // ---- no-flicker sweeps --------------------------------------------------------
  await scen('cold_sweep_opens_nothing_until_rest', async () => {
    const T = setup();
    const rows = [0, 1, 2, 3, 4, 5].map(i => T.trig({ id: SID, name: 'Row ' + i }, T.rect(100, 100 + i * 24, 160, 22)));
    T.over(rows[0]);
    let everOpen = false;
    for (let i = 1; i < rows.length; i++) {
      for (let k = 0; k < 10; k++) { T.clock.advance(10); everOpen = everOpen || T.c.isOpen(); }   // 100 ms per row
      T.move(rows[i - 1], rows[i]);
    }
    T.clock.advance(299); const before = T.c.isOpen();
    T.clock.advance(1);
    const opens = T.events.filter(e => e.n === 'open');
    return { everOpen, before, after: T.c.isOpen(), opens: opens.length, current: rows.indexOf(T.c.current()) };
  });

  await scen('warm_sweep_swaps_in_place', async () => {
    const T = setup();
    const rows = [0, 1, 2, 3, 4, 5].map(i => T.trig({ id: SID, set: 'cmm', cn: String(400 + i), name: 'Row ' + i },
                                                    T.rect(100, 100 + i * 24, 160, 22)));
    T.over(rows[0]); T.clock.advance(300);
    const el = T.pop();
    let closedOrHidden = false;
    const sample = () => { closedOrHidden = closedOrHidden || !T.c.isOpen() || el.hidden; };
    // Row 1: measure the swap delay.
    T.move(rows[0], rows[1]);
    T.clock.advance(59); sample(); const cur59 = rows.indexOf(T.c.current());
    T.clock.advance(1); sample(); const cur60 = rows.indexOf(T.c.current());
    const swapClass = el.classList.contains('is-swap');
    // Rows 2..3 scrubbed fast (30 ms each): skipped. Row 4: 80 ms dwell -> shown. Row 5: rest.
    for (const [i, dwell] of [[2, 30], [3, 30], [4, 80], [5, 200]]) {
      T.move(rows[i - 1], rows[i]);
      for (let k = 0; k < dwell; k += 10) { T.clock.advance(10); sample(); }
    }
    const shown = T.events.filter(e => e.n === 'open').map(e => rows.indexOf(e.d.trigger));
    return { closedOrHidden, cur59, cur60, swapClass, shown,
             closes: T.events.filter(e => e.n === 'close').length,
             warmFlags: T.events.filter(e => e.n === 'open').map(e => e.d.warm),
             described: rows.map(r => r.getAttribute('aria-describedby')),
             openClass: rows.map(r => r.classList.contains('is-cp-open')),
             popovers: T.doc.body.byClass('hud-cp').length };
  });

  await scen('pending_row_left_to_empty_space_closes_open_preview', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'A' }), b = T.trig({ id: SID2, name: 'B' }, T.rect(100, 130, 120, 20));
    T.over(a); T.clock.advance(300);
    T.move(a, b); T.clock.advance(30);          // b's warm open still pending
    T.out(b, null);                              // leave the list entirely
    T.clock.advance(119); const at119 = T.c.isOpen();
    T.clock.advance(1);
    return { at119, closed: !T.c.isOpen(), shownB: T.events.some(e => e.n === 'open' && e.d.trigger === b) };
  });

  // ---- keyboard / a11y ------------------------------------------------------
  await scen('keyboard', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    a.setAttribute('tabindex', '0');
    a._fv = true; a.focus(); T.doc.fire('focusin', { target: a });
    T.clock.advance(399); const at399 = T.c.isOpen();
    T.clock.advance(1); const at400 = T.c.isOpen();
    const el = T.pop();
    const described = a.getAttribute('aria-describedby') === el.id;
    const focusStayed = T.doc.activeElement === a;
    const popTabindex = el.getAttribute('tabindex');
    const esc1 = T.doc.fire('keydown', { target: a, key: 'Escape' });
    const afterEsc = T.c.isOpen();
    const focusAfterEsc = T.doc.activeElement === a;
    const esc2 = T.doc.fire('keydown', { target: a, key: 'Escape' });
    // A repeated focusin on the same trigger (e.g. window refocus) does not reopen after Esc.
    T.doc.fire('focusin', { target: a }); T.clock.advance(500); const reopenedAfterEsc = T.c.isOpen();
    // Mouse-click focus (not :focus-visible) does not open.
    const b = T.trig({ id: SID2, name: 'B' }, T.rect(100, 140, 100, 20));
    T.doc.fire('focusout', { target: a, relatedTarget: b });
    b._fv = false; b.focus(); T.doc.fire('focusin', { target: b }); T.clock.advance(500); const mouseFocusOpened = T.c.isOpen();
    // focusout closes immediately.
    const c = T.trig({ id: SID3, name: 'C' }, T.rect(100, 180, 100, 20));
    c._fv = true; c.focus(); T.doc.fire('focusin', { target: c }); T.clock.advance(400);
    const cOpen = T.c.isOpen();
    T.doc.fire('focusout', { target: c, relatedTarget: null }); const afterBlur = T.c.isOpen();
    // Esc while an open is still pending cancels it and is NOT swallowed.
    T.doc.fire('focusin', { target: a }); T.clock.advance(100);
    const esc3 = T.doc.fire('keydown', { target: a, key: 'Escape' }); T.clock.advance(500);
    return { at399, at400, described, focusStayed, popTabindex, afterEsc,
             esc1: [esc1.defaultPrevented, esc1.propagationStopped], esc2: [esc2.defaultPrevented, esc2.propagationStopped],
             describedAfterEsc: a.getAttribute('aria-describedby'), focusAfterEsc,
             reopenedAfterEsc, mouseFocusOpened, cOpen, afterBlur,
             esc3: [esc3.defaultPrevented, esc3.propagationStopped], pendingCancelled: !T.c.isOpen() };
  });

  // ---- loading / error / timeout ------------------------------------------------
  await scen('loading_then_loaded', async () => {
    const T = setup({ imageMode: 'manual' });
    const a = T.trig({ id: SID, name: 'Sol Ring', set: 'cmm', cn: '410' });
    T.over(a);
    const preloadedBeforeOpen = T.loads.slice();
    T.clock.advance(300);
    const loading = T.state();
    T.pending[T.loads[0]].resolve(); await T.flush();
    return { preloadedBeforeOpen, loading, loaded: T.state(), cap: T.cap() };
  });

  await scen('error_state', async () => {
    const T = setup({ imageMode: 'fail' });
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    T.over(a); T.clock.advance(300); await T.flush();
    return { faces: T.state(), cap: T.cap(), hasError: T.pop().classList.contains('has-error'),
             skeleton: T.faces()[0].byClass('hud-cp-ph').length, imageState: T.c.imageState(T.loads[0]) };
  });

  await scen('timeout_state', async () => {
    const T = setup({ imageMode: 'manual' });
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    T.over(a); T.clock.advance(300);
    T.clock.advance(7699); await T.flush(); const before = T.state()[0].state;
    T.clock.advance(1); await T.flush();
    return { before, after: T.state()[0].state, cap: T.cap() };
  });

  await scen('failed_url_not_retried_while_sweeping', async () => {
    const T = setup({ imageMode: 'fail' });
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    for (let i = 0; i < 3; i++) { T.over(a); T.clock.advance(300); await T.flush(); T.out(a); T.clock.advance(400); }
    return { loads: T.loads.length };
  });

  // ---- name-only marking ------------------------------------------------------------
  await scen('name_only_marked', async () => {
    const T = setup();
    T.SI.config.warn = true;
    const warns = []; const orig = console.warn; console.warn = m => warns.push(String(m));
    let r;
    try {
      const n = T.trig({ name: 'Rhystic Study' });
      T.over(n); T.clock.advance(300);
      r = { cap: T.cap(), nameOnlyClass: T.pop().classList.contains('is-name-only'), src: T.loads[0] };
      const p = T.trig({ id: SID, name: 'Sol Ring' }, T.rect(100, 140, 100, 20));
      T.move(n, p); T.clock.advance(60);
      r.printing = { cap: T.cap(), nameOnlyClass: T.pop().classList.contains('is-name-only') };
    } finally { console.warn = orig; }
    r.warns = warns.filter(w => w.includes('Rhystic Study')).length;
    return r;
  });

  // ---- DFC ----------------------------------------------------------------------------
  await scen('dfc_side_by_side', async () => {
    const T = setup();
    const a = T.trig({}, T.rect(100, 300, 160, 22));
    T.CHP.bind(a, DELVER);
    T.over(a); T.clock.advance(300); await T.flush();
    const el = T.pop();
    return { faces: T.state(), width: el.byClass('hud-cp-faces')[0].style.width, side: el.getAttribute('data-side'),
             dataFaces: el.getAttribute('data-faces'), cap: T.cap(), interactive: el.classList.contains('is-interactive') };
  });

  await scen('dfc_scaled_down_but_above_floor', async () => {
    const T = setup({ vw: 800 });
    const a = T.trig({}, T.rect(20, 300, 40, 22));    // right room: 800 - 60 - 20 = 720 -> faces 356 px
    T.CHP.bind(a, DELVER);
    T.over(a); T.clock.advance(300);
    const el = T.pop();
    return { n: T.faces().length, width: el.byClass('hud-cp-faces')[0].style.width, side: el.getAttribute('data-side') };
  });

  await scen('dfc_narrow_flip', async () => {
    const T = setup({ vw: 500 });
    const a = T.trig({}, T.rect(200, 100, 100, 22));
    T.CHP.bind(a, DELVER);
    T.over(a); T.clock.advance(300);
    const el = T.pop();
    const front = { faces: T.state(), cap: T.cap(), interactive: el.classList.contains('is-interactive'),
                    side: el.getAttribute('data-side'), width: el.byClass('hud-cp-faces')[0].style.width };
    el.byClass('hud-cp-flip')[0].fire('click');
    const back = { faces: T.state(), cap: T.cap() };
    el.byClass('hud-cp-faces')[0].fire('click');
    const again = T.state();
    // Moving from the trigger onto the (interactive) popover keeps it open.
    T.out(a, el); T.clock.advance(50); el.fire('pointerenter'); T.clock.advance(300);
    const stillOpen = T.c.isOpen();
    el.fire('pointerleave', { relatedTarget: null }); T.clock.advance(120);
    return { front, back, again, stillOpen, closedAfterLeave: !T.c.isOpen() };
  });

  await scen('dfc_from_attributes', async () => {
    const T = setup();
    const a = T.trig({ id: SID2, name: 'Delver of Secrets // Insectile Aberration', faces: 2 });
    T.over(a); T.clock.advance(300);
    const r = { faces: T.state(), cap: T.cap() };
    const n = T.trig({ name: 'Delver of Secrets // Insectile Aberration', layout: 'transform' }, T.rect(100, 140, 100, 20));
    T.move(a, n); T.clock.advance(60);
    r.nameOnly = { faces: T.state(), cap: T.cap() };
    const s = T.trig({ id: SID3, name: 'Fire // Ice', layout: 'split' }, T.rect(100, 180, 100, 20));
    T.move(n, s); T.clock.advance(60);
    r.split = T.state();
    r.loads = T.loads;
    return r;
  });

  await scen('dpr_large', async () => {
    const T = setup({ dpr: 2 });
    const a = T.trig({ id: SID, name: 'Sol Ring' });
    T.over(a); T.clock.advance(300);
    return T.loads[0];
  });

  // ---- placement -------------------------------------------------------------------
  await scen('placement', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'A' }, T.rect(100, 300, 120, 20));
    T.over(a); T.clock.advance(300);
    const el = T.pop();
    const right = { side: el.getAttribute('data-side'), transform: el.style.transform, width: el.byClass('hud-cp-faces')[0].style.width };
    T.c.close();
    const b = T.trig({ id: SID2, name: 'B' }, T.rect(1200, 300, 100, 20));
    T.c.open(b);
    const left = { side: el.getAttribute('data-side'), transform: el.style.transform };
    T.c.close();
    const top = T.trig({ id: SID3, name: 'C' }, T.rect(100, 2, 100, 20));
    T.c.open(top);
    const clampedTop = el.style.transform;
    T.c.close();
    // Wrapped inline name: anchor to the line box under the pointer.
    const w = T.trig({ id: SID, name: 'Wrapped' }, T.rect(100, 400, 300, 40));
    w._rects = [T.rect(250, 400, 150, 20), T.rect(100, 420, 90, 20)];
    T.doc.fire('pointerover', { target: w, relatedTarget: null, pointerType: 'mouse', clientX: 120, clientY: 430 });
    T.clock.advance(300);
    const wrapped = el.style.transform;
    return { right, left, clampedTop, wrapped };
  });

  // ---- touch -------------------------------------------------------------------------
  await scen('touch', async () => {
    const T = setup({ vw: 390, vh: 844 });
    const link = new (T.doc.body.constructor)('a', T.doc);
    link.setAttribute('href', '/deck/x'); T.doc.body.appendChild(link);
    const thumb = T.trig({ id: SID, name: 'Sol Ring', parent: link }, T.rect(20, 100, 80, 110));
    const pic = new (T.doc.body.constructor)('img', T.doc); thumb.appendChild(pic);
    const td = (t, x, y) => T.doc.fire('pointerdown', { target: t, pointerType: 'touch', clientX: x, clientY: y });
    const tm = (t, x, y) => T.doc.fire('pointermove', { target: t, pointerType: 'touch', clientX: x, clientY: y });
    const tu = (t) => T.doc.fire('pointerup', { target: t, pointerType: 'touch' });
    // Touch pointerover (emulated hover) is ignored.
    T.doc.fire('pointerover', { target: thumb, pointerType: 'touch' }); T.clock.advance(400);
    const hoverIgnored = !T.c.isOpen();
    // Long-press on an actionable thumbnail.
    td(pic, 50, 150); T.clock.advance(449); const lp449 = T.c.isOpen(); T.clock.advance(1); const lp450 = T.c.isOpen();
    tu(pic);
    const click = T.doc.fire('click', { target: pic });
    const el = T.pop(), scrim = T.c.scrim();
    const sheet = { isSheet: el.classList.contains('is-sheet'), scrimShown: !scrim.hidden, role: el.getAttribute('role'),
                    side: el.getAttribute('data-side'), width: el.byClass('hud-cp-faces')[0].style.width,
                    label: el.getAttribute('aria-label') };
    scrim.fire('click'); const afterScrim = T.c.isOpen();
    // A short tap on an actionable thumbnail does not open (the link handles it) and is not swallowed.
    td(pic, 50, 150); T.clock.advance(100); tu(pic); const tapClick = T.doc.fire('click', { target: pic });
    T.clock.advance(500); const tapOpened = T.c.isOpen();
    // > 10 px travel cancels; <= 10 px does not.
    td(pic, 50, 150); tm(pic, 50, 161); T.clock.advance(500); const slopCancelled = !T.c.isOpen(); tu(pic);
    td(pic, 50, 150); tm(pic, 56, 157); T.clock.advance(450); const smallMoveOpened = T.c.isOpen(); tu(pic);
    el.byClass('hud-cp-close')[0].fire('click'); const afterClose = T.c.isOpen();
    // Inert text: a tap opens straight away.
    const name = T.trig({ id: SID2, name: 'Thassa' }, T.rect(20, 300, 80, 20));
    td(name, 30, 305); tu(name); const tapInert = T.c.isOpen();
    const swallowed = T.doc.fire('click', { target: name }).defaultPrevented;
    return { hoverIgnored, lp449, lp450, clickSwallowed: click.defaultPrevented, sheet, afterScrim,
             tapClickPrevented: tapClick.defaultPrevented, tapOpened, slopCancelled, smallMoveOpened, afterClose,
             tapInert, swallowed };
  });

  await scen('mouse_pointerdown_scroll_resize_close', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'A' });
    const r = {};
    T.over(a); T.clock.advance(300); T.doc.fire('pointerdown', { target: T.doc.body, pointerType: 'mouse' }); r.pointerdown = T.c.isOpen();
    T.c.open(a); T.win.fire('scroll'); r.scroll = T.c.isOpen();
    T.c.open(a); T.win.fire('resize'); r.resize = T.c.isOpen();
    T.c.open(a); a.remove(); T.clock.advance(500); r.removed = T.c.isOpen();
    return r;
  });

  // ---- cache / preload -------------------------------------------------------------------
  await scen('cache_and_preload', async () => {
    const T = setup();
    const a = T.trig({ id: SID, name: 'Sol Ring' }), b = T.trig({ id: SID2, name: 'Sol Ring' }, T.rect(100, 140, 100, 20));
    T.over(a); const atIntent = T.loads.length;
    T.clock.advance(300); await T.flush(); T.out(a); T.clock.advance(400);
    T.over(a); T.clock.advance(300); await T.flush(); T.out(a); T.clock.advance(400);
    const afterRehover = T.loads.length;
    T.over(b); T.clock.advance(300); await T.flush();
    return { atIntent, afterRehover, urls: T.loads, stats: T.SI.cache.stats(),
             keys: T.SI.cache.keys(), state: T.state()[0].state };
  });

  await scen('lookup_upgrades_to_cdn_dfc', async () => {
    const fetches = [];
    const json = Object.assign({}, DELVER);
    const T = setup({ config: { lookup: true },
                      fetch: (url, init) => { fetches.push({ url, accept: init.headers.Accept }); return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(json) }); } });
    const a = T.trig({ id: SID2, name: 'Delver of Secrets' }, T.rect(100, 300, 160, 22));
    T.over(a);
    const beforeOpen = fetches.length;
    await T.flush();
    T.clock.advance(300);
    const opened = T.state();
    T.out(a); T.clock.advance(400);
    T.over(a); T.clock.advance(300); await T.flush();
    return { beforeOpen, opened, again: T.state(), fetches, loads: T.loads, width: T.pop().byClass('hud-cp-faces')[0].style.width };
  });

  await scen('lookup_failure_falls_back_to_api_image', async () => {
    const T = setup({ config: { lookup: true } });           // default fake fetch rejects
    const a = T.trig({ set: 'cmm', cn: '410', name: 'Sol Ring' });
    T.over(a); const loadsDuringLookup = T.loads.length;
    T.clock.advance(300); const whileOpen = T.state();
    await T.flush();
    return { loadsDuringLookup, whileOpen, after: T.state(), loads: T.loads, fetches: T.fetches };
  });

  await scen('lookup_upgrades_open_popover_in_place', async () => {
    let release;
    const gate = new Promise(r => { release = r; });
    const T = setup({ config: { lookup: true },
                      fetch: () => gate.then(() => ({ ok: true, status: 200, json: () => Promise.resolve(DELVER) })) });
    const a = T.trig({ id: SID2, name: 'Delver of Secrets' }, T.rect(100, 300, 160, 22));
    T.over(a); T.clock.advance(300);
    const before = T.state().length;
    const beforeState = T.state()[0].state;
    const loadsBefore = T.loads.length;
    release(); await T.flush();
    return { before, beforeState, loadsBefore, loads: T.loads, after: T.state().map(f => f.src || f.alt), open: T.c.isOpen() };
  });

  // ---- API / markup helpers ---------------------------------------------------------------
  await scen('api', async () => {
    const T = setup();
    const H = T.CHP;
    const listeners = T.doc.listenerCount();
    T.c.destroy();
    return {
      exports: Object.keys(H).sort(),
      defaults: H.defaults,
      attrs: H.attrs({ scryfallId: 'x"y', set: 'CMM', collectorNumber: '410', name: 'A <b> & "c"' }),
      attrsDfc: H.attrs(DELVER),
      attrsNameOnly: H.attrs({ name: 'Sol Ring' }),
      wrap: H.wrap('Sol Ring', { uid: SID, name: 'Sol Ring' }, { name: true }),
      wrapLink: H.wrap('<a href="/x">x</a>', { uid: SID }, { focusable: false, tag: 'div"><script>' }),
      listenersBefore: listeners, listenersAfter: T.doc.listenerCount(),
      popoverRemoved: T.doc.body.byClass('hud-cp').length,
    };
  });

  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node not installed: card_hover_preview.js tests need it")
class TestCardHoverPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        consts = {"SID": SID, "SID2": SID2, "SID3": SID3, "CDN": CDN, "API": API, "DELVER": DELVER}
        res = subprocess.run(["node", "-e", HARNESS, str(CHP_JS), str(SI_JS), json.dumps(consts)],
                             capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def v(self, name):
        entry = self.out[name]
        if isinstance(entry, dict):
            self.assertNotIn("error", entry, entry.get("error"))
        return entry

    # ---- timing ---------------------------------------------------------------
    def test_cold_open_after_300ms_and_close_after_120ms(self):
        r = self.v("cold_open_and_close")
        self.assertEqual(r["intentDelay"], 300)
        self.assertFalse(r["at299"])
        self.assertTrue(r["at300"])
        self.assertTrue(r["at119"])
        self.assertFalse(r["at120"])

    def test_aria_tooltip_describedby_and_no_focus_trap(self):
        r = self.v("cold_open_and_close")
        self.assertEqual(r["role"], "tooltip")
        self.assertTrue(r["id"])
        self.assertEqual(r["described"], "note-1 " + r["id"])   # existing tokens kept
        self.assertEqual(r["describedAfter"], "note-1")          # and restored on close
        self.assertIsNone(r["tabindex"])                         # popover is never focusable
        self.assertTrue(r["openClass"])

    def test_exit_fade_then_hidden(self):
        r = self.v("cold_open_and_close")
        self.assertFalse(r["hiddenRightAway"])
        self.assertTrue(r["hiddenAfterFade"])

    def test_reentering_within_grace_does_not_close(self):
        r = self.v("reenter_within_grace_keeps_open")
        self.assertEqual(r, {"open": True, "closes": 0, "opens": 1})

    def test_warm_window_uses_60ms_then_falls_back_to_300ms(self):
        r = self.v("warm_window")
        self.assertFalse(r["b59"])
        self.assertTrue(r["b60"])
        self.assertFalse(r["c299"])
        self.assertTrue(r["c300"])
        self.assertEqual(r["delays"], [300, 60, 300])

    # ---- no-flicker sweeps ------------------------------------------------------
    def test_cold_sweep_opens_nothing_until_the_cursor_rests(self):
        r = self.v("cold_sweep_opens_nothing_until_rest")
        self.assertFalse(r["everOpen"])
        self.assertFalse(r["before"])
        self.assertTrue(r["after"])
        self.assertEqual(r["opens"], 1)
        self.assertEqual(r["current"], 5)

    def test_warm_sweep_swaps_without_closing(self):
        r = self.v("warm_sweep_swaps_in_place")
        self.assertFalse(r["closedOrHidden"], "popover closed or hid during the sweep (flicker)")
        self.assertEqual(r["closes"], 0)
        self.assertEqual(r["popovers"], 1)                       # one shared popover instance
        self.assertEqual((r["cur59"], r["cur60"]), (0, 1))       # 60 ms warm swap, not 300
        self.assertTrue(r["swapClass"])                          # no re-entrance animation
        self.assertEqual(r["shown"], [0, 1, 4, 5])               # 30 ms rows 2-3 skipped
        self.assertEqual(r["warmFlags"], [False, True, True, True])
        self.assertEqual([d is not None for d in r["described"]], [False] * 5 + [True])
        self.assertEqual(r["openClass"], [False] * 5 + [True])

    def test_leaving_the_list_from_a_pending_row_closes(self):
        r = self.v("pending_row_left_to_empty_space_closes_open_preview")
        self.assertTrue(r["at119"])
        self.assertTrue(r["closed"])
        self.assertFalse(r["shownB"])

    # ---- keyboard -------------------------------------------------------------------
    def test_focus_visible_opens_after_400ms(self):
        r = self.v("keyboard")
        self.assertFalse(r["at399"])
        self.assertTrue(r["at400"])
        self.assertTrue(r["described"])
        self.assertTrue(r["focusStayed"])
        self.assertIsNone(r["popTabindex"])

    def test_escape_closes_immediately_and_is_swallowed_once(self):
        r = self.v("keyboard")
        self.assertFalse(r["afterEsc"])
        self.assertEqual(r["esc1"], [True, True])     # first Esc: preview only, modal untouched
        self.assertEqual(r["esc2"], [False, False])   # second Esc reaches the modal
        self.assertIsNone(r["describedAfterEsc"])
        self.assertTrue(r["focusAfterEsc"])
        self.assertFalse(r["reopenedAfterEsc"])

    def test_mouse_focus_does_not_open_and_blur_closes(self):
        r = self.v("keyboard")
        self.assertFalse(r["mouseFocusOpened"])
        self.assertTrue(r["cOpen"])
        self.assertFalse(r["afterBlur"])

    def test_escape_cancels_pending_open_without_swallowing(self):
        r = self.v("keyboard")
        self.assertEqual(r["esc3"], [False, False])
        self.assertTrue(r["pendingCancelled"])

    # ---- loading / error -----------------------------------------------------------
    def test_skeleton_at_card_ratio_then_image(self):
        r = self.v("loading_then_loaded")
        url = f"{API}/cards/{SID}?format=image&version=normal"
        self.assertEqual(r["preloadedBeforeOpen"], [url])        # preload during the open delay
        self.assertEqual(r["loading"], [{"state": "loading", "face": "front", "alt": "Sol Ring", "src": "", "ratio": "488 / 680"}])
        self.assertEqual(r["loaded"][0]["state"], "ok")
        self.assertEqual(r["loaded"][0]["src"], url)
        self.assertIn("CMM \u00b7 410", r["cap"]["text"])
        self.assertEqual(r["cap"]["chip"], "")                   # printing-specific: no marker

    def test_error_keeps_skeleton_and_captions_failure(self):
        r = self.v("error_state")
        self.assertEqual(r["faces"][0]["state"], "error")
        self.assertEqual(r["faces"][0]["ratio"], "488 / 680")
        self.assertEqual(r["skeleton"], 1)
        self.assertEqual(r["cap"]["status"], "Failed to load image")
        self.assertTrue(r["hasError"])
        self.assertEqual(r["imageState"], "err")

    def test_image_times_out_after_8s(self):
        r = self.v("timeout_state")
        self.assertEqual(r["before"], "loading")
        self.assertEqual(r["after"], "error")
        self.assertEqual(r["cap"]["status"], "Failed to load image")

    def test_failed_url_is_not_rerequested(self):
        self.assertEqual(self.v("failed_url_not_retried_while_sweeping")["loads"], 1)

    def test_name_only_preview_is_marked_and_logged(self):
        r = self.v("name_only_marked")
        self.assertEqual(r["cap"]["chip"], "Printing unknown")
        self.assertTrue(r["nameOnlyClass"])
        self.assertEqual(r["src"], f"{API}/cards/named?exact=Rhystic%20Study&format=image&version=normal")
        self.assertEqual(r["warns"], 1)
        self.assertEqual(r["printing"]["cap"]["chip"], "")
        self.assertFalse(r["printing"]["nameOnlyClass"])

    # ---- DFC ------------------------------------------------------------------------------
    def test_dfc_side_by_side_360_each(self):
        r = self.v("dfc_side_by_side")
        faces = r["faces"]
        self.assertEqual([f["face"] for f in faces], ["front", "back"])
        self.assertEqual([f["alt"] for f in faces], ["Delver of Secrets", "Insectile Aberration"])
        self.assertEqual([f["src"] for f in faces], [CDN + "/normal/front/d/e/delver.jpg", CDN + "/normal/back/d/e/delver.jpg"])
        self.assertEqual(r["width"], "728px")                    # 2 x 360 + 8
        self.assertEqual(r["dataFaces"], "2")
        self.assertEqual(r["side"], "right")
        self.assertFalse(r["cap"]["flip"])
        self.assertFalse(r["interactive"])

    def test_dfc_scales_down_but_not_below_280(self):
        r = self.v("dfc_scaled_down_but_above_floor")
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["side"], "right")
        self.assertEqual(r["width"], "720px")

    def test_dfc_falls_back_to_single_face_with_flip(self):
        r = self.v("dfc_narrow_flip")
        front, back = r["front"], r["back"]
        self.assertEqual([(f["face"], f["alt"]) for f in front["faces"]], [("front", "Delver of Secrets")])
        self.assertTrue(front["cap"]["flip"])
        self.assertEqual(front["cap"]["chip"], "Front")
        self.assertTrue(front["interactive"])
        self.assertEqual(front["side"], "below")
        self.assertEqual([(f["face"], f["alt"]) for f in back["faces"]], [("back", "Insectile Aberration")])
        self.assertEqual(back["cap"]["chip"], "Back")
        self.assertEqual(r["again"][0]["face"], "front")         # clicking the art flips too
        self.assertTrue(r["stillOpen"])
        self.assertTrue(r["closedAfterLeave"])

    def test_dfc_from_data_attributes(self):
        r = self.v("dfc_from_attributes")
        self.assertEqual([f["alt"] for f in r["faces"]], ["Delver of Secrets", "Insectile Aberration"])
        self.assertEqual(r["cap"]["chip"], "")
        nm = r["nameOnly"]
        self.assertEqual(len(nm["faces"]), 2)
        self.assertEqual(nm["cap"]["chip"], "Printing unknown")
        self.assertEqual(len(r["split"]), 1)                     # split cards are one image
        self.assertEqual(r["split"][0]["alt"], "Fire // Ice")

    def test_dfc_attribute_urls(self):
        r = self.v("dfc_from_attributes")
        name = "Delver%20of%20Secrets%20%2F%2F%20Insectile%20Aberration"
        self.assertEqual(r["loads"], [
            f"{API}/cards/{SID2}?format=image&version=normal",
            f"{API}/cards/{SID2}?format=image&version=normal&face=back",
            f"{API}/cards/named?exact={name}&format=image&version=normal",
            f"{API}/cards/named?exact={name}&format=image&version=normal&face=back",
            f"{API}/cards/{SID3}?format=image&version=normal",
        ])

    def test_high_dpr_requests_large(self):
        self.assertEqual(self.v("dpr_large"), f"{API}/cards/{SID}?format=image&version=large")

    # ---- placement ------------------------------------------------------------------------
    def test_placement_right_left_clamp_and_line_box(self):
        r = self.v("placement")
        # right of the 100..220 anchor: x = 220 + 12; card 420 x (420*680/488 + 32) centred on y=310
        self.assertEqual(r["right"]["side"], "right")
        self.assertEqual(r["right"]["width"], "420px")
        self.assertTrue(r["right"]["transform"].startswith("translate3d(232px,"), r["right"]["transform"])
        self.assertEqual(r["left"]["side"], "left")
        self.assertTrue(r["left"]["transform"].startswith("translate3d(768px,"), r["left"]["transform"])  # 1200 - 12 - 420
        self.assertTrue(r["clampedTop"].endswith(",8px,0)"), r["clampedTop"])
        self.assertTrue(r["wrapped"].startswith("translate3d(202px,"), r["wrapped"])  # second line box: 190 + 12

    # ---- touch ---------------------------------------------------------------------------
    def test_touch_long_press_tap_slop_and_sheet(self):
        r = self.v("touch")
        self.assertTrue(r["hoverIgnored"])
        self.assertFalse(r["lp449"])
        self.assertTrue(r["lp450"])
        self.assertTrue(r["clickSwallowed"])
        self.assertEqual(r["sheet"], {"isSheet": True, "scrimShown": True, "role": "dialog", "side": "sheet",
                                      "width": "358px", "label": "Sol Ring"})   # min(390 - 32, 360)
        self.assertFalse(r["afterScrim"])
        self.assertFalse(r["tapClickPrevented"])
        self.assertFalse(r["tapOpened"])
        self.assertTrue(r["slopCancelled"])
        self.assertTrue(r["smallMoveOpened"])
        self.assertFalse(r["afterClose"])
        self.assertTrue(r["tapInert"])
        self.assertTrue(r["swallowed"])

    def test_mouse_down_scroll_resize_and_removed_trigger_close(self):
        self.assertEqual(self.v("mouse_pointerdown_scroll_resize_close"),
                         {"pointerdown": False, "scroll": False, "resize": False, "removed": False})

    # ---- cache / lookup ----------------------------------------------------------------------
    def test_preload_and_cache(self):
        r = self.v("cache_and_preload")
        self.assertEqual(r["atIntent"], 1)
        self.assertEqual(r["afterRehover"], 1)                  # re-hover: no new image request
        self.assertEqual(r["urls"], [f"{API}/cards/{SID}?format=image&version=normal",
                                     f"{API}/cards/{SID2}?format=image&version=normal"])  # two printings differ
        self.assertIn(f"id:{SID}|normal|front", r["keys"])
        self.assertGreater(r["stats"]["hits"], 0)               # resolver cache is reused
        self.assertEqual(r["state"], "ok")

    def test_lookup_once_then_cdn_dfc(self):
        r = self.v("lookup_upgrades_to_cdn_dfc")
        self.assertEqual(r["beforeOpen"], 1)                    # lookup starts during the delay
        self.assertEqual(len(r["fetches"]), 1)                  # and happens once
        self.assertEqual(r["fetches"][0]["url"], f"{API}/cards/{SID2}")
        self.assertEqual(r["fetches"][0]["accept"], "application/json")
        self.assertEqual([f["src"] for f in r["again"]],
                         [CDN + "/normal/front/d/e/delver.jpg", CDN + "/normal/back/d/e/delver.jpg"])
        self.assertEqual(len(r["opened"]), 2)
        self.assertEqual(r["width"], "728px")
        # One API request per card: images wait for the lookup and come from the CDN.
        self.assertEqual(r["loads"], [CDN + "/normal/front/d/e/delver.jpg", CDN + "/normal/back/d/e/delver.jpg"])

    def test_lookup_failure_falls_back_to_api_image(self):
        r = self.v("lookup_failure_falls_back_to_api_image")
        url = f"{API}/cards/cmm/410?format=image&version=normal"
        self.assertEqual(r["fetches"], [f"{API}/cards/cmm/410"])
        self.assertEqual(r["loadsDuringLookup"], 0)
        self.assertEqual(r["loads"], [url])
        self.assertEqual(r["after"][0]["state"], "ok")
        self.assertEqual(r["after"][0]["src"], url)

    def test_lookup_upgrades_an_open_popover(self):
        r = self.v("lookup_upgrades_open_popover_in_place")
        self.assertEqual(r["before"], 1)
        self.assertEqual(r["beforeState"], "loading")           # skeleton while the lookup runs
        self.assertEqual(r["loadsBefore"], 0)                   # no api.scryfall.com image request
        self.assertEqual(r["loads"], [CDN + "/normal/front/d/e/delver.jpg", CDN + "/normal/back/d/e/delver.jpg"])
        self.assertEqual(r["after"], [CDN + "/normal/front/d/e/delver.jpg", CDN + "/normal/back/d/e/delver.jpg"])
        self.assertTrue(r["open"])

    # ---- API ---------------------------------------------------------------------------------
    def test_exports_and_markup_helpers(self):
        r = self.v("api")
        for k in ["attrs", "bind", "create", "defaults", "init", "unbind", "wrap"]:
            self.assertIn(k, r["exports"])
        d = r["defaults"]
        self.assertEqual((d["openCold"], d["openWarm"], d["warmWindow"], d["close"], d["focusOpen"], d["press"],
                          d["pressSlop"], d["imgTimeout"], d["gap"], d["margin"], d["width"], d["widthDfc"],
                          d["minFaceWidth"], d["faceGap"], d["capH"], d["sheetWidth"]),
                         (300, 60, 250, 120, 400, 450, 10, 8000, 12, 8, 420, 360, 280, 8, 28, 360))
        self.assertEqual(r["attrs"], ' data-card-preview data-cp-id="x&quot;y" data-cp-set="cmm" data-cp-cn="410"'
                                     ' data-cp-name="A &lt;b&gt; &amp; &quot;c&quot;"')
        self.assertIn('data-cp-faces="2"', r["attrsDfc"])
        self.assertIn('data-cp-layout="transform"', r["attrsDfc"])
        self.assertEqual(r["attrsNameOnly"], ' data-card-preview data-cp-name="Sol Ring"')
        self.assertEqual(r["wrap"], '<span class="hud-cp-trigger hud-cp-name" tabindex="0" data-card-preview'
                                    f' data-cp-id="{SID}" data-cp-name="Sol Ring">Sol Ring</span>')
        self.assertTrue(r["wrapLink"].startswith('<span class="hud-cp-trigger" data-card-preview'))  # bad tag rejected
        self.assertNotIn("tabindex", r["wrapLink"])
        self.assertGreater(r["listenersBefore"], 0)
        self.assertEqual(r["listenersAfter"], 0)
        self.assertEqual(r["popoverRemoved"], 0)

    def test_stylesheet_tokens_match_spec(self):
        css = CHP_CSS.read_text()
        for token in ["--z-preview:    1050", "--cp-w:         420px", "--cp-w-dfc:     360px", "--cp-w-min:     280px",
                      "--cp-gap:       12px", "--cp-margin:    8px", "--cp-cap-h:     28px", "--cp-sheet-w:   360px",
                      "4.76% / 3.41%", "rgba(0, 0, 0, 0.5)", "translateY(var(--cp-slide))", "--cp-in:        140ms",
                      "--cp-out:       100ms", "prefers-reduced-motion: reduce", "pointer-events: none"]:
            self.assertIn(token, css)

    def test_loaded_app_wide_from_base(self):
        base = (ROOT / "templates" / "base.html").read_text()
        for needle in ("card_hover_preview.css", "scryfall_image.js", "card_hover_preview.js"):
            self.assertIn(needle, base)
        self.assertLess(base.index("scryfall_image.js"), base.index("card_hover_preview.js"))


if __name__ == "__main__":
    unittest.main()
