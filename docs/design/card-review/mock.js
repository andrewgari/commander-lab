/* Commander Lab: Single Card Review mock data + scripted playback (CR-D).
 * Docs only, NOT app code.
 *
 * REAL: the deck, card names, CMC, colours and Scryfall ids (Holy Hellfire).
 * ILLUSTRATIVE: every EDHREC %, score, price, ownership, argument and ruling.
 * They are the SHAPE the review API should return (UX_SPEC.md section 7),
 * not values it will return.
 */
(function () {
    "use strict";
    var C = MOCK.CARDS;

    var CARD = { name: "Steel Hellkite", uid: C["Steel Hellkite"].uid, cmc: 6, colors: "", type: "Artifact Creature, Dragon", slot: "Creatures" };

    var ARGS = [
        { seq: 1, side: "keep",   round: "opening",  text: "Kaalia puts Dragons into play attacking for free, so the 6 mana cost never matters. Steel Hellkite is colourless, so it can’t be stranded by the WBR mana." },
        { seq: 2, side: "remove", round: "opening",  text: "Only 12% of Kaalia decks run it, against a 31% median for this deck. Strength 34 is the 6th lowest of 99 cards." },
        { seq: 3, side: "keep",   round: "rebuttal", text: "Its pump makes a hasted Hellkite a real clock, and it flies. That is evasion most of the other Dragons lack." },
        { seq: 4, side: "remove", round: "rebuttal", text: "The pump costs {2} per use and nothing triggers on entry or attack. Six other Dragons already fill this slot with a trigger attached." }
    ];

    var VERDICT = {
        ruling: "remove", confidence: 0.78, strength: 34, deck_median: 62, reviewed: "just now",
        judge_line: "Remove. The deck has surplus Dragons, and Kaalia’s discount doesn’t make a no-trigger threat worth a slot.",
        role: { label: "Top-end threat", detail: "9 in deck · target 7 to 9" },
        signals: [
            { label: "EDHREC", v: 0.12, text: "12%", low: true },
            { label: "Fit to deck", v: 0.41, text: "0.41", low: true },
            { label: "Role overlap", v: 0.86, text: "6 Dragons" }
        ],
        reasons: {
            keep: [
                { text: "Kaalia cheats it in for free, so its CMC is irrelevant.", cite: "Kaalia ability" },
                { text: "Colourless: castable off any mana base.", cite: "colour identity" },
                { text: "Flying plus a firebreathing pump gives it reach.", cite: "oracle text" }
            ],
            remove: [
                { text: "12% inclusion in Kaalia decks versus 31% median here.", cite: "EDHREC 12%" },
                { text: "Role is saturated: 6 other Dragons do this job with a trigger.", cite: "9 top-end, target 7 to 9" },
                { text: "No ETB or attack value to pair with Kaalia’s engine.", cite: "AN-1 edges: 1" }
            ]
        },
        notes: []
    };

    var REPL = [
        { name: "Scourge of the Throne", uid: "cd44f295-c4c8-4f47-9d51-d5ba578f7f8d", cmc: 6, colors: "R", kind: "like_for_like", role: "Top-end threat", from_role: "Top-end threat",
          rationale: "Same slot, a better body: a Dragon with flying and an extra-combat attack trigger that doubles Kaalia’s value.", edhrec: 24, price: 4.1, ownership: "owned_free", where: "box, free", oracle: "Flying, dethrone. Whenever Scourge attacks for the first time each turn, if it’s attacking the player with the most life or tied, untap all attacking creatures. After this phase there is an additional combat phase." },
        { name: "Neheb, the Eternal", uid: "767c5831-2195-4efa-802d-2ea2e6dabb5b", cmc: 5, colors: "R", kind: "like_for_like", role: "Top-end threat", from_role: "Top-end threat",
          rationale: "Cheaper by one and makes mana off each Dragon-sized swing, so it pays for itself when Kaalia connects.", edhrec: 19, price: 18.5, ownership: "not_owned", where: "", flag: "Over soft cap $15", oracle: "At the beginning of your postcombat main phase, add {R} for each 1 life your opponents have lost this turn. You don’t lose this mana as steps and phases end." },
        { name: "Moraug, Fury of Akoum", uid: "4481eab8-9e7d-4db8-b1f2-fdf5ee1919a2", cmc: 5, colors: "R", kind: "function_change", role: "Extra combat", from_role: "Top-end threat",
          rationale: "The deck is long on threats and short on ways to attack twice. Moraug turns every Kaalia hit into two.", edhrec: 33, price: 6.4, ownership: "not_owned", where: "", oracle: "Each creature you control gets +1/+0 for each time it has attacked this turn. Landfall: if it’s your main phase, there is an additional combat phase after this phase (once each turn)." },
        { name: "Combat Celebrant", uid: "28b63c3d-2e55-4343-b49a-11fa602ec473", cmc: 3, colors: "R", kind: "function_change", role: "Extra combat", from_role: "Top-end threat",
          rationale: "A three-drop that exerts for another combat. Frees three mana a turn on the turn Kaalia lands.", edhrec: 28, price: 1.2, ownership: "owned_elsewhere", where: "Dawnbringer", oracle: "If Combat Celebrant hasn’t been exerted this turn, you may exert it as it attacks. When you do, untap all other creatures you control and after this phase there is an additional combat phase." }
    ];

    var NUMBERS = { text: "No debate, no ruling. These come straight from EDHREC and the deck’s own tags.", edhrec: ["12%", "of 4,210 Kaalia decks"], strength: ["34 / 100", "deck median 62"], role: ["Top-end", "9 in deck, target 7 to 9"] };

    function deck(kind) { return { kind: kind, name: "Holy Hellfire" }; }
    function ev(full) {
        return {
            edhrec: full >= 1 ? { main: "12%", sub: "of 4,210 Kaalia decks" } : null,
            fit: full >= 2 ? { main: "0.41", sub: "nearest: Hellkite Charger" } : null,
            role: full >= 3 ? { main: "Top-end threat", sub: "9 in deck · target 7 to 9" } : null
        };
    }
    function run(step, elapsed, speaking) { return { step: step, elapsed: elapsed, speaking: speaking || null }; }
    function verdictState(kind, over, repl) {
        return Object.assign({ deck: deck(kind), card: CARD, phase: "verdict", verdict: Object.assign({}, VERDICT), repl: repl || { state: "ready", items: REPL, chosen: null, skipped: [], cursor: 0 } }, over || {});
    }
    function running(kind, step, elapsed, speaking, nArgs, evN) {
        return { deck: deck(kind), card: CARD, phase: "running", run: run(step, elapsed, speaking), evidence: ev(evN), args: ARGS.slice(0, nArgs), live: "" };
    }

    var STATES = {
        "run-1-evidence":  { label: "Running · 1 evidence",        fn: function (k) { return running(k, 0, 3, null, 0, 2); } },
        "run-2-opening":   { label: "Running · 2 opening (stream)", fn: function (k) { var s = running(k, 1, 9, "remove", 2, 3); s.args = ARGS.slice(0, 2).map(function (a, i) { return Object.assign({}, a, { streaming: i === 1, text: i === 1 ? a.text.slice(0, 72) : a.text }); }); return s; } },
        "run-3-rebuttal":  { label: "Running · 3 rebuttal",        fn: function (k) { return running(k, 2, 16, "keep", 3, 3); } },
        "run-4-ruling":    { label: "Running · 4 judge",           fn: function (k) { return running(k, 3, 22, null, 4, 3); } },
        "run-slow":        { label: "Running · slow (30s+)",       fn: function (k) { return running(k, 1, 41, null, 1, 3); } },
        "verdict-loading": { label: "Verdict · replacements loading", fn: function (k) { return verdictState(k, null, { state: "loading" }); } },
        "verdict-remove":  { label: "Verdict · remove + replacements", fn: function (k) { return verdictState(k); } },
        "remove-chosen":   { label: "Remove · one queued",         fn: function (k) { return verdictState(k, null, { state: "ready", items: REPL, chosen: k === "physical" ? "Moraug, Fury of Akoum" : "Scourge of the Throne", skipped: ["Neheb, the Eternal"], cursor: 0 }); } },
        "remove-closecall": { label: "Remove · close call (52%)",  fn: function (k) { var s = verdictState(k); s.verdict.confidence = 0.52; s.verdict.judge_line = "Remove, narrowly. Both sides had a real point; the Dragon glut tipped it."; return s; } },
        "repl-empty":      { label: "Replacements · empty",        fn: function (k) { return verdictState(k, null, { state: "empty", reason: "Every candidate in W/B/R under your $15 soft cap was either already in the deck or scored below Steel Hellkite’s own 34. Widening the search drops the cap and allows off-role cards." }); } },
        "repl-error":      { label: "Replacements · error",        fn: function (k) { return verdictState(k, null, { state: "error", error: "502 edhrec_unavailable" }); } },
        "verdict-keep":    { label: "Verdict · keep",              fn: function (k) {
            var s = verdictState(k, null, { state: "hidden" }); s.card = Object.assign({}, CARD, { name: "Lightning Greaves", uid: C["Lightning Greaves"].uid, cmc: 2, type: "Artifact — Equipment" });
            s.verdict = Object.assign({}, VERDICT, { ruling: "keep", confidence: 0.91, strength: 88, judge_line: "Keep. Haste and shroud are what let Kaalia attack the turn she lands and survive the swing back.",
                role: { label: "Protection", detail: "3 in deck · target 3 to 5" }, signals: [{ label: "EDHREC", v: 0.64, text: "64%" }, { label: "Fit to deck", v: 0.92, text: "0.92" }, { label: "Role overlap", v: 0.4, text: "2 others" }],
                reasons: { keep: [{ text: "Gives Kaalia haste and shroud for 0 to equip.", cite: "EDHREC 64%" }, { text: "Protects the engine: 8 cards in the Keep her alive pillar.", cite: "pillar" }], remove: [{ text: "Does nothing if Kaalia is already gone.", cite: "situational" }] } });
            return s; } },
        "degraded":        { label: "Verdict · no EDHREC data",    fn: function (k) { var s = verdictState(k); s.verdict.notes = [{ tone: "info", text: "No EDHREC data for Steel Hellkite in this commander. The ruling used deck fit and oracle text only, so confidence is capped at Medium." }]; s.verdict.signals[0] = { label: "EDHREC", v: 0, text: "no data", low: true }; s.verdict.confidence = 0.62; return s; } },
        "stale":           { label: "Verdict · saved, deck changed", fn: function (k) { var s = verdictState(k); s.verdict.reviewed = "2 days ago"; s.verdict.notes = [{ tone: "signal", text: "The deck changed since this review (3 cards swapped). The ruling may no longer hold.", action: ["rerun", "Re-run review"] }]; return s; } },
        "err-offline":     { label: "Error · oracle offline",      fn: function (k) { return { deck: deck(k), card: CARD, phase: "error", error: { code: "offline" }, run: run(0, 2), numbers: NUMBERS }; } },
        "err-timeout":     { label: "Error · timeout",             fn: function (k) { return { deck: deck(k), card: CARD, phase: "error", error: { code: "timeout" }, run: run(2, 90), numbers: NUMBERS }; } },
        "err-badruling":   { label: "Error · no ruling (args kept)", fn: function (k) { return { deck: deck(k), card: CARD, phase: "error", error: { code: "failed" }, run: run(3, 27), evidence: ev(3), args: ARGS }; } },
        "err-gone":        { label: "Error · card left the deck",  fn: function (k) { return { deck: deck(k), card: CARD, phase: "error", error: { code: "gone" }, run: run(1, 11) }; } },
        "err-network":     { label: "Error · stream dropped",      fn: function (k) { return { deck: deck(k), card: CARD, phase: "error", error: { code: "network" }, run: run(2, 19), evidence: ev(3), args: ARGS.slice(0, 3) }; } },
        "blocked-cmdr":    { label: "Blocked · commander",         fn: function (k) { return { deck: deck(k), card: { name: "Kaalia of the Vast", uid: C["Kaalia of the Vast"].uid, cmc: 4, colors: "WBR", type: "Legendary Creature, Angel Cleric", slot: "Commander" }, phase: "blocked", blocked: { h: "Commanders aren’t reviewed", p: "The commander defines what the rest of the deck is judged against, so there is nothing to argue for removing. Review any other card instead." } }; } }
    };

    // Scripted live playback: evidence -> opening (streamed) -> rebuttal -> ruling -> verdict -> replacements.
    function play(modal, kind, done) {
        var t = 0, timers = [], s = running(kind, 0, 0, null, 0, 0), paint = function () { CardReview.show(modal, s); };
        var at = function (ms, fn) { timers.push(setTimeout(function () { fn(); paint(); }, ms)); };
        var live = function (m) { s.live = m; };
        var tick = setInterval(function () { if (s.phase === "running") { s.run.elapsed++; var c = modal.querySelector(".hud-job-clock"); if (c) c.textContent = String(0).padStart(2, "0") + ":" + String(s.run.elapsed).padStart(2, "0"); } }, 1000);
        paint();
        at(700, function () { s.evidence = ev(1); });
        at(1500, function () { s.evidence = ev(2); });
        at(2200, function () { s.evidence = ev(3); s.run.step = 1; live("Opening arguments started."); });
        function stream(i, start) {
            var a = ARGS[i], words = a.text.split(" "), n = 0;
            at(start, function () { s.run.speaking = a.side; s.args.push(Object.assign({}, a, { text: "", streaming: true })); });
            for (var w = 1; w <= words.length; w++) (function (w) { at(start + 150 + w * 55, function () { s.args[s.args.length - 1].text = words.slice(0, w).join(" "); if (w === words.length) { s.args[s.args.length - 1].streaming = false; s.run.speaking = null; } }); })(w);
            return start + 150 + words.length * 55 + 300;
        }
        var nxt = stream(0, 2500); nxt = stream(1, nxt);
        at(nxt + 100, function () { s.run.step = 2; live("Rebuttals started."); });
        nxt = stream(2, nxt + 300); nxt = stream(3, nxt);
        at(nxt + 100, function () { s.run.step = 3; live("The judge is deliberating."); });
        at(nxt + 1800, function () { clearInterval(tick); s = verdictState(kind, null, { state: "loading" }); s.live = "Verdict: remove, 78 percent confidence, strength 34 of 100."; });
        at(nxt + 3200, function () { s.repl = { state: "ready", items: REPL, chosen: null, skipped: [], cursor: 0 }; s.live = "4 replacement options."; });
        at(nxt + 3300, function () { if (done) done(); });
        return function cancel() { timers.forEach(clearTimeout); clearInterval(tick); };
    }

    window.CRMOCK = { CARD: CARD, STATES: STATES, play: play, REPL: REPL };
})();
