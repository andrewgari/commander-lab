/* ==========================================================================
   Commander Lab — HUD component kit, client side (window.HUD).
   Loaded from base.html on every page.

   - Sortable tables: any <table data-hud-sortable> whose <th> has
     data-sort="num|text" sorts on header click; aria-sort is kept in sync.
     A <td data-value="..."> overrides the cell text as the sort key.
   - String builders that mirror the Jinja macros in templates/partials/hud.html
     for pages that render from fetched JSON: HUD.pips(), HUD.chip(),
     HUD.statusChip(), HUD.stat(), HUD.stack(), HUD.barChart(),
     HUD.rankList(), HUD.art(). Output is escaped.
   - HUD.scryfall(uid, version): CDN image URL + API fallback for a card.
   - Modals: HUD.openModal()/closeModal() on a .hud-modal; scrim click,
     [data-hud-close] and Escape close it.
   ========================================================================== */
(function () {
    "use strict";

    // Keep in sync with STATUS_TONES in templates/partials/hud.html.
    var STATUS_TONES = {
        physical: "ok", testing: "signal", digital: "data", retired: "neutral",
        in_deck: "ok", in_collection: "data", in_mail: "signal", not_owned: "neutral",
        clean: "ok", dirty: "signal", error: "danger", untracked: "neutral"
    };
    var MANA = ["W", "U", "B", "R", "G", "C", "M"];

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    function pips(colors, size) {
        var list = Array.isArray(colors) ? colors : String(colors || "").split("");
        list = list.map(function (c) { return String(c).toUpperCase(); })
                   .filter(function (c) { return MANA.indexOf(c) !== -1; });
        var label = list.length ? list.join(" ") : "Colourless";
        if (!list.length) list = ["C"];
        var cls = size ? " hud-pip--" + size : "";
        return '<span class="hud-pips" role="img" aria-label="' + esc(label) + '">' +
            list.map(function (c) {
                return '<span class="hud-pip hud-pip--' + c.toLowerCase() + cls + '" aria-hidden="true">' + c + "</span>";
            }).join("") + "</span>";
    }

    function chip(text, tone, dot) {
        return '<span class="hud-chip hud-chip--' + esc(tone || "neutral") + '">' +
            (dot ? '<span class="hud-chip-dot" aria-hidden="true"></span>' : "") + esc(text) + "</span>";
    }

    function statusChip(status, text) {
        var key = String(status || "").toLowerCase();
        return chip(text || key.replace(/_/g, " "), STATUS_TONES[key] || "neutral", true);
    }

    // Attribute string from an object; values escaped, null/false skipped.
    function attrs(obj) {
        return Object.keys(obj || {}).map(function (k) {
            var v = obj[k];
            if (v == null || v === false) return "";
            return " " + k + (v === true ? "" : '="' + esc(v) + '"');
        }).join("");
    }

    // Stat tile (mirrors the `stat` macro). opts: unit, foot, mod ("hero signal").
    function stat(label, value, opts) {
        opts = opts || {};
        var mods = String(opts.mod || "").split(/\s+/).filter(Boolean)
            .map(function (m) { return " hud-stat--" + m; }).join("");
        return '<div class="hud-stat' + mods + '">' +
            '<span class="hud-label">' + esc(label) + "</span>" +
            '<span class="hud-stat-value">' + esc(value) +
            (opts.unit ? '<span class="hud-stat-unit">' + esc(opts.unit) + "</span>" : "") + "</span>" +
            (opts.foot ? '<span class="hud-stat-foot"><span>' + esc(opts.foot) + "</span></span>" : "") +
            "</div>";
    }

    // Colour-distribution bar (mirrors `stack`). segments: [{color, value}].
    function stack(segments, legend) {
        var total = segments.reduce(function (s, x) { return s + (x.value || 0); }, 0) || 1;
        var label = segments.map(function (s) { return s.color + " " + s.value; }).join(", ");
        var bar = '<div class="hud-stack" role="img" aria-label="' + esc(label) + '">' +
            segments.filter(function (s) { return s.value; }).map(function (s) {
                return '<span class="hud-swatch--' + esc(String(s.color).toLowerCase()) + '" style="flex: ' + Number(s.value) +
                    '" title="' + esc(s.color + " " + s.value) + '"></span>';
            }).join("") + "</div>";
        if (legend === false) return bar;
        return bar + '<div class="hud-stack-legend">' + segments.map(function (s) {
            return '<span><span class="hud-swatch hud-swatch--' + esc(String(s.color).toLowerCase()) + '"></span>' + esc(s.color) +
                ' <span class="hud-muted">' + esc(s.value) + " · " + Math.round(s.value / total * 100) + "%</span></span>";
        }).join("") + "</div>";
    }

    // Vertical bar chart (mirrors `bar_chart`). bars: [{label, value}].
    // opts: hl (bar index drawn in signal), height, ticks, legend [series, ref, hl], title,
    // ref (numbers, one per bar: drawn as a dashed step line, e.g. a template curve).
    function barChart(bars, opts) {
        opts = opts || {};
        var ticks = opts.ticks || 4, height = opts.height || 160, n = bars.length || 1;
        var ref = Array.isArray(opts.ref) && opts.ref.length ? opts.ref : null;
        var peak = Math.max.apply(null, bars.map(function (b) { return b.value || 0; }).concat(ref || []).concat([1]));
        var step = Math.max(Math.ceil(peak / ticks), 1), top = step * ticks;
        var pct = function (v) { return Math.round((v / top) * 100 * 1000) / 1000; };
        var y = "", grid = "", rects = "", vals = "", xs = "";
        for (var i = 0; i <= ticks; i++) y += '<span style="bottom: ' + (i / ticks * 100) + '%">' + (i * step) + "</span>";
        for (var g = 1; g < ticks; g++) {
            var gy = 100 - g / ticks * 100;
            grid += '<line class="hud-chart-grid" x1="0" x2="' + n * 10 + '" y1="' + gy + '" y2="' + gy + '" vector-effect="non-scaling-stroke"/>';
        }
        bars.forEach(function (b, idx) {
            var h = pct(b.value || 0);
            rects += '<rect class="hud-chart-bar' + (opts.hl === idx ? " is-hl" : "") + '" x="' + (idx * 10 + 1.5) + '" y="' + (100 - h) +
                '" width="7" height="' + h + '"><title>' + esc(b.label + ": " + b.value) + "</title></rect>";
            vals += '<span style="bottom: ' + h + '%">' + (b.value ? esc(b.value) : "") + "</span>";
            xs += "<span>" + esc(b.label) + "</span>";
        });
        if (ref) {
            rects += '<path class="hud-chart-ref" vector-effect="non-scaling-stroke" d="' + ref.map(function (v, i) {
                return (i ? "L" : "M") + i * 10 + "," + (100 - pct(v || 0)) + " H" + (i + 1) * 10;
            }).join(" ") + '"/>';
        }
        var aria = (opts.title || "Bar chart") + ": " + bars.map(function (b) { return b.label + " " + b.value; }).join(", ");
        var legend = "";
        if (opts.legend) {
            legend = '<figcaption class="hud-chart-legend"><span><span class="hud-swatch"></span>' + esc(opts.legend[0]) + "</span>" +
                (ref && opts.legend[1] ? '<span><span class="hud-swatch hud-swatch--ref"></span>' + esc(opts.legend[1]) + "</span>" : "") +
                (opts.hl != null && opts.legend[2] ? '<span><span class="hud-swatch hud-swatch--signal"></span>' + esc(opts.legend[2]) + "</span>" : "") +
                "</figcaption>";
        }
        return '<figure class="hud-chart" style="--chart-h: ' + height + "px; --chart-n: " + n + '; margin: 0;" role="img" aria-label="' + esc(aria) + '">' +
            '<div class="hud-chart-body"><div class="hud-chart-yaxis" aria-hidden="true">' + y + "</div>" +
            '<div class="hud-chart-plot"><svg viewBox="0 0 ' + n * 10 + ' 100" preserveAspectRatio="none" aria-hidden="true">' + grid + rects + "</svg>" +
            '<div class="hud-chart-values" aria-hidden="true">' + vals + "</div></div>" +
            '<div class="hud-chart-xaxis" aria-hidden="true">' + xs + "</div></div>" + legend + "</figure>";
    }

    // Horizontal ranked-bar list (mirrors `rank_list`). items: [{label, value, display, sub, href, color, hl}].
    function rankList(items, opts) {
        opts = opts || {};
        var top = opts.max || Math.max.apply(null, items.map(function (i) { return i.value || 0; }).concat([1]));
        var numbered = opts.numbered !== false;
        return '<ol class="hud-rank">' + items.map(function (it, idx) {
            var c = it.color ? String(it.color).toLowerCase() : "";
            var label = it.href ? '<a href="' + esc(it.href) + '">' + esc(it.label) + "</a>" : esc(it.label);
            var width = Math.round(Math.min(it.value / top * 100, 100) * 10) / 10;
            return '<li class="hud-rank-item' + (it.hl ? " is-hl" : "") + '">' +
                '<span class="hud-rank-pos">' + (numbered ? idx + 1 : "") + "</span>" +
                '<span class="hud-rank-label">' + (c ? '<span class="hud-swatch hud-swatch--' + esc(c) + '"></span>' : "") +
                '<span class="hud-rank-text"><span>' + label + "</span>" + (it.sub ? '<span class="hud-rank-sub">' + esc(it.sub) + "</span>" : "") + "</span></span>" +
                '<span class="hud-rank-track" role="presentation"><span class="hud-rank-fill" style="width: ' + width + "%" + (c ? "; --swatch: var(--mana-" + esc(c) + ")" : "") + '"></span></span>' +
                '<span class="hud-rank-value">' + esc(it.display != null ? it.display : it.value) + "</span></li>";
        }).join("") + "</ol>";
    }

    // Card-art frame (mirrors `art`). opts: meta, colors, href, kind ("art"|"card"),
    // chip [text, tone], alt, fallback (image URL tried once if img fails), attrs {}.
    function art(name, img, opts) {
        opts = opts || {};
        var tag = opts.href ? "a" : "figure", cap = opts.href ? "div" : "figcaption";
        var extra = attrs(Object.assign({ href: opts.href }, opts.attrs || {}));
        var media = img
            ? '<img src="' + esc(img) + '" alt="' + esc(opts.alt || name) + '" loading="lazy"' + attrs({ "data-fallback": opts.fallback }) + ">"
            : '<span class="hud-art-placeholder">No art</span>';
        if (opts.chip) media += chip(opts.chip[0], opts.chip[1], true);
        var meta = "";
        if (opts.meta || opts.colors != null) {
            meta = '<span class="hud-art-meta">' + (opts.meta ? "<span>" + esc(opts.meta) + "</span>" : "") +
                (opts.colors != null ? pips(opts.colors, "sm") : "") + "</span>";
        }
        return "<" + tag + ' class="hud-art' + (opts.kind === "card" ? " hud-art--card" : "") + '"' + extra + ">" +
            '<div class="hud-art-media">' + media + "</div>" +
            "<" + cap + ' class="hud-art-caption"><span class="hud-art-name">' + esc(name) + "</span>" + meta + "</" + cap + ">" +
            "</" + tag + ">";
    }

    // Commander art group (mirrors the `commanders` macro). roles:
    // deck.commander_roles from /api/decks (commander_roles.py). Older payloads
    // without roles fall back to plain commanders from names/uids.
    var CMDR_ROLE_LABELS = { partner: "Partner", doctor: "Doctor", doctors_companion: "Doctor's companion",
                             companion: "Companion", commander: "Commander" };
    function commanderRoles(deck) {
        if (deck && deck.commander_roles) return deck.commander_roles;
        var names = (deck && deck.commanders) || [], uids = ((deck && deck.commander_uids) || []).filter(Boolean);
        var n = names.length || Math.min(uids.length, 2), out = [];
        for (var i = 0; i < n; i++) out.push({ name: names[i] || "", uid: uids[i] || null, role: n > 1 ? "partner" : "commander" });
        return out;
    }
    function commanders(roles, cls) {
        roles = roles || [];
        var mains = roles.filter(function (c) { return c.role !== "companion"; }).length;
        var items = roles.map(function (c) {
            var label = CMDR_ROLE_LABELS[c.role] || "Commander";
            var title = c.name + " · " + label + (c.can_be_commander ? " (" + (c.type_label || "card") + " that can be your commander)" : "");
            var img = "";
            if (c.uid) {
                var s = scryfall(c.uid, "art_crop");
                img = '<img alt="' + esc(c.name) + '" src="' + esc(s.src) + '" loading="lazy" data-fallback="' + esc(s.fallback) + '">';
            } else if (c.name) {
                img = '<img alt="' + esc(c.name) + '" loading="lazy" src="https://api.scryfall.com/cards/named?exact=' +
                    encodeURIComponent(c.name) + '&amp;format=image&amp;version=art_crop">';
            }
            return '<figure class="hud-cmdr hud-cmdr--' + esc(String(c.role || "commander").replace("_", "-")) + (c.can_be_commander ? " is-cbc" : "") +
                '" title="' + esc(title) + '">' +
                (c.role === "companion" ? '<span class="hud-cmdr-tag">Companion</span>' : "") +
                '<span class="hud-cmdr-art">' + img +
                (c.can_be_commander ? '<span class="hud-cmdr-badge">' + esc(c.type_label || "Commander") + "</span>" : "") +
                "</span></figure>";
        });
        if (!items.length) items.push('<figure class="hud-cmdr hud-cmdr--commander"><span class="hud-cmdr-art"></span></figure>');
        return '<div class="hud-cmdrs' + (mains > 1 ? " hud-cmdrs--multi" : "") + (roles.length > 1 ? " hud-cmdrs--many" : "") + (cls ? " " + esc(cls) : "") + '">' + items.join("") + "</div>";
    }

    // Scryfall image for a card (printing) id. Uses the image CDN, which is not
    // rate-limited like the api.scryfall.com ?format=image redirect (that one
    // starts returning 429 when a 100-card deck loads at once). version:
    // "normal" | "large" | "small" | "art_crop" | "png". Use `.fallback` as the
    // art() fallback so a missing CDN file still resolves through the API.
    function scryfall(uid, version) {
        var v = version || "normal";
        if (!uid) return { src: "", fallback: "" };
        var id = String(uid).toLowerCase();
        var ext = v === "png" ? ".png" : ".jpg";
        return {
            src: "https://cards.scryfall.io/" + v + "/front/" + id.charAt(0) + "/" + id.charAt(1) + "/" + encodeURIComponent(id) + ext,
            fallback: "https://api.scryfall.com/cards/" + encodeURIComponent(id) + "?format=image&version=" + v
        };
    }

    // One retry per <img data-fallback>: swap to the fallback URL on error.
    document.addEventListener("error", function (ev) {
        var img = ev.target;
        if (!img || img.tagName !== "IMG") return;
        var fb = img.getAttribute("data-fallback");
        if (fb && img.src !== fb) { img.removeAttribute("data-fallback"); img.src = fb; }
    }, true);

    // --- Modal -------------------------------------------------------------
    // <div class="hud-modal" id="x"><div class="hud-panel hud-modal-dialog">…</div></div>
    // Click on the scrim, any [data-hud-close], or Escape closes it.
    function openModal(el) {
        el = typeof el === "string" ? document.getElementById(el) : el;
        if (!el) return;
        el.classList.add("is-open");
        el.setAttribute("aria-hidden", "false");
    }
    function closeModal(el) {
        el = typeof el === "string" ? document.getElementById(el) : el;
        if (!el) return;
        el.classList.remove("is-open");
        el.setAttribute("aria-hidden", "true");
    }
    document.addEventListener("click", function (ev) {
        var modal = ev.target.closest && ev.target.closest(".hud-modal");
        if (!modal) return;
        if (ev.target === modal || ev.target.closest("[data-hud-close]")) closeModal(modal);
    });
    document.addEventListener("keydown", function (ev) {
        if (ev.key !== "Escape") return;
        // An open flyout takes Esc first and hands focus back to its trigger.
        if (openMenu) { ev.preventDefault(); closeMenu(true); return; }
        document.querySelectorAll(".hud-modal.is-open").forEach(closeModal);
    });

    // --- Menu / flyout (hud.menu) ---------------------------------------
    // Trigger: [data-hud-menu][aria-controls][aria-expanded]. The panel is
    // position:fixed beside the trigger, so the scrolling sidebar can't clip it.
    var openMenu = null; // { trigger, panel }

    function menuItems(panel) {
        return Array.prototype.slice.call(panel.querySelectorAll(".hud-menu-item"));
    }
    function placeMenu(trigger, panel) {
        var r = trigger.getBoundingClientRect();
        var gap = 4, edge = 8;
        var w = panel.offsetWidth, h = panel.offsetHeight;
        var vw = window.innerWidth, vh = window.innerHeight;
        var left = r.right + gap;
        if (left + w > vw - edge) left = Math.max(edge, vw - w - edge);
        var top = r.top;
        if (top + h > vh - edge) top = Math.max(edge, vh - h - edge);
        panel.style.left = left + "px";
        panel.style.top = top + "px";
    }
    function closeMenu(returnFocus) {
        if (!openMenu) return;
        var m = openMenu;
        openMenu = null;
        m.panel.hidden = true;
        m.trigger.setAttribute("aria-expanded", "false");
        if (returnFocus) m.trigger.focus();
    }
    function showMenu(trigger, focusIdx) {
        var panel = document.getElementById(trigger.getAttribute("aria-controls"));
        if (!panel) return;
        if (openMenu && openMenu.trigger !== trigger) closeMenu(false);
        panel.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        openMenu = { trigger: trigger, panel: panel };
        placeMenu(trigger, panel);
        if (focusIdx != null) {
            var items = menuItems(panel);
            var i = focusIdx < 0 ? items.length - 1 : focusIdx;
            if (items[i]) items[i].focus();
        }
    }
    function initMenus(root) {
        (root || document).querySelectorAll("[data-hud-menu]").forEach(function (trigger) {
            if (trigger.__hudMenu) return;
            trigger.__hudMenu = true;
            var panel = document.getElementById(trigger.getAttribute("aria-controls"));
            if (!panel) return;
            trigger.addEventListener("click", function (ev) {
                // Modified clicks keep the link's own behaviour (open /decks in a new tab).
                if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey || (ev.button && ev.button !== 0)) return;
                ev.preventDefault();
                if (openMenu && openMenu.trigger === trigger) closeMenu(false);
                // Keyboard activation (detail 0) moves focus into the menu.
                else showMenu(trigger, ev.detail === 0 ? 0 : null);
            });
            trigger.addEventListener("keydown", function (ev) {
                if (ev.key === " " || ev.key === "Enter" || ev.key === "ArrowDown") {
                    ev.preventDefault();
                    showMenu(trigger, 0);
                } else if (ev.key === "ArrowUp") {
                    ev.preventDefault();
                    showMenu(trigger, -1);
                }
            });
            panel.addEventListener("keydown", function (ev) {
                var items = menuItems(panel);
                var i = items.indexOf(document.activeElement);
                var next = null;
                if (ev.key === "ArrowDown") next = (i + 1) % items.length;
                else if (ev.key === "ArrowUp") next = (i - 1 + items.length) % items.length;
                else if (ev.key === "Home") next = 0;
                else if (ev.key === "End") next = items.length - 1;
                else if (ev.key === " ") { ev.preventDefault(); if (items[i]) items[i].click(); return; }
                if (next != null) { ev.preventDefault(); items[next].focus(); }
            });
            // Tabbing (or clicking) away from both the trigger and the panel closes it.
            [trigger, panel].forEach(function (el) {
                el.addEventListener("focusout", function (ev) {
                    var to = ev.relatedTarget;
                    if (openMenu && openMenu.trigger === trigger && to && !trigger.contains(to) && !panel.contains(to)) closeMenu(false);
                });
            });
        });
    }
    document.addEventListener("click", function (ev) {
        if (!openMenu) return;
        if (openMenu.trigger.contains(ev.target) || openMenu.panel.contains(ev.target)) return;
        closeMenu(false);
    });
    window.addEventListener && window.addEventListener("resize", function () {
        if (openMenu) placeMenu(openMenu.trigger, openMenu.panel);
    });
    // Keep it beside the trigger if the sidebar (or page) scrolls underneath.
    document.addEventListener("scroll", function () {
        if (openMenu) placeMenu(openMenu.trigger, openMenu.panel);
    }, true);

    // --- Sortable tables ---------------------------------------------------
    function cellKey(row, idx, kind) {
        var cell = row.cells[idx];
        if (!cell) return kind === "num" ? -Infinity : "";
        var raw = cell.hasAttribute("data-value") ? cell.getAttribute("data-value") : cell.textContent;
        if (kind === "num") {
            var n = parseFloat(String(raw).replace(/[^0-9.+-]/g, ""));
            return isNaN(n) ? -Infinity : n;
        }
        return String(raw).trim().toLowerCase();
    }

    function sortTable(table, th) {
        var headers = Array.prototype.slice.call(th.parentNode.children);
        var idx = headers.indexOf(th);
        var kind = th.getAttribute("data-sort") || "text";
        var current = th.getAttribute("aria-sort");
        // First click on a numeric column sorts high-to-low: that's what you want for stats.
        var dir = current === "descending" ? "ascending"
                : current === "ascending" ? "descending"
                : (kind === "num" ? "descending" : "ascending");
        headers.forEach(function (h) { if (h.hasAttribute("data-sort")) h.setAttribute("aria-sort", "none"); });
        th.setAttribute("aria-sort", dir);

        var body = table.tBodies[0];
        if (!body) return;
        var rows = Array.prototype.slice.call(body.rows);
        var sign = dir === "ascending" ? 1 : -1;
        rows.map(function (r, i) { return { r: r, k: cellKey(r, idx, kind), i: i }; })
            .sort(function (a, b) {
                if (a.k < b.k) return -sign;
                if (a.k > b.k) return sign;
                return a.i - b.i;  // stable
            })
            .forEach(function (x) { body.appendChild(x.r); });
    }

    function initSortable(root) {
        (root || document).querySelectorAll("table[data-hud-sortable]").forEach(function (table) {
            if (table.__hudSortable) return;
            table.__hudSortable = true;
            table.addEventListener("click", function (ev) {
                var btn = ev.target.closest(".hud-sort");
                if (!btn || !table.contains(btn)) return;
                var th = btn.closest("th[data-sort]");
                if (th) sortTable(table, th);
            });
        });
    }

    // --- Interaction graph (UX_SPEC §3.1.1-3.1.4) ---------------------------
    // HUD.graph(svg, {nodes, edges, engines, commander, commanders, onSelect})
    //   nodes   [{id, degree}]                 id = card name, degree = distinct neighbours
    //   edges   [{source, target, reasons[]}]   directed: source's output feeds target's input
    //   engines [{id, cards[]}]
    // returns {focusEngine(id|null), select(id|null), search(q) -> id|null, nodeEl(id)}.
    // Needs window.d3 (static/vendor/d3-force-3.0.0.min.js, loaded on /stats only).
    // Laid out once, synchronously (300 ticks), then frozen: no live physics.
    var SVGNS = "http://www.w3.org/2000/svg";
    function svgEl(tag, attrs) { var e = document.createElementNS(SVGNS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
    function cssNum(name, fallback) { var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name)); return isNaN(v) ? fallback : v; }

    function graph(svg, opt) {
        var noop = { focusEngine: function () {}, select: function () {}, search: function () { return null; }, nodeEl: function () { return null; } };
        if (!window.d3 || !window.d3.forceSimulation || !opt.nodes.length) return noop;
        var d3 = window.d3;
        var W = svg.clientWidth || 800, H = svg.clientHeight || 560;
        svg.setAttribute("viewBox", "0 0 " + W + " " + H);
        var cmds = {}; (opt.commanders || [opt.commander]).forEach(function (c) { if (c) cmds[c] = 1; });
        var rMin = cssNum("--graph-node-min", 5), rMax = cssNum("--graph-node-max", 18);
        var maxDeg = Math.max.apply(null, opt.nodes.map(function (n) { return n.degree; }).concat([1]));
        var radius = function (n) { return cmds[n.id] ? rMax + 4 : rMin + (rMax - rMin) * Math.sqrt(n.degree / maxDeg); };

        var nodes = opt.nodes.map(function (n) { return Object.assign({}, n); });
        var byId = {}; nodes.forEach(function (n) { byId[n.id] = n; });
        var links = opt.edges.filter(function (e) { return byId[e.source] && byId[e.target]; })
            .map(function (e) { return { source: e.source, target: e.target, reasons: e.reasons }; });
        var engCards = {}; (opt.engines || []).forEach(function (g) { engCards[g.id] = g.cards; });

        var pin = byId[opt.commander || Object.keys(cmds)[0]]; if (pin) { pin.fx = W / 2; pin.fy = H / 2; }
        var sim = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(function (l) { return 70 + 18 / l.reasons.length; }).strength(0.35))
            .force("charge", d3.forceManyBody().strength(-340))
            .force("collide", d3.forceCollide().radius(function (d) { return radius(d) + (nodes.length > 90 ? 34 : 26); }).iterations(2))
            .force("x", d3.forceX(W / 2).strength(0.06)).force("y", d3.forceY(H / 2).strength(0.09))
            .stop();
        for (var i = 0; i < 300; i++) sim.tick();
        // Fit the viewBox to the laid-out bounds (+ label room).
        var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        nodes.forEach(function (n) { var r = radius(n); x0 = Math.min(x0, n.x - r - 60); x1 = Math.max(x1, n.x + r + 60); y0 = Math.min(y0, n.y - r - 12); y1 = Math.max(y1, n.y + r + 22); });
        var full = [x0, y0, x1 - x0, y1 - y0];
        function setView(b) { svg.setAttribute("viewBox", b.map(function (v) { return v.toFixed(0); }).join(" ")); }
        setView(full);
        svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
        // Zoom-to-fit a node set (engine focus). It jumps: tweening the
        // viewBox would re-layout every text node each frame.
        function fit(set) {
            if (!set) { setView(full); return; }
            var a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
            nodes.forEach(function (n) { if (!set.has(n.id)) return; var r = radius(n); a = Math.min(a, n.x - r - 90); c = Math.max(c, n.x + r + 90); b = Math.min(b, n.y - r - 24); d = Math.max(d, n.y + r + 30); });
            if (a === Infinity) { setView(full); return; }
            var w = Math.max(c - a, full[2] * 0.45), h = Math.max(d - b, full[3] * 0.45), cx = (a + c) / 2, cy = (b + d) / 2;
            setView([cx - w / 2, cy - h / 2, w, h]);
        }

        while (svg.firstChild) svg.removeChild(svg.firstChild);
        var gE = svgEl("g", {}), gL = svgEl("g", {}), gN = svgEl("g", {});
        svg.appendChild(gE); svg.appendChild(gN); svg.appendChild(gL);
        var mid = "g-arrow-" + Math.random().toString(36).slice(2, 8);
        var defs = svgEl("defs", {}), mk = svgEl("marker", { id: mid, viewBox: "0 0 8 8", refX: "8", refY: "4", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
        var mp = svgEl("path", { d: "M0,0 L8,4 L0,8 z" }); mp.style.fill = "var(--signal)"; mk.appendChild(mp); defs.appendChild(mk); svg.insertBefore(defs, svg.firstChild);

        links.forEach(function (l) {
            var s = l.source, t = l.target, dx = t.x - s.x, dy = t.y - s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
            var rt = radius(t) + 3;
            l.line = svgEl("line", { x1: s.x, y1: s.y, x2: t.x - dx / d * rt, y2: t.y - dy / d * rt, class: "g-edge" });
            l.line.style.strokeWidth = String(0.75 + l.reasons.length * 0.75);
            gE.appendChild(l.line);
            l.label = svgEl("text", { x: (s.x + t.x) / 2, y: (s.y + t.y) / 2 - 3, "text-anchor": "middle", class: "g-elabel" });
            l.label.textContent = l.reasons.join(" · ");
            gL.appendChild(l.label);
        });

        var state = { engine: null, sel: null };
        var order = nodes.slice().sort(function (a, b) { return b.degree - a.degree; });
        order.forEach(function (n, idx) {
            // Outer <g> owns the SVG position; the inner <g> owns the CSS entrance
            // animation (a CSS transform on the outer <g> would replace translate()).
            var isCmd = !!cmds[n.id];
            var g = svgEl("g", { class: "g-node" + (isCmd ? " is-commander" : "") + (n.degree < 3 && !isCmd ? " is-label-off" : ""),
                transform: "translate(" + n.x.toFixed(1) + "," + n.y.toFixed(1) + ")", tabindex: "0", role: "button",
                "aria-label": n.id + ", " + n.degree + " connection" + (n.degree === 1 ? "" : "s") });
            var inner = svgEl("g", { class: "g-node-in is-entering" });
            inner.style.setProperty("--i", idx);
            inner.appendChild(svgEl("circle", { r: radius(n).toFixed(1) }));
            var tx = svgEl("text", { y: (radius(n) + 13).toFixed(1), "text-anchor": "middle" }); tx.textContent = n.id.split(",")[0];
            inner.appendChild(tx);
            g.appendChild(inner);
            g.addEventListener("mouseenter", function () { if (!state.engine && !state.sel) hover(n.id); });
            g.addEventListener("mouseleave", function () { if (!state.engine && !state.sel) clear(); });
            g.addEventListener("focus", function () { if (!state.engine && !state.sel) hover(n.id); });
            g.addEventListener("blur", function () { if (!state.engine && !state.sel) clear(); });
            g.addEventListener("click", function () { select(n.id); });
            g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n.id); } });
            n.g = g; gN.appendChild(g);
        });
        svg.addEventListener("click", function (e) { if (e.target === svg) select(null); });

        function paint(set, hot, showLabels) {
            svg.classList.toggle("is-focusing", !!set);
            nodes.forEach(function (n) { n.g.classList.toggle("is-near", !!set && set.has(n.id)); n.g.classList.toggle("is-engine", !!state.engine && !!set && set.has(n.id)); });
            // One label per (hub, reasons) group, with xN: hub spokes share reasons.
            var groups = {};
            links.forEach(function (l) {
                var on = !!set && hot(l);
                l.line.classList.toggle("is-near", on); l.line.classList.toggle("is-hot", on);
                if (on) l.line.setAttribute("marker-end", "url(#" + mid + ")"); else l.line.removeAttribute("marker-end");
                l.label.classList.remove("is-on");
                if (!on || !showLabels) return;
                var hub = byId[l.source.id].degree >= byId[l.target.id].degree ? l.source.id : l.target.id;
                var key = hub + "|" + l.reasons.join("·");
                (groups[key] = groups[key] || []).push(l);
            });
            Object.keys(groups).forEach(function (k) {
                var g = groups[k], l = g[Math.floor(g.length / 2)];
                l.label.textContent = l.reasons.join(" · ") + (g.length > 1 ? " ×" + g.length : "");
                l.label.classList.add("is-on");
            });
            declutter();
        }
        // Greedy: heaviest edge label first; drop any label that hits a visible
        // node or a placed label. The inspector lists every reason anyway.
        function declutter() {
            var kept = [], hit = function (a, b) { return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height; };
            var focusing = svg.classList.contains("is-focusing");
            nodes.forEach(function (n) { if (!focusing || n.g.classList.contains("is-near")) { var r = radius(n); kept.push({ x: n.x - r, y: n.y - r, width: 2 * r, height: 2 * r }); } });
            links.filter(function (l) { return l.label.classList.contains("is-on"); })
                .sort(function (a, b) { return b.reasons.length - a.reasons.length; })
                .forEach(function (l) {
                    var bb; try { bb = l.label.getBBox(); } catch (e) { return; }
                    var box = { x: bb.x - 2, y: bb.y - 1, width: bb.width + 4, height: bb.height + 2 };
                    if (kept.some(function (k) { return hit(box, k); })) l.label.classList.remove("is-on"); else kept.push(box);
                });
        }
        function neighbours(id) {
            var s = new Set([id]); links.forEach(function (l) { if (l.source.id === id) s.add(l.target.id); if (l.target.id === id) s.add(l.source.id); }); return s;
        }
        function hover(id) { paint(neighbours(id), function (l) { return l.source.id === id || l.target.id === id; }, true); }
        function clear() { paint(null); }
        function focusEngine(id) {
            state.engine = id || null; state.sel = null;
            nodes.forEach(function (n) { n.g.classList.remove("is-selected"); });
            if (!state.engine) { clear(); fit(null); return; }
            var set = new Set((engCards[id] || []).filter(function (c) { return byId[c]; }));
            paint(set, function (l) { return set.has(l.source.id) && set.has(l.target.id); }, true);
            fit(set);
        }
        function conns(id) {
            return links.filter(function (l) { return l.source.id === id || l.target.id === id; })
                .map(function (l) { return { out: l.source.id === id, other: l.source.id === id ? l.target.id : l.source.id, reasons: l.reasons }; });
        }
        function select(id, silent) {
            state.sel = id && byId[id] ? id : null;
            nodes.forEach(function (n) { n.g.classList.toggle("is-selected", n.id === state.sel); });
            if (!state.engine) { if (state.sel) hover(state.sel); else clear(); }
            if (opt.onSelect && !silent) opt.onSelect(id || null, id ? conns(id) : []);
        }
        function search(q) {
            q = (q || "").trim().toLowerCase();
            if (!q) { if (!state.engine && !state.sel) clear(); return null; }
            var hit = order.find(function (n) { return n.id.toLowerCase().indexOf(q) !== -1; });
            if (hit && !state.engine) hover(hit.id);
            return hit ? hit.id : null;
        }
        return { focusEngine: focusEngine, select: select, search: search, conns: conns,
                 nodeEl: function (id) { return byId[id] ? byId[id].g : null; } };
    }

    // Sticky doc index (hud.doc_index): mark the link of the section currently in view.
    function initDocIndex() {
        var nav = document.querySelector ? document.querySelector("[data-doc-index]") : null;
        if (!nav || !window.requestAnimationFrame) return;
        var links = Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]'));
        var heads = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
        var mark = function (i) {
            links.forEach(function (a, k) { if (k === i) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current"); });
            var a = links[i];
            if (a && nav.scrollWidth > nav.clientWidth) {
                var over = a.getBoundingClientRect().right - nav.getBoundingClientRect().right;
                var under = a.getBoundingClientRect().left - nav.getBoundingClientRect().left;
                if (over > 0) nav.scrollLeft += over; else if (under < 0) nav.scrollLeft += under;
            }
        };
        // The last head above the top third of the viewport is current; at the end of the page
        // the last section wins (a short final section's head may never reach that line).
        var queued = false;
        var update = function () {
            queued = false;
            var cur = 0, line = window.innerHeight / 3;
            heads.forEach(function (h, i) { if (h && h.getBoundingClientRect().top <= line) cur = i; });
            var doc = document.documentElement;
            if (window.innerHeight + window.scrollY >= doc.scrollHeight - 2) cur = heads.length - 1;
            mark(cur);
        };
        var schedule = function () { if (!queued) { queued = true; window.requestAnimationFrame(update); } };
        window.addEventListener("scroll", schedule, { passive: true });
        window.addEventListener("resize", schedule);
        update();
    }

    // --- Deck status control -------------------------------------------
    // Physical decks are locked (their status comes from the Archidekt
    // Commander Decks folder): a disabled select plus a note. Every other
    // deck gets Virtual/WIP/Retired, mapped to digital/testing/retired.
    var DECK_STATUS_OPTIONS = [["digital", "Virtual"], ["testing", "WIP"], ["retired", "Retired"]];
    var DECK_LOCK_NOTE = "Physical: managed by your Archidekt Commander Decks folder";
    function deckStatusControl(deck, opts) {
        opts = opts || {};
        var id = opts.id || "deckStatusSelect";
        var noteId = id + "Note";
        var locked = Boolean(deck && (deck.status_locked || deck.status === "physical"));
        var status = (deck && deck.status) || "digital";
        var options = locked
            ? '<option value="physical" selected>Physical</option>'
            : DECK_STATUS_OPTIONS.map(function (o) {
                return '<option value="' + o[0] + '"' + (o[0] === status ? " selected" : "") + ">" + esc(o[1]) + "</option>";
            }).join("");
        return '<div class="hud-field hud-status-field' + (locked ? " is-locked" : "") + '">' +
            '<label class="hud-field-label" for="' + esc(id) + '">Status</label>' +
            '<select id="' + esc(id) + '" data-deck-status' + (locked ? ' disabled aria-describedby="' + esc(noteId) + '"' : "") + ">" +
            options + "</select>" +
            (locked ? '<span class="hud-field-hint" id="' + esc(noteId) + '">' + esc(DECK_LOCK_NOTE) + "</span>" : "") +
            '<span class="hud-field-hint hud-status-msg" role="status" aria-live="polite"></span>' +
            "</div>";
    }

    // Wire a deckStatusControl inside `root`: change -> POST
    // /api/decks/{id}/status. On a rejection the select goes back to the
    // saved value and the server's message is shown.
    function bindDeckStatus(root, deck, onSaved) {
        var sel = root && root.querySelector("[data-deck-status]");
        if (!sel || sel.disabled) return;
        var msg = root.querySelector(".hud-status-msg");
        sel.addEventListener("change", function () {
            var prev = deck.status, next = sel.value;
            if (msg) msg.textContent = "Saving…";
            fetch("/api/decks/" + encodeURIComponent(deck.registry_id || deck.id) + "/status", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status: next })
            }).then(function (res) {
                return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
            }).then(function (out) {
                if (out.ok && out.data.success) {
                    deck.status = next;
                    if (msg) msg.textContent = "Saved";
                    if (onSaved) onSaved(deck);
                } else {
                    sel.value = prev;
                    if (msg) msg.textContent = (out.data && out.data.error) || "Could not change status";
                }
            }).catch(function () {
                sel.value = prev;
                if (msg) msg.textContent = "Could not change status";
            });
        });
    }

    window.HUD = {
        DECK_STATUS_OPTIONS: DECK_STATUS_OPTIONS,
        DECK_LOCK_NOTE: DECK_LOCK_NOTE,
        deckStatusControl: deckStatusControl,
        bindDeckStatus: bindDeckStatus,
        STATUS_TONES: STATUS_TONES,
        esc: esc,
        pips: pips,
        chip: chip,
        statusChip: statusChip,
        stat: stat,
        stack: stack,
        barChart: barChart,
        rankList: rankList,
        art: art,
        commanders: commanders,
        commanderRoles: commanderRoles,
        scryfall: scryfall,
        graph: graph,
        openModal: openModal,
        closeModal: closeModal,
        sortTable: sortTable,
        initSortable: initSortable,
        initDocIndex: initDocIndex,
        initMenus: initMenus,
        openMenu: function (trigger) { showMenu(typeof trigger === "string" ? document.getElementById(trigger) : trigger, null); },
        closeMenu: closeMenu
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { initSortable(); initDocIndex(); initMenus(); });
    } else {
        initSortable();
        initDocIndex();
        initMenus();
    }
})();
