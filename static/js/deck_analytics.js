/* ==========================================================================
   Commander Lab: deck analytics view models + deck-tab hotkeys (AN-4).
   Loaded on every deck tab (partials/deck_head.html), exposes
   window.DeckAnalytics. Pure functions that turn the AN-1..AN-3 JSON
   payloads into what the Stats / Identity pages draw (UX_SPEC.md §3.1-3.2).
   No DOM access except the 1-5 tab hotkeys at the bottom.
   ========================================================================== */
(function () {
    "use strict";

    // Edges whose only reasons are these are hidden by default (UX_SPEC §3.1.1:
    // otherwise every signet connects to everything). ?mana=1 shows them.
    var MANA_REASONS = { mana: 1, lands: 1, ramp: 1 };
    // Template curve shape (UX_SPEC §3.1.8), normalised to the deck's non-land count.
    var CURVE_TEMPLATE = [0, 4, 18, 12, 10, 7, 4, 3];
    var MINUS = "\u2212";

    function isLand(card) { return !!(card && card.type && String(card.type).indexOf("Land") !== -1); }
    function shortName(name) { return String(name || "").split(",")[0]; }

    // AN-1 /interactions graph -> HUD.graph input.
    // degree = DISTINCT neighbours over the visible edges (what "N connections" means).
    function graphData(graph, opts) {
        opts = opts || {};
        var labels = graph.labels || {}, hide = opts.mana ? {} : MANA_REASONS;
        var edges = [];
        (graph.edges || []).forEach(function (e) {
            var keys = (e.reasons || []).filter(function (k) { return !hide[k]; });
            if (!keys.length) return;
            edges.push({ source: e.source, target: e.target, keys: keys,
                         reasons: keys.map(function (k) { return String(labels[k] || k).toLowerCase(); }) });
        });
        var nb = {};
        edges.forEach(function (e) {
            (nb[e.source] = nb[e.source] || {})[e.target] = 1;
            (nb[e.target] = nb[e.target] || {})[e.source] = 1;
        });
        var meta = {}, commanders = [];
        (graph.nodes || []).forEach(function (n) {
            meta[n.name] = n;
            if (n.is_commander) commanders.push(n.name);
        });
        var deg = function (id) { return nb[id] ? Object.keys(nb[id]).length : 0; };
        var ids = Object.keys(nb);
        commanders.forEach(function (c) { if (ids.indexOf(c) === -1) ids.push(c); });
        var nodes = ids.map(function (id) {
            return { id: id, degree: deg(id), is_commander: commanders.indexOf(id) !== -1 };
        });
        var isolated = (graph.nodes || []).filter(function (n) { return !n.is_commander && deg(n.name) <= 1; })
            .map(function (n) { return n.name; });
        return { nodes: nodes, edges: edges, commander: commanders[0] || null, commanders: commanders,
                 isolated: isolated, meta: meta, labels: labels, degree: deg };
    }

    // AN-2 /engines -> engine list rows. "Archetype: a → b" splits into name + flow.
    // pillar = the pillar most members fit (AN-3 per-card fit), if it covers
    // at least 40% of the members; otherwise null (UX: "candidate pillar?").
    function engineList(payload, identity) {
        var fitBy = {};
        ((identity && identity.fit && identity.fit.per_card) || []).forEach(function (c) {
            if (c.bucket === "pillar" && c.pillar) fitBy[c.name] = c.pillar;
        });
        return ((payload && payload.engines) || []).map(function (g, i) {
            var full = String(g.name || ""), cut = full.indexOf(": ");
            // The name carries the LLM/catalog label ("Aristocrat Burn: tokens → …");
            // archetype can be a generic bucket ("Loop"), so it is only a fallback.
            var name = cut > 0 ? full.slice(0, cut) : (full || g.archetype || "Engine");
            var flow = cut > 0 ? full.slice(cut + 2) : "";
            var votes = {}, best = null;
            (g.cards || []).forEach(function (c) { var p = fitBy[c]; if (p) votes[p] = (votes[p] || 0) + 1; });
            Object.keys(votes).forEach(function (p) { if (!best || votes[p] > votes[best]) best = p; });
            var cards = g.cards || [];
            return { id: g.signature || ("engine-" + i), name: name, flow: flow, kind: g.kind || "chain",
                     cards: cards, commanders: g.commanders || [], internal_edges: g.internal_edges || 0,
                     pillar: best && votes[best] >= Math.max(1, Math.ceil(cards.length * 0.4)) ? best : null };
        });
    }

    // Composition delta chip (UX_SPEC §3.1.7): ±1 ok, short ≥2 signal, over ≥2 neutral.
    function deltaChip(d) {
        if (Math.abs(d) <= 1) return [d === 0 ? "on target" : (d > 0 ? "+" + d : MINUS + Math.abs(d)) + " ok", "ok"];
        return d < 0 ? [MINUS + Math.abs(d) + " short", "signal"] : ["+" + d + " over", "neutral"];
    }
    function compRow(row) {
        var d = row.actual - row.target, chip = deltaChip(d);
        return { key: row.key, label: row.label, actual: row.actual, target: row.target, delta: d,
                 text: chip[0], tone: chip[1], under: d <= -2 };
    }

    // Non-land cards by CMC 0..6, 7+.
    function curveBuckets(cards) {
        var b = [0, 0, 0, 0, 0, 0, 0, 0];
        (cards || []).forEach(function (c) { if (!isLand(c)) b[Math.min(Math.floor(c.cmc || 0), 7)] += 1; });
        return b;
    }
    // Template shape (UX_SPEC §3.1.8), normalised to the deck's non-land count:
    // v * nonLand / sum(template). The template sums to 58, so dividing by 65
    // drew the line ~11% short (AN-4R, coder Q1). Unrounded: it only drives the
    // dashed path, and rounding per bucket would break the total.
    var CURVE_TEMPLATE_SUM = CURVE_TEMPLATE.reduce(function (s, v) { return s + v; }, 0);
    function curveRef(nonLand) {
        return CURVE_TEMPLATE.map(function (v) { return nonLand ? v * nonLand / CURVE_TEMPLATE_SUM : 0; });
    }

    // Spellbook near-miss ownership (UX_SPEC §3.1.10). rank: 0 owned·free, 1 owned elsewhere, 2 not owned.
    function comboOwnership(combo) {
        var miss = (combo.missing || [])[0];
        var own = miss && combo.owned ? combo.owned[miss] : null;
        if (own && own.available > 0) return { text: "owned · free", tone: "ok", rank: 0, missing: miss };
        if (own && own.copies > 0) {
            var where = (own.decks || [])[0];
            return { text: where ? "owned · in " + where : "owned", tone: "signal", rank: 1, missing: miss };
        }
        return { text: "not owned", tone: "neutral", rank: 2, missing: miss };
    }
    function sortCombos(list) {
        return (list || []).map(function (c, i) { return { c: c, i: i, r: comboOwnership(c).rank }; })
            .sort(function (a, b) { return a.r - b.r || a.i - b.i; })
            .map(function (x) { return x.c; });
    }

    // Pillar-fit meter segments: pillar / glue / generic.
    function fitMeter(fit) {
        fit = fit || {};
        var parts = [["pillar", fit.pillar || 0], ["glue", fit.glue || 0], ["generic", fit.generic || 0]];
        var total = parts.reduce(function (s, p) { return s + p[1]; }, 0) || 1;
        return parts.map(function (p) { return { key: p[0], count: p[1], pct: Math.round(p[1] / total * 1000) / 10 }; });
    }

    // "Least on-plan": bottom N non-land cards by fit score. Sub-line is always
    // "{edges} edge(s) · {short reason}" (UX_SPEC §3.1.9).
    function lowFit(identity, weak, lands, n) {
        var landSet = lands instanceof Set ? lands : new Set(lands || []);
        var byName = {};
        ((weak && weak.cards) || []).forEach(function (w) { byName[w.name] = w; });
        var cmd = {};
        ((identity && identity.deck && identity.deck.commanders) || []).forEach(function (c) { cmd[c] = 1; });
        return ((identity && identity.fit && identity.fit.per_card) || [])
            .filter(function (c) { return !landSet.has(c.name) && !cmd[c.name] && typeof c.score === "number"; })
            .sort(function (a, b) { return a.score - b.score; })
            .slice(0, n || 6)
            .map(function (c) {
                var w = byName[c.name], edges = w ? (w.edges || 0) : 0;
                var why = w && (w.reasons || []).filter(function (r) { return !/\bedges?$/.test(r); })[0];
                if (!why) why = c.bucket === "pillar" ? (c.pillar || "on pillar") : c.bucket === "glue" ? "glue" : "no pillar";
                return { name: c.name, score: c.score, display: c.score.toFixed(2), pet: !!(w && w.pet),
                         sub: edges + " edge" + (edges === 1 ? "" : "s") + " · " + why };
            });
    }

    function age(iso) {
        if (!iso) return "";
        var s = (Date.now() - new Date(iso).getTime()) / 1000;
        if (!isFinite(s)) return "";
        if (s < 90) return "just now";
        if (s < 5400) return Math.round(s / 60) + "m ago";
        if (s < 129600) return Math.round(s / 3600) + "h ago";
        return Math.round(s / 86400) + "d ago";
    }

    // Typing in a field never triggers single-key shortcuts (UX_SPEC §4).
    function inTextField(t) {
        if (!t) return false;
        var tag = t.tagName;
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!t.isContentEditable;
    }

    // ---- Journal / check-in (AN-5, UX_SPEC §3.3) ----------------------------------
    var TIERS = [[4, "casual"], [6, "focused"], [8, "optimized"], [10, "competitive"]];
    function tierOf(scale) {
        for (var i = 0; i < TIERS.length; i++) if (scale <= TIERS[i][0]) return TIERS[i][1];
        return "competitive";
    }
    function money(v) {
        if (v == null || !isFinite(v)) return "\u2014";
        var whole = Math.abs(v - Math.round(v)) < 0.005;
        return "$" + (whole ? Math.round(v).toLocaleString("en-US")
                            : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    }
    // "a → b" with the new value tinted: up = ok, down = signal (never red, D8).
    // `better` is +1 when a higher value is good, -1 when lower is good, 0 for neutral.
    function arrow(a, b, fmt, better) {
        var e = function (x) { return window.HUD ? window.HUD.esc(x) : String(x); };
        if (b == null) return a == null ? "\u2014" : e(fmt(a));
        if (a == null || a === b) return e(fmt(b));
        var cls = !better ? "" : ((b > a) === (better > 0) ? "up" : "down");
        return e(fmt(a)) + " \u2192 " + (cls ? '<span class="' + cls + '">' + e(fmt(b)) + "</span>" : e(fmt(b)));
    }
    function sinceItems(d) {
        d = d || {};
        var add = (d.added || []).length, rem = (d.removed || []).length;
        var pw = d.power || [], pr = d.price || [], op = d.on_plan || [];
        var pct = function (x) { return x + "%"; };
        return [
            { label: "Cards", html: (add ? '<span class="up">+' + add + "</span>" : "0") + " / " +
                                    (rem ? '<span class="down">' + MINUS + rem + "</span>" : "0") },
            { label: "Power est.", html: arrow(pw[0], pw[1], function (x) { return String(x); }, 1) },
            { label: "Price", html: arrow(pr[0], pr[1], money, 0) },
            { label: "On-plan", html: arrow(op[0], op[1], pct, 1) }
        ];
    }
    function progressSegments(rows) {
        return (rows || []).map(function (r) { return r.answer === "changed" ? "is-changed" : r.answer ? "is-done" : ""; });
    }
    function progressLabel(p) {
        p = p || {};
        return (p.answered || 0) + " of " + (p.total || 0) + " answered" + (p.changed ? " \u00b7 " + p.changed + " changed" : "");
    }

    // ---- Tune (AN-6, UX_SPEC §3.4) ---------------------------------------------
    // The option a decision takes: "own" | "buy" (null = the row's pick).
    function tuneOption(swap, decision) {
        var d = decision || swap.pick;
        return (d === "buy" ? swap.buy : swap.own) || swap.own || swap.buy || null;
    }
    function tuneSource(opt) {
        if (!opt) return "";
        if (opt.source === "buy") return "buy " + money(opt.price);
        if (opt.source === "free") return "owned \u00b7 free";
        if (opt.source === "accepted") return "accepted";
        return "from " + (opt.deck || "another deck");
    }
    // Accepted swaps + extra adds -> ordered change list, spend, on-plan projection.
    function changeList(tune, adds) {
        tune = tune || {};
        var items = [], spend = 0, swaps = 0;
        var on = (tune.on_plan && tune.on_plan.on) || 0, total = (tune.on_plan && tune.on_plan.total) || 0;
        var moves = [];
        (tune.swaps || []).forEach(function (s) {
            if (s.decision !== "own" && s.decision !== "buy") return;
            var o = tuneOption(s, s.decision);
            // The card accepted is the truth, even if a recompute now ranks another first.
            if (s.decision_add && (!o || o.name !== s.decision_add)) {
                o = [s.own, s.buy].filter(function (x) { return x && x.name === s.decision_add; })[0] ||
                    { name: s.decision_add, source: "accepted", price: 0, on_plan: true };
            }
            if (!o) return;
            swaps += 1;
            items.push({ op: "out", name: s.cut.name, source: "" });
            items.push({ op: "in", name: o.name, source: tuneSource(o) });
            if (o.source === "buy") spend += o.price || 0;
            if (o.source === "deck" && o.deck) moves.push({ name: o.name, from: o.deck, give: o.donor_replacement || null });
            on += (o.on_plan ? 1 : 0) - (s.cut.on_plan ? 1 : 0);
        });
        (adds || tune.adds || []).forEach(function (a) {
            items.push({ op: "in", name: a.name, qty: a.qty || 1, source: a.source === "basic" ? "basic land" : (a.source || "") });
            on += a.qty || 1; total += a.qty || 1;   // basics count as glue (lands row)
        });
        var before = tune.on_plan && tune.on_plan.pct;
        var after = total ? Math.round(on / total * 100) : null;
        return { items: items, swaps: swaps, spend: Math.round(spend * 100) / 100, moves: moves,
                 onPlan: [before == null ? null : before, after] };
    }
    function changeText(deckName, list) {
        var outs = list.items.filter(function (i) { return i.op === "out"; });
        var ins = list.items.filter(function (i) { return i.op === "in"; });
        var lines = ["// " + deckName + " \u00b7 Tune", "// Out"];
        outs.forEach(function (i) { lines.push("1 " + i.name); });
        lines.push("// In");
        ins.forEach(function (i) { lines.push((i.qty || 1) + " " + i.name + (i.source ? "  // " + i.source : "")); });
        lines.push("// Spend " + money(list.spend));
        return lines.join("\n");
    }
    // The three weak-performer inputs as mini-bars (low value = signal).
    function whyBars(why) {
        why = why || {};
        var fit = why.fit, edh = why.edhrec;
        var bars = [
            { key: "Edges", pct: Math.min(100, Math.round((why.edges || 0) / 10 * 100)), low: (why.edges || 0) <= 1, text: String(why.edges || 0) },
            { key: "Fit", pct: fit == null ? 0 : Math.round(fit * 100), low: fit == null || fit < 0.35, text: fit == null ? "\u2014" : fit.toFixed(2) },
            { key: "EDHREC", pct: Math.round((edh || 0) * 100), low: !edh || edh < 0.10, text: edh == null ? "0%" : Math.round(edh * 100) + "%" }
        ];
        // AN-7 card2vec: only when the model knows the card (unknown is not weak).
        if (why.played != null) bars.push({ key: "Played", pct: Math.round(why.played * 100), low: why.played < 0.35, text: why.played.toFixed(2) });
        return bars;
    }
    function gapText(g) { return g == null ? "" : (g >= 0 ? "+" : MINUS) + Math.abs(g).toFixed(2); }
    function verdictChip(swap) {
        if (swap.buy && swap.own) return swap.pick === "buy" ? ["worth buying", "signal"] : ["owned is good enough", "ok"];
        if (swap.verdict === "keep_yours") return ["keep yours", "ok"];
        return null;
    }
    function keepYoursLine(swap) {
        var n = swap.near_buy;
        if (!n) return "";
        return "Keep yours: " + gapText(n.gap) + " isn't worth " + money(n.price) + " (" + n.name + ").";
    }

    window.DeckAnalytics = {
        MANA_REASONS: MANA_REASONS, CURVE_TEMPLATE: CURVE_TEMPLATE,
        isLand: isLand, shortName: shortName, graphData: graphData, engineList: engineList,
        deltaChip: deltaChip, compRow: compRow, curveBuckets: curveBuckets, curveRef: curveRef,
        comboOwnership: comboOwnership, sortCombos: sortCombos, fitMeter: fitMeter, lowFit: lowFit,
        age: age, inTextField: inTextField,
        tierOf: tierOf, money: money, sinceItems: sinceItems, progressSegments: progressSegments,
        progressLabel: progressLabel,
        tuneOption: tuneOption, tuneSource: tuneSource, changeList: changeList, changeText: changeText,
        whyBars: whyBars, gapText: gapText, verdictChip: verdictChip, keepYoursLine: keepYoursLine
    };

    // Deck tabs: keys 1-5 switch tab (data-key on .hud-deck-tabs links).
    if (document.addEventListener) {
        // On narrow screens the strip scrolls inside itself: keep the active tab in view.
        document.addEventListener("DOMContentLoaded", function () {
            var strip = document.querySelector(".hud-deck-tabs"), act = strip && strip.querySelector('[aria-current="page"]');
            if (!act) return;
            var over = act.getBoundingClientRect().right - strip.getBoundingClientRect().right;
            if (over > 0) strip.scrollLeft += over;
        });
        document.addEventListener("keydown", function (e) {
            if (e.metaKey || e.ctrlKey || e.altKey || inTextField(e.target)) return;
            if (!/^[1-5]$/.test(e.key)) return;
            var a = document.querySelector('.hud-deck-tabs [data-key="' + e.key + '"]');
            if (a && a.getAttribute("aria-current") !== "page") { e.preventDefault(); window.location.href = a.getAttribute("href"); }
        });
    }
})();
