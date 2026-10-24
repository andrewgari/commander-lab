/* Deck Tuner overlay — DT-7 module: recommendation explanations + the
 * "Reconsider" / "Recommend something else" buttons (overlay spec §3.3).
 *
 * Self-contained under window.TuneOverlay so sibling cards (DT-1 shell, DT-5
 * cuts, DT-6 adds) extend the same namespace instead of overwriting this file.
 *
 * Two mount modes:
 *   1. Classic Tune page (deck_tune.html): auto-boots when the page loaded this
 *      script with a data-deck-id attribute and a #swaps list exists. Rows
 *      (.hud-swap[data-cut]) rendered by the page's own inline script are
 *      decorated after every render via MutationObserver — nothing in the
 *      page's script is touched.
 *   2. DT-1 overlay shell: call TuneOverlay.explanations.boot({api, root,
 *      rowSelector, cutAttr}) directly.
 *
 * Styles are injected scoped (.tn7-*) on HUD tokens so hud.css stays untouched.
 */
(function () {
    "use strict";
    var NS = (window.TuneOverlay = window.TuneOverlay || {});
    if (NS.explanations) return;

    function esc(s) {
        if (window.HUD && window.HUD.esc) return window.HUD.esc(s);
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    var CSS =
        ".tn7-expl{grid-column:1/-1;display:flex;flex-direction:column;gap:var(--sp-2);" +
        "padding:var(--sp-2) 0 0;border-top:var(--hairline) solid var(--line)}" +
        ".tn7-reason{margin:0;color:var(--text-2);font-size:var(--fs-xs)}" +
        ".tn7-recon{margin:0;color:var(--text-1);font-size:var(--fs-xs)}" +
        ".tn7-recon .tn7-tag{color:var(--text-3);text-transform:uppercase;letter-spacing:.04em;" +
        "font-family:var(--font-mono);font-size:var(--fs-micro);margin-right:var(--sp-1)}" +
        ".tn7-prev{margin:0;color:var(--text-3);font-size:var(--fs-xs)}" +
        ".tn7-act{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap}" +
        ".tn7-msg{color:var(--text-3);font-size:var(--fs-xs)}";

    function injectCss() {
        if (document.getElementById("tn7-css")) return;
        var el = document.createElement("style");
        el.id = "tn7-css";
        el.textContent = CSS;
        document.head.appendChild(el);
    }

    var Ex = {};
    var S = {
        api: null, root: null,
        rowSelector: ".hud-swap[data-cut]", cutAttr: "data-cut",
        entries: {}, log: [], loading: null, observer: null,
    };

    function send(method, path, body) {
        return fetch(S.api + path, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        }).then(function (r) {
            return r.json().then(function (j) {
                if (!r.ok || j.success === false) {
                    var e = new Error(j.error || "HTTP " + r.status);
                    e.status = r.status;
                    throw e;
                }
                return j;
            });
        });
    }

    function index(payload) {
        S.entries = {};
        S.log = (payload && payload.entry_log) || [];
        ((payload && payload.swaps) || []).forEach(function (s) {
            if (!s.cut || !s.cut.name) return;
            var add = (s.pick === "buy" ? s.buy : s.own) || s.own || s.buy;
            if (!add) return;
            S.entries[s.cut.name] = {
                entryId: s.entry_id || null, cut: s.cut.name, add: add.name,
                reason: s.reason || s.why || "", status: s.status || "open",
                reconsideration: s.reconsideration || "",
            };
        });
    }

    function fetchSaved() {
        return send("GET", "/tune/saved").then(function (j) {
            var rec = j.recommendation, p = rec && rec.payload;
            if (!p) { index(null); return; }
            var legacy = (p.swaps || []).some(function (s) { return !s.entry_id; });
            if (!legacy) { index(p); return; }
            // Saved before DT-7: /tune/why doubles as the entry sync (it writes
            // entry ids + explanations back into the active record), then refetch.
            var pr = rec.params || {};
            var opts = { mode: pr.mode || "flex", in_deck: pr.in_deck !== false, buy: pr.buy !== false,
                         goal: pr.goal || "balanced" };
            return send("POST", "/tune/why", opts).then(reload, reload);
            function reload() {
                return send("GET", "/tune/saved").then(function (j2) {
                    index(j2.recommendation && j2.recommendation.payload);
                });
            }
        });
    }

    function load() {
        if (S.loading) return S.loading;
        S.loading = fetchSaved().catch(function () { /* entries are best effort */ })
            .then(function () { S.loading = null; });
        return S.loading;
    }

    function previousFor(cut) {
        var prev = null;
        S.log.forEach(function (e) {
            if (e && e.cut === cut && e.status === "ignored") prev = e;
        });
        return prev;
    }

    function decorateRow(li) {
        var cut = li.getAttribute(S.cutAttr);
        if (!cut) return;
        var stale = li.querySelector(".tn7-expl");
        if (stale) stale.remove();
        var e = S.entries[cut];
        if (!e || e.status === "ignored") return;
        var html = "";
        // The page's own .tn-why shows the same (LLM) explanation — don't double it.
        if (!li.querySelector(".tn-why") && e.reason) {
            html += '<p class="tn7-reason">' + esc(e.reason) + "</p>";
        }
        if (e.reconsideration) {
            html += '<p class="tn7-recon"><span class="tn7-tag">Reconsidered</span>' + esc(e.reconsideration) + "</p>";
        }
        var prev = previousFor(cut);
        if (prev && prev.card) {
            html += '<p class="tn7-prev">Previously suggested for this slot: ' + esc(prev.card) + " (ignored).</p>";
        }
        html += '<div class="tn7-act">' +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-tn7="reconsider"' +
            ' title="Challenge this recommendation; it answers and stands its ground">Reconsider</button>' +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-tn7="alternative"' +
            ' title="Ignore this card and recommend something else for the same slot">Recommend something else</button>' +
            '<span class="tn7-msg" aria-live="polite"></span></div>';
        var div = document.createElement("div");
        div.className = "tn7-expl";
        div.innerHTML = html;
        li.appendChild(div);
    }

    function decorate() {
        if (!S.root) return;
        var rows = S.root.querySelectorAll(S.rowSelector);
        for (var i = 0; i < rows.length; i++) decorateRow(rows[i]);
    }

    var refreshTimer = null;
    function scheduleRefresh() {
        if (refreshTimer) return;
        refreshTimer = setTimeout(function () {
            refreshTimer = null;
            load().then(decorate);
        }, 250);
    }

    function watch() {
        decorate();
        if (S.observer || !S.root || !window.MutationObserver) return;
        S.observer = new MutationObserver(function () {
            // The page re-renders rows on every action; decorate the new nodes.
            // Only re-fetch entries when a row shows up that we don't know yet.
            var unknown = false;
            var rows = S.root.querySelectorAll(S.rowSelector);
            for (var i = 0; i < rows.length; i++) {
                var cut = rows[i].getAttribute(S.cutAttr);
                if (cut && !S.entries[cut]) { unknown = true; break; }
            }
            if (unknown) scheduleRefresh();
            else decorate();
        });
        S.observer.observe(S.root, { childList: true, subtree: true });
    }

    function msg(li, text) {
        var el = li.querySelector(".tn7-msg");
        if (el) el.textContent = text || "";
    }

    function buttons(li, disabled) {
        var bs = li.querySelectorAll("[data-tn7]");
        for (var i = 0; i < bs.length; i++) bs[i].disabled = disabled;
    }

    function onReconsider(li) {
        var cut = li.getAttribute(S.cutAttr);
        buttons(li, true);
        msg(li, "Thinking it over…");
        send("POST", "/tune/reconsider", { cut: cut }).then(function (j) {
            if (S.entries[cut]) S.entries[cut].reconsideration = j.response;
            decorateRow(li);
        }).catch(function (e) {
            msg(li, e.message || "Reconsider failed.");
            buttons(li, false);
        });
    }

    function onAlternative(li) {
        var cut = li.getAttribute(S.cutAttr);
        buttons(li, true);
        msg(li, "Finding another card for this slot…");
        send("POST", "/tune/alternative", { cut: cut }).then(function (j) {
            if (!j.alternative) {
                msg(li, j.message || "No alternative right now.");
                buttons(li, false);
                return;
            }
            // The saved recommendation changed under the page's own snapshot, so
            // reload focused on this slot: the page re-renders the replacement row.
            var glue = location.search ? "&" : "?";
            location.href = location.pathname + location.search + glue + "focus=" + encodeURIComponent(cut);
        }).catch(function (e) {
            msg(li, e.message || "Could not find an alternative.");
            buttons(li, false);
        });
    }

    function onClick(ev) {
        var b = ev.target && ev.target.closest ? ev.target.closest("[data-tn7]") : null;
        if (!b) return;
        var li = b.closest(S.rowSelector.split(",")[0]);
        if (!li) return;
        ev.preventDefault();
        if (b.getAttribute("data-tn7") === "reconsider") onReconsider(li);
        else onAlternative(li);
    }

    Ex.boot = function (opts) {
        opts = opts || {};
        if (!opts.api) return;
        injectCss();
        S.api = opts.api;
        S.root = opts.root || document.body;
        if (opts.rowSelector) S.rowSelector = opts.rowSelector;
        if (opts.cutAttr) S.cutAttr = opts.cutAttr;
        S.root.addEventListener("click", onClick);
        load().then(watch);
    };

    Ex.reload = function () { return load().then(decorate); };
    Ex.entries = function () { return S.entries; };
    NS.explanations = Ex;

    // Classic Tune page auto-boot: the template includes this script with
    // data-deck-id; #swaps is the existing recommendation list.
    document.addEventListener("DOMContentLoaded", function () {
        var tag = document.querySelector("script[data-tune-overlay][data-deck-id]");
        var swaps = document.getElementById("swaps");
        if (!tag || !swaps) return;
        Ex.boot({ api: "/api/decks/" + encodeURIComponent(tag.getAttribute("data-deck-id")), root: swaps });
    });
})();
