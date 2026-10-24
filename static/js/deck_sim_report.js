/* Simulate tab: WoWLogs-style mechanical diagnostics, built ONLY from the persisted
   latest run (sim.report.telemetry + sim.report.games_log). Pure model in build();
   html in render(). Missing data is shown as "not emitted", never as zero. */
(function (root) {
    "use strict";
    var LOW = 20;

    function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
    function isNum(x) { return typeof x === "number" && isFinite(x); }
    function pct(a, n) { return n ? Math.round(100 * a / n) : null; }
    function median(v) { if (!v.length) return null; var s = v.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

    // ---- model ------------------------------------------------------------
    function build(report) {
        report = report || {};
        var t = report.telemetry || null;
        var games = (t && t.games) || [];
        var testedId = t && t.deck_id;
        var m = { hasTelemetry: !!t, n: games.length, testedId: testedId, mode: t && t.params && t.params.opponent_mode,
                  wins: games.filter(function (g) { return g.user_won; }).length };
        // curve execution for the tested deck: per turn, over games that have a curve
        var byTurn = {}, curveGames = 0, spellTurnsZero = 0;
        games.forEach(function (g) {
            var c = g.curves && g.curves[testedId];
            if (!Array.isArray(c) || !c.length) return;
            curveGames++;
            var lands = 0;
            c.forEach(function (row) {
                if (!isNum(row.turn)) return;
                var b = byTurn[row.turn] || (byTurn[row.turn] = { turn: row.turn, n: 0, land: 0, lands: [], spent: [] });
                b.n++;
                if (row.land_played) { b.land++; lands++; }
                b.lands.push(lands);
                if (isNum(row.mana_spent)) b.spent.push(row.mana_spent);
            });
        });
        m.curve = Object.keys(byTurn).map(Number).sort(function (a, b) { return a - b; }).map(function (k) {
            var b = byTurn[k];
            return { turn: k, n: b.n, landHit: pct(b.land, b.n),
                     available: b.lands.length ? b.lands.reduce(function (a, c) { return a + c; }, 0) / b.lands.length : null,
                     spent: b.spent.length ? b.spent.reduce(function (a, c) { return a + c; }, 0) / b.spent.length : null, spentN: b.spent.length };
        });
        m.curveGames = curveGames;
        // landdrop hit over turns 1-4
        var early = m.curve.filter(function (r) { return r.turn <= 4; });
        var en = early.reduce(function (a, r) { return a + r.n; }, 0), eh = early.reduce(function (a, r) { return a + Math.round(r.landHit * r.n / 100); }, 0);
        m.earlyLand = { n: en, pct: pct(eh, en) };
        // cards (owned decks only)
        var cards = (t && t.aggregate && t.aggregate.cards) || {};
        m.decks = Object.keys(cards).map(function (id) {
            var rows = Object.keys(cards[id]).map(function (name) {
                var c = cards[id][name], drawn = c.drawn || {}, cast = c.cast || {}, stuck = c.stuck_in_hand || {};
                var dead = (isNum(cast.n) && cast.n > 0 && isNum(drawn.count)) ? Math.max(0, (drawn.count || 0) - (cast.count || 0)) : null;
                return { name: name, games: c.games || 0, drawn: drawn.n ? drawn.count : null, drawnN: drawn.n || 0,
                         cast: cast.n ? cast.count : null, castN: cast.n || 0, stuck: stuck.n ? stuck.count : null, stuckN: stuck.n || 0,
                         dead: dead, firstCast: c.first_cast_turn && c.first_cast_turn.n ? c.first_cast_turn.median : null };
            });
            return { id: id, tested: id === testedId, rows: rows };
        });
        // commander cast turn: prefer the existing tempo comparison
        var tc = report.comparison && report.comparison.tempo_comparison;
        var cmdr = tc && tc.sim && tc.sim.avg_first_commander_turn;
        m.commander = { avg: isNum(cmdr) ? cmdr : null, n: m.n };
        // pods
        var pods = {};
        games.forEach(function (g) {
            var opp = (g.pod || []).filter(function (p) { return !p.tested; }).map(function (p) { return p.deck_id; }).sort();
            var key = opp.join(" · ") || "(no opponents recorded)";
            var b = pods[key] || (pods[key] = { pod: key, size: (g.pod || []).length, n: 0, w: 0, l: 0, none: 0, turns: [] });
            b.n++;
            if (g.user_won) b.w++; else if (g.winner) b.l++; else b.none++;
            if (isNum(g.total_turns) && g.total_turns > 0) b.turns.push(g.total_turns);
        });
        m.pods = Object.keys(pods).map(function (k) { var b = pods[k]; b.medTurns = median(b.turns); return b; });
        m.games = games;
        // scorecard: mechanical rules, only when their own n >= LOW
        var working = [], notWorking = [], unknown = [];
        function rule(label, n, val, goodIf, text) {
            if (val == null || n < LOW) { unknown.push(label + (n ? " (n=" + n + ", under " + LOW + ")" : " (not emitted yet)")); return; }
            (goodIf(val) ? working : notWorking).push(text(val) + " (n=" + n + ")");
        }
        rule("Early land drops", m.curveGames, m.earlyLand.pct, function (v) { return v >= 85; }, function (v) { return "Land drop made on " + v + "% of turns 1–4"; });
        rule("Commander cast turn", m.commander.avg != null ? m.n : 0, m.commander.avg, function (v) { return v <= 5; }, function (v) { return "Commander first cast on turn " + v + " on average"; });
        m.decks.filter(function (d) { return d.tested; }).forEach(function (d) {
            d.rows.forEach(function (r) {
                if (r.stuck != null && r.drawnN >= LOW && r.drawn) {
                    var p = pct(r.stuck, r.drawn);
                    if (p >= 40) notWorking.push(r.name + " stuck in hand in " + p + "% of draws (n=" + r.drawn + ")");
                }
            });
        });
        m.score = { working: working, notWorking: notWorking, unknown: unknown };
        return m;
    }

    // ---- html -------------------------------------------------------------
    function lowChip(n) { return n < LOW ? ' <span class="hud-chip hud-chip--signal sm-low" title="Under ' + LOW + ' samples: treat as anecdotal">low sample · n=' + n + "</span>" : ' <span class="sm-n">n=' + n + "</span>"; }
    function na(txt) { return '<span class="sm-na">' + esc(txt || "not emitted") + "</span>"; }
    function panel(title, n, body) {
        return '<div class="hud-panel" style="margin-bottom:var(--sp-4)"><div class="hud-panel-head"><span class="hud-label">' + esc(title) + "</span>" +
            (n == null ? "" : lowChip(n)) + '</div><div class="hud-panel-body">' + body + "</div></div>";
    }
    function empty(msg) { return '<div class="sm-unrec"><b>No data captured for this run</b><p>' + esc(msg) + "</p></div>"; }
    function list(items, none) { return items.length ? '<ul class="hud-doc-list">' + items.map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>" : '<p class="hud-muted">' + esc(none) + "</p>"; }

    function scorecard(m) {
        var s = m.score;
        return panel("Scorecard · mechanical diagnostics", m.n,
            '<div class="sm-score"><div><h3 class="hud-rv-h">Working</h3>' + list(s.working, "Nothing confirmed yet.") + "</div>" +
            '<div><h3 class="hud-rv-h">Not working</h3>' + list(s.notWorking, "Nothing flagged.") + "</div></div>" +
            (s.unknown.length ? '<p class="sm-foot">Not enough data to judge: ' + esc(s.unknown.join("; ")) + ".</p>" : ""));
    }
    function curve(m) {
        if (!m.curve.length) return panel("Curve execution", 0, empty("The simulator did not emit per-turn land and mana data for this run. Land-drop hit rate, mana available vs spent and colour screw fill in once it does."));
        var rows = m.curve.map(function (r) {
            return "<tr><td class=\"is-num\">T" + r.turn + "</td><td class=\"is-num\">" + (r.landHit == null ? "n/a" : r.landHit + "%") + "</td>" +
                "<td class=\"is-num\">" + (r.available == null ? "n/a" : r.available.toFixed(1)) + "</td><td class=\"is-num\">" +
                (r.spent == null ? "n/a" : r.spent.toFixed(1)) + "</td><td class=\"is-num\">" + r.n + (r.n < LOW ? ' <span class="sm-low">low</span>' : "") + "</td></tr>";
        }).join("");
        return panel("Curve execution", m.curveGames,
            '<div class="hud-table-wrap"><table class="hud-table sm-curve" aria-label="Curve execution by turn"><thead><tr><th>Turn</th><th class="is-num">Land drop hit</th><th class="is-num">Lands in play (mana available)</th><th class="is-num">Mana spent</th><th class="is-num">Games</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
            '<p class="sm-foot">Colour screw: ' + na("not emitted by the simulator yet") + ". Mana available is approximated by lands played.</p>");
    }
    function cmdr(m) {
        return panel("Commander cast turn", m.commander.avg == null ? 0 : m.n,
            m.commander.avg == null ? empty("No commander cast turn recorded for this run.") : '<p class="hud-doc-prose">First cast on turn <b>' + esc(m.commander.avg) + "</b> on average.</p>");
    }
    var COLS = [["name", "Card", "text"], ["drawn", "Drawn", "num"], ["cast", "Cast", "num"], ["stuck", "Stuck in hand", "num"], ["dead", "Dead (drawn, never cast)", "num"], ["firstCast", "Median cast turn", "num"]];
    function cardTable(m) {
        if (!m.decks.length) return panel("Cards · owned decks", 0, empty("No per-card data was emitted for this run. Card metrics cover only decks you own."));
        return m.decks.map(function (d, di) {
            var maxN = d.rows.reduce(function (a, r) { return Math.max(a, r.drawnN); }, 0);
            var head = COLS.map(function (c) { return '<th scope="col" aria-sort="none"' + (c[2] === "num" ? ' class="is-num"' : "") + '><button class="hud-sort sm-sortbtn" type="button" data-key="' + c[0] + '" data-type="' + c[2] + '">' + esc(c[1]) + "</button></th>"; }).join("");
            var body = d.rows.map(function (r) {
                function cell(v, n) { return '<td class="is-num" data-value="' + (v == null ? "" : v) + '">' + (v == null ? na("n/a") : v + (n < LOW ? '<sup class="sm-low" title="n=' + n + '">*</sup>' : "")) + "</td>"; }
                return '<tr><td data-value="' + esc(r.name.toLowerCase()) + '">' + esc(r.name) + "</td>" + cell(r.drawn, r.drawnN) + cell(r.cast, r.castN) + cell(r.stuck, r.stuckN) + cell(r.dead, r.castN) + cell(r.firstCast, r.castN) + "</tr>";
            }).join("");
            return panel("Cards · " + (d.tested ? "this deck" : d.id), maxN,
                '<div class="hud-table-wrap"><table class="hud-table sm-cardtbl" id="smCards' + di + '" aria-label="Per-card metrics"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
                '<p class="sm-foot">Counts are games. * = fewer than ' + LOW + " observations.</p>");
        }).join("");
    }
    function pods(m) {
        if (!m.pods.length) return panel("Results by pod", 0, empty("No games recorded."));
        var rows = m.pods.map(function (p) {
            return "<tr><td>" + esc(p.pod) + '</td><td class="is-num">' + p.n + "</td><td class=\"is-num\">" + p.w + "</td><td class=\"is-num\">" + p.l + "</td><td class=\"is-num\">" + p.none +
                '</td><td class="is-num">' + (p.medTurns == null ? "n/a" : p.medTurns) + "</td><td>" + (p.n < LOW ? '<span class="sm-low">low sample</span>' : "") + "</td></tr>";
        }).join("");
        return panel("Results by pod · counts, not a rating", m.n,
            '<div class="hud-table-wrap"><table class="hud-table" aria-label="Results by pod"><thead><tr><th>Opponents</th><th class="is-num">Games</th><th class="is-num">Won</th><th class="is-num">Lost</th><th class="is-num">No winner</th><th class="is-num">Median turns</th><th></th></tr></thead><tbody>' + rows + "</tbody></table></div>");
    }
    function gameLog(m, rawLog) {
        if (!m.games.length) return "";
        var rows = m.games.map(function (g, i) {
            var pod = (g.pod || []).filter(function (p) { return !p.tested; }).map(function (p) { return p.deck_id; }).join(", ");
            var raw = rawLog && rawLog[i] ? JSON.stringify(rawLog[i], null, 2) : null;
            return "<tr><td class=\"is-num\">#" + esc(g.game == null ? i + 1 : g.game) + "</td><td>" + esc(pod) + "</td><td>" + (g.user_won ? "Won" : g.winner ? "Lost to " + esc(g.winner) : "No winner") +
                '</td><td class="is-num">' + esc(g.total_turns == null ? "n/a" : g.total_turns) + "</td><td>" + esc(g.win_reason || "") + "</td><td>" +
                (raw ? '<details class="sm-raw"><summary>Raw</summary><pre class="sm-rawpre" data-raw-idx="' + i + '"></pre></details>' : na("not persisted")) + "</td></tr>";
        }).join("");
        return panel("Per-game log", m.n,
            '<div class="hud-table-wrap"><table class="hud-table" aria-label="Per-game log"><thead><tr><th class="is-num">#</th><th>Pod (opponents)</th><th>Result</th><th class="is-num">Turns</th><th>How it ended</th><th>Raw log</th></tr></thead><tbody>' + rows + "</tbody></table></div>");
    }
    function render(report) {
        var m = build(report), meta = '<span class="hud-label">Diagnostics</span>';
        var head = '<p class="sm-foot">Mechanical diagnostics from the saved run: ' + m.n + " games" + (m.mode ? ", " + esc(m.mode) + " mode" : "") + "." + lowChip(m.n) + "</p>";
        if (!m.hasTelemetry) return '<div class="hud-doc-section">' + meta + empty("This saved run predates diagnostics. Run a new simulation to get them.") + "</div>";
        return '<div class="hud-doc-section" id="smDiag">' + meta + head + scorecard(m) + curve(m) + cmdr(m) + cardTable(m) + pods(m) + gameLog(m, report.games_log) + "</div>";
    }
    function wire(rootEl, report) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        var log = (report && report.games_log) || [];
        rootEl.querySelectorAll("pre[data-raw-idx]").forEach(function (pre) {
            var det = pre.parentNode;
            det.addEventListener("toggle", function () { if (det.open && !pre.textContent) pre.textContent = JSON.stringify(log[+pre.getAttribute("data-raw-idx")], null, 2); });
        });
        rootEl.querySelectorAll(".sm-cardtbl").forEach(function (table) {
            table.addEventListener("click", function (ev) {
                var b = ev.target.closest && ev.target.closest(".sm-sortbtn");
                if (!b) return;
                var th = b.closest("th"), idx = Array.prototype.indexOf.call(th.parentNode.children, th);
                var dir = th.getAttribute("aria-sort") === "ascending" ? -1 : 1;
                table.querySelectorAll("th").forEach(function (h) { h.setAttribute("aria-sort", "none"); });
                th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
                var tb = table.tBodies[0], rows = Array.prototype.slice.call(tb.rows), num = b.getAttribute("data-type") === "num";
                rows.sort(function (a, c) { return sortCmp(a.cells[idx].getAttribute("data-value"), c.cells[idx].getAttribute("data-value"), num, dir) || sortCmp(a.cells[0].getAttribute("data-value"), c.cells[0].getAttribute("data-value"), false, 1); });
                rows.forEach(function (r) { tb.appendChild(r); });
            });
        });
    }
    function sortCmp(a, b, num, dir) {
        var ea = a === "" || a == null, eb = b === "" || b == null;
        if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1;   // empty values last in both directions
        if (num) return (parseFloat(a) - parseFloat(b)) * dir;
        return (a < b ? -1 : a > b ? 1 : 0) * dir;
    }

    root.DeckSimReport = { LOW_SAMPLE: LOW, build: build, render: render, wire: wire, sortCmp: sortCmp };
    if (typeof module !== "undefined") module.exports = root.DeckSimReport;
})(typeof window !== "undefined" ? window : globalThis);
