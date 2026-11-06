/* Auto Tag UI (AT-UI): role / theme tags on the deck page.
   Spec + endpoint shapes: docs/AUTO_TAG_DESIGN.md, docs/design/auto-tag/UX_SPEC.md.

       var at = AutoTag.mount({ deckId, strip, filterbar, getCards, onChange });
       at.chips(cardName, 3)      -> chip HTML for a decklist tile / table cell
       at.filter(cards)           -> cards that pass the active tag filter
       at.groups(cards)           -> [{label, cards}] when "Group by Tag" is on
       at.bindEditor(el, name)    -> per-card editor (card detail modal)

   Data: GET/POST/DELETE /api/decks/{id}/tags (deck) and /card-tags (per card),
   POST /api/decks/{id}/auto-tag (engine; 501 until it ships).
   Entry shape: { tag, source: "auto" | "user", created_at, version }.
   Source rules: Auto Tag replaces "auto" entries and never touches "user" ones.
   Anything the user adds is "user"; the ◇ glyph on an auto chip converts it to
   "user" (keep). Removing an auto tag just deletes it (the API has no tombstone).

   Pure helpers (effective, roleCounts, filterCards, tagGroups, chipsHtml ...)
   need no DOM; tests/test_auto_tag_ui.py runs them in node. Every tag name goes
   through esc() before it reaches innerHTML. */
(function (root) {
    "use strict";

    /* ---- Tag ids and labels -----------------------------------------------
       The engine (autotag/engine.py FUNCTION_TAGS / THEME_TAGS) stores snake_case
       ids; kindred themes are "kindred:<subtype>". Ids are what we store, send and
       compare. label(id) is display only, so a tag the user adds from the picker is
       byte-identical to the engine's and dedupes against it.
       The picker offers only the fixed list (free text would make a tag nobody
       else uses, which can't be filtered on). kindred:* is engine-only. */
    var FUNCTION_IDS = ["ramp", "card_advantage", "removal", "board_wipe", "counterspell", "finisher", "tutor", "recursion", "protection", "mana_fixing"];
    var THEME_IDS = ["lands_matter", "tokens", "counters", "sacrifice", "graveyard", "spellslinger", "artifacts", "enchantments", "lifegain", "voltron", "blink"];
    var LABELS = {
        ramp: "Ramp", card_advantage: "Draw", removal: "Removal", board_wipe: "Board wipe", counterspell: "Counterspell",
        finisher: "Finisher", tutor: "Tutor", recursion: "Recursion", protection: "Protection", mana_fixing: "Fixing",
        lands_matter: "Lands", tokens: "Tokens", counters: "+1/+1 counters", sacrifice: "Sacrifice", graveyard: "Graveyard",
        spellslinger: "Spellslinger", artifacts: "Artifacts", enchantments: "Enchantments", lifegain: "Lifegain", voltron: "Voltron", blink: "Blink"
    };
    function label(id) {
        id = String(id == null ? "" : id);
        if (LABELS[id]) return LABELS[id];
        if (id.indexOf("kindred:") === 0) {
            var sub = id.slice(8).replace(/[-_]+/g, " ").replace(/(^|\s)(\S)/g, function (m, a, b) { return a + b.toUpperCase(); });
            return (sub || "Creature") + " kindred";
        }
        var t = id.replace(/_+/g, " ").trim();    // unknown id: readable, never raw snake_case
        return t ? t.charAt(0).toUpperCase() + t.slice(1) : id;
    }
    function byLabel(a, b) { var x = label(a).toLowerCase(), y = label(b).toLowerCase(); return x < y ? -1 : x > y ? 1 : a < b ? -1 : a > b ? 1 : 0; }
    var VOCAB = [
        { group: "Roles", tags: FUNCTION_IDS.slice() },
        { group: "Themes", tags: THEME_IDS.slice() }
    ];
    // Not a NUL: the sentinel is written into data-at-filter attributes, and the HTML parser turns NUL into U+FFFD.
    var UNTAGGED = "__untagged__";
    var MAX_CHIPS = 3;       // per tile / row
    var INLINE_FILTERS = 8;  // filter chips before "+N more"
    var GLYPH = { auto: "\u25C7", user: "\u25C6" };   // ◇ auto · ◆ yours

    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function plural(n, one, many) { return n + " " + (n === 1 ? one : many || one + "s"); }

    /* ---- Pure helpers ---------------------------------------------------- */

    /* One chip per tag name. A user entry beats an auto entry with the same
       name; users first, then newest (version), then A-Z by label. */
    function effective(entries) {
        var by = {};
        (entries || []).forEach(function (e) {
            if (!e || !e.tag) return;
            var cur = by[e.tag];
            if (!cur || (e.source === "user" && cur.source !== "user")) by[e.tag] = e;
        });
        return Object.keys(by).map(function (k) { return by[k]; }).sort(function (a, b) {
            if ((a.source === "user") !== (b.source === "user")) return a.source === "user" ? -1 : 1;
            if ((b.version || 0) !== (a.version || 0)) return (b.version || 0) - (a.version || 0);
            return byLabel(a.tag, b.tag);
        });
    }
    function tagsOf(map, name) { return effective((map || {})[name]); }

    /* [{tag, n}] cards per tag, most first (ties A-Z by label). */
    function roleCounts(cards, map) {
        var n = {};
        (cards || []).forEach(function (c) { tagsOf(map, c.name).forEach(function (e) { n[e.tag] = (n[e.tag] || 0) + 1; }); });
        return Object.keys(n).map(function (t) { return { tag: t, n: n[t] }; }).sort(function (a, b) { return b.n - a.n || byLabel(a.tag, b.tag); });
    }
    function untaggedCount(cards, map) { return (cards || []).filter(function (c) { return !tagsOf(map, c.name).length; }).length; }

    function filterCards(cards, map, f) {
        if (!f) return cards;
        return cards.filter(function (c) {
            var t = tagsOf(map, c.name);
            return f === UNTAGGED ? !t.length : t.some(function (e) { return e.tag === f; });
        });
    }

    /* Grouping by tag: a card sits in every group it is tagged with, so group
       counts add up to more than the deck. Untagged cards go last. */
    function tagGroups(cards, map) {
        var groups = {}, rest = [];
        cards.forEach(function (c) {
            var t = tagsOf(map, c.name);
            if (!t.length) { rest.push(c); return; }
            t.forEach(function (e) { (groups[e.tag] = groups[e.tag] || []).push(c); });
        });
        var out = Object.keys(groups).map(function (t) { return { label: label(t), tag: t, cards: groups[t] }; })
            .sort(function (a, b) { return b.cards.length - a.cards.length || byLabel(a.tag, b.tag); });
        if (rest.length) out.push({ label: "Untagged", tag: UNTAGGED, cards: rest });
        return out;
    }

    function summarize(cards, map, deckTags) {
        var autoN = 0, userN = 0, tagged = 0;
        (cards || []).forEach(function (c) {
            var t = tagsOf(map, c.name);
            if (t.length) tagged += 1;
            t.forEach(function (e) { if (e.source === "user") userN += 1; else autoN += 1; });
        });
        effective(deckTags).forEach(function (e) { if (e.source === "user") userN += 1; else autoN += 1; });
        return { cards: (cards || []).length, tagged: tagged, auto: autoN, user: userN, any: autoN + userN > 0 };
    }

    function chipHtml(e, o) {
        o = o || {};
        var user = e.source === "user", cls = "hud-chip at-chip " + (user ? "hud-chip--signal at-chip--user" : "at-chip--auto") + (o.fresh ? " is-new" : "");
        var who = user ? "yours" : "auto", L = label(e.tag);
        var glyph = '<span class="at-glyph" aria-hidden="true">' + GLYPH[e.source === "user" ? "user" : "auto"] + "</span>";
        var style = o.i != null ? ' style="--i:' + o.i + '"' : "";
        if (!o.edit) {
            return '<span class="' + cls + '" title="' + esc(L) + " \u00B7 " + who + '"' + style + ">" + glyph +
                '<span class="at-chip-text">' + esc(L) + '</span><span class="hud-sr"> (' + who + ")</span></span>";
        }
        // Editable: ◇ keeps an auto tag as yours; ✕ removes it.
        var lead = user ? glyph : '<button type="button" class="at-pin" data-at-pin="' + esc(e.tag) + '" title="Auto tag. Keep as yours so Auto Tag never replaces it." aria-label="Keep ' + esc(e.tag) + ' as your tag (currently auto)">' + GLYPH.auto + "</button>";
        return '<span class="' + cls + '" title="' + esc(L) + " \u00B7 " + who + '"' + style + ">" + lead +
            '<span class="at-chip-text">' + esc(L) + (user ? '<span class="hud-sr"> (yours)</span>' : "") + "</span>" +
            '<button type="button" class="hud-chip-remove" data-at-remove="' + esc(e.tag) + '" aria-label="Remove ' + esc(e.tag) + " (" + who + ')">\u2715</button></span>';
    }

    /* Compact chips for a tile / row: at most `max`, then "+N". */
    function chipsHtml(entries, max, fresh) {
        max = max || MAX_CHIPS;
        if (!entries.length) return "";
        var shown = entries.slice(0, max), more = entries.slice(max);
        var html = shown.map(function (e, i) { return chipHtml(e, { fresh: fresh, i: i }); }).join("");
        if (more.length) html += '<span class="hud-chip hud-chip--neutral at-chip at-chip--more" title="' + esc(more.map(function (e) { return label(e.tag); }).join(", ")) + '">+' + more.length + '<span class="hud-sr"> more: ' + esc(more.map(function (e) { return label(e.tag); }).join(", ")) + "</span></span>";
        return '<span class="at-chips">' + html + "</span>";
    }

    function picksHtml(have) {
        var taken = {}; (have || []).forEach(function (e) { taken[e.tag] = true; });
        var out = VOCAB.map(function (g) {
            var opts = g.tags.filter(function (t) { return !taken[t]; });
            if (!opts.length) return "";
            return '<div class="at-pick-group" role="group" aria-label="' + esc(g.group) + '"><span class="hud-label">' + esc(g.group) + "</span>" +
                opts.map(function (t) { return '<button type="button" class="hud-tagopt" data-at-add="' + esc(t) + '">' + esc(label(t)) + "</button>"; }).join("") + "</div>";
        }).join("");
        return out || '<span class="hud-muted">Every tag in the list is already on.</span>';
    }

    /* ---- Mount ------------------------------------------------------------ */

    var ICON_TAG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line></svg>';
    var ICON_SPIN = '<svg class="sync-spin" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.6"></path></svg>';
    var ICON_OK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

    function mount(opts) {
        var S = {
            deck: [], cards: {}, loaded: false, loadError: null,
            run: { state: "idle" },          // idle | running | ok | unavailable | error
            filter: new URLSearchParams(location.search).get("tag") || null,
            groupBy: new URLSearchParams(location.search).get("group") === "tag" ? "tag" : "type",
            fresh: {}, pickerOpen: false, moreOpen: false, lastFocus: null
        };
        if (S.filter === "untagged") S.filter = UNTAGGED;
        var base = "/api/decks/" + encodeURIComponent(opts.deckId);
        var strip = opts.strip, bar = opts.filterbar;
        var cards = function () { return (opts.getCards && opts.getCards()) || []; };
        var changed = function () { if (opts.onChange) opts.onChange(); };
        var editors = [];   // {el, name}

        async function call(method, path, body) {
            var res = await fetch(base + path, {
                method: method,
                headers: body ? { "Content-Type": "application/json" } : undefined,
                body: body ? JSON.stringify(body) : undefined
            });
            var data = {};
            try { data = await res.json(); } catch (e) { /* empty body */ }
            if (!res.ok) { var err = new Error(data.error || "Request failed (" + res.status + ")"); err.status = res.status; throw err; }
            return data;
        }

        /* -- data -- */
        async function load() {
            try {
                var out = await Promise.all([call("GET", "/tags"), call("GET", "/card-tags")]);
                S.deck = out[0].tags || [];
                S.cards = out[1].tags || {};
                S.loaded = true; S.loadError = null;
            } catch (e) {
                S.loadError = e.message || "Couldn't load tags";
                S.loaded = true;
            }
            renderAll();
        }
        function hasTags() { return summarize(cards(), S.cards, S.deck).any; }

        function snapshotAuto() {
            var k = {};
            Object.keys(S.cards).forEach(function (n) { (S.cards[n] || []).forEach(function (e) { if (e.source !== "user") k[n + "\u0001" + e.tag] = 1; }); });
            return k;
        }

        /* -- Auto Tag run -- */
        async function runAutoTag() {
            if (S.run.state === "running") return;
            var before = snapshotAuto();
            S.run = { state: "running" }; renderStrip();
            try {
                await call("POST", "/auto-tag", {});
                await load();
                var after = snapshotAuto(), fresh = {}, added = 0;
                Object.keys(after).forEach(function (k) { if (!before[k]) { added += 1; fresh[k.split("\u0001")[0]] = 1; } });
                var sum = summarize(cards(), S.cards, S.deck);
                S.fresh = fresh;
                S.run = { state: "ok", added: added, tagged: sum.tagged, cards: sum.cards, user: sum.user };
                setTimeout(function () { S.fresh = {}; }, 1500);
            } catch (e) {
                S.run = e.status === 501
                    ? { state: "unavailable" }
                    : { state: "error", msg: e.message || "Auto Tag failed" };
            }
            renderAll();
            var btn = strip.querySelector("[data-at-run]"); if (btn && S.run.state !== "ok") btn.focus({ preventScroll: true });
        }

        function askRun() {
            if (!hasTags()) { runAutoTag(); return; }
            var sum = summarize(cards(), S.cards, S.deck);
            var m = confirmModal();
            m.querySelector("[data-at-confirm-body]").innerHTML =
                "<p>Auto Tag will replace the <strong>" + plural(sum.auto, "auto tag") + "</strong> on this deck with a fresh pass.</p>" +
                "<p><strong>" + plural(sum.user, "tag") + "</strong> you added or kept stay exactly as they are.</p>" +
                '<p class="hud-muted">An auto tag you removed earlier can be applied again.</p>';
            S.lastFocus = document.activeElement;
            HUD.openModal(m);
            var go = m.querySelector("[data-at-confirm-go]"); if (go) go.focus();
        }
        function confirmModal() {
            var m = document.getElementById("atConfirmModal");
            if (m) return m;
            m = document.createElement("div");
            m.id = "atConfirmModal"; m.className = "hud-modal"; m.setAttribute("aria-hidden", "true");
            m.innerHTML = '<section class="hud-panel hud-modal-dialog at-confirm" role="alertdialog" aria-modal="true" aria-labelledby="atConfirmTitle" aria-describedby="atConfirmBody">' +
                '<header class="hud-panel-head"><h3 class="hud-panel-title" id="atConfirmTitle">Re-run Auto Tag?</h3></header>' +
                '<div class="hud-panel-body"><div id="atConfirmBody" data-at-confirm-body></div>' +
                '<div class="at-confirm-actions"><button type="button" class="hud-btn hud-btn--sm" data-hud-close>Cancel</button>' +
                '<button type="button" class="hud-btn hud-btn--sm hud-btn--primary" data-at-confirm-go>Re-run Auto Tag</button></div></div></section>';
            document.body.appendChild(m);
            m.addEventListener("click", function (ev) {
                if (ev.target.closest("[data-at-confirm-go]")) { HUD.closeModal(m); runAutoTag(); return; }
                if (ev.target === m || ev.target.closest("[data-hud-close]")) restoreFocus();
            });
            m.addEventListener("keydown", function (ev) {
                if (ev.key !== "Tab") return;
                var b = m.querySelectorAll("button"), first = b[0], last = b[b.length - 1];
                if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
                else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
            });
            // Capture phase: HUD's own Escape handler closes the modal first, so check before it runs.
            document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && m.classList.contains("is-open")) restoreFocus(); }, true);
            return m;
        }
        function restoreFocus() { var f = S.lastFocus; S.lastFocus = null; if (f && f.focus) setTimeout(function () { f.focus({ preventScroll: true }); }, 0); }

        /* -- tag edits (card + deck) -- */
        function fail(where, e) { setErr(where, e.message || "Couldn't save that change"); }
        function setErr(where, msg) {
            S.err = msg ? { where: where, msg: msg } : null;
            renderStrip(); refreshEditors();
        }
        async function addTag(scope, name, tag) {
            try {
                var out = scope === "deck"
                    ? await call("POST", "/tags", { tag: tag, source: "user" })
                    : await call("POST", "/card-tags", { card_name: name, tag: tag, source: "user" });
                var list = scope === "deck" ? S.deck : (S.cards[name] = S.cards[name] || []);
                list.push(out.tag);
                S.err = null;
            } catch (e) { return fail(scope === "deck" ? "deck" : name, e); }
            afterEdit(scope);
        }
        async function removeTag(scope, name, tag) {
            try {
                if (scope === "deck") { await call("DELETE", "/tags", { tag: tag }); S.deck = S.deck.filter(function (e) { return e.tag !== tag; }); }
                else {
                    await call("DELETE", "/card-tags", { card_name: name, tag: tag });
                    S.cards[name] = (S.cards[name] || []).filter(function (e) { return e.tag !== tag; });
                    if (!S.cards[name].length) delete S.cards[name];
                }
                S.err = null;
            } catch (e) { return fail(scope === "deck" ? "deck" : name, e); }
            afterEdit(scope);
        }
        /* Keep: auto -> user. The API replaces by tag name, so delete then add as user. */
        async function pinTag(scope, name, tag) {
            try {
                if (scope === "deck") {
                    await call("DELETE", "/tags", { tag: tag });
                    S.deck = S.deck.filter(function (e) { return e.tag !== tag; });
                    S.deck.push((await call("POST", "/tags", { tag: tag, source: "user" })).tag);
                } else {
                    await call("DELETE", "/card-tags", { card_name: name, tag: tag });
                    S.cards[name] = (S.cards[name] || []).filter(function (e) { return e.tag !== tag; });
                    (S.cards[name] = S.cards[name] || []).push((await call("POST", "/card-tags", { card_name: name, tag: tag, source: "user" })).tag);
                }
                S.err = null;
            } catch (e) { await load(); return fail(scope === "deck" ? "deck" : name, e); }
            afterEdit(scope);
        }
        function afterEdit(scope) {
            S.run = S.run.state === "ok" ? { state: "idle" } : S.run;
            renderAll();
            if (scope !== "deck") refocusEditor();
        }

        /* -- strip (deck tags, summary, button, status) -- */
        function statusHtml() {
            var sum = summarize(cards(), S.cards, S.deck), r = S.run;
            if (r.state === "running") return '<span class="hud-chip hud-chip--data"><span class="hud-chip-dot" aria-hidden="true"></span>Running</span><span>Tagging ' + plural(sum.cards, "card") + "\u2026</span>";
            if (r.state === "ok") return '<span class="hud-chip hud-chip--ok">' + ICON_OK + "Done</span><span>" +
                (r.added ? "Added " + plural(r.added, "auto tag") + " \u00B7 " : "No new tags \u00B7 ") + r.tagged + " of " + plural(r.cards, "card") + " tagged" +
                (r.user ? " \u00B7 your " + plural(r.user, "tag") + " kept" : "") + "</span>";
            if (r.state === "unavailable") return '<span class="hud-chip hud-chip--signal">Not available yet</span><span>The tagging engine hasn\u2019t shipped. Your tags are untouched.</span>';
            if (r.state === "error") return '<span class="hud-chip hud-chip--danger">Failed</span><span role="alert">' + esc(r.msg) + '</span><button type="button" class="hud-btn hud-btn--sm" data-at-run>Retry</button>';
            if (S.loadError) return '<span class="hud-chip hud-chip--danger">Failed</span><span role="alert">Couldn\u2019t load tags: ' + esc(S.loadError) + '</span><button type="button" class="hud-btn hud-btn--sm" data-at-reload>Retry</button>';
            if (!sum.any) return "<span>No tags yet. Auto Tag labels each card by role (Ramp, Removal\u2026) and the deck by theme.</span>";
            return "<span>" + sum.tagged + " of " + plural(sum.cards, "card") + " tagged \u00B7 " + sum.auto + " auto \u00B7 " + sum.user + " yours</span>";
        }
        function legendHtml(cls) { return '<span class="at-legend ' + cls + '" aria-hidden="true"><span class="at-key at-key--auto">' + GLYPH.auto + ' auto</span><span class="at-key at-key--user">' + GLYPH.user + ' yours</span></span>'; }
        function renderStrip() {
            if (!strip) return;
            var sum = summarize(cards(), S.cards, S.deck), running = S.run.state === "running";
            var deckTags = effective(S.deck);
            var counts = roleCounts(cards(), S.cards);
            var focusKey = strip.contains(document.activeElement) ? (document.activeElement.getAttribute("data-at-run") != null ? "run" : document.activeElement.getAttribute("data-at-toggle") != null ? "toggle" : null) : null;
            var shown = counts.slice(0, 6), rest = counts.length - shown.length;
            strip.innerHTML =
                '<section class="hud-panel at-strip" aria-labelledby="atH">' +
                '<header class="hud-panel-head"><h3 class="hud-panel-title" id="atH">Tags</h3>' +
                '<div class="hud-panel-meta">' + legendHtml("at-legend--head") +
                '<button type="button" class="hud-btn hud-btn--sm' + (sum.any ? "" : " hud-btn--primary") + '" data-at-run' + (running ? ' disabled aria-busy="true"' : "") + ">" +
                (running ? ICON_SPIN + " Tagging\u2026" : ICON_TAG + " Auto Tag") + "</button></div></header>" +
                '<div class="hud-panel-body at-body">' +
                '<div class="at-row"><span class="hud-label" id="atDeckL">Deck</span><div class="at-deck" role="group" aria-labelledby="atDeckL">' +
                (deckTags.length ? deckTags.map(function (e, i) { return chipHtml(e, { edit: true, i: i }); }).join("") : '<span class="hud-muted at-none">No deck tags</span>') +
                '<button type="button" class="hud-tagopt at-add" data-at-toggle aria-expanded="' + S.pickerOpen + '" aria-controls="atDeckPicks">Add tag</button></div></div>' +
                '<div class="at-row at-picks-row" id="atDeckPicks"' + (S.pickerOpen ? "" : " hidden") + '><span class="hud-label">Add</span><div class="at-picks">' + picksHtml(deckTags) + "</div></div>" +
                (S.err && S.err.where === "deck" ? '<p class="at-err" role="alert">' + esc(S.err.msg) + "</p>" : "") +
                (counts.length ? '<div class="at-row"><span class="hud-label" id="atCntL">Cards</span><div class="at-counts" role="group" aria-labelledby="atCntL">' +
                    shown.map(function (c) { return '<button type="button" class="at-count" data-at-jump="' + esc(c.tag) + '" title="Show the ' + esc(label(c.tag)) + ' cards">' + esc(label(c.tag)) + " <b>" + c.n + "</b></button>"; }).join('<span class="at-dot" aria-hidden="true">\u00B7</span>') +
                    (rest > 0 ? '<span class="at-dot" aria-hidden="true">\u00B7</span><span class="hud-muted">+' + rest + " more</span>" : "") + "</div></div>" : "") +
                '<div class="at-status' + (S.run.state === "ok" ? " is-ok" : "") + '" role="status" aria-live="polite">' + statusHtml() + "</div>" + (sum.any ? legendHtml("at-legend--body") : "") + "</div></section>";
            if (focusKey) { var f = strip.querySelector(focusKey === "run" ? "[data-at-run]" : "[data-at-toggle]"); if (f && !f.disabled) f.focus({ preventScroll: true }); }
        }

        /* -- decklist filter bar -- */
        function renderFilter() {
            if (!bar) return;
            var counts = roleCounts(cards(), S.cards), none = untaggedCount(cards(), S.cards);
            if (!counts.length) { bar.innerHTML = ""; bar.hidden = true; return; }
            bar.hidden = false;
            if (S.filter && S.filter !== UNTAGGED && !counts.some(function (c) { return c.tag === S.filter; })) S.filter = null;
            var inline = counts.slice(0, INLINE_FILTERS), rest = counts.slice(INLINE_FILTERS);
            if (S.filter && rest.some(function (c) { return c.tag === S.filter; })) { inline = inline.concat(rest.filter(function (c) { return c.tag === S.filter; })); rest = rest.filter(function (c) { return c.tag !== S.filter; }); }
            var fb = function (tag, label, n, cls) {
                var on = S.filter === tag;
                return '<button type="button" class="at-fchip' + (cls || "") + '" data-at-filter="' + esc(tag) + '" aria-pressed="' + on + '">' + esc(label) + ' <span class="at-n">' + n + "</span></button>";
            };
            var shownN = filterCards(cards(), S.cards, S.filter).length, total = cards().length;
            var inList = opts.view && opts.view() === "list";
            bar.innerHTML =
                '<div class="hud-filterbar at-filterbar" role="group" aria-label="Filter and group the decklist by tag">' +
                (inList ? "" : '<div class="hud-seg" role="radiogroup" aria-label="Group decklist by"><input type="radio" name="atGroup" id="atGroupType" value="type"' + (S.groupBy === "type" ? " checked" : "") + '><label for="atGroupType">By type</label>' +
                    '<input type="radio" name="atGroup" id="atGroupTag" value="tag"' + (S.groupBy === "tag" ? " checked" : "") + '><label for="atGroupTag">By tag</label></div>') +
                '<div class="at-fchips">' + inline.map(function (c) { return fb(c.tag, label(c.tag), c.n); }).join("") +
                (rest.length ? '<span class="at-rest"' + (S.moreOpen ? "" : " hidden") + ">" + rest.map(function (c) { return fb(c.tag, label(c.tag), c.n); }).join("") + "</span>" +
                    '<button type="button" class="at-fchip at-fchip--more" data-at-more aria-expanded="' + S.moreOpen + '">' + (S.moreOpen ? "Fewer" : "+" + rest.length + " more") + "</button>" : "") +
                (none ? fb(UNTAGGED, "Untagged", none, " at-fchip--none") : "") + "</div>" +
                (S.filter ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-at-clear>Clear filter</button>' : "") +
                '<span class="hud-sr" role="status" aria-live="polite">' + (S.filter ? shownN + " of " + plural(total, "card") + " shown" : "") + "</span></div>";
        }

        function syncUrl() {
            var u = new URL(location.href);
            if (S.filter) u.searchParams.set("tag", S.filter === UNTAGGED ? "untagged" : S.filter); else u.searchParams.delete("tag");
            if (S.groupBy === "tag") u.searchParams.set("group", "tag"); else u.searchParams.delete("group");
            history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
        }
        function setFilter(f) { S.filter = S.filter === f ? null : f; syncUrl(); renderFilter(); changed(); }

        /* -- per-card editor (card detail modal) -- */
        function editorHtml(name) {
            var have = tagsOf(S.cards, name), err = S.err && S.err.where === name ? S.err.msg : "";
            return '<div class="at-editor-head"><span class="hud-label">Role tags \u00B7 this deck</span>' +
                '<span class="at-legend" aria-hidden="true"><span class="at-key at-key--auto">' + GLYPH.auto + ' auto</span><span class="at-key at-key--user">' + GLYPH.user + ' yours</span></span></div>' +
                '<div class="at-deck" data-at-chips>' + (have.length ? have.map(function (e, i) { return chipHtml(e, { edit: true, i: i }); }).join("") : '<span class="hud-muted at-none">No tags on this card</span>') + "</div>" +
                '<div class="at-picks" data-at-picks><span class="hud-label at-add-label">Add</span>' + picksHtml(have) + "</div>" +
                '<p class="at-hint">Tags you add are yours and survive re-running Auto Tag. Click \u25C7 on an auto tag to keep it as yours.</p>' +
                (err ? '<p class="at-err" role="alert">' + esc(err) + "</p>" : "");
        }
        function bindEditor(el, name) {
            editors = editors.filter(function (x) { return document.body.contains(x.el) && x.el !== el; });
            editors.push({ el: el, name: name });
            el.classList.add("at-editor");
            el.setAttribute("data-at-editor", name);
            el.innerHTML = editorHtml(name);
            if (!el._atBound) {
                el._atBound = true;
                el.addEventListener("click", function (ev) {
                    var nm = el.getAttribute("data-at-editor");
                    var a = ev.target.closest("[data-at-add]"), rm = ev.target.closest("[data-at-remove]"), pin = ev.target.closest("[data-at-pin]");
                    if (a) { S.lastEditorKey = "add"; addTag("card", nm, a.getAttribute("data-at-add")); }
                    else if (rm) { S.lastEditorKey = "remove"; removeTag("card", nm, rm.getAttribute("data-at-remove")); }
                    else if (pin) { S.lastEditorKey = "pin"; pinTag("card", nm, pin.getAttribute("data-at-pin")); }
                });
            }
        }
        function refreshEditors() {
            editors = editors.filter(function (x) { return document.body.contains(x.el); });
            editors.forEach(function (x) { x.el.innerHTML = editorHtml(x.name); });
        }
        function focusEditor() {
            var e = editors.filter(function (x) { return document.body.contains(x.el); })[0];
            var t = e && (e.el.querySelector("[data-at-pin],[data-at-remove],[data-at-add]"));
            if (t) t.focus({ preventScroll: false });
        }
        /* After an edit the buttons are re-rendered; keep focus inside the editor. */
        function refocusEditor() {
            var e = editors.filter(function (x) { return document.body.contains(x.el); })[0];
            if (!e) return;
            var t = e.el.querySelector(S.lastEditorKey === "add" ? "[data-at-add]" : "[data-at-remove],[data-at-add]") || e.el.querySelector("button");
            if (t && e.el.contains(document.activeElement) === false) t.focus({ preventScroll: true });
        }

        function renderAll() { renderStrip(); renderFilter(); refreshEditors(); changed(); }

        /* -- events -- */
        if (strip) {
            strip.addEventListener("click", function (ev) {
                var t = ev.target;
                if (t.closest("[data-at-run]")) { askRun(); return; }
                if (t.closest("[data-at-reload]")) { load(); return; }
                if (t.closest("[data-at-toggle]")) { S.pickerOpen = !S.pickerOpen; renderStrip(); var p = strip.querySelector("#atDeckPicks .hud-tagopt"); if (S.pickerOpen && p) p.focus(); return; }
                var a = t.closest("[data-at-add]"), rm = t.closest("[data-at-remove]"), pin = t.closest("[data-at-pin]"), jump = t.closest("[data-at-jump]");
                if (a) { addTag("deck", null, a.getAttribute("data-at-add")); return; }
                if (rm) { removeTag("deck", null, rm.getAttribute("data-at-remove")); return; }
                if (pin) { pinTag("deck", null, pin.getAttribute("data-at-pin")); return; }
                if (jump) {
                    S.filter = jump.getAttribute("data-at-jump"); syncUrl(); renderFilter(); changed();
                    var tgt = document.getElementById("decklistContainer");
                    if (tgt) { var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches; (bar && !bar.hidden ? bar : tgt).scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" }); }
                }
            });
            strip.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && S.pickerOpen) { S.pickerOpen = false; renderStrip(); var b = strip.querySelector("[data-at-toggle]"); if (b) b.focus(); } });
        }
        if (bar) {
            bar.addEventListener("click", function (ev) {
                var f = ev.target.closest("[data-at-filter]");
                if (f) { setFilter(f.getAttribute("data-at-filter")); var k = bar.querySelector('[data-at-filter="' + CSS.escape(f.getAttribute("data-at-filter")) + '"]'); if (k) k.focus({ preventScroll: true }); return; }
                if (ev.target.closest("[data-at-clear]")) { S.filter = null; syncUrl(); renderFilter(); changed(); return; }
                if (ev.target.closest("[data-at-more]")) { S.moreOpen = !S.moreOpen; renderFilter(); var m = bar.querySelector("[data-at-more]"); if (m) m.focus({ preventScroll: true }); }
            });
            bar.addEventListener("change", function (ev) {
                if (ev.target.name !== "atGroup") return;
                S.groupBy = ev.target.value === "tag" ? "tag" : "type"; syncUrl(); changed();
                var r = bar.querySelector('input[name="atGroup"]:checked'); if (r) r.focus({ preventScroll: true });
            });
        }

        load();
        return {
            load: load, update: function () { renderAll(); },
            state: S,
            chips: function (name, max) { return chipsHtml(tagsOf(S.cards, name), max || MAX_CHIPS, !!S.fresh[name]); },
            tagsOf: function (name) { return tagsOf(S.cards, name); },
            filter: function (list) { return filterCards(list, S.cards, S.filter); },
            filtered: function () { return !!S.filter; },
            groupBy: function () { return S.groupBy; },
            groups: function (list) { return tagGroups(list, S.cards); },
            bindEditor: bindEditor, focusEditor: focusEditor,
            run: askRun
        };
    }

    var api = { VOCAB: VOCAB, label: label, FUNCTION_IDS: FUNCTION_IDS, THEME_IDS: THEME_IDS, UNTAGGED: UNTAGGED, MAX_CHIPS: MAX_CHIPS, mount: mount, effective: effective, roleCounts: roleCounts, untaggedCount: untaggedCount,
        filterCards: filterCards, tagGroups: tagGroups, summarize: summarize, chipsHtml: chipsHtml, chipHtml: chipHtml, picksHtml: picksHtml, esc: esc };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.AutoTag = api;
})(typeof window !== "undefined" ? window : this);
