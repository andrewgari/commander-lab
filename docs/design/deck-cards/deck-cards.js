/* ==========================================================================
   Deck-list card options (mockup harness). Not loaded by the app.

   Each option page calls DC.boot({ option, render, wrap }) where
     render(deck, i) -> markup for one deck (card / row / tile)
     wrap(itemsHtml, group) -> the container for one group (grid / list)
   Data: window.DECKS (data.js, a snapshot of GET /api/decks).
   Needs static/js/hud.js (HUD.pips, HUD.statusChip, HUD.scryfall, modals).
   ========================================================================== */
(function () {
    "use strict";

    var LABEL = { physical: "Physical", digital: "Virtual", testing: "WIP", retired: "Retired" };
    var GROUPS = [
        { status: "physical", title: "Physical decks" },
        { status: "digital", title: "Virtual decks" },
        { status: "testing", title: "WIP decks" },
        { status: "retired", title: "Retired decks" }
    ];
    // The three decks from Andrew's screenshots: longest title (Tetsuo),
    // shortest title (Loot), partner pair with an orphaned "Seer" (Keruga + Helga).
    var PROBLEM_IDS = [3503957, 8553560, 8781313];
    var MAX_STAGGER = 15;  // cap the entrance stagger so a long list isn't slow

    var esc = function (s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    };

    // "Keruga, the Macrosage" -> "Keruga"; "Avatar Aang // Aang, ..." -> "Avatar Aang".
    function shortName(n) { return String(n).split(" // ")[0].split(",")[0].trim(); }

    function commanders(d) { return (d.commanders || []).slice(); }
    function count(d) { return commanders(d).length || (d.commander_uids || []).length; }

    // Visible commander line: full name for one, short names for partners.
    function cmdrLine(d) {
        var c = commanders(d);
        if (!c.length) return "";
        return c.length > 1 ? c.map(shortName).join(" & ") : c[0];
    }
    function cmdrFull(d) { return commanders(d).join(" & ") || "No commander set"; }

    // One frame no matter how many commanders: partners split it (data-n="2").
    function art(d, cls) {
        var uids = (d.commander_uids || []).slice(0, Math.max(1, count(d)));
        var names = commanders(d);
        var imgs = uids.map(function (uid, i) {
            var s = HUD.scryfall(uid, "art_crop");
            return '<img src="' + esc(s.src) + '" data-fallback="' + esc(s.fallback) +
                '" alt="" loading="lazy" decoding="async" data-name="' + esc(names[i] || "") + '">';
        }).join("");
        if (!imgs) imgs = '<span class="dc-art-none">No commander</span>';
        return '<span class="' + (cls || "dc-art") + '" data-n="' + Math.min(uids.length || 1, 2) + '" aria-hidden="true">' + imgs + "</span>";
    }

    function chip(d) { return HUD.statusChip(d.status, LABEL[d.status] || "Virtual"); }
    function pips(d, size) { return HUD.pips(d.color || "", size); }

    function deckHref(d) { return "/deck/" + encodeURIComponent(d.name); }
    function invHref(d) { return "/inventory?deck=" + encodeURIComponent(d.name); }

    // Same glyph as the sidebar's Inventory item, so the icon link is learnable.
    var INV_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>';

    function invLink(d, withText) {
        return '<a class="dc-inv' + (withText ? " dc-inv--text" : "") + '" href="' + esc(invHref(d)) +
            '" data-mock-nav title="Inventory for ' + esc(d.name) + '" aria-label="Inventory for ' + esc(d.name) + '">' +
            INV_ICON + (withText ? "<span>Inventory</span>" : "") + "</a>";
    }
    function titleLink(d) {
        return '<a href="' + esc(deckHref(d)) + '" data-mock-nav>' + esc(d.name) + "</a>";
    }
    // Stretched button: the whole card opens the quick-view modal (mouse and
    // keyboard). Links inside the card sit above it.
    function openBtn(d) {
        return '<button type="button" class="dc-open" data-open="' + d.id + '" aria-label="Quick view: ' + esc(d.name) + '"></button>';
    }

    // --- Shell (sidebar copied from the live app, v0.4.29) ----------------------
    var SIDEBAR = '<nav class="sidebar" aria-label="Primary"><a href="#" class="brand" title="Commander Lab"><svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg><span class="brand-label">Cmdr Lab</span></a>' +
        '<div class="nav-section-label">Navigate</div><div class="nav-items">' +
        navItem("Home", '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline>') +
        navItem("Commander Decks", '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>', true) +
        navItem("Inventory", '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line>') +
        navItem("Instances", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path>') +
        navItem("Tag Management", '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line>') +
        '</div><div class="nav-section-label">Reference</div><div class="nav-items">' +
        navItem("Changelog", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline>') +
        navItem("Styleguide", '<rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect>') +
        '</div><div class="nav-section-label">Configure</div><div class="nav-items">' +
        navItem("Settings", '<circle cx="12" cy="12" r="3"></circle><path d="M12 1v3M12 20v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M1 12h3M20 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path>') +
        '</div><div class="sidebar-footer"><span class="sidebar-footer-label">Version</span><span class="version-badge">mockup</span></div></nav>';

    function navItem(label, path, active) {
        return '<a href="#" class="nav-item' + (active ? " active" : "") + '" title="' + label + '"' + (active ? ' aria-current="page"' : "") +
            '><svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            path + '</svg><span class="nav-label">' + label + "</span></a>";
    }

    var OPTIONS = [
        { key: "a", file: "a-compact.html", label: "A · Compact card" },
        { key: "b", file: "b-rows.html", label: "B · Dense rows" },
        { key: "c", file: "c-tiles.html", label: "C · Art tile" }
    ];

    function pageHead(opt, extra) {
        var sw = OPTIONS.map(function (o) {
            return '<a href="' + o.file + '"' + (o.key === opt ? ' aria-current="page"' : "") + ">" + o.label + "</a>";
        }).join("");
        return '<nav class="dc-switch" aria-label="Mockup options"><span class="hud-label">Mockup · deck list</span>' + sw + "</nav>" +
            '<header class="hud-page-head"><div><span class="hud-label">Decks</span><h1>Commander Decks</h1></div>' +
            '<div class="hud-page-actions">' + (extra || "") +
            '<label class="dc-toggle"><input type="checkbox" id="dcShowAll" checked> Show all decks</label>' +
            '<button type="button" class="hud-btn hud-btn--primary" data-mock-nav>Import deck</button></div></header>';
    }

    function section(code, title, n, body, id) {
        return '<section class="dc-section"' + (id ? ' id="' + id + '"' : "") + ' aria-labelledby="h-' + code + '">' +
            '<div class="hud-section-head"><span class="hud-section-id">' + code + '</span><h2 id="h-' + code + '">' + esc(title) +
            '</h2><span class="dc-count">' + n + "</span></div>" + body + "</section>";
    }

    // --- Quick-view modal (stand-in for the existing deck modal, unchanged) ----
    var MODAL = '<div class="hud-modal" id="dcModal" aria-hidden="true"><section class="hud-panel hud-modal-dialog dc-modal" role="dialog" aria-modal="true" aria-labelledby="dcModalTitle">' +
        '<div class="hud-panel-head"><span class="hud-label">Quick view (existing deck modal opens here, unchanged)</span><button type="button" class="hud-btn hud-btn--ghost hud-btn--sm" data-hud-close>Close · Esc</button></div>' +
        '<div class="hud-panel-body dc-modal-body" id="dcModalBody"></div></section></div>';

    var lastTrigger = null;
    function openDeck(d, trigger) {
        lastTrigger = trigger || document.activeElement;
        document.getElementById("dcModalBody").innerHTML =
            art(d, "dc-art dc-modal-art") +
            '<div><h2 id="dcModalTitle" class="dc-modal-title">' + titleLink(d) + "</h2>" +
            '<p class="dc-modal-cmdr">' + esc(cmdrFull(d)) + "</p>" +
            '<div class="dc-modal-meta">' + chip(d) + pips(d) + invLink(d, true) + "</div></div>";
        HUD.openModal("dcModal");
        var close = document.querySelector("#dcModal [data-hud-close]");
        if (close) close.focus();
    }
    // hud.js closes on Esc / scrim / [data-hud-close]; hand focus back after it does.
    new MutationObserver(function () {
        var m = document.getElementById("dcModal");
        if (m && !m.classList.contains("is-open") && lastTrigger) { lastTrigger.focus(); lastTrigger = null; }
    }).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ["class"] });

    // --- Toast for links that would leave the mockup ---------------------------
    function toast(text) {
        var t = document.getElementById("dcToast");
        t.textContent = text;
        t.classList.add("is-on");
        clearTimeout(toast.t);
        toast.t = setTimeout(function () { t.classList.remove("is-on"); }, 1600);
    }

    function boot(cfg) {
        var decks = (window.DECKS || []).slice();
        var byId = {};
        decks.forEach(function (d) { byId[d.id] = d; });

        document.body.classList.add("app-shell", "dc-opt-" + cfg.option);
        document.body.insertAdjacentHTML("afterbegin", SIDEBAR);
        var main = document.getElementById("main");

        function renderAll() {
            var showAll = document.getElementById("dcShowAll");
            var all = !showAll || showAll.checked;
            var q = (cfg.filter ? cfg.filter() : "").toLowerCase();
            var match = function (d) {
                return !q || (d.name + " " + cmdrFull(d)).toLowerCase().indexOf(q) !== -1;
            };
            var html = "";
            var problems = PROBLEM_IDS.map(function (id) { return byId[id]; }).filter(Boolean).filter(match);
            if (problems.length) {
                html += section("00", "Problem decks (from the screenshots)", problems.length,
                    cfg.wrap(problems.map(cfg.render).join(""), "problem"), "problems");
            }
            var order = { physical: 1, digital: 2, testing: 3, retired: 4 };
            if (cfg.flat) {
                // One table across every status: the Status column earns its
                // place and sorting works across groups.
                var flat = decks.filter(function (d) { return (all || d.status === "physical") && match(d); })
                    .sort(function (a, b) { return (order[a.status] || 9) - (order[b.status] || 9) || a.name.localeCompare(b.name); });
                html += section("01", all ? "All decks" : "Physical decks", flat.length,
                    flat.length ? cfg.wrap(flat.map(cfg.render).join(""), "all") : '<div class="hud-empty">No matches</div>');
            }
            GROUPS.forEach(function (g, gi) {
                if (cfg.flat) return;
                if (!all && g.status !== "physical") return;
                var list = decks.filter(function (d) { return (d.status || "testing") === g.status && match(d); });
                if (cfg.sort) list = cfg.sort(list);
                var body = list.length
                    ? cfg.wrap(list.map(cfg.render).join(""), g.status)
                    : '<div class="hud-empty">' + (q ? "No matches" : "No " + g.title.toLowerCase()) + "</div>";
                html += section("0" + (gi + 1), g.title, list.length, body);
            });
            document.getElementById("dcList").innerHTML = html;
            // Entrance stagger per section, capped.
            document.querySelectorAll(".dc-section").forEach(function (s) {
                s.querySelectorAll(".dc-item").forEach(function (el, i) {
                    el.style.setProperty("--i", Math.min(i, MAX_STAGGER));
                    el.classList.add("hud-enter");
                });
            });
            if (cfg.after) cfg.after();
        }

        main.innerHTML = pageHead(cfg.option, cfg.headExtra) + (cfg.toolbar || "") +
            '<div id="dcList"></div>' + MODAL + '<div id="dcToast" class="dc-toast" role="status" aria-live="polite"></div>';
        renderAll();

        document.getElementById("dcShowAll").addEventListener("change", renderAll);
        main.addEventListener("click", function (ev) {
            var nav = ev.target.closest("[data-mock-nav]");
            if (nav) {
                ev.preventDefault();
                toast("→ " + (nav.getAttribute("href") || nav.textContent.trim()) + "  (live app)");
                return;
            }
            var open = ev.target.closest("[data-open]");
            if (open) openDeck(byId[open.getAttribute("data-open")], open);
        });

        // Keyboard: j/k (or ↓/↑ in list views) move between decks, Enter opens,
        // i opens inventory for the focused deck, / focuses the filter (B).
        document.addEventListener("keydown", function (ev) {
            var t = ev.target;
            if (t.matches && t.matches("input, textarea, select")) {
                if (ev.key === "Escape") t.blur();
                return;
            }
            if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
            if (document.querySelector(".hud-modal.is-open")) return;
            var items = Array.prototype.slice.call(document.querySelectorAll(".dc-open"));
            var cur = t.closest ? t.closest(".dc-item") : null;
            var idx = cur ? items.indexOf(cur.querySelector(".dc-open")) : -1;
            if (ev.key === "j" || ev.key === "k") {
                ev.preventDefault();
                var next = ev.key === "j" ? Math.min(items.length - 1, idx + 1) : Math.max(0, idx - 1);
                if (items[next]) { items[next].focus(); items[next].closest(".dc-item").scrollIntoView({ block: "nearest" }); }
            } else if (ev.key === "i" && cur) {
                var inv = cur.querySelector(".dc-inv");
                if (inv) inv.click();
            } else if (ev.key === "/" && cfg.filterInput) {
                ev.preventDefault();
                document.getElementById(cfg.filterInput).focus();
            }
        });

        return { rerender: renderAll };
    }

    window.DC = {
        boot: boot, esc: esc, art: art, chip: chip, pips: pips, cmdrLine: cmdrLine, cmdrFull: cmdrFull,
        count: count, shortName: shortName, titleLink: titleLink, invLink: invLink, openBtn: openBtn, LABEL: LABEL
    };
})();
