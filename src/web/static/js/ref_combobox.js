/*!
 * ref_combobox.js — category ref input helpers (loaded globally by base.html).
 *
 * Catalog: GET /api/composition/refs?scope=preset|deck[&deck_id=ID] -> {refs:[{ref,label,kind,aliases}]}
 * (the refs the composition validator accepts). Fetched once per scope/deck and cached.
 * If the fetch fails, a console.warn is logged and exact "kind:id" refs are still accepted.
 *
 * ── CategoryPillInput (single text input -> autocorrected pills) ──
 *   const pi = CategoryPillInput.mount(containerEl, {
 *     scope: 'preset' | 'deck',      // catalog scope (default 'preset')
 *     deckId: '123',                 // required for scope 'deck'
 *     initial: ['core:ramp', {ref:'core:ramp', target:10, min:8}],  // strings or entry objects
 *     inputName: 'ref' | (entry, i) => name,  // adds <input type=hidden name=… value=ref> per pill
 *     onChange(entries) {},          // after every add/remove (not on initial render)
 *     renderExtraFields(entry, pillEl) {},    // optional; append per-category fields into pillEl.
 *                                    // entry is the live object: mutate it (entry.target = 5) and call
 *                                    // pi.notify() to fire onChange.
 *     placeholder: 'Add category…',
 *   });
 *   pi.getEntries()   -> copies of [{ref, name, …extra}] in pill order
 *   pi.addEntry(ref|text) -> Promise<boolean>  (autocorrects; false on duplicate / no plausible match)
 *   pi.removeEntry(ref), pi.focus(), pi.notify(), pi.destroy()
 *   Pills: <span class="cpi-pill" data-ref="core:ramp">; the input has role=combobox.
 *   Enter/blur with free text autocorrects to the best match, else shows an inline error (no pill).
 *   ArrowUp/Down move through suggestions, Enter picks, Escape closes.
 *
 * ── RefCombobox (legacy API used by composition_presets / deck_composition) ──
 *   RefCombobox.attach(inputEl, {scope, deckId})   suggestions dropdown on a plain text input
 *   RefCombobox.load(scope, deckId)                -> Promise<catalog array>
 *   RefCombobox.showFixes(box, inputs, catalog, onFix)   "Did you mean" buttons for unknown refs
 */
(function () {
  'use strict';
  var cache = {};

  function load(scope, deckId) {
    scope = scope || 'preset';
    var key = scope + '|' + (deckId || '');
    if (!cache[key]) {
      var url = '/api/composition/refs?scope=' + encodeURIComponent(scope) + (deckId ? '&deck_id=' + encodeURIComponent(deckId) : '');
      cache[key] = fetch(url).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).then(function (d) {
        return (d && d.refs || []).map(function (e) {
          return { ref: e.ref, name: e.label || e.ref, kind: e.kind, aliases: e.aliases || [] };
        });
      }).catch(function (err) {
        console.warn('[ref_combobox] could not load refs; exact refs only:', err && err.message);
        return [];
      });
    }
    return cache[key];
  }

  function norm(s) { return String(s || '').trim().toLowerCase().replace(/[\s_-]+/g, ' '); }
  var REF_RE = /^[a-z]+:[A-Za-z0-9_:. -]+$/;

  function lev(a, b) {
    var p = [], i, j;
    for (j = 0; j <= b.length; j++) p[j] = j;
    for (i = 1; i <= a.length; i++) {
      var prev = p[0]; p[0] = i;
      for (j = 1; j <= b.length; j++) {
        var t = p[j];
        p[j] = Math.min(p[j] + 1, p[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
        prev = t;
      }
    }
    return p[b.length];
  }

  // Ranked matches: exact, prefix (ref/name/alias), substring. Lower rank = better.
  function match(cat, text) {
    var q = norm(text);
    if (!q) return [];
    var out = [];
    cat.forEach(function (e) {
      var keys = [e.ref, e.ref.split(':').slice(1).join(':'), e.name].concat(e.aliases || []).map(norm);
      var rank = 9;
      keys.forEach(function (k) {
        if (k === q) rank = Math.min(rank, 0);
        else if (k.indexOf(q) === 0) rank = Math.min(rank, 1);
        else if (k.indexOf(q) >= 0) rank = Math.min(rank, 2);
      });
      if (rank < 9) out.push({ e: e, rank: rank });
    });
    out.sort(function (a, b) { return a.rank - b.rank || a.e.ref.localeCompare(b.e.ref); });
    return out.map(function (x) { return x.e; });
  }

  // Best single autocorrect target, or null when nothing is plausible.
  function bestMatch(cat, text) {
    var q = norm(text);
    if (!q) return null;
    var exact = cat.filter(function (e) { return norm(e.ref) === q; })[0];
    if (exact) return exact;
    var m = match(cat, text);
    if (m.length) return m[0];
    if (q.length < 4) return null;
    var best = null, bd = 3;
    cat.forEach(function (e) {
      [e.ref.split(':').slice(1).join(':'), e.name].concat(e.aliases || []).forEach(function (k) {
        var d = lev(q, norm(k));
        if (d < bd && d <= Math.max(1, Math.floor(q.length / 4))) { bd = d; best = e; }
      });
    });
    return best;
  }

  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (text != null) n.textContent = text;
    return n;
  }

  var uid = 0;
  function dropdown(input, getItems, onPick) {
    var id = 'cpi-lb-' + (++uid);
    var box = el('div', { 'class': 'cpi-dropdown', role: 'listbox', id: id });
    box.hidden = true;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', id);
    var active = -1, items = [];
    function close() { box.hidden = true; active = -1; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); }
    function mark() {
      Array.prototype.forEach.call(box.children, function (c, i) {
        c.classList.toggle('is-active', i === active);
        c.setAttribute('aria-selected', i === active ? 'true' : 'false');
        if (i === active) input.setAttribute('aria-activedescendant', c.id);
      });
    }
    function open() {
      items = getItems().slice(0, 8);
      box.replaceChildren();
      if (!items.length) { close(); return; }
      items.forEach(function (e, i) {
        var o = el('div', { 'class': 'cpi-option', role: 'option', id: id + '-' + i });
        o.appendChild(el('span', { 'class': 'cpi-opt-name' }, e.name));
        o.appendChild(el('span', { 'class': 'cpi-opt-ref' }, e.ref));
        o.addEventListener('mousedown', function (ev) { ev.preventDefault(); close(); onPick(e); });
        box.appendChild(o);
      });
      box.hidden = false; active = -1; input.setAttribute('aria-expanded', 'true');
    }
    // Returns true when the key was consumed.
    function key(ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        if (box.hidden) open();
        if (!items.length) return true;
        var d = ev.key === 'ArrowDown' ? 1 : -1;
        active = (active + d + items.length) % items.length; mark(); ev.preventDefault(); return true;
      }
      if (ev.key === 'Escape') { close(); return true; }
      if (ev.key === 'Enter' && !box.hidden && active >= 0) { ev.preventDefault(); var e = items[active]; close(); onPick(e); return true; }
      return false;
    }
    return { el: box, open: open, close: close, key: key };
  }

  // ───────── CategoryPillInput ─────────
  function mount(container, opts) {
    if (!container) { console.warn('[CategoryPillInput] no container'); return null; }
    opts = opts || {};
    var cat = [], entries = [], destroyed = false;
    var ready = load(opts.scope, opts.deckId).then(function (c) {
      cat = c;
      entries.forEach(function (en) { if (en._pill) { var f = byRef(en.ref); if (f && en.name === en.ref) { en.name = f.name; en._label.textContent = f.name; } } });
    });
    function byRef(ref) { return cat.filter(function (e) { return e.ref === ref; })[0]; }

    var root = el('div', { 'class': 'cpi' });
    var pills = el('div', { 'class': 'cpi-pills' });
    var input = el('input', { type: 'text', 'class': 'cpi-input', autocomplete: 'off', 'aria-label': 'Category', placeholder: opts.placeholder || 'Add category…' });
    var err = el('div', { 'class': 'cpi-error', role: 'alert' });
    var dd = dropdown(input, function () { return match(cat, input.value); }, function (e) { input.value = ''; add(e.ref); });
    var wrap = el('div', { 'class': 'cpi-field' });
    wrap.appendChild(input); wrap.appendChild(dd.el);
    root.appendChild(pills); root.appendChild(wrap); root.appendChild(err);
    container.appendChild(root);

    function snapshot() {
      return entries.map(function (en) {
        var c = {}; Object.keys(en).forEach(function (k) { if (k[0] !== '_') c[k] = en[k]; }); return c;
      });
    }
    function notify() { if (opts.onChange) opts.onChange(snapshot()); }
    function setErr(m) { err.textContent = m || ''; }
    function has(ref) { return entries.some(function (e) { return e.ref.toLowerCase() === ref.toLowerCase(); }); }

    function render(en, silent) {
      var pill = el('span', { 'class': 'cpi-pill', 'data-ref': en.ref });
      var label = el('span', { 'class': 'cpi-pill-label', title: en.ref }, en.name);
      var rm = el('button', { type: 'button', 'class': 'cpi-pill-rm', 'aria-label': 'Remove ' + en.name }, '×');
      rm.addEventListener('click', function () { removeEntry(en.ref); });
      pill.appendChild(label); pill.appendChild(rm);
      if (opts.inputName) {
        var nm = typeof opts.inputName === 'function' ? opts.inputName(en, entries.indexOf(en)) : opts.inputName;
        pill.appendChild(el('input', { type: 'hidden', name: nm, value: en.ref }));
      }
      en._pill = pill; en._label = label;
      if (opts.renderExtraFields) { try { opts.renderExtraFields(en, pill); } catch (e) { console.warn('[CategoryPillInput] renderExtraFields failed', e); } }
      pills.appendChild(pill);
    }

    function put(ref, name, extra, silent) {
      if (has(ref)) return false;
      var en = {}; Object.keys(extra || {}).forEach(function (k) { en[k] = extra[k]; });
      en.ref = ref; en.name = name || ref;
      entries.push(en); render(en);
      if (!silent) notify();
      return true;
    }

    function add(text) {
      return ready.then(function () {
        if (destroyed) return false;
        text = String(text || '').trim();
        if (!text) return false;
        var hit = bestMatch(cat, text);
        var ref, name;
        if (hit) { ref = hit.ref; name = hit.name; }
        else if (!cat.length && REF_RE.test(text)) { ref = text; name = text; }   // catalog unavailable: exact refs only
        else { setErr('No matching category for “' + text + '”.'); return false; }
        if (has(ref)) { setErr(name + ' is already added.'); input.value = ''; return false; }
        setErr(''); put(ref, name); input.value = ''; dd.close();
        return true;
      });
    }

    function removeEntry(ref) {
      var i = entries.map(function (e) { return e.ref; }).indexOf(ref);
      if (i < 0) return false;
      var en = entries.splice(i, 1)[0];
      if (en._pill) en._pill.remove();
      notify(); return true;
    }

    input.addEventListener('input', function () { setErr(''); ready.then(dd.open); });
    input.addEventListener('keydown', function (ev) {
      if (ev.isComposing) return;
      if (dd.key(ev)) return;
      if (ev.key === 'Enter') { ev.preventDefault(); add(input.value); }
      else if (ev.key === 'Backspace' && !input.value && entries.length) { removeEntry(entries[entries.length - 1].ref); }
    });
    input.addEventListener('blur', function () { dd.close(); if (input.value.trim()) add(input.value); });

    (opts.initial || []).forEach(function (it) {
      var o = typeof it === 'string' ? { ref: it } : it;
      if (o && o.ref) put(o.ref, o.name || o.label, o, true);
    });

    return {
      getEntries: snapshot, addEntry: add, removeEntry: removeEntry, notify: notify,
      focus: function () { input.focus(); },
      destroy: function () { destroyed = true; root.remove(); },
    };
  }

  // ───────── Legacy RefCombobox ─────────
  function attach(input, o) {
    if (!input) return null;
    o = o || {};
    var cat = [];
    var ready = load(o.scope, o.deckId).then(function (c) { cat = c; });
    var dd = dropdown(input, function () { return match(cat, input.value); }, function (e) {
      input.value = e.ref; input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    var host = input.parentNode;
    if (host) { host.classList.add('refcb-host'); host.appendChild(dd.el); }
    input.addEventListener('input', function () { ready.then(dd.open); });
    input.addEventListener('focus', function () { if (input.value) ready.then(dd.open); });
    input.addEventListener('keydown', function (ev) { dd.key(ev); });
    input.addEventListener('blur', dd.close);
    return { close: dd.close };
  }

  function isKnown(cat, v) {
    v = String(v || '').trim();
    return !v || /^theme:kindred:.+/.test(v) || cat.some(function (e) { return e.ref === v; });
  }

  function showFixes(box, inputs, cat, onFix) {
    if (!box) return;
    box.replaceChildren();
    (inputs || []).forEach(function (inp) {
      var v = inp.value.trim();
      if (isKnown(cat, v)) return;
      var sugg = match(cat, v);
      if (!sugg.length) { var b = bestMatch(cat, v); if (b) sugg = [b]; }
      if (!sugg.length) return;
      var row = el('div', { 'class': 'refcb-fix' });
      row.appendChild(el('span', { 'class': 'refcb-fix-label' }, '“' + v + '” — did you mean '));
      sugg.slice(0, 5).forEach(function (e) {
        var btn = el('button', { type: 'button', 'class': 'refcb-fix-btn' }, e.ref);
        btn.addEventListener('click', function () { inp.value = e.ref; row.remove(); if (onFix) onFix(); });
        row.appendChild(btn);
      });
      box.appendChild(row);
    });
  }

  window.CategoryPillInput = { mount: mount };
  window.RefCombobox = { attach: attach, load: load, showFixes: showFixes };
})();
