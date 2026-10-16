/* Commander Lab: Single Card Review view layer (CR-D). Docs only, NOT app code.
 *
 * This is the render layer a coder can lift into templates/deck.html (or a
 * static/js/card_review.js). It is pure view code: `CardReview.render(state)`
 * returns HTML strings, `CardReview.show(modal, state)` paints them, and every
 * button emits `data-cr="<action>"` that the host handles through one
 * delegated listener (`CardReview.onAction`). It talks to NO API: the real
 * transport (POST/SSE/poll) is in UX_SPEC.md section 7.
 *
 * Depends on the shipped HUD helpers only: HUD.esc, HUD.chip, HUD.pips,
 * HUD.scryfall, HUD.art, HUD.openModal / closeModal.
 */
(function () {
    "use strict";

    var esc = function (s) { return HUD.esc(s); };
    var STEPS = ["Evidence", "Opening", "Rebuttal", "Ruling"];
    var BAND = function (c) { return c >= 0.75 ? "High" : c >= 0.55 ? "Medium" : "Low"; };
    var STRENGTH_BAND = function (s) { return s >= 80 ? "Strong" : s >= 60 ? "Solid" : s >= 40 ? "Fair" : "Weak"; };
    var mmss = function (s) { return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };
    var isPhone = function () { return window.matchMedia && window.matchMedia("(max-width: 640px)").matches; };

    // ---- Subject column (the card stays put while the stage changes) -----
    function subject(s) {
        var c = s.card, img = HUD.scryfall(c.uid, "normal"), role = s.verdict ? s.verdict.role : null;
        var art = HUD.art(c.name, img.src, { kind: "card", fallback: img.fallback, meta: c.cmc ? "CMC " + c.cmc : "", colors: c.colors || "" });
        var kv = '<dl class="hud-kv"><dt>Type</dt><dd>' + esc(c.type) + "</dd>" +
            (role ? "<dt>Role</dt><dd>" + HUD.chip(role.label, "data") + "</dd>" : "") +
            "<dt>In deck</dt><dd>" + esc(c.slot || "Mainboard") + "</dd></dl>";
        var thumb = HUD.scryfall(c.uid, "small");
        var vchip = s.verdict ? HUD.chip(s.verdict.ruling === "remove" ? "Remove" : "Keep", s.verdict.ruling === "remove" ? "signal" : "ok", true) : "";
        // Phone header: 40px thumb + name + the verdict chip once there is one.
        var id = '<div class="hud-cr-subject-id"><img alt="" src="' + esc(thumb.src) + '" data-fallback="' + esc(thumb.fallback) + '">' +
            '<span><span class="hud-card-name">' + esc(c.name) + '</span><span class="hud-art-meta"><span>' + esc(c.type) + "</span></span></span>" + vchip + "</div>";
        return art + kv + id;
    }

    // ---- Scope line: which kind of deck, and what the actions will do ----
    function scope(s) {
        var phys = s.deck.kind === "physical";
        return '<div class="hud-cr-scope">' + HUD.statusChip(phys ? "physical" : "virtual") +
            '<span class="hud-field-hint">' + (phys ? "Physical deck. Suggestions go to your want/buy list; nothing changes in Archidekt."
                                                      : "Digital deck. Suggestions queue onto the Tune change list; apply them in Archidekt.") + "</span>" +
            (s.verdict && s.verdict.reviewed ? '<span class="hud-freshness">Reviewed <b>' + esc(s.verdict.reviewed) + "</b></span>" : "") + "</div>";
    }

    // ---- RUNNING: stepper + evidence + live debate ------------------------
    function running(s) {
        var r = s.run, slow = r.elapsed >= 30, step = r.step;
        var steps = '<ol class="hud-cr-steps" aria-label="Review progress">' + STEPS.map(function (n, i) {
            var st = i < step ? "is-done" : i === step ? "is-active" : "";
            return '<li class="hud-cr-step ' + st + '"' + (i === step ? ' aria-current="step"' : "") + "><span>" + (i < step ? "✓ " : (i + 1) + " ") + n + "</span></li>";
        }).join("") + "</ol>";
        var msg = slow ? "Taking longer than usual. The model is queued behind another request. You can keep waiting, or close this and come back: the review finishes in the background."
                       : (step === 0 ? "Pulling EDHREC inclusion and similarity to the rest of the deck." :
                          step === 1 ? "Each side makes its opening case." :
                          step === 2 ? "Each side answers the other." : "The judge weighs both cases.") + " Usually 10 to 25 seconds.";
        var job = '<div class="hud-job' + (slow ? "" : "") + '" role="group" aria-label="Review in progress">' +
            '<div class="hud-job-head"><h3 class="hud-job-title">Reviewing ' + esc(s.card.name) + '</h3><span class="hud-job-clock" aria-hidden="true">' + mmss(r.elapsed) + "</span></div>" +
            steps + '<div class="hud-job-track" aria-hidden="true"><i></i></div><p class="hud-job-msg">' + msg + "</p>" +
            '<div class="hud-job-actions"><span class="hud-field-hint">Closing this window does not cancel the review.</span>' +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="cancel">Cancel review</button></div></div>';
        return scope(s) + job + evidence(s) + debate(s);
    }

    function evidence(s) {
        var e = s.evidence || {}, cell = function (label, v, enter) {
            if (!v) return '<div class="hud-cr-ev is-pending"><span class="hud-label">' + label + "</span><b></b><small>checking</small></div>";
            return '<div class="hud-cr-ev is-enter' + (v.miss ? " is-miss" : "") + '" data-k="ev-' + label + '"><span class="hud-label">' + label + "</span><b>" + esc(v.main) + "</b><small>" + esc(v.sub) + "</small></div>";
        };
        return '<div class="hud-cr-evidence" aria-label="Evidence">' + cell("EDHREC", e.edhrec) + cell("Fit to deck", e.fit) + cell("Role", e.role) + "</div>";
    }

    function debate(s) {
        var sp = s.run.speaking, args = s.args || [];
        var side = function (k, title) {
            var mine = args.filter(function (a) { return a.side === k; });
            var tag = { opening: "Opening", rebuttal: "Rebuttal" };
            var li = mine.map(function (a) {
                return '<li class="hud-cr-arg' + (a.streaming ? " is-streaming" : "") + '" data-k="arg-' + a.seq + '" style="--seq:' + a.seq + '"><span class="hud-cr-arg-tag" data-side="' + title + '">' + tag[a.round] + "</span><p>" + esc(a.text) + "</p></li>";
            }).join("");
            var waiting = mine.length < 2 && s.run.step < 3 ? '<li class="hud-cr-arg-wait" aria-hidden="true"></li>' : "";
            return '<section class="hud-cr-side' + (sp === k ? " is-speaking" : "") + '" data-side="' + k + '" aria-label="' + title + ' side"><header class="hud-cr-side-head"><h3>' + title +
                '</h3><span class="hud-cr-side-state">' + (sp === k ? "Arguing" : mine.length >= 2 ? "Rested" : "Waiting") + '</span></header><ol class="hud-cr-args">' + li + waiting + "</ol></section>";
        };
        var judge = '<div class="hud-cr-judge' + (s.run.step === 3 && !s.run.judge ? " is-active" : "") + '"><span>Judge</span><b>' +
            (s.run.judge || (s.run.step === 3 ? "Deliberating" : s.run.step === 0 ? "Waiting for evidence" : "Listening")) + "</b></div>";
        return '<div class="hud-cr-debate">' + side("keep", "Keep") + side("remove", "Remove") + "</div>" + judge ;
    }

    // ---- VERDICT ---------------------------------------------------------
    function meter(v, extra, cls) { return '<span class="hud-cr-meter ' + (cls || "") + '" role="img" aria-label="' + esc(extra.label) + '" style="--v:' + v.toFixed(3) + (extra.ref != null ? ";--ref:" + extra.ref.toFixed(3) : "") + '"><i></i>' + (extra.ref != null ? "<b></b>" : "") + "</span>"; }

    function verdict(s) {
        var v = s.verdict, rm = v.ruling === "remove", strong = v.strength;
        var conf = Math.round(v.confidence * 100);
        var ruling = '<div><h3 class="hud-cr-ruling"><span>' + (rm ? "Remove" : "Keep") + '</span><small>' + BAND(v.confidence) + " confidence</small></h3>" +
            '<p class="hud-cr-judgeline">' + esc(v.judge_line) + "</p>" +
            '<div class="hud-cr-chips">' + HUD.chip(v.role.label, "data") + (v.role.detail ? '<span class="hud-field-hint">' + esc(v.role.detail) + "</span>" : "") + "</div></div>";
        var metrics = '<dl class="hud-cr-metrics">' +
            '<div class="hud-cr-metric"><dt>Confidence</dt><dd>' + conf + "<small>%</small></dd>" + meter(v.confidence, { label: "Confidence " + conf + " percent, " + BAND(v.confidence) }, "hud-cr-meter--conf") + "</div>" +
            '<div class="hud-cr-metric"><dt>Strength</dt><dd>' + strong + "<small> / 100 · " + STRENGTH_BAND(strong) + "</small></dd>" +
            meter(strong / 100, { label: "Strength " + strong + " of 100, deck median " + v.deck_median, ref: v.deck_median / 100 }) +
            '<span class="hud-cr-ref">Deck median <b>' + v.deck_median + "</b>" + (strong < v.deck_median ? " · below" : " · above") + "</span></div></dl>";
        var sig = '<div class="hud-cr-signals">' + v.signals.map(function (g) {
            return '<div class="hud-why' + (g.low ? " is-low" : "") + '"><span>' + esc(g.label) + '</span><span class="t"><i style="width:' + Math.round(g.v * 100) + '%"></i></span><span class="v">' + esc(g.text) + "</span></div>";
        }).join("") + "</div>";
        var notes = (v.notes || []).map(function (n) {
            return '<div class="hud-cr-note' + (n.tone === "signal" ? " hud-cr-note--signal" : "") + '" role="note"><span>' + esc(n.text) + "</span>" + (n.action ? '<button type="button" class="hud-btn hud-btn--sm" data-cr="' + n.action[0] + '">' + esc(n.action[1]) + "</button>" : "") + "</div>";
        }).join("");
        var card = '<section class="hud-cr-verdict" data-k="verdict" data-ruling="' + v.ruling + '" aria-label="Verdict">' + ruling + metrics + sig + "</section>";
        return notes + card + cases(v);
    }

    function cases(v) {
        var phone = isPhone();
        var one = function (k, title) {
            var ruled = v.ruling === k;
            return '<details class="hud-cr-case' + (ruled ? " is-ruled" : "") + '" data-side="' + k + '"' + (ruled || !phone ? " open" : "") + '><summary><h3>Case to ' + title + "</h3>" +
                (ruled ? HUD.chip("Ruled for", k === "remove" ? "signal" : "ok") : HUD.chip("Overruled", "neutral")) + "</summary>" +
                '<ul class="hud-cr-reasons">' + v.reasons[k].map(function (r) { return "<li><span>" + esc(r.text) + "</span>" + (r.cite ? HUD.chip(r.cite, "ghost") : "") + "</li>"; }).join("") + "</ul></details>";
        };
        return '<div class="hud-cr-cases">' + one("keep", "keep") + one("remove", "remove") + "</div>";
    }

    // ---- REPLACEMENTS ----------------------------------------------------
    function actionLabel(deckKind, it) {
        if (deckKind === "physical") return it.ownership === "owned_free" ? "Pull from collection" : "Add to want/buy list";
        return "Swap into deck";
    }
    function repl(s) {
        var R = s.repl, phys = s.deck.kind === "physical", v = s.verdict;
        if (!R || R.state === "hidden") {
            return '<section class="hud-cr-state hud-cr-state--info" aria-label="Replacements"><h3>Keep ' + esc(s.card.name) + '. No replacement needed.</h3><p>Replacements only appear when the ruling is Remove. You can still look at alternatives if you are curious.</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn" data-cr="alts">Show alternatives anyway</button></div></section>';
        }
        var close = v.confidence < 0.6 && v.ruling === "remove" ? '<div class="hud-cr-note hud-cr-note--signal" role="note"><span>Close call. Keep is defensible. Treat these as options, not a recommendation.</span></div>' : "";
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
            return '<section class="hud-cr-repl" aria-label="Replacements">' + head(0) + '<div class="hud-cr-state"><h3>No better options found</h3><p>' + esc(R.reason) + '</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn" data-cr="widen">Widen search</button><button type="button" class="hud-btn hud-btn--ghost" data-cr="cut-only">' + (phys ? "Mark as cut, no replacement" : "Remove without replacing") + '</button><button type="button" class="hud-btn hud-btn--ghost" data-cr="retry-repl">Re-run</button></div></div></section>';
        }
        var chosen = R.chosen;
        var list = R.items.map(function (it, i) {
            var img = HUD.scryfall(it.uid, "normal"), isC = chosen === it.name, isS = (R.skipped || []).indexOf(it.name) >= 0;
            var kind = it.kind === "like_for_like" ? HUD.chip("= Like-for-like", "data") : HUD.chip("↻ Function change", "signal");
            var shift = it.kind === "function_change" ? '<span class="hud-cr-shift"><s>' + esc(it.from_role) + "</s> → " + esc(it.role) + "</span>" : '<span class="hud-cr-shift">' + esc(it.role) + "</span>";
            var own = "";
            if (phys) own = it.ownership === "owned_free" ? '<span class="is-ok">● Owned · ' + esc(it.where) + "</span>" : it.ownership === "owned_elsewhere" ? "<span>◐ In " + esc(it.where) + "</span>" : "<span>○ Not owned</span>";
            var facts = '<div class="hud-cr-facts"><span>EDHREC <b>' + it.edhrec + '%</b></span><span>' + (it.price ? '$<b>' + it.price.toFixed(2) + "</b>" : "<b>no price</b>") + "</span>" + own + (it.flag ? "<span>" + esc(it.flag) + "</span>" : "") + "</div>";
            var primary = isC ? '<span class="hud-chip hud-chip--ok">' + (phys ? (it.ownership === "owned_free" ? "Pulled" : "On list") : "Queued") + '</span><button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="undo" data-name="' + esc(it.name) + '">Undo</button>'
                : '<button type="button" class="hud-btn" data-cr="pick" data-name="' + esc(it.name) + '">' + (chosen ? "Use this instead" : actionLabel(s.deck.kind, it)) + '</button>' +
                  (isS ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="unskip" data-name="' + esc(it.name) + '">Restore</button>' : '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="skip" data-name="' + esc(it.name) + '">Skip</button>');
            return '<li class="hud-cr-item' + (isC ? " is-chosen" : "") + (isS ? " is-skipped" : "") + (i === (R.cursor || 0) && !chosen ? " is-current" : "") + '" style="--i:' + i + '" data-k="item-' + esc(it.name) + '" data-name="' + esc(it.name) + '" tabindex="-1">' +
                '<span class="hud-cr-thumb"><img alt="' + esc(it.name) + '" src="' + esc(img.src) + '" data-fallback="' + esc(img.fallback) + '" loading="lazy"></span>' +
                '<div class="hud-cr-body"><div class="hud-cr-name"><span class="hud-card-name">' + esc(it.name) + '</span><span class="hud-art-meta"><span>CMC ' + it.cmc + "</span>" + HUD.pips(it.colors || "", "sm") + "</span></div>" +
                '<div class="hud-cr-kind">' + kind + shift + "</div><p>" + esc(it.rationale) + "</p>" + facts +
                (it.oracle ? '<details class="hud-cr-oracle"><summary>Oracle text</summary><div>' + esc(it.oracle) + "</div></details>" : "") + "</div>" +
                '<div class="hud-cr-act">' + primary + "</div></li>";
        }).join("");
        var queued = chosen ? (function () {
            var it = R.items.filter(function (x) { return x.name === chosen; })[0];
            return '<div class="hud-cr-queued" data-k="queued-' + esc(chosen) + '" role="status"><span><b>' + esc(s.card.name) + "</b> → <b>" + esc(chosen) + "</b> " + (phys ? (it.ownership === "owned_free" ? "marked to pull from your collection." : "added to your want/buy list.") : "queued on the change list.") +
                '</span><a class="hud-btn hud-btn--sm" href="#">' + (phys ? "Open want/buy list" : "Open Tune change list") + " →</a></div>";
        })() : "";
        var keys = '<div class="hud-cr-keys" aria-hidden="true"><span><span class="hud-kbd">j</span> <span class="hud-kbd">k</span> move</span><span><span class="hud-kbd">a</span> ' + (phys ? "add" : "swap") + '</span><span><span class="hud-kbd">x</span> skip</span><span><span class="hud-kbd">Esc</span> close</span></div>';
        return '<section class="hud-cr-repl" aria-label="Replacements">' + head(R.items.length) + close + queued + '<ul class="hud-cr-list" id="crList">' + list + "</ul>" +
            '<div class="hud-cr-more-row"><span>None of these?</span><button type="button" class="hud-btn hud-btn--sm" data-cr="more">Show more</button><button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-cr="widen">Widen search</button></div>' + keys + "</section>";
    }

    // ---- ERRORS + BLOCKED ------------------------------------------------
    var ERRORS = {
        offline:  { h: "The review engine is offline", p: "Couldn’t reach the oracle. Nothing was reviewed and nothing was changed. The deterministic numbers below still work without it.", code: "503 oracle_unavailable", acts: [["retry", "Try again", "primary"]] },
        timeout:  { h: "The review timed out after 90 seconds", p: "The model never returned a ruling. This usually clears on its own. The evidence it gathered is kept, so a retry is faster.", code: "504 job_timeout", acts: [["retry", "Try again", "primary"]] },
        failed:   { h: "The judge couldn’t reach a ruling", p: "Both sides made their case, but the ruling came back malformed. The arguments are kept below so the work isn’t lost.", code: "502 bad_ruling", acts: [["retry-ruling", "Re-run the ruling only", "primary"], ["retry", "Start over", ""]] },
        gone:     { h: "This card is no longer in the deck", p: "The deck re-synced from Archidekt while this review was running and the card is gone. Nothing was changed.", code: "404 card_not_in_deck", acts: [["close", "Back to the deck", "primary"]] },
        network:  { h: "Connection lost", p: "Reconnecting to the review. If this doesn’t come back in a few seconds, the review keeps running on the server; reopen the card to pick it up.", code: "stream closed", acts: [["retry", "Reconnect now", "primary"]] }
    };
    function errorView(s) {
        var e = ERRORS[s.error.code], r = s.run || { step: 0, elapsed: 0 };
        var steps = '<ol class="hud-cr-steps" aria-label="Review progress">' + STEPS.map(function (n, i) {
            return '<li class="hud-cr-step ' + (i < r.step ? "is-done" : i === r.step ? "is-active" : "") + '"><span>' + (i < r.step ? "✓ " : (i + 1) + " ") + n + "</span></li>";
        }).join("") + "</ol>";
        var job = '<div class="hud-job is-failed" role="alert"><div class="hud-job-head"><h3 class="hud-job-title">' + e.h + '</h3><span class="hud-job-clock">' + mmss(r.elapsed) + "</span></div>" + (s.error.code === "gone" || s.error.code === "offline" ? "" : steps) +
            '<p class="hud-job-msg">' + esc(e.p) + ' <code>' + esc(e.code) + "</code></p>" +
            '<div class="hud-cr-state-actions">' + e.acts.map(function (a) { return '<button type="button" class="hud-btn ' + (a[2] ? "hud-btn--" + a[2] : "") + '" data-cr="' + a[0] + '">' + a[1] + "</button>"; }).join("") + "</div></div>";
        var extra = "";
        if (s.error.code === "failed") extra = evidence(s) + debate(Object.assign({}, s, { run: Object.assign({}, r, { speaking: null, step: 3, judge: "No ruling" }) }));
        if (s.error.code === "offline" || s.error.code === "timeout") extra = numbersOnly(s);
        return scope(s) + job + extra;
    }
    function numbersOnly(s) {
        var n = s.numbers; if (!n) return "";
        return '<section class="hud-cr-state hud-cr-state--info" aria-label="Numbers only"><h3>What we can say without the model</h3><p>' + esc(n.text) + '</p><div class="hud-cr-evidence" style="width:100%">' +
            [["EDHREC", n.edhrec], ["Strength", n.strength], ["Role", n.role]].map(function (x) { return '<div class="hud-cr-ev"><span class="hud-label">' + x[0] + "</span><b>" + esc(x[1][0]) + "</b><small>" + esc(x[1][1]) + "</small></div>"; }).join("") + "</div></section>";
    }
    function blocked(s) {
        var b = s.blocked;
        return scope(s) + '<section class="hud-cr-state hud-cr-state--info"><h3>' + esc(b.h) + "</h3><p>" + esc(b.p) + '</p><div class="hud-cr-state-actions"><button type="button" class="hud-btn" data-cr="close">Back to the deck</button></div></section>';
    }

    // ---- Public ---------------------------------------------------------
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
        modal.classList.add("hud-cr-modal");
        var body = modal.querySelector("[data-cr-body]"), seen = {}, fx = document.activeElement;
        body.querySelectorAll("[data-k]").forEach(function (e) { seen[e.getAttribute("data-k")] = 1; });
        var keep = fx && body.contains(fx) ? [fx.getAttribute("data-cr"), fx.getAttribute("data-name")] : null, top = modal.scrollTop;
        modal.querySelector("[data-cr-title]").textContent = title(s);
        // Persistent live region (outside the repainted body, or announcements die with the repaint).
        // Announce phase changes and the verdict only; never the streamed tokens.
        var live = modal.querySelector("[data-cr-live]"); if (live && live.textContent !== (s.live || "")) live.textContent = s.live || "";
        body.innerHTML = render(s);
        modal.__crState = s;
        // Repaint rule: an element that was already on screen must not replay its entrance
        // (a streamed argument would flash on every token), and focus + scroll survive the repaint.
        body.querySelectorAll("[data-k]").forEach(function (e) { if (seen[e.getAttribute("data-k")]) e.style.animation = "none"; });
        modal.scrollTop = top;
        if (keep && keep[0]) { var again = body.querySelector('[data-cr="' + keep[0] + '"]' + (keep[1] ? '[data-name="' + keep[1].replace(/"/g, '\\"') + '"]' : "")); if (again) again.focus({ preventScroll: true }); }
    }

    // Delegated actions + the Tune-style keys on the replacement list.
    var handler = function () {};
    document.addEventListener("click", function (ev) {
        var b = ev.target.closest && ev.target.closest("[data-cr]");
        if (!b) return;
        handler(b.getAttribute("data-cr"), b.getAttribute("data-name"), b);
    });
    document.addEventListener("keydown", function (ev) {
        var list = document.getElementById("crList");
        if (!list || ev.metaKey || ev.ctrlKey || ev.altKey) return;
        var t = ev.target; if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
        var items = Array.prototype.slice.call(list.querySelectorAll(".hud-cr-item"));
        var cur = items.findIndex(function (x) { return x.classList.contains("is-current"); });
        if (ev.key === "j" || ev.key === "k") {
            ev.preventDefault();
            var n = Math.max(0, Math.min(items.length - 1, cur + (ev.key === "j" ? 1 : -1)));
            items.forEach(function (x, i) { x.classList.toggle("is-current", i === n); });
            items[n].scrollIntoView({ block: "nearest" });
        } else if ((ev.key === "a" || ev.key === "x") && cur >= 0) {
            ev.preventDefault();
            handler(ev.key === "a" ? "pick" : "skip", items[cur].getAttribute("data-name"), items[cur]);
        }
    });

    // Focus management. HUD.openModal() does not move focus or trap Tab today
    // (found in the CR-D adversarial pass). This is the reference fix; ship it
    // inside HUD.openModal so the card detail modal gets it too.
    function enter(modal) {
        modal = typeof modal === "string" ? document.getElementById(modal) : modal;
        if (modal.__crEntered) return;
        modal.__crEntered = true;
        modal.__crReturn = document.activeElement;
        var head = modal.querySelector(".hud-panel-title");
        head.setAttribute("tabindex", "-1"); head.focus({ preventScroll: true });
        modal.__crKey = function (ev) {
            if (ev.key !== "Tab") return;
            var f = Array.prototype.slice.call(modal.querySelectorAll("button:not([disabled]),a[href],summary,[tabindex]:not([tabindex='-1'])")).filter(function (e) { return e.offsetParent !== null; });
            if (!f.length) return;
            var first = f[0], last = f[f.length - 1], a = document.activeElement;
            if (ev.shiftKey && (a === first || a === head)) { ev.preventDefault(); last.focus(); }
            else if (!ev.shiftKey && a === last) { ev.preventDefault(); first.focus(); }
        };
        modal.addEventListener("keydown", modal.__crKey);
        modal.__crObs = new MutationObserver(function () {
            if (modal.classList.contains("is-open")) return;
            modal.__crObs.disconnect(); modal.removeEventListener("keydown", modal.__crKey); modal.__crEntered = false;
            if (modal.__crReturn && document.contains(modal.__crReturn)) modal.__crReturn.focus();
        });
        modal.__crObs.observe(modal, { attributes: true, attributeFilter: ["class"] });
    }

    window.CardReview = { render: render, show: show, enter: enter, onAction: function (fn) { handler = fn; }, actionLabel: actionLabel, bands: { conf: BAND, strength: STRENGTH_BAND } };
})();
