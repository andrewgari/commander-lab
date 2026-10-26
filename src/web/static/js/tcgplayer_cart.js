/* ==========================================================================
   Commander Lab: "Buy on TCGplayer" cart button (client-side renderer).

   The ONE reusable cart button. Two renderers, one markup:
     - Jinja: {{ hud.tcg_cart(cards, size="sm") }}  (templates/partials/hud.html,
       backed by tcgplayer.cart() in tcgplayer.py)
     - JS:    TcgCart.button(cards, {size: "sm"})   (this file, for surfaces
       rendered from fetched JSON)
   tests/test_tcgplayer_cart_button.py asserts both produce identical HTML
   and identical URLs, so keep them in sync.

   cards: [{name, qty}]  qty missing/null/"" -> 1 (what the lists display);
   qty <= 0 or blank name -> dropped. Duplicates merge; "Front // Back"
   double-faced names submit the front face (TCGplayer indexes that).

   Browser: window.TcgCart. Node: require("./tcgplayer_cart.js"). No DOM
   access except TcgCart.mount().

   TcgCart.items(cards)          -> normalized [{name, qty}]
   TcgCart.count(cards)          -> total copies
   TcgCart.url(cards)            -> Mass Entry URL, "" when empty
   TcgCart.button(cards, opts?)  -> <a> HTML string, "" when empty
       opts.size  "sm" for single-card rows
       opts.label visible text (default "Buy on TCGplayer")
   TcgCart.mount(el, cards, opts?) -> renders into el, hides el when empty,
       returns the <a> (or null)
   ========================================================================== */
(function (root, factory) {
    "use strict";
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.TcgCart = factory();
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var BASE_URL = "https://www.tcgplayer.com/massentry";
    var LINE_SEPARATOR = "||";
    var LABEL = "Buy on TCGplayer";
    // Square-cornered cart, 16-unit grid, stroked in currentColor.
    var ICON = '<svg class="hud-btn-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">' +
        '<path d="M1 2h2l1.5 8h8l1.5-6H4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square"/>' +
        '<rect x="4.5" y="12" width="2" height="2" fill="currentColor"/><rect x="11" y="12" width="2" height="2" fill="currentColor"/></svg>';
    // "Leaves the app" arrow, same stroke language as the cart.
    var EXT = '<svg viewBox="0 0 16 16" width="10" height="10" focusable="false">' +
        '<path d="M4 12L12 4M6 4h6v6" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="square"/></svg>';

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&#34;", "'": "&#39;" }[c];
        });
    }

    // Python's urllib.parse.quote(s, safe=""): encodeURIComponent plus !'()*.
    function quote(s) {
        return encodeURIComponent(s).replace(/[!'()*]/g, function (c) {
            return "%" + c.charCodeAt(0).toString(16).toUpperCase();
        });
    }

    // Mirrors tcgplayer._qty().
    function qtyOf(raw) {
        if (raw === undefined || raw === null || raw === "") return 1;
        var n = Math.trunc(Number(raw));
        return isFinite(n) ? n : 0;
    }

    function frontFace(name) {
        var i = name.indexOf("//");
        return (i === -1 ? name : name.slice(0, i)).trim();
    }

    // Mirrors tcgplayer._set_code(): a present set code pins the printing
    // (`1 Lightning Bolt [SLD]`); absent means any printing is acceptable.
    function setCode(raw) {
        return String(raw == null ? "" : raw).trim().toUpperCase();
    }

    function items(cards) {
        var order = [], merged = {};
        (Array.isArray(cards) ? cards : []).forEach(function (card) {
            if (!card || typeof card !== "object") return;
            var name = String(card.name || "").trim();
            if (!name) return;
            var qty = qtyOf(card.qty);
            if (qty <= 0) return;
            var front = frontFace(name), set = setCode(card.set);
            var key = front + "\u0001" + set;
            if (!Object.prototype.hasOwnProperty.call(merged, key)) {
                var it = { name: front, qty: 0 };
                if (set) it.set = set;
                merged[key] = it; order.push(it);
            }
            merged[key].qty += qty;
        });
        return order;
    }

    function total(list) {
        return list.reduce(function (n, it) { return n + it.qty; }, 0);
    }

    function count(cards) { return total(items(cards)); }

    function urlFor(list) {
        if (!list.length) return "";
        var c = list.map(function (it) { return it.qty + " " + it.name + (it.set ? " [" + it.set + "]" : ""); }).join(LINE_SEPARATOR);
        return BASE_URL + "?c=" + quote(c) + "&productline=Magic";
    }

    function url(cards) { return urlFor(items(cards)); }

    // Python json.dumps (what Jinja's |tojson uses) with Jinja's HTML-safe escapes.
    function tojson(list) {
        return "[" + list.map(function (it) {
            // ensure_ascii: escape everything above U+007F (DEL itself stays raw).
            return '{"name": ' + JSON.stringify(it.name).replace(/[\u0080-\uffff]/g, function (c) {
                return "\\u" + ("000" + c.charCodeAt(0).toString(16)).slice(-4);
            }) + ', "qty": ' + it.qty + (it.set ? ', "set": ' + JSON.stringify(it.set) : "") + "}";
        }).join(", ") + "]";
    }

    function attrJson(list) {
        return tojson(list).replace(/[<>&']/g, function (c) {
            return { "<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "'": "\\u0027" }[c];
        });
    }

    function button(cards, opts) {
        opts = opts || {};
        var list = items(cards);
        if (!list.length) return "";
        var n = total(list);
        var cls = "hud-btn hud-btn--buy" + (opts.size ? " hud-btn--" + esc(opts.size) : "");
        return '<a class="' + cls + '" href="' + esc(urlFor(list)) + '" target="_blank" rel="noopener noreferrer"' +
            " data-tcg-cart='" + attrJson(list) + "' data-tcg-count=\"" + n + '">' +
            ICON +
            '<span class="hud-btn-text">' + esc(opts.label || LABEL) + "</span>" +
            (n > 1 ? '<span class="hud-btn-count" aria-hidden="true">' + n + "</span>" : "") +
            '<span class="hud-sr"> (' + n + " card" + (n === 1 ? "" : "s") + ", opens in a new tab)</span>" +
            '<span class="hud-btn-ext" aria-hidden="true">' + EXT + "</span>" +
            "</a>";
    }

    function mount(el, cards, opts) {
        if (!el) return null;
        var html = button(cards, opts);
        el.innerHTML = html;
        el.hidden = !html;
        return html ? el.querySelector("a") : null;
    }

    return {
        BASE_URL: BASE_URL,
        items: items,
        count: count,
        url: url,
        button: button,
        mount: mount
    };
});
