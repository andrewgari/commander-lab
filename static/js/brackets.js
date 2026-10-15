/* ==========================================================================
   Commander Lab: bracket evaluation view models + renderers (BR-6).
   Direction A (Inline) of the BR-D designer spec (docs/BRACKETS_UX_SPEC.md on
   the BR-D branch, reference only). Reads the stored BR-5 result
   (GET /api/deck/{id}/bracket -> {status, stale, pending, evaluation}) where
   evaluation = analytics.bracket_eval.BracketEvaluation.model_dump().

   The UI never decides severity or tolerance: hard vs advisory comes from
   conflict.severity, power in/out of tolerance from evaluation.power_aligned
   (engine.POWER_ALIGN_TOLERANCE); the tolerance number shown is passed in by
   the page from that same constant. Pure string builders; the pages bind the
   behaviour. Exposes window.Brackets.
   ========================================================================== */
(function () {
    "use strict";
    // hud.js is deferred and loads after page scripts: resolve it lazily.
    function H() { return window.HUD; }
    function esc(s) { return H().esc(s); }
    var MINUS = "\u2212";

    // Plain-language line + hard rules per bracket (BR-D spec §2 copy table;
    // rules mirror analytics/brackets.py BRACKETS).
    var COPY = {
        1: { name: "Exhibition", line: "Theme over power. Show off something odd.",
             rules: "No Game Changers, two-card combos, extra turns or land denial." },
        2: { name: "Core", line: "Straightforward and social. Telegraphed wins.",
             rules: "No Game Changers or two-card combos. A few extra turns, never looped." },
        3: { name: "Upgraded", line: "Powered up. Strong synergy, can win in one big turn.",
             rules: "Up to 3 Game Changers. Two-card combos only late. No land denial." },
        4: { name: "Optimized", line: "Lethal, fast, consistent. Not tuned for the cEDH meta.",
             rules: "Anything legal goes." },
        5: { name: "cEDH", line: "Built for the cEDH metagame. Winning over expression.",
             rules: "Anything legal goes. We never compute 5; you declare it." }
    };
    function name(n) { return COPY[n] ? COPY[n].name : ""; }

    var RULE_LABELS = { game_changers: "Game Changers", mass_land_denial: "Mass land denial",
                        extra_turns: "Extra turns", two_card_combos: "Two-card combos" };
    var CARD_REASON = { game_changers: "Game Changer", mass_land_denial: "Land denial",
                        extra_turns: "Extra turn", two_card_combos: "Two-card combo" };
    var SIGNAL_LABELS = {
        game_changers: "Game Changers", two_card_combos: "Two-card combos", mass_land_denial: "Mass land denial",
        extra_turns: "Extra turns", tutors: "Tutors", exhibition_rule: "Exhibition rule",
        commandersalt_power: "Commander Salt power", our_power_estimate: "Our power estimate",
        power_fallback: "Power fallback", commandersalt_bracket_rating: "Commander Salt bracket rating",
        commandersalt_salt: "Commander Salt salt", edhrec_overlap: "EDHREC overlap", price: "Deck price",
        deck_tags: "Deck tags"
    };

    function plural(n, word, pl) { return n + " " + (n === 1 ? word : (pl || word + "s")); }
    function firstSentence(s) { var m = String(s || "").match(/^.*?[.!?](\s|$)/); return (m ? m[0] : String(s || "")).trim().replace(/[.!?]$/, ""); }

    // ---- State of the API response --------------------------------------------
    // httpStatus 200 + evaluation -> ok | running (stale/pending) ; 404 -> pending
    // (not evaluated) | error ; anything else -> unavailable.
    function state(res, httpStatus) {
        // A failed check keeps the last result (stale): show it as failed, not
        // "re-evaluating", unless a new check is actually queued.
        if (httpStatus === 200 && res && res.evaluation) {
            if (res.pending) return "running";
            if (res.status === "error") return "ok";
            return res.stale ? "running" : "ok";
        }
        if (httpStatus === 404 && res && res.status === "error") return "error";
        if (httpStatus === 404 && res && res.status === "not_evaluated") return "pending";
        return "unavailable";
    }

    function counts(ev) {
        var c = (ev && ev.conflicts) || [], hard = c.filter(function (x) { return x.severity === "hard"; }).length;
        return { all: c.length, hard: hard, adv: c.length - hard };
    }

    // Short bold title per conflict (BR-4 has none: derived from the rule,
    // else the message's first sentence).
    function title(c, ev) {
        var m = String(c.message || ""), n = (c.cards || []).length;
        if (c.rule === "game_changers") {
            var gc = ev && ev.findings && ev.findings.game_changers ? ev.findings.game_changers.count : n;
            return plural(gc, "Game Changer");
        }
        if (c.rule === "two_card_combos") return /early/.test(m) ? "Early two-card combos" : "Two-card combos";
        if (c.rule === "extra_turns") return /loop/i.test(m) ? "Extra-turn loop" : "Extra turns";
        if (c.rule === "mass_land_denial") return "Mass land denial";
        if (c.rule === "bracket_mismatch") return /below/.test(m) ? "Plays below target" : /never estimate/.test(m) ? "cEDH is declared" : "Plays above target";
        if (c.rule === "power_target") return /below/.test(m) ? "Plays below target power" : "Plays above target power";
        if (c.rule === "power_band") return "Outside the bracket's power band";
        if (c.rule === "pet_card") return "Your pet card " + ((c.cards || [])[0] || "");
        if (c.rule === "journal_card") return "Your journal mentions " + ((c.cards || [])[0] || "");
        return firstSentence(m) || (RULE_LABELS[c.rule] || c.rule);
    }

    // card name -> {sev: hard|adv, rules: [...], pet: bool}. Hard wins.
    function offenders(ev) {
        var m = {};
        ((ev && ev.conflicts) || []).forEach(function (c) {
            (c.cards || []).forEach(function (n) {
                var o = m[n] || (m[n] = { sev: "adv", rules: [], pet: false });
                if (o.rules.indexOf(c.rule) === -1) o.rules.push(c.rule);
                if (c.rule === "pet_card") o.pet = true;
                if (c.severity === "hard") o.sev = "hard";
            });
        });
        return m;
    }
    // Caption line under a flagged card: "Game Changer · Over Bracket 1".
    function caption(o, ev) {
        var hard = o.rules.filter(function (r) { return CARD_REASON[r]; });
        var what = hard.length ? hard.map(function (r) { return CARD_REASON[r]; }).join(" + ") : (o.pet ? "Pet card" : "Journal card");
        if (o.pet && hard.length) what += " · pet";
        var t = ev && ev.target_bracket;
        return what + (t ? " · Over Bracket " + t : "");
    }

    // One delta notation for both estimates, coloured by "outside tolerance",
    // never by sign (BR-D §1.2).
    function arrow(d) { return d > 0 ? "\u25b2 +" : d < 0 ? "\u25bc " + MINUS : "\u25cf "; }
    function bracketDelta(ev) {
        if (!ev || ev.target_bracket == null) return { d: null, out: false, text: "" };
        var d = ev.estimated_bracket - ev.target_bracket;
        return { d: d, out: d !== 0, text: d > 0 ? arrow(d) + d + " above target" : d < 0 ? arrow(d) + (-d) + " below target" : "\u25cf on target" };
    }
    function powerDelta(ev, tol) {
        if (!ev || ev.power_delta == null) return { d: null, out: false, text: "" };
        var d = Math.round(ev.power_delta * 10) / 10, out = ev.power_aligned === false;
        return { d: d, out: out, text: arrow(d) + Math.abs(d).toFixed(1) + " \u00b7 " + (out ? "outside" : "within") + " \u00b1" + tol };
    }
    function deltaHtml(x) { return x.text ? '<span class="hud-bracket-delta ' + (x.out ? "is-out" : "is-in") + '">' + esc(x.text) + "</span>" : ""; }

    function sevChip(sev) {
        return sev === "hard"
            ? '<span class="hud-bracket-sev hud-bracket-sev--hard"><span aria-hidden="true">\u25a0</span> Hard rule</span>'
            : '<span class="hud-bracket-sev hud-bracket-sev--adv"><span aria-hidden="true">\u25b2</span> Advisory</span>';
    }
    function cardLinks(cards) {
        return (cards || []).map(function (n) { return '<button type="button" class="hud-cardlink" data-show="' + esc(n) + '">' + esc(n) + "</button>"; }).join("");
    }
    function age(iso) {
        if (!iso) return "";
        var s = (Date.now() - new Date(iso).getTime()) / 1000;
        if (!isFinite(s)) return "";
        if (s < 90) return "just now";
        if (s < 5400) return Math.round(s / 60) + " min ago";
        if (s < 129600) return Math.round(s / 3600) + " h ago";
        return Math.round(s / 86400) + " days ago";
    }
    function fmtValue(v, signal) {
        if (v == null) return "\u2014";
        if (Array.isArray(v)) return v.join(", ");
        // BR-7: units the summary uses ("EDHREC overlap 35%", "$599.11").
        if (typeof v === "number" && signal === "price") return "$" + v.toFixed(2);
        if (typeof v === "number" && signal === "edhrec_overlap") return Math.round(v) + "%";
        if (typeof v === "number") return String(Math.round(v * 100) / 100);
        return String(v);
    }

    // "How this was decided" tray (BR-D §1.3). Kind column dropped: BR-4
    // signals carry no kind/drives (designer open question, simplest choice).
    function explanation(res) {
        var ev = res && res.evaluation, ex = (ev && ev.explanation) || { signals: [], summary: "" };
        var rows = (ex.signals || []).map(function (g) {
            return '<tr><th scope="row">' + esc(SIGNAL_LABELS[g.signal] || g.signal) + "</th>" +
                '<td class="is-num">' + esc(fmtValue(g.value, g.signal)) + "</td><td>" + esc(g.effect) + "</td>" +
                '<td class="hud-muted">' + esc(g.source) + "</td></tr>";
        }).join("");
        var stamp = [res && res.evaluated_at ? "Evaluated " + age(res.evaluated_at) : "", res && res.input_hash ? "input " + String(res.input_hash).slice(0, 6) : ""].filter(Boolean).join(" \u00b7 ");
        return '<details class="hud-tray hud-bracket-why"><summary>How this was decided <span class="hud-muted">\u00b7 ' + plural((ex.signals || []).length, "signal") + "</span></summary>" +
            // BR-7: estimated_bracket_reason is not repeated here; the summary
            // already says it (and the clean banner shows it as its sub-line).
            '<div class="hud-bracket-why-body"><p class="hud-bracket-why-summary">' + esc(ex.summary) + "</p>" +
            '<div class="hud-table-wrap"><table class="hud-table hud-bracket-why-table"><thead><tr><th scope="col">Signal</th><th scope="col" class="is-num">Value</th><th scope="col">Effect</th><th scope="col">Source</th></tr></thead><tbody>' +
            rows + "</tbody></table></div>" +
            '<p class="hud-field-hint">Hard signals set a floor (the lowest bracket the deck can be). Advisory signals never move the bracket.' + (stamp ? " " + esc(stamp) + "." : "") + "</p></div></details>";
    }

    function estBlock(label, big, unit, tgt, delta) {
        return '<div class="hud-bracket-vs"><span class="hud-label">' + esc(label) + '</span><div class="hud-bracket-vs-row"><span class="hud-bracket-vs-big">' + esc(big) +
            (unit ? "<small>" + esc(unit) + "</small>" : "") + "</span>" +
            // BR-7: no dangling "target —" when nothing is set.
            '<span class="hud-bracket-vs-tgt">' + (tgt == null ? "no target" : "target <b>" + esc(tgt) + "</b>") + "</span>" + delta + "</div></div>";
    }

    // Banner (BR-D §2 deck page). opts: {deckUrl, tolerance}.
    function banner(res, httpStatus, opts) {
        opts = opts || {};
        var st = state(res, httpStatus), journalUrl = (opts.deckUrl || "") + "/journal";
        var evalBtn = '<div class="hud-bracket-actions"><button class="hud-btn hud-btn--sm" type="button" data-bracket-evaluate>Evaluate now</button></div>';
        if (st === "pending" || st === "unavailable" || st === "error") {
            var head = st === "pending" ? "Not evaluated yet" : st === "error" ? "Last check failed" : "Bracket check unavailable";
            var hint = st === "pending" ? "The bracket check runs in the background after a sync or a journal change. Nothing to show until it has run once."
                : st === "error" ? "The background check failed and there is no earlier result. " + esc(firstSentence(res && res.error))
                : "Couldn't reach the stored result right now. The rest of the page still works.";
            return '<section class="hud-bracket is-quiet" aria-labelledby="brH"><div><span class="hud-label">Bracket</span><h2 id="brH">' + head + "</h2>" +
                '<p class="hud-field-hint">' + hint + "</p></div>" + (st === "unavailable" ? "" : evalBtn) + "</section>";
        }
        var ev = res.evaluation, c = counts(ev), t = ev.target_bracket, tol = opts.tolerance;
        var est = '<div class="hud-bracket-est">' +
            estBlock("Estimated bracket", ev.estimated_bracket, ev.estimated_bracket_name, t, deltaHtml(bracketDelta(ev))) +
            estBlock("Estimated power", Number(ev.estimated_power_level).toFixed(1), "/10", ev.target_power_level, deltaHtml(powerDelta(ev, tol))) + "</div>";
        var cls, label, h2;
        if (t == null) {
            cls = "is-quiet"; label = "Bracket \u00b7 no target";
            h2 = "This deck plays like <em>Bracket " + esc(ev.estimated_bracket) + "</em> \u00b7 " + esc(ev.estimated_bracket_name);
        } else {
            label = "Bracket \u00b7 target " + esc(t) + " \u00b7 " + esc(ev.target_bracket_name || name(t));
            if (!c.all) { cls = "is-ok"; h2 = '<em aria-hidden="true">\u25cf</em> No conflicts with Bracket ' + esc(t); }
            else { cls = c.hard ? "is-hard" : "is-adv"; h2 = "<em>" + plural(c.all, "conflict") + "</em> with Bracket " + esc(t); }
        }
        var tally = c.all ? '<p class="hud-bracket-tally">' + (c.hard ? '<span class="hud-bracket-t-hard">\u25a0 ' + plural(c.hard, "hard rule") + " broken</span>" : "") +
            (c.adv ? '<span class="hud-bracket-t-adv">\u25b2 ' + plural(c.adv, "advisory", "advisories") + "</span>" : "") + "</p>" : "";
        var sub = t == null ? '<p class="hud-field-hint">No target bracket, so no bracket rules are checked. <a href="' + esc(journalUrl) + '#j-power">Set a target bracket</a> in the Journal.</p>'
            : !c.all ? '<p class="hud-field-hint">' + esc(ev.estimated_bracket_reason) + "</p>" : tally;
        var list = (ev.conflicts || []).map(function (x) {
            var acts = "";
            if (x.severity === "hard" && x.rule === "game_changers") {
                acts = '<span class="hud-bracket-row-acts"><button type="button" class="hud-btn hud-btn--sm" data-bracket-show-first data-show="' + esc((x.cards || [])[0] || "") + '">Show in list</button>' +
                    (ev.minimum_bracket > t ? '<a class="hud-btn hud-btn--sm hud-btn--ghost" href="' + esc(journalUrl) + "?bracket=" + esc(ev.minimum_bracket) + '#j-power">Raise target to ' + esc(ev.minimum_bracket) + "</a>" : "") + "</span>";
            }
            // Bold title, then the message; when the message already opens with the
            // title ("3 Game Changers in the deck; …"), bold that part instead of repeating it.
            // BR-7: also when it opens with a count + the title ("2 two-card combos (…)"),
            // which used to read "Two-card combos. 2 two-card combos …".
            var tt = title(x, ev), msg = String(x.message || ""), lead;
            var num = (msg.match(/^\d+ /) || [""])[0];
            if (num && msg.slice(num.length).toLowerCase().indexOf(tt.toLowerCase()) === 0) tt = msg.slice(0, num.length + tt.length);
            if (msg.toLowerCase().indexOf(tt.toLowerCase()) === 0) lead = "<b>" + esc(msg.slice(0, tt.length)) + "</b>" + esc(msg.slice(tt.length));
            else lead = "<b>" + esc(tt) + ".</b> " + esc(msg);
            return "<li>" + sevChip(x.severity) + "<div><p>" + lead + "</p>" +
                ((x.cards || []).length || acts ? '<div class="hud-bracket-row-cards">' + cardLinks(x.cards) + acts + "</div>" : "") + "</div></li>";
        }).join("");
        var running = st === "running" ? ' <span class="hud-chip hud-chip--data"><span class="hud-chip-dot" aria-hidden="true"></span>Re-evaluating\u2026</span>' : "";
        var failed = res.status === "error" ? '<p class="hud-field-hint">Last check failed' + (res.error_at ? " " + esc(age(res.error_at)) : "") +
            ' \u00b7 showing the previous result \u00b7 <button type="button" class="hud-bracket-link" data-bracket-evaluate>retry</button></p>' : "";
        return '<section class="hud-bracket ' + cls + '" aria-labelledby="brH"' + (st === "running" ? ' aria-busy="true"' : "") + "><div>" +
            '<span class="hud-label">' + label + running + '</span><h2 id="brH" tabindex="-1">' + h2 + "</h2>" + sub + failed + "</div>" + est +
            (list ? '<ul class="hud-bracket-list">' + list + "</ul>" : "") + explanation(res) + "</section>";
    }

    // Header meta chips: target (always neutral) + a severity count button.
    function metaChip(res, httpStatus) {
        var st = state(res, httpStatus);
        if (st !== "ok" && st !== "running") return st === "pending" ? H().chip("Bracket \u00b7 not checked", "neutral") : "";
        var ev = res.evaluation, t = ev.target_bracket, c = counts(ev);
        if (t == null) return '<span class="hud-chip hud-chip--neutral hud-bracket-chip">Est. B' + esc(ev.estimated_bracket) + " \u00b7 " + esc(ev.estimated_bracket_name) + "</span>";
        var tgt = '<span class="hud-chip hud-chip--neutral hud-bracket-chip">B' + esc(t) + " \u00b7 " + esc(ev.target_bracket_name || name(t)) + "</span>";
        return tgt + (c.all
            ? ' <button type="button" data-bracket-jump class="hud-chip hud-bracket-chipbtn ' + (c.hard ? "hud-chip--danger" : "hud-chip--signal") + '">' + (c.hard ? "\u25a0 " : "\u25b2 ") + plural(c.all, "conflict") + "</button>"
            : ' <span class="hud-chip hud-chip--ok">\u25cf No conflicts</span>');
    }

    // ---- Journal ------------------------------------------------------------------
    function definition(n) {
        var b = COPY[n];
        if (!b) return "<b>Unset</b><span>No target, so no bracket checks. The deck page still shows the estimate.</span>";
        return "<b>" + esc(n + " \u00b7 " + b.name) + "</b><span>" + esc(b.line) + "</span><small>" + esc(b.rules) + "</small>";
    }
    // Advisory note when the picked target is below today's hard-rule minimum.
    function belowFloor(target, ev) {
        if (!target || !ev || !(ev.minimum_bracket > target)) return "";
        var why = ((ev.findings && ev.findings.floors) || []).filter(function (f) { return f.floor > target; })
            .map(function (f) { var l = RULE_LABELS[f.rule] || f.rule; return f.rule === "game_changers" ? l : l.toLowerCase(); });
        var what = why.length ? "its " + why.join(" and ") : "its hard-rule hits";
        return "This list is <b>Bracket " + esc(ev.minimum_bracket) + " today</b>: " + esc(what) + " break the " + esc(name(target)) + " rules" +
            ". Saving will flag it on the deck page.";
    }

    window.Brackets = {
        COPY: COPY, name: name, state: state, counts: counts, title: title, offenders: offenders, caption: caption,
        bracketDelta: bracketDelta, powerDelta: powerDelta, sevChip: sevChip, explanation: explanation,
        banner: banner, metaChip: metaChip, definition: definition, belowFloor: belowFloor
    };
})();
