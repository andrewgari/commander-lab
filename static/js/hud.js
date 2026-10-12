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
    // opts: hl (bar index drawn in signal), height, ticks, legend [series, ref, hl], title.
    function barChart(bars, opts) {
        opts = opts || {};
        var ticks = opts.ticks || 4, height = opts.height || 160, n = bars.length || 1;
        var peak = Math.max.apply(null, bars.map(function (b) { return b.value || 0; }).concat([1]));
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
        var aria = (opts.title || "Bar chart") + ": " + bars.map(function (b) { return b.label + " " + b.value; }).join(", ");
        var legend = "";
        if (opts.legend) {
            legend = '<figcaption class="hud-chart-legend"><span><span class="hud-swatch"></span>' + esc(opts.legend[0]) + "</span>" +
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
        document.querySelectorAll(".hud-modal.is-open").forEach(closeModal);
    });

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

    window.HUD = {
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
        scryfall: scryfall,
        openModal: openModal,
        closeModal: closeModal,
        sortTable: sortTable,
        initSortable: initSortable
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { initSortable(); });
    } else {
        initSortable();
    }
})();
