/* Deck Genre (GN): the Journal's Archetype / Themes / Kindred tag fields + the deck-header strip.
   Spec: docs/DECK_GENRE_UX_SPEC.md (ported from docs/design/genre/genre.js). Data rules:
   docs/DECK_GENRE_TAXONOMY.md; the server re-checks everything (deck_genre.py).

       var G = DeckGenre.create(OPTIONS, CREATURE_TYPES);   // deck_genre.page_options() + creature_types()
       var f = new G.TagField(el, "archetype" | "theme" | "kindred", values, onChange);

   Plain ES5 + DOM like the rest of the journal. The pure helpers (canon*, strip*, toPayload, readHtml)
   need no DOM, so tests/test_deck_genre.py runs them in node. Every user-typed or stored value goes
   through esc() before it reaches innerHTML / insertAdjacentHTML; labels and hints are constants. */
(function (root) {
    "use strict";

    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function low(s) { return String(s || "").trim().toLowerCase(); }
    function clean(s) { return String(s || "").trim().replace(/\s+/g, " "); }
    function byId(list) { var m = {}; (list || []).forEach(function (o) { m[o.id] = o; }); return m; }

    function create(T, TYPES) {
        TYPES = TYPES || [];
        var LIM = T.limits || { archetype: 4, theme: 5, kindred: 5, custom_max_len: 32 };
        var CUSTOM_MAX = LIM.custom_max_len || 32;
        var ARCH = byId(T.archetypes), GENRE = byId(T.genres);
        var TYPE_BY_LOW = {}; TYPES.forEach(function (t) { TYPE_BY_LOW[t.toLowerCase()] = t; });
        var ALIAS = {}; Object.keys(T.kindred.aliases || {}).forEach(function (k) { ALIAS[k.toLowerCase()] = T.kindred.aliases[k]; });

        /* Kindred normalization (taxonomy §3): trim, case-insensitive, alias map, then strip a
           trailing "es"/"s" and re-match. Returns the catalog spelling or null. */
        function canonType(q) {
            var s = low(q); if (!s) return null;
            if (TYPE_BY_LOW[s]) return TYPE_BY_LOW[s];
            if (ALIAS[s]) return ALIAS[s];
            if (/es$/.test(s) && TYPE_BY_LOW[s.slice(0, -2)]) return TYPE_BY_LOW[s.slice(0, -2)];
            if (/s$/.test(s) && TYPE_BY_LOW[s.slice(0, -1)]) return TYPE_BY_LOW[s.slice(0, -1)];
            return null;
        }
        /* Seeded list lookup: exact id or label (case-insensitive) -> id. */
        function canonSeed(map, q) {
            var s = low(q); if (!s) return null;
            for (var id in map) { if (id === s.replace(/\s+/g, "_") || low(map[id].label) === s) return id; }
            return null;
        }
        /* Levenshtein distance, for "did you mean" on kindred typos. */
        function dist(a, b) {
            var prev = [], i, j;
            for (j = 0; j <= b.length; j++) prev[j] = j;
            for (i = 1; i <= a.length; i++) {
                var cur = [i];
                for (j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1));
                prev = cur;
            }
            return prev[b.length];
        }
        /* Nearest creature type within a small typo budget (1 edit up to 5 letters, else 2), or null. */
        function closeType(q, skip) {
            var s = low(q), best = null, bd = 99;
            if (s.length < 3) return null;
            var budget = s.length <= 5 ? 1 : 2;
            TYPES.forEach(function (t) {
                if (skip && skip(t)) return;
                var d = dist(s, t.toLowerCase());
                if (d < bd) { bd = d; best = t; }
            });
            return bd <= budget ? best : null;
        }
        function hl(text, q) {
            var t = String(q || "").trim(), i = low(text).indexOf(low(t));
            if (!t || i < 0) return esc(text);
            return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + t.length)) + "</mark>" + esc(text.slice(i + t.length));
        }

        var FIELDS = {
            archetype: {
                label: "Style", max: LIM.archetype, primary: true, custom: false, noun: "style",
                groups: T.archetype_groups, options: T.archetypes, map: ARCH,
                quick: ["tokens", "aristocrats", "voltron", "spellslinger", "counters", "ramp", "combo", "control", "graveyard", "blink"], quickShow: 6,
                placeholder: "Search " + T.archetypes.length + " styles…",
                hint: "How the deck plays. The first is the primary (★); up to " + (LIM.archetype - 1) + " more. Feeds Deep Review and the tuner."
            },
            theme: {
                label: "Themes", max: LIM.theme, primary: false, custom: true, noun: "theme",
                groups: T.genre_groups, options: T.genres, map: GENRE,
                quick: ["budget", "precon_upgrade", "pet_deck", "battlecruiser", "horror", "meme", "political", "flavor_first"], quickShow: 6,
                placeholder: "Search or add a theme…",
                hint: "Flavor, vibe, why you built it. Up to " + LIM.theme + "; type your own if it's not listed. Labels only: never scored."
            },
            kindred: {
                label: "Kindred", max: LIM.kindred, primary: false, custom: true, noun: "creature type",
                quick: T.kindred.suggestions || [], quickShow: 8, kindredGroups: T.kindred.groups || [],
                placeholder: "Search " + TYPES.length + " creature types…",
                hint: "Creature types the deck cares about. Up to " + LIM.kindred + ". Picking Kindred as a style is separate and optional."
            }
        };

        function labelOf(kind, v) {
            if (kind === "archetype") return ARCH[v] ? ARCH[v].label : v;
            if (kind === "theme") return GENRE[v] ? GENRE[v].label : v;
            return v;
        }
        function isCustom(kind, v) {
            if (kind === "theme") return !GENRE[v];
            if (kind === "kindred") return !TYPE_BY_LOW[low(v)];
            return false;
        }

        // ---- Journal payload mapping ---------------------------------------------------------
        /* archetype = one ordered list, index 0 = primary. excluded_archetypes + custom_themes
           aren't shown here; they ride along untouched in `extras` (like custom_win_conditions). */
        function fromJournal(j) {
            var ap = (j && j.archetype_preferences) || {}, dg = (j && j.deck_genre) || {};
            var arch = ap.primary_archetype ? [ap.primary_archetype] : [];
            (ap.secondary_archetypes || []).forEach(function (s) { if (arch.indexOf(s) === -1) arch.push(s); });
            return { archetype: arch, theme: (dg.genres || []).slice(), kindred: (dg.kindred || []).slice() };
        }
        function toPayload(v, extras) {
            extras = extras || {};
            return {
                archetype_preferences: { primary_archetype: v.archetype[0] || null, secondary_archetypes: v.archetype.slice(1),
                                         excluded_archetypes: extras.excluded_archetypes || [], custom_themes: extras.custom_themes || [] },
                deck_genre: { genres: v.theme.slice(), kindred: v.kindred.slice() }
            };
        }

        // ---- Header strip (view mode). Same markup as partials/deck_head.html genre_strip. ------
        /* Order: primary archetype, first kindred, first theme, then the remaining secondaries,
           kindred, themes. Desktop shows 4 + "+N"; phone 2 + "+N" (CSS picks by breakpoint). */
        function stripItems(v) {
            var a = v.archetype || [], k = v.kindred || [], t = v.theme || [], out = [];
            if (a[0]) out.push({ kind: "archetype", v: a[0] });
            if (k[0]) out.push({ kind: "kindred", v: k[0] });
            if (t[0]) out.push({ kind: "theme", v: t[0] });
            a.slice(1).forEach(function (x) { out.push({ kind: "archetype", v: x }); });
            k.slice(1).forEach(function (x) { out.push({ kind: "kindred", v: x }); });
            t.slice(1).forEach(function (x) { out.push({ kind: "theme", v: x }); });
            return out;
        }
        function stripHtml(v, href) {
            var items = stripItems(v);
            if (!items.length) return "";
            var chips = items.slice(0, 4).map(function (it, i) {
                var tone = it.kind === "archetype" ? "neutral" : isCustom(it.kind, it.v) ? "ghost hud-chip--custom" : "ghost";
                var lab = labelOf(it.kind, it.v);
                return '<span class="hud-chip hud-chip--' + tone + '" data-rank="' + (i + 1) + '" title="' + esc(FIELDS[it.kind].label + ": " + lab) + '"><span>' + esc(lab) + "</span></span>";
            }).join("");
            function more(n, cls, from) {
                if (n <= 0) return "";
                var rest = items.slice(from).map(function (it) { return labelOf(it.kind, it.v); }).join(", ");
                return '<a class="hud-chip hud-chip--neutral ' + cls + '" href="' + esc(href) + '" title="' + esc(rest) + '" aria-label="' + n + " more deck tags: " + esc(rest) + '. Open the Journal.">+' + n + "</a>";
            }
            return chips + more(items.length - 4, "hud-chip-more-desktop", 4) + more(items.length - 2, "hud-chip-more-phone", 2);
        }

        // ---- Deep Review read under Archetype (informational; no mismatch warning) -----------
        function readHtml(genre, href, stale) {
            if (!genre || !genre.primary) return "";
            var parts = [genre.primary].concat(genre.secondary || []).map(function (g) { return "<b>" + esc(g.name) + "</b> " + Math.round(g.coverage * 100) + "%"; });
            return "Deep Review's read (computed from the cards): " + parts.join(" · ") + (stale ? " · stale" : "") + ' · <a href="' + esc(href) + '">Open Review</a>';
        }

        // ---- TagField ----------------------------------------------------------------------------
        /* root: the .hud-field container. value: array (archetype: [primary, ...secondaries]).
           onChange(value) fires after every add/remove/promote. improveFn(kind, text) -> Promise<string>
           (GN-TXT-1): when given, a custom-capable field (theme/kindred) shows an "Improve" button next
           to the input; the suggestion is shown inline for the user to accept (fills the input, not yet
           added as a tag) or discard (input unchanged). Never auto-applies. */
        function TagField(root, kind, value, onChange, improveFn) {
            var cfg = FIELDS[kind], self = this, active = -1, rows = [];
            var id = "gn-" + kind;
            var canImprove = !!(improveFn && cfg.custom);
            this.value = (value || []).slice();
            root.innerHTML =
                '<div class="hud-tagfield-head"><label for="' + id + '-in" id="' + id + '-l">' + cfg.label + "</label>" +
                '<span class="hud-tagfield-count" id="' + id + '-n" data-testid="genre-' + kind + '-count" aria-hidden="true"></span></div>' +
                '<div class="hud-picker" id="' + id + '-box" role="group" aria-labelledby="' + id + '-l" aria-describedby="' + id + '-h">' +
                '<input id="' + id + '-in" data-testid="genre-' + kind + '-input" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="' + id + '-list" autocomplete="off" spellcheck="false" maxlength="' + CUSTOM_MAX + '">' +
                (canImprove ? '<button type="button" class="hud-btn hud-btn--sm hud-tagfield-improve" id="' + id + '-imp" data-testid="genre-' + kind + '-improve" title="Reword with AI">◇ Improve</button>' : "") +
                '<ul class="hud-picker-list" id="' + id + '-list" role="listbox" aria-label="' + cfg.label + ' options" hidden></ul></div>' +
                (canImprove ? '<div class="hud-tagfield-sugg" id="' + id + '-sugg" data-testid="genre-' + kind + '-sugg" hidden></div>' : "") +
                '<div class="hud-tagfield-picks" id="' + id + '-picks"></div>' +
                '<span class="hud-field-hint" id="' + id + '-h">' + esc(cfg.hint) + "</span>" +
                (kind === "archetype" ? '<span class="hud-field-hint hud-tagfield-read" id="gn-read" hidden></span>' : "") +
                '<span class="hud-field-hint hud-tagfield-note" id="' + id + '-note" data-testid="genre-' + kind + '-note" role="status" hidden></span>' +
                '<span class="hud-sr" aria-live="polite" id="' + id + '-live"></span>';
            root.classList.add("hud-tagfield");
            var box = root.querySelector("#" + id + "-box"), input = root.querySelector("#" + id + "-in"),
                list = root.querySelector("#" + id + "-list"), picks = root.querySelector("#" + id + "-picks"),
                count = root.querySelector("#" + id + "-n"), note = root.querySelector("#" + id + "-note"), live = root.querySelector("#" + id + "-live");
            var impBtn = canImprove ? root.querySelector("#" + id + "-imp") : null;
            var sugg = canImprove ? root.querySelector("#" + id + "-sugg") : null;
            var moreOpen = false;

            // ---- AI "Improve" (GN-TXT-1): suggestion is shown, never auto-applied --------------
            function renderImprove() {
                if (!impBtn) return;
                var t = clean(input.value);
                impBtn.disabled = !t || t.length > CUSTOM_MAX;
            }
            function hideSugg() { if (sugg) { sugg.hidden = true; sugg.innerHTML = ""; } }
            function askImprove() {
                if (!impBtn || impBtn.disabled) return;
                var original = clean(input.value);
                if (!original) return;
                impBtn.disabled = true; impBtn.textContent = "Improving…";
                sugg.hidden = false; sugg.innerHTML = '<span class="hud-tagfield-sugg-meta">Improving “' + esc(original) + '”…</span>';
                improveFn(kind, original).then(function (text) {
                    impBtn.disabled = false; impBtn.textContent = "◇ Improve";
                    if (!text || low(text) === low(original)) { hideSugg(); if (!text) say("Couldn't improve that text"); return; }
                    sugg.innerHTML = '<span class="hud-tagfield-sugg-text">“' + esc(text) + '”</span>' +
                        '<span class="hud-tagfield-sugg-a">' +
                        '<button type="button" class="hud-btn hud-btn--sm hud-btn--primary" data-sugg="accept" data-testid="genre-' + kind + '-sugg-accept">Accept</button>' +
                        '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-sugg="discard" data-testid="genre-' + kind + '-sugg-discard">Discard</button></span>';
                    sugg.setAttribute("data-suggested", text);
                }).catch(function (e) {
                    impBtn.disabled = false; impBtn.textContent = "◇ Improve";
                    sugg.innerHTML = '<span class="hud-tagfield-sugg-meta is-error">' + esc((e && e.message) || "Couldn't improve that text") + "</span>";
                });
            }
            if (impBtn) {
                impBtn.textContent = "◇ Improve";
                impBtn.addEventListener("click", askImprove);
                sugg.addEventListener("click", function (e) {
                    var b = e.target.closest("[data-sugg]"); if (!b) return;
                    if (b.getAttribute("data-sugg") === "accept") {
                        input.value = sugg.getAttribute("data-suggested") || input.value;
                        say("Replaced with the AI suggestion"); input.focus();
                    } else { say("Discarded the AI suggestion"); }
                    hideSugg(); renderImprove();
                });
            }

            function say(msg) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); }
            var noteTimer = null;
            function flashNote(msg) {
                note.textContent = msg; note.hidden = false; say(msg);
                clearTimeout(noteTimer); noteTimer = setTimeout(function () { note.hidden = true; }, 3000);
            }
            /* The already-added value the typed text refers to (same normalization the listbox uses), or null. */
            function addedMatch(q) {
                var c = kind === "kindred" ? canonType(q) : canonSeed(cfg.map, q) || clean(q);
                if (!c) return null;
                var hit = self.value.filter(function (x) { return low(x) === low(c); })[0];
                return hit || null;
            }
            function full() { return self.value.length >= cfg.max; }
            function has(v) { return self.value.some(function (x) { return low(x) === low(v); }); }

            function chip(v, i) {
                var lab = labelOf(kind, v), custom = isCustom(kind, v), tone, star = "";
                if (cfg.primary) {
                    tone = i === 0 ? "signal" : "data";
                    star = i === 0 ? '<span class="hud-chip-star is-on" aria-hidden="true">★</span><span class="hud-sr">Primary: </span>'
                                   : '<button class="hud-chip-star" type="button" data-testid="genre-' + kind + '-promote" data-act="primary" aria-label="Make ' + esc(lab) + ' primary" title="Make primary">☆</button>';
                } else tone = custom ? "ghost hud-chip--custom" : "ghost";
                var title = custom ? (kind === "kindred" ? "Custom: not an official creature type" : "Custom theme") : (cfg.map && cfg.map[v] ? cfg.map[v].desc : "");
                return '<span class="hud-chip hud-chip--' + tone + ' hud-tagchip" data-testid="genre-' + kind + '-chip" data-value="' + esc(v) + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + star +
                    '<span class="hud-tagchip-text">' + esc(lab) + "</span>" +
                    '<button class="hud-chip-remove" type="button" data-testid="genre-' + kind + '-remove" data-act="remove" aria-label="Remove ' + esc(lab) + '">×</button></span>';
            }
            function quickHtml() {
                if (full()) return "";
                var free = cfg.quick.filter(function (v) { return !has(v); });
                var head = free.slice(0, cfg.quickShow), rest = free.slice(cfg.quickShow);
                var b = function (v) { return '<button type="button" class="hud-tagopt" data-add="' + esc(v) + '"' + (cfg.map && cfg.map[v] ? ' title="' + esc(cfg.map[v].desc) + '"' : "") + ">" + esc(labelOf(kind, v)) + "</button>"; };
                var h = '<span class="hud-label">Add</span>';
                if (kind === "kindred") {
                    // Rules-defined groups first: one tap inserts every member type (all-or-nothing).
                    cfg.kindredGroups.forEach(function (g) {
                        var add = g.members.filter(function (m) { return !has(m); });
                        var room = cfg.max - self.value.length, ok = add.length && add.length <= room;
                        h += '<button type="button" class="hud-tagopt hud-tagopt--group" data-group="' + esc(g.id) + '"' + (ok ? "" : " disabled") +
                            ' title="' + esc(g.label + " adds " + g.members.join(", ") + (ok ? "" : add.length ? " (needs " + add.length + " free slots, " + room + " left)" : " (all added)")) + '">' +
                            esc(g.label) + " <small>+" + add.length + "</small></button>";
                    });
                }
                h += head.map(b).join("");
                if (kind === "kindred" && rest.length) {
                    h += '<span class="hud-tagopt-rest" id="' + id + '-rest"' + (moreOpen ? "" : " hidden") + ">" + rest.map(b).join("") + "</span>" +
                        '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-act="more" aria-expanded="' + moreOpen + '" aria-controls="' + id + '-rest">' + (moreOpen ? "Less" : "More… " + rest.length) + "</button>";
                }
                return h;
            }
            function render() {
                box.querySelectorAll(".hud-tagchip").forEach(function (c) { c.remove(); });
                input.insertAdjacentHTML("beforebegin", self.value.map(chip).join(""));
                count.textContent = self.value.length + " / " + cfg.max;
                count.classList.toggle("is-full", full());
                input.placeholder = full() ? cfg.max + " of " + cfg.max + " · remove one to add" : (self.value.length ? "Add another…" : cfg.placeholder);
                picks.innerHTML = quickHtml(); picks.hidden = !picks.innerHTML;
                renderImprove();
            }

            // Mutations. Archetype rule: index 0 is the primary; the first add becomes primary.
            function add(v, quiet) {
                if (has(v)) { flash(v); flashNote(labelOf(kind, v) + " is already added"); return false; }
                if (full()) { say("Limit reached: " + cfg.max + " " + cfg.noun + "s"); return false; }
                self.value.push(v);
                if (!quiet) say("Added " + labelOf(kind, v) + (cfg.primary ? (self.value.length === 1 ? " as primary" : " as secondary") : "") + (isCustom(kind, v) ? " (custom)" : ""));
                return true;
            }
            function remove(v) {
                var i = self.value.indexOf(v);
                if (i < 0) return;
                self.value.splice(i, 1);
                say("Removed " + labelOf(kind, v) + (cfg.primary && i === 0 && self.value.length ? ". " + labelOf(kind, self.value[0]) + " is now primary" : ""));
            }
            function promote(v) {
                var i = self.value.indexOf(v); if (i <= 0) return;
                var old = self.value[0];
                self.value.splice(i, 1); self.value.splice(1, 0, old); self.value[0] = v;  // old primary -> first secondary
                say(labelOf(kind, v) + " is now primary. " + labelOf(kind, old) + " moved to secondary");
            }
            function flash(v) {
                var c = Array.prototype.filter.call(box.querySelectorAll(".hud-tagchip"), function (x) { return low(x.getAttribute("data-value")) === low(v); })[0];
                if (!c) return;
                c.classList.add("is-flash"); setTimeout(function () { c.classList.remove("is-flash"); }, 700);
            }
            function commit() { render(); onChange(self.value.slice()); }

            // Search -> listbox rows [{value, html, create?, disabled?} | {group} | {note}]
            function search(q) {
                var s = low(q), out = [];
                if (full()) return [{ note: "Limit reached (" + cfg.max + "). Remove one first." }];
                if (kind === "kindred") {
                    if (!s) return [{ note: "Type to search " + TYPES.length + " creature types, or use a quick pick below." }];
                    var c = canonType(q), seen = {};
                    if (c) { out.push({ rank: 0, value: c, html: esc(c) + (low(c) !== s ? " <small>from “" + esc(q.trim()) + "”</small>" : "") }); seen[c] = 1; }
                    var pre = [], sub = [];
                    TYPES.forEach(function (t) {
                        if (seen[t]) return; var i = t.toLowerCase().indexOf(s);
                        if (i === 0) pre.push(t); else if (i > 0) sub.push(t);
                    });
                    var preSet = {}; pre.forEach(function (t) { preSet[t] = 1; });
                    pre.concat(sub).slice(0, 8 - out.length).forEach(function (t) { out.push({ rank: preSet[t] ? 0 : 1, value: t, html: hl(t, q) }); });
                    // Typo with no catalog hit: offer the nearest creature type first so Enter lands on it, not on a junk custom value.
                    if (!c && !pre.length && !sub.length) {
                        var near = closeType(q);
                        if (near && !has(near)) out.unshift({ rank: 0, value: near, suggest: true, html: "Did you mean <b>" + esc(near) + "</b>? <small>closest creature type to “" + esc(q.trim()) + "”</small>" });
                    }
                    out = out.map(function (r) { if (has(r.value)) { r.disabled = true; r.html += " <small>already added</small>"; } return r; });
                    if (!c && cfg.custom) out.push(createRow(q));
                    return out;
                }
                // Seeded lists: grouped; label prefix, then label/id substring, then description.
                var exact = canonSeed(cfg.map, q);
                cfg.groups.forEach(function (g) {
                    var opts = cfg.options.filter(function (o) { return o.group === g.id; }).map(function (o) {
                        var li = low(o.label).indexOf(s);
                        var rank = !s ? 1 : li === 0 ? 0 : li > 0 || o.id.indexOf(s.replace(/\s+/g, "_")) >= 0 ? 1 : low(o.desc).indexOf(s) >= 0 ? 2 : -1;
                        return { o: o, rank: rank };
                    }).filter(function (x) { return x.rank >= 0; }).sort(function (a, b) { return a.rank - b.rank; });
                    if (!opts.length) return;
                    out.push({ group: g.label });
                    opts.forEach(function (x) {
                        var picked = has(x.o.id);
                        out.push({ rank: x.rank, value: x.o.id, disabled: picked, html: "<span class=\"hud-tagopt-label\">" + hl(x.o.label, q) + (picked ? " · added" : "") + "</span>" + "<small>" + esc(x.o.desc) + "</small>" });
                    });
                });
                if (cfg.custom && s && !exact) out.push(createRow(q));
                if (!out.length) out.push({ note: "No " + cfg.noun + " matches “" + esc(q.trim()) + "”. Styles are a fixed list: try a theme instead." });
                return out;
            }
            function createRow(q) {
                var t = clean(q);
                if (has(t)) return { disabled: true, create: true, html: "“" + esc(t) + "” is already added" };
                return { rank: 3, value: t, create: true, html: "<b>+</b><span>Add “" + esc(t) + "” as a custom " + cfg.noun + "</span>" };
            }
            function positionList() {
                // Touch: keep the listbox fully visible above the sticky save bar.
                // Scroll the field into view (accounting for the save bar height),
                // then cap the list height / flip above the field if it won't fit below.
                var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
                if (!coarse) return;
                var savebar = document.querySelector(".hud-savebar:not([hidden])");
                var savebarH = savebar ? savebar.getBoundingClientRect().height : 0;
                var vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
                var fieldRect = input.closest(".hud-picker").getBoundingClientRect();
                var below = vh - savebarH - fieldRect.bottom;
                var above = fieldRect.top;
                list.style.top = ""; list.style.bottom = ""; list.style.maxHeight = "";
                if (below < 120 && above > below) {
                    list.style.bottom = "calc(100% + 2px)";
                    list.style.top = "auto";
                    list.style.maxHeight = Math.max(120, above - 8) + "px";
                } else {
                    list.style.maxHeight = Math.max(120, below - 8) + "px";
                }
                input.closest(".hud-picker").scrollIntoView({ block: "nearest" });
            }
            function open() {
                rows = search(input.value);
                var n = 0;
                list.innerHTML = rows.map(function (r) {
                    if (r.group) return '<li class="hud-picker-group" role="presentation">' + esc(r.group) + "</li>";
                    if (r.note) return '<li class="hud-picker-note" role="presentation">' + r.note + "</li>";
                    r.idx = n++;
                    return '<li role="option" id="' + id + "-o" + r.idx + '" data-testid="genre-' + kind + '-option" aria-selected="false"' + (r.disabled ? ' aria-disabled="true"' : "") + (r.create ? ' class="is-create"' : "") + ' data-i="' + r.idx + '">' + r.html + "</li>";
                }).join("");
                list.hidden = false; input.setAttribute("aria-expanded", "true");
                active = -1;
                positionList();
                /* Typing highlights the best match so Enter picks it: lowest rank (label prefix, then label
                   substring), first in list order on ties. A description-only hit (rank 2) is never
                   auto-highlighted, so "spel" can't land on Ramp. */
                if (input.value.trim()) {
                    var best = null;
                    rows.forEach(function (r) { if (r.idx != null && !r.disabled && r.rank <= 1 + (r.create ? 2 : 0) && (!best || r.rank < best.rank)) best = r; });
                    if (best) { active = best.idx - 1; move(1); }
                }
            }
            function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
            function opts() { return list.querySelectorAll('[role="option"]'); }
            function move(d) {
                var o = opts(); if (!o.length) return;
                for (var k = 0; k < o.length; k++) {
                    active = (active + d + o.length) % o.length;
                    if (o[active].getAttribute("aria-disabled") !== "true") break;
                }
                if (o[active].getAttribute("aria-disabled") === "true") { active = -1; return; }
                o.forEach(function (x, i) { x.setAttribute("aria-selected", String(i === active)); });
                input.setAttribute("aria-activedescendant", o[active].id);
                o[active].scrollIntoView({ block: "nearest" });
            }
            function pick(i) {
                var r = rows.filter(function (x) { return x.idx === i; })[0];
                if (!r || r.disabled) return;
                if (add(r.value)) { input.value = ""; hideSugg(); commit(); }
                close();
            }

            // Opens on typing, ArrowDown or a pointer click; not on focus, so tabbing through
            // (or focus returning after a chip ×) doesn't pop a 30-row list.
            input.addEventListener("click", function () { if (list.hidden) open(); });
            input.addEventListener("input", function () { open(); renderImprove(); hideSugg(); });
            input.addEventListener("blur", function () { setTimeout(close, 150); });
            input.addEventListener("keydown", function (e) {
                if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) open(); move(1); }
                else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
                else if (e.key === "Enter" && !(e.metaKey || e.ctrlKey)) { e.preventDefault();
                    if (active >= 0) pick(active);
                    else if (input.value.trim() && addedMatch(input.value)) {  // duplicate: nothing to add, so clear + say so
                        var dup = addedMatch(input.value);
                        input.value = ""; close(); flash(dup); flashNote(labelOf(kind, dup) + " is already added");
                    }
                }
                else if (e.key === "Escape") { if (!list.hidden) { e.stopPropagation(); close(); } }
                else if (e.key === "Backspace" && !input.value && self.value.length) {
                    remove(self.value[self.value.length - 1]); commit();
                    if (!list.hidden) open();  // keep an open list in step ("· added" rows free up)
                }
            });
            list.addEventListener("mousedown", function (e) {
                var o = e.target.closest('[role="option"]'); if (!o) return;
                e.preventDefault(); pick(Number(o.getAttribute("data-i")));
            });
            root.addEventListener("click", function (e) {
                var b = e.target.closest("button"); if (!b || b.disabled || !root.contains(b)) return;
                var c = b.closest(".hud-tagchip"), act = b.getAttribute("data-act");
                if (act === "remove" && c) { remove(c.getAttribute("data-value")); commit(); input.focus(); }
                else if (act === "primary" && c) {
                    var v = c.getAttribute("data-value"); promote(v); commit();
                    input.focus();
                }
                else if (act === "more") { moreOpen = !moreOpen; render(); var m = picks.querySelector('[data-act="more"]'); if (m) m.focus(); }
                else if (b.hasAttribute("data-add")) {
                    if (add(b.getAttribute("data-add"))) commit();
                    var next = picks.querySelector(".hud-tagopt:not(:disabled)"); (next || input).focus();
                } else if (b.hasAttribute("data-group")) {
                    var g = cfg.kindredGroups.filter(function (x) { return x.id === b.getAttribute("data-group"); })[0];
                    var addm = g.members.filter(function (m) { return !has(m); });
                    if (!addm.length || addm.length > cfg.max - self.value.length) return;  // all-or-nothing
                    addm.forEach(function (m) { add(m, true); });
                    say("Added " + g.label + ": " + addm.join(", ")); commit(); input.focus();
                }
            });

            this.set = function (v) { self.value = (v || []).slice(); render(); };
            this.openWith = function (q) { input.focus(); input.value = q; open(); };
            render();
        }

        return { FIELDS: FIELDS, closeType: closeType, canonType: canonType, canonSeed: canonSeed, labelOf: labelOf, isCustom: isCustom,
                 fromJournal: fromJournal, toPayload: toPayload, stripItems: stripItems, stripHtml: stripHtml,
                 readHtml: readHtml, TagField: TagField, esc: esc };
    }

    root.DeckGenre = { create: create };
    if (typeof module !== "undefined" && module.exports) module.exports = { create: create };
})(typeof window !== "undefined" ? window : this);
