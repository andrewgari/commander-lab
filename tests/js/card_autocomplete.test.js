// Run with: node --test tests/js/card_autocomplete.test.js
// Exercise the actual shared browser script with a small DOM and controlled fetches/timers.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../src/web/static/js/card_autocomplete.js'), 'utf8');

class Element {
    constructor(id = '') {
        this.id = id;
        this.value = '';
        this.hidden = false;
        this.children = [];
        this.attributes = {};
        this.handlers = {};
        this.classList = { add() {} };
        this.style = {};
        this.parentElement = null;
    }
    set innerHTML(value) { this.html = value; this.children = []; }
    get innerHTML() { return this.html || ''; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] || null; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); }
    removeEventListener(name, callback) { this.handlers[name] = (this.handlers[name] || []).filter((f) => f !== callback); }
    appendChild(child) { child.parentElement = this; this.children.push(child); }
    contains(other) { return this === other || this.children.some((child) => child.contains(other)); }
    fire(name, properties = {}) {
        const event = { target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties };
        for (const callback of this.handlers[name] || []) callback(event);
        return event;
    }
}

function harness(options = {}) {
    const input = new Element('input');
    const list = new Element('results');
    list.parentElement = new Element('parent');
    const document = new Element('document');
    document.createElement = () => new Element();
    document.getElementById = (id) => list.children.find((child) => child.id === id) || null;
    const timers = new Map();
    const requests = [];
    const selected = [];
    let timerId = 0;
    const window = { getComputedStyle: () => ({ position: 'relative' }) };
    const context = {
        window, document, getComputedStyle: window.getComputedStyle,
        setTimeout: (callback) => { const id = ++timerId; timers.set(id, callback); return id; },
        clearTimeout: (id) => timers.delete(id),
        fetch: (url) => new Promise((resolve, reject) => requests.push({ url, resolve, reject }))
    };
    vm.runInNewContext(source, context);
    const handle = window.CardAutocomplete.attach(input, {
        listEl: list, onSelect: (card) => selected.push(card.name), ...options
    });
    return {
        input, list, document, requests, selected, handle, timers,
        type(value) { input.value = value; input.fire('input'); },
        key(key, extra = {}) { return input.fire('keydown', { key, ...extra }); },
        runTimer() { const [id, callback] = timers.entries().next().value; timers.delete(id); callback(); },
        respond(index, results, extra = {}) {
            requests[index].resolve({ ok: true, json: async () => ({ results, ...extra }) });
        },
        async settle() { await new Promise((resolve) => setImmediate(resolve)); }
    };
}

const card = (name) => ({ name });

test('Enter during debounce starts the current search and selects Kaalia once', async () => {
    const h = harness({ commandersOnly: true, commitPendingOnEnter: true });
    h.type('Kaalia of the Vast');
    assert.equal(h.requests.length, 0);
    assert.equal(h.key('Enter').defaultPrevented, true);
    assert.equal(h.key('Enter').defaultPrevented, true);
    assert.equal(h.requests.length, 1);
    assert.equal(h.timers.size, 0);
    assert.match(h.requests[0].url, /commanders_only=true/);
    h.respond(0, [card('Kaalia of the Vast')]);
    await h.settle();
    assert.deepEqual(h.selected, ['Kaalia of the Vast']);
    assert.equal(h.list.hidden, true);
});

test('typing invalidates old rows; Enter selects the new result, not the old one', async () => {
    const h = harness({ commitPendingOnEnter: true });
    h.type('Kaalia'); h.runTimer();
    h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    assert.equal(h.list.children[0].getAttribute('role'), 'option');
    const oldRow = h.list.children[0];
    h.type('Krenko');
    assert.equal(h.list.children.some((row) => row.getAttribute('role') === 'option'), false);
    oldRow.fire('mousedown');
    assert.deepEqual(h.selected, []);
    h.key('Enter');
    h.respond(1, [card('Krenko, Mob Boss')]); await h.settle();
    assert.deepEqual(h.selected, ['Krenko, Mob Boss']);
});

test('in-flight Enter waits for the current query and stale responses cannot commit', async () => {
    const h = harness({ commitPendingOnEnter: true });
    h.type('Kaalia'); h.runTimer(); h.key('Enter');
    h.type('Krenko'); h.runTimer(); h.key('Enter');
    assert.equal(h.requests.length, 2); // Enter did not duplicate either request
    h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    assert.deepEqual(h.selected, []);
    h.respond(1, [card('Krenko, Mob Boss')]); await h.settle();
    assert.deepEqual(h.selected, ['Krenko, Mob Boss']);
});

test('editing after Enter cancels the selection intent even when the new search succeeds', async () => {
    const h = harness({ commitPendingOnEnter: true });
    h.type('Kaalia'); h.key('Enter');
    h.type('Krenko'); h.runTimer();
    h.respond(1, [card('Krenko, Mob Boss')]); await h.settle();
    h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    assert.deepEqual(h.selected, []);
    assert.equal(h.list.children[0].getAttribute('role'), 'option');
});

test('Escape during debounce cancels the timer without issuing a request', () => {
    const h = harness();
    h.type('Kaalia'); h.key('Escape');
    assert.equal(h.timers.size, 0);
    assert.equal(h.requests.length, 0);
    assert.equal(h.list.hidden, true);
});

test('dismissed result rows cannot be selected by a late mouse event', async () => {
    const h = harness();
    h.type('Kaalia'); h.runTimer(); h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    const row = h.list.children[0];
    h.key('Escape');
    row.fire('mousedown');
    assert.deepEqual(h.selected, []);
});

for (const dismissal of ['Escape', 'blur', 'outside', 'close', 'destroy']) {
    test(`${dismissal} cancels pending Enter and prevents late selection`, async () => {
        const h = harness({ commitPendingOnEnter: true });
        h.type('Kaalia'); h.key('Enter');
        if (dismissal === 'Escape') h.key('Escape');
        else if (dismissal === 'blur') h.input.fire('blur', { relatedTarget: null });
        else if (dismissal === 'outside') h.document.fire('mousedown', { target: new Element() });
        else h.handle[dismissal]();
        h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
        assert.deepEqual(h.selected, []);
        assert.equal(h.list.hidden, true);
    });
}

test('empty, failed, and degraded searches retain their distinct behavior', async () => {
    const h = harness({ commitPendingOnEnter: true });
    h.type('empty'); h.key('Enter'); h.respond(0, []); await h.settle();
    assert.deepEqual(h.selected, []);
    assert.match(h.list.children[0].className, /--empty/);
    h.type('error'); h.key('Enter'); h.requests[1].reject(new Error('offline')); await h.settle();
    assert.deepEqual(h.selected, []);
    assert.match(h.list.children[0].className, /--error/);
    h.type('local'); h.runTimer(); h.respond(2, [card('Local Commander')], { degraded: true }); await h.settle();
    assert.match(h.list.children[0].className, /--degraded/);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Local Commander']);
    h.type('offline'); h.key('Enter'); h.respond(3, [card('Offline Commander')], { degraded: true }); await h.settle();
    assert.deepEqual(h.selected, ['Local Commander']);
    assert.match(h.list.children[0].className, /--degraded/);
    h.key('ArrowDown');
    assert.match(h.list.children[0].className, /--degraded/);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Local Commander', 'Offline Commander']);
});

test('mouse and arrow selection still work; composition does not commit', async () => {
    const h = harness();
    h.type('ka'); h.runTimer(); h.respond(0, [card('First'), card('Second')]); await h.settle();
    h.key('Enter', { isComposing: true });
    assert.deepEqual(h.selected, []);
    h.key('ArrowDown'); h.key('ArrowDown'); h.key('Enter');
    assert.deepEqual(h.selected, ['Second']);
    h.type('ka'); h.runTimer(); h.respond(1, [card('First')]); await h.settle();
    const mouse = h.list.children[0].fire('mousedown');
    assert.equal(mouse.defaultPrevented, true);
    assert.deepEqual(h.selected, ['Second', 'First']);
});

test('non-first Enter policy does not auto-select a pending result', async () => {
    const h = harness({ commitOnEnter: 'highlighted', commitPendingOnEnter: true });
    h.type('Kaalia'); h.key('Enter'); h.runTimer();
    h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    assert.deepEqual(h.selected, []);
    h.key('ArrowDown'); h.key('Enter');
    assert.deepEqual(h.selected, ['Kaalia of the Vast']);
});

test('local-only sources honor pending Enter and network errors with extras remain selectable', async () => {
    const local = harness({ endpoint: null, commitPendingOnEnter: true, extraSource: async () => ['Local Commander'] });
    local.type('local'); local.key('Enter'); await local.settle();
    assert.deepEqual(local.selected, ['Local Commander']);
    const mixed = harness({ extraSource: async () => ['Owned Commander'] });
    mixed.type('owned'); mixed.runTimer(); mixed.requests[0].reject(new Error('offline')); await mixed.settle();
    assert.match(mixed.list.children[0].className, /--error/);
    mixed.key('Enter');
    assert.deepEqual(mixed.selected, ['Owned Commander']);
});

test('pending Enter is opt-in and leaves the event available to a consumer fallback', async () => {
    const h = harness();
    h.type('Custom Card');
    const event = h.key('Enter');
    assert.equal(event.defaultPrevented, false);
    assert.equal(h.requests.length, 0);
    assert.equal(h.timers.size, 1);
    h.runTimer(); h.respond(0, [card('Different Card')]); await h.settle();
    assert.deepEqual(h.selected, []);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Different Card']);
});

test('default consumer Enter also bubbles while a request is in flight', async () => {
    const h = harness();
    h.type('Custom Card'); h.runTimer();
    assert.equal(h.key('Enter').defaultPrevented, false);
    h.respond(0, [card('Different Card')]); await h.settle();
    assert.deepEqual(h.selected, []);
    assert.equal(h.list.children[0].getAttribute('role'), 'option');
});

test('pending search prevents form submission unless the consumer opts out', async () => {
    const h = harness();
    h.input.form = {};
    h.type('Kaalia');
    assert.equal(h.key('Enter').defaultPrevented, true); // debounce
    assert.equal(h.requests.length, 0);
    h.runTimer();
    assert.equal(h.key('Enter').defaultPrevented, true); // in flight
    h.respond(0, [card('Kaalia of the Vast')]); await h.settle();
    assert.deepEqual(h.selected, []);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Kaalia of the Vast']);
    const tune = harness({ allowPendingFormEnter: true });
    tune.input.form = {};
    tune.type('Custom Card');
    assert.equal(tune.key('Enter').defaultPrevented, false);
    tune.runTimer();
    assert.equal(tune.key('Enter').defaultPrevented, false);
});

test('held Enter cannot confirm a degraded result; a fresh Enter can', async () => {
    const h = harness({ commitPendingOnEnter: true });
    h.type('Kaalia'); h.key('Enter');
    h.respond(0, [card('Kaalia of the Vast')], { degraded: true }); await h.settle();
    assert.match(h.list.children[0].className, /--degraded/);
    assert.equal(h.key('Enter', { repeat: true }).defaultPrevented, true);
    assert.deepEqual(h.selected, []);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Kaalia of the Vast']);
});

test('pending Enter with network error and local extras shows error and requires confirmation', async () => {
    const h = harness({ commitPendingOnEnter: true, extraSource: async () => ['Owned Commander'] });
    h.type('owned'); h.key('Enter'); h.requests[0].reject(new Error('offline')); await h.settle();
    assert.deepEqual(h.selected, []);
    assert.match(h.list.children[0].className, /--error/);
    assert.equal(h.key('Enter', { repeat: true }).defaultPrevented, true);
    assert.deepEqual(h.selected, []);
    h.key('ArrowDown');
    assert.match(h.list.children[0].className, /--error/);
    h.key('Enter');
    assert.deepEqual(h.selected, ['Owned Commander']);
});
