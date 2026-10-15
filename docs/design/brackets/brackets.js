/* Commander Lab BR-D mockups: shared shell, fixtures and renderers.
 * Docs only, NOT app code. Opens straight from disk (loads ../../../static/css/hud.css + hud.js).
 *
 * REAL (prod, 2026-10-15): the bracket table (GET /api/brackets), the 53 Game
 * Changers, Holy Hellfire's decklist (deck.js), its 3 Game Changers, its
 * journal target power (8) and the computed power estimate (6.5).
 * ILLUSTRATIVE: the BracketEvaluation objects below. They follow the shape the
 * BR-4/BR-5 cards ask for ({target_bracket, estimated_bracket, estimated_power_level,
 * conflicts[{severity, rule, message, cards[]}], explanation{signals[], summary}})
 * but the evaluator does not exist yet, so the Commander Salt numbers, power-band
 * wording and the bracket-1 scenario's target power (4) are made up for the mock.
 */
(function () {
    "use strict";
    var H = window.HUD, esc = H.esc;

    // ---- Bracket table: GET /api/brackets (as_of 2026-10-15) + our plain-language line
    var BRACKETS = [
        { n: 1, name: "Exhibition", gc: 0, turns: 9,
          line: "Theme over power. Show off something odd.",
          rules: "No Game Changers, two-card combos, extra turns or land denial." },
        { n: 2, name: "Core", gc: 0, turns: 8,
          line: "Straightforward and social. Telegraphed wins.",
          rules: "No Game Changers or two-card combos. A few extra turns, never looped." },
        { n: 3, name: "Upgraded", gc: 3, turns: 6,
          line: "Powered up. Strong synergy, can win in one big turn.",
          rules: "Up to 3 Game Changers. Two-card combos only late. No land denial." },
        { n: 4, name: "Optimized", gc: null, turns: 4,
          line: "Lethal, fast, consistent. Not tuned for the cEDH meta.",
          rules: "Anything legal goes." },
        { n: 5, name: "cEDH", gc: null, turns: null,
          line: "Built for the cEDH metagame. Winning over expression.",
          rules: "Anything legal goes. We never compute 5; you declare it." }
    ];
    function br(n) { return BRACKETS[n - 1]; }

    var DECK = { name: "Holy Hellfire", colors: "WBR", commander: "Kaalia of the Vast", status: "physical",
        commanderUid: "ef099f25-2b0b-4028-8490-fbc859e35f6c", cards: 100, lands: 35 };

    var GC = ["Demonic Tutor", "Necropotence", "Teferi's Protection"];            // real
    var TUTORS = ["Demonic Tutor", "Burning-Rune Demon", "Rune-Scarred Demon", "Razaketh, the Foulblooded"]; // real (oracle text)
    var POWER_TOL = 1.5; // analytics/engine.py power_aligned tolerance

    // ---- Explanation signals (shared by every scenario; values real unless marked)
    var SIGNALS = [
        { signal: "Game Changers", value: "3", effect: "Floor: Bracket 3", kind: "hard", drives: "bracket",
          cards: GC, source: "Scryfall is:gamechanger" },
        { signal: "Two-card combos", value: "0", effect: "No floor", kind: "hard", drives: "bracket",
          cards: [], source: "Commander Spellbook", illus: true },
        { signal: "Extra turns", value: "0", effect: "No floor", kind: "hard", drives: "bracket",
          cards: [], source: "BR-2 list" },
        { signal: "Mass land denial", value: "0", effect: "No floor", kind: "hard", drives: "bracket",
          cards: [], source: "BR-2 list" },
        { signal: "Tutors", value: "4", effect: "Note only", kind: "advisory", drives: "bracket",
          cards: TUTORS, source: "Oracle text" },
        { signal: "Commander Salt power", value: "6.6", effect: "Base for power", kind: "advisory", drives: "power",
          cards: [], source: "commandersalt.com", illus: true },
        { signal: "Average CMC", value: "4.69", effect: "Power −0.4 (slow curve)", kind: "advisory", drives: "power",
          cards: [], source: "Decklist" },
        { signal: "Interaction", value: "10 cards", effect: "Power +0.3", kind: "advisory", drives: "power",
          cards: [], source: "Lab tags", illus: true },
        { signal: "Spellbook estimate", value: "Bracket 3", effect: "Agrees", kind: "advisory", drives: "bracket",
          cards: [], source: "backend.commanderspellbook.com", illus: true }
    ];
    // NB: real journal target power is 8. The "target 2" scenarios use 4 (and
    // the bracket-1 one uses 3) so the story is coherent: illustrative. The
    // bracket-1 scenario also pretends Necropotence is a pet card (real pets:
    // Dragon Tempest, Lyra Dawnbringer) to show the pet-card advisory.
    var SUMMARY = "3 Game Changers (Demonic Tutor, Necropotence, Teferi's Protection) set a hard floor of Bracket 3. " +
        "No two-card combos, extra turns or land denial, so nothing pushes it to 4. Power 6.5: Commander Salt says 6.6, " +
        "the slow curve (average CMC 4.69) takes 0.4 off and 10 interaction pieces add 0.3 back.";

    function gcConflict(target) {
        var b = br(target);
        return { severity: "hard", rule: "game_changers", title: "3 Game Changers",
            message: "Bracket " + target + " allows " + (b.gc === 0 ? "none" : "up to " + b.gc) + ". Cut 3 to fit, or raise the target.",
            cards: GC };
    }
    function above(target, est) {
        return { severity: "advisory", rule: "plays_above", title: "Plays above target",
            message: "Estimated Bracket " + est + ", target " + target + ". The pod will feel the difference.", cards: [] };
    }

    function pwMismatch(tpw, pw) {
        return { severity: "advisory", rule: "power_mismatch", title: "Plays above target power",
            message: "Estimated power " + pw + ", target " + tpw + ": more than 1.5 apart.", cards: [] };
    }

    // ---- Scenarios (mock switcher). est = estimated_bracket, pw = estimated_power_level
    var SCENARIOS = {
        b2: { label: "Target 2 · conflicts", target: 2, tpw: 4, est: 3, pw: 6.5,
              conflicts: [gcConflict(2), above(2, 3), pwMismatch(4, 6.5)] },
        b1: { label: "Target 1 · pet card", target: 1, tpw: 3, est: 3, pw: 6.5, pets: ["Necropotence", "Dragon Tempest"],
              conflicts: [gcConflict(1), above(1, 3), pwMismatch(3, 6.5),
                  { severity: "advisory", rule: "pet_card", title: "Your pet card Necropotence is a Game Changer",
                    message: "It's on your pets list, so it's your call: keep it and play Bracket 3, or let it go. We won't suggest cutting it.", cards: ["Necropotence"] }] },
        b3: { label: "Target 3 · no conflicts", target: 3, tpw: 7, est: 3, pw: 6.5, conflicts: [] },
        b4: { label: "Target 4 · plays below", target: 4, tpw: 8, est: 3, pw: 6.5,
              conflicts: [{ severity: "advisory", rule: "plays_below", title: "Plays below target",
                  message: "Estimated Bracket 3, target 4. Fine if you're sandbagging on purpose.", cards: [] }] },
        unset: { label: "No target", target: null, tpw: 8, est: 3, pw: 6.5, conflicts: [] },
        pending: { label: "Not evaluated yet", pending: true, target: 2, tpw: 4 },
        running: { label: "Re-evaluating", running: true, target: 2, tpw: 4, est: 3, pw: 6.5,
              conflicts: [gcConflict(2), above(2, 3), pwMismatch(4, 6.5)] }
    };
    // Drop the "we checked" row from counts.
    function real(conflicts) { return (conflicts || []).filter(function (c) { return !c.quiet; }); }
    function counts(s) {
        var c = real(s.conflicts), hard = c.filter(function (x) { return x.severity === "hard"; }).length;
        return { all: c.length, hard: hard, adv: c.length - hard };
    }
    // card name -> { sev: "hard"|"adv", reasons: [conflict...] }. Hard wins.
    function offenders(s) {
        var m = {};
        real(s.conflicts).forEach(function (c) {
            c.cards.forEach(function (n) {
                var o = m[n] || (m[n] = { sev: "adv", reasons: [] });
                o.reasons.push(c);
                if (c.severity === "hard") o.sev = "hard";
            });
        });
        return m;
    }

    function current() {
        var k = (location.hash.match(/s=([a-z0-9]+)/) || [])[1] || "b2";
        return { key: k, s: SCENARIOS[k] || SCENARIOS.b2 };
    }
    function view() { return (location.hash.match(/v=([a-z]+)/) || [])[1] || "deck"; }
    function setHash(k, v) { location.hash = "s=" + k + "&v=" + v; }

    // ---- Shell (mirrors base.html so hud.css styles it for real)
    var NAV = [
        ["Home", '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>'],
        ["Commander Decks", '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>', true],
        ["Inventory", '<rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line>'],
        ["Instances", '<circle cx="9" cy="7" r="4"></circle><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>'],
        ["Tag Management", '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path>']
    ];
    function shell() {
        var items = NAV.map(function (n) {
            return '<a href="#" class="nav-item' + (n[2] ? ' active" aria-current="page' : "") + '" title="' + n[0] + '">' +
                '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' + n[1] + "</svg>" +
                '<span class="nav-label">' + n[0] + "</span></a>";
        }).join("");
        var nav = document.createElement("nav");
        nav.className = "sidebar"; nav.setAttribute("aria-label", "Primary");
        nav.innerHTML = '<a href="#" class="brand"><svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg><span class="brand-label">Cmdr Lab</span></a>' +
            '<div class="nav-section-label">Navigate</div><div class="nav-items">' + items + "</div>" +
            '<div class="sidebar-footer"><span class="sidebar-footer-label">Version</span><span class="version-badge">v0.4.32</span></div>';
        document.body.classList.add("app-shell");
        document.body.insertBefore(nav, document.body.firstChild);
    }

    // Mock-only state switcher. Dashed so it never reads as part of the design.
    function switcher(dir) {
        var cur = current(), v = view();
        var opts = Object.keys(SCENARIOS).map(function (k) {
            return '<option value="' + k + '"' + (k === cur.key ? " selected" : "") + ">" + SCENARIOS[k].label + "</option>";
        }).join("");
        return '<div class="br-mockbar" role="region" aria-label="Mockup controls"><span class="hud-label">Mockup · ' + esc(dir) + '</span>' +
            '<label>State <select id="mockState">' + opts + "</select></label>" +
            '<span class="br-mockbar-views"><a href="#s=' + cur.key + '&v=deck"' + (v === "deck" ? ' aria-current="page"' : "") + ">Deck page</a>" +
            '<a href="#s=' + cur.key + '&v=journal"' + (v === "journal" ? ' aria-current="page"' : "") + ">Journal</a></span>" +
            '<a class="br-mockbar-link" href="a-inline.html' + location.hash + '">A</a><a class="br-mockbar-link" href="b-ledger.html' + location.hash + '">B</a><a class="br-mockbar-link" href="c-rule0.html' + location.hash + '">C</a></div>';
    }

    function deckhead(active, metaExtra, actions) {
        var art = H.scryfall(DECK.commanderUid, "art_crop");
        var tabs = [["overview", "Overview", "deck"], ["stats", "Stats"], ["identity", "Identity", null, "4"], ["journal", "Journal", "journal"], ["tune", "Tune", null, "17"], ["ideas", "Ideas", null, "2"]];
        var t = tabs.map(function (x, i) {
            var on = x[0] === active, href = x[2] ? "#s=" + current().key + "&v=" + x[2] : "#";
            return '<a class="hud-tab' + (on ? " active" : "") + '" href="' + href + '"' + (on ? ' aria-current="page"' : "") + ' data-key="' + (i + 1) + '">' +
                x[1] + (x[3] ? ' <span class="hud-tab-count">' + x[3] + "</span>" : "") + "</a>";
        }).join("");
        return '<div class="app-topbar"><ol class="hud-crumbs"><li><a href="#">Decks</a></li><li><a href="#">' + DECK.name + '</a></li><li aria-current="page">' +
            (active === "journal" ? "Journal" : "Overview") + '</li></ol><div class="app-topbar-meta"><span class="hud-freshness">Archidekt #417707</span></div></div>' +
            '<header class="hud-deckhead"><div class="hud-cmdrs hud-deckhead-art"><figure class="hud-cmdr hud-cmdr--commander"><span class="hud-cmdr-art"><img alt="Kaalia of the Vast" src="' + art.src + '" data-fallback="' + art.fallback + '"></span></figure></div>' +
            '<div><span class="hud-label">Commander deck · ' + DECK.commander + "</span><h1>" + DECK.name + "</h1>" +
            '<div class="hud-deckhead-meta">' + H.pips(DECK.colors, "sm") + H.statusChip(DECK.status) +
            '<span class="hud-freshness">' + DECK.cards + " cards · " + DECK.lands + " lands</span>" + (metaExtra || "") + "</div></div>" +
            '<div class="hud-deckhead-actions">' + (actions || "") + "</div></header>" +
            '<nav class="hud-tabs hud-deck-tabs" aria-label="Deck sections">' + t + "</nav>";
    }

    // ---- Decklist (real cards). flag(card) -> {cls, chip, label} | null
    var GROUPS = [["Commanders", function (c) { return c[5]; }], ["Creatures", function (c) { return !c[5] && c[4] === "Creature"; }],
        ["Artifacts", function (c) { return c[4] === "Artifact"; }], ["Enchantments", function (c) { return c[4] === "Enchantment"; }],
        ["Instants", function (c) { return c[4] === "Instant"; }], ["Sorceries", function (c) { return c[4] === "Sorcery"; }],
        ["Lands", function (c) { return c[4] === "Land"; }]];
    function sortCards(a, b) { return (a[2] - b[2]) || a[0].localeCompare(b[0]); }
    function decklist(flag, opts) {
        opts = opts || {};
        var list = window.DECKLIST.slice().sort(sortCards);
        var only = opts.only;
        return GROUPS.map(function (g) {
            var cards = list.filter(g[1]);
            if (only) cards = cards.filter(function (c) { return flag(c[0]); });
            if (!cards.length) return "";
            var n = cards.filter(function (c) { return flag(c[0]); }).length;
            return '<section class="deck-group"><div class="hud-section-head"><h2>' + g[0] + '</h2><span class="deck-group-count">' + cards.length +
                (n && !only ? ' · <button type="button" class="br-count-flag" data-show="' + esc(cards.filter(function (c) { return flag(c[0]); })[0][0]) + '">' + n + " flagged ↓</button>" : "") + "</span></div>" +
                '<div class="deck-card-grid">' + cards.map(function (c) { return cardTile(c, flag(c[0]), opts); }).join("") + "</div></section>";
        }).join("");
    }
    function cardTile(c, f, opts) {
        var s = H.scryfall(c[1], "normal");
        var land = c[4] === "Land";
        var html = H.art(c[0], s.src, { kind: "card", fallback: s.fallback, meta: land ? "Land" : "CMC " + c[2], colors: land ? null : c[3],
            href: "#", attrs: { "data-card": c[0] } });
        if (!f) return html;
        // Flag: severity bar on top of the frame + a reason line in the caption.
        // Nothing goes on the card image (kit rule: captions sit below the art).
        html = html.replace('class="hud-art hud-art--card"', 'class="hud-art hud-art--card br-flag br-flag--' + f.sev + (opts.flagCls ? " " + opts.flagCls : "") + '"');
        html = html.replace('</span></div></a>', '</span><span class="br-flag-why"><span aria-hidden="true">' + (f.sev === "hard" ? "■" : "▲") + "</span> " +
            '<span class="br-sr">' + (f.sev === "hard" ? "Hard rule: " : "Advisory: ") + "</span>" + esc(f.chip) + " · " + esc(f.label) + "</span></div></a>");
        return html;
    }

    // ---- Small pieces every direction reuses
    function sevChip(sev) {
        return sev === "hard" ? '<span class="br-sev br-sev--hard"><span aria-hidden="true">■</span> Hard rule</span>'
            : '<span class="br-sev br-sev--adv"><span aria-hidden="true">▲</span> Advisory</span>';
    }
    function bracketChip(n, tone, prefix) {
        if (!n) return H.chip((prefix || "Bracket") + " · unset", "neutral");
        return '<span class="hud-chip hud-chip--' + (tone || "neutral") + ' br-bchip">' + (prefix ? esc(prefix) + " " : "") + "B" + n + " · " + br(n).name + "</span>";
    }
    function cardLinks(cards) {
        return cards.map(function (n) { return '<button type="button" class="hud-cardlink" data-show="' + esc(n) + '">' + esc(n) + "</button>"; }).join(" ");
    }
    // One delta notation for both estimates: arrow + signed number, coloured by
    // "outside tolerance" (bracket: any difference; power: > 1.5), never by sign.
    function pwDelta(s) {
        var d = Math.round((s.pw - s.tpw) * 10) / 10, out = Math.abs(d) > POWER_TOL;
        var arrow = d > 0 ? "▲ +" : d < 0 ? "▼ −" : "● ";
        return { d: d, out: out, text: arrow + Math.abs(d).toFixed(1) + (out ? " · outside ±1.5" : " · within ±1.5") };
    }
    function brDelta(s) {
        if (!s.target) return { out: false, text: "" };
        var d = s.est - s.target;
        return { d: d, out: d !== 0, text: d > 0 ? "▲ +" + d + " above target" : d < 0 ? "▼ −" + (-d) + " below target" : "● on target" };
    }
    function deltaHtml(x) { return x.text ? '<span class="br-delta ' + (x.out ? "is-out" : "is-in") + '">' + esc(x.text) + "</span>" : ""; }
    function explanation(s, open) {
        var rows = SIGNALS.map(function (g) {
            return "<tr><th scope=\"row\">" + esc(g.signal) + (g.illus ? ' <span class="br-illus" title="Illustrative value in this mockup">*</span>' : "") + "</th>" +
                '<td class="is-num">' + esc(g.value) + "</td><td>" + esc(g.effect) + "</td>" +
                "<td>" + (g.kind === "hard" ? '<span class="br-sev br-sev--hard br-sev--sm">Hard</span>' : '<span class="br-sev br-sev--adv br-sev--sm">Advisory</span>') +
                ' <span class="hud-muted">' + (g.drives === "power" ? "→ power" : "→ bracket") + "</span></td>" +
                '<td class="hud-muted">' + esc(g.source) + "</td></tr>";
        }).join("");
        return '<details class="hud-tray br-why"' + (open ? " open" : "") + '><summary>How this was decided <span class="hud-muted">· ' + SIGNALS.length + " signals</span></summary>" +
            '<div class="br-why-body"><p class="br-why-summary">' + esc(SUMMARY) + "</p>" +
            '<div class="hud-table-wrap"><table class="hud-table br-why-table"><thead><tr><th scope="col">Signal</th><th scope="col" class="is-num">Value</th><th scope="col">Effect</th><th scope="col">Kind</th><th scope="col">Source</th></tr></thead><tbody>' +
            rows + "</tbody></table></div>" +
            '<p class="hud-field-hint">Hard signals set a floor (the lowest bracket the deck can be). Advisory signals never move the bracket. Evaluated 4 min ago · input a3f9c2 · <span class="br-illus">*</span> illustrative in this mockup.</p></div></details>';
    }

    // Journal: the existing Target power section (live markup) + a bracket control slot.
    function journalPage(s, bracketControl, sectionTitle, sectionHint) {
        var opts = "";
        var estN = s.pw != null ? Math.floor(s.pw) : null; // notch sits on the cell the estimate falls in (6.5 → 6)
        for (var i = 1; i <= 10; i++) opts += '<input type="radio" name="power" value="' + i + '" id="pw-' + i + '"' + (i === s.tpw ? " checked" : "") + '><label for="pw-' + i + '"' + (i === estN ? ' class="is-est" title="Estimate ' + s.pw + '"' : "") + '>' + i + "</label>";
        var tier = s.tpw <= 4 ? "casual" : s.tpw <= 6 ? "focused" : s.tpw <= 8 ? "optimized" : "competitive";
        return '<div class="hud-section-head"><span class="hud-section-id">01</span><h2>Deck journal</h2></div>' +
            '<nav class="jr-anchor" aria-label="Journal sections"><a href="#">Vision</a><a href="#">Win cons</a><a href="#j-power" class="is-on">' + esc(sectionTitle) + '</a><a href="#">Budget</a><a href="#">Pets &amp; novelties</a><a href="#">Pillars</a></nav>' +
            '<div class="hud-form">' +
            '<section class="hud-form-section br-ghost-section"><header><h2>Win cons</h2><p>Primary is one choice. Secondary can be several.</p></header><div><span class="hud-muted">Combat (primary) · commander damage</span></div></section>' +
            '<section class="hud-form-section" id="j-power"><header><h2>' + esc(sectionTitle) + "</h2><p>" + sectionHint + "</p></header><div>" +
            '<div class="hud-field"><span class="hud-field-label" id="pwL">Power · <span class="jr-power-now">' + s.tpw + " · " + tier + "</span></span>" +
            '<span class="hud-seg hud-seg--scale br-a-seg" role="radiogroup" aria-labelledby="pwL">' + opts + "</span>" +
            '<div class="hud-scale-tiers" aria-hidden="true"><span' + (tier === "casual" ? ' class="is-on"' : "") + '>Casual</span><span' + (tier === "focused" ? ' class="is-on"' : "") + '>Focused</span><span' + (tier === "optimized" ? ' class="is-on"' : "") + '>Optimized</span><span' + (tier === "competitive" ? ' class="is-on"' : "") + ">cEDH</span></div>" +
            '<span class="hud-field-hint">Current estimate: ' + (s.pw != null ? s.pw + " (computed)" + (Math.abs(s.pw - s.tpw) > POWER_TOL ? ' · <span class="br-t-adv">▲ ' + Math.abs(s.pw - s.tpw).toFixed(1) + " from target (over 1.5)</span>" : " · within 1.5 of target") : "computing…") + "</span></div>" +
            bracketControl +
            "</div></section>" +
            '<section class="hud-form-section br-ghost-section"><header><h2>Budget</h2><p>Caps the tuner\'s suggestions.</p></header><div><span class="hud-muted">$650 total · per-card hard $40 / soft $15</span></div></section>' +
            "</div>" +
            '<div class="hud-savebar is-dirty"><span class="hud-freshness">Unsaved · target bracket ' + (s.target ? "→ " + s.target : "cleared") + '</span><button class="hud-btn hud-btn--ghost" type="button">Discard</button><button class="hud-btn hud-btn--primary" type="button">Save journal</button></div>';
    }

    // Shared behaviour: card-link → scroll to card + pulse; Esc clears highlight.
    function bindCommon(root) {
        root.addEventListener("click", function (e) {
            var b = e.target.closest("[data-show]");
            if (b) {
                e.preventDefault();
                var el = root.querySelector('.hud-art[data-card="' + CSS.escape(b.getAttribute("data-show")) + '"]');
                if (el) { el.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
                    el.classList.remove("is-pulse"); void el.offsetWidth; el.classList.add("is-pulse"); el.focus({ preventScroll: true }); }
            }
            if (e.target.closest('a[href="#"]')) e.preventDefault();
            var j = e.target.closest("[data-jump]");
            if (j) { var t = document.getElementById(j.getAttribute("data-jump")); if (t) { t.scrollIntoView({ block: "start" }); var sec = t.closest("section"); if (sec) { sec.setAttribute("tabindex", "-1"); sec.focus({ preventScroll: true }); } } }
            var l = e.target.closest("[data-lens]");
            if (l) { var first = root.querySelector(".br-flag"); if (first) { first.scrollIntoView({ block: "center" }); first.focus({ preventScroll: true }); } }
        });
        var sel = document.getElementById("mockState");
        if (sel) sel.addEventListener("change", function () { setHash(sel.value, view()); });
    }

    function boot(dir, render) {
        shell();
        var main = document.getElementById("main");
        function draw() {
            var c = current();
            main.innerHTML = render(c.s, view(), c.key);
            var top = main.querySelector(".app-topbar");
            if (top) top.insertAdjacentHTML("afterend", switcher(dir)); else main.insertAdjacentHTML("afterbegin", switcher(dir));
            bindCommon(main);
            if (render.after) render.after(main, c.s, view());
        }
        window.addEventListener("hashchange", draw);
        draw();
    }

    window.BR = { BRACKETS: BRACKETS, br: br, DECK: DECK, GC: GC, TUTORS: TUTORS, SIGNALS: SIGNALS, SUMMARY: SUMMARY, POWER_TOL: POWER_TOL,
        SCENARIOS: SCENARIOS, counts: counts, offenders: offenders, real: real, current: current, view: view, setHash: setHash,
        deckhead: deckhead, decklist: decklist, sevChip: sevChip, bracketChip: bracketChip, cardLinks: cardLinks, pwDelta: pwDelta, brDelta: brDelta, deltaHtml: deltaHtml,
        explanation: explanation, journalPage: journalPage, boot: boot, esc: esc };
})();
