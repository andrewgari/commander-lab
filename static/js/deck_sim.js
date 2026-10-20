/* ==========================================================================
   Simulate tab (SM-4, docs/design/sim/UX_SPEC.md): deck page 8th tab.
   Renders the whole page (builder / live job / saved report) from the real
   SM-3 API (GET/POST .../sim, .../sim/run, .../sim/jobs/{id}), following the
   exact markup and CSS in docs/design/sim/mockup.html + sim.css (now at the
   end of hud.css under the `sm-` prefix). Mirrors templates/deck_review.html
   for the job-poll / localStorage-resume plumbing.
   ========================================================================== */
(function () {
    "use strict";
    function H() { return (typeof window !== "undefined" && window.HUD) || {}; }
    var esc = function (s) { return (window.HUD && window.HUD.esc) ? window.HUD.esc(s) : String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); };

    var DECK_ID = null, API = null, STORE = null;
    var POLL_MS = 2500, STALE_MS = 15 * 60 * 1000;
    var timer = null, clockTimer = null;
    var STATE = { data: null, mode: "standard", openSeat: -1 };

    function $(id) { return document.getElementById(id); }
    function root() { return $("simRoot"); }

    // ---- fetch helpers (mirrors deck_review.html) --------------------------
    function friendly(status, body) {
        if (body && body.error_kind === "busy") return "A simulation for this deck is already running; following it.";
        if (body && body.error && status < 500) return String(body.error).slice(0, 300);
        return status >= 500 ? "The server had a problem (HTTP " + status + "). Try again later." : "Couldn't reach Commander Lab. Check the connection and try again.";
    }
    function call(method, url, body) {
        var opts = { method: method };
        if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
        return fetch(url, opts).then(function (r) {
            return r.text().then(function (t) {
                var j = null; try { j = JSON.parse(t); } catch (e) { j = null; }
                if (!r.ok || (j && j.success === false)) { var err = new Error(friendly(r.status, j)); err.status = r.status; err.body = j; throw err; }
                return j || {};
            });
        }, function () { var err = new Error("Couldn't reach Commander Lab. Check the connection and try again."); err.status = 0; throw err; });
    }
    function remember(j) { try { localStorage.setItem(STORE, JSON.stringify(j)); } catch (e) { /* private mode */ } }
    function forget() { try { localStorage.removeItem(STORE); } catch (e) { /* ignore */ } }
    function saved() { try { return JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { return null; } }
    function mmss(ms) { var s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }

    // ---- small render helpers ------------------------------------------------
    function chip(t, tone, dot, title) {
        return '<span class="hud-chip hud-chip--' + esc(tone || "neutral") + '"' + (title ? ' title="' + esc(title) + '"' : "") + '>' +
            (dot ? '<span class="hud-chip-dot" aria-hidden="true"></span>' : "") + esc(t) + "</span>";
    }
    function card(name) { return H().cardName(name, (window.HUD_CARD_REFS || null)); }
    function pct(x) { return x == null ? null : Math.round(x * 100); }
    function fmtPct(x) { var p = pct(x); return p == null ? '<span class="sm-na">n/a</span>' : p + "%"; }
    function fmtNum(x, nd) { return x == null ? '<span class="sm-na">n/a</span>' : (nd ? Number(x).toFixed(nd) : String(x)); }
    function sec(code, title, body) {
        return '<section class="hud-doc-section" aria-labelledby="sm' + code + '"><div class="hud-section-head"><span class="hud-section-id">' + code +
            '</span><h2 id="sm' + code + '">' + title + "</h2></div>" + body + "</section>";
    }
    function relTime(iso) {
        if (!iso) return "";
        var age = window.DeckAnalytics && window.DeckAnalytics.age ? window.DeckAnalytics.age(iso) : "";
        return age ? "Built " + age : "Built " + new Date(iso).toLocaleString();
    }

    // ---- Builder ---------------------------------------------------------
    function modeTiles(mode) {
        var modes = [
            ["standard", "Standard pod", "Cavalry Charge, Explorers of the Deep, Endless Punishment. The same three every game."],
            ["random", "Random each game", "Three new opponents on every game, from Forge precons and your synced decks."],
            ["specific", "Specific", "You pick 1 to 5 opponents from precons and synced decks. Fixed for the run."],
            ["rapid1v1", "Rapid 1v1", "Test against a single opponent. 5 games. Fastest way to see how a specific matchup plays."]
        ];
        return '<div class="sm-modes" role="radiogroup" aria-labelledby="lgMode">' + modes.map(function (m) {
            return '<div class="sm-mode"><input type="radio" name="mode" id="m-' + m[0] + '" value="' + m[0] + '"' + (mode === m[0] ? " checked" : "") + '>' +
                '<label for="m-' + m[0] + '"><span class="sm-mode-name">' + m[1] + '</span><span class="sm-mode-desc">' + m[2] +
                '</span></label></div>';
        }).join("") + "</div>";
    }
    // The daemon's standard pod is a fixed trio and the API does not expose its
    // roster (build_opponent_pool only lists the pickable pool), so the three
    // names/sets are taken verbatim from the UX_SPEC's own table (§4.2) -- a
    // noted deviation: if the daemon's standard roster ever changes, this list
    // needs a matching edit here (or SM-3 should start exposing it).
    var STANDARD_POD = [["Cavalry Charge", "MOC 2023"], ["Explorers of the Deep", "LCC 2023"], ["Endless Punishment", "DSC 2024"]];
    function seatsHtml() {
        var ai = ["Reckless", "Default", "Cautious"];
        return '<ol class="sm-seats" aria-label="Standard pod">' + [0, 1, 2].map(function (k) {
            var s = STANDARD_POD[k];
            return '<li class="sm-seat"><span class="sm-seat-id"><b>Seat ' + (k + 2) + '</b> · ' + ai[k] + '</span><span class="sm-seat-name">' +
                esc(s[0]) + '</span><span class="sm-seat-set">' + esc(s[1]) + "</span></li>";
        }).join("") + "</ol>";
    }
    function comboRows(opts, taken, q) {
        var precons = opts.filter(function (o) { return o.source === "precon"; });
        var decks = opts.filter(function (o) { return o.source !== "precon"; });
        var qq = (q || "").toLowerCase();
        function hi(name) {
            if (!qq) return esc(name);
            var idx = name.toLowerCase().indexOf(qq);
            if (idx < 0) return esc(name);
            return esc(name.slice(0, idx)) + "<mark>" + esc(name.slice(idx, idx + qq.length)) + "</mark>" + esc(name.slice(idx + qq.length));
        }
        function row(o) {
            var disabled = taken[o.id] && taken[o.id] !== o._seatKey;
            var right = o.source === "precon" ? ((o.set || "") + " " + (o.year || "")).trim() : (disabled ? ("in Seat " + taken[o.id]) : (o.status || "synced"));
            return '<li class="sm-combo-opt" role="option" data-id="' + esc(o.id) + '"' + (disabled ? ' aria-disabled="true"' : '') + '>' +
                "<span>" + hi(o.name) + "</span><small>" + esc(right) + "</small></li>";
        }
        var filt = function (list) { return qq ? list.filter(function (o) { return o.name.toLowerCase().indexOf(qq) > -1; }) : list; };
        var p = filt(precons), d = filt(decks);
        if (!p.length && !d.length) return '<li class="sm-combo-empty">No deck matches &ldquo;' + esc(q) + '&rdquo;.</li>';
        var out = "";
        if (p.length) out += '<li class="sm-combo-group" role="presentation">Forge precons · ' + precons.length + "</li>" + p.map(row).join("");
        if (d.length) out += '<li class="sm-combo-group" role="presentation">Your synced decks · ' + decks.length + "</li>" + d.map(row).join("");
        return out;
    }
    function pickers(pool, picks, modePrefix, count) {
        count = count || 3;
        var ai = ["Reckless", "Default", "Cautious", "Reckless", "Cautious"];
        var opts = (pool && pool.opponents) || [];
        var byId = {}; opts.forEach(function (o) { byId[o.id] = o; });
        var taken = {}; picks.forEach(function (id, k) { if (id) taken[id] = k + 2; });
        var arr = [];
        for (var k = 0; k < count; k++) arr.push(k);
        return '<div class="sm-pickers">' + arr.map(function (k) {
            var val = picks[k] ? (byId[picks[k]] ? byId[picks[k]].name : picks[k]) : "";
            var placeholder = modePrefix === "standard" ? "Default (" + STANDARD_POD[k][0] + ")" : "Search " + opts.length + " decks…";
            var idBase = modePrefix + "opp" + k;
            return '<div class="sm-combo" data-seat="' + k + '" data-mode="' + modePrefix + '"><label for="' + idBase + '"><b>Seat ' + (k + 2) + "</b> · " + ai[k] + '</label>' +
                '<input id="' + idBase + '" role="combobox" aria-expanded="false" aria-controls="lb' + idBase + '" aria-autocomplete="list" autocomplete="off" ' +
                'placeholder="' + placeholder + '" value="' + esc(val) + '" data-value="' + esc(picks[k] || "") + '">' +
                '<ul class="sm-combo-list" id="lb' + idBase + '" role="listbox" aria-label="Seat ' + (k + 2) + ' opponent" hidden>' +
                comboRows(opts.map(function (o) { return Object.assign({ _seatKey: k + 2 }, o); }), taken, "") + "</ul>" +
                '<p class="sm-combo-err" role="alert" hidden></p></div>';
        }).join("") + "</div>";
    }
    function detail(mode, pool, picks) {
        var opps = (pool && pool.opponents) || [];
        var preconCount = opps.filter(function (o) { return o.source === "precon"; }).length;
        var syncedCount = opps.length - preconCount;
        return '<div class="sm-opp" id="oppStandard"' + (mode === "standard" ? "" : " hidden") + '>' + pickers(pool, picks, "standard", 3) +
            '<p class="sm-opp-note" style="margin-top:var(--sp-2)">Search by name, or leave empty for default (Cavalry Charge, Explorers of the Deep, Endless Punishment). Best for comparing one change against the last run.</p></div>' +
            '<div class="sm-opp" id="oppRandom"' + (mode === "random" ? "" : " hidden") + '><p class="sm-opp-note"><b style="color:var(--text-1)">Fresh pod every game.</b> Three distinct decks drawn at random from <b style="color:var(--text-1)">' +
            opps.length + '</b>: ' + preconCount + " Forge precons plus " + syncedCount +
            ' of your synced decks. This deck and its mirrors are left out. The log in section 04 shows which pod each game got.</p></div>' +
            '<div class="sm-opp" id="oppSpecific"' + (mode === "specific" ? "" : " hidden") + '>' + pickers(pool, picks, "specific", 5) +
            '<p class="sm-opp-note" style="margin-top:var(--sp-2)">Search by name. Test against 1 to 5 opponents. Seat order sets the AI: Reckless, Default, Cautious.</p></div>' +
            '<div class="sm-opp" id="oppRapid1v1"' + (mode === "rapid1v1" ? "" : " hidden") + '>' + pickers(pool, picks, "rapid", 1) +
            '<p class="sm-opp-note" style="margin-top:var(--sp-2)">Search by name. Test 1v1 matchup.</p></div>';
    }
    function builderBody(btnLabel, mode, pool, picks, games, notes) {
        var opCount = 3;
        if (mode === "rapid1v1") opCount = 1;
        if (mode === "specific") {
            opCount = picks.filter(function(p) { return p; }).length || 3;
        }
        var multiplier = (opCount + 1) / 4;
        return '<form id="simForm" class="sm-form" novalidate>' +
            '<fieldset><legend id="lgMode">Opponents</legend>' + modeTiles(mode) + "</fieldset>" + detail(mode, pool, picks) +
            '<fieldset><legend id="lgGames">Games</legend><div class="sm-count"><div class="hud-seg" role="radiogroup" aria-labelledby="lgGames">' +
            '<input type="radio" name="games" id="g5" value="5"' + (games === 5 ? " checked" : "") + '><label for="g5">5 games · ~' + Math.ceil(2 * multiplier) + ' min</label>' +
            '<input type="radio" name="games" id="g20" value="20"' + (games === 20 ? " checked" : "") + '><label for="g20">20 games · ~' + Math.ceil(10 * multiplier) + ' min</label>' +
            '<input type="radio" name="games" id="g60" value="60"' + (games === 60 ? " checked" : "") + '><label for="g60">60 games · ~' + Math.ceil(30 * multiplier) + ' min</label></div>' +
            '<span class="sm-count-note">More games = tighter numbers. 20 is enough to spot a pattern, 60 to trust it.</span></div></fieldset>' +
            '<div class="hud-field"><label for="notes">Test notes <span class="hud-muted">(optional)</span></label>' +
            '<textarea id="notes" maxlength="500" rows="3" placeholder="e.g. Watch if Kaalia gets removed before attacking, or if colors brick on T4">' + esc(notes || "") + '</textarea>' +
            '<div class="sm-notes-foot"><span class="hud-field-hint">Oracle answers this directly in section 01.</span><span class="sm-counter" aria-live="off">' + (notes || "").length + ' / 500</span></div></div>' +
            '<p class="sm-error" id="simError" role="alert"></p>' +
            '<div class="sm-runbar"><button class="hud-btn hud-btn--primary" type="submit" id="runBtn">' + esc(btnLabel) + '</button>' +
            '<p class="sm-runbar-hint">Runs on the Forge server, ~30 s a game. You can leave this page; the report saves itself when it finishes.</p></div></form>';
    }
    function builder(d) {
        var pool = d.pool, hasReport = !!(d.sim && d.sim.report);
        var mode = STATE.mode, games = STATE.games || 20, notes = STATE.notes || "";
        var picks = STATE.picks || ["", "", ""];
        if (!hasReport) {
            return '<section class="hud-panel hud-panel--signal sm-builder" aria-labelledby="bldH"><header class="hud-panel-head">' +
                '<h2 class="hud-panel-title" id="bldH">Run a simulation</h2><span class="hud-panel-meta">No simulation saved yet</span></header>' +
                '<div class="hud-panel-body">' + builderBody("Run simulation", mode, pool, picks, games, notes) + "</div>" +
                '<footer class="hud-panel-foot">You get: <b>01</b> Oracle review of the games · <b>02</b> curve and tempo vs the math · ' +
                "<b>03</b> which cards earned their slot · <b>04</b> every game, opponents and result.</footer></section>";
        }
        var report = d.sim.report, params = report.params || {};
        var last = (params.opponent_mode === "random" ? "Random each game" : params.opponent_mode === "specific" ? "Specific" : params.opponent_mode === "rapid1v1" ? "Rapid 1v1" : "Standard pod") +
            " · " + (params.games || "?") + " games";
        return '<details class="hud-tray sm-tray" id="tray"><summary><span>Run new simulation</span><span class="sm-tray-last">Last run: ' + esc(last) + "</span></summary>" +
            '<div class="sm-tray-body">' + builderBody("Run new simulation", mode, pool, picks, games, notes) + "</div></details>";
    }

    // ---- Offline banner ----------------------------------------------------
    function offlineBanner(d) {
        if (!d.pool || !d.pool.daemon_error) return "";
        return '<div class="hud-panel hud-panel--signal sm-banner" role="status"><div class="hud-panel-body">' +
            "<b>The Forge simulator isn't reachable.</b> New runs are paused. The saved report below is unaffected. " +
            '<button class="hud-btn hud-btn--sm" type="button" id="checkAgainBtn">Check again</button></div></div>';
    }

    // ---- Job ---------------------------------------------------------------
    function jobLabel(status) { return { queued: "Queued", running: "Simulating", writing_review: "Writing Oracle review", done: "Done", failed: "Failed" }[status] || status; }
    function jobTitle(job) {
        if (job.status === "queued") return "Waiting for the simulator…";
        if (job.status === "writing_review") return "All " + (job.games_total || "N") + " games done. Oracle is reading the log.";
        if (job.status === "running") {
            var x = Math.min((job.games_completed || 0) + 1, job.games_total || 1);
            return "Simulating game " + x + " of " + (job.games_total || "?");
        }
        if (job.status === "done") return "Done. Loading the report…";
        if (job.status === "failed") return "Couldn't finish the simulation: " + String(job.error || "unknown error").slice(0, 300);
        return "";
    }
    function jobCount(job) {
        if (job.status !== "running") return job.status === "writing_review" ? "<b>" + (job.games_total || 0) + " / " + (job.games_total || 0) + "</b> games" : "";
        var left = Math.max(0, (job.games_total || 0) - (job.games_completed || 0));
        return "<b>" + (job.games_completed || 0) + " / " + (job.games_total || 0) + "</b> games" + (left ? " · about " + Math.ceil(left * 0.5) + " min left" : "");
    }
    function jobHtml(job, hasReport) {
        if (!job || job.status === "done") return "";
        var phase = job.status === "writing_review" ? "oracle" : "sim";
        var p = job.games_total ? (job.games_completed || 0) / job.games_total : 0;
        return '<section class="hud-job sm-job" id="job" aria-live="polite" tabindex="-1" data-phase="' + phase + '" style="--p:' + p + '">' +
            '<div class="hud-job-head"><span class="hud-label" id="jobLabel">' + jobLabel(job.status) + '</span><span class="hud-job-clock" id="jobClock">0:00</span></div>' +
            '<p class="hud-job-title" id="jobTitle">' + esc(jobTitle(job)) + "</p>" +
            '<div class="hud-job-track" aria-hidden="true"><i></i></div>' +
            '<p class="sm-job-count" id="jobCount">' + jobCount(job) + "</p>" +
            '<p class="hud-job-msg">' + (hasReport ? "Your saved report stays exactly as it is until this run finishes. You can leave this page." : "The report appears here when it finishes.") + "</p>" +
            (job.status === "failed" ? '<button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" id="jobDismiss">Dismiss</button>' : "") + "</section>";
    }

    // ---- Report: head + stat strip -----------------------------------------
    function reportHead(report, meta) {
        var params = report.params || {}, pod = (report.comparison && report.comparison.pod_breakdown) || {};
        var bits = '<time class="hud-freshness" id="createdAt" datetime="' + esc(meta.created_at || "") + '" title="">' + esc(relTime(meta.created_at)) + "</time>" +
            (meta.stale ? chip("Stale", "signal", true, meta.stale_reason) : chip("Up to date", "ok", true)) +
            chip(params.opponent_mode === "random" ? "Random each game" : params.opponent_mode === "specific" ? "Specific" : params.opponent_mode === "rapid1v1" ? "Rapid 1v1" : "Standard pod", "data") +
            chip((params.games || pod.games_played || "?") + " games", "neutral") +
            chip("Won " + (pod.user_wins != null ? pod.user_wins : "?") + " of " + (pod.games_played != null ? pod.games_played : (params.games || "?")), (pod.user_wins || 0) * 4 >= (pod.games_played || 1) ? "ok" : "neutral");
        var notes = params.notes ? '<div class="hud-callout sm-notes" style="grid-template-columns:minmax(0,1fr)"><div><span class="hud-label">Your test notes</span><q>' +
            esc(params.notes) + "</q></div></div>" : "";
        var stale = meta.stale ? '<div class="hud-panel hud-panel--signal" id="staleNote" role="status" style="margin-top:var(--sp-4)"><div class="hud-panel-body">' +
            "<b>This report is out of date.</b> " + esc(meta.stale_reason || "The decklist changed since this simulation was run.") +
            " The numbers describe the old list. Open <i>Run new simulation</i> above to refresh.</div></div>" : "";
        return '<header class="hud-doc-head"><div><span class="hud-label">Simulation · saved</span><h2>' + esc(meta.deck_name || "") +
            '</h2><div class="hud-doc-facts">' + bits + "</div></div></header>" + notes + stale;
    }
    function metricsAvailable(report) {
        var tc = (report.comparison && report.comparison.tempo_comparison) || {};
        var sim = tc.sim || {};
        return sim.t5_mana_starvation_rate != null || sim.avg_first_commander_turn != null || sim.avg_mana_utilization_t2_t6 != null;
    }
    function statStrip(report) {
        var pod = (report.comparison && report.comparison.pod_breakdown) || {};
        var tc = (report.comparison && report.comparison.tempo_comparison) || {};
        var avail = metricsAvailable(report);
        var wr = pod.win_rate;
        var s = H().stat("Win rate", wr == null ? "n/a" : pct(wr) + "%", { foot: "even odds 25%", mod: "signal" }) +
            H().stat("Avg game", pod.avg_total_turns == null ? "n/a" : pod.avg_total_turns, { unit: "turns", foot: "table turns" });
        if (!avail) {
            s += H().stat("Commander cast", "n/a", { foot: "not recorded in random mode" }) + H().stat("Mana used T2–T6", "n/a", { foot: "not recorded in random mode" });
        } else {
            var cmdrT = tc.sim && tc.sim.avg_first_commander_turn;
            var mana = tc.sim && tc.sim.avg_mana_utilization_t2_t6;
            var staticT = tc.static && tc.static.commander_cmc;
            var staticMana = tc.static && tc.static.mana_utilization_t2_t6;
            s += H().stat("Commander cast", cmdrT == null ? "n/a" : "T" + cmdrT, { unit: "avg", foot: "static: T" + (staticT == null ? "?" : staticT) }) +
                H().stat("Mana used T2–T6", mana == null ? "n/a" : pct(mana) + "%", { foot: "static math: " + (staticMana == null ? "n/a" : pct(staticMana) + "%") });
        }
        return '<div class="hud-stat-strip" style="margin-top:var(--sp-6)">' + s + "</div>";
    }

    // ---- Section 01: Oracle review -----------------------------------------
    function s1(report) {
        var oracle = report.oracle_review || {};
        if (!oracle || (!oracle.headline && oracle.source !== "oracle" && oracle.source !== "fallback")) {
            return sec("01", "One-off Oracle simulation review",
                '<div class="hud-empty">Oracle couldn\'t write the review for this run. The numbers below are saved.</div>');
        }
        var prose = '<p class="sm-headline">' + esc(oracle.headline || "") + "</p><div class=\"hud-rv-prose\">";
        if (oracle.notes_response) prose += '<h3 class="hud-rv-h">On your notes</h3><p class="hud-doc-prose">' + esc(oracle.notes_response) + "</p>";
        if (oracle.tempo_and_mana_read) prose += '<h3 class="hud-rv-h">Against the static review</h3><p class="hud-doc-prose">' + esc(oracle.tempo_and_mana_read) + "</p>";
        if (oracle.card_performance_read) prose += '<p class="hud-doc-prose">' + esc(oracle.card_performance_read) + "</p>";
        if (oracle.actionable_tweaks && oracle.actionable_tweaks.length)
            prose += '<h3 class="hud-rv-h">Worth trying</h3><ul class="hud-doc-list">' + oracle.actionable_tweaks.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
        prose += "</div>";
        return sec("01", "One-off Oracle simulation review", prose);
    }

    // ---- Section 02: curve & tempo ------------------------------------------
    function cmpRow(name, simV, statV, sim01, stat01, read, i) {
        var chipHtml = read === "worse" ? chip("▼ Below math", "signal") : read === "better" ? chip("▲ Above math", "ok") : chip("In line", "neutral");
        return '<li class="hud-comp-row' + (read === "worse" ? " is-under" : "") + '" style="--i:' + i + '"><span class="hud-comp-name">' + esc(name) +
            '</span><span class="hud-comp-track" role="img" aria-label="' + esc(name) + ": simulated " + simV + ", math " + statV + '">' +
            '<span class="hud-comp-fill" style="width:' + Math.max(0, Math.min(100, (sim01 || 0) * 100)) + '%"></span>' +
            '<span class="hud-comp-target" style="left:' + Math.max(0, Math.min(100, (stat01 || 0) * 100)) + '%"></span></span>' +
            '<span class="hud-comp-val">' + simV + " <small>vs " + statV + "</small></span>" + chipHtml + "</li>";
    }
    function s2(report) {
        var tc = (report.comparison && report.comparison.tempo_comparison) || {};
        if (!metricsAvailable(report)) {
            return sec("02", "Curve &amp; tempo: simulation vs static math",
                '<p class="hud-doc-prose">Curve metrics are missing from this legacy run. Run a new simulation to see them.</p>');
        }
        var drops = tc.land_drops || [];
        var rows = drops.map(function (p, k) {
            var d = p.delta;
            var read = d == null ? "in" : d <= -0.08 ? "worse" : d >= 0.08 ? "better" : "in";
            var simV = p.sim_land_drop_rate == null ? "n/a" : pct(p.sim_land_drop_rate) + "%";
            var statV = p.static_land_drop_probability == null ? "n/a" : pct(p.static_land_drop_probability) + "%";
            return cmpRow("T" + p.turn + " land drop", simV, statV, p.sim_land_drop_rate, p.static_land_drop_probability, read, k);
        }).join("");
        var st = tc.static || {}, sim = tc.sim || {};
        var tbl = [
            ["T5 mana starvation (lower is better)", sim.t5_mana_starvation_rate, st.t5_starvation_probability, "pct", "signal", "worse", true],
            ["Commander first cast (mean turn, when cast)", sim.avg_first_commander_turn, st.commander_cmc, "turn", "signal", "worse"],
            ["Commander on curve (cast on turn cmc)", null, st.p_commander_on_curve, "pct", "signal", "worse"],
            ["Commander casts / game", null, null, "n", "", ""],
            ["Commander never cast (n of N games)", null, null, "n", "", ""],
            ["Mana used, T2–T6 (spent ÷ available)", sim.avg_mana_utilization_t2_t6, st.mana_utilization_t2_t6, "pct", "signal", "worse"],
            ["First spell cast (mean turn)", null, null, "turn", "", ""]
        ].map(function (r) {
            var simTxt = r[3] === "pct" ? fmtPct(r[1]) : r[3] === "turn" ? (r[1] == null ? '<span class="sm-na">n/a</span>' : "T" + r[1]) : fmtNum(r[1]);
            var mathTxt = r[3] === "pct" ? fmtPct(r[2]) : r[3] === "turn" ? (r[2] == null ? '<span class="sm-na">n/a</span>' : "T" + r[2]) : fmtNum(r[2]);
            var showChip = r[1] != null && r[2] != null && r[4];
            return "<tr><td class=\"is-primary c-m\">" + esc(r[0].split(" (")[0]) + '<span class="hud-field-hint" style="display:block">' +
                esc((r[0].match(/\(([^)]+)\)/) || [, ""])[1]) + "</span></td><td class=\"is-num c-s\" data-l=\"Sim\">" + simTxt +
                "</td><td class=\"is-num c-x\" data-l=\"Math\">" + mathTxt + "</td><td>" + (showChip ? chip(r[5] === "worse" ? "▼ Below math" : "In line", r[4]) : "") + "</td></tr>";
        }).join("");
        return sec("02", "Curve &amp; tempo: simulation vs static math",
            '<div class="hud-panel hud-panel--flush"><header class="hud-panel-head"><h3 class="hud-panel-title">Land drop made on turn</h3>' +
            '<span class="hud-panel-meta">math: ' + (st.lands != null ? st.lands + " lands in " + (st.library_size || "?") : "n/a") + '</span></header>' +
            '<div class="hud-panel-body sm-cmp"><p class="sm-legend"><span><i class="k-sim"></i>Simulated</span><span><i class="k-static"></i>Static math</span></p>' +
            '<ul class="hud-comp" style="list-style:none;margin:0;padding:0">' + (rows || '<li class="hud-empty">No land-drop data in this run.</li>') + "</ul></div></div>" +
            '<div class="hud-panel hud-panel--flush sm-block"><header class="hud-panel-head"><h3 class="hud-panel-title">Tempo &amp; commander</h3>' +
            '<span class="hud-panel-meta">simulated vs the Review\'s numbers</span></header>' +
            '<div class="hud-table-wrap"><table class="hud-table sm-tempo"><thead><tr><th scope="col">Metric</th><th scope="col" class="is-num">Sim</th>' +
            '<th scope="col" class="is-num">Math</th><th scope="col">Read</th></tr></thead><tbody>' + tbl + "</tbody></table></div></div>" +
            '<p class="sm-foot">A chip appears only when sim and math differ by more than this many games can explain. Smaller gaps read as In line.</p>');
    }

    // ---- Section 03: card performance ---------------------------------------
    function cardGroup(title, meta, rows, emptyTxt, signal) {
        var li = rows.map(function (r, k) {
            var cut = r.cut_since ? " " + chip("cut since", "neutral") : "";
            var chipTxt = r.static_strong || r.static_straggler ? "Both agree" : "Sim only";
            var chipTone = r.static_strong || r.static_straggler ? "ok" : "data";
            if (title.indexOf("dead draws") > -1) { chipTxt = r.static_straggler ? "Both agree" : "Not in static"; chipTone = r.static_straggler ? "ok" : "signal"; }
            return '<li class="sm-card' + (k > 2 ? " is-hidden" : "") + '"><div class="hud-comp-row" style="--i:' + k + '"><span class="hud-comp-name">' + card(r.name) +
                '</span><span class="hud-comp-track" role="img" aria-label="U ' + (r.utilization_rate == null ? "n/a" : r.utilization_rate) + ', 1.0 is expected">' +
                '<span class="hud-comp-fill" style="width:' + Math.min(100, (r.utilization_rate || 0) / 2 * 100) + '%"></span><span class="hud-comp-target" style="left:50%"></span></span>' +
                '<span class="hud-comp-val"><small>U</small> ' + (r.utilization_rate == null ? "n/a" : r.utilization_rate.toFixed(2)) + "</span>" + chip(chipTxt, chipTone) +
                '</div><span class="hud-tcard-note">Drawn in ' + (r.games_drawn == null ? "n/a" : r.games_drawn) + " games, cast in " + (r.games_cast == null ? "n/a" : r.games_cast) +
                "." + cut + "</span></li>";
        }).join("");
        return '<div class="hud-panel hud-panel--flush sm-block' + (signal ? " sm-group--signal" : "") + '"><header class="hud-panel-head">' +
            '<h3 class="hud-panel-title">' + title + '</h3><span class="hud-panel-meta">' + meta + "</span></header>" +
            (rows.length ? '<ul class="sm-cards">' + li + "</ul>" + (rows.length > 3 ? '<p class="sm-more"><button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" aria-expanded="false">Show all ' + rows.length + "</button></p>" : "") :
                '<div class="hud-panel-body"><div class="hud-empty">' + emptyTxt + "</div></div>") + "</div>";
    }
    function s3(report) {
        if (!metricsAvailable(report)) {
            return sec("03", "Card performance vs static review",
                '<p class="hud-doc-prose">Card metrics are missing from this legacy run. Run a new simulation to see them.</p>');
        }
        var cc = (report.comparison && report.comparison.card_comparison) || {};
        var dd = cc.surprise_dead_draws || [], sg = cc.confirmed_stragglers || [], wh = cc.confirmed_workhorses || [];
        return sec("03", "Card performance vs static review",
            '<p class="hud-doc-prose">U is how much a card did against how often it should have been drawn. <b>1.0</b> is as expected.</p>' +
            cardGroup("Surprise dead draws", dd.length + " cards · static review missed these", dd, "No surprise dead draws.", true) +
            cardGroup("Confirmed stragglers", sg.length + " cards · sim and static agree", sg, "No confirmed stragglers. Every card the Review flagged did its job here.", false) +
            cardGroup("Confirmed workhorses", wh.length + " cards", wh, "No workhorses stood out.", false) +
            '<p class="sm-foot">' + (cc.unmatched_cards || []).length + ' cards were drawn too rarely to judge and are left out. More games judges more of them.</p>');
    }

    // ---- Section 04: game log -----------------------------------------------
    function s4(report) {
        var pod = (report.comparison && report.comparison.pod_breakdown) || {};
        var games = pod.games || [];
        var wins = pod.user_wins || 0, n = games.length;
        var losses = games.filter(function (g) { return !g.user_won && g.winner; }).length;
        var draws = n - wins - losses;
        var randomMode = pod.opponent_mode === "random";
        var rows = games.map(function (g) {
            var res, lab;
            if (!g.winner && !g.user_won) { res = chip("No winner", "signal"); }
            else if (g.user_won) { res = chip("You won", "ok", true); }
            else { res = '<span class="sm-opp-name">Lost to ' + esc(g.winner || "?") + "</span>"; }
            var seats = (g.opponents || []).map(function (o, k) {
                return '<td class="c-seat"><span class="sm-lab">Seat ' + (k + 2) + '</span><span><span class="sm-opp-name">' + esc(o.deck || "?") +
                    '</span><span class="sm-opp-set">' + esc(o.ai || "") + "</span></span></td>";
            }).join("");
            return '<tr data-res="' + (!g.winner && !g.user_won ? "draw" : g.user_won ? "win" : "loss") + '"><td class="is-num c-n is-primary" data-value="' + g.game + '">#' +
                g.game + "</td>" + seats + '<td class="c-res" data-value="' + esc(g.user_won ? "you" : (g.winner || "~ no winner")) + '">' + res + '</td>' +
                '<td class="is-num c-turns" data-value="' + (g.total_turns || 0) + '">' + (g.total_turns || "?") + '<span class="sm-lab"> turns</span></td>' +
                '<td class="c-why"><span class="sm-reason">' + esc(g.win_reason || "") + "</span></td></tr>";
        }).join("");
        var seatAi = ["Reckless", "Default", "Cautious"];
        var th = [["#", "num"], ["Seat 2", "text", seatAi[0]], ["Seat 3", "text", seatAi[1]], ["Seat 4", "text", seatAi[2]], ["Winner", "text"], ["Turns", "num"], ["How it ended", "text"]]
            .map(function (c) {
                var right = c[1] === "num";
                return '<th scope="col" data-sort="' + c[1] + '" aria-sort="none"' + (right ? ' class="is-num"' : "") + '><button class="hud-sort" type="button">' + c[0] +
                    "</button>" + (c[2] ? '<span class="sm-seat-ai">' + c[2] + "</span>" : "") + "</th>";
            }).join("");
        var distinct = randomMode ? n + " different pods" : "same pod all " + n + " games";
        return sec("04", "Per-game opponent &amp; outcome log",
            '<div class="hud-panel hud-panel--flush"><div class="sm-logbar"><span class="hud-panel-meta" style="margin:0">' + wins + " W · " + losses + " L" +
            (draws ? " · " + draws + " timeout/draw" : "") + " · " + distinct + '</span>' +
            '<div class="hud-seg" role="radiogroup" aria-label="Filter games"><input type="radio" name="flt" id="f-all" checked><label for="f-all">All ' + n + '</label>' +
            '<input type="radio" name="flt" id="f-w"><label for="f-w">Wins</label><input type="radio" name="flt" id="f-l"><label for="f-l">Losses</label></div>' +
            '<label class="sm-sortsel"><span class="hud-label">Sort</span><select aria-label="Sort games"><option>Game #</option><option>Turns, longest first</option><option>Result</option></select></label></div>' +
            '<div class="hud-table-wrap"><table class="hud-table sm-log" data-hud-sortable aria-label="Games"><thead><tr>' + th + "</tr></thead><tbody>" +
            (rows || '<tr><td colspan="7"><div class="hud-empty">No games in this run.</div></td></tr>') + "</tbody></table></div></div>");
    }

    function reportHtml(d) {
        var sim = d.sim;
        if (!sim || !sim.report) return "";
        var report = sim.report, meta = sim.meta || {};
        meta.deck_name = meta.deck_name || d.deck_name;
        return '<article class="hud-doc" id="doc" data-state="' + (meta.stale ? "stale" : "fresh") + '">' + reportHead(report, meta) + statStrip(report) +
            '<nav class="hud-doc-index" aria-label="Sections"><a href="#sm01">01 Oracle review</a><a href="#sm02">02 Curve &amp; tempo</a>' +
            '<a href="#sm03">03 Cards</a><a href="#sm04">04 Game log</a></nav>' + s1(report) + s2(report) + s3(report) + s4(report) + "</article>";
    }

    // ---- Full page render ---------------------------------------------------
    function render(d) {
        var job = d.job;
        var html = '<div class="hud-doc" style="max-width:60rem" data-deck-id="' + esc(DECK_ID) + '">' +
            offlineBanner(d) + builder(d) + jobHtml(job, !!(d.sim && d.sim.report)) + "</div>" + reportHtml(d);
        var r = root();
        r.innerHTML = html;
        wireBuilder(d);
        var jobActive = job && job.status !== "done" && job.status !== "failed";
        if (jobActive) {
            var form = $("simForm");
            if (form) form.querySelectorAll("fieldset").forEach(function (f) { f.disabled = true; });
        }
        if (job && job.status !== "done") wireJob(job, !!(d.sim && d.sim.report));
        H().initSortable(r);
        wireLog();
        wireCardGroups();
    }

    function wireLog() {
        ["f-all", "f-w", "f-l"].forEach(function (id) {
            var el = $(id);
            if (el) el.addEventListener("change", function () {
                document.querySelectorAll(".sm-log tbody tr").forEach(function (tr) {
                    tr.classList.toggle("is-hidden", (id === "f-w" && tr.dataset.res !== "win") || (id === "f-l" && tr.dataset.res !== "loss"));
                });
                var meta = document.querySelector(".sm-logbar .hud-panel-meta");
            });
        });
    }
    function wireCardGroups() {
        document.querySelectorAll(".sm-more button").forEach(function (b) {
            b.addEventListener("click", function () {
                var ul = b.closest(".hud-panel").querySelector(".sm-cards");
                ul.querySelectorAll(".is-hidden").forEach(function (li) { li.classList.remove("is-hidden"); });
                b.setAttribute("aria-expanded", "true");
                b.parentNode.remove();
            });
        });
    }

    // ---- Builder wiring: mode switch, comboboxes, validation, submit --------
    function wireBuilder(d) {
        function updateTimes() {
            var opCount = 3;
            if (STATE.mode === "rapid1v1") opCount = 1;
            else if (STATE.mode === "specific") opCount = (STATE.picks || []).filter(function(p) { return p; }).length || 3;
            var mult = (opCount + 1) / 4;
            var g5 = $("g5"), g20 = $("g20"), g60 = $("g60");
            if (g5 && g5.nextElementSibling) g5.nextElementSibling.textContent = "5 games · ~" + Math.ceil(2 * mult) + " min";
            if (g20 && g20.nextElementSibling) g20.nextElementSibling.textContent = "20 games · ~" + Math.ceil(10 * mult) + " min";
            if (g60 && g60.nextElementSibling) g60.nextElementSibling.textContent = "60 games · ~" + Math.ceil(30 * mult) + " min";
        }
        document.querySelectorAll('input[name="mode"]').forEach(function (rb) {
            rb.addEventListener("change", function () {
                STATE.mode = rb.value;
                if (STATE.mode === "rapid1v1") {
                    STATE.games = 5;
                    var g5 = $("g5");
                    if (g5) g5.checked = true;
                } else if (STATE.games === 5) {
                    STATE.games = 20;
                    var g20 = $("g20");
                    if (g20) g20.checked = true;
                }
                updateTimes();
                ["Standard", "Random", "Specific", "Rapid1v1"].forEach(function (n) {
                    var el = $("opp" + n);
                    if (el) el.hidden = n.toLowerCase() !== rb.value.toLowerCase();
                });
            });
        });
        var ta = $("notes");
        if (ta) ta.addEventListener("input", function () {
            STATE.notes = ta.value;
            var counter = ta.parentNode.querySelector(".sm-counter");
            counter.textContent = ta.value.length + " / 500";
            counter.classList.toggle("is-near", ta.value.length >= 450);
        });
        document.querySelectorAll('input[name="games"]').forEach(function (g) {
            g.addEventListener("change", function () { STATE.games = parseInt(g.value, 10); });
        });

        // Comboboxes (ARIA 1.2 pattern).
        document.querySelectorAll(".sm-combo").forEach(function (combo) {
            var input = combo.querySelector("input"), list = combo.querySelector(".sm-combo-list"), seat = parseInt(combo.dataset.seat, 10);
            var pool = d.pool || { opponents: [] };
            function picksArr() { return STATE.picks || (STATE.picks = ["", "", "", "", ""]); }
            function refresh(q) {
                var taken = {}; picksArr().forEach(function (id, k) { if (id && k !== seat) taken[id] = k + 2; });
                list.innerHTML = comboRows(pool.opponents.map(function (o) { return Object.assign({ _seatKey: seat + 2 }, o); }), taken, q);
            }
            function open() { list.hidden = false; input.setAttribute("aria-expanded", "true"); refresh(input.value); }
            function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
            input.addEventListener("focus", open);
            input.addEventListener("input", function () { refresh(input.value); open(); });
            input.addEventListener("keydown", function (e) {
                if (e.key === "Escape") { close(); input.value = input.dataset.valueName || ""; }
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    var opts = Array.prototype.slice.call(list.querySelectorAll('.sm-combo-opt:not([aria-disabled="true"])'));
                    if (!opts.length) return;
                    var idx = opts.findIndex(function (o) { return o.classList.contains("is-active"); });
                    idx = e.key === "ArrowDown" ? (idx + 1) % opts.length : (idx - 1 + opts.length) % opts.length;
                    opts.forEach(function (o) { o.classList.remove("is-active"); o.removeAttribute("aria-selected"); });
                    opts[idx].classList.add("is-active"); opts[idx].setAttribute("aria-selected", "true");
                    opts[idx].scrollIntoView({ block: "nearest" });
                }
                if (e.key === "Enter") {
                    e.preventDefault();
                    var active = list.querySelector(".is-active") || list.querySelector('.sm-combo-opt[aria-selected="true"]');
                    if (active) pick(active);
                }
            });
            list.addEventListener("click", function (e) {
                var opt = e.target.closest(".sm-combo-opt");
                if (opt && opt.getAttribute("aria-disabled") !== "true") pick(opt);
            });
            function pick(opt) {
                var id = opt.dataset.id, name = opt.querySelector("span").textContent;
                picksArr()[seat] = id;
                input.value = name; input.dataset.valueName = name; input.dataset.value = id;
                combo.classList.remove("is-invalid");
                var err = combo.querySelector(".sm-combo-err"); if (err) err.hidden = true;
                close();
                updateTimes();
            }
            document.addEventListener("click", function (e) { if (!combo.contains(e.target)) close(); });
        });

        var form = $("simForm");
        if (form) form.addEventListener("submit", function (e) { e.preventDefault(); submit(d); });
        var dismiss = $("jobDismiss");
        if (dismiss) dismiss.addEventListener("click", function () { $("job").hidden = true; });
        var again = $("checkAgainBtn");
        if (again) again.addEventListener("click", function () { load(); });
    }

    function validate() {
        var picks = STATE.picks || ["", "", "", "", ""];
        if (STATE.mode === "rapid1v1") {
            if (!picks[0]) {
                var combo = document.querySelector('.sm-combo[data-seat="0"][data-mode="rapid"]');
                if (combo) {
                    combo.classList.add("is-invalid");
                    combo.querySelector(".sm-combo-err").hidden = false;
                    combo.querySelector(".sm-combo-err").textContent = "Pick an opponent.";
                    combo.querySelector("input").focus();
                }
                return false;
            }
        } else if (STATE.mode === "specific") {
            if (!picks.some(function(p) { return p; })) {
                var combo = document.querySelector('.sm-combo[data-seat="0"][data-mode="specific"]');
                if (combo) {
                    combo.classList.add("is-invalid");
                    combo.querySelector(".sm-combo-err").hidden = false;
                    combo.querySelector(".sm-combo-err").textContent = "Pick at least 1 opponent.";
                    combo.querySelector("input").focus();
                }
                return false;
            }
        }
        return true;
    }
    function submit() {
        $("simError").textContent = "";
        if (!validate()) return;
        var btn = $("runBtn");
        btn.disabled = true;
        var body = { opponent_mode: STATE.mode, games: STATE.games || 20, notes: STATE.notes || "" };
        if (STATE.mode === "specific" || STATE.mode === "standard" || STATE.mode === "rapid1v1") {
             var picks = STATE.picks || ["", "", "", "", ""];
             if (STATE.mode === "rapid1v1") body.opponents = [picks[0]];
             else if (STATE.mode === "standard") {
                 var filled = picks.slice(0, 3).map(function(p, i) { return p || STANDARD_POD[i][0]; });
                 if (picks.slice(0, 3).some(function(x) { return !!x; })) body.opponents = filled;
             }
             else if (STATE.mode === "specific") body.opponents = picks.filter(function(x) { return !!x; });
        }
        call("POST", API + "/run", body).then(function (res) {
            var j = { job_id: res.job_id, started: new Date().toISOString() };
            remember(j);
            load();
        }).catch(function (err) {
            btn.disabled = false;
            if (err.status === 409 && err.body && err.body.job_id) {
                var j = { job_id: err.body.job_id };
                remember(j);
                load();
                return;
            }
            $("simError").textContent = err.message;
        });
    }

    // ---- Job polling ---------------------------------------------------------
    function wireJob(job, hasReport) {
        clearInterval(clockTimer);
        var t0 = Date.now();
        clockTimer = setInterval(function () { var c = $("jobClock"); if (c) c.textContent = mmss(Date.now() - t0); }, 1000);
    }
    function poll(j) {
        clearTimeout(timer);
        call("GET", API + "/jobs/" + encodeURIComponent(j.job_id)).then(function (res) {
            if (res.status === "done") { forget(); load(); return; }
            if (res.status === "failed") { forget(); load(); return; }
            var seen = Date.parse(res.updated_at || res.created_at || j.started);
            if (isFinite(seen) && Date.now() - seen > STALE_MS) { forget(); load(); return; }
            STATE.lastJob = res;
            var card = $("job");
            if (card) {
                $("jobLabel").textContent = jobLabel(res.status);
                $("jobTitle").textContent = jobTitle(res);
                $("jobCount").innerHTML = jobCount(res);
                card.dataset.phase = res.status === "writing_review" ? "oracle" : "sim";
                card.style.setProperty("--p", res.games_total ? (res.games_completed || 0) / res.games_total : 0);
            }
            timer = setTimeout(function () { poll(j); }, POLL_MS);
        }).catch(function () {
            timer = setTimeout(function () { poll(j); }, POLL_MS * 3);
        });
    }

    // ---- Load ----------------------------------------------------------------
    function load() {
        call("GET", API).then(function (res) {
            var d = { pool: { opponents: res.opponent_pool || [], daemon_error: res.opponent_pool_error || null },
                      sim: res.sim ? { meta: res.sim.meta, report: res.sim.report } : null,
                      job: res.running_job, deck_name: res.sim && res.sim.meta && res.sim.meta.deck_name };
            var pending = saved();
            if (!d.job && pending && pending.job_id) {
                d.job = { job_id: pending.job_id, status: "queued", games_completed: 0,
                          games_total: (d.sim && d.sim.report && d.sim.report.params && d.sim.report.params.games) || 20 };
            }
            if (!STATE.data) {
                var p = d.sim && d.sim.report && d.sim.report.params;
                if (p) { STATE.mode = p.opponent_mode || "standard"; STATE.games = p.games || 20; }
            }
            STATE.data = d;
            render(d);
            var followId = (res.running_job && res.running_job.job_id) || (pending && pending.job_id);
            if (followId) poll({ job_id: followId });
        }).catch(function () {
            root().innerHTML = '<div class="hud-panel"><div class="hud-panel-body"><div class="hud-empty">Couldn\'t load the simulation. Try again later.</div></div></div>';
        });
    }

    function init(deckId) {
        DECK_ID = deckId;
        API = "/api/decks/" + encodeURIComponent(DECK_ID) + "/sim";
        STORE = "simJob:" + DECK_ID;
        load();
    }

    window.DeckSim = { init: init };
})();
