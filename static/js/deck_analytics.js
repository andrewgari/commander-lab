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
    // Template shape (UX_SPEC §3.1.8), normalised so the line always sums to the
    // deck's non-land count: v * nonLand / sum(template). The template sums to 58,
    // so dividing by 65 drew the line ~11% short (AN-4R, coder Q1).
    var CURVE_TEMPLATE_SUM = CURVE_TEMPLATE.reduce(function (s, v) { return s + v; }, 0);
    function curveRef(nonLand) {
        return CURVE_TEMPLATE.map(function (v) { return nonLand ? Math.round(v * nonLand / CURVE_TEMPLATE_SUM * 10) / 10 : 0; });
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

    window.DeckAnalytics = {
        MANA_REASONS: MANA_REASONS, CURVE_TEMPLATE: CURVE_TEMPLATE,
        isLand: isLand, shortName: shortName, graphData: graphData, engineList: engineList,
        deltaChip: deltaChip, compRow: compRow, curveBuckets: curveBuckets, curveRef: curveRef,
        comboOwnership: comboOwnership, sortCombos: sortCombos, fitMeter: fitMeter, lowFit: lowFit,
        age: age, inTextField: inTextField
    };

    // Deck tabs: keys 1-5 switch tab (data-key on .hud-deck-tabs links).
    if (document.addEventListener) {
        document.addEventListener("keydown", function (e) {
            if (e.metaKey || e.ctrlKey || e.altKey || inTextField(e.target)) return;
            if (!/^[1-5]$/.test(e.key)) return;
            var a = document.querySelector('.hud-deck-tabs [data-key="' + e.key + '"]');
            if (a && a.getAttribute("aria-current") !== "page") { e.preventDefault(); window.location.href = a.getAttribute("href"); }
        });
    }
})();
