/* Deck Genre (GN-D) mockup: reference behaviour for the journal's Genre section.
   Opens from disk; needs ../../../static/css/hud.css + taxonomy.js + creature_types.js.
   The TagField below is written to be ported: plain DOM, no framework, ES5 like
   templates/deck_journal.html. Everything under "MOCK" is fixture/shell only. */
(function () {
    "use strict";
    var T = window.TAXONOMY, TYPES = window.CREATURE_TYPES;
    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    function low(s) { return String(s || "").trim().toLowerCase(); }
    var CUSTOM_MAX = 32;

    // ---- Option sources ------------------------------------------------------------
    function byId(list) { var m = {}; list.forEach(function (o) { m[o.id] = o; }); return m; }
    var ARCH = byId(T.archetypes), GENRE = byId(T.genres);
    var TYPE_BY_LOW = {}; TYPES.forEach(function (t) { TYPE_BY_LOW[t.toLowerCase()] = t; });
    var ALIAS = {}; Object.keys(T.kindred.aliases).forEach(function (k) { ALIAS[k.toLowerCase()] = T.kindred.aliases[k]; });

    /* Kindred normalization (DECK_GENRE_TAXONOMY.md §3): trim, case-insensitive,
       alias map, then strip a trailing "es"/"s" and re-match. Returns the catalog
       spelling or null. */
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
    function hl(text, q) {
        var t = String(q || "").trim(), i = low(text).indexOf(low(t));
        if (!t || i < 0) return '<span class="hud-tagopt-label">' + esc(text) + '</span>';
        return '<span class="hud-tagopt-label">' + esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + t.length)) + "</mark>" + esc(text.slice(i + t.length)) + '</span>';
    }

    /* Field configs. kind decides chips, search and rules. */
    var FIELDS = {
        archetype: {
            label: "Archetype", max: 4, primary: true, custom: false, noun: "archetype",
            groups: T.archetype_groups, options: T.archetypes, map: ARCH,
            quick: ["tokens", "aristocrats", "voltron", "spellslinger", "counters", "ramp", "combo", "control", "graveyard", "blink"], quickShow: 6,
            placeholder: "Search 30 archetypes…",
            hint: "How the deck plays. The first is the primary (★); up to 3 more. Feeds Deep Review and the tuner."
        },
        theme: {
            label: "Themes", max: 5, primary: false, custom: true, noun: "theme",
            groups: T.genre_groups, options: T.genres, map: GENRE,
            quick: ["budget", "precon_upgrade", "pet_deck", "battlecruiser", "horror", "meme", "political", "flavor_first"], quickShow: 6,
            placeholder: "Search or add a theme…",
            hint: "Flavor, vibe, why you built it. Up to 5; type your own if it's not listed. Labels only: never scored."
        },
        kindred: {
            label: "Kindred", max: 5, primary: false, custom: true, noun: "creature type",
            quick: T.kindred.suggestions, quickShow: 8, kindredGroups: T.kindred.groups,
            placeholder: "Search 351 creature types…",
            hint: "Creature types the deck cares about. Up to 5. Picking Kindred as an archetype is separate and optional."
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

    // ---- TagField --------------------------------------------------------------------
    /* root: the .hud-field container. value: array (archetype: [primary, ...secondaries]).
       onChange(value) fires after every add/remove/promote. */
    function TagField(root, kind, value, onChange) {
        var cfg = FIELDS[kind], self = this, active = -1, rows = [];
        var id = "gn-" + kind;
        this.value = (value || []).slice();
        root.innerHTML =
            '<div class="hud-tagfield-head"><label for="' + id + '-in" id="' + id + '-l">' + cfg.label + "</label>" +
            '<span class="hud-tagfield-count" id="' + id + '-n" aria-hidden="true"></span></div>' +
            '<div class="hud-picker" id="' + id + '-box" role="group" aria-labelledby="' + id + '-l" aria-describedby="' + id + '-h">' +
            '<input id="' + id + '-in" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="' + id + '-list" autocomplete="off" spellcheck="false" maxlength="' + CUSTOM_MAX + '">' +
            '<ul class="hud-picker-list" id="' + id + '-list" role="listbox" aria-label="' + cfg.label + ' options" hidden></ul></div>' +
            '<div class="hud-tagfield-picks" id="' + id + '-picks"></div>' +
            '<span class="hud-field-hint" id="' + id + '-h">' + cfg.hint + "</span>" +
            (kind === "archetype" ? '<span class="hud-field-hint hud-tagfield-read" id="gn-read" hidden></span>' : "") +
            '<span class="hud-sr" aria-live="polite" id="' + id + '-live"></span>';
        root.classList.add("hud-tagfield");
        var box = root.querySelector("#" + id + "-box"), input = root.querySelector("#" + id + "-in"),
            list = root.querySelector("#" + id + "-list"), picks = root.querySelector("#" + id + "-picks"),
            count = root.querySelector("#" + id + "-n"), live = root.querySelector("#" + id + "-live");
        var moreOpen = false;

        function say(msg) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); }
        function full() { return self.value.length >= cfg.max; }
        function has(v) { return self.value.some(function (x) { return low(x) === low(v); }); }

        function chip(v, i) {
            var lab = labelOf(kind, v), custom = isCustom(kind, v), tone, star = "";
            if (cfg.primary) {
                tone = i === 0 ? "signal" : "data";
                star = i === 0 ? '<button class="hud-chip-star is-on" type="button" aria-pressed="true" aria-disabled="true" data-testid="genre-' + kind + '-promote">★<span class="hud-sr">Primary: </span></button>'
                               : '<button class="hud-chip-star" type="button" data-testid="genre-' + kind + '-promote" data-act="primary" aria-label="Make ' + esc(lab) + ' primary" title="Make primary">☆</button>';
            } else tone = custom ? "ghost hud-chip--custom" : "ghost";
            var title = custom ? (kind === "kindred" ? "Custom: not an official creature type" : "Custom theme") : (cfg.map && cfg.map[v] ? cfg.map[v].desc : "");
            return '<span class="hud-chip hud-chip--' + tone + ' hud-tagchip" data-value="' + esc(v) + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + star +
                '<span class="hud-tagchip-text">' + esc(lab) + "</span>" +
                '<button class="hud-chip-remove" type="button" data-act="remove" aria-label="Remove ' + esc(lab) + '">×</button></span>';
        }
        function quickHtml() {
            if (full()) return "";
            var free = cfg.quick.filter(function (v) { return !has(v); });
            var head = free.slice(0, cfg.quickShow), rest = free.slice(cfg.quickShow);
            var b = function (v) { return '<button type="button" class="hud-tagopt" data-add="' + esc(v) + '"' + (cfg.map && cfg.map[v] ? ' title="' + esc(cfg.map[v].desc) + '"' : "") + ">" + esc(labelOf(kind, v)) + "</button>"; };
            var h = '<span class="hud-label">Add</span>';
            if (kind === "kindred") {
                // Rules-defined groups first: one tap inserts every member type.
                cfg.kindredGroups.forEach(function (g) {
                    var add = g.members.filter(function (m) { return !has(m); });
                    var room = cfg.max - self.value.length, ok = add.length && add.length <= room;
                    h += '<button type="button" class="hud-tagopt hud-tagopt--group" data-group="' + g.id + '"' + (ok ? "" : " disabled") +
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
        }

        // Mutations. Archetype rule: index 0 is the primary; first add becomes primary.
        function add(v, quiet) {
            if (has(v)) { flash(v); say(labelOf(kind, v) + " is already added"); return false; }
            if (full()) { say("Limit reached: " + cfg.max + " " + cfg.noun + "s"); return false; }
            self.value.push(v);
            if (!quiet) say("Added " + labelOf(kind, v) + (cfg.primary ? (self.value.length === 1 ? " as primary" : " as secondary") : "") + (isCustom(kind, v) ? " (custom)" : ""));
            return true;
        }
        function remove(v) {
            var i = self.value.findIndex(function (x) { return x === v; });
            if (i < 0) return;
            self.value.splice(i, 1);
            say("Removed " + labelOf(kind, v) + (cfg.primary && i === 0 && self.value.length ? ". " + labelOf(kind, self.value[0]) + " is now primary" : ""));
        }
        function promote(v) {
            var i = self.value.indexOf(v); if (i <= 0) return;
            var old = self.value[0];
            self.value.splice(i, 1); self.value.splice(1, 0, old); self.value[0] = v;  // old primary becomes the first secondary
            say(labelOf(kind, v) + " is now primary. " + labelOf(kind, old) + " moved to secondary");
        }
        function flash(v) {
            var c = box.querySelector('.hud-tagchip[data-value="' + CSS.escape(v) + '"]'); if (!c) return;
            c.classList.add("is-flash"); setTimeout(function () { c.classList.remove("is-flash"); }, 700);
        }
        function commit() { render(); onChange(self.value.slice()); }

        // Search -> listbox rows [{value, html, create?, disabled?}]
        function search(q) {
            var s = low(q), out = [];
            if (full()) return [{ note: "Limit reached (" + cfg.max + "). Remove one first." }];
            if (kind === "kindred") {
                if (!s) return [{ note: "Type to search 351 creature types, or use a quick pick below." }];
                var c = canonType(q), seen = {};
                if (c) { out.push({ value: c, html: esc(c) + (low(c) !== s ? " <small>from “" + esc(q.trim()) + "”</small>" : "") }); seen[c] = 1; }
                var pre = [], sub = [];
                TYPES.forEach(function (t) {
                    if (seen[t]) return; var i = t.toLowerCase().indexOf(s);
                    if (i === 0) pre.push(t); else if (i > 0) sub.push(t);
                });
                pre.concat(sub).slice(0, 8 - out.length).forEach(function (t) { out.push({ value: t, html: hl(t, q) }); });
                out = out.map(function (r) { if (has(r.value)) { r.disabled = true; r.html += " <small>already added</small>"; } return r; });
                if (!c && cfg.custom) out.push(createRow(q));
                return out;
            }
            // Seeded lists: grouped; label/id matches first, description matches after.
            var exact = canonSeed(cfg.map, q);
            cfg.groups.forEach(function (g) {
                var opts = cfg.options.filter(function (o) { return o.group === g.id; }).map(function (o) {
                    var rank = !s ? 1 : low(o.label).indexOf(s) === 0 ? 0 : low(o.label).indexOf(s) > 0 || o.id.indexOf(s.replace(/\s+/g, "_")) >= 0 ? 1 : low(o.desc).indexOf(s) >= 0 ? 2 : -1;
                    return { o: o, rank: rank };
                }).filter(function (x) { return x.rank >= 0; }).sort(function (a, b) { return a.rank - b.rank; });
                if (!opts.length) return;
                out.push({ group: g.label });
                opts.forEach(function (x) {
                    var picked = has(x.o.id);
                    out.push({ value: x.o.id, disabled: picked, html: hl(x.o.label, q) + (picked ? " · added" : "") + "<small>" + esc(x.o.desc) + "</small>" });
                });
            });
            if (cfg.custom && s && !exact) out.push(createRow(q));
            if (!out.length) out.push({ note: "No " + cfg.noun + " matches “" + esc(q.trim()) + "”. Archetypes are a fixed list: try a theme instead." });
            return out;
        }
        function createRow(q) {
            var t = q.trim().replace(/\s+/g, " ");
            if (has(t)) return { disabled: true, create: true, html: "“" + esc(t) + "” is already added" };
            return { value: t, create: true, html: "<b>+</b><span>Add “" + esc(t) + "” as a custom " + cfg.noun + "</span>" };
        }
        function open() {
            rows = search(input.value);
            var n = 0;
            list.innerHTML = rows.map(function (r) {
                if (r.group) return '<li class="hud-picker-group" role="presentation">' + esc(r.group) + "</li>";
                if (r.note) return '<li class="hud-picker-note" role="presentation">' + r.note + "</li>";
                r.idx = n++;
                return '<li role="option" id="' + id + "-o" + r.idx + '" aria-selected="false"' + (r.disabled ? ' aria-disabled="true"' : "") + (r.create ? ' class="is-create"' : "") + ' data-i="' + r.idx + '">' + r.html + "</li>";
            }).join("");
            list.hidden = false; input.setAttribute("aria-expanded", "true");
            
            // On touch devices, ensure list is visible above the save bar and on screen
            if (window.matchMedia("(pointer: coarse)").matches) {
                var rect = box.getBoundingClientRect();
                var saveBar = document.getElementById("hudSaveBar") || document.getElementById("journalSaveBar");
                var saveHeight = saveBar && !saveBar.hidden ? saveBar.offsetHeight : 0;
                var spaceBelow = window.innerHeight - rect.bottom - saveHeight;
                
                if (spaceBelow < 200 && rect.top > 250) {
                    list.style.bottom = "100%";
                    list.style.top = "auto";
                    list.style.maxHeight = (rect.top - 20) + "px";
                } else {
                    list.style.top = "calc(100% + 2px)";
                    list.style.bottom = "auto";
                    list.style.maxHeight = Math.max(100, spaceBelow - 20) + "px";
                    box.scrollIntoView({ behavior: "smooth", block: "center" });
                }
            }
            
            active = -1;
            // Typing makes the first enabled option active, so Enter picks the best match.
            if (input.value.trim()) move(1);
        }
        function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
        function opts() { return list.querySelectorAll('[role="option"]'); }
        function move(d) {
            var o = opts(); if (!o.length) return;
            for (var k = 0; k < o.length; k++) {
                active = (active + d + o.length) % o.length;
                if (o[active].getAttribute("aria-disabled") !== "true") break;
            }
            o.forEach(function (x, i) { x.setAttribute("aria-selected", String(i === active)); });
            input.setAttribute("aria-activedescendant", o[active].id);
            o[active].scrollIntoView({ block: "nearest" });
        }
        function pick(i) {
            var r = rows.filter(function (x) { return x.idx === i; })[0];
            if (!r || r.disabled) return;
            if (add(r.value)) { input.value = ""; commit(); }
            close();
        }

        // Opens on typing, ArrowDown or a pointer click; not on focus, so tabbing
        // through (or focus returning after a chip ×) doesn't pop a 30-row list.
        input.addEventListener("click", function () { if (list.hidden) open(); });
        input.addEventListener("input", open);
        input.addEventListener("blur", function () { setTimeout(close, 150); });
        input.addEventListener("keydown", function (e) {
            if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) open(); move(1); }
            else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
            else if (e.key === "Enter") { e.preventDefault(); if (active >= 0) pick(active); }
            else if (e.key === "Escape") { if (!list.hidden) { e.stopPropagation(); close(); } }
            else if (e.key === "Backspace" && !input.value && self.value.length) { remove(self.value[self.value.length - 1]); commit(); }
        });
        list.addEventListener("mousedown", function (e) {
            var o = e.target.closest('[role="option"]'); if (!o) return;
            e.preventDefault(); pick(Number(o.getAttribute("data-i")));
        });
        root.addEventListener("click", function (e) {
            var b = e.target.closest("button"); if (!b || b.disabled || !root.contains(b)) return;
            var c = b.closest(".hud-tagchip"), act = b.getAttribute("data-act");
            if (act === "remove" && c) { remove(c.getAttribute("data-value")); commit(); input.focus(); }
            else if (act === "primary" && c) { var v = c.getAttribute("data-value"); promote(v); commit(); var s = box.querySelector('.hud-tagchip[data-value="' + CSS.escape(v) + '"] .hud-chip-star'); if (s) s.focus(); }
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

    // ---- Journal payload mapping (what collect()/fill() do in deck_journal.html) -------
    /* archetype_preferences keeps excluded_archetypes + custom_themes untouched
       (carried in `extras`, like custom_win_conditions today). */
    function toPayload(v, extras) {
        return {
            archetype_preferences: { primary_archetype: v.archetype[0] || null, secondary_archetypes: v.archetype.slice(1),
                                     excluded_archetypes: extras.excluded_archetypes, custom_themes: extras.custom_themes },
            deck_genre: { genres: v.theme.slice(), kindred: v.kindred.slice() }
        };
    }

    // ---- Header strip (view mode) --------------------------------------------------------
    /* Order: primary archetype, first kindred, first theme, then the rest of the
       secondaries, kindred, themes. Desktop shows 4 + "+N"; phone 2 + "+N". */
    function stripItems(v) {
        var a = v.archetype, k = v.kindred, t = v.theme, out = [];
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
            return '<a class="hud-chip hud-chip--neutral ' + cls + '" href="' + href + '" title="' + esc(rest) + '" aria-label="' + n + " more genre tags: " + esc(rest) + '. Open the Journal.">+' + n + "</a>";
        }
        return chips + more(items.length - 4, "hud-chip-more-desktop", 4) + more(items.length - 2, "hud-chip-more-phone", 2);
    }

    // ---- Deep Review read under Archetype -------------------------------------------------
    function readHtml(review) {
        if (!review || !review.primary) return "";
        var parts = [review.primary].concat(review.secondary || []).map(function (g) { return "<b>" + esc(g.name) + "</b> " + Math.round(g.coverage * 100) + "%"; });
        return "Deep Review's read (computed from the cards): " + parts.join(" · ") + ' · <a href="#review">Open Review</a>';
    }

    // ======================================================================================
    // MOCK: states + page shell
    // ======================================================================================
    var STATES = {
        empty:   { label: "Empty (new deck)", v: { archetype: [], theme: [], kindred: [] }, review: null },
        set:     { label: "Set (typical)", v: { archetype: ["aristocrats", "tokens"], theme: ["horror", "budget"], kindred: ["Zombie", "Vampire"] },
                   review: { primary: { name: "Aristocrats", coverage: 0.72 }, secondary: [{ name: "Tokens", coverage: 0.54 }] } },
        full:    { label: "Full + long + custom", v: { archetype: ["voltron", "extra_combats", "cheat", "landfall"],
                                                       theme: ["crossover", "challenge", "flavor_first", "Space western, but gothic", "pet_deck"],
                                                       kindred: ["Dragon", "Dinosaur", "Shapeshifter", "Eldrazi", "Changeling (any type)"] },
                   review: { primary: { name: "Voltron", coverage: 0.81 }, secondary: [] } },
        search:  { label: "Theme search + custom", v: { archetype: ["spellslinger"], theme: ["chaos"], kindred: ["Wizard"] }, review: null, open: ["theme", "noir"] },
        kindred: { label: "Kindred alias", v: { archetype: ["tribal", "counters"], theme: ["nature"], kindred: ["Squirrel"] }, review: null, open: ["kindred", "elves"] },
        browse:  { label: "Archetype list", v: { archetype: ["aristocrats"], theme: [], kindred: [] }, review: null, open: ["archetype", ""] }
    };
    var params = new URLSearchParams(location.search);
    var key = STATES[params.get("state")] ? params.get("state") : "set";
    var st = STATES[key];

    document.getElementById("mockStates").innerHTML = "State:" + Object.keys(STATES).map(function (k) {
        return '<a href="?state=' + k + '"' + (k === key ? ' class="is-on" aria-current="true"' : "") + ">" + STATES[k].label + "</a>";
    }).join("");

    var saved = JSON.parse(JSON.stringify(st.v)), cur = JSON.parse(JSON.stringify(st.v));
    var extras = { excluded_archetypes: [], custom_themes: [] };
    var strip = document.getElementById("deckGenre"), bar = document.getElementById("savebar"), sum = document.getElementById("saveSummary");
    function paintStrip() { strip.innerHTML = stripHtml(saved, "#j-genre"); strip.hidden = !strip.innerHTML; }
    function dirty() { return JSON.stringify(cur) !== JSON.stringify(saved); }
    function changed() {
        bar.classList.toggle("is-dirty", dirty());
        sum.innerHTML = dirty() ? "<b>Unsaved · genre</b>" : "Saved · edit anytime";
        document.getElementById("payload").textContent = JSON.stringify(toPayload(cur, extras), null, 1);
    }
    var fields = {};
    ["archetype", "theme", "kindred"].forEach(function (k) {
        fields[k] = new TagField(document.getElementById("f-" + k), k, cur[k], function (val) { cur[k] = val; changed(); });
    });
    var read = document.getElementById("gn-read");
    read.innerHTML = readHtml(st.review); read.hidden = !read.innerHTML;
    document.getElementById("saveBtn").addEventListener("click", function () {
        saved = JSON.parse(JSON.stringify(cur)); paintStrip(); changed(); sum.textContent = "Saved just now · edit anytime";
    });
    document.getElementById("discardBtn").addEventListener("click", function () {
        cur = JSON.parse(JSON.stringify(saved)); Object.keys(fields).forEach(function (k) { fields[k].set(cur[k]); }); changed(); sum.textContent = "Changes discarded";
    });
    paintStrip(); changed();
    if (st.open) setTimeout(function () { fields[st.open[0]].openWith(st.open[1]); }, 50);
})();
