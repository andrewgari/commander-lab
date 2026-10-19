/* Commander Lab: Single Card Review view layer & session (CR-F).
 *
 * Implements the Single Card Review modal UI for the deck viewer.
 * Spec: docs/design/card-review/UX_SPEC.md
 *
 * Depends on HUD helpers: HUD.esc, HUD.chip, HUD.pips, HUD.scryfall,
 * HUD.art, HUD.statusChip, HUD.openModal, HUD.closeModal.
 */
(function () {
    "use strict";
    function H() { return (typeof window !== "undefined" && window.HUD) || {}; }
    var HUD = (typeof Proxy !== "undefined")
        ? new Proxy({}, { get: function (_, prop) { return H()[prop]; } })
        : {};

    var esc = function (s) { return (window.HUD && window.HUD.esc) ? window.HUD.esc(s) : String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); };
    var STEPS = ["Evidence", "Opening", "Rebuttal", "Ruling"];
    var BAND = function (c) { return c >= 0.75 ? "High" : c >= 0.55 ? "Medium" : "Low"; };
    var STRENGTH_BAND = function (s) { return s >= 80 ? "Strong" : s >= 60 ? "Solid" : s >= 40 ? "Fair" : "Weak"; };
    var mmss = function (s) { return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };
    var isPhone = function () { return window.matchMedia && window.matchMedia("(max-width: 640px)").matches; };

    function formatRole(role) {
        if (!role) return "";
        var s = String(role).replace(/_/g, " ");
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    function unreviewable(card) {
        if (!card) return null;
        if (card.is_commander || (card.categories && card.categories.indexOf("Commander") >= 0)) {
            return { h: "Commanders aren’t reviewed", p: "Commanders define the deck identity and cannot be removed." };
        }
        if (card.type && /^Basic Land /.test(card.type)) {
            return { h: "Basic lands aren’t reviewed", p: "Basic lands are adjusted in the mana base step." };
        }
        if (card.categories && (card.categories.indexOf("Maybeboard") >= 0 || card.categories.indexOf("Sideboard") >= 0)) {
            return { h: "Not in the 99", p: "Only cards in the active deck are reviewed." };
        }
        return null;
    }

    function actionLabel(deckKind, it) {
        if (deckKind === "physical") {
            return (it && it.ownership === "owned_free") ? "Pull from collection" : "Add to want/buy list";
        }
        return "Swap into deck";
    }

    function decisionFor(deckKind, it) {
        if (deckKind === "digital") return "own";
        return (it && it.ownership === "owned_free") ? "own" : "buy";
    }

    // ---- Subject column --------------------------------------------------
    function subject(s) {
        var c = s.card || {}, role = (s.verdict && s.verdict.role) ? s.verdict.role : null;
        var img = window.HUD ? H().scryfall(c.uid, "normal") : { src: "", fallback: "" };
        var art = window.HUD ? H().art(c.name, img.src, { kind: "card", fallback: img.fallback, meta: c.cmc ? "CMC " + c.cmc : "", colors: c.colors || "" }) : "";
        var kv = '<dl class="hud-kv"><dt>Type</dt><dd>' + esc(c.type || "") + "</dd>" +
            (role ? "<dt>Role</dt><dd>" + (window.HUD ? H().chip(role.label, "data") : "") + "</dd>" : "") +
            "<dt>In deck</dt><dd>" + esc(c.slot || "Mainboard") + "</dd></dl>";
        var thumb = window.HUD ? H().scryfall(c.uid, "small") : { src: "", fallback: "" };
        var vchip = (s.verdict && window.HUD) ? H().chip(s.verdict.ruling === "remove" ? "Remove" : "Keep", s.verdict.ruling === "remove" ? "signal" : "ok", true) : "";
        var id = '<div class="hud-cr-subject-id"><img alt="" src="' + esc(thumb.src) + '" data-fallback="' + esc(thumb.fallback) + '">' +
            '<span><span class="hud-card-name">' + esc(c.name || "") + '</span><span class="hud-art-meta"><span>' + esc(c.type || "") + "</span></span></span>" + vchip + "</div>";
        return art + kv + id;
    }

    // ---- Scope line ------------------------------------------------------
    function scope(s) {
        var phys = s.deck && s.deck.kind === "physical";
        return '<div class="hud-cr-scope">' + (window.HUD ? H().statusChip(phys ? "physical" : "virtual") : "") +
            '<span class="hud-field-hint">' + (phys ? "Physical deck. Suggestions go to your want/buy list; nothing changes in Archidekt."
                                                      : "Digital deck. Suggestions queue onto the Tune change list; apply them in Archidekt.") + "</span>" +
            (s.verdict && s.verdict.reviewed ? '<span class="hud-freshness">Reviewed <b>' + esc(s.verdict.reviewed) + "</b></span>" : "") + "</div>";
    }

    // ---- RUNNING ---------------------------------------------------------
    function running(s) {
        var r = s.run || { step: 0, elapsed: 0 }, slow = r.elapsed >= 30, step = r.step || 0;
        var steps = '<ol class="hud-cr-steps" aria-label="Review progress">' + STEPS.map(function (n, i) {
            var st = i < step ? "is-done" : i === step ? "is-active" : "";
            return '<li class="hud-cr-step ' + st + '"' + (i === step ? ' aria-current="step"' : "") + "><span>" + (i < step ? "✓ " : (i + 1) + " ") + n + "</span></li>";
        }).join("") + "</ol>";
        var msg = slow ? "Taking longer than usual. The model is queued behind another request. You can keep waiting, or close this and come back: the review finishes in the background."
                       : (step === 0 ? "Pulling EDHREC inclusion and similarity to the rest of the deck." :
                          step === 1 ? "Each side makes its opening case." :
                          step === 2 ? "Each side answers the other." : "The judge weighs both cases.") + " Usually 10 to 25 seconds.";
        var job = '<div class="hud-job" role="group" aria-label="Review in progress">' +
            '<div class="hud-job-head"><h3 class="hud-job-title">Reviewing ' + esc(s.card.name) + '</h3><span class="hud-job-clock" aria-hidden="true">' + mmss(r.elapsed) + "</span></div>" +
            steps + '<div class="hud-job-track" aria-hidden="true"><i></i></div><p class="hud-job-msg">' + msg + "</p>" +
            '<div class="hud-job-actions"><span class="hud-field-hint">Closing this window does not cancel the review.</span></div></div>';
        return scope(s) + job + evidence(s) + debate(s);
    }

    function evidence(s) {
        var list = s.evidence || [];
        var cells = list.map(function (e) {
            if (!e.main) return '<div class="hud-cr-ev is-pending"><span class="hud-label">' + esc(e.label) + "</span><b></b><small>checking</small></div>";
            return '<div class="hud-cr-ev is-enter' + (e.miss ? " is-miss" : "") + '" data-k="ev-' + esc(e.label) + '"><span class="hud-label">' + esc(e.label) + "</span><b>" + esc(e.main) + "</b><small>" + esc(e.sub || "") + "</small></div>";
        }).join("");
        return '<div class="hud-cr-evidence" aria-label="Evidence">' + cells + "</div>";
    }

    function debate(s) {
        var r = s.run || {}, sp = r.speaking, args = s.args || [];
        var side = function (k, title) {
            var mine = args.filter(function (a) { return a.side === k; });
            var tag = { opening: "Opening", rebuttal: "Rebuttal" };
            var li = mine.map(function (a) {
                return '<li class="hud-cr-arg' + (a.streaming ? " is-streaming" : "") + '" data-k="arg-' + a.seq + '" style="--seq:' + a.seq + '"><span class="hud-cr-arg-tag" data-side="' + title + '">' + (tag[a.round] || esc(a.round)) + "</span><p>" + esc(a.text) + "</p></li>";
            }).join("");
            var waiting = mine.length < 2 && (r.step || 0) < 3 ? '<li class="hud-cr-arg-wait" aria-hidden="true"></li>' : "";
            return '<section class="hud-cr-side' + (sp === k ? " is-speaking" : "") + '" data-side="' + k + '" aria-label="' + title + ' side"><header class="hud-cr-side-head"><h3>' + title +
                '</h3><span class="hud-cr-side-state">' + (sp === k ? "Arguing" : mine.length >= 2 ? "Rested" : "Waiting") + '</span></header><ol class="hud-cr-args">' + li + waiting + "</ol></section>";
        };
        var judgeText = r.judge || ((r.step || 0) === 3 ? "Deliberating" : (r.step || 0) === 0 ? "Waiting for evidence" : "Listening");
        var judge = '<div class="hud-cr-judge' + ((r.step || 0) === 3 && !r.judge ? " is-active" : "") + '"><span>Judge</span><b>' + esc(judgeText) + "</b></div>";
        return '<div class="hud-cr-debate">' + side("keep", "Keep") + side("remove", "Remove") + "</div>" + judge;
    }

    // ---- VERDICT ---------------------------------------------------------
    function meter(v, extra, cls) {
        return '<span class="hud-cr-meter ' + (cls || "") + '" role="img" aria-label="' + esc(extra.label) + '" style="--v:' + v.toFixed(3) + (extra.ref != null ? ";--ref:" + extra.ref.toFixed(3) : "") + '"><i></i>' + (extra.ref != null ? "<b></b>" : "") + "</span>";
    }

    function verdict(s) {
        var v = s.verdict, rm = v.ruling === "remove", strong = v.strength;
        var conf = Math.round(v.confidence * 100);
        var ruling = '<div><h3 class="hud-cr-ruling"><span>' + (rm ? "Remove" : "Keep") + '</span><small>' + BAND(v.confidence) + " confidence</small></h3>" +
            '<p class="hud-cr-judgeline">' + esc(v.judge_line) + "</p>" +
            '<div class="hud-cr-chips">' + (window.HUD ? H().chip(v.role.label, "data") : "") + (v.role.detail ? '<span class="hud-field-hint">' + esc(v.role.detail) + "</span>" : "") + "</div></div>";

        var strengthMetric = "";
        if (strong != null) {
            var strLabel = "Strength " + strong + " of 100" + (v.deck_median != null ? ", deck median " + v.deck_median : "");
            var refVal = v.deck_median != null ? v.deck_median / 100 : null;
            var medRef = v.deck_median != null ? '<span class="hud-cr-ref">Deck median <b>' + v.deck_median + "</b>" + (strong < v.deck_median ? " · below" : " · above") + "</span>" : "";
            strengthMetric = '<div class="hud-cr-metric"><dt>Strength</dt><dd>' + strong + "<small> / 100 · " + STRENGTH_BAND(strong) + "</small></dd>" +
                meter(strong / 100, { label: strLabel, ref: refVal }) + medRef + "</div>";
        }

        var metrics = '<dl class="hud-cr-metrics">' +
            '<div class="hud-cr-metric"><dt>Confidence</dt><dd>' + conf + "<small>%</small></dd>" + meter(v.confidence, { label: "Confidence " + conf + " percent, " + BAND(v.confidence) }, "hud-cr-meter--conf") + "</div>" +
            strengthMetric + '</dl>';

        var sig = '<div class="hud-cr-signals">' + (v.signals || []).map(function (g) {
            return '<div class="hud-why' + (g.low ? " is-low" : "") + '"><span>' + esc(g.label) + '</span><span class="t"><i style="width:' + Math.round(g.v * 100) + '%"></i></span><span class="v">' + esc(g.text) + "</span></div>";
        }).join("") + "</div>";

        var notes = (v.notes || []).map(function (n) {
            return '<div class="hud-cr-note' + (n.tone === "signal" ? " hud-cr-note--signal" : "") + '" role="note"><span>' + esc(n.text) + "</span>" + (n.action ? '<button type="button" class="hud-btn hud-btn--sm" data-cr="' + n.action[0] + '">' + esc(n.action[1]) + "</button>" : "") + "</div>";
        }).join("");

        var card = '<section class="hud-cr-verdict" data-k="verdict" data-ruling="' + esc(v.ruling) + '" aria-label="Verdict">' + ruling + metrics + sig + "</section>";
        return notes + card + cases(v);
    }

    function cases(v) {
        var phone = isPhone();
        var one = function (k, title) {
            var ruled = v.ruling === k;
            var list = (v.reasons && v.reasons[k]) || [];
            return '<details class="hud-cr-case' + (ruled ? " is-ruled" : "") + '" data-side="' + k + '"' + (ruled || !phone ? " open" : "") + '><summary><h3>Case to ' + title + "</h3>" +
                (ruled ? (window.HUD ? H().chip("Ruled for", k === "remove" ? "signal" : "ok") : "") : (window.HUD ? H().chip("Overruled", "neutral") : "")) + "</summary>" +
                '<ul class="hud-cr-reasons">' + list.map(function (r) { return "<li><span>" + esc(r.text) + "</span>" + (r.cite && window.HUD ? H().chip(r.cite, "ghost") : "") + "</li>"; }).join("") + "</ul></details>";
        };
        return '<div class="hud-cr-cases">' + one("keep", "keep") + one("remove", "remove") + "</div>";
    }

    // ---- REPLACEMENTS ----------------------------------------------------
    function repl(s) {
        var R = s.repl, phys = s.deck && s.deck.kind === "physical", v = s.verdict;
        if (!R || R.state === "hidden") {
            return '<section class="hud-cr-state hud-cr-state--info" aria-label="Replacements"><h3>Keep ' + esc(s.card.name) + '. No replacement needed.</h3><p>Replacements only appear when the ruling is Remove. You can still look at alternatives if you are curious.</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn" data-cr="alts">Show alternatives anyway</button></div></section>';
        }
        var close = (v && v.confidence < 0.6 && v.ruling === "remove") ? '<div class="hud-cr-note hud-cr-note--signal" role="note"><span>Close call. Keep is defensible. Treat these as options, not a recommendation.</span></div>' : "";
        var head = function (n) {
            var lfl = (R.items || []).filter(function (i) { return i.kind === "like_for_like"; }).length;
            return '<div class="hud-cr-repl-head"><div class="hud-section-head" style="margin:0;flex:1 1 auto"><h2>Replace ' + esc(s.card.name) + (n != null ? " · " + n : "") + "</h2></div>" +
                (n ? '<span class="hud-freshness"><b>' + lfl + "</b> like-for-like · <b>" + (n - lfl) + "</b> function change</span>" : "") + "</div>";
        };
        if (R.state === "loading") {
            var g = [0, 1, 2].map(function (i) {
                return '<li class="hud-cr-item hud-cr-item--ghost" style="--i:' + i + '" aria-hidden="true"><span class="hud-cr-thumb"></span><div class="hud-cr-body"><span class="hud-cr-ghost-line" style="width:55%"></span><span class="hud-cr-ghost-line" style="width:35%"></span><span class="hud-cr-ghost-line"></span></div><div class="hud-cr-act"><span class="hud-cr-ghost-line" style="height:2rem"></span></div></li>';
            }).join("");
            return '<section class="hud-cr-repl" aria-label="Replacements" aria-busy="true">' + head() + close + '<p class="hud-field-hint" role="status">Finding replacements that keep its job, or change it if the deck needs something else…</p><ul class="hud-cr-list">' + g + "</ul></section>";
        }
        if (R.state === "error") {
            return '<section class="hud-cr-repl" aria-label="Replacements">' + head() + '<div class="hud-cr-state hud-cr-state--error" role="alert"><h3>Couldn’t load replacements</h3><p>The ruling above is saved. Only the suggestion step failed (<code>' + esc(R.error || "502 upstream") + '</code>).</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn hud-btn--primary" data-cr="retry-repl">Try again</button><button type="button" class="hud-btn hud-btn--ghost" data-cr="cut-only">' + (phys ? "Mark as cut, no replacement" : "Remove without replacing") + "</button></div></div></section>";
        }
        if (R.state === "empty") {
            return '<section class="hud-cr-repl" aria-label="Replacements">' + head(0) + '<div class="hud-cr-state"><h3>No better options found</h3><p>' + esc(R.reason) + '</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn hud-btn--ghost" data-cr="retry">Re-run</button></div></div></section>';
        }
        var chosen = R.chosen;
        var actionErr = R.actionError ? '<div class="hud-cr-note hud-cr-note--signal" role="alert"><span>' + esc(R.actionError) + '</span></div>' : "";
        var list = (R.items || []).map(function (it, i) {
            var img = window.HUD ? H().scryfall(it.uid, "normal") : { src: "", fallback: "" };
            var isC = chosen === it.name, isS = (R.skipped || []).indexOf(it.name) >= 0;
            var kind = window.HUD ? (it.kind === "like_for_like" ? H().chip("= Like-for-like", "data") : H().chip("↻ Function change", "signal")) : "";
            var shift = it.kind === "function_change" ? '<span class="hud-cr-shift"><s>' + esc(it.from_role || "") + "</s> → " + esc(it.role || "") + "</span>" : '<span class="hud-cr-shift">' + esc(it.role || "") + "</span>";
            var own = "";
            if (phys) own = it.ownership === "owned_free" ? '<span class="is-ok">● Owned · ' + esc(it.where || "collection") + "</span>" : it.ownership === "owned_elsewhere" ? "<span>◐ In " + esc(it.where) + "</span>" : "<span>○ Not owned</span>";
            var facts = '<div class="hud-cr-facts"><span>EDHREC <b>' + it.edhrec + '%</b></span><span>' + (it.price ? '$<b>' + it.price.toFixed(2) + "</b>" : "<b>no price</b>") + "</span>" + own + (it.flag ? "<span>" + esc(it.flag) + "</span>" : "") + "</div>";
            var primary = isC ? '<span class="hud-chip hud-chip--ok">' + (phys ? (it.ownership === "owned_free" ? "Pulled" : "On list") : "Queued") + '</span><button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="undo" data-name="' + esc(it.name) + '">Undo</button>'
                : '<button type="button" class="hud-btn" data-cr="pick" data-name="' + esc(it.name) + '">' + (chosen ? "Use this instead" : actionLabel(s.deck ? s.deck.kind : "digital", it)) + '</button>' +
                  (isS ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="unskip" data-name="' + esc(it.name) + '">Restore</button>' : '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="skip" data-name="' + esc(it.name) + '">Skip</button>');
            return '<li class="hud-cr-item' + (isC ? " is-chosen" : "") + (isS ? " is-skipped" : "") + (i === (R.cursor || 0) && !chosen ? " is-current" : "") + '" style="--i:' + i + '" data-k="item-' + esc(it.name) + '" data-name="' + esc(it.name) + '" tabindex="-1">' +
                '<span class="hud-cr-thumb"><img alt="' + esc(it.name) + '" src="' + esc(img.src) + '" data-fallback="' + esc(img.fallback) + '" loading="lazy"></span>' +
                '<div class="hud-cr-body"><div class="hud-cr-name"><span class="hud-card-name">' + esc(it.name) + '</span><span class="hud-art-meta"><span>CMC ' + it.cmc + "</span>" + (window.HUD ? H().pips(it.colors || "", "sm") : "") + "</span></div>" +
                '<div class="hud-cr-kind">' + kind + shift + "</div><p>" + esc(it.rationale) + "</p>" + facts +
                (it.oracle ? '<details class="hud-cr-oracle"><summary>Oracle text</summary><div>' + esc(it.oracle) + "</div></details>" : "") + "</div>" +
                '<div class="hud-cr-act">' + primary + "</div></li>";
        }).join("");
        var tuneUrl = s.tuneUrl || (s.deck ? '/deck/' + encodeURIComponent(s.deck.name) + '/tune' : '#');
        var queued = chosen ? (function () {
            var it = (R.items || []).filter(function (x) { return x.name === chosen; })[0] || {};
            return '<div class="hud-cr-queued" data-k="queued-' + esc(chosen) + '" role="status"><span><b>' + esc(s.card.name) + "</b> → <b>" + esc(chosen) + "</b> " + (phys ? (it.ownership === "owned_free" ? "marked to pull from your collection." : "added to your want/buy list.") : "queued on the change list.") +
                '</span><a class="hud-btn hud-btn--sm" href="' + esc(tuneUrl) + '">' + (phys ? "Open want/buy list" : "Open Tune change list") + " →</a></div>";
        })() : "";
        var keys = '<div class="hud-cr-keys" aria-hidden="true"><span><span class="hud-kbd">j</span> <span class="hud-kbd">k</span> move</span><span><span class="hud-kbd">a</span> ' + (phys ? "add" : "swap") + '</span><span><span class="hud-kbd">x</span> skip</span><span><span class="hud-kbd">Esc</span> close</span></div>';
        return '<section class="hud-cr-repl" aria-label="Replacements">' + head(R.items.length) + close + actionErr + queued + '<ul class="hud-cr-list" id="crList">' + list + "</ul>" + keys + "</section>";
    }

    // ---- ERRORS & BLOCKED ------------------------------------------------
    var ERRORS = {
        offline:  { h: "The review engine is offline", p: "Couldn’t reach the oracle. Nothing was reviewed and nothing was changed. The deterministic numbers below still work without it.", code: "503 oracle_unavailable", acts: [["retry", "Try again", "primary"]] },
        timeout:  { h: "The review timed out after 90 seconds", p: "The model never returned a ruling. This usually clears on its own. The evidence it gathered is kept, so a retry is faster.", code: "504 job_timeout", acts: [["retry", "Try again", "primary"]] },
        failed:   { h: "The review failed", p: "Both sides made their case, but the ruling came back malformed. The arguments are kept below so the work isn’t lost.", code: "502 bad_ruling", acts: [["retry", "Start over", "primary"]] },
        gone:     { h: "This card is no longer in the deck", p: "The deck re-synced from Archidekt while this review was running and the card is gone. Nothing was changed.", code: "404 card_not_in_deck", acts: [["close", "Back to the deck", "primary"]] },
        busy:     { h: "The review engine is busy", p: "Too many reviews are running right now. Please wait a moment and try again.", code: "429 too_many_requests", acts: [["retry", "Try again", "primary"]] },
        network:  { h: "Connection lost", p: "Reconnecting to the review. If this doesn’t come back in a few seconds, the review keeps running on the server; reopen the card to pick it up.", code: "stream closed", acts: [["reconnect", "Reconnect now", "primary"]] }
    };

    function errorView(s) {
        var errCode = (s.error && s.error.code) || "failed";
        var e = ERRORS[errCode] || ERRORS.failed;
        var r = s.run || { step: 0, elapsed: 0 };
        var steps = '<ol class="hud-cr-steps" aria-label="Review progress">' + STEPS.map(function (n, i) {
            return '<li class="hud-cr-step ' + (i < r.step ? "is-done" : i === r.step ? "is-active" : "") + '"><span>' + (i < r.step ? "✓ " : (i + 1) + " ") + n + "</span></li>";
        }).join("") + "</ol>";
        var job = '<div class="hud-job is-failed" role="alert"><div class="hud-job-head"><h3 class="hud-job-title">' + e.h + '</h3><span class="hud-job-clock">' + mmss(r.elapsed) + "</span></div>" + (errCode === "gone" || errCode === "offline" ? "" : steps) +
            '<p class="hud-job-msg">' + esc(e.p) + ' <code>' + esc(e.code) + "</code></p>" +
            '<div class="hud-cr-state-actions">' + e.acts.map(function (a) { return '<button type="button" class="hud-btn ' + (a[2] ? "hud-btn--" + a[2] : "") + '" data-cr="' + a[0] + '">' + a[1] + "</button>"; }).join("") + "</div></div>";
        var extra = "";
        if (errCode === "failed") {
            extra = evidence(s) + debate(Object.assign({}, s, { run: Object.assign({}, r, { speaking: null, step: 3, judge: "No ruling" }) }));
        }
        return scope(s) + job + extra;
    }

    function blocked(s) {
        var b = s.blocked || { h: "Card cannot be reviewed", p: "" };
        return scope(s) + '<section class="hud-cr-state hud-cr-state--info"><h3>' + esc(b.h) + "</h3><p>" + esc(b.p) + '</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn" data-cr="close">Back to the deck</button></div></section>';
    }

    // ---- Public view rendering -------------------------------------------
    function stage(s) {
        if (s.phase === "running") return running(s);
        if (s.phase === "error") return errorView(s);
        if (s.phase === "blocked") return blocked(s);
        var out = scope(s) + verdict(s);
        return out + (s.verdict.ruling === "remove" || (s.repl && s.repl.state !== "hidden") ? repl(s) : repl(Object.assign({}, s, { repl: { state: "hidden" } })));
    }

    function render(s) {
        return '<div class="hud-cr"><aside class="hud-cr-subject" aria-label="Card under review">' + subject(s) + '</aside><div class="hud-cr-stage" id="crStage">' + stage(s) + "</div></div>";
    }

    function title(s) { return s.phase === "running" ? "Reviewing…" : "Card review"; }

    function show(modal, s) {
        modal = typeof modal === "string" ? document.getElementById(modal) : modal;
        if (!modal) return;
        modal.classList.add("hud-cr-modal");
        var body = modal.querySelector("[data-cr-body]"), seen = {}, fx = document.activeElement;
        if (body) {
            body.querySelectorAll("[data-k]").forEach(function (e) { seen[e.getAttribute("data-k")] = 1; });
        }
        var keep = fx && body && body.contains(fx) ? [fx.getAttribute("data-cr"), fx.getAttribute("data-name")] : null, top = modal.scrollTop;
        var titleEl = modal.querySelector("[data-cr-title]");
        if (titleEl) titleEl.textContent = title(s);
        var live = modal.querySelector("[data-cr-live]");
        if (live && live.textContent !== (s.live || "")) live.textContent = s.live || "";
        if (body) body.innerHTML = render(s);
        modal.__crState = s;
        if (body) {
            body.querySelectorAll("[data-k]").forEach(function (e) { if (seen[e.getAttribute("data-k")]) e.style.animation = "none"; });
        }
        modal.scrollTop = top;
        if (keep && keep[0] && body) {
            var again = body.querySelector('[data-cr="' + keep[0] + '"]' + (keep[1] ? '[data-name="' + keep[1].replace(/"/g, '\"') + '"]' : ""));
            if (again) again.focus({ preventScroll: true });
        }
    }

    // Delegated actions
    var handler = function () {};
    if (typeof document !== "undefined" && document.addEventListener) {
        document.addEventListener("click", function (ev) {
            var b = ev.target && ev.target.closest && ev.target.closest("[data-cr]");
            if (!b) return;
            handler(b.getAttribute("data-cr"), b.getAttribute("data-name"), b);
        });
        document.addEventListener("keydown", function (ev) {
            var list = document.getElementById("crList");
            if (!list || ev.metaKey || ev.ctrlKey || ev.altKey) return;
            var t = ev.target;
            if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
            var items = Array.prototype.slice.call(list.querySelectorAll(".hud-cr-item"));
            var cur = items.findIndex(function (x) { return x.classList.contains("is-current"); });
            if (ev.key === "j" || ev.key === "k") {
                ev.preventDefault();
                var n = Math.max(0, Math.min(items.length - 1, cur + (ev.key === "j" ? 1 : -1)));
                items.forEach(function (x, i) { x.classList.toggle("is-current", i === n); });
                if (items[n]) items[n].scrollIntoView({ block: "nearest" });
            } else if ((ev.key === "a" || ev.key === "x") && cur >= 0) {
                ev.preventDefault();
                handler(ev.key === "a" ? "pick" : "skip", items[cur].getAttribute("data-name"), items[cur]);
            }
        });
    }

    function enter(modal) {
        modal = typeof modal === "string" ? document.getElementById(modal) : modal;
        if (!modal || modal.__crEntered) return;
        modal.__crEntered = true;
        modal.__crReturn = document.activeElement;
        var head = modal.querySelector(".hud-panel-title");
        if (head) {
            head.setAttribute("tabindex", "-1");
            head.focus({ preventScroll: true });
        }
        modal.__crKey = function (ev) {
            if (ev.key !== "Tab") return;
            var f = Array.prototype.slice.call(modal.querySelectorAll("button:not([disabled]),a[href],summary,[tabindex]:not([tabindex='-1'])")).filter(function (e) { return e.offsetParent !== null; });
            if (!f.length) return;
            var first = f[0], last = f[f.length - 1], a = document.activeElement;
            if (ev.shiftKey && (a === first || a === head)) { ev.preventDefault(); last.focus(); }
            else if (!ev.shiftKey && a === last) { ev.preventDefault(); first.focus(); }
        };
        modal.addEventListener("keydown", modal.__crKey);
        if (typeof MutationObserver !== "undefined") {
            modal.__crObs = new MutationObserver(function () {
                if (modal.classList.contains("is-open")) return;
                modal.__crObs.disconnect();
                modal.removeEventListener("keydown", modal.__crKey);
                modal.__crEntered = false;
                if (modal.__crReturn && document.contains(modal.__crReturn)) modal.__crReturn.focus();
            });
            modal.__crObs.observe(modal, { attributes: true, attributeFilter: ["class"] });
        }
    }

    // ---- Adapter: backend job -> view state -------------------------------
    function fromJob(job, base, now) {
        now = now || Date.now();
        var s = {
            deck: base.deck || { kind: "digital", name: "" },
            card: base.card || {},
            tuneUrl: base.tuneUrl || ('/deck/' + encodeURIComponent((base.deck && base.deck.name) || "") + '/tune')
        };

        var transcript = (job && job.transcript) || [];
        var args = transcript.map(function (t, i) {
            var text = (t.claims && t.claims[0] && t.claims[0].text) || t.content || "";
            return {
                seq: i + 1,
                side: t.side,
                round: t.round === 1 ? "opening" : "rebuttal",
                text: text,
                streaming: false
            };
        });
        s.args = args;

        var rubric = (job && job.result && job.result.rubric) || (job && job.rubric) || {};
        var roles = (job && job.result && job.result.roles) || (job && job.roles) || {};
        var strength = rubric.strength != null ? Math.round(rubric.strength) : null;
        if (strength == null && job && job.events) {
            var evEv = job.events.filter(function (e) { return e.stage === 'evaluated' && e.strength != null; })[0];
            if (evEv) strength = Math.round(evEv.strength);
        }
        var band = rubric.band ? rubric.band.replace(/_/g, " ") : "";
        var inclusion = (rubric.breakdown && rubric.breakdown.inclusion != null) ? Math.round(rubric.breakdown.inclusion * 100) + "%" : "";
        var primaryRole = roles.primary ? formatRole(roles.primary) : "";

        var evList = [];
        if (strength != null) {
            evList.push({ label: "Strength", main: String(strength), sub: band });
        }
        if (inclusion) {
            evList.push({ label: "EDHREC", main: inclusion, sub: "inclusion" });
        }
        if (primaryRole) {
            evList.push({ label: "Role", main: primaryRole, sub: "primary" });
        }
        s.evidence = evList;

        var createdAt = job && job.created_at ? Date.parse(job.created_at) : now;
        var elapsed = Math.max(0, Math.round((now - createdAt) / 1000));

        var step = 0;
        if (job && (job.stage === "judging" || job.stage === "verdict" || job.stage === "done")) {
            step = 3;
        } else if (job && job.stage === "round_start" && transcript.length === 0) {
            step = 0;
        } else {
            var rounds = transcript.map(function (t) { return t.round; });
            if (rounds.indexOf(2) >= 0) step = 2;
            else if (rounds.indexOf(1) >= 0 || transcript.length > 0) step = 1;
            else step = 0;
        }

        if (job && (job.status === "running" || job.status === "failed")) {
            s.run = {
                step: step,
                elapsed: elapsed,
                speaking: null,
                judge: (job && job.stage === "judging") ? "Deliberating" : null
            };
        }

        if (job && job.status === "failed") {
            if (job.error_kind === "not_allowed") {
                s.phase = "blocked";
                s.blocked = { h: "Commanders aren’t reviewed", p: job.error || "" };
            } else if (job.error_kind === "OracleUnavailable") {
                s.phase = "error";
                s.error = { code: "offline", message: job.error };
            } else if (job.error_kind === "UnknownCardError") {
                s.phase = "error";
                s.error = { code: "gone", message: job.error };
            } else {
                s.phase = "error";
                s.error = { code: "failed", message: job.error };
            }
            return s;
        }

        if (job && job.status === "running") {
            s.phase = "running";
            return s;
        }

        // status === 'done'
        s.phase = "verdict";
        var res = (job && job.result) || {};
        var ruling = res.verdict || "keep";
        var keyReasons = res.key_reasons || [];
        var firstReason = keyReasons[0] || "";
        var firstText = firstReason.replace(/\s*\[.*?\]\s*$/, "").trim();
        var judgeLine = (ruling === "remove" ? "Remove. " : "Keep. ") + firstText;

        var parseReason = function (str) {
            var m = str.match(/^(.*?)\s*\[(.*?)\]\s*$/);
            if (m) return { text: m[1].trim(), cite: m[2].trim() };
            return { text: str.trim(), cite: "" };
        };

        var winReasons = keyReasons.map(parseReason);
        var loseSide = ruling === "remove" ? "keep" : "remove";
        var loseClaims = [];
        var seenClaim = {};
        transcript.filter(function (t) { return t.side === loseSide; }).forEach(function (t) {
            (t.claims || []).forEach(function (c) {
                if (!seenClaim[c.text]) {
                    seenClaim[c.text] = 1;
                    loseClaims.push({ text: c.text, cite: (c.evidence && c.evidence.join(", ")) || "" });
                }
            });
        });

        var reasons = {};
        if (ruling === "remove") {
            reasons.remove = winReasons;
            reasons.keep = loseClaims.length ? loseClaims : [{ text: "Solid card in this archetype.", cite: "" }];
        } else {
            reasons.keep = winReasons;
            reasons.remove = loseClaims.length ? loseClaims : [{ text: "Underperforms deck median.", cite: "" }];
        }

        var signals = [];
        var bd = rubric.breakdown || {};
        if (bd.inclusion != null) {
            signals.push({ label: "EDHREC inclusion", v: Math.max(0, Math.min(1, bd.inclusion)), text: Math.round(bd.inclusion * 100) + "%" });
        }
        if (bd.synergy != null) {
            signals.push({ label: "Synergy", v: Math.max(0, Math.min(1, bd.synergy)), text: (bd.synergy >= 0 ? "+" : "") + bd.synergy.toFixed(2) });
        }
        if (bd.role_need != null) {
            signals.push({ label: "Role need", v: Math.max(0, Math.min(1, bd.role_need)), text: Math.round(bd.role_need * 100) + "%" });
        }

        var notes = [];
        if (res.flags && res.flags.indexOf("edhrec_data_missing") >= 0) {
            notes.push({ text: "No EDHREC data: score uses archetype fallback.", tone: "signal" });
        }
        if (res.provisional) {
            notes.push({ text: "Provisional: leans on the strength score until more games are logged.", tone: "signal" });
        }
        if (res.gate === "pet_card") {
            notes.push({ text: "Flagged as a pet card in your deck profile.", tone: "signal" });
        }

        s.verdict = {
            ruling: ruling,
            confidence: res.confidence != null ? res.confidence : 0.5,
            strength: strength,
            deck_median: null,
            reviewed: "just now",
            role: { label: primaryRole, detail: "" },
            judge_line: judgeLine,
            reasons: reasons,
            signals: signals,
            notes: notes
        };

        if (ruling === "keep") {
            s.repl = { state: "hidden", items: [] };
        } else {
            var rawRepl = res.replacements || [];
            if (!rawRepl.length) {
                var droppedCount = (res.replacement_meta && res.replacement_meta.dropped ? res.replacement_meta.dropped.length : 0);
                s.repl = {
                    state: "empty",
                    reason: (droppedCount || 1) + " candidates were considered, but none met the criteria.",
                    items: []
                };
            } else {
                var items = rawRepl.map(function (r) {
                    return {
                        name: r.card,
                        uid: r.oracle_id || "",
                        cmc: r.cmc != null ? r.cmc : 0,
                        colors: r.colors || "",
                        kind: r.like_for_like ? "like_for_like" : "function_change",
                        role: formatRole(r.role),
                        from_role: formatRole(roles.primary),
                        rationale: r.rationale || "",
                        edhrec: r.edhrec != null ? r.edhrec : 30,
                        price: r.price != null ? r.price : null,
                        ownership: r.tier === "owned" ? "owned_free" : "not_owned",
                        where: r.owned && r.owned.where ? r.owned.where : (r.tier === "owned" ? "collection" : ""),
                        oracle: r.oracle_text || r.oracle || "",
                        flag: r.flags && r.flags.length ? r.flags.join(", ") : ""
                    };
                });
                s.repl = {
                    state: "ready",
                    items: items,
                    chosen: null,
                    skipped: []
                };
            }
        }

        return s;
    }

    // ---- Session ---------------------------------------------------------
    function createSession(opts) {
        var deckId = opts.deckId;
        var deckName = opts.deckName;
        var deckKind = opts.deckKind;
        var card = opts.card;
        var tuneUrl = opts.tuneUrl;
        var fetchFn = opts.fetch || (typeof window !== "undefined" ? window.fetch : null);
        var storage = opts.storage || (typeof window !== "undefined" ? window.localStorage : null);
        var pollMs = opts.pollMs != null ? opts.pollMs : 2000;
        var nowFn = opts.now || Date.now;
        var setTime = opts.setTimeout || (typeof window !== "undefined" ? window.setTimeout : setTimeout);
        var clearTime = opts.clearTimeout || (typeof window !== "undefined" ? window.clearTimeout : clearTimeout);
        var onState = opts.onState || function () {};

        var pollTimer = null;
        var currentJobId = null;
        var currentState = null;
        var startedAt = null;

        var storageKey = 'cr:job:' + String(deckId).toLowerCase() + ':' + String(card.name).toLowerCase();
        var base = {
            deck: { kind: deckKind, name: deckName },
            card: card,
            tuneUrl: tuneUrl || ('/deck/' + encodeURIComponent(deckName) + '/tune')
        };

        function setState(st) {
            currentState = st;
            onState(st);
        }

        async function pollJob(jobId) {
            var now = nowFn();
            if (startedAt && (now - startedAt >= 600000)) {
                setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'timeout' } }));
                return;
            }
            var su = '/api/decks/' + encodeURIComponent(deckId) + '/cards/' + encodeURIComponent(card.name) + '/review';
            try {
                var r = await fetchFn(su + '/jobs/' + encodeURIComponent(jobId));
                if (!r.ok) {
                    if (r.status === 404) {
                        if (storage) storage.removeItem(storageKey);
                        await start(true);
                        return;
                    }
                    setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'failed' } }));
                    return;
                }
                var data = await r.json();
                var job = data.job;
                var st = fromJob(job, base, nowFn());
                if (st.phase === 'running') {
                    setState(st);
                    pollTimer = setTime(function () {
                        pollJob(jobId);
                    }, pollMs);
                } else if (st.phase === 'verdict') {
                    if (st.verdict && st.repl && st.repl.items) {
                        st.live = "Verdict: " + st.verdict.ruling + ", " + Math.round(st.verdict.confidence * 100) + " percent confidence. " + st.repl.items.length + " replacement options.";
                    }
                    setState(st);
                } else {
                    setState(st);
                }
            } catch (err) {
                setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'network', message: err.message } }));
            }
        }

        async function start(forceRerun) {
            if (pollTimer) clearTime(pollTimer);
            startedAt = nowFn();

            var blockedCheck = unreviewable(card);
            if (blockedCheck) {
                setState({ phase: 'blocked', blocked: blockedCheck, deck: base.deck, card: card, tuneUrl: base.tuneUrl });
                return;
            }

            var savedJobId = (!forceRerun && storage) ? storage.getItem(storageKey) : null;
            if (savedJobId) {
                currentJobId = savedJobId;
                setState({ phase: 'running', run: { step: 0, elapsed: 0 }, deck: base.deck, card: card, tuneUrl: base.tuneUrl, args: [], evidence: [] });
                await pollJob(savedJobId);
                return;
            }

            var su = '/api/decks/' + encodeURIComponent(deckId) + '/cards/' + encodeURIComponent(card.name) + '/review';
            setState({ phase: 'running', run: { step: 0, elapsed: 0 }, deck: base.deck, card: card, tuneUrl: base.tuneUrl, args: [], evidence: [] });

            try {
                var r = await fetchFn(su, { method: 'POST' });
                var data = await r.json().catch(function () { return {}; });

                if (r.status === 202) {
                    currentJobId = data.job_id;
                    if (storage) storage.setItem(storageKey, currentJobId);
                    await pollJob(currentJobId);
                } else if (r.status === 409) {
                    var m = (data.error || '').match(/\(job\s+([a-zA-Z0-9]+)\)/);
                    if (m) {
                        currentJobId = m[1];
                        if (storage) storage.setItem(storageKey, currentJobId);
                        await pollJob(currentJobId);
                    } else {
                        setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'busy', message: data.error } }));
                    }
                } else if (r.status === 400) {
                    setState({ phase: 'blocked', blocked: { h: "Commanders aren’t reviewed", p: data.error || "" }, deck: base.deck, card: card, tuneUrl: base.tuneUrl });
                } else if (r.status === 503) {
                    setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'offline', message: data.error } }));
                } else if (r.status === 404) {
                    setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'gone', message: data.error } }));
                } else if (r.status === 429) {
                    setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'busy', message: data.error } }));
                } else {
                    setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'failed', message: data.error } }));
                }
            } catch (err) {
                setState(Object.assign({}, currentState, { phase: 'error', error: { code: 'network', message: err.message } }));
            }
        }

        async function handle(action, name, el) {
            if (action === 'retry') {
                await start(true);
            } else if (action === 'reconnect') {
                if (currentJobId) {
                    setState(Object.assign({}, currentState, { phase: 'running' }));
                    await pollJob(currentJobId);
                } else {
                    await start(false);
                }
            } else if (action === 'skip') {
                if (currentState && currentState.repl) {
                    var skipped = (currentState.repl.skipped || []).slice();
                    if (skipped.indexOf(name) < 0) skipped.push(name);
                    var newRepl = Object.assign({}, currentState.repl, { skipped: skipped });
                    setState(Object.assign({}, currentState, { repl: newRepl }));
                }
            } else if (action === 'unskip') {
                if (currentState && currentState.repl) {
                    var skipped = (currentState.repl.skipped || []).filter(function (x) { return x !== name; });
                    var newRepl = Object.assign({}, currentState.repl, { skipped: skipped });
                    setState(Object.assign({}, currentState, { repl: newRepl }));
                }
            } else if (action === 'pick') {
                if (!currentState || !currentState.repl) return;
                var item = (currentState.repl.items || []).filter(function (x) { return x.name === name; })[0];
                var decision = decisionFor(deckKind, item || {});
                var body = { cut: card.name, decision: decision, add: name };
                var tuneEndpoint = '/api/decks/' + encodeURIComponent(deckId) + '/tune/decisions';
                try {
                    var r = await fetchFn(tuneEndpoint, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });
                    var data = await r.json().catch(function () { return {}; });
                    if (r.ok && data.success !== false) {
                        var newRepl = Object.assign({}, currentState.repl, { chosen: name, actionError: null });
                        setState(Object.assign({}, currentState, { repl: newRepl }));
                    } else {
                        var errMsg = data.error || 'Failed to update tune decision';
                        var newRepl = Object.assign({}, currentState.repl, { chosen: null, actionError: errMsg });
                        setState(Object.assign({}, currentState, { repl: newRepl }));
                    }
                } catch (err) {
                    var newRepl = Object.assign({}, currentState.repl, { chosen: null, actionError: err.message });
                    setState(Object.assign({}, currentState, { repl: newRepl }));
                }
            } else if (action === 'undo') {
                if (!currentState || !currentState.repl) return;
                var prevChosen = currentState.repl.chosen;
                var body = { cut: card.name, decision: null, add: prevChosen };
                var tuneEndpoint = '/api/decks/' + encodeURIComponent(deckId) + '/tune/decisions';
                try {
                    var r = await fetchFn(tuneEndpoint, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });
                    var data = await r.json().catch(function () { return {}; });
                    if (r.ok && data.success !== false) {
                        var newRepl = Object.assign({}, currentState.repl, { chosen: null, actionError: null });
                        setState(Object.assign({}, currentState, { repl: newRepl }));
                    }
                } catch (err) {
                    var newRepl = Object.assign({}, currentState.repl, { actionError: err.message });
                    setState(Object.assign({}, currentState, { repl: newRepl }));
                }
            }
        }

        return {
            start: start,
            handle: handle,
            getState: function () { return currentState; }
        };
    }

    // ---- Mount on deck page ----------------------------------------------
    function mount(opts) {
        var modalId = opts.modalId;
        var deckId = opts.deckId;
        var deckName = opts.deckName;
        var getDeckKind = typeof opts.deckKind === 'function' ? opts.deckKind : function () { return opts.deckKind || 'digital'; };
        var onBack = opts.onBack;

        var modal = typeof document !== 'undefined' ? document.getElementById(modalId) : null;
        var activeSession = null;

        if (modal) {
            modal.addEventListener('click', function (ev) {
                var b = ev.target && ev.target.closest && ev.target.closest('[data-cr]');
                if (!b) return;
                var action = b.getAttribute('data-cr');
                var name = b.getAttribute('data-name');
                if (action === 'back') {
                    if (window.HUD) H().closeModal(modalId);
                    if (onBack && modal.__crIndex != null) onBack(modal.__crIndex);
                } else if (action === 'close') {
                    if (window.HUD) H().closeModal(modalId);
                } else if (activeSession) {
                    activeSession.handle(action, name, b);
                }
            });
        }

        return {
            open: function (card, index) {
                if (!modal && typeof document !== 'undefined') modal = document.getElementById(modalId);
                if (!modal) return;
                modal.__crIndex = index;
                if (window.HUD) H().openModal(modalId);
                enter(modal);
                var kind = getDeckKind();
                activeSession = createSession({
                    deckId: deckId,
                    deckName: deckName,
                    deckKind: kind,
                    card: card,
                    onState: function (st) {
                        show(modal, st);
                    }
                });
                activeSession.start();
            }
        };
    }

    window.CardReview = {
        render: render,
        show: show,
        enter: enter,
        onAction: function (fn) { handler = fn; },
        actionLabel: actionLabel,
        decisionFor: decisionFor,
        unreviewable: unreviewable,
        fromJob: fromJob,
        createSession: createSession,
        mount: mount,
        bands: { conf: BAND, strength: STRENGTH_BAND }
    };
})();
