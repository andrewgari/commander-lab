/* Commander Lab: bulk decommission mock (BD-D). Docs only, NOT app code.
 *
 * REAL: deck names, commanders, colours and Scryfall ids (the owner's physical roster).
 * ILLUSTRATIVE: card counts, the three failure reasons, and the response shape.
 *   The shape is what the UI needs from the bulk endpoint (UX_SPEC.md section 8).
 *
 * The same code paths a coder needs: selection model, range select, filter rules,
 * confirm dialog (+ type-to-confirm), busy lock, result view, toasts, focus return.
 */
(function () {
    "use strict";
    var esc = function (s) { return HUD.esc(s); };
    var $ = function (id) { return document.getElementById(id); };
    var TYPE_TO_CONFIRM_AT = 5;   // N >= 5 asks for the count to be typed
    var TOAST_MS = 6000;

    var DECKS = [
        { id: "archidekt:101", name: "Holy Hellfire", cmdr: "Kaalia of the Vast", uid: "ef099f25-2b0b-4028-8490-fbc859e35f6c", color: "WBR", cards: 100, status: "physical" },
        { id: "archidekt:102", name: "Magic in Stereo", cmdr: "Riku of Two Reflections", uid: "716d0b3b-bac9-4fb8-882e-bd6171864043", color: "URG", cards: 100, status: "physical" },
        { id: "archidekt:103", name: "Adventure After Elevensies", cmdr: "Sam, Loyal Attendant & Frodo, Adventurous Hobbit", uid: "687a2b76-3f05-4732-bd9b-513d06d16300", color: "WBG", cards: 100, status: "physical" },
        { id: "archidekt:104", name: "The Word is 'Gamer'", cmdr: "The Most Dangerous Gamer", uid: "a08b8ff8-13e4-46e7-b028-d055e4548f6a", color: "BG", cards: 99, status: "physical" },
        { id: "archidekt:105", name: "No The Other Frozen Throne", cmdr: "Darien, King of Kjeldor", uid: "ed5f96a4-55e0-4ae3-bd78-f01703649e9d", color: "W", cards: 99, status: "physical" },
        { id: "archidekt:106", name: "Everything is permitted.", cmdr: "Ezio Auditore da Firenze", uid: "07ce00ad-acb5-4418-9ea1-50eaf2273242", color: "WUBRG", cards: 100, status: "physical" },
        { id: "archidekt:107", name: "Yip! Yip!", cmdr: "Avatar Aang // Aang, Master of Elements", uid: "257928ba-27ae-4a11-ae41-76dfcd626ed4", color: "WUBRG", cards: 100, status: "physical" },
        { id: "archidekt:108", name: "I cast, therefore I am", cmdr: "Vivi Ornitier", uid: "ada6aeb6-8c9f-4498-9326-b4da0fcb19a3", color: "UR", cards: 100, status: "physical" },
        { id: "archidekt:109", name: "For Cheese and Country", cmdr: "Mabel, Heir to Cragflame", uid: "1696453f-a678-47ff-b583-53c2130b7d49", color: "WR", cards: 100, status: "physical" },
        { id: "archidekt:110", name: "The world is slowly ebbing away...", cmdr: "Celes, Rune Knight", uid: "d30dd823-9a04-4e3d-89c6-0fc5afc9efa4", color: "WBR", cards: 100, status: "physical" },
        { id: "archidekt:111", name: "The most handome guy here.", cmdr: "Squall, SeeD Mercenary", uid: "076af4bd-1b74-4cf3-b6a0-8ec17d5b0e26", color: "WB", cards: 100, status: "physical" },
        { id: "archidekt:112", name: "Alright Everyone, Let's Mosey", cmdr: "Cloud, Ex-SOLDIER", uid: "15ce92a3-0d51-4c8e-bbce-d225021b4c35", color: "WRG", cards: 100, status: "physical" },
        { id: "archidekt:201", name: "Cause it rhymes with \"tough!\"", cmdr: "Toph, the First Metalbender", uid: "aaa3e063-472e-445a-8440-dc1910899d81", color: "WRG", cards: 100, status: "testing" }
    ];
    var STATUS_LABELS = { physical: "Physical", digital: "Virtual", testing: "WIP", retired: "Retired" };
    var BASE = JSON.parse(JSON.stringify(DECKS));

    var S = {
        decks: JSON.parse(JSON.stringify(BASE)),
        mode: false, sel: new Set(), failed: new Map(), anchor: null,
        busy: false, q: "", showAll: false, outcome: "success",
        dlg: null   // { ids, view: 'confirm'|'busy'|'result', result }
    };

    /* ---------------- derived ---------------- */
    var selectable = function (d) { return d.status === "physical"; };
    function visible() {
        var q = S.q.trim().toLowerCase();
        return S.decks.filter(function (d) {
            if (q && (d.name + " " + d.cmdr).toLowerCase().indexOf(q) === -1) return false;
            if (!S.showAll && d.status !== "physical") return false;
            return true;
        });
    }
    var visibleSelectable = function () { return visible().filter(selectable); };
    var byId = function (id) { return S.decks.filter(function (d) { return d.id === id; })[0]; };
    function say(msg) { var el = $("liveMsg"); el.textContent = ""; setTimeout(function () { el.textContent = msg; }, 30); }
    var plural = function (n, w) { return n + " " + w + (n === 1 ? "" : "s"); };

    /* ---------------- tile + grid ---------------- */
    var CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="square" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    function art(d) {
        var s = HUD.scryfall(d.uid, "art_crop");
        return '<div class="deck-art" aria-hidden="true"><img src="' + esc(s.src) + '" data-fallback="' + esc(s.fallback) + '" alt="" loading="lazy" decoding="async"></div>';
    }
    function tile(d, i) {
        var picked = S.sel.has(d.id), failed = S.failed.has(d.id);
        var can = selectable(d), selecting = S.mode;
        var cls = "deck-card" + (selecting && picked ? " is-selected" : "") + (selecting && failed ? " is-failed" : "") + (selecting && !can ? " is-unselectable" : "");
        var chip = failed && selecting
            ? '<span class="hud-chip hud-chip--danger"><span aria-hidden="true">✕</span> Failed</span>'
            : HUD.statusChip(d.status, STATUS_LABELS[d.status]);
        var layer;
        if (selecting && can) {
            layer = '<input type="checkbox" class="deck-pick" data-id="' + esc(d.id) + '"' + (picked ? " checked" : "") +
                ' aria-label="Select ' + esc(d.name) + '"' + (S.busy ? ' aria-disabled="true"' : "") + ' tabindex="0">' +
                '<span class="deck-check" aria-hidden="true">' + CHECK + '</span>';
        } else if (selecting) {
            layer = '<button type="button" class="deck-open" aria-disabled="true" aria-label="' + esc(d.name) + ': only Physical decks can be decommissioned" title="Only Physical decks can be decommissioned"></button>';
        } else {
            layer = '<button type="button" class="deck-open" data-open="' + esc(d.id) + '" aria-label="Quick view: ' + esc(d.name) + '"></button>';
        }
        var tab = selecting ? ' tabindex="-1"' : "";
        return '<article class="' + cls + '" data-id="' + esc(d.id) + '" title="' + esc(d.name + " · " + d.cmdr) + '">' + art(d) +
            '<div class="deck-top"><div class="deck-status-row">' + chip + '</div>' + HUD.pips(d.color) + '</div>' +
            '<div class="deck-plate"><div class="deck-header-info"><h3 class="deck-name"><a href="#"' + tab + '>' + esc(d.name) + '</a></h3>' +
            '<p class="deck-commanders">' + esc(d.cmdr) + '</p></div></div>' + layer + '</article>';
    }
    function render() {
        var focusId = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.id;
        var list = visible(), groups = {};
        list.forEach(function (d) { (groups[d.status] = groups[d.status] || []).push(d); });
        var order = ["physical", "digital", "testing", "retired"], html = "";
        order.forEach(function (st) {
            if (!groups[st]) return;
            var head = st === "physical" ? "" : '<div class="folder-header"><span class="folder-title">' + STATUS_LABELS[st] + ' Decks</span><span class="folder-count">' + groups[st].length + "</span></div>";
            html += '<section class="folder-section" aria-label="' + STATUS_LABELS[st] + ' decks">' + head +
                '<div class="deck-grid' + (S.mode ? " is-selecting" : "") + '"' + (S.busy ? ' aria-busy="true"' : "") + ">" +
                groups[st].map(tile).join("") + "</div></section>";
        });
        if (!html) {
            var phys = S.decks.filter(selectable).length;
            html = '<div class="hud-empty" role="status">' + (S.q ? "No decks match “" + esc(S.q) + "”." : (phys ? "No decks to show." : "No physical decks left.")) + "</div>";
        }
        $("decksContainer").innerHTML = html;
        document.body.classList.toggle("is-bulk-on", S.mode);
        if (focusId) { var again = document.querySelector('[data-id="' + CSS.escape(focusId) + '"].deck-pick'); if (again) again.focus(); }
        renderBar();
        var btn = $("selectBtn");
        btn.setAttribute("aria-pressed", String(S.mode));
        btn.textContent = S.mode ? "Selecting…" : "Select";
        var none = !S.decks.some(selectable);
        btn.setAttribute("aria-disabled", String(none));
        btn.title = none ? "No physical decks to decommission" : "Select decks to decommission (S)";
    }

    /* ---------------- action bar ---------------- */
    function renderBar() {
        var bar = $("bulkBar");
        bar.hidden = !S.mode;
        if (!S.mode) return;
        var vis = visibleSelectable(), visIds = vis.map(function (d) { return d.id; });
        var n = S.sel.size;
        var shownSel = visIds.filter(function (id) { return S.sel.has(id); }).length;
        var hiddenSel = n - shownSel, failedN = S.failed.size;
        var allOn = vis.length > 0 && shownSel === vis.length;
        var busy = S.busy;
        bar.setAttribute("aria-busy", String(busy));
        bar.innerHTML =
            '<label class="hud-bulk-all"><input type="checkbox" id="bulkAll"' + (allOn ? " checked" : "") + (busy ? ' aria-disabled="true"' : "") +
            ' aria-label="Select all ' + vis.length + ' shown decks"><span>All ' + vis.length + '</span></label>' +
            '<span class="hud-bulk-count" id="bulkCount" aria-live="polite" aria-atomic="true"><b>' + n + "</b> selected</span>" +
            (hiddenSel ? '<span class="hud-bulk-note">+ ' + hiddenSel + " hidden by filter</span>" : "") +
            (failedN ? '<span class="hud-bulk-note is-fail">✕ ' + failedN + ' failed · <button type="button" id="bulkWhy">Details</button></span>' : "") +
            (n === 0 ? '<span class="hud-bulk-hint">Click decks to select. <span class="hud-kbd">Shift</span>+click for a range.</span>' : "") +
            '<button type="button" class="hud-btn hud-btn--ghost hud-btn--sm" id="bulkClear"' + (n === 0 || busy ? ' aria-disabled="true"' : "") + ">Clear</button>" +
            '<span class="hud-bulk-spacer"></span>' +
            '<span class="hud-bulk-keys"><span class="hud-kbd">Esc</span> clear</span>' +
            '<button type="button" class="hud-btn hud-btn--ghost" id="bulkDone"' + (busy ? ' aria-disabled="true"' : "") + ">Done</button>" +
            '<button type="button" class="hud-btn hud-btn--danger" id="bulkGo" aria-haspopup="dialog"' + (n === 0 || busy ? ' aria-disabled="true"' : "") + (n === 0 ? ' aria-describedby="bulkCount"' : "") + ">Decommission (" + n + ")</button>";
        var all = $("bulkAll"); if (all) all.indeterminate = shownSel > 0 && !allOn;
    }

    /* ---------------- selection ---------------- */
    function setSel(id, on) { on ? S.sel.add(id) : S.sel.delete(id); S.failed.delete(id); }
    function toggleRange(toId, on) {
        var ids = visibleSelectable().map(function (d) { return d.id; });
        var a = ids.indexOf(S.anchor), b = ids.indexOf(toId);
        if (a < 0 || b < 0) return setSel(toId, on);
        ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (id) { setSel(id, on); });
    }
    function selectAllVisible(on) { visibleSelectable().forEach(function (d) { setSel(d.id, on); }); }
    function enter(announce) { if (S.mode) return; S.mode = true; render(); if (announce !== false) say("Select mode on. Space toggles a deck. Escape clears."); }
    function exit() { S.mode = false; S.sel.clear(); S.failed.clear(); S.anchor = null; render(); $("selectBtn").focus(); say("Select mode off."); }
    function clearSel() { S.sel.clear(); S.failed.clear(); render(); say("Selection cleared."); }

    /* ---------------- toasts ---------------- */
    function toast(o) {
        var el = document.createElement("div");
        el.className = "hud-toast hud-toast--" + o.tone;
        el.setAttribute("role", o.tone === "danger" ? "alert" : "status");
        el.innerHTML = '<p class="hud-toast-title" data-glyph="' + esc(o.glyph) + '">' + esc(o.title) + "</p>" +
            '<button type="button" class="hud-toast-x" aria-label="Dismiss">×</button>' +
            (o.body ? '<p class="hud-toast-body">' + esc(o.body) + "</p>" : "") +
            (o.action ? '<div class="hud-toast-act"><button type="button" class="hud-btn hud-btn--sm">' + esc(o.action.label) + "</button></div>" : "");
        $("toasts").appendChild(el);
        var timer, left = TOAST_MS;
        function kill() { clearTimeout(timer); el.classList.add("is-leaving"); setTimeout(function () { el.remove(); }, 120); }
        function arm() { if (o.sticky) return; clearTimeout(timer); timer = setTimeout(kill, left); }
        el.addEventListener("mouseenter", function () { clearTimeout(timer); });
        el.addEventListener("focusin", function () { clearTimeout(timer); });
        el.addEventListener("mouseleave", arm); el.addEventListener("focusout", arm);
        el.querySelector(".hud-toast-x").onclick = kill;
        if (o.action) el.querySelector(".hud-toast-act button").onclick = function () { o.action.run(); kill(); };
        arm();
    }

    /* ---------------- dialog ---------------- */
    var returnFocus = null;
    function orderedIds(ids) { return S.decks.filter(function (d) { return ids.indexOf(d.id) > -1; }).map(function (d) { return d.id; }); }
    function openDlg(ids, trigger) {
        returnFocus = trigger || document.activeElement;
        S.dlg = { ids: orderedIds(ids), view: "confirm", result: null };
        drawDlg();
        HUD.openModal("bulkDlg");
        var cancel = $("dlgCancel"); if (cancel) cancel.focus();
    }
    function closeDlg() {
        HUD.closeModal("bulkDlg"); S.dlg = null;
        var t = returnFocus && document.body.contains(returnFocus) && returnFocus.getAttribute("aria-disabled") !== "true" ? returnFocus : null;
        (t || $("bulkGo") || $("selectBtn")).focus();
    }
    var visibleIds = function () { return visible().map(function (d) { return d.id; }); };
    function rowHtml(d, state, why) {
        var glyph = { pending: "·", running: "…", done: "✓", failed: "✕" }[state], word = { pending: "Pending", running: "Working", done: "Done", failed: "Failed" }[state];
        var hidden = visibleIds().indexOf(d.id) < 0 ? '<span class="hud-chip hud-chip--ghost">Hidden by filter</span>' : "";
        return '<li class="hud-bulk-row is-' + state + '"><span class="hud-bulk-state" aria-hidden="true">' + glyph + '</span>' +
            '<span style="min-width:0"><span class="hud-bulk-row-name">' + esc(d.name) + '</span><span class="hud-bulk-row-sub">' + esc(d.cmdr) + '</span><span class="hud-sr">' + word + '</span></span>' +
            '<span class="hud-bulk-row-meta">' + hidden + (state === "done" && why ? esc(why) : d.cards + " cards") + "</span>" +
            (why && state === "failed" ? '<div class="hud-bulk-why">' + esc(why.human) + "<code>" + esc(why.raw) + "</code></div>" : "") + "</li>";
    }
    function drawDlg() {
        var D = S.dlg, p = $("bulkPanel"), n = D.ids.length, decks = D.ids.map(byId);
        var cards = decks.reduce(function (a, d) { return a + d.cards; }, 0);
        p.className = "hud-panel hud-modal-dialog hud-bulk-dialog";
        p.setAttribute("aria-busy", String(D.view === "busy"));
        if (D.view === "confirm") {
            var needType = n >= TYPE_TO_CONFIRM_AT;
            p.innerHTML =
                '<div class="hud-panel-head"><h2 class="hud-panel-title" id="bulkTitle">Decommission ' + plural(n, "deck") + "?</h2></div>" +
                '<div class="hud-panel-body hud-bulk-body">' +
                '<div id="bulkDesc" class="hud-bulk-body">' +
                '<p class="hud-bulk-lead">' + (n === 1 ? "This deck is taken apart for good." : "These decks are taken apart for good.") + " Decommissioning can’t be undone from here.</p>" +
                '<ul class="hud-bulk-does">' +
                "<li><span><b>Moves " + (n === 1 ? "it" : "each deck") + " to Retired.</b> The deck and its list stay in Commander Lab.</span></li>" +
                "<li><span><b>Returns " + cards + " cards to your collection.</b> Their copies go from In Deck to In Collection.</span></li>" +
                '<li class="is-keep"><span><b>Nothing is deleted.</b> Not the cards, not the deck record.</span></li></ul>' +
                '<div class="hud-bulk-caution" role="note"><span>Physical status comes from the Archidekt folder. ' + (n === 1 ? "Move this deck" : "Move these decks") + " out of it, or the next sync can mark " + (n === 1 ? "it" : "them") + " Physical again.</span></div></div>" +
                '<div><div class="hud-bulk-list-head"><span class="hud-label" id="bulkListLabel">' + plural(n, "deck") + '</span><span class="hud-bulk-note">' + cards + " cards</span></div>" +
                '<ul class="hud-bulk-list" tabindex="0" aria-labelledby="bulkListLabel" style="margin-top:var(--sp-2)">' + decks.map(function (d) { return rowHtml(d, "pending"); }).join("") + "</ul></div>" +
                (needType ? '<div class="hud-bulk-type"><label for="bulkType" class="hud-field-label">Type <b class="hud-kbd">' + n + '</b> to confirm</label><input id="bulkType" inputmode="numeric" autocomplete="off" aria-describedby="bulkTypeHint"><span class="hud-field-hint" id="bulkTypeHint">The number of decks, so Enter alone can’t do this.</span></div>' : "") +
                "</div>" +
                '<div class="hud-bulk-foot"><button type="button" class="hud-btn" id="dlgCancel">Cancel</button>' +
                '<button type="button" class="hud-btn hud-btn--danger-solid" id="dlgGo"' + (needType ? ' aria-disabled="true"' : "") + ">Decommission " + plural(n, "deck") + "</button></div>";
        } else if (D.view === "busy") {
            p.innerHTML =
                '<div class="hud-panel-head"><h2 class="hud-panel-title" id="bulkTitle">Decommissioning ' + plural(n, "deck") + "…</h2></div>" +
                '<div class="hud-panel-body hud-bulk-body"><div id="bulkDesc" class="hud-bulk-progress"><div class="hud-job-track"><i></i></div><p class="hud-job-msg">Working on ' + plural(n, "deck") + ". Keep this tab open.</p></div>" +
                '<ul class="hud-bulk-list" aria-label="Decks being decommissioned">' + decks.map(function (d) { return rowHtml(d, "running"); }).join("") + "</ul></div>" +
                '<div class="hud-bulk-foot"><span class="hud-bulk-foot-note">Esc and outside clicks are off until this finishes.</span><button type="button" class="hud-btn" id="dlgCancel" aria-disabled="true">Cancel</button>' +
                '<button type="button" class="hud-btn hud-btn--danger-solid" id="dlgGo" aria-disabled="true">Decommissioning…</button></div>';
        } else {
            var R = D.result, ok = R.ok, bad = R.failed;
            var retry = bad.filter(function (f) { return f.retryable; }).length;
            var tone = ok.length ? "partial" : "fail";
            p.className += " is-result-" + tone;
            var title = ok.length ? ok.length + " of " + n + " decommissioned" : "Nothing was decommissioned";
            p.innerHTML =
                '<div class="hud-panel-head"><h2 class="hud-panel-title" id="bulkTitle">' + esc(title) + "</h2></div>" +
                '<div class="hud-panel-body hud-bulk-body"><div id="bulkDesc" class="hud-bulk-body" role="alert">' +
                '<p class="hud-bulk-lead">' + (R.network ? "Couldn’t reach the server, so some of these may have gone through. Retrying is safe: a deck that’s already retired counts as done." : plural(bad.length, "deck") + " didn’t go through. " + (ok.length ? plural(ok.length, "deck") + " did, and left the list." : "Nothing changed.")) + "</p></div>" +
                '<div><div class="hud-bulk-list-head"><span class="hud-label" id="bulkListLabel">Failed · ' + bad.length + "</span></div>" +
                '<ul class="hud-bulk-list" tabindex="0" aria-labelledby="bulkListLabel" style="margin-top:var(--sp-2)">' +
                bad.map(function (f) { return rowHtml(byId(f.id), "failed", f); }).join("") + "</ul></div>" +
                (ok.length ? '<details class="hud-bulk-fold"><summary>' + plural(ok.length, "deck") + " decommissioned</summary><ul class=\"hud-bulk-list\">" + ok.map(function (o) { return rowHtml(BASE.filter(function (b) { return b.id === o.id; })[0], "done", o.already ? "was already retired" : null); }).join("") + "</ul></details>" : "") +
                "</div>" +
                '<div class="hud-bulk-foot"><button type="button" class="hud-btn hud-btn--ghost" id="dlgDeselect">Deselect failed</button>' +
                (retry ? '<button type="button" class="hud-btn hud-btn--danger-solid" id="dlgRetry">Retry ' + retry + "</button>" : "") +
                '<button type="button" class="hud-btn" id="dlgCancel">Close</button></div>';
        }
    }

    /* ---------------- the (fake) server ---------------- */
    var REASONS = {
        gone:   { human: "This deck no longer exists in Commander Lab. Refresh the list.", raw: "404 deck not found: {id}", retryable: false },
        status: { human: "Not a Physical deck any more, so it can’t be decommissioned.", raw: "400 only physical decks can be decommissioned (deck status is 'digital')", retryable: false },
        server: { human: "The server hit an error. Try again.", raw: "500 internal error", retryable: true }
    };
    function fakeServer(ids, attempt) {
        var delay = { success: 1100, partial: 1300, fail: 900, network: 1900 }[S.outcome];
        return new Promise(function (resolve) {
            setTimeout(function () {
                var ok = [], failed = [];
                function fail(id, key) { var r = REASONS[key]; failed.push({ id: id, human: r.human, raw: r.raw.replace("{id}", id), retryable: r.retryable }); }
                ids.forEach(function (id, i) {
                    if (S.outcome === "partial" && attempt === 1 && i === 1) return fail(id, "status");
                    if (S.outcome === "partial" && attempt === 1 && i === 3) return fail(id, "server");
                    if (S.outcome === "partial" && attempt > 1 && i === 1) return fail(id, "status");
                    if (S.outcome === "fail") return fail(id, i % 2 ? "server" : "gone");
                    if (S.outcome === "network" && attempt === 1) return fail(id, "server");
                    ok.push({ id: id, already: false });
                });
                resolve({ network: S.outcome === "network" && attempt === 1, ok: ok, failed: failed });
            }, delay);
        });
    }

    var attempts = 0;
    function submit(ids) {
        attempts += 1; S.busy = true; S.dlg.ids = ids; S.dlg.view = "busy";
        drawDlg(); renderBar(); render();
        var go = $("dlgGo"); if (go) go.focus();
        say("Decommissioning " + plural(ids.length, "deck") + ".");
        return fakeServer(ids, attempts).then(function (R) {
            S.busy = false;
            // apply successes: decks become Retired and leave the Physical grid
            R.ok.forEach(function (o) { var d = byId(o.id); if (d) d.status = "retired"; S.sel.delete(o.id); S.failed.delete(o.id); });
            R.failed.forEach(function (f) { S.failed.set(f.id, f); S.sel.add(f.id); });
            if (!R.failed.length) {
                var cards = R.ok.reduce(function (a, o) { return a + BASE.filter(function (b) { return b.id === o.id; })[0].cards; }, 0);
                closeDlgOnly();
                var tiles = R.ok.map(function (o) { return document.querySelector('.deck-card[data-id="' + CSS.escape(o.id) + '"]'); }).filter(Boolean);
                tiles.forEach(function (t) { t.classList.add("is-leaving"); });
                setTimeout(function () {
                    S.mode = false; S.failed.clear(); S.anchor = null; render();
                    toast({ tone: "ok", glyph: "✓", title: plural(R.ok.length, "deck") + " decommissioned", body: cards + " cards returned to your collection. Decks moved to Retired.", action: { label: "View Retired", run: function () { S.showAll = true; $("showAllFolders").checked = true; render(); } } });
                    say(plural(R.ok.length, "deck") + " decommissioned. " + cards + " cards returned to your collection.");
                    $("selectBtn").focus();
                }, tiles.length && !matchMedia("(prefers-reduced-motion: reduce)").matches ? 170 : 0);
                return;
            }
            S.dlg.view = "result"; S.dlg.result = R;
            drawDlg(); render();
            // keep focus inside the dialog: land on the safest primary action
            var f = $("dlgRetry") || $("dlgCancel"); if (f) f.focus();
            say(R.ok.length + " of " + ids.length + " decommissioned. " + R.failed.length + " failed.");
        });
    }
    function closeDlgOnly() { HUD.closeModal("bulkDlg"); S.dlg = null; }

    /* ---------------- events ---------------- */
    document.addEventListener("click", function (ev) {
        var t = ev.target;
        // Block scrim-close while busy / own close handling (HUD would also close; we swallow it).
        if (t.id === "bulkDlg") { ev.stopImmediatePropagation(); if (!S.busy) closeDlg(); return; }
        var pick = t.closest && t.closest(".deck-pick");
        if (pick) {
            if (S.busy) { ev.preventDefault(); return; }
            var id = pick.dataset.id, on = pick.checked;
            if (ev.shiftKey && S.anchor) toggleRange(id, on); else setSel(id, on);
            S.anchor = id; render(); return;
        }
        var open = t.closest && t.closest("[data-open]");
        if (open) {
            if (ev.ctrlKey || ev.metaKey) { enter(false); setSel(open.dataset.open, true); S.anchor = open.dataset.open; render(); say("Select mode on. 1 selected."); return; }
            openQuickView(open.dataset.open, open); return;
        }
        if (t.id === "selectBtn" || (t.closest && t.closest("#selectBtn"))) { if (t.closest("#selectBtn").getAttribute("aria-disabled") === "true") return; S.mode ? exit() : enter(); return; }
        if (t.closest && t.closest("#bulkClear")) { if (!S.busy && S.sel.size) clearSel(); return; }
        if (t.closest && t.closest("#bulkDone")) { if (!S.busy) exit(); return; }
        if (t.closest && t.closest("#bulkGo")) { if (t.closest("#bulkGo").getAttribute("aria-disabled") !== "true") openDlg(Array.from(S.sel), t.closest("#bulkGo")); return; }
        if (t.closest && t.closest("#bulkWhy")) { var ids = Array.from(S.failed.keys()); S.dlg = null; openDlg(ids, t); S.dlg.view = "result"; S.dlg.result = { ok: [], network: false, failed: ids.map(function (i) { return S.failed.get(i); }) }; drawDlg(); $("dlgCancel").focus(); return; }
        if (t.id === "bulkAll") { if (S.busy) { ev.preventDefault(); return; } selectAllVisible(t.checked); render(); say(S.sel.size + " selected."); return; }
        if (t.closest && t.closest("#dlgCancel")) { if (!S.busy) closeDlg(); return; }
        if (t.closest && t.closest("#dlgGo")) {
            var go = t.closest("#dlgGo"); if (S.busy) return;
            if (go.getAttribute("aria-disabled") === "true") { var ty = $("bulkType"); ty.setAttribute("aria-invalid", "true"); ty.focus(); return; }
            attempts = 0; submit(S.dlg.ids); return;
        }
        if (t.closest && t.closest("#dlgRetry")) { var r = S.dlg.result.failed.filter(function (f) { return f.retryable; }).map(function (f) { return f.id; }); submit(r); return; }
        if (t.closest && t.closest("#dlgDeselect")) { S.dlg.result.failed.forEach(function (f) { S.sel.delete(f.id); S.failed.delete(f.id); }); closeDlg(); render(); say("Failed decks deselected."); return; }
        if (t.closest && t.closest("[data-qv-decom]")) { var qid = t.closest("[data-qv-decom]").dataset.qvDecom; HUD.closeModal("qvModal"); openDlg([qid], t.closest("[data-qv-decom]")); return; }
    }, true);

    document.addEventListener("input", function (ev) {
        if (ev.target.id === "bulkType") {
            var ok = ev.target.value.trim() === String(S.dlg.ids.length);
            $("dlgGo").setAttribute("aria-disabled", String(!ok));
            ev.target.removeAttribute("aria-invalid");
        }
        if (ev.target.id === "q") { S.q = ev.target.value; render(); }
    });
    $("showAllFolders").addEventListener("change", function (e) { S.showAll = e.target.checked; render(); });

    function typing(el) { return el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.type !== "checkbox" || el.isContentEditable); }
    function moveFocus(dir) {
        var boxes = Array.from(document.querySelectorAll(".deck-pick"));
        var i = boxes.indexOf(document.activeElement); if (i < 0) return;
        var r = boxes[i].closest(".deck-card").getBoundingClientRect(), best = null, bd = 1e9;
        if (dir === "ArrowRight" || dir === "ArrowLeft") { best = boxes[i + (dir === "ArrowRight" ? 1 : -1)]; }
        else boxes.forEach(function (b, j) {
            var c = b.closest(".deck-card").getBoundingClientRect(), dy = c.top - r.top;
            if ((dir === "ArrowDown" ? dy > r.height / 2 : dy < -r.height / 2) && Math.abs(c.left - r.left) < bd) { bd = Math.abs(c.left - r.left); best = b; }
        });
        if (best) best.focus();
    }
    document.addEventListener("keydown", function (ev) {
        if (S.dlg) {   // dialog owns the keyboard
            if (ev.key === "Escape") { ev.stopImmediatePropagation(); ev.preventDefault(); if (!S.busy) closeDlg(); return; }
            if (ev.key === "Tab") {
                var f = Array.from($("bulkPanel").querySelectorAll("button, input, [tabindex='0'], summary, a[href]")).filter(function (e) { return e.offsetParent !== null && !e.disabled; });
                if (!f.length) return;
                var first = f[0], last = f[f.length - 1];
                if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
                else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
            }
            if (ev.key === "Enter" && ev.target.id === "bulkType") { ev.preventDefault(); $("dlgGo").getAttribute("aria-disabled") === "true" ? null : ($("dlgGo").click()); }
            return;
        }
        if ($("qvModal").classList.contains("is-open")) return;
        if (typing(ev.target) || ev.altKey) return;
        if (ev.key === "s" || ev.key === "S") { if (ev.ctrlKey || ev.metaKey) return; ev.preventDefault(); S.mode ? exit() : enter(); return; }
        if (!S.mode) return;
        if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "a") { ev.preventDefault(); selectAllVisible(true); render(); say(S.sel.size + " selected."); return; }
        if (ev.key === "Escape") { S.sel.size || S.failed.size ? clearSel() : exit(); return; }
        if (/^Arrow/.test(ev.key) && document.activeElement.classList.contains("deck-pick")) { ev.preventDefault(); moveFocus(ev.key); }
    }, true);

    /* ---------------- single deck (quick view) ---------------- */
    function openQuickView(id, trigger) {
        var d = byId(id); returnFocus = trigger;
        $("qvTitle").textContent = d.name;
        $("qvBody").innerHTML = '<div>' + esc(d.cmdr) + '</div><div>' + HUD.statusChip(d.status, STATUS_LABELS[d.status]) + " " + HUD.pips(d.color) + "</div>" +
            '<div class="qv-actions"><button class="hud-btn hud-btn--primary" type="button">Sync</button><button class="hud-btn" type="button">Review</button>' +
            (d.status === "physical"
                ? '<button class="hud-btn hud-btn--danger" type="button" data-qv-decom="' + esc(d.id) + '" style="margin-left:auto" aria-haspopup="dialog">Decommission…</button>'
                : "") + "</div>";
        HUD.openModal("qvModal");
        $("qvBody").querySelector("button").focus();
    }

    /* ---------------- state menu (mock only) ---------------- */
    var P = function (n) { return S.decks.filter(selectable).slice(0, n).map(function (d) { return d.id; }); };
    var STATES = [
        ["idle", "01 Idle (no select mode)", function () {}],
        ["mode-0", "02 Select mode, 0 selected", function () { S.mode = true; }],
        ["mode-3", "03 3 selected", function () { S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); S.anchor = P(3)[2]; }],
        ["mode-all", "04 Select all (12)", function () { S.mode = true; selectAllVisible(true); }],
        ["filtered", "05 Filter hides 2 selected", function () { S.mode = true; P(4).forEach(function (i) { S.sel.add(i); }); S.q = "i"; $("q").value = "i"; }],
        ["other-group", "06 Show all: non-physical dimmed", function () { S.showAll = true; $("showAllFolders").checked = true; S.mode = true; P(2).forEach(function (i) { S.sel.add(i); }); }],
        ["quickview", "07 Single deck: quick view", function () { openQuickView("archidekt:101", null); }],
        ["dlg-1", "08 Dialog, 1 deck", function () { S.mode = true; S.sel.add("archidekt:101"); openDlg(["archidekt:101"], null); }],
        ["dlg-3", "09 Dialog, 3 decks", function () { S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); openDlg(P(3), null); }],
        ["dlg-7", "10 Dialog, 7 decks (type to confirm)", function () { S.mode = true; P(7).forEach(function (i) { S.sel.add(i); }); openDlg(P(7), null); }],
        ["dlg-filtered", "11 Dialog with a hidden-by-filter deck", function () { S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); S.q = "magic"; $("q").value = "magic"; openDlg(P(3), null); }],
        ["busy", "12 In flight", function () { S.outcome = "success"; $("outcomeSel").value = "success"; S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); openDlg(P(3), null); attempts = 0; S.outcome = "network"; submit(P(3)); S.outcome = "success"; }],
        ["partial", "13 Result: partial failure", function () { S.outcome = "partial"; $("outcomeSel").value = "partial"; S.mode = true; P(5).forEach(function (i) { S.sel.add(i); }); openDlg(P(5), null); S.dlg.view = "confirm"; attempts = 0; submit(P(5)); }],
        ["fail", "14 Result: every deck failed", function () { S.outcome = "fail"; $("outcomeSel").value = "fail"; S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); openDlg(P(3), null); attempts = 0; submit(P(3)); }],
        ["after-partial", "15 After closing a partial result", function () { S.outcome = "partial"; $("outcomeSel").value = "partial"; S.mode = true; P(5).forEach(function (i) { S.sel.add(i); }); S.dlg = { ids: P(5), view: "confirm" }; attempts = 0; submit(P(5)).then(function () { closeDlg(); }); }],
        ["toast", "16 Success toast", function () { S.outcome = "success"; $("outcomeSel").value = "success"; S.mode = true; P(3).forEach(function (i) { S.sel.add(i); }); S.dlg = { ids: P(3), view: "confirm" }; attempts = 0; submit(P(3)); }],
        ["empty", "17 No physical decks left", function () { S.decks.forEach(function (d) { if (d.status === "physical") d.status = "retired"; }); }]
    ];
    function resetAll() {
        S.decks = JSON.parse(JSON.stringify(BASE)); S.mode = false; S.sel.clear(); S.failed.clear(); S.anchor = null; S.busy = false; S.q = ""; S.showAll = false;
        $("q").value = ""; $("showAllFolders").checked = false;
        if (S.dlg) closeDlgOnly(); HUD.closeModal("qvModal"); $("toasts").innerHTML = "";
    }
    var sel = $("stateSel");
    STATES.forEach(function (s) { var o = document.createElement("option"); o.value = s[0]; o.textContent = s[1]; sel.appendChild(o); });
    function setState(name) {
        resetAll(); S.outcome = $("outcomeSel").value;
        var st = STATES.filter(function (s) { return s[0] === name; })[0]; if (!st) return;
        st[2](); render();
    }
    sel.addEventListener("change", function () { setState(sel.value); });
    $("outcomeSel").addEventListener("change", function (e) { S.outcome = e.target.value; });
    window.BDMOCK = { setState: setState, STATES: STATES };

    var start = new URLSearchParams(location.search).get("state") || "idle";
    sel.value = start; setState(start);
})();
