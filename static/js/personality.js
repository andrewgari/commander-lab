/* ==========================================================================
   Commander Lab — ability scores UI (PS-4), client side (window.PSUI).
   Spec: docs/design/personality/UX_SPEC.md (PS-D, direction B "character
   sheet"). Data: GET /api/decks/{id}/personality and
   GET /api/profile/personality (PS-3, docs/PERSONALITY_SPEC.md §10).

   Pure string builders (no DOM reads) so tests can run them in node:
     PSUI.deckModel(personality)        API payload -> view model
     PSUI.leanSentence(d)               {head, why} (UX_SPEC §2.3)
     PSUI.tiles(d, ref, opts)           the five ability tiles
     PSUI.evidence(d, axis, opts)       one axis' evidence drawer
     PSUI.deckPanelBody(state, ...)     deck panel body for every state
     PSUI.profileHtml(state, ...)       the whole /profile article
   Binders (DOM): PSUI.bindEvidence, PSUI.bindProfile.
   The UI never re-decides leans_toward; it only labels what PS-3 stored.
   ========================================================================== */
(function () {
    "use strict";

    var AX = ["timmy", "johnny", "spike", "vorthos", "melvin"];
    var AXIS = {
        timmy:   { name: "Timmy",   mono: "T", key: "t", who: "Big, splashy moments" },
        johnny:  { name: "Johnny",  mono: "J", key: "j", who: "Shows off a build" },
        spike:   { name: "Spike",   mono: "S", key: "s", who: "Plays to win" },
        vorthos: { name: "Vorthos", mono: "V", key: "v", who: "The deck tells a story" },
        melvin:  { name: "Melvin",  mono: "M", key: "m", who: "Loves the mechanics" }
    };
    var CARD_CAP = 6;            // card names per signal before "+N more"
    var NOTE_NO_JOURNAL = "no journal: cards-only score";

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    // Server copy (headline, why, trend text) may carry <b> around axis/deck
    // names; deck names are already escaped server-side. Keep <b>, drop any
    // other tag.
    function onlyB(s) { return String(s == null ? "" : s).replace(/<(?!\/?b>)[^>]*>/g, ""); }
    function chip(text, tone) { return '<span class="hud-chip hud-chip--' + (tone || "neutral") + '">' + esc(text) + "</span>"; }
    function plural(n, one, many) { return n === 1 ? one : (many || one + "s"); }
    function deckHref(name) { return "/deck/" + encodeURIComponent(name); }

    // ------------------------------------------------------------------ copy
    // How each signal's raw `value` reads (UX_SPEC §2.3). Inverted signals say so.
    function pct(v) { return Math.round(Number(v) * 100) + "%"; }
    var FMT = {
        T1: function (v) { return v + " creatures at mv 6+"; },
        T2: function (v) { return v + " are creatures"; },
        T3: function (v) { return v + " ramp pieces"; },
        T4: function (v) { return v + " splashy " + plural(Number(v), "card"); },
        T5: function (v) { return v + " interaction · fewer scores higher"; },
        J1: function (v) { return v + " " + plural(Number(v), "combo") + " in deck"; },
        J2: function (v) { return v + " edges per card"; },
        J3: function (v) { return v + " under 15% on EDHREC"; },
        J4: function (v) { return pct(v) + " touch a commander"; },
        J5: function (v) { return pct(v) + " sit in a chain"; },
        S1: function (v) { return "power " + v; },
        S2: function (v) { return v + " " + plural(Number(v), "tutor"); },
        S3: function (v) { return v + " fast mana"; },
        S4: function (v) { return v + " interaction pieces"; },
        S5: function (v) { return "avg mv " + v + " · lower scores higher"; },
        S6: function (v) { return v + " " + plural(Number(v), "combo") + " of 3 cards or fewer"; },
        S7: function (v) { return v + " Game " + plural(Number(v), "Changer"); },
        V1: function (v) {
            // "type Alien 5/22; set WHO 41/55" -> "Alien 5/22 creatures · WHO 41/55 cards"
            var m = String(v).match(/^type (.+?) (\d+\/\d+); set (.+?) (\d+\/\d+)$/);
            if (!m) return String(v);
            var parts = [];
            // Non-breaking spaces: a wrap never strands "set" from its code.
            if (m[1] !== "None") parts.push(m[1] + "\u00a0" + m[2] + " creatures");
            if (m[3] !== "None") parts.push("set\u00a0" + m[3] + "\u00a0" + m[4]);
            return parts.join(" · ") || "no shared type or set";
        },
        V2: function (v) { return v + " legendary"; },
        V3: function (v) { return v + " name a commander"; },
        V4: function (v) { return v + " story " + plural(Number(v), "link"); },
        M1: function (v) { return v + " unusual " + plural(Number(v), "mechanic"); },
        M2: function (v) { return v + " move rules objects"; },
        M3: function (v) { return v + " " + plural(Number(v), "loop"); },
        M4: function (v) { return v + " cheap " + plural(Number(v), "cantrip"); },
        M5: function (v) { return v + " odd types or rules"; }
    };
    // Journal evidence `fields` are machine strings; the UI shows sentences.
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
        f = String(f || "");
        var m = f.match(/^([^=]+)=(.*)$/);
        if (m && FIELD_COPY[m[1]]) return FIELD_COPY[m[1]] + ": " + m[2].replace(/_/g, " ");
        if (/^text: /.test(f)) return "Words in your notes: " + f.slice(6).split(/,\s*/).map(function (w) { return "“" + w + "”"; }).join(", ");
        return FIELD_COPY[f] || f.replace(/_/g, " ");
    }
    // Degraded-input notes (PS-1 §7) -> foot chips. no-journal is neutral, the rest advisory.
    function noteChip(n) {
        if (n === NOTE_NO_JOURNAL) return chip("Cards only · no journal", "neutral");
        if (/without metadata/.test(n)) return chip("▲ " + n + " · low confidence", "signal");
        return chip("▲ " + n, "signal");
    }
    function ago(iso, now) {
        var t = Date.parse(iso || "");
        if (isNaN(t)) return "";
        var s = Math.max(0, Math.round(((now || Date.now()) - t) / 1000));
        if (s < 60) return "just now";
        if (s < 3600) return Math.round(s / 60) + " min ago";
        if (s < 86400) return Math.round(s / 3600) + " h ago";
        return Math.round(s / 86400) + " d ago";
    }

    // ------------------------------------------------------------------ model
    function rank(scores) { return AX.slice().sort(function (a, b) { return (scores[b] - scores[a]) || (AX.indexOf(a) - AX.indexOf(b)); }); }

    // API `personality` (PersonalityScores) + optional `read` (PersonalityReadDoc) -> view model
    function deckModel(p, read) {
        var scores = {}, rulebook_scores = {};
        AX.forEach(function (a) {
            rulebook_scores[a] = p && p[a] ? p[a].score : 0;
            if (read && read.scores && read.scores[a] && read.scores[a].score != null) {
                scores[a] = read.scores[a].score;
            } else {
                scores[a] = rulebook_scores[a];
            }
        });
        var oracle_used = !!(read && read.scores);
        var justifications = {}, scores_detail = {};
        if (oracle_used) {
            AX.forEach(function (a) {
                justifications[a] = read.scores[a] ? (read.scores[a].justification || "") : "";
                scores_detail[a] = read.scores[a] || null;
            });
        }
        var leans = oracle_used ? (read.leans_toward || "balanced") : (p ? (p.leans_toward || "balanced") : "balanced");
        var runner = oracle_used ? (read.runner_up || null) : (p ? (p.runner_up || null) : null);
        return {
            scores: scores,
            rulebook_scores: rulebook_scores,
            leans: leans,
            runner: runner,
            journal: !!(p && p.journal_used),
            notes: p ? (p.notes || []) : [],
            detail: p,
            version: p ? p.version : null,
            computed_at: p ? p.computed_at : null,
            oracle_used: oracle_used,
            oracle_summary: oracle_used ? (read.summary || "") : "",
            justifications: justifications,
            scores_detail: scores_detail,
            overrides: oracle_used ? (read.overrides || []) : [],
            table_impact_flags: oracle_used ? (read.table_impact_flags || []) : []
        };
    }

    // Kind of lean: clear | close | bunched (balanced, top >= 35) | low (balanced, top < 35)
    function leanInfo(d) {
        var s = d.scores, r = rank(s);
        if (d.leans === "balanced" || !AXIS[d.leans]) {
            if (s[r[0]] < 35) return { kind: "low", top: r[0], r: r };
            return { kind: "bunched", top: r[0], r: r, spread: s[r[0]] - s[r[2]] };
        }
        if (d.runner) return { kind: "close", top: d.leans, runner: d.runner, r: r, gap: s[d.leans] - s[d.runner] };
        var next = r.filter(function (a) { return a !== d.leans; })[0];
        return { kind: "clear", top: d.leans, r: r, next: next, gap: s[d.leans] - s[next] };
    }
    function leanSentence(d) {
        if (d.oracle_used && d.oracle_summary) {
            var n = function (a) { return AXIS[a] ? AXIS[a].name : a; };
            var head = (d.leans === "balanced" || !AXIS[d.leans]) ? "Balanced" : "Leans <em>" + n(d.leans) + "</em>";
            return { head: head, why: esc(d.oracle_summary) };
        }
        var li = leanInfo(d), s = d.scores, n = function (a) { return AXIS[a].name; };
        if (li.kind === "low") return { head: "Nothing stands out", why: "Top score is <b>" + n(li.top) + " " + s[li.top] + "</b>, below 35 on every axis." };
        if (li.kind === "bunched") return { head: "Balanced", why: "<b>" + n(li.r[0]) + ", " + n(li.r[1]) + " and " + n(li.r[2]) + "</b> are within " + li.spread + " " + plural(li.spread, "point") + "." };
        if (li.kind === "close") return { head: "Leans <em>" + n(li.top) + "</em>", why: "Close second <b>" + n(li.runner) + "</b>, " + li.gap + " " + plural(li.gap, "point") + " behind." };
        return { head: "Leans <em>" + n(li.top) + "</em>", why: AXIS[li.top].who + ". " + li.gap + " " + plural(li.gap, "point") + " clear of " + n(li.next) + "." };
    }
    function srSummary(d) {
        return AX.map(function (a) { return AXIS[a].name + " " + d.scores[a]; }).join(", ") + ". " + leanSentence(d).head.replace(/<[^>]+>/g, "") + ".";
    }

    // ------------------------------------------------------------------ tiles
    function mono(a) { return '<span class="hud-ps-mono" aria-hidden="true">' + AXIS[a].mono + "</span>"; }
    function nudge(v, journalUsed) {
        if (!journalUsed) return "";
        var exact = Math.round((Number(v) || 0) * 10) / 10, r = Math.round(Number(v) || 0);
        if (!r) return '<span class="hud-ps-nudge is-none" title="Journal nudge: ' + exact + '">±0</span>';
        return '<span class="hud-ps-nudge ' + (r > 0 ? "is-up" : "is-down") + '" title="Journal nudge (capped at ±10): ' + exact + '">' +
            (r > 0 ? "+" : "−") + Math.abs(r) + '<span class="hud-ps-sr"> from journal</span></span>';
    }
    function meter(score, i, ref) {
        return '<span class="hud-ps-meter" role="presentation"><span class="hud-ps-meter-fill" style="width:' + Number(score) + "%;--i:" + i + '"></span>' +
            (ref != null ? '<span class="hud-ps-meter-ref" style="left:' + Number(ref) + '%" title="Your average: ' + Number(ref) + '"></span>' : "") + "</span>";
    }
    // d: view model; ref: {axis: avg} or null; opts.static: no drawer (profile average); opts.skeleton.
    function tiles(d, ref, opts) {
        opts = opts || {};
        var li = leanInfo(d), cls = "hud-ps-b";
        if (li.kind === "close") cls += " is-close";
        if (li.kind === "low" || li.kind === "bunched") cls += " is-balanced";
        if (opts.static) cls += " is-static";
        return '<div class="' + cls + '" role="group" aria-label="Ability scores">' + AX.map(function (a, i) {
            var c = ["hud-ps-tile"];
            if (d.leans === a) c.push("is-lean");
            if (d.runner === a) c.push("is-runner");
            if (li.r[0] === a) c.push("is-top");
            var label = AXIS[a].name + " " + d.scores[a] + (d.leans === a ? ", lean" : d.runner === a ? ", close second" : "");
            var x = d.detail && d.detail[a];
            var tag = opts.static ? "div" : "button";
            var deltaHtml = "";
            if (d.oracle_used && d.rulebook_scores) {
                var delta = d.scores[a] - d.rulebook_scores[a];
                var deltaSign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
                var deltaCls = delta > 0 ? "is-up" : delta < 0 ? "is-down" : "is-none";
                deltaHtml = '<span class="hud-ps-delta ' + deltaCls + '" title="Oracle: ' + d.scores[a] + ' · Rulebook: ' + d.rulebook_scores[a] + ' (' + (delta >= 0 ? '+' : '') + delta + ')">' + deltaSign + Math.abs(delta) + '</span>';
            }
            return "<" + tag + (tag === "button" ? ' type="button" aria-expanded="false" aria-controls="psEv"' : ' role="img"') +
                ' class="' + c.join(" ") + '" data-axis="' + a + '" aria-label="' + esc(label) + '">' +
                '<span class="hud-ps-top">' + mono(a) + '<span class="hud-label">' + AXIS[a].name + "</span>" +
                (d.runner === a ? '<span class="hud-ps-2nd">2nd</span>' : "") +
                (d.oracle_used ? deltaHtml : (x ? nudge(x.journal_nudge, d.journal) : "")) + "</span>" +
                '<span class="hud-ps-num">' + (opts.skeleton ? "—" : d.scores[a]) + "</span>" +
                meter(d.scores[a], i, ref ? ref[a] : null) +
                '<span class="hud-ps-desc">' + AXIS[a].who + "</span></" + tag + ">";
        }).join("") + "</div>";
    }
    function leanBlock(d) {
        var s = leanSentence(d);
        return '<div class="hud-ps-lean"><h4 class="hud-ps-lean-name">' + s.head + '</h4><span class="hud-ps-lean-why">' + s.why + "</span></div>";
    }

    // ------------------------------------------------------------------ evidence drawer
    function cardBtns(cards) {
        return cards.map(function (c) { return '<button type="button" class="hud-cardlink" data-card="' + esc(c) + '">' + esc(c) + "</button>"; }).join("");
    }
    function evRow(e, i, journalUsed) {
        if (e.signal === "journal") {
            var none = !journalUsed;
            var noNudge = !none && !(e.fields && e.fields.length);
            var pts = Number(e.points) || 0;
            var copy = none ? "Cards-only score. A journal can nudge each axis by up to ±10."
                : noNudge ? "Your journal doesn't move this axis." : (e.fields || []).map(fieldCopy).join(" · ");
            return '<li class="hud-ps-ev-row is-journal' + (none || noNudge ? " is-zero" : "") + '"><span class="hud-ps-ev-code">' + (none ? "—" : "JNL") + "</span>" +
                '<span class="hud-ps-ev-label"><b>' + (none ? "No journal" : "Journal") + "</b><span>" + esc(copy) + "</span></span>" +
                '<span class="hud-ps-ev-track is-diverge" role="presentation">' + (pts ? '<i class="' + (pts < 0 ? "is-neg" : "") + '" style="width:' + Math.min(50, Math.abs(pts) * 5) + '%"></i>' : "") + "</span>" +
                '<span class="hud-ps-ev-pts">' + (pts ? (pts > 0 ? "+" : "−") + Math.abs(pts).toFixed(1) : "0.0") + " <small>/ ±10</small></span></li>";
        }
        var cards = e.cards || [], tags = [];
        if (e.signal === "M1") tags = (e.fields || []).map(function (f) { return String(f).replace(/^keyword=/, ""); });
        if (e.signal === "M3") tags = (e.fields || []).map(function (f) { return String(f).replace(/^loop:/, ""); });
        var extra = "";
        if (tags.length) {
            extra = '<span class="hud-ps-ev-cards">' + tags.map(function (t) { return chip(t, "ghost"); }).join("") + "</span>";
        } else if (cards.length) {
            var more = cards.length - CARD_CAP;
            extra = '<span class="hud-ps-ev-cards">' + cardBtns(cards.slice(0, CARD_CAP)) +
                (more > 0 ? '<button type="button" class="hud-chip hud-chip--ghost hud-ps-ev-more" data-more="' + i + '" aria-label="Show ' + more + ' more cards">+' + more + " more</button>" : "") + "</span>";
        }
        var w = e.weight == null ? null : Number(e.weight);
        var sub = Math.max(0, Math.min(1, Number(e.sub_score) || 0));
        var val = FMT[e.signal] ? FMT[e.signal](e.value) : String(e.value);
        return '<li class="hud-ps-ev-row' + (Number(e.points) === 0 ? " is-zero" : "") + '" data-signal="' + esc(e.signal) + '"><span class="hud-ps-ev-code">' + esc(e.signal) + "</span>" +
            '<span class="hud-ps-ev-label"><b>' + esc(e.label) + "</b><span>" + esc(val) + "</span></span>" +
            '<span class="hud-ps-ev-track" role="presentation"><i style="width:' + Math.round(sub * 100) + "%;--i:" + i + '"></i></span>' +
            '<span class="hud-ps-ev-pts">' + (Number(e.points) || 0).toFixed(1) + (w != null ? " <small>/ " + Math.round(w) + "</small>" : "") + "</span>" + extra + "</li>";
    }
    function evidence(d, a) {
        var x = d.detail && d.detail[a];
        if (!x && !d.oracle_used) return '<div class="hud-ps-ev-head"><h4 id="psEvTitle">' + AXIS[a].name + " · " + d.scores[a] + "</h4><p>No evidence stored for this axis.</p></div>";
        var ev = (x && x.evidence) || [];
        // Header sum = the shown (1 dp) card rows added up, so a reader adding the column gets the same number.
        var cardSum = ev.filter(function (e) { return e.signal !== "journal"; }).reduce(function (t, e) { return t + Math.round((Number(e.points) || 0) * 10) / 10; }, 0);
        var nud = Math.round((Number(x ? x.journal_nudge : 0) || 0) * 10) / 10;
        var rows = ev.map(function (e, i) { return evRow(e, i, d.journal); }).join("");

        var sumText = "";
        if (d.oracle_used && d.rulebook_scores) {
            var delta = d.scores[a] - d.rulebook_scores[a];
            sumText = '<span class="hud-ps-ev-sum">Oracle <b>' + d.scores[a] + '</b> · Rulebook <b>' + d.rulebook_scores[a] + '</b> (delta <b>' + (delta >= 0 ? '+' : '−') + Math.abs(delta) + '</b>)</span>';
        } else {
            sumText = '<span class="hud-ps-ev-sum">cards <b>' + cardSum.toFixed(1) + "</b>" + (d.journal ? " " + (nud >= 0 ? "+" : "−") + " journal <b>" + Math.abs(nud).toFixed(1) + "</b>" : "") + " ≈ <b>" + d.scores[a] + "</b></span>";
        }

        var oracleHtml = "";
        if (d.oracle_used) {
            var just = d.justifications && d.justifications[a];
            var cardsCited = d.scores_detail && d.scores_detail[a] && d.scores_detail[a].cards;
            var ov = (d.overrides || []).filter(function (o) { return o.axis === a; })[0];
            oracleHtml = '<div class="hud-ps-ev-oracle">' +
                '<div class="hud-ps-ev-oracle-head">' +
                chip("Oracle verdict", "signal") +
                '<span class="hud-ps-ev-oracle-score">Score <b>' + d.scores[a] + '</b> (Rulebook ' + d.rulebook_scores[a] + ')</span>' +
                '</div>' +
                (just ? '<p class="hud-ps-ev-oracle-why">“' + esc(just) + '”</p>' : "") +
                (cardsCited && cardsCited.length ? '<div class="hud-ps-ev-oracle-cards"><span class="hud-ps-ev-code">Cited:</span> ' + cardBtns(cardsCited) + '</div>' : "") +
                '</div>' +
                (ov ? '<div class="hud-ps-ev-override">' + chip("Rulebook override", "neutral") + '<span>' + esc(ov.reason) + '</span></div>' : "");
        }

        return '<div class="hud-ps-ev-head"><h4 id="psEvTitle">' + AXIS[a].name + " · " + d.scores[a] + "</h4><p>" + AXIS[a].who + "</p>" +
            sumText +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost hud-ps-ev-close" data-close aria-label="Close evidence">Esc</button></div>' +
            oracleHtml +
            (d.oracle_used ? '<div class="hud-ps-ev-rulebook-head">Rulebook evidence signals (base score ' + d.rulebook_scores[a] + '):</div>' : "") +
            '<ol class="hud-ps-ev-list">' + rows + "</ol>" +
            '<div class="hud-ps-ev-foot">' +
            (d.oracle_used ? "Oracle makes the final call. The rulebook signals above were provided as evidence." : "Points = weight × how far the signal got along its band. Every signal is listed, including zeros.") +
            "</div>";
    }

    // ------------------------------------------------------------------ deck panel states
    // state: loading | ok | not_scored | queued | error | unavailable
    // info: {d, ref, refN, computed_at, stale, pending, error, status_line, can_score}
    function stateBox(title, text, actions, isError) {
        return '<div class="hud-ps-state' + (isError ? ' is-error" role="alert' : "") + '"><h4>' + esc(title) + "</h4><p>" + esc(text) + "</p>" +
            (actions ? '<div class="hud-ps-state-actions">' + actions + "</div>" : "") + "</div>";
    }
    var ZERO = { scores: { timmy: 0, johnny: 0, spike: 0, vorthos: 0, melvin: 0 }, leans: "none", runner: null, journal: false };
    function deckPanelBody(state, info) {
        info = info || {};
        if (state === "loading") return '<div class="hud-ps-skel" aria-busy="true">' + tiles(ZERO, null, { static: true, skeleton: true }) + "</div>";
        if (state === "not_scored") return stateBox("Not scored yet",
            "Scores are worked out in the background after a sync or a journal save, usually within a minute.",
            info.can_score === false ? "" : '<button type="button" class="hud-btn hud-btn--sm" data-ps-score>Score now</button>');
        if (state === "queued") return stateBox("Scoring…", "Queued for the background worker. This panel updates when the scores land.", "");
        if (state === "error") return stateBox("Couldn't load ability scores",
            (info.status_line || "The request failed.") + " The rest of the page is fine.",
            '<button type="button" class="hud-btn hud-btn--sm" data-ps-retry>Retry</button>' +
            (info.can_score ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-ps-score>Score again</button>' : ""), true);
        var d = info.d;
        return '<p class="hud-ps-sr">' + esc(srSummary(d)) + "</p>" + leanBlock(d) + tiles(d, info.ref || null) +
            '<div class="hud-ps-evidence" id="psEv" role="region" hidden></div>';
    }
    function deckPanelFoot(info) {
        var d = info.d, bits = (d.notes || []).map(noteChip);
        if (d.oracle_used) bits.push(chip("Oracle read", "signal"));
        else if (info.oracle_status === "offline") bits.push(chip("oracle offline, showing rulebook", "neutral"));
        else if (info.oracle_status === "invalid") bits.push(chip("invalid read, showing rulebook", "signal"));

        if (info.error) bits.push(chip("▲ Last rescore failed · showing earlier scores", "signal"));
        else if (info.stale || info.pending) bits.push(chip("Inputs changed · rescoring", "neutral"));
        if (info.ref) bits.push('<span class="hud-ps-key"><i aria-hidden="true"></i>Your average (' + info.refN + " physical " + plural(info.refN, "deck") + ")</span>");
        var when = ago(info.computed_at);
        if (when) bits.push('<span class="hud-freshness" title="' + esc(info.computed_at) + '">Scored ' + when + (d.version ? " · v" + esc(d.version) : "") + "</span>");
        return '<div class="hud-ps-foot"><span class="hud-ps-notes">' + bits.join("") + '</span><span><a href="/profile">Your player profile →</a></span></div>';
    }

    // Evidence drawer binder. opts.onCard(name) for card buttons.
    function bindEvidence(root, d, opts) {
        opts = opts || {};
        var drawer = root.querySelector("#psEv"), open = null;
        if (!drawer) return null;
        function triggers() { return root.querySelectorAll("[data-axis][aria-controls='psEv']"); }
        function trigger(a) { return root.querySelector("[data-axis='" + a + "'][aria-controls='psEv']"); }
        function hide(refocus) {
            var was = open;
            open = null; drawer.hidden = true; drawer.innerHTML = "";
            triggers().forEach(function (t) { t.setAttribute("aria-expanded", "false"); });
            if (refocus && was && trigger(was)) trigger(was).focus();
        }
        function show(a) {
            if (open === a) { hide(true); return; }
            open = a;
            drawer.innerHTML = evidence(d, a); drawer.hidden = false;
            drawer.setAttribute("aria-labelledby", "psEvTitle");
            triggers().forEach(function (t) { t.setAttribute("aria-expanded", t.getAttribute("data-axis") === a ? "true" : "false"); });
        }
        drawer.addEventListener("click", function (ev) {
            var more = ev.target.closest("[data-more]");
            if (more) {
                var e = d.detail[open].evidence[Number(more.getAttribute("data-more"))];
                more.parentNode.innerHTML = cardBtns(e.cards || []);
                return;
            }
            if (ev.target.closest("[data-close]")) { hide(true); return; }
            var card = ev.target.closest("[data-card]");
            if (card && opts.onCard) opts.onCard(card.getAttribute("data-card"));
        });
        triggers().forEach(function (t) {
            t.addEventListener("click", function () { show(t.getAttribute("data-axis")); });
            t.addEventListener("keydown", function (ev) {
                var list = Array.prototype.slice.call(triggers()), j = list.indexOf(t);
                if (ev.key === "ArrowRight" || ev.key === "ArrowDown") { ev.preventDefault(); list[(j + 1) % list.length].focus(); }
                if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") { ev.preventDefault(); list[(j + list.length - 1) % list.length].focus(); }
            });
        });
        return { show: show, hide: hide, isOpen: function () { return open; } };
    }
    function typing(el) { return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable); }
    function axisForKey(k) { k = String(k || "").toLowerCase(); return AX.filter(function (a) { return AXIS[a].key === k; })[0] || null; }

    // ------------------------------------------------------------------ /profile
    function level(v) { return v >= 80 ? 5 : v >= 65 ? 4 : v >= 50 ? 3 : v >= 35 ? 2 : v >= 20 ? 1 : 0; }
    // Deterministic vertical jitter so stacked dots stay countable.
    function jitter(id, a) { var h = 0, s = String(id) + a; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return ((h % 1000) / 1000 - 0.5) * 0.7; }
    function avgModel(p) {
        var scores = p.average || ZERO.scores, k = p.headline_kind;
        var leans = k === "even" || k === "none" || !p.dominant || p.dominant === "balanced" ? "balanced" : p.dominant;
        return { scores: scores, leans: leans, runner: k === "close" ? p.dominant_runner_up : null, journal: false };
    }
    function leanChip(d) {
        if (d.leans_toward === "balanced" || !AXIS[d.leans_toward]) return chip("Balanced", "neutral");
        return '<span class="hud-chip hud-chip--signal">' + AXIS[d.leans_toward].name + (d.runner_up && AXIS[d.runner_up] ? " · " + AXIS[d.runner_up].mono : "") + "</span>";
    }
    function deckFlags(d) {
        var f = [];
        if (!d.journal_used) f.push("cards only");
        var bad = (d.notes || []).filter(function (n) { return n !== NOTE_NO_JOURNAL; })[0];
        if (bad) f.push("▲ " + bad.replace(" cards without metadata", " no metadata"));
        return f;
    }
    function thumb(d) {
        var uid = (d.commander_uids || [])[0], init = esc(((d.commanders || [])[0] || d.name || "?").charAt(0));
        var img = "";
        if (uid) {
            var id = String(uid).toLowerCase();
            // Eager: 39 small art crops; lazy left the lower rows blank in full-page captures (PS-D).
            img = '<img alt="" src="https://cards.scryfall.io/art_crop/front/' + id.charAt(0) + "/" + id.charAt(1) + "/" + encodeURIComponent(id) + '.jpg" data-fallback="https://api.scryfall.com/cards/' + encodeURIComponent(id) + '?format=image&amp;version=art_crop">';
        }
        return '<span class="hud-ps-thumb" aria-hidden="true">' + init + img + "</span>";
    }
    function rowAttrs(d) {
        return ' data-id="' + esc(d.id) + '" data-lean="' + esc(d.leans_toward) + '" data-journal="' + (d.journal_used ? 1 : 0) + '" data-groups="' + esc((d.groups || []).join(" ")) + '"';
    }
    function failedRow(f) {
        return '<tr class="is-failed" data-id="' + esc(f.id) + '" data-lean="failed"><td data-value="' + esc(f.name || f.id) + '"><span class="hud-ps-deckcell"><span class="hud-ps-thumb" aria-hidden="true">!</span><span>' +
            (f.name ? '<a href="' + esc(deckHref(f.name)) + '">' + esc(f.name) + "</a>" : esc(f.id)) + "<small>" + esc(f.reason || "Scoring failed") + "</small></span></span></td>" +
            '<td colspan="5">Not scored · ' + rescoreBtn(f) + "</td><td>" + chip("Couldn't score", "danger") + "</td></tr>";
    }
    // Heat cell state: the lean is ringed in signal; a close runner-up gets a neutral dashed
    // ring so the cells agree with the "Melvin · V" chip (signal stays on the lean only).
    function heatCls(d, a) { return d.leans_toward === a ? " is-lean" : d.runner_up === a ? " is-runner" : ""; }
    function rescoreBtn(f) { return '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-ps-rescore="' + esc(f.id) + '">Retry</button>'; }
    function tableRows(p) {
        return (p.decks || []).map(function (d) {
            var meta = [(d.commanders || []).join(" + ")].concat(deckFlags(d)).filter(Boolean).join(" · ");
            return "<tr" + rowAttrs(d) + '><td data-value="' + esc(d.name) + '"><span class="hud-ps-deckcell">' + thumb(d) +
                '<span><a href="' + esc(deckHref(d.name)) + '" title="' + esc(d.name) + '">' + esc(d.name) + "</a><small>" + esc(meta) + "</small></span></span></td>" +
                AX.map(function (a) { return '<td class="hud-ps-h' + heatCls(d, a) + '" data-level="' + level(d.scores[a]) + '" data-value="' + d.scores[a] + '"><span>' + d.scores[a] + "</span></td>"; }).join("") +
                '<td data-value="' + esc(d.leans_toward) + '">' + leanChip(d) + "</td></tr>";
        }).join("");
    }
    function failedCard(f) {
        return '<li class="hud-ps-card is-failed" data-id="' + esc(f.id) + '" data-lean="failed"><span class="hud-ps-deckcell"><span>' + (f.name ? '<a href="' + esc(deckHref(f.name)) + '">' + esc(f.name) + "</a>" : esc(f.id)) +
            "<small>" + esc(f.reason || "Scoring failed") + "</small></span></span>" + chip("Couldn't score", "danger") +
            '<span class="hud-ps-card-retry">' + rescoreBtn(f) + "</span></li>";
    }
    function cardRows(p) {
        return (p.decks || []).map(function (d) {
            var meta = [(d.commanders || []).join(" + ")].concat(deckFlags(d)).filter(Boolean).join(" · ");
            return '<li class="hud-ps-card"' + rowAttrs(d) + '><span class="hud-ps-deckcell"><span><a href="' + esc(deckHref(d.name)) + '">' + esc(d.name) + "</a><small>" + esc(meta) + "</small></span></span>" + leanChip(d) +
                '<span class="hud-ps-card-scores">' + AX.map(function (a) { return '<span class="hud-ps-h' + heatCls(d, a) + '" data-level="' + level(d.scores[a]) + '"><span>' + d.scores[a] + "<small>" + AXIS[a].mono + "</small></span></span>"; }).join("") + "</span></li>";
        }).join("");
    }
    function filterOpts(p) {
        var o = [["all", "All", "All decks · key 0"]].concat(AX.map(function (a) { return [a, AXIS[a].mono, "Leans " + AXIS[a].name + " · key " + AXIS[a].key.toUpperCase()]; }))
            .concat([["balanced", "Bal", "Balanced · key B"], ["no_journal", "No journal", "Cards-only decks"]]);
        // Trend callouts can filter by group (PS-3 action values tribal / universe): those
        // options appear only when some deck is in the group.
        ["tribal", "universe"].forEach(function (g) {
            if ((p.decks || []).some(function (d) { return (d.groups || []).indexOf(g) !== -1; })) o.push([g, g.charAt(0).toUpperCase() + g.slice(1), g.charAt(0).toUpperCase() + g.slice(1) + " decks"]);
        });
        return o;
    }
    function deckTable(p) {
        var n = (p.decks || []).length + (p.failed || []).length;
        var seg = '<span class="hud-seg" role="radiogroup" aria-label="Filter by lean">' + filterOpts(p).map(function (o) {
            return '<input type="radio" name="psLean" value="' + o[0] + '" id="psLean-' + o[0] + '"' + (o[0] === "all" ? " checked" : "") + '><label for="psLean-' + o[0] + '" title="' + esc(o[2]) + '">' + esc(o[1]) + "</label>";
        }).join("") + "</span>";
        var head = '<th scope="col" data-sort="text" aria-sort="ascending"><button type="button" class="hud-sort">Deck</button></th>' +
            AX.map(function (a) { return '<th scope="col" class="hud-ps-h" data-sort="num" aria-sort="none" data-axis-col="' + a + '"><button type="button" class="hud-sort" title="' + AXIS[a].name + '">' + AXIS[a].mono + '<span class="hud-ps-sr"> ' + AXIS[a].name + "</span></button></th>"; }).join("") +
            '<th scope="col" data-sort="text" aria-sort="none"><button type="button" class="hud-sort">Leans</button></th>';
        // Failed decks sit in their own tbody / list: HUD.sortTable only sorts tBodies[0], so
        // they stay last under every header or callout sort (Copilot, PR #106).
        var fails = p.failed || [];
        return '<div class="hud-ps-filters">' + seg + '<input type="search" id="psFind" placeholder="Filter decks  /" aria-label="Filter decks by name or commander"><span class="hud-ps-count" id="psCount" aria-live="polite">' + n + " " + plural(n, "deck") + "</span></div>" +
            '<div class="hud-table-wrap hud-ps-table-wrap"><table class="hud-table hud-ps-table" data-hud-sortable id="psTable"><thead><tr>' + head + "</tr></thead><tbody>" + tableRows(p) + "</tbody>" +
            (fails.length ? '<tbody class="hud-ps-failed">' + fails.map(failedRow).join("") + "</tbody>" : "") + "</table></div>" +
            '<ul class="hud-ps-cards" id="psCards">' + cardRows(p) + fails.map(failedCard).join("") + "</ul>";
    }
    function failedList(p) {
        var fails = p.failed || [];
        if (!fails.length) return "";
        return '<div class="hud-table-wrap hud-ps-table-wrap"><table class="hud-table hud-ps-table" id="psTable"><tbody class="hud-ps-failed">' + fails.map(failedRow).join("") + "</tbody></table></div>" +
            '<ul class="hud-ps-cards" id="psCards">' + fails.map(failedCard).join("") + "</ul>";
    }
    function spread(p) {
        var rows = AX.map(function (a) {
            var dots = (p.decks || []).map(function (d) {
                return '<button type="button" class="hud-ps-dot' + (d.leans_toward === a ? " is-lean" : "") + '" style="--x:' + d.scores[a] + ";--jy:" + jitter(d.id, a).toFixed(3) + '" data-deck="' + esc(d.id) + '" aria-label="' + esc(d.name) + ": " + AXIS[a].name + " " + d.scores[a] + '" tabindex="-1"></button>';
            }).join("");
            return '<li class="hud-ps-spread-row">' + mono(a) + '<span class="hud-ps-spread-name">' + AXIS[a].name + '</span><span class="hud-ps-spread-track">' + dots +
                '<span class="hud-ps-avg" style="--x:' + p.average[a] + '" title="Average ' + p.average[a] + '"></span></span>' +
                '<span class="hud-ps-spread-val">avg <b>' + p.average[a] + '</b><span class="hud-ps-lean-n"> · <b>' + ((p.lean_counts || {})[a] || 0) + "</b> lean</span></span></li>";
        }).join("");
        return '<ul class="hud-ps-spread" aria-label="Every deck on each axis">' + rows + '</ul><div class="hud-ps-spread-axis" aria-hidden="true"><span></span><span></span><span><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></span><span></span></div>';
    }
    function callouts(p) {
        return '<ul class="hud-ps-callouts">' + (p.trends || []).map(function (t, i) {
            // group callouts carry both: `of` is the group size (already in the sentence), `unit` = "avg".
            var useUnit = t.unit && (t.of == null || t.kind === "group");
            var small = useUnit ? "<small> " + esc(t.unit) + "</small>" : t.of != null ? "<small> / " + esc(t.of) + "</small>" : "";
            var act = t.action || null, label = "";
            if (act) {
                if (act.type === "filter") label = act.value === "no_journal" ? "Cards-only decks" : "Show them";
                else if (act.type === "sort") label = AXIS[act.value] ? "Sort by " + AXIS[act.value].name : "Sort";
                else if (act.type === "open") label = "Open deck";
            }
            return '<li class="hud-ps-callout' + (i === 0 ? " is-lead" : "") + '" style="--i:' + i + '" data-kind="' + esc(t.kind) + '"><span class="hud-ps-callout-n">' + esc(t.number) + small + "</span><p>" + onlyB(t.text) + "</p>" +
                (label ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-act="' + esc(act.type) + '" data-val="' + esc(act.value) + '">' + label + " →</button>" : "") + "</li>";
        }).join("") + "</ul>";
    }
    function profHead(p, opts) {
        var counts = p.scope_counts || {};
        var scope = '<span class="hud-seg" role="radiogroup" aria-label="Which decks count">' +
            '<input type="radio" name="psScope" id="psScope-physical" value="physical"' + (p.scope !== "all" ? " checked" : "") + '><label for="psScope-physical">Physical · ' + esc(counts.physical != null ? counts.physical : "") + "</label>" +
            '<input type="radio" name="psScope" id="psScope-all" value="all"' + (p.scope === "all" ? " checked" : "") + '><label for="psScope-all">All · ' + esc(counts.all != null ? counts.all : "") + "</label></span>";
        var meta = [];
        if (p.not_scored) meta.push(p.not_scored + " not scored");
        if ((p.failed || []).length) meta.push('<span class="is-danger">' + p.failed.length + " failed</span>");
        if (p.decks_scored) meta.push(p.journal_count + " of " + p.decks_scored + " have a journal");
        return '<header class="hud-ps-prof-head"><div><span class="hud-label">Player profile · from your decks\' ability scores</span>' +
            '<div class="hud-thesis hud-ps-thesis"><span class="hud-thesis-bar" aria-hidden="true"><span class="hud-ps-thesis-bar"></span></span><div><blockquote id="psHeadline">' + onlyB(p.headline) + '</blockquote><p class="hud-ps-thesis-sub">' + onlyB(p.why) + "</p></div></div></div>" +
            '<div class="hud-ps-scope">' + scope + '<span class="hud-freshness" id="psScopeMeta">' + meta.join(" · ") + "</span></div></header>";
    }
    function plainHead() {
        return '<header class="hud-ps-prof-head"><div><span class="hud-label">Player profile</span><h1>Who you are at the table</h1></div></header>';
    }
    function sec(id, n, title, body) {
        return '<section class="hud-ps-sec" aria-labelledby="' + id + '"><div class="hud-section-head"><span class="hud-section-id">' + n + '</span><h2 id="' + id + '">' + title + "</h2></div>" + body + "</section>";
    }
    var WHAT = "Every deck gets five independent 0–100 scores from its cards (and its journal, if it has one): " +
        AX.map(function (a) { return AXIS[a].name + " (" + AXIS[a].who.toLowerCase() + ")"; }).join(", ") + ". Your profile is their average.";
    // state: loading | ok | error ; p = API profile ; info.status_line
    function profileHtml(state, p, info) {
        info = info || {};
        if (state === "loading") {
            return '<header class="hud-ps-prof-head"><div><span class="hud-label">Player profile</span><div class="hud-thesis"><span class="hud-thesis-bar" aria-hidden="true"><span></span></span><div><blockquote>Reading your decks…</blockquote></div></div></div></header>' +
                '<div class="hud-ps-sec hud-ps-skel" aria-busy="true">' + tiles(ZERO, null, { static: true, skeleton: true }) + "</div>";
        }
        if (state === "error") {
            return plainHead() + '<div class="hud-ps-sec">' + stateBox("Couldn't build your profile",
                (info.status_line || "The request failed.") + " Nothing is lost: deck scores are stored per deck and the profile is rebuilt from them.",
                '<button type="button" class="hud-btn hud-btn--sm" data-ps-retry>Retry</button>', true) + "</div>";
        }
        if (!p.decks_scored) {
            var failedNote = (p.failed || []).length ? " " + p.failed.length + " " + plural(p.failed.length, "deck") + " failed to score." : "";
            return plainHead() + '<div class="hud-ps-sec">' + stateBox("No decks scored yet",
                "Your profile is the average of your decks' ability scores. Scores are worked out in the background after a sync." + failedNote,
                '<a class="hud-btn hud-btn--primary hud-btn--sm" href="/decks">Sync decks</a><button type="button" class="hud-btn hud-btn--sm" data-ps-what aria-expanded="false" aria-controls="psWhat">What are these?</button>') +
                '<p class="hud-ps-thesis-sub" id="psWhat" hidden>' + esc(WHAT) + "</p>" +
                (p.scope !== "all" && (p.scope_counts || {}).all ? '<p class="hud-ps-thesis-sub"><a href="/profile?scope=all">Include testing and digital decks →</a></p>' : "") + "</div>" +
                ((p.failed || []).length ? sec("psSecDecks", "03", "Decks that failed to score", failedList(p)) : "");
        }
        var html = profHead(p);
        var avg = sec("psSecAvg", "01", "Your average", tiles(avgModel(p), null, { static: true }) +
            (p.low_data ? "" : '<section class="hud-panel hud-ps-panel-legend"><header class="hud-panel-head"><h3 class="hud-panel-title">Every deck, every axis</h3><div class="hud-panel-meta"><span class="hud-ps-legend"><span><i class="hud-ps-lg-dot"></i>A deck</span><span><i class="hud-ps-lg-dot is-lean"></i>…that leans this way</span><span><i class="hud-ps-lg-avg"></i>Average</span></span></div></header><div class="hud-panel-body">' + spread(p) + "</div></section>"));
        var tr = p.low_data
            ? stateBox(p.decks_to_unlock_trends + " more " + plural(p.decks_to_unlock_trends, "deck") + " to unlock trends", "With " + p.decks_scored + " " + plural(p.decks_scored, "deck") + ", a trend is just a deck. Sync or import more and they'll show up here.", "")
            : (p.trends || []).length ? callouts(p) : stateBox("No trends stand out", "Nothing in your decks crosses a trend threshold yet.", "");
        return html + avg + sec("psSecTr", "02", "Trends", tr) + sec("psSecDecks", "03", "Your decks", deckTable(p));
    }

    // Profile binder: filters, keys, strip-plot tooltip, callout actions, failed retry.
    function bindProfile(root, p, opts) {
        opts = opts || {};
        var H = window.HUD;
        var find = root.querySelector("#psFind"), count = root.querySelector("#psCount");
        function items() { return root.querySelectorAll("#psTable tbody tr, #psCards li"); }
        function apply() {
            if (!find) return;
            var lean = (root.querySelector("input[name=psLean]:checked") || {}).value || "all", q = find.value.trim().toLowerCase(), shown = 0;
            items().forEach(function (el) {
                var l = el.getAttribute("data-lean"), groups = (el.getAttribute("data-groups") || "").split(" ");
                var ok = lean === "all" || (lean === "no_journal" ? el.getAttribute("data-journal") === "0"
                    : (lean === "tribal" || lean === "universe") ? groups.indexOf(lean) !== -1 : l === lean);
                if (ok && q) ok = el.textContent.toLowerCase().indexOf(q) !== -1;
                el.hidden = !ok;
                if (ok && el.tagName === "TR") shown++;
            });
            count.textContent = shown + " " + plural(shown, "deck");
        }
        function setLean(v) { var el = root.querySelector("#psLean-" + v); if (el) { el.checked = true; apply(); } }
        // Phone card list mirrors the table order after every sort (header click or callout),
        // so a sort done on desktop still holds after a resize (Copilot, PR #106).
        function syncCards() {
            var list = root.querySelector("#psCards"), table = root.querySelector("#psTable");
            if (!list || !table || !table.tBodies[0]) return;
            var byId = {};
            Array.prototype.forEach.call(list.children, function (li) { byId[li.getAttribute("data-id")] = li; });
            Array.prototype.forEach.call(table.tBodies[0].rows, function (tr) { var li = byId[tr.getAttribute("data-id")]; if (li) list.appendChild(li); });
            list.querySelectorAll("li.is-failed").forEach(function (li) { list.appendChild(li); });
        }
        if (find) {
            root.querySelectorAll("input[name=psLean]").forEach(function (i) { i.addEventListener("change", apply); });
            find.addEventListener("input", apply);
            if (H) H.initSortable(root);
            var tbl = root.querySelector("#psTable");
            // After HUD's own (earlier-registered) sort handler on the table has run.
            if (tbl) tbl.addEventListener("click", function (ev) { if (ev.target.closest(".hud-sort")) syncCards(); });
        }
        root.querySelectorAll("input[name=psScope]").forEach(function (i) {
            i.addEventListener("change", function () { if (opts.onScope) opts.onScope(i.value); });
        });
        var what = root.querySelector("[data-ps-what]");
        if (what) what.addEventListener("click", function () {
            var box = root.querySelector("#psWhat"); box.hidden = !box.hidden; what.setAttribute("aria-expanded", box.hidden ? "false" : "true");
        });
        var retry = root.querySelector("[data-ps-retry]");
        if (retry && opts.onRetry) retry.addEventListener("click", opts.onRetry);
        root.querySelectorAll("[data-ps-rescore]").forEach(function (b) {
            b.addEventListener("click", function () { if (opts.onRescore) opts.onRescore(b.getAttribute("data-ps-rescore"), b); });
        });
        function scrollToDecks() {
            var s = root.querySelector("#psSecDecks");
            if (s && s.scrollIntoView) s.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        }
        root.querySelectorAll("[data-act]").forEach(function (b) {
            b.addEventListener("click", function () {
                var t = b.getAttribute("data-act"), v = b.getAttribute("data-val");
                if (t === "open") {
                    var d = (p.decks || []).filter(function (x) { return x.id === v; })[0];
                    if (d) window.location.href = deckHref(d.name);
                    return;
                }
                if (t === "filter") setLean(v);
                if (t === "sort") {
                    setLean("all");
                    var th = root.querySelector("th[data-axis-col='" + v + "']");
                    if (th && H) { th.setAttribute("aria-sort", "none"); H.sortTable(root.querySelector("#psTable"), th); syncCards(); }
                }
                scrollToDecks();
            });
        });
        // Strip plot: hover lights every dot of that deck; click jumps to its row.
        var tip = document.createElement("div"); tip.className = "hud-ps-tip"; tip.hidden = true; root.appendChild(tip);
        root.querySelectorAll(".hud-ps-dot").forEach(function (b) {
            var id = b.getAttribute("data-deck");
            b.addEventListener("mouseenter", function () {
                var r = b.getBoundingClientRect(); tip.textContent = b.getAttribute("aria-label"); tip.hidden = false;
                tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, r.left - tip.offsetWidth / 2)) + "px";
                tip.style.top = (r.top - 30) + "px";
                root.querySelectorAll(".hud-ps-dot").forEach(function (x) { if (x.getAttribute("data-deck") === id) x.classList.add("is-hot"); });
            });
            b.addEventListener("mouseleave", function () { tip.hidden = true; root.querySelectorAll(".hud-ps-dot.is-hot").forEach(function (x) { x.classList.remove("is-hot"); }); });
            b.addEventListener("click", function () {
                setLean("all"); if (find) { find.value = ""; apply(); }
                var rows = Array.prototype.filter.call(items(), function (el) { return el.getAttribute("data-id") === id && el.offsetParent !== null; });
                var row = rows[0]; if (!row) return;
                row.classList.add("is-hot");
                row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
                var a = row.querySelector("a"); if (a) a.focus({ preventScroll: true });
                setTimeout(function () { row.classList.remove("is-hot"); }, 1600);
            });
        });
        function onKey(ev) {
            if (!document.body.contains(root)) { document.removeEventListener("keydown", onKey); return; }
            var el = document.activeElement;
            if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
            if (typing(el)) { if (ev.key === "Escape" && el === find) { find.value = ""; apply(); find.blur(); } return; }
            if (!find) return;
            if (ev.key === "/") { ev.preventDefault(); find.focus(); return; }
            var a = axisForKey(ev.key);
            if (a) setLean(a); else if (ev.key.toLowerCase() === "b") setLean("balanced"); else if (ev.key === "0") setLean("all");
        }
        document.addEventListener("keydown", onKey);
        return { setLean: setLean, apply: apply, syncCards: syncCards };
    }

    window.PSUI = {
        AX: AX, AXIS: AXIS, FMT: FMT, esc: esc, onlyB: onlyB, fieldCopy: fieldCopy, ago: ago, level: level,
        deckModel: deckModel, avgModel: avgModel, leanInfo: leanInfo, leanSentence: leanSentence, srSummary: srSummary,
        tiles: tiles, evidence: evidence, deckPanelBody: deckPanelBody, deckPanelFoot: deckPanelFoot,
        profileHtml: profileHtml, bindEvidence: bindEvidence, bindProfile: bindProfile, typing: typing, axisForKey: axisForKey
    };
})();
