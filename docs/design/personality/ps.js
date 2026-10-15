/* Commander Lab PS-D mockups: shared shell, renderers, states. Docs only, NOT app code.
 * Opens straight from disk (loads ../../../static/css/hud.css + hud.js + fixtures.js).
 *
 * REAL: every deck's five scores, leans_toward, runner_up, journal flag and
 * notes (fixtures.js: the PS-1 hand calculator run over live-app snapshots,
 * 2026-10-15), full evidence for the 6 showcase decks, deck names, colours,
 * status and commander art (/api/decks, v0.4.34).
 * ILLUSTRATIVE: the profile headline/callout thresholds (PS-3 owns those rules;
 * the mock uses placeholder ones, marked below), the failed/loading states, and
 * timestamps.
 */
(function () {
    "use strict";
    var H = window.HUD, esc = H.esc;
    var AX = ["timmy", "johnny", "spike", "vorthos", "melvin"];
    var AXIS = {
        timmy:   { name: "Timmy",   mono: "T", key: "t", who: "Big, splashy moments" },
        johnny:  { name: "Johnny",  mono: "J", key: "j", who: "Shows off a build" },
        spike:   { name: "Spike",   mono: "S", key: "s", who: "Plays to win" },
        vorthos: { name: "Vorthos", mono: "V", key: "v", who: "The deck tells a story" },
        melvin:  { name: "Melvin",  mono: "M", key: "m", who: "Loves the mechanics" }
    };
    // Copy table: how each signal's raw `value` reads in the UI (UX_SPEC §3.4).
    var FMT = {
        T1: function (v) { return v + " creatures at mv 6+"; },
        T2: function (v) { return v + " are creatures"; },
        T3: function (v) { return v + " ramp pieces"; },
        T4: function (v) { return v + " splashy cards"; },
        T5: function (v) { return v + " interaction · fewer scores higher"; },
        J1: function (v) { return v + (v === 1 ? " combo" : " combos") + " in deck"; },
        J2: function (v) { return v + " edges per card"; },
        J3: function (v) { return v + " under 15% on EDHREC"; },
        J4: function (v) { return Math.round(v * 100) + "% touch a commander"; },
        J5: function (v) { return Math.round(v * 100) + "% sit in a chain"; },
        S1: function (v) { return "power " + v; },
        S2: function (v) { return v + (v === 1 ? " tutor" : " tutors"); },
        S3: function (v) { return v + " fast mana"; },
        S4: function (v) { return v + " interaction pieces"; },
        S5: function (v) { return "avg mv " + v + " · lower scores higher"; },
        S6: function (v) { return v + " combos of 3 cards or fewer"; },
        S7: function (v) { return v + " Game Changer" + (v === 1 ? "" : "s"); },
        V1: function (v) { return v + " share a theme"; },
        V2: function (v) { return v + " legendary"; },
        V3: function (v) { return v + " name a commander"; },
        V4: function (v) { return v + " story links"; },
        M1: function (v) { return v + " unusual mechanics"; },
        M2: function (v) { return v + " move rules objects"; },
        M3: function (v) { return v + (v === 1 ? " loop" : " loops"); },
        M4: function (v) { return v + " cheap cantrips"; },
        M5: function (v) { return v + " odd types or rules"; }
    };
    var CARD_CAP = 6; // card names shown per signal before "+N more"
    // Journal evidence `fields` are machine strings (PERSONALITY_SPEC §7). The UI shows them as sentences.
    var FIELD_COPY = {
        "preferred_win_conditions.primary": "Primary win con",
        "preferred_win_conditions.secondary": "Secondary win con",
        "preferred_win_conditions.disliked_or_excluded": "Excluded win con",
        "archetype_preferences.primary_archetype": "Archetype",
        "archetype_preferences.secondary_archetypes": "Secondary archetype",
        "target_power_level.scale": "Target power",
        "target_bracket": "Target bracket",
        "novelties (non-empty)": "You listed novelties"
    };
    function fieldCopy(f) {
        var m = f.match(/^([^=]+)=(.*)$/);
        if (m && FIELD_COPY[m[1]]) return FIELD_COPY[m[1]] + ": " + m[2].replace(/_/g, " ");
        if (/^text: /.test(f)) return "Words in your notes: " + f.slice(6).split(",").map(function (w) { return "“" + w + "”"; }).join(", ");
        return FIELD_COPY[f] || f.replace(/_/g, " ");
    }

    var DECKS = window.PS_DECKS || [];
    function byId(id) { return DECKS.filter(function (d) { return d.id === id; })[0]; }
    function deckHref(d) { return "#deck-" + d.id; } // product: /deck/{name|urlencode}

    // ---------------------------------------------------------------- params
    function param(k, def) { var m = location.hash.match(new RegExp("[#&]" + k + "=([^&]+)")); return m ? decodeURIComponent(m[1]) : def; }
    function setParam(k, v) {
        var h = location.hash.replace(/^#/, "").split("&").filter(Boolean).filter(function (p) { return p.split("=")[0] !== k; });
        h.push(k + "=" + encodeURIComponent(v));
        location.hash = h.join("&");
    }

    // ---------------------------------------------------------------- lean logic (read-only: from PS-1 §8)
    function rank(scores) { return AX.slice().sort(function (a, b) { return (scores[b] - scores[a]) || (AX.indexOf(a) - AX.indexOf(b)); }); }
    function leanInfo(d) {
        var s = d.scores, r = rank(s);
        if (d.leans === "balanced") {
            if (s[r[0]] < 35) return { kind: "low", top: r[0], r: r };
            return { kind: "bunched", top: r[0], r: r, spread: s[r[0]] - s[r[2]] };
        }
        return { kind: d.runner ? "close" : "clear", top: d.leans, runner: d.runner, r: r, gap: d.runner ? s[d.leans] - s[d.runner] : s[r[0]] - s[r[1]] };
    }
    function leanSentence(d, li) {
        li = li || leanInfo(d);
        var s = d.scores, n = function (a) { return AXIS[a].name; };
        if (li.kind === "low") return { head: "Nothing stands out", why: "Top score is <b>" + n(li.top) + " " + s[li.top] + "</b>, below 35 on every axis." };
        if (li.kind === "bunched") return { head: "Balanced", why: "<b>" + n(li.r[0]) + ", " + n(li.r[1]) + " and " + n(li.r[2]) + "</b> are within " + li.spread + " points." };
        if (li.kind === "close") return { head: "Leans <em>" + n(li.top) + "</em>", why: "Close second <b>" + n(li.runner) + "</b>, " + li.gap + " point" + (li.gap === 1 ? "" : "s") + " behind." };
        return { head: "Leans <em>" + n(li.top) + "</em>", why: AXIS[li.top].who + ". " + li.gap + " points clear of " + n(li.r[1]) + "." };
    }

    // ---------------------------------------------------------------- profile aggregation (mock of PS-3; thresholds ILLUSTRATIVE)
    function profile(scope) {
        var pool = DECKS.filter(function (d) { return scope === "all" || d.status === "physical"; });
        var allMeta = (window.PS_ALL_DECKS || []).filter(function (d) { return scope === "all" || d.status === "physical"; });
        var scored = pool.filter(function (d) { return d.scores; });
        var n = scored.length, avg = {}, leans = { balanced: 0 }, hi = {}, lo = {};
        AX.forEach(function (a) {
            leans[a] = 0;
            avg[a] = n ? Math.floor(scored.reduce(function (t, d) { return t + d.scores[a]; }, 0) / n + 0.5) : 0;
            hi[a] = scored.slice().sort(function (x, y) { return y.scores[a] - x.scores[a]; })[0];
            lo[a] = scored.slice().sort(function (x, y) { return x.scores[a] - y.scores[a]; })[0];
        });
        scored.forEach(function (d) { leans[d.leans] += 1; });
        var r = rank(avg);
        var mostLean = AX.slice().sort(function (a, b) { return (leans[b] - leans[a]) || (AX.indexOf(a) - AX.indexOf(b)); })[0];
        var notScored = allMeta.filter(function (m) { return !pool.some(function (d) { return d.id === m.id; }); });
        return { scope: scope, n: n, total: pool.length + notScored.length, notScored: notScored, avg: avg, rank: r, leans: leans, hi: hi, lo: lo,
                 mostLean: mostLean, journals: scored.filter(function (d) { return d.journal; }).length, decks: scored,
                 degraded: scored.filter(function (d) { return (d.notes || []).some(function (x) { return /without metadata/.test(x); }); }).length };
    }

    // ---------------------------------------------------------------- shell (mirrors base.html so hud.css styles it for real)
    var NAV = [
        ["Home", '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>'],
        ["Commander Decks", '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>', "deck"],
        ["Player Profile", '<polygon points="12 3 21 9.5 17.5 20 6.5 20 3 9.5"></polygon><polygon points="12 8 16 11 14.5 16 9.5 16 8 11"></polygon>', "profile"],
        ["Inventory", '<rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line>'],
        ["Instances", '<circle cx="9" cy="7" r="4"></circle><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>'],
        ["Tag Management", '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path>']
    ];
    function shell(active) {
        var items = NAV.map(function (n) {
            var on = n[2] === active;
            var href = n[2] === "profile" ? "profile.html" : n[2] === "deck" ? "b-sheet.html" : "#";
            return '<a href="' + href + '" class="nav-item' + (on ? ' active" aria-current="page' : "") + '" title="' + n[0] + '">' +
                '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' + n[1] + "</svg>" +
                '<span class="nav-label">' + n[0] + "</span></a>";
        }).join("");
        var nav = document.createElement("nav");
        nav.className = "sidebar"; nav.setAttribute("aria-label", "Primary");
        nav.innerHTML = '<a href="#" class="brand"><svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg><span class="brand-label">Cmdr Lab</span></a>' +
            '<div class="nav-section-label">Navigate</div><div class="nav-items">' + items + "</div>" +
            '<div class="sidebar-footer"><span class="sidebar-footer-label">Version</span><span class="version-badge">v0.4.34</span></div>';
        document.body.classList.add("app-shell");
        document.body.insertBefore(nav, document.body.firstChild);
    }

    // ---------------------------------------------------------------- small pieces
    function mono(a) { return '<span class="ps-mono" aria-hidden="true">' + AXIS[a].mono + "</span>"; }
    function nudge(v, journalUsed, long) {
        if (!journalUsed) return long ? '<span class="ps-nudge is-none">cards only</span>' : "";
        var r = Math.round(v);  // tiles show whole points; the drawer shows the exact nudge
        if (!r) return '<span class="ps-nudge is-none" title="Journal nudge: ' + (Math.round(v * 10) / 10) + '">±0</span>';
        return '<span class="ps-nudge ' + (r > 0 ? "is-up" : "is-down") + '" title="Journal nudge (capped at ±10): ' + (Math.round(v * 10) / 10) + '">' + (r > 0 ? "+" : "−") + Math.abs(r) + '<span class="ps-sr"> from journal</span></span>';
    }
    function meter(score, i, ref) {
        return '<span class="ps-meter" role="presentation">' +
            '<span class="ps-meter-fill" style="width:' + score + "%;--i:" + (i || 0) + '"></span>' +
            (ref != null ? '<span class="ps-meter-ref" style="left:' + ref + '%" title="Your average: ' + ref + '"></span>' : "") + "</span>";
    }
    function axisNudge(d, a) { return d.detail ? d.detail[a].journal_nudge : 0; }
    function leanBlock(d, tag) {
        var s = leanSentence(d);
        tag = tag || "h3";
        return '<div class="ps-lean"><' + tag + ' class="ps-lean-name">' + s.head + "</" + tag + '><span class="ps-lean-why">' + s.why + "</span></div>";
    }
    function classes(d, a) {
        var li = leanInfo(d), c = [];
        if (d.leans === a) c.push("is-lean");
        if (d.runner === a) c.push("is-runner");
        if (li.r[0] === a) c.push("is-top");
        return c.join(" ");
    }
    function srSummary(d) {
        var s = leanSentence(d);
        return AX.map(function (a) { return AXIS[a].name + " " + d.scores[a]; }).join(", ") + ". " + s.head.replace(/<[^>]+>/g, "") + ".";
    }

    // ---------------------------------------------------------------- A · bars
    function renderA(d, ref) {
        return '<ul class="ps-a" role="list">' + AX.map(function (a, i) {
            return '<li class="ps-a-row ' + classes(d, a) + '"><button type="button" class="ps-a-btn" data-axis="' + a + '" aria-expanded="false" aria-controls="psEv">' +
                mono(a) + '<span class="ps-a-name"><b>' + AXIS[a].name + "</b><span>" + AXIS[a].who + "</span></span>" +
                meter(d.scores[a], i, ref ? ref[a] : null) +
                '<span class="ps-a-val">' + d.scores[a] + '</span><span class="ps-a-nudge">' + nudge(axisNudge(d, a), d.journal) + "</span></button></li>";
        }).join("") + "</ul>";
    }
    // ---------------------------------------------------------------- B · character sheet
    function renderB(d, ref, opts) {
        opts = opts || {};
        var li = leanInfo(d);
        return '<div class="ps-b' + (d.leans === "balanced" ? " is-balanced" : "") + (d.runner ? " is-close" : "") + '" role="group" aria-label="Ability scores">' + AX.map(function (a, i) {
            return '<button type="button" class="ps-b-tile ' + classes(d, a) + '" data-axis="' + a + '"' + (opts.static ? ' tabindex="-1"' : ' aria-expanded="false" aria-controls="psEv"') +
                ' aria-label="' + AXIS[a].name + " " + d.scores[a] + (d.leans === a ? ", lean" : d.runner === a ? ", close second" : "") + '">' +
                '<span class="ps-b-top">' + mono(a) + '<span class="hud-label">' + AXIS[a].name + "</span>" + (d.runner === a ? '<span class="ps-2nd">2nd</span>' : "") + (opts.nudge === false ? "" : nudge(axisNudge(d, a), d.journal)) + "</span>" +
                '<span class="ps-b-num">' + d.scores[a] + "</span>" +
                meter(d.scores[a], i, ref ? ref[a] : null) +
                '<span class="ps-b-desc">' + AXIS[a].who + "</span></button>";
        }).join("") + "</div>";
    }
    // ---------------------------------------------------------------- C · pentagon
    function pt(i, v, R) { var ang = -Math.PI / 2 + i * 2 * Math.PI / 5, r = (v / 100) * R; return [Math.cos(ang) * r, Math.sin(ang) * r]; }
    function poly(vals, R) { return vals.map(function (v, i) { var p = pt(i, v, R); return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "); }
    function radar(d, ref, opts) {
        opts = opts || {};
        var R = 100, vals = AX.map(function (a) { return d.scores[a]; });
        var rings = [25, 50, 75, 100].map(function (q) { return '<polygon class="ps-c-ring' + (q === 50 ? " is-50" : "") + '" points="' + poly([q, q, q, q, q], R) + '"/>'; }).join("");
        var spokes = AX.map(function (a, i) { var p = pt(i, 100, R); return '<line class="ps-c-spoke" x1="0" y1="0" x2="' + p[0].toFixed(1) + '" y2="' + p[1].toFixed(1) + '"/>'; }).join("");
        var refP = ref ? '<polygon class="ps-c-ref" points="' + poly(AX.map(function (a) { return ref[a]; }), R) + '"/>' : "";
        var shape = '<polygon class="ps-c-shape" points="' + poly(vals, R) + '"/>';
        var refV = ref ? AX.map(function (a, i) { var p = pt(i, ref[a], R); return '<rect class="ps-c-refv" x="' + (p[0] - 2.5).toFixed(1) + '" y="' + (p[1] - 2.5).toFixed(1) + '" width="5" height="5"/>'; }).join("") : "";
        var vtx = AX.map(function (a, i) { var p = pt(i, vals[i], R); return '<circle class="ps-c-vtx' + (d.leans === a ? " is-lean" : "") + '" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="' + (d.leans === a ? 5 : 3.5) + '"/>'; }).join("");
        // Labels sit on a fixed radius beyond the 100 ring, centred on the spoke direction (uniform offset).
        var labels = opts.labels === false ? "" : AX.map(function (a, i) {
            var w = 58, h = 24, v = pt(i, 100, R), len = Math.hypot(v[0], v[1]), ux = v[0] / len, uy = v[1] / len;
            // Constant 12-unit gap from the 100-vertex to the nearest edge of the label box, along the spoke.
            var reach = Math.min(Math.abs(ux) > 1e-6 ? (w / 2) / Math.abs(ux) : Infinity, Math.abs(uy) > 1e-6 ? (h / 2) / Math.abs(uy) : Infinity);
            var p = [v[0] + ux * (reach + 12), v[1] + uy * (reach + 12)];
            return '<g class="ps-c-axis ' + classes(d, a) + '" data-axis="' + a + '" tabindex="0" role="button" aria-expanded="false" aria-controls="psEv" aria-label="' + AXIS[a].name + " " + d.scores[a] + '">' +
                '<circle class="ps-c-hit" cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="30"/>' +
                '<rect class="ps-c-lbl-box" x="' + (p[0] - w / 2).toFixed(1) + '" y="' + (p[1] - h / 2).toFixed(1) + '" width="' + w + '" height="' + h + '" rx="2"/>' +
                '<text class="ps-c-lbl" x="' + (p[0] - w / 2 + 7).toFixed(1) + '" y="' + (p[1] + 4).toFixed(1) + '">' + AXIS[a].mono + "</text>" +
                '<text class="ps-c-lbl-n" x="' + (p[0] + w / 2 - 7).toFixed(1) + '" y="' + (p[1] + 5).toFixed(1) + '" text-anchor="end">' + d.scores[a] + "</text></g>";
        }).join("");
        var vb = opts.labels === false ? "-104 -104 208 208" : "-168 -158 336 312";
        // Draw order: grid, deck shape, THEN your-average outline + markers on top so the comparison is never buried.
        return '<svg class="' + (opts.cls || "ps-c-svg") + '" viewBox="' + vb + '" role="img" aria-label="' + esc(srSummary(d)) + '">' + rings + spokes + shape + refP + refV + vtx + labels + "</svg>";
    }
    function sigil(d) { return radar(d, null, { labels: false, cls: "ps-sigil" }); }
    function renderC(d, ref) {
        var rows = AX.map(function (a) {
            return '<tr class="' + classes(d, a) + '"><th scope="row">' + mono(a) + " " + AXIS[a].name + '</th><td class="is-num ps-c-deck">' + d.scores[a] + "</td>" +
                (ref ? '<td class="is-num ps-c-avg">' + ref[a] + "</td>" : "") + "<td>" + nudge(axisNudge(d, a), d.journal) + "</td></tr>";
        }).join("");
        return '<div class="ps-c"><div class="ps-c-plot">' + radar(d, ref) + "</div>" +
            '<div class="ps-c-side">' + leanBlock(d) +
            '<table class="ps-c-table"><thead><tr><th scope="col"><span class="ps-sr">Axis</span></th><th scope="col" class="is-num"><i class="ps-c-sw"></i>Deck</th>' + (ref ? '<th scope="col" class="is-num"><i class="ps-c-sw is-ref"></i>Avg</th>' : "") + '<th scope="col"><span class="ps-sr">Journal</span></th></tr></thead><tbody>' + rows + "</tbody></table></div></div>";
    }

    // ---------------------------------------------------------------- evidence drawer
    function evidence(d, a) {
        var x = d.detail && d.detail[a];
        if (!x) return '<div class="ps-ev-head"><h4>' + AXIS[a].name + " " + d.scores[a] + "</h4><p>Evidence for this deck isn't in the mock fixtures (the real panel always has it).</p></div>";
        var rows = x.evidence.map(function (e, i) {
            if (e.signal === "journal") {
                var none = !d.journal;
                return '<li class="ps-ev-row is-journal' + (none ? " is-zero" : "") + '"><span class="ps-ev-code">' + (none ? "—" : "JNL") + "</span>" +
                    '<span class="ps-ev-label is-wrap"><b>' + (none ? "No journal" : "Journal") + "</b><span>" + (none ? "Cards-only score. A journal can nudge each axis by up to ±10." : esc((e.fields || []).map(fieldCopy).join(" · "))) + "</span></span>" +
                    '<span class="ps-ev-track is-diverge" role="presentation">' + (none ? "" : '<i class="' + (e.points < 0 ? "is-neg" : "") + '" style="width:' + Math.min(50, Math.abs(e.points) * 5) + '%"></i>') + "</span>" +
                    '<span class="ps-ev-pts">' + (none ? "0.0" : (e.points > 0 ? "+" : "−") + Math.abs(e.points).toFixed(1)) + " <small>/ ±10</small></span></li>";
            }
            var cards = e.cards || [], more = cards.length - CARD_CAP;
            var cardHtml = cards.length ? '<span class="ps-ev-cards">' + cards.slice(0, CARD_CAP).map(function (c) { return '<button type="button" class="hud-cardlink">' + esc(c) + "</button>"; }).join("") +
                (more > 0 ? '<button type="button" class="hud-chip hud-chip--ghost ps-ev-more" data-more="' + i + '" aria-label="Show ' + more + ' more cards">+' + more + " more</button>" : "") + "</span>" : "";
            if (e.tags && e.tags.length) cardHtml = '<span class="ps-ev-cards">' + e.tags.map(function (t) { return H.chip(t, "ghost"); }).join("") + "</span>";
            return '<li class="ps-ev-row' + (e.points === 0 ? " is-zero" : "") + '" data-i="' + i + '"><span class="ps-ev-code">' + e.signal + "</span>" +
                '<span class="ps-ev-label"><b>' + esc(e.label) + "</b><span>" + esc(FMT[e.signal] ? FMT[e.signal](e.value) : e.value) + "</span></span>" +
                '<span class="ps-ev-track" role="presentation"><i style="width:' + Math.round(e.sub_score * 100) + "%;--i:" + i + '"></i></span>' +
                '<span class="ps-ev-pts">' + e.points.toFixed(1) + " <small>/ " + e.weight + "</small></span>" + cardHtml + "</li>";
        }).join("");
        var nud = Math.round(x.journal_nudge * 10) / 10;
        // Header sum = the shown row values added up (1 dp), so a reader adding the column always gets the same number.
        var shownSum = x.evidence.filter(function (e) { return e.signal !== "journal"; }).reduce(function (t, e) { return t + Math.round(e.points * 10) / 10; }, 0);
        return '<div class="ps-ev-head"><h4 id="psEvTitle">' + AXIS[a].name + " · " + d.scores[a] + "</h4><p>" + AXIS[a].who + "</p>" +
            '<span class="ps-ev-sum">cards <b>' + shownSum.toFixed(1) + "</b>" + (d.journal ? " " + (nud >= 0 ? "+" : "−") + " journal <b>" + Math.abs(nud).toFixed(1) + "</b>" : "") + " ≈ <b>" + d.scores[a] + "</b></span>" +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost ps-ev-close" data-close aria-label="Close evidence">Esc</button></div>' +
            '<ol class="ps-ev-list">' + rows + "</ol>" +
            '<div class="ps-ev-foot">Points = weight × how far the signal got along its band. Every signal is listed, including zeros.</div>';
    }
    function bindEvidence(root, d) {
        var drawer = root.querySelector("#psEv"), open = null;
        function triggers() { return root.querySelectorAll("[data-axis][aria-controls='psEv']"); }
        function show(a, focusDrawer) {
            if (open === a) { hide(); return; }
            open = a;
            drawer.innerHTML = evidence(d, a); drawer.hidden = false;
            drawer.setAttribute("aria-labelledby", "psEvTitle");
            triggers().forEach(function (t) { t.setAttribute("aria-expanded", t.getAttribute("data-axis") === a ? "true" : "false"); });
            drawer.querySelectorAll("[data-more]").forEach(function (b) {
                b.addEventListener("click", function () {
                    var i = +b.getAttribute("data-more"), e = d.detail[a].evidence[i];
                    b.parentNode.innerHTML = e.cards.map(function (c) { return '<button type="button" class="hud-cardlink">' + esc(c) + "</button>"; }).join("");
                });
            });
            var c = drawer.querySelector("[data-close]"); if (c) c.addEventListener("click", function () { var t = root.querySelector("[data-axis='" + a + "']"); hide(); if (t) t.focus(); });
            if (focusDrawer) drawer.querySelector("[data-close]").focus();
        }
        function hide() { open = null; drawer.hidden = true; drawer.innerHTML = ""; triggers().forEach(function (t) { t.setAttribute("aria-expanded", "false"); }); }
        triggers().forEach(function (t, idx) {
            t.addEventListener("click", function () { show(t.getAttribute("data-axis")); });
            t.addEventListener("keydown", function (ev) {
                if ((ev.key === "Enter" || ev.key === " ") && t.tagName !== "BUTTON") { ev.preventDefault(); show(t.getAttribute("data-axis")); }
                var list = Array.prototype.slice.call(triggers()), j = list.indexOf(t);
                if (ev.key === "ArrowRight" || ev.key === "ArrowDown") { ev.preventDefault(); list[(j + 1) % list.length].focus(); }
                if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") { ev.preventDefault(); list[(j + list.length - 1) % list.length].focus(); }
            });
        });
        document.addEventListener("keydown", function (ev) {
            var tag = (document.activeElement && document.activeElement.tagName) || "";
            if (/INPUT|TEXTAREA|SELECT/.test(tag) || ev.metaKey || ev.ctrlKey || ev.altKey) return;
            if (ev.key === "Escape" && open) { var t = root.querySelector("[data-axis='" + open + "']"); hide(); if (t) t.focus(); return; }
            var a = AX.filter(function (x) { return AXIS[x].key === ev.key.toLowerCase(); })[0];
            if (a && !ev.shiftKey) { var tr = root.querySelector("[data-axis='" + a + "'][aria-controls]"); if (tr) { tr.focus(); show(a); } }
        });
        return { show: show, hide: hide };
    }

    // ---------------------------------------------------------------- deck page mock
    var DECK_STATES = [
        ["doctor", 5620189, "Doctor + Rose · control, no journal"],
        ["kaalia", 417707, "Holy Hellfire · journal nudges"],
        ["celes", 13958290, "Celes · one big spike"],
        ["bird", 13690458, "Bird UP · balanced (bunched)"],
        ["wild", 417278, "Wild Things · nothing stands out"],
        ["dragons", 21315594, "Dragons · 39 cards no metadata"],
        ["loading", 5620189, "Loading (first paint)"],
        ["pending", 5620189, "Not scored yet"],
        ["error", 5620189, "API failed"]
    ];
    function mockbar(dir, page) {
        var cur = param("s", "doctor");
        var opts = DECK_STATES.map(function (s) { return '<option value="' + s[0] + '"' + (s[0] === cur ? " selected" : "") + ">" + s[2] + "</option>"; }).join("");
        var files = [["a-bars.html", "A · Bars"], ["b-sheet.html", "B · Sheet"], ["c-pentagon.html", "C · Pentagon"], ["profile.html", "Profile"]];
        return '<div class="ps-mockbar" role="region" aria-label="Mockup controls"><span class="hud-label">Mockup · ' + esc(dir) + "</span>" +
            (page === "deck" ? '<label>State <select id="mockState">' + opts + "</select></label>" : "") +
            '<span class="ps-mockbar-links">' + files.map(function (f) { return '<a href="' + f[0] + (page === "deck" && f[0] !== "profile.html" ? location.hash : "") + '"' + (location.pathname.indexOf(f[0]) !== -1 ? ' aria-current="page"' : "") + ">" + f[1] + "</a>"; }).join("") + "</span></div>";
    }
    function deckhead(d, bar) {
        var roles = d.commanders.map(function (n, i) { return { name: n, uid: d.uids[i], role: d.commanders.length > 1 ? (/Rose Tyler|Clara/.test(n) ? "doctors_companion" : /Doctor/.test(n) ? "doctor" : "partner") : "commander" }; });
        var tabs = [["Overview", 1], ["Stats", 2], ["Identity", 3], ["Journal", 4], ["Tune", 5], ["Ideas", 6]].map(function (t, i) {
            return '<a class="hud-tab' + (i === 0 ? " active" : "") + '" href="#"' + (i === 0 ? ' aria-current="page"' : "") + ' data-key="' + t[1] + '">' + t[0] + "</a>";
        }).join("");
        return '<div class="app-topbar"><ol class="hud-crumbs"><li><a href="#">Decks</a></li><li><a href="#">' + esc(d.name) + '</a></li><li aria-current="page">Overview</li></ol>' +
            '<div class="app-topbar-meta"><span class="hud-freshness">Archidekt #' + d.id + "</span></div></div>" + (bar || "") +
            '<header class="hud-deckhead">' + H.commanders(roles, "hud-deckhead-art") +
            '<div><span class="hud-label">Commander deck · ' + esc(d.commanders.join(" + ")) + "</span><h1>" + esc(d.name) + "</h1>" +
            '<div class="hud-deckhead-meta">' + H.pips(d.colors, "sm") + H.statusChip(d.status) + '<span class="hud-freshness">100 cards</span></div></div>' +
            '<div class="hud-deckhead-actions"><a class="hud-btn" href="#">Manage</a><button type="button" class="hud-btn">Review</button><button type="button" class="hud-btn hud-btn--primary">Sync to External</button></div></header>' +
            '<nav class="hud-tabs hud-deck-tabs" aria-label="Deck sections">' + tabs + "</nav>";
    }
    function ghostPanel(t, meta) { return '<section class="hud-panel ps-ghost" aria-hidden="true"><header class="hud-panel-head"><h3 class="hud-panel-title">' + t + "</h3>" + (meta ? '<div class="hud-panel-meta">' + meta + "</div>" : "") + '</header><div class="hud-panel-body"></div></section>'; }
    function scoresPanel(dir, d, st) {
        var p = profile("physical"), ref = p.avg;
        var body, foot = "";
        if (st === "loading") {
            var sk = { scores: { timmy: 0, johnny: 0, spike: 0, vorthos: 0, melvin: 0 }, leans: "x", runner: null, journal: false };
            body = '<div class="ps-skel" aria-busy="true">' + (dir === "a" ? renderA(sk) : dir === "c" ? renderA(sk) : renderB(sk, null, { static: true, nudge: false })) + "</div>";
            foot = '<span class="hud-freshness">Loading…</span>';
        } else if (st === "pending") {
            body = '<div class="ps-state"><h4>Not scored yet</h4><p>Scores are worked out after a sync or a journal save. This deck synced 2 min ago; its scores will appear on the next run (usually under a minute).</p>' +
                '<button type="button" class="hud-btn hud-btn--sm">Score now</button></div>';
        } else if (st === "error") {
            body = '<div class="ps-state is-error" role="alert"><h4>Couldn\'t load ability scores</h4><p>GET /api/decks/' + d.id + '/personality returned 500. The rest of the page is fine. Your last scores are still stored; this is a read failure.</p>' +
                '<button type="button" class="hud-btn hud-btn--sm">Retry</button></div>';
        } else {
            var chart = dir === "a" ? leanBlock(d) + renderA(d, ref) : dir === "c" ? renderC(d, ref) : leanBlock(d) + renderB(d, ref);
            body = chart + '<div class="ps-evidence" id="psEv" role="region" hidden></div>';
            var notes = (d.notes || []).map(function (n) {
                return /no journal/.test(n) ? H.chip("Cards only · no journal", "neutral") : /without metadata/.test(n) ? H.chip("▲ " + n + " · low confidence", "signal") : H.chip(n, "signal");
            }).join("");
            foot = '<div class="ps-foot"><span class="ps-notes">' + notes + '<span class="ps-key"><i aria-hidden="true"></i>Your average (' + p.n + ' physical decks)</span><span class="hud-freshness">Scored 4 min ago · v1</span></span>' +
                '<span><a href="profile.html">Your player profile →</a></span></div>';
        }
        var meta = st && /loading|pending|error/.test(st) ? "" : '<span class="ps-hint">Evidence: click a score or press</span><span class="hud-kbd">T</span><span class="hud-kbd">J</span><span class="hud-kbd">S</span><span class="hud-kbd">V</span><span class="hud-kbd">M</span>';
        return '<section class="hud-panel" id="psPanel" aria-labelledby="psH"><header class="hud-panel-head"><h3 class="hud-panel-title" id="psH">Ability scores</h3><div class="hud-panel-meta">' + meta + '</div></header>' +
            '<div class="hud-panel-body"><p class="ps-sr">' + (d.scores && !st ? esc(srSummary(d)) : "") + "</p>" + body + "</div>" +
            (foot ? '<div class="hud-panel-foot">' + foot + "</div>" : "") + "</section>";
    }
    function deckPage(dir, label) {
        shell("deck");
        var main = document.getElementById("main");
        function draw() {
            var key = param("s", "doctor"), s = DECK_STATES.filter(function (x) { return x[0] === key; })[0] || DECK_STATES[0];
            var d = byId(s[1]), st = /loading|pending|error/.test(key) ? key : null;
            main.innerHTML = deckhead(d, mockbar(label, "deck")) +
                '<div class="deck-hero"><aside class="deck-hero-side">' + H.commanders(d.commanders.map(function (n, i) { return { name: n, uid: d.uids[i], role: "partner" }; }), "ps-ghost") +
                ghostPanel("External") + ghostPanel("Lifecycle") + "</aside>" +
                '<div class="deck-hero-main"><div class="hud-stat-strip ps-ghost" aria-hidden="true">' + H.stat("Cards", "100", { mod: "hero" }) + H.stat("Avg CMC", "—") + H.stat("Lands", "—") + H.stat("Value", "—") + "</div>" +
                scoresPanel(dir, d, st) +
                '<div class="hud-grid hud-grid--2">' + ghostPanel("Mana curve", "Non-land") + ghostPanel("Card types") + "</div>" +
                '<div class="hud-grid hud-grid--2">' + ghostPanel("Colour identity") + ghostPanel("Profile") + "</div></div></div>";
            var sel = document.getElementById("mockState");
            if (sel) sel.addEventListener("change", function () { setParam("s", sel.value); });
            if (!st) {
                var ev = bindEvidence(document.getElementById("psPanel"), d);
                var open = param("open", null); if (open) ev.show(open);
            }
        }
        window.addEventListener("hashchange", draw);
        draw();
    }

    // ---------------------------------------------------------------- profile page
    var PROF_STATES = [["full", "Physical decks (39)"], ["all", "All decks"], ["low", "Low data · 2 decks"], ["one", "Low data · 1 deck"], ["empty", "No decks scored"], ["partial", "2 decks failed"], ["error", "API failed"], ["loading", "Loading"]];
    function profMockbar() {
        var cur = param("p", "full"), chart = param("chart", "b");
        return '<div class="ps-mockbar" role="region" aria-label="Mockup controls"><span class="hud-label">Mockup · Profile</span>' +
            '<label>State <select id="mockProf">' + PROF_STATES.map(function (s) { return '<option value="' + s[0] + '"' + (s[0] === cur ? " selected" : "") + ">" + s[1] + "</option>"; }).join("") + "</select></label>" +
            '<label>Chart <select id="mockChart"><option value="b"' + (chart === "b" ? " selected" : "") + '>B · Sheet</option><option value="c"' + (chart === "c" ? " selected" : "") + ">C · Pentagon</option></select></label>" +
            '<span class="ps-mockbar-links"><a href="a-bars.html">A · Bars</a><a href="b-sheet.html">B · Sheet</a><a href="c-pentagon.html">C · Pentagon</a><a href="profile.html" aria-current="page">Profile</a></span></div>';
    }
    function headline(p) {
        // ILLUSTRATIVE thresholds (PS-3 owns the rule): gap to 2nd >= 5 clear, else "with a lot of"; top-third < 3 "a bit of everything".
        var r = p.rank, a = p.avg, n = function (x) { return AXIS[x].name; };
        if (a[r[0]] - a[r[2]] < 3) return { kind: "even", html: "You play <em>a bit of everything</em>." };
        if (a[r[0]] - a[r[1]] < 5) return { kind: "close", html: "You are mostly <em>" + n(r[0]) + "</em>, with a lot of " + n(r[1]) + "." };
        return { kind: "clear", html: "You are mostly <em>" + n(r[0]) + "</em>." };
    }
    function callouts(p) {
        // Every callout: big number (+ optional "/ of" or unit) then ONE sentence that continues it. Same grammar everywhere.
        var out = [], n = function (x) { return AXIS[x].name; }, L = p.leans, r = p.rank;
        // 1. Most common lean
        out.push({ lead: true, n: L[p.mostLean], of: p.n, text: "decks lean <b>" + n(p.mostLean) + "</b>, your most common lean.", act: ["Show them", "lean=" + p.mostLean] });
        // 2. Average vs lean disagreement
        if (r[0] !== p.mostLean) out.push({ n: p.avg[r[0]], unit: "avg", text: "<b>" + n(r[0]) + "</b> is your highest average, but fewer decks lean that way than " + n(p.mostLean) + ". It shows up everywhere without taking over.", act: ["Sort by " + n(r[0]), "sort=" + r[0]] });
        // 3. Rarest lean / no deck leans X
        var rare = AX.slice().sort(function (a, b) { return (L[a] - L[b]) || (AX.indexOf(a) - AX.indexOf(b)); })[0];
        out.push({ n: L[rare], of: p.n, text: L[rare] ? (L[rare] === 1 ? "deck leans" : "decks lean") + " <b>" + n(rare) + "</b>, the rarest lean in your collection." : "decks lean <b>" + n(rare) + "</b>.", act: L[rare] ? ["Show them", "lean=" + rare] : null });
        // 4. Widest spread
        var wide = AX.slice().sort(function (a, b) { return (p.hi[b].scores[b] - p.lo[b].scores[b]) - (p.hi[a].scores[a] - p.lo[a].scores[a]); })[0];
        out.push({ n: p.lo[wide].scores[wide] + "–" + p.hi[wide].scores[wide], unit: "range", text: "<b>" + n(wide) + "</b> is all or nothing: from " + dname(p.lo[wide]) + " to " + dname(p.hi[wide]) + ".", act: ["Sort by " + n(wide), "sort=" + wide] });
        // 5. Most extreme single score (outlier). Skips the axis/deck already named in 4 so two cards never say the same thing.
        var best = null; AX.forEach(function (a) { if (a === wide) return; if (!best || p.hi[a].scores[a] > best.s) best = { a: a, s: p.hi[a].scores[a], d: p.hi[a] }; });
        if (best && best.d.id !== p.hi[wide].id) out.push({ n: best.s, unit: n(best.a), text: "Your most " + n(best.a) + " deck is " + dname(best.d) + ".", act: ["Open deck", "deck=" + best.d.id] });
        // 6. Journals
        if (p.journals < p.n) out.push({ n: p.journals, of: p.n, text: "decks have a journal. The rest are scored from cards alone, so your intent doesn't nudge them.", act: ["Cards-only decks", "lean=nojournal"] });
        if (L.balanced) out.push({ n: L.balanced, of: p.n, text: "deck" + (L.balanced === 1 ? " is" : "s are") + " <b>balanced</b>: no axis stands out.", act: ["Show them", "lean=balanced"] });
        return out;
    }
    // Deck names in prose: quoted, truncated at 34 chars, full name in title.
    function dname(d) {
        var s = d.name;
        if (s.length > 34) { var cut = s.slice(0, 33), sp = cut.lastIndexOf(" "); s = (sp > 12 ? cut.slice(0, sp) : cut).replace(/[\s.,;:!?'"-]+$/, "") + "…"; }
        return '<b class="ps-dname" title="' + esc(d.name) + '">“' + esc(s) + "”</b>";
    }
    // Deterministic vertical jitter so stacked dots stay countable (no random: same page, same picture).
    function jitter(id, a) { var h = 0, s = String(id) + a; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return ((h % 1000) / 1000 - 0.5) * 0.7; }
    function spread(p) {
        var rows = AX.map(function (a) {
            var dots = p.decks.map(function (d) {
                return '<button type="button" class="ps-spread-dot' + (d.leans === a ? " is-lean" : "") + '" style="--x:' + d.scores[a] + ";--jy:" + jitter(d.id, a).toFixed(3) + '" data-deck="' + d.id + '" aria-label="' + esc(d.name) + ": " + AXIS[a].name + " " + d.scores[a] + '" tabindex="-1"></button>';
            }).join("");
            return '<li class="ps-spread-row">' + mono(a) + '<span class="ps-spread-name">' + AXIS[a].name + '</span><span class="ps-spread-track">' + dots +
                '<span class="ps-spread-avg" style="--x:' + p.avg[a] + '" title="Average ' + p.avg[a] + '"></span></span>' +
                '<span class="ps-spread-val">avg <b>' + p.avg[a] + '</b><span class="ps-lean-n"> · <b>' + p.leans[a] + "</b> lean</span></span></li>";
        }).join("");
        return '<ul class="ps-spread" aria-label="Every deck on each axis">' + rows + '</ul><div class="ps-spread-axis" aria-hidden="true"><span></span><span></span><span><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></span><span></span></div>';
    }
    function spreadLegend() {
        return '<span class="ps-legend"><span><i class="ps-lg-dot"></i>A deck</span><span><i class="ps-lg-dot is-lean"></i>…that leans this way</span><span><i class="ps-lg-avg"></i>Average</span></span>';
    }
    function level(v) { return v >= 80 ? 5 : v >= 65 ? 4 : v >= 50 ? 3 : v >= 35 ? 2 : v >= 20 ? 1 : 0; }
    // Thumbnail: eager (39 small art crops; lazy left the bottom rows blank in full-page captures),
    // initial-letter frame underneath so a missing image is an intentional placeholder.
    function thumb(d, art) {
        var init = esc((d.commanders[0] || d.name || "?").charAt(0));
        return '<span class="ps-thumb" aria-hidden="true">' + (art.src ? '<img alt="" src="' + art.src + '" data-fallback="' + art.fallback + '">' : "") + init + "</span>";
    }
    function leanChip(d) {
        if (d.error) return H.chip("Couldn't score", "danger", true);
        if (d.leans === "balanced") return H.chip("Balanced", "neutral");
        return '<span class="hud-chip hud-chip--signal">' + AXIS[d.leans].name + (d.runner ? " · " + AXIS[d.runner].mono : "") + "</span>";
    }
    function deckRows(list, chart) {
        return list.map(function (d) {
            var art = d.uids && d.uids[0] ? H.scryfall(d.uids[0], "art_crop") : { src: "", fallback: "" };
            var flags = [d.journal ? "" : "cards only", (d.notes || []).filter(function (n) { return /metadata/.test(n); }).map(function (n) { return "▲ " + n.replace(" cards without metadata", " no metadata"); })[0] || ""].filter(Boolean).join(" · ");
            if (d.error) {
                return '<tr class="is-failed" data-id="' + d.id + '"><td><span class="ps-deckcell">' + thumb(d, art) + '<span><a href="' + deckHref(d) + '">' + esc(d.name) + "</a><small>" + esc(d.error) + '</small></span></span></td>' +
                    (chart === "c" ? "<td></td>" : "") + '<td colspan="5" class="hud-muted">Not scored · <button type="button" class="hud-btn hud-btn--sm hud-btn--ghost">Retry</button></td><td>' + leanChip(d) + "</td></tr>";
            }
            return '<tr data-id="' + d.id + '" data-lean="' + d.leans + '" data-journal="' + (d.journal ? 1 : 0) + '"><td data-value="' + esc(d.name) + '"><span class="ps-deckcell">' + thumb(d, art) + '<span><a href="' + deckHref(d) + '">' + esc(d.name) + "</a>" +
                "<small>" + esc(d.commanders.join(" + ")) + (flags ? " · " + flags : "") + "</small></span></span></td>" +
                (chart === "c" ? "<td>" + sigil(d) + "</td>" : "") +
                AX.map(function (a) { return '<td class="ps-h' + (d.leans === a ? " is-lean" : "") + '" data-level="' + level(d.scores[a]) + '" data-value="' + d.scores[a] + '"><span>' + d.scores[a] + "</span></td>"; }).join("") +
                '<td data-value="' + d.leans + '">' + leanChip(d) + "</td></tr>";
        }).join("");
    }
    function deckCards(list) {
        return list.map(function (d) {
            if (d.error) return '<li class="ps-card" data-id="' + d.id + '"><span class="ps-deckcell"><span><a href="' + deckHref(d) + '">' + esc(d.name) + "</a><small>" + esc(d.error) + "</small></span></span>" + leanChip(d) + "</li>";
            return '<li class="ps-card" data-id="' + d.id + '" data-lean="' + d.leans + '" data-journal="' + (d.journal ? 1 : 0) + '"><span class="ps-deckcell"><span><a href="' + deckHref(d) + '">' + esc(d.name) + "</a><small>" + esc(d.commanders.join(" + ")) + (d.journal ? "" : " · cards only") + "</small></span></span>" + leanChip(d) +
                '<span class="ps-card-scores ps-table">' + AX.map(function (a) { return '<span class="ps-h' + (d.leans === a ? " is-lean" : "") + '" data-level="' + level(d.scores[a]) + '"><span>' + d.scores[a] + "<small>" + AXIS[a].mono + "</small></span></span>"; }).join("") + "</span></li>";
        }).join("");
    }
    function table(p, chart, failed) {
        var list = p.decks.slice().sort(function (a, b) { return a.name.localeCompare(b.name); }).concat(failed || []);
        var segs = [["all", "All"], ["timmy", "T"], ["johnny", "J"], ["spike", "S"], ["vorthos", "V"], ["melvin", "M"], ["balanced", "Bal"], ["nojournal", "No journal"]];
        var seg = '<span class="hud-seg" role="radiogroup" aria-label="Filter by lean">' + segs.map(function (s) {
            return '<input type="radio" name="lean" value="' + s[0] + '" id="ln-' + s[0] + '"' + (s[0] === "all" ? " checked" : "") + '><label for="ln-' + s[0] + '" title="' + (AXIS[s[0]] ? "Leans " + AXIS[s[0]].name + " (" + AXIS[s[0]].key + ")" : s[1]) + '">' + s[1] + "</label>";
        }).join("") + "</span>";
        var head = '<th scope="col" data-sort="text" aria-sort="ascending"><button type="button" class="hud-sort">Deck</button></th>' + (chart === "c" ? '<th scope="col"><span class="ps-sr">Shape</span></th>' : "") +
            AX.map(function (a) { return '<th scope="col" class="ps-h" data-sort="num" aria-sort="none" data-axis-col="' + a + '"><button type="button" class="hud-sort" title="' + AXIS[a].name + '">' + AXIS[a].mono + '<span class="ps-sr"> ' + AXIS[a].name + "</span></button></th>"; }).join("") +
            '<th scope="col" data-sort="text" aria-sort="none"><button type="button" class="hud-sort">Leans</button></th>';
        return '<div class="ps-filters">' + seg + '<input type="search" id="psFind" placeholder="Filter decks  /" aria-label="Filter decks by name or commander" style="max-width:16rem"><span class="ps-count" id="psCount" aria-live="polite">' + list.length + " decks</span></div>" +
            '<div class="hud-table-wrap ps-table-wrap"><table class="hud-table ps-table" data-hud-sortable id="psTable"><thead><tr>' + head + "</tr></thead><tbody>" + deckRows(list, chart) + "</tbody></table></div>" +
            '<ul class="ps-cards" id="psCards">' + deckCards(list) + "</ul>";
    }
    function bindTable(root) {
        var find = root.querySelector("#psFind"), count = root.querySelector("#psCount");
        if (!find) return;
        function apply() {
            var lean = (root.querySelector("input[name=lean]:checked") || {}).value || "all", q = find.value.trim().toLowerCase(), shown = 0;
            root.querySelectorAll("#psTable tbody tr, #psCards li").forEach(function (tr) {
                var ok = lean === "all" || (lean === "nojournal" ? tr.getAttribute("data-journal") === "0" : tr.getAttribute("data-lean") === lean);
                if (ok && q) ok = tr.textContent.toLowerCase().indexOf(q) !== -1;
                tr.hidden = !ok; if (ok && tr.tagName === "TR") shown++;
            });
            count.textContent = shown + " deck" + (shown === 1 ? "" : "s");
        }
        root.querySelectorAll("input[name=lean]").forEach(function (i) { i.addEventListener("change", apply); });
        find.addEventListener("input", apply);
        root.__apply = function (lean) { var el = root.querySelector("#ln-" + lean); if (el) { el.checked = true; apply(); } };
        document.addEventListener("keydown", function (ev) {
            var tag = (document.activeElement && document.activeElement.tagName) || "";
            if (ev.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(tag)) { ev.preventDefault(); find.focus(); return; }
            if (/INPUT|TEXTAREA|SELECT/.test(tag) || ev.metaKey || ev.ctrlKey || ev.altKey) { if (ev.key === "Escape" && tag === "INPUT") { find.value = ""; apply(); find.blur(); } return; }
            var k = ev.key.toLowerCase(), a = AX.filter(function (x) { return AXIS[x].key === k; })[0];
            if (a) root.__apply(a); else if (k === "b") root.__apply("balanced"); else if (k === "0") root.__apply("all");
        });
        H.initSortable(root);
    }
    function bindSpread(root) {
        var tip = document.createElement("div"); tip.className = "ps-tip"; tip.hidden = true; document.body.appendChild(tip);
        root.querySelectorAll(".ps-spread-dot").forEach(function (b) {
            function on() {
                var r = b.getBoundingClientRect(); tip.textContent = b.getAttribute("aria-label"); tip.hidden = false;
                tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, r.left - tip.offsetWidth / 2)) + "px"; tip.style.top = (r.top - 30) + "px";
                var id = b.getAttribute("data-deck");
                root.querySelectorAll(".ps-spread-dot[data-deck='" + id + "']").forEach(function (x) { x.classList.add("is-hot"); });
            }
            function off() { tip.hidden = true; root.querySelectorAll(".ps-spread-dot.is-hot").forEach(function (x) { x.classList.remove("is-hot"); }); }
            b.addEventListener("mouseenter", on); b.addEventListener("mouseleave", off);
            b.addEventListener("click", function () {
                var row = root.querySelector("#psTable tr[data-id='" + b.getAttribute("data-deck") + "']");
                if (row) { root.__apply && root.__apply("all"); row.classList.add("is-hot"); row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); row.querySelector("a").focus(); setTimeout(function () { row.classList.remove("is-hot"); }, 1600); }
            });
        });
    }
    function profPage() {
        shell("profile");
        var main = document.getElementById("main");
        function draw() {
            var st = param("p", "full"), chart = param("chart", "b");
            var p = profile(st === "all" ? "all" : "physical"), failed = [];
            if (st === "low") { p = Object.assign({}, p); p.decks = [byId(5620189), byId(417707)]; }
            if (st === "one") { p = Object.assign({}, p); p.decks = [byId(5620189)]; }
            if (st === "low" || st === "one") {
                var n = p.decks.length, avg = {}; AX.forEach(function (a) { avg[a] = Math.floor(p.decks.reduce(function (t, d) { return t + d.scores[a]; }, 0) / n + 0.5); });
                p.n = n; p.avg = avg; p.rank = rank(avg);
            }
            if (st === "partial") failed = [
                { id: 7119320, name: "The great thing about being me, is that there are so many mes.", commanders: ["The Master, Multiplied"], uids: ["7f734ca0-91bc-4496-9bd7-2d09415e850f"], error: "Scoring failed: interaction graph build timed out · 06:02" },
                { id: 543157, name: "Knights", commanders: [], uids: [], error: "No commander on the decklist, so it can't be scored" }];
            var crumbs = '<div class="app-topbar"><ol class="hud-crumbs"><li aria-current="page">Player profile</li></ol><div class="app-topbar-meta"><span class="hud-freshness">' +
                (st === "loading" ? "Loading…" : "Scores updated 4 min ago") + "</span></div></div>";
            var html = crumbs + profMockbar() + '<article class="ps-prof" id="psProf">';
            var scopeSeg = '<span class="hud-seg" role="radiogroup" aria-label="Which decks count"><input type="radio" name="scope" id="sc-p" value="full"' + (st !== "all" ? " checked" : "") + '><label for="sc-p">Physical · 39</label><input type="radio" name="scope" id="sc-a" value="all"' + (st === "all" ? " checked" : "") + '><label for="sc-a">All · 57</label></span>';
            if (st === "error") {
                html += '<header class="ps-prof-head"><div><span class="hud-label">Player profile</span><h1 style="margin:var(--sp-1) 0 0">Who you are at the table</h1></div></header>' +
                    '<div class="ps-prof-sec"><div class="ps-state is-error" role="alert"><h4>Couldn\'t build your profile</h4><p>GET /api/profile/personality returned 503 (Redis unavailable). Nothing is lost: deck scores are stored per deck and the profile is rebuilt from them.</p><button type="button" class="hud-btn hud-btn--sm">Retry</button></div></div>';
            } else if (st === "empty") {
                html += '<header class="ps-prof-head"><div><span class="hud-label">Player profile</span><h1 style="margin:var(--sp-1) 0 0">Who you are at the table</h1></div></header>' +
                    '<div class="ps-prof-sec"><div class="ps-state"><h4>No decks scored yet</h4><p>Your profile is the average of your decks\' ability scores (Timmy, Johnny, Spike, Vorthos, Melvin). Scores are worked out after a sync. Run one and come back.</p><div style="display:flex;gap:var(--sp-2)"><button type="button" class="hud-btn hud-btn--primary hud-btn--sm">Sync decks</button><a class="hud-btn hud-btn--sm" href="#">What are these?</a></div></div></div>';
            } else if (st === "loading") {
                var z = { scores: { timmy: 0, johnny: 0, spike: 0, vorthos: 0, melvin: 0 }, leans: "x", journal: false };
                html += '<header class="ps-prof-head"><div><span class="hud-label">Player profile</span><div class="hud-thesis"><span class="hud-thesis-bar" aria-hidden="true"><span style="background:var(--surface-3)"></span></span><div><blockquote style="color:var(--text-4)">Reading your decks…</blockquote></div></div></div></header>' +
                    '<div class="ps-prof-sec ps-skel" aria-busy="true">' + renderB(z, null, { static: true, nudge: false }) + "</div>";
            } else {
                var low = p.n < 3, hl = headline(p);
                var thesis = low ? "Early read: <em>" + AXIS[p.rank[0]].name + "</em>, so far." : hl.html;
                var sub = low
                    ? "Based on <b>" + p.n + " deck" + (p.n === 1 ? "" : "s") + "</b>. A profile needs at least 3 to say anything about <i>you</i> rather than one deck. Trends unlock at 3."
                    : (function () {
                        var others = AX.filter(function (a) { return a !== p.mostLean; }).sort(function (a, b) { return (p.leans[b] - p.leans[a]) || (AX.indexOf(a) - AX.indexOf(b)); });
                        var tie = p.leans[others[0]] === p.leans[p.mostLean];
                        var countLine = p.mostLean === p.rank[0] && !tie
                            ? "It's also your most common lean: <b>" + p.leans[p.mostLean] + " decks</b>."
                            : tie
                                ? "By count, <b>" + AXIS[p.mostLean].name + "</b> and <b>" + AXIS[others[0]].name + "</b> tie: " + p.leans[p.mostLean] + " decks lean each way, then " + AXIS[others[1]].name + " (" + p.leans[others[1]] + ")."
                                : "By count, <b>" + AXIS[p.mostLean].name + "</b> wins: " + p.leans[p.mostLean] + " decks lean that way, then " + AXIS[others[0]].name + " (" + p.leans[others[0]] + ") and " + AXIS[others[1]].name + " (" + p.leans[others[1]] + ").";
                        return "That's your average over <b>" + p.n + " " + (p.scope === "all" ? "" : "physical ") + "decks</b>. " + countLine + (p.leans.balanced ? " " + p.leans.balanced + " are balanced." : "");
                    })();
                var avgDeck = { scores: p.avg, leans: low ? p.rank[0] : (hl.kind === "even" ? "balanced" : p.rank[0]), runner: hl.kind === "close" ? p.rank[1] : null, journal: false };
                html += '<header class="ps-prof-head"><div><span class="hud-label">Player profile · from your decks\' ability scores</span>' +
                    '<div class="hud-thesis"><span class="hud-thesis-bar" aria-hidden="true"><span style="background:var(--signal)"></span></span><div><blockquote>' + thesis + '</blockquote><p class="ps-thesis-sub">' + sub + "</p></div></div></div>" +
                    '<div class="ps-scope">' + scopeSeg + '<span class="hud-freshness">' + (p.scope === "all" && p.notScored.length ? p.notScored.length + " not scored · " : "") + (failed.length ? '<span style="color:var(--danger)">' + failed.length + " failed</span> · " : "") + p.journals + " of " + p.n + " have a journal</span></div></header>";
                html += '<section class="ps-prof-sec" aria-labelledby="secAvg"><div class="hud-section-head"><span class="hud-section-id">01</span><h2 id="secAvg">Your average</h2></div>' +
                    (chart === "c"
                        ? '<div class="hud-panel"><div class="hud-panel-body">' + renderC(avgDeck, null).replace(/aria-controls="psEv"/g, "") + "</div></div>"
                        : renderB(avgDeck, null, { static: true, nudge: false })) +
                    (low ? "" : '<div class="hud-panel" style="margin-top:var(--sp-4)"><header class="hud-panel-head"><h3 class="hud-panel-title">Every deck, every axis</h3><div class="hud-panel-meta">' + spreadLegend() + '</div></header><div class="hud-panel-body">' + spread(p) + "</div></div>") +
                    "</section>";
                if (low) {
                    html += '<section class="ps-prof-sec" aria-labelledby="secTr"><div class="hud-section-head"><span class="hud-section-id">02</span><h2 id="secTr">Trends</h2></div><div class="ps-state"><h4>' + (3 - p.n) + " more deck" + (3 - p.n === 1 ? "" : "s") + " to unlock trends</h4><p>With " + p.n + " deck" + (p.n === 1 ? "" : "s") + ", a trend is just a deck. Sync or import more and they'll show up here.</p></div></section>";
                } else {
                    html += '<section class="ps-prof-sec" aria-labelledby="secTr"><div class="hud-section-head"><span class="hud-section-id">02</span><h2 id="secTr">Trends</h2></div><ul class="ps-callouts">' +
                        callouts(p).map(function (c, i) {
                            return '<li class="ps-callout' + (c.lead ? " is-lead" : "") + '" style="--i:' + i + '"><span class="ps-callout-n">' + c.n + (c.of ? "<small> / " + c.of + "</small>" : c.unit ? "<small> " + esc(c.unit) + "</small>" : "") + "</span><p>" + c.text + "</p>" +
                                (c.act ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-act="' + c.act[1] + '">' + c.act[0] + " →</button>" : "") + "</li>";
                        }).join("") + "</ul></section>";
                }
                html += '<section class="ps-prof-sec" aria-labelledby="secDecks"><div class="hud-section-head"><span class="hud-section-id">03</span><h2 id="secDecks">Your decks</h2></div>' + table(p, chart, failed) + "</section>";
            }
            html += "</article>";
            main.innerHTML = html;
            var sel = document.getElementById("mockProf"); sel.addEventListener("change", function () { setParam("p", sel.value); });
            var ch = document.getElementById("mockChart"); ch.addEventListener("change", function () { setParam("chart", ch.value); });
            main.querySelectorAll("input[name=scope]").forEach(function (i) { i.addEventListener("change", function () { setParam("p", i.value); }); });
            var root = document.getElementById("psProf");
            bindTable(root); bindSpread(root);
            root.querySelectorAll("[data-act]").forEach(function (b) {
                b.addEventListener("click", function () {
                    var kv = b.getAttribute("data-act").split("=");
                    if (kv[0] === "lean") root.__apply(kv[1]);
                    if (kv[0] === "sort") { var th = root.querySelector("th[data-axis-col='" + kv[1] + "']"); th.setAttribute("aria-sort", "none"); H.sortTable(root.querySelector("#psTable"), th); }
                    if (kv[0] === "deck") { location.href = "b-sheet.html#s=doctor"; return; }
                    root.querySelector("#secDecks").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
                });
            });
        }
        window.addEventListener("hashchange", draw);
        draw();
    }

    window.PS = { AX: AX, AXIS: AXIS, deckPage: deckPage, profPage: profPage, profile: profile, leanInfo: leanInfo, byId: byId };
})();
