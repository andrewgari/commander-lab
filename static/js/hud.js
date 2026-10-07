/* ==========================================================================
   Commander Lab — HUD component kit, client side (window.HUD).
   Loaded from base.html on every page.

   - Sortable tables: any <table data-hud-sortable> whose <th> has
     data-sort="num|text" sorts on header click; aria-sort is kept in sync.
     A <td data-value="..."> overrides the cell text as the sort key.
   - String builders that mirror the Jinja macros in templates/partials/hud.html
     for pages that render from fetched JSON: HUD.pips(), HUD.chip(),
     HUD.statusChip(). Output is escaped.
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
        sortTable: sortTable,
        initSortable: initSortable
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { initSortable(); });
    } else {
        initSortable();
    }
})();
