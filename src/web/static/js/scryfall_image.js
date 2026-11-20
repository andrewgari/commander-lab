/* ==========================================================================
   Commander Lab: printing-aware Scryfall image URL resolver.

   One shared helper for the card hover preview (docs/design/hover-preview/
   SPEC.md) and anything else that needs "the image of THIS printing".
   Browser: window.ScryfallImage. Node: require("./scryfall_image.js").
   No DOM access, no dependencies; fetch/timers/clock are injectable.

   ---- Interface ---------------------------------------------------------

   cardRef (every field optional; snake_case aliases from Archidekt/Scryfall
   payloads are accepted: scryfall_id / uid, image_uris, card_faces,
   collector_number):
     { scryfallId, imageUris, cardFaces, set, collectorNumber, name,
       face, layout, version }
     scryfallId       Scryfall printing id (Archidekt `uid` is the same id)
     imageUris        Scryfall `image_uris` object ({small, normal, large, png,
                      art_crop, border_crop})
     cardFaces        Scryfall `card_faces` array ([{name, image_uris?}, ...])
     set, collectorNumber   printing identity when there is no id
     name             last-resort lookup, NOT printing specific
     face             "front" (default) | "back": which face `url` points at
     layout           Scryfall layout; only the layouts whose faces have
                      separate images (transform, modal_dfc, reversible_card,
                      double_faced_token) get API-built `faces`
     version          "small"|"normal"|"large"|"png"|"art_crop"|"border_crop"
                      (default "normal"; the spec asks for "large" when
                      devicePixelRatio > 1.5, see versionForDpr)

   ScryfallImage.resolve(cardRef, opts?) -> { url, printingSpecific, faces? }
     Synchronous, no network. Resolution order:
       1. imageUris        -> cards.scryfall.io URL (prefers `version`, else
                              normal, large, png, small)
       2. scryfallId       -> https://api.scryfall.com/cards/{id}?format=image&version=normal
       3. set + number     -> https://api.scryfall.com/cards/{set}/{number}?format=image&version=normal
                              (set lower-cased, number URL-encoded: "★" -> %E2%98%85)
       4. name only        -> https://api.scryfall.com/cards/named?exact={name}&format=image&version=normal
                              printingSpecific:false + a console.warn per name
     DFC (no top-level imageUris, cardFaces[*].imageUris present): `faces` has
     one {url, name} per face; `url` is the front face, or the back face when
     face:"back". The hover spec shows all faces side by side, so use `faces`.
     For API URLs, face:"back" appends &face=back.
     An empty ref returns { url: "", printingSpecific: false }.

   ScryfallImage.resolveRemote(cardRef, opts?) -> Promise<same shape>
     Only for refs that resolve() can answer with an API URL and the caller
     wants the real cards.scryfall.io URLs / DFC faces: fetches the card JSON
     (throttled, Accept: application/json, User-Agent when not in a browser),
     then resolves from its image_uris/card_faces. Network failure falls back
     to the synchronous result. Name lookups stay printingSpecific:false.

   Shared state, exported for reuse:
     ScryfallImage.cache      Map-like (get/set/has/delete/clear/size/stats())
                              keyed by id, else set+number, else name (plus
                              version and face). A ref carrying imageUris
                              refreshes the entry; later refs for the same
                              printing without imageUris hit the cache.
     ScryfallImage.throttle   <= 10 requests/second queue: throttle.run(fn)
     ScryfallImage.createCache(max) / createThrottle(perSecond, hooks)
     ScryfallImage.cacheKey(cardRef, opts), versionForDpr(dpr), config

   Returned objects are frozen and shared with the cache: do not mutate them.
   ========================================================================== */
(function (root, factory) {
    "use strict";
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.ScryfallImage = factory();
})(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var API = "https://api.scryfall.com";
    var VERSIONS = ["small", "normal", "large", "png", "art_crop", "border_crop"];
    var FALLBACK_ORDER = ["normal", "large", "png", "small", "border_crop", "art_crop"];
    // Layouts where every face is its own image (the others are one image).
    var TWO_IMAGE_LAYOUTS = { transform: 1, modal_dfc: 1, reversible_card: 1, double_faced_token: 1 };

    var config = {
        retryDelay: 1500,            // ms to back off before the single retry after a 429/503/network error
        appName: "CommanderLab",
        contact: "https://github.com/andrewgari/commander-lab",
        warn: true,                 // console.warn on name-only (not printing specific) refs
        maxRequestsPerSecond: 10
    };

    function userAgent() { return config.appName + "/1.0 (" + config.contact + ")"; }

    function isBrowser() { return typeof window !== "undefined" && typeof window.document !== "undefined"; }

    // ---- cache ------------------------------------------------------------

    /** Small LRU Map wrapper with hit/miss counters. */
    function createCache(maxEntries) {
        var max = maxEntries || 2000;
        var map = new Map();
        var hits = 0, misses = 0;
        return {
            get: function (key) {
                if (!map.has(key)) { misses++; return undefined; }
                var v = map.get(key);
                map.delete(key); map.set(key, v);       // refresh recency
                hits++;
                return v;
            },
            peek: function (key) { return map.get(key); },
            set: function (key, value) {
                if (map.has(key)) map.delete(key);
                map.set(key, value);
                if (map.size > max) map.delete(map.keys().next().value);
                return this;
            },
            has: function (key) { return map.has(key); },
            delete: function (key) { return map.delete(key); },
            clear: function () { map.clear(); hits = 0; misses = 0; },
            keys: function () { return Array.from(map.keys()); },
            get size() { return map.size; },
            stats: function () { return { hits: hits, misses: misses, size: map.size }; }
        };
    }

    // ---- throttle ---------------------------------------------------------

    /**
     * Spaces calls at least 1000/perSecond ms apart (default 10/s => 100 ms).
     * run(fn) returns a promise for fn()'s result. Hooks (for tests):
     * { now: () => ms, setTimeout: (fn, ms) => handle }.
     */
    function createThrottle(perSecond, hooks) {
        hooks = hooks || {};
        var now = hooks.now || Date.now;
        var later = hooks.setTimeout || setTimeout;
        var interval = 1000 / Math.max(1, perSecond || config.maxRequestsPerSecond);
        var nextSlot = 0;
        return {
            interval: interval,
            run: function (fn) {
                return new Promise(function (resolve, reject) {
                    var t = now();
                    var start = Math.max(t, nextSlot);
                    nextSlot = start + interval;
                    var go = function () {
                        try { Promise.resolve(fn()).then(resolve, reject); } catch (e) { reject(e); }
                    };
                    if (start <= t) go(); else later(go, start - t);
                });
            }
        };
    }

    var cache = createCache(2000);
    var throttle = createThrottle(10);

    // ---- helpers ----------------------------------------------------------

    function str(v) { return v === undefined || v === null ? "" : String(v).trim(); }

    // Accept camelCase and Scryfall/Archidekt snake_case spellings.
    function normalizeRef(ref) {
        ref = ref || {};
        var set = str(ref.set != null ? ref.set : ref.set_code).toLowerCase();
        var cn = str(ref.collectorNumber != null ? ref.collectorNumber : ref.collector_number);
        var id = str(ref.scryfallId != null ? ref.scryfallId : (ref.scryfall_id != null ? ref.scryfall_id : (ref.uid != null ? ref.uid : ref.id))).toLowerCase();
        var faces = ref.cardFaces || ref.card_faces;
        return {
            id: id,
            imageUris: ref.imageUris || ref.image_uris || null,
            cardFaces: Array.isArray(faces) ? faces : null,
            set: set,
            cn: cn,
            name: str(ref.name),
            face: ref.face === "back" ? "back" : "front",
            layout: str(ref.layout),
            version: ref.version,
            printingSpecific: ref.printingSpecific
        };
    }

    function pickVersion(opts, n) {
        var v = (opts && opts.version) || n.version;
        return VERSIONS.indexOf(v) >= 0 ? v : "normal";
    }

    function pickUri(uris, version) {
        if (!uris || typeof uris !== "object") return "";
        if (uris[version]) return uris[version];
        for (var i = 0; i < FALLBACK_ORDER.length; i++) if (uris[FALLBACK_ORDER[i]]) return uris[FALLBACK_ORDER[i]];
        return "";
    }

    function faceListFrom(cardFaces, version) {
        if (!cardFaces) return null;
        var out = [];
        for (var i = 0; i < cardFaces.length; i++) {
            var u = pickUri(cardFaces[i] && (cardFaces[i].imageUris || cardFaces[i].image_uris), version);
            if (!u) return null;                       // all-or-nothing: a partial DFC is not usable
            out.push({ url: u, name: str(cardFaces[i].name) });
        }
        return out.length ? out : null;
    }

    function baseKey(n) {
        if (n.id) return "id:" + n.id;
        if (n.set && n.cn) return "sn:" + n.set + "/" + n.cn;
        if (n.name) return "name:" + n.name.toLowerCase();
        return "";
    }

    /** Cache key: id, else set+number, else name; plus version and face. "" if the ref has no identity. */
    function cacheKey(cardRef, opts) {
        var n = normalizeRef(cardRef);
        var b = baseKey(n);
        return b ? b + "|" + pickVersion(opts, n) + "|" + n.face : "";
    }

    function apiUrl(n, version) {
        var q = "?format=image&version=" + version + (n.face === "back" ? "&face=back" : "");
        if (n.id) return API + "/cards/" + encodeURIComponent(n.id) + q;
        if (n.set && n.cn) return API + "/cards/" + encodeURIComponent(n.set) + "/" + encodeURIComponent(n.cn) + q;
        return API + "/cards/named?exact=" + encodeURIComponent(n.name) + "&format=image&version=" + version +
            (n.face === "back" ? "&face=back" : "");
    }

    // Shared on the global so a second copy of this script cannot warn again for the same name.
    var warnRoot = typeof globalThis !== "undefined" ? globalThis : {};
    var warned = warnRoot.__scryfallImageWarned || (warnRoot.__scryfallImageWarned = {});
    function warnNameOnly(name, opts) {
        var k = name.toLowerCase();
        if (warned[k]) return;
        warned[k] = true;
        var msg = "[ScryfallImage] \"" + name + "\" has no Scryfall id or set/collector number: " +
            "showing an arbitrary printing (printingSpecific: false)";
        if (opts && typeof opts.onWarn === "function") opts.onWarn(msg, name);
        else if (config.warn && typeof console !== "undefined" && console.warn) console.warn(msg);
    }

    function freeze(r) {
        if (r.faces) { r.faces.forEach(Object.freeze); Object.freeze(r.faces); }
        return Object.freeze(r);
    }

    // ---- resolve ----------------------------------------------------------

    function build(n, version, opts) {
        var ps = n.printingSpecific === false ? false : true;

        // 1. real image_uris (single image: normal, split, flip, adventure...)
        var top = pickUri(n.imageUris, version);
        if (top) return { url: top, printingSpecific: ps };

        // 1b. DFC: no top-level image_uris, per-face image_uris
        var faces = faceListFrom(n.cardFaces, version);
        if (faces) {
            var idx = n.face === "back" && faces.length > 1 ? 1 : 0;
            return { url: faces[idx].url, printingSpecific: ps, faces: faces };
        }

        // 2./3./4. API image redirects
        if (n.id || (n.set && n.cn)) {
            var r = { url: apiUrl(n, version), printingSpecific: ps };
            // card_faces without images but a known two-image layout: one API URL per face
            if (n.cardFaces && n.cardFaces.length > 1 && TWO_IMAGE_LAYOUTS[n.layout]) {
                var f = n.face, self = n;
                r.faces = n.cardFaces.map(function (cf, i) {
                    return { url: apiUrl({ id: self.id, set: self.set, cn: self.cn, face: i === 0 ? "front" : "back" }, version),
                             name: str(cf && cf.name) };
                });
                r.url = r.faces[f === "back" ? 1 : 0].url;
            }
            return r;
        }
        if (n.name) return { url: apiUrl(n, version), printingSpecific: false };
        return null;
    }

    /**
     * Resolve a card reference to an image URL without touching the network.
     * @param {object} cardRef see the header above
     * @param {{version?: string, onWarn?: Function}} [opts]
     * @returns {{url: string, printingSpecific: boolean, faces?: Array<{url: string, name: string}>}}
     */
    function resolve(cardRef, opts) {
        var n = normalizeRef(cardRef);
        var version = pickVersion(opts, n);
        var key = baseKey(n) ? baseKey(n) + "|" + version + "|" + n.face : "";
        var hasImages = !!(pickUri(n.imageUris, version) || faceListFrom(n.cardFaces, version));

        // Refs that carry real image data refresh the entry; others read it.
        if (key && !hasImages) {
            var hit = cache.get(key);
            if (hit) return hit;
        }
        var built = build(n, version, opts);
        if (!built) return Object.freeze({ url: "", printingSpecific: false });
        if (!built.printingSpecific && !n.id && !(n.set && n.cn) && !hasImages) warnNameOnly(n.name, opts);
        var out = freeze(built);
        if (key) cache.set(key, out);
        return out;
    }

    // ---- network lookup (throttled) --------------------------------------

    function cardJsonUrl(n) {
        if (n.id) return API + "/cards/" + encodeURIComponent(n.id);
        if (n.set && n.cn) return API + "/cards/" + encodeURIComponent(n.set) + "/" + encodeURIComponent(n.cn);
        return API + "/cards/named?exact=" + encodeURIComponent(n.name);
    }

    /** Request headers per Scryfall etiquette. Browsers forbid setting User-Agent, so only Node gets it. */
    function requestHeaders(accept) {
        var h = { "Accept": accept || "application/json" };
        if (!isBrowser()) h["User-Agent"] = userAgent();
        return h;
    }

    /**
     * Fetch the Scryfall card JSON for a ref (id, else set/number, else name), throttled.
     * @returns {Promise<object>} Scryfall card object; rejects on HTTP error.
     */
    function fetchCard(cardRef, opts) {
        var n = normalizeRef(cardRef);
        if (!baseKey(n)) return Promise.reject(new Error("ScryfallImage.fetchCard: empty card reference"));
        var f = (opts && opts.fetch) || (typeof fetch !== "undefined" ? fetch : null);
        if (!f) return Promise.reject(new Error("ScryfallImage.fetchCard: no fetch available"));
        var url = cardJsonUrl(n);
        var delay = opts && opts.retryDelay != null ? opts.retryDelay : config.retryDelay;
        var later = (opts && opts.setTimeout) || setTimeout;
        function attempt(left) {
            return throttle.run(function () { return f(url, { headers: requestHeaders("application/json") }); })
                .then(function (res) {
                    if (res.ok) return res.json();
                    // 429/503: back off and retry once instead of hammering Scryfall.
                    if (left > 0 && (res.status === 429 || res.status === 503)) return wait(left);
                    throw new Error("Scryfall " + res.status + " for " + url);
                }, function (err) {
                    // A 429 has no CORS headers, so browsers surface it as a network TypeError.
                    if (left > 0) return wait(left);
                    throw err;
                });
        }
        function wait(left) {
            return new Promise(function (r) { later(r, delay); }).then(function () { return attempt(left - 1); });
        }
        return attempt(1);
    }

    /**
     * Like resolve(), but looks the card up on Scryfall (throttled) to get the real
     * cards.scryfall.io image_uris / DFC faces. Falls back to resolve() on any failure.
     * Name-only refs stay printingSpecific:false.
     * @returns {Promise<{url: string, printingSpecific: boolean, faces?: Array}>}
     */
    function resolveRemote(cardRef, opts) {
        var n = normalizeRef(cardRef);
        var version = pickVersion(opts, n);
        var key = baseKey(n) ? baseKey(n) + "|" + version + "|" + n.face : "";
        var local = resolve(cardRef, opts);
        // Already real image data (imageUris/cardFaces, or cached from an earlier lookup)?
        if (!key || /^https:\/\/cards\.scryfall\.io\//.test(local.url) || (local.faces && local.faces.length)) {
            return Promise.resolve(local);
        }
        var nameOnly = !n.id && !(n.set && n.cn);
        return fetchCard(cardRef, opts).then(function (card) {
            var ref = {
                scryfallId: n.id || card.id, set: n.set || card.set, collectorNumber: n.cn || card.collector_number,
                name: n.name || card.name, imageUris: card.image_uris, cardFaces: card.card_faces,
                layout: card.layout, face: n.face, version: version,
                printingSpecific: nameOnly ? false : undefined
            };
            var out = resolve(ref, opts);
            cache.set(key, out);                                   // also answer the original (e.g. set/number) key
            if (!nameOnly && card.id) cache.set("id:" + String(card.id).toLowerCase() + "|" + version + "|" + n.face, out);
            return out;
        }).catch(function () { return local; });
    }

    /** Scryfall image version for a device pixel ratio (hover spec §3): large above 1.5, else normal. */
    function versionForDpr(dpr) { return dpr > 1.5 ? "large" : "normal"; }

    return {
        resolve: resolve,
        resolveRemote: resolveRemote,
        fetchCard: fetchCard,
        cacheKey: cacheKey,
        versionForDpr: versionForDpr,
        requestHeaders: requestHeaders,
        userAgent: userAgent,
        cache: cache,
        throttle: throttle,
        createCache: createCache,
        createThrottle: createThrottle,
        config: config
    };
});
