/* ==========================================================================
   Commander Lab: card hover preview (CardHoverPreview).

   Shows the Scryfall image of the exact printing in a popover when a card
   name or thumbnail is hovered, focused (Tab) or long-pressed / tapped.
   Spec: docs/design/hover-preview/SPEC.md (HP-D). Styles:
   static/css/card_hover_preview.css. URL resolution: ScryfallImage
   (static/js/scryfall_image.js), which must be loaded first in the browser.

   The app is server-rendered Jinja + vanilla JS that re-renders lists with
   innerHTML, so the "component" is a marker attribute plus ONE delegated
   controller and ONE shared popover element: no per-trigger listeners, and
   sweeping across a list re-targets the same popover instead of flickering.

   ---- Marking a trigger ----------------------------------------------------

   Any element becomes a trigger with data-card-preview plus a card ref:

     <span data-card-preview tabindex="0"
           data-cp-id="<scryfall uuid>"        preferred: exact printing
           data-cp-set="cmm" data-cp-cn="410"  else: exact printing
           data-cp-name="Sol Ring"             last resort (NOT printing exact),
                                               also alt text; DFC: "Front // Back"
           data-cp-layout="transform"          optional Scryfall layout
           data-cp-faces="2">                  optional: card has two images
       Sol Ring
     </span>

   From JS (the equivalent of <CardHoverPreview cardRef={...}>{children}):

     CardHoverPreview.wrap(childrenHtml, cardRef, {name: true})
         -> '<span class="hud-cp-trigger hud-cp-name" tabindex="0" data-card-preview ...>children</span>'
     CardHoverPreview.attrs(cardRef)        -> ' data-card-preview data-cp-id="..." ...'
                                               (for an existing <a>/<img> tag)
     CardHoverPreview.bind(el, cardRef)     -> marks el and attaches a full ref
                                               object (e.g. a Scryfall card with
                                               image_uris / card_faces), which
                                               wins over data-cp-* attributes.

   cardRef is anything ScryfallImage.resolve() accepts: {scryfallId|uid,
   set, collectorNumber, name, imageUris, cardFaces, layout} (snake_case ok).

   ---- Behaviour (SPEC section in brackets) --------------------------------

   Timing [1]: mouse cold open 300 ms; warm open 60 ms while a preview is open
     or closed < 250 ms ago, so moving to the next row swaps the open popover
     in place (no close/reopen, no entrance animation); close 120 ms after
     leaving; keyboard focus (:focus-visible) 400 ms.
   Placement [2]: anchored to the element (the hovered line box for wrapped
     inline names), 12 px gap, 8 px viewport margin, right > left > scaled
     right/left > below/above. z-index 1050.
   Size [3]: 420 px single face, 488:680 ratio; Scryfall "large" when
     devicePixelRatio > 1.5, else "normal" (ScryfallImage.versionForDpr).
   States [4]: card-shaped skeleton immediately; image aborted after 8 s;
     on failure the skeleton stays and the 28 px caption plate reads
     "Failed to load image"; name-only refs show a "Printing unknown" chip
     (ScryfallImage also console.warns once per name).
   DFC [5]: both faces side by side, 360 px each + 8 px gap, scaled down to a
     280 px floor; below that, one face with a Flip button (the popover turns
     interactive so the button can be reached; click the art to flip too).
   Touch [6]: long-press 450 ms on actionable triggers (inside a link, button,
     [role=button], [onclick], [data-action]); tap on inert text; > 10 px of
     travel cancels. Touch opens a centered sheet (max 360 px) over a scrim;
     tapping the scrim or Close dismisses it.
   Keyboard/a11y [7]: role="tooltip" popover, never focused, never traps
     focus; the trigger gets aria-describedby=<popover id> while open; Esc
     closes immediately and is swallowed (a modal under it stays open; a
     second Esc reaches the modal); focus stays on / returns to the trigger.
     <img alt> is the card or face name.
   Cache: ScryfallImage.cache holds resolved URLs. Images are preloaded while
     the open delay runs and their load state is remembered per URL (failures
     for 60 s), so re-hovering never re-requests. With `lookup` on (default),
     refs that only resolve to an api.scryfall.com redirect are looked up once
     through ScryfallImage.resolveRemote (throttled <= 10/s, cached) to get the
     cards.scryfall.io URLs and DFC faces. Their images wait for that lookup
     (skeleton meanwhile), so a card costs one API request, not an image
     redirect plus a JSON call; a failed lookup falls back to the API image.
     Refs with image_uris / card_faces (CardHoverPreview.bind) skip the lookup.

   ---- API ------------------------------------------------------------------

     CardHoverPreview.init(options?) -> controller   browser singleton; called
         automatically on load unless window.CardHoverPreviewManual is truthy.
     CardHoverPreview.create(options?) -> controller  standalone (tests); pass
         {env: {document, window, setTimeout, clearTimeout, now, loadImage,
         fetch, resolver}} to inject the DOM, clock, image loader and resolver.
     controller.listen() / destroy() / open(el, how?) / close() / isOpen()
         / current() / element() / modelFor(el) / on(event, fn) / config
     Events: "intent", "open" ({how, warm}), "close", "render", "lookup".
     CardHoverPreview.defaults   timing / size constants (override per create)
   ========================================================================== */
(function (root, factory) {
    "use strict";
    if (typeof module === "object" && module.exports) {
        module.exports = factory(root, function () { return require("./scryfall_image.js"); });
    } else {
        root.CardHoverPreview = factory(root, function () { return root.ScryfallImage; });
    }
})(typeof self !== "undefined" ? self : this, function (root, defaultResolver) {
    "use strict";

    var DEFAULTS = {
        openCold: 300,       // ms  mouse open, nothing shown recently
        openWarm: 60,        // ms  mouse open while open / closed < warmWindow ago
        warmWindow: 250,     // ms
        close: 120,          // ms  grace after the pointer leaves
        focusOpen: 400,      // ms  keyboard (:focus-visible) open
        press: 450,          // ms  touch long-press on actionable triggers
        pressSlop: 10,       // px  finger travel that cancels the press
        imgTimeout: 8000,    // ms  abort an image that never arrives
        negTtl: 60000,       // ms  remember a failed URL
        outMs: 100,          // ms  exit fade before the element is hidden
        watchMs: 500,        // ms  re-render safety net (trigger removed)
        gap: 12, margin: 8, capH: 28, capGap: 4, faceGap: 8,
        width: 420, widthDfc: 360, minFaceWidth: 280, sheetWidth: 360, sheetPad: 16,
        ratio: 488 / 680,
        lookup: true,        // resolveRemote() for api-only refs (CDN URLs + DFC faces)
        idPrefix: "hud-cp"
    };

    // Layouts whose two faces are separate images (others are one image).
    var TWO_IMAGE_LAYOUTS = { transform: 1, modal_dfc: 1, reversible_card: 1, double_faced_token: 1 };
    var CDN_RE = /^https:\/\/cards\.scryfall\.io\//;

    var refs = typeof WeakMap !== "undefined" ? new WeakMap() : null;   // el -> bound cardRef
    var instanceSeq = 0;

    // ---- small helpers (no DOM queries: the controller runs on a fake DOM in tests)

    function assign(target) {
        for (var i = 1; i < arguments.length; i++) {
            var s = arguments[i];
            if (s) for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k)) target[k] = s[k];
        }
        return target;
    }
    function str(v) { return v === undefined || v === null ? "" : String(v).trim(); }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function attr(el, k) { return el && el.getAttribute ? el.getAttribute(k) : null; }

    /** Nearest ancestor-or-self carrying data-card-preview. */
    function findTrigger(node) {
        while (node) {
            if (node.hasAttribute && (node.hasAttribute("data-card-preview") || node.hasAttribute("data-card-ref"))) return node;
            node = node.parentNode;
        }
        return null;
    }

    /** Does a tap on `node` already do something (so preview needs a long-press)? */
    function isActionable(node) {
        while (node) {
            if (node.getAttribute) {
                var tag = String(node.tagName || "").toUpperCase();
                if ((tag === "A" && node.hasAttribute("href")) || tag === "BUTTON" || tag === "SUMMARY" ||
                    node.getAttribute("role") === "button" || node.hasAttribute("onclick") ||
                    node.hasAttribute("data-action")) return true;
            }
            node = node.parentNode;
        }
        return false;
    }

    function splitNames(name) {
        var s = str(name);
        return s ? s.split(/\s+\/\/\s+/) : [];
    }

    /** cardRef from data-cp-* attributes. */
    function refFromAttrs(el) {
        // App-wide markup (card_refs.html_attrs / HUD.cardRefAttrs): data-card-ref
        // holds the CardRef as compact JSON. data-cp-* attributes win when both exist.
        var json = attr(el, "data-card-ref");
        if (json && !el.hasAttribute("data-cp-id") && !el.hasAttribute("data-cp-name")) {
            try {
                var parsed = JSON.parse(json);
                if (parsed && typeof parsed === "object") {
                    var nm = splitNames(parsed.name);
                    if (nm.length > 1 && !parsed.cardFaces && TWO_IMAGE_LAYOUTS[str(parsed.layout)]) {
                        parsed.cardFaces = nm.map(function (n) { return { name: n }; });
                        parsed.twoFaces = true;
                    }
                    if (!parsed.name) parsed.name = str(attr(el, "data-card-name"));
                    return parsed;
                }
            } catch (e) { /* fall through to data-cp-* */ }
        }
        var names = splitNames(attr(el, "data-cp-name"));
        var layout = str(attr(el, "data-cp-layout"));
        var faces = parseInt(attr(el, "data-cp-faces") || "0", 10) || 0;
        var ref = {
            scryfallId: str(attr(el, "data-cp-id")),
            set: str(attr(el, "data-cp-set")),
            collectorNumber: str(attr(el, "data-cp-cn")),
            name: str(attr(el, "data-cp-name")),
            layout: layout
        };
        if (faces >= 2 || (TWO_IMAGE_LAYOUTS[layout] && names.length > 1)) {
            // Let the resolver build one URL per face (it does so for two-image layouts).
            ref.layout = TWO_IMAGE_LAYOUTS[layout] ? layout : "transform";
            ref.cardFaces = [{ name: names[0] || ref.name }, { name: names[1] || names[0] || ref.name }];
            ref.twoFaces = true;
        }
        return ref;
    }

    function refFor(el) {
        var bound = refs && refs.get(el);
        return bound || refFromAttrs(el);
    }

    function faceNamesOf(ref) {
        var cf = ref.cardFaces || ref.card_faces;
        if (Array.isArray(cf) && cf.length > 1) return cf.map(function (f) { return str(f && f.name); });
        return splitNames(ref.name);
    }

    function wantsTwoFaces(ref) {
        if (ref.twoFaces) return true;
        var cf = ref.cardFaces || ref.card_faces;
        return !!(Array.isArray(cf) && cf.length > 1 && TWO_IMAGE_LAYOUTS[str(ref.layout)]);
    }

    // ---- markup helpers for template-string renderers ------------------------

    /** data-* attribute string for a cardRef (leading space; values escaped). */
    function attrs(cardRef) {
        var r = cardRef || {};
        var id = str(r.scryfallId || r.scryfall_id || r.uid || r.id);
        var set = str(r.set || r.set_code).toLowerCase();
        var cn = str(r.collectorNumber != null ? r.collectorNumber : r.collector_number);
        var name = str(r.name);
        var layout = str(r.layout);
        var out = " data-card-preview";
        if (id) out += ' data-cp-id="' + esc(id) + '"';
        if (set && cn) out += ' data-cp-set="' + esc(set) + '" data-cp-cn="' + esc(cn) + '"';
        if (name) out += ' data-cp-name="' + esc(name) + '"';
        if (layout) out += ' data-cp-layout="' + esc(layout) + '"';
        if (wantsTwoFaces(r) || r.faces === 2) out += ' data-cp-faces="2"';
        return out;
    }

    /**
     * Wrap trusted child markup in a preview trigger.
     * opts: {tag="span", className, name (bool: dotted-underline text style),
     *        focusable (default true: tabindex="0" so Tab reaches plain text;
     *        pass false when the child is already a link/button)}
     */
    function wrap(childrenHtml, cardRef, opts) {
        opts = opts || {};
        var tag = /^[a-z][a-z0-9-]*$/i.test(opts.tag || "") ? opts.tag : "span";
        var cls = "hud-cp-trigger" + (opts.name ? " hud-cp-name" : "") + (opts.className ? " " + opts.className : "");
        return "<" + tag + ' class="' + esc(cls) + '"' + (opts.focusable === false ? "" : ' tabindex="0"') +
            attrs(cardRef) + ">" + (childrenHtml == null ? "" : childrenHtml) + "</" + tag + ">";
    }

    /** Mark an element as a trigger and attach a full card ref (wins over data-cp-*). */
    function bind(el, cardRef) {
        if (!el) return el;
        if (el.setAttribute) el.setAttribute("data-card-preview", "");
        if (refs) refs.set(el, cardRef || {});
        return el;
    }
    function unbind(el) { if (refs && el) refs.delete(el); return el; }

    // ---- controller -----------------------------------------------------------

    function create(options) {
        options = options || {};
        var env = options.env || {};
        var cfg = assign({}, DEFAULTS, options);
        delete cfg.env;
        var win = env.window || (typeof window !== "undefined" ? window : {});
        var doc = env.document || win.document;
        var setT = env.setTimeout || function (f, ms) { return setTimeout(f, ms); };
        var clearT = env.clearTimeout || function (h) { clearTimeout(h); };
        var now = env.now || function () { return Date.now(); };
        var resolver = function () { return env.resolver || defaultResolver(); };
        var uid = cfg.idPrefix + "-" + (++instanceSeq);

        var el = null, inner, facesEl, capEl, capSet, capName, capStatus, capChip, flipBtn, sheetBar, closeBtn, scrim;
        var cur = null, curModel = null, curPt = null, sheet = false, flipped = 0, both = false;
        var isOpen = false, lastClose = -Infinity, openT = null, closeT = null, hideT = null, watchT = null;
        var renderSeq = 0, escHeld = null;
        var images = {};             // url -> {s: "loading"|"ok"|"err", t, p}
        var lookups = {};            // resolver cache key -> promise (one lookup per key)
        var hooks = {};
        var listening = false, unlisten = [];
        var press = { t: null, target: null, fired: false, x: 0, y: 0 }, swallowClick = false;

        function emit(name, data) {
            var fns = hooks[name];
            if (fns) for (var i = 0; i < fns.length; i++) { try { fns[i](data); } catch (e) { /* hook errors never break the UI */ } }
        }
        function clear(h) { if (h !== null && h !== undefined) clearT(h); return null; }
        function mk(tag, cls, text) {
            var n = doc.createElement(tag);
            if (cls) n.className = cls;
            if (text != null) n.textContent = text;
            return n;
        }
        function empty(n) { while (n.firstChild) n.removeChild(n.firstChild); }
        function dpr() { return win.devicePixelRatio || 1; }
        function vw() { return win.innerWidth || (doc.documentElement && doc.documentElement.clientWidth) || 1024; }
        function vh() { return win.innerHeight || (doc.documentElement && doc.documentElement.clientHeight) || 768; }

        // ---- model: what to show for a trigger -------------------------------

        function modelFor(t) {
            var SI = resolver();
            var ref = refFor(t);
            var version = SI.versionForDpr(dpr());
            var names = faceNamesOf(ref);
            var r = SI.resolve(ref, { version: version });
            var faces;
            if (r.faces && r.faces.length > 1) {
                faces = r.faces.slice(0, 2).map(function (f, i) {
                    return { url: f.url, name: f.name || names[i] || names[0] || str(ref.name) };
                });
            } else if (r.url && wantsTwoFaces(ref)) {
                // e.g. name-only DFC: the resolver has no per-face list, ask for each face.
                var back = SI.resolve(assign({}, ref, { face: "back" }), { version: version });
                faces = [{ url: r.url, name: names[0] || str(ref.name) }, { url: back.url, name: names[1] || names[0] || str(ref.name) }];
            } else {
                faces = [{ url: r.url, name: str(ref.name) || names.join(" // ") }];
            }
            return {
                ref: ref, version: version, faces: faces, empty: !r.url,
                printingSpecific: !!(r.url && r.printingSpecific),
                set: str(ref.set || ref.set_code).toLowerCase(),
                cn: str(ref.collectorNumber != null ? ref.collectorNumber : ref.collector_number),
                name: str(ref.name) || names.join(" // ")
            };
        }

        // ---- images: preload, one request per URL, timeout, negative cache ----

        function defaultLoad(url) {
            var Img = win.Image;
            var im = null;
            var p = new Promise(function (res, rej) {
                if (!Img) { rej(new Error("no Image")); return; }
                im = new Img();
                im.decoding = "async";
                im.onload = function () { res(url); };
                im.onerror = function () { rej(new Error("load")); };
                im.src = url;
            });
            p.abort = function () { if (im) { im.onload = im.onerror = null; im.src = ""; } };
            return p;
        }

        function load(url) {
            var c = images[url];
            if (c && (c.s !== "err" || now() - c.t < cfg.negTtl)) return c.p;
            var raw = (env.loadImage || defaultLoad)(url);
            var p = new Promise(function (res, rej) {
                var to = setT(function () {
                    if (raw && typeof raw.abort === "function") raw.abort();
                    rej(new Error("timeout"));
                }, cfg.imgTimeout);
                Promise.resolve(raw).then(function (v) { clearT(to); res(v); }, function (e) { clearT(to); rej(e); });
            });
            c = images[url] = { s: "loading", t: now(), p: p };
            p.then(function () { c.s = "ok"; }, function () { c.s = "err"; c.t = now(); });
            return p;
        }
        function preload(m) {
            m.faces.forEach(function (f) { if (f.url) load(f.url).then(null, function () {}); });
        }

        /**
         * One throttled JSON lookup per ref, to swap api.scryfall.com image
         * redirects (rate limited) for cards.scryfall.io URLs and learn DFC
         * faces. Images for a ref wait for its lookup, so each card costs one
         * API request, not two; if the lookup fails the API image is used.
         * Returns the pending lookup entry, or null when none is needed.
         */
        function lookup(t, m) {
            if (!cfg.lookup || m.empty) return null;
            if (CDN_RE.test(m.faces[0].url) && (m.faces.length > 1 || !wantsTwoFaces(m.ref))) return null;
            var SI = resolver();
            if (typeof SI.resolveRemote !== "function") return null;
            var key = SI.cacheKey(m.ref, { version: m.version });
            if (!key) return null;
            if (lookups[key]) return lookups[key].done ? null : lookups[key];
            var opts = { version: m.version };
            if (env.fetch) opts.fetch = env.fetch;
            emit("lookup", { key: key });
            var entry = lookups[key] = { done: false, p: null };
            var req;
            try { req = SI.resolveRemote(m.ref, opts); } catch (e) { req = Promise.reject(e); }
            entry.p = Promise.resolve(req)
                .then(null, function () {})
                .then(function () {
                    entry.done = true;
                    if (isOpen && cur === t) {
                        curModel = modelFor(t);
                        preload(curModel);
                        layoutAndRender();            // paints the deferred faces (upgraded or not)
                    } else {
                        preload(modelFor(t));
                    }
                });
            return entry;
        }
        function lookupPending(m) {
            if (!cfg.lookup) return false;
            var key = resolver().cacheKey(m.ref, { version: m.version });
            return !!(key && lookups[key] && !lookups[key].done);
        }
        /** Start the lookup if one is needed, else preload the images now. */
        function prefetch(t, m) {
            if (!lookup(t, m)) preload(m);
        }

        // ---- DOM ------------------------------------------------------------

        function build() {
            if (el) return;
            scrim = mk("div", "hud-cp-scrim");
            scrim.hidden = true;
            el = mk("div", "hud-cp");
            el.id = uid;
            el.setAttribute("role", "tooltip");
            el.hidden = true;
            inner = mk("div", "hud-cp-inner");
            facesEl = mk("div", "hud-cp-faces");
            capEl = mk("div", "hud-cp-cap");
            capSet = mk("span", "hud-cp-cap-set");
            capName = mk("span", "hud-cp-cap-name");
            capStatus = mk("span", "hud-cp-cap-status");
            capChip = mk("span", "hud-chip");
            capChip.hidden = true;
            flipBtn = mk("button", "hud-cp-flip", "Flip");
            flipBtn.type = "button";
            flipBtn.hidden = true;
            capEl.appendChild(capSet); capEl.appendChild(capName); capEl.appendChild(capStatus);
            capEl.appendChild(capChip); capEl.appendChild(flipBtn);
            sheetBar = mk("div", "hud-cp-sheet-bar");
            closeBtn = mk("button", "hud-cp-close", "Close");
            closeBtn.type = "button";
            sheetBar.appendChild(closeBtn);
            inner.appendChild(facesEl); inner.appendChild(capEl); inner.appendChild(sheetBar);
            el.appendChild(inner);
            doc.body.appendChild(scrim);
            doc.body.appendChild(el);
            flipBtn.addEventListener("click", function (e) { if (e && e.stopPropagation) e.stopPropagation(); flip(); });
            closeBtn.addEventListener("click", function (e) { if (e && e.stopPropagation) e.stopPropagation(); hide(0); });
            scrim.addEventListener("click", function () { hide(0); });
            facesEl.addEventListener("click", function () { if (isOpen && !both && curModel && curModel.faces.length > 1) flip(); });
            // Flip mode makes the popover interactive: moving onto it must not close it.
            el.addEventListener("pointerenter", function () { if (isOpen && !sheet) closeT = clear(closeT); });
            el.addEventListener("pointerleave", function (e) {
                if (!isOpen || sheet) return;
                if (cur && e && findTrigger(e.relatedTarget) === cur) return;
                hide(cfg.close);
            });
        }

        function paintFace(fig, face, seq, deferred) {
            empty(fig);
            fig.style.aspectRatio = "488 / 680";
            var im = mk("img");
            im.alt = face.name || "Card";
            im.decoding = "async";
            im.setAttribute("draggable", "false");
            var ph = mk("div", "hud-cp-ph");
            var lbl = mk("span", "hud-cp-ph-lbl", "Loading");
            ph.appendChild(mk("span", "hud-cp-ph-name", face.name || "Card"));
            ph.appendChild(lbl);
            fig.appendChild(im);
            fig.appendChild(ph);
            function fail() {
                fig.setAttribute("data-state", "error");
                lbl.textContent = "Image unavailable";
                capStatus.textContent = "Failed to load image";
                el.classList.add("has-error");
            }
            if (!face.url) { fail(); return; }
            var c = images[face.url];
            if (c && c.s === "ok") { im.src = face.url; fig.setAttribute("data-state", "ok"); return; }
            fig.setAttribute("data-state", "loading");
            if (deferred) return;                 // lookup in flight: it re-renders when done
            load(face.url).then(function () {
                if (seq !== renderSeq) return;
                im.src = face.url;
                fig.setAttribute("data-state", "ok");
            }, function () {
                if (seq !== renderSeq) return;
                fail();
            });
        }

        function render(m) {
            var seq = ++renderSeq;
            var deferred = lookupPending(m);
            var two = m.faces.length > 1;
            var shown = two ? (both ? [0, 1] : [flipped]) : [0];
            empty(facesEl);
            el.classList.remove("has-error");
            capStatus.textContent = "";
            shown.forEach(function (i) {
                var fig = mk("figure", "hud-cp-face");
                fig.setAttribute("data-face", i === 0 ? "front" : "back");
                facesEl.appendChild(fig);
                paintFace(fig, m.faces[i], seq, deferred);
            });
            capSet.textContent = m.set ? m.set.toUpperCase() + (m.cn ? " \u00b7 " + m.cn : "") : "";
            capName.textContent = shown.length === 1 ? m.faces[shown[0]].name : m.name;
            var chip = "", chipCls = "hud-chip";
            if (!m.printingSpecific && !m.empty) { chip = "Printing unknown"; chipCls += " hud-chip--signal"; }
            else if (two && !both) { chip = flipped ? "Back" : "Front"; chipCls += " hud-chip--neutral"; }
            capChip.textContent = chip;
            capChip.className = chipCls;
            capChip.hidden = !chip;
            var flippable = two && !both;
            flipBtn.hidden = !flippable;
            flipBtn.setAttribute("tabindex", sheet ? "0" : "-1");
            el.classList.toggle("is-flippable", flippable);
            el.classList.toggle("is-interactive", flippable && !sheet);
            el.classList.toggle("is-name-only", !m.printingSpecific && !m.empty);
            el.setAttribute("data-faces", String(shown.length));
            emit("render", { faces: shown.length, both: both, flipped: flipped });
        }

        // ---- placement ------------------------------------------------------

        function anchorRect(t, pt) {
            var rs = t.getClientRects ? t.getClientRects() : [];
            if (!rs || rs.length < 2 || !pt) return t.getBoundingClientRect();
            for (var k = 0; k < rs.length; k++) if (pt.y >= rs[k].top && pt.y <= rs[k].bottom) return rs[k];
            return rs[0];
        }

        /** Best placement for k faces of nominal width fw. ok=false when a face would drop below minFaceWidth. */
        function fit(t, k, fw, pt) {
            var a = anchorRect(t, pt), W = vw(), H = vh(), G = cfg.gap, M = cfg.margin;
            var extraW = (k - 1) * cfg.faceGap, extraH = cfg.capH + cfg.capGap;
            function scaleFor(roomW, roomH) {
                return Math.min(1, (roomW - extraW) / (k * fw), (roomH - extraH) * cfg.ratio / fw);
            }
            var minS = cfg.minFaceWidth / fw;
            var right = W - a.right - G - M, left = a.left - G - M, roomV = H - 2 * M;
            var sR = scaleFor(right, roomV), sL = scaleFor(left, roomV);
            var side, s;
            if (sR >= 1) { side = "right"; s = 1; }
            else if (sL >= 1) { side = "left"; s = 1; }
            else if (Math.max(sR, sL) >= minS) { side = sR >= sL ? "right" : "left"; s = Math.max(sR, sL); }
            else {
                var below = H - a.bottom - G - M, above = a.top - G - M;
                side = below >= above ? "below" : "above";
                s = scaleFor(W - 2 * M, Math.max(below, above));
            }
            s = Math.max(0.1, s);
            var faceW = fw * s, w = k * faceW + extraW, h = faceW / cfg.ratio + extraH;
            var x, y;
            if (side === "right") { x = a.right + G; y = a.top + a.height / 2 - h / 2; }
            else if (side === "left") { x = a.left - G - w; y = a.top + a.height / 2 - h / 2; }
            else { x = a.left + a.width / 2 - w / 2; y = side === "below" ? a.bottom + G : a.top - G - h; }
            x = Math.max(M, Math.min(x, W - M - w));
            y = Math.max(M, Math.min(y, H - M - h));
            return { side: side, scale: s, ok: s >= minS - 1e-9, k: k, faceW: faceW, w: w, h: h, x: x, y: y };
        }

        function layoutAndRender() {
            var m = curModel, two = m.faces.length > 1, L = null;
            if (sheet) {
                both = false;
            } else {
                L = two ? fit(cur, 2, cfg.widthDfc, curPt) : null;
                both = !!(L && L.ok);
                if (!both) L = fit(cur, 1, cfg.width, curPt);
            }
            render(m);
            if (sheet) {
                el.style.transform = "";
                facesEl.style.width = Math.round(Math.min(vw() - 2 * cfg.sheetPad, cfg.sheetWidth)) + "px";
                el.setAttribute("data-side", "sheet");
            } else {
                facesEl.style.width = Math.round(L.w) + "px";
                el.style.transform = "translate3d(" + Math.round(L.x) + "px," + Math.round(L.y) + "px,0)";
                el.setAttribute("data-side", L.side);
            }
            return L;
        }

        // ---- ARIA -------------------------------------------------------------

        function addDescribedBy(t) {
            var prev = attr(t, "aria-describedby");
            var toks = prev ? prev.split(/\s+/) : [];
            if (toks.indexOf(uid) < 0) toks.push(uid);
            t.setAttribute("aria-describedby", toks.join(" "));
        }
        function removeDescribedBy(t) {
            var prev = attr(t, "aria-describedby");
            if (prev === null) return;
            var toks = prev.split(/\s+/).filter(function (x) { return x && x !== uid; });
            if (toks.length) t.setAttribute("aria-describedby", toks.join(" "));
            else t.removeAttribute("aria-describedby");
        }

        // ---- show / hide --------------------------------------------------------

        function show(t, how, pt) {
            openT = clear(openT);
            closeT = clear(closeT);
            hideT = clear(hideT);
            if (!t || (t.isConnected === false)) return;
            build();
            var warm = isOpen;
            if (cur && cur !== t) { cur.classList.remove("is-cp-open"); removeDescribedBy(cur); }
            cur = t; curPt = pt || null; sheet = how === "touch"; flipped = 0;
            curModel = modelFor(t);
            prefetch(t, curModel);
            el.classList.toggle("is-sheet", sheet);
            el.setAttribute("role", sheet ? "dialog" : "tooltip");
            if (sheet) el.setAttribute("aria-label", curModel.name || "Card preview");
            else el.removeAttribute("aria-label");
            scrim.hidden = !sheet;
            el.hidden = false;
            layoutAndRender();
            el.classList.toggle("is-swap", warm);
            if (!warm) { el.classList.remove("is-open"); void el.offsetWidth; }   // restart the entrance
            el.classList.add("is-open");
            if (sheet) { void scrim.offsetWidth; scrim.classList.add("is-open"); }
            t.classList.add("is-cp-open");
            addDescribedBy(t);
            isOpen = true;
            watch();
            emit("open", { how: how, warm: warm, trigger: t });
        }

        function doClose() {
            closeT = clear(closeT);
            if (!isOpen) return;
            isOpen = false;
            lastClose = now();
            el.classList.remove("is-open");
            scrim.classList.remove("is-open");
            var t = cur;
            if (t) { t.classList.remove("is-cp-open"); removeDescribedBy(t); }
            // Never strand focus inside a closed sheet: hand it back to the trigger.
            var active = doc.activeElement;
            if (t && active && el.contains && el.contains(active) && t.focus) t.focus();
            cur = null; curModel = null;
            watchT = clear(watchT);
            hideT = setT(function () { hideT = null; if (!isOpen) { el.hidden = true; scrim.hidden = true; } }, cfg.outMs);
            emit("close", { trigger: t });
        }

        /** Close after `delay` ms (0 = now). Also cancels a pending open. */
        function hide(delay) {
            openT = clear(openT);
            closeT = clear(closeT);
            if (!isOpen) return;
            if (!delay) doClose();
            else closeT = setT(doClose, delay);
        }

        function watch() {
            watchT = clear(watchT);
            watchT = setT(function tick() {
                watchT = null;
                if (!isOpen) return;
                if (cur && cur.isConnected === false) { hide(0); return; }   // list re-rendered under us
                watchT = setT(tick, cfg.watchMs);
            }, cfg.watchMs);
        }

        function flip() {
            if (!isOpen || !curModel || curModel.faces.length < 2) return;
            flipped = flipped ? 0 : 1;
            render(curModel);
        }

        // ---- intent ---------------------------------------------------------------

        function intent(t, how, pt) {
            closeT = clear(closeT);
            openT = clear(openT);
            if (t === cur && isOpen) return;
            var m = modelFor(t);
            prefetch(t, m);                      // fetch during the delay
            var warm = isOpen || now() - lastClose < cfg.warmWindow;
            var d = how === "key" ? cfg.focusOpen : (warm ? cfg.openWarm : cfg.openCold);
            emit("intent", { how: how, delay: d, warm: warm, trigger: t });
            openT = setT(function () { openT = null; show(t, how, pt); }, d);
        }

        function focusVisible(t) {
            try { return t.matches ? t.matches(":focus-visible") : true; } catch (e) { return true; }
        }

        // ---- event handlers -----------------------------------------------------------

        function onPointerOver(e) {
            if (e.pointerType === "touch") return;
            var t = findTrigger(e.target);
            if (!t || t === findTrigger(e.relatedTarget)) return;
            intent(t, "mouse", { x: e.clientX, y: e.clientY });
        }
        function onPointerOut(e) {
            if (e.pointerType === "touch") return;
            var t = findTrigger(e.target);
            if (!t || t === findTrigger(e.relatedTarget)) return;
            // Leaving any trigger cancels its pending open; if a preview is up
            // (this one, or the one we were about to swap away from) it gets
            // the close grace period, which the next trigger's intent cancels.
            openT = clear(openT);
            if (isOpen) hide(cfg.close);
        }
        function onFocusIn(e) {
            var t = findTrigger(e.target);
            if (!t || !focusVisible(e.target) || escHeld === t) return;
            intent(t, "key");
        }
        function onFocusOut(e) {
            var t = findTrigger(e.target);
            if (!t) return;
            if (escHeld === t) escHeld = null;
            if (el && e.relatedTarget && el.contains(e.relatedTarget)) return;   // into the sheet's buttons
            if (t === cur) hide(0); else openT = clear(openT);
        }
        function onKeyDown(e) {
            if (e.key !== "Escape" && e.key !== "Esc") return;
            if (!isOpen) { openT = clear(openT); return; }
            // First Esc belongs to the preview, not to a modal underneath it.
            e.preventDefault();
            e.stopPropagation();
            if (e.stopImmediatePropagation) e.stopImmediatePropagation();
            escHeld = cur;                       // don't reopen until focus moves
            hide(0);
        }
        function onPointerDown(e) {
            if (e.pointerType === "touch") return onTouchStart(e);
            openT = clear(openT);
            if (isOpen && !sheet && !(el && el.contains(e.target))) hide(0);
        }
        function onScroll() { openT = clear(openT); if (isOpen && !sheet) hide(0); }
        function onResize() { openT = clear(openT); if (isOpen) hide(0); }
        function onBlur() { hide(0); }

        // Touch: long-press on actionable triggers, tap on inert ones.
        function onTouchStart(e) {
            swallowClick = false;                // never let a stale flag eat a real tap
            var t = findTrigger(e.target);
            if (!t || (sheet && isOpen)) return;
            press.target = t; press.fired = false; press.x = e.clientX; press.y = e.clientY;
            press.actionable = isActionable(e.target);
            press.t = clear(press.t);
            if (!press.actionable) return;       // inert: opens on pointerup
            press.t = setT(function () {
                press.t = null; press.fired = true; swallowClick = true;
                if (win.navigator && win.navigator.vibrate) { try { win.navigator.vibrate(8); } catch (err) { /* ignore */ } }
                show(t, "touch");
            }, cfg.press);
        }
        function onPointerMove(e) {
            if (e.pointerType !== "touch" || !press.target) return;
            var dx = e.clientX - press.x, dy = e.clientY - press.y;
            if (Math.sqrt(dx * dx + dy * dy) > cfg.pressSlop) { press.t = clear(press.t); press.target = null; }
        }
        function onPointerUp(e) {
            if (e.pointerType !== "touch") return;
            press.t = clear(press.t);
            var t = press.target;
            press.target = null;
            if (t && !press.fired && !press.actionable && !isOpen) { swallowClick = true; show(t, "touch"); }
        }
        function onPointerCancel() { press.t = clear(press.t); press.target = null; }
        function onContextMenu(e) {
            if (press.fired || (findTrigger(e.target) && press.target)) e.preventDefault();
        }
        function onClick(e) {
            if (swallowClick) { swallowClick = false; e.preventDefault(); e.stopPropagation(); }
        }

        function on(target, type, fn, capture) {
            if (!target || !target.addEventListener) return;
            target.addEventListener(type, fn, !!capture);
            unlisten.push(function () { target.removeEventListener(type, fn, !!capture); });
        }

        function listen() {
            if (listening) return api;
            listening = true;
            on(doc, "pointerover", onPointerOver);
            on(doc, "pointerout", onPointerOut);
            on(doc, "focusin", onFocusIn);
            on(doc, "focusout", onFocusOut);
            on(doc, "keydown", onKeyDown, true);
            on(doc, "pointerdown", onPointerDown, true);
            on(doc, "pointermove", onPointerMove);
            on(doc, "pointerup", onPointerUp);
            on(doc, "pointercancel", onPointerCancel);
            on(doc, "contextmenu", onContextMenu);
            on(doc, "click", onClick, true);
            on(win, "scroll", onScroll, true);
            on(win, "resize", onResize);
            on(win, "blur", onBlur);
            return api;
        }

        function destroy() {
            hide(0);
            openT = clear(openT); hideT = clear(hideT); watchT = clear(watchT); press.t = clear(press.t);
            unlisten.forEach(function (f) { f(); });
            unlisten = [];
            listening = false;
            if (el && el.parentNode) el.parentNode.removeChild(el);
            if (scrim && scrim.parentNode) scrim.parentNode.removeChild(scrim);
            el = null;
        }

        var api = {
            config: cfg,
            id: uid,
            listen: listen,
            destroy: destroy,
            /** Open for `t` now (how: "mouse" | "key" | "touch"). */
            open: function (t, how) { show(t, how || "mouse"); return api; },
            close: function () { hide(0); return api; },
            isOpen: function () { return isOpen; },
            current: function () { return cur; },
            element: function () { return el; },
            scrim: function () { return scrim; },
            modelFor: modelFor,
            flip: flip,
            on: function (name, fn) { (hooks[name] = hooks[name] || []).push(fn); return api; },
            imageState: function (url) { return images[url] ? images[url].s : null; }
        };
        return api;
    }

    // ---- browser singleton --------------------------------------------------------

    var singleton = null;
    function init(options) {
        if (!singleton) singleton = create(options).listen();
        return singleton;
    }

    if (typeof module === "undefined" && typeof document !== "undefined" && root && !root.CardHoverPreviewManual) {
        if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { init(); });
        else init();
    }

    return {
        defaults: DEFAULTS,
        create: create,
        init: init,
        bind: bind,
        unbind: unbind,
        attrs: attrs,
        wrap: wrap,
        findTrigger: findTrigger,
        isActionable: isActionable
    };
});
