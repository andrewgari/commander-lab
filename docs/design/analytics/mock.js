/* Commander Lab AN mockups: shared shell + fixture. Docs only, NOT app code.
 *
 * Card names, CMC, colours and Scryfall ids are real (Holy Hellfire on prod,
 * 2026-10-12). Graph edges, engines, pillars, scores, EDHREC %, prices and
 * ownership of non-deck cards are ILLUSTRATIVE: they are what the AN-1..AN-3
 * APIs should return in shape, not in value.
 *
 * In the app, the shell comes from templates/base.html and the deck header
 * comes from a new partial (UX_SPEC.md §3.0). This file only fakes them so
 * that the mockups open straight from disk.
 */
(function () {
    "use strict";

    var DECK = { name: "Holy Hellfire", colors: "WBR", commander: "Kaalia of the Vast", status: "physical",
        commanderUid: "ef099f25-2b0b-4028-8490-fbc859e35f6c", cards: 100, lands: 35, synced: "23h ago" };

    // ---- Shell (mirrors base.html markup so hud.css styles it for real) ----
    var NAV = [
        ["/decks", "Commander Decks", '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>', true],
        ["/inventory", "Inventory", '<rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line>'],
        ["/instances", "Instances", '<circle cx="9" cy="7" r="4"></circle><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>'],
        ["/tags", "Tag Management", '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path>']
    ];
    function shell() {
        var items = NAV.map(function (n) {
            return '<a href="#" class="nav-item' + (n[3] ? ' active" aria-current="page' : "") + '" title="' + n[1] + '">' +
                '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' + n[2] + "</svg>" +
                '<span class="nav-label">' + n[1] + "</span></a>";
        }).join("");
        var nav = document.createElement("nav");
        nav.className = "sidebar"; nav.setAttribute("aria-label", "Primary");
        nav.innerHTML = '<a href="#" class="brand"><svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none" aria-hidden="true"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg><span class="brand-label">Cmdr Lab</span></a>' +
            '<div class="nav-section-label">Navigate</div><div class="nav-items">' + items + "</div>" +
            '<div class="sidebar-footer"><span class="sidebar-footer-label">Version</span><span class="version-badge">v0.4.6</span></div>';
        document.body.classList.add("app-shell");
        document.body.insertBefore(nav, document.body.firstChild);
        var skip = document.createElement("a");
        skip.className = "skip-link"; skip.href = "#main"; skip.textContent = "Skip to content";
        document.body.insertBefore(skip, document.body.firstChild);
    }

    // ---- Deck header + deck tabs (UX_SPEC §3.0) ----------------------------
    var TABS = [
        ["overview", "Overview", "#", null, "1"],
        ["stats", "Stats", "stats.html", null, "2"],
        ["identity", "Identity", "pillars.html", "4", "3"],
        ["journal", "Journal", "journal.html", null, "4"],
        ["tune", "Tune", "tuner.html", "9", "5"]
    ];
    function deckhead(active, actionsHtml) {
        var art = HUD.scryfall(DECK.commanderUid, "art_crop");
        var tabs = TABS.map(function (t) {
            var on = t[0] === active;
            return '<a class="hud-tab' + (on ? " active" : "") + '" href="' + t[2] + '"' + (on ? ' aria-current="page"' : "") +
                ' data-key="' + t[4] + '">' + t[1] + (t[3] ? ' <span class="hud-tab-count">' + t[3] + "</span>" : "") + "</a>";
        }).join("");
        return '<div class="app-topbar"><ol class="hud-crumbs"><li><a href="#">Decks</a></li><li><a href="#">' + DECK.name +
            '</a></li><li aria-current="page">' + active.charAt(0).toUpperCase() + active.slice(1) + '</li></ol>' +
            '<div class="app-topbar-meta"><span class="hud-freshness">Archidekt <b>' + DECK.synced + '</b></span><span class="hud-freshness">Graph <b>2h ago</b></span></div></div>' +
            '<header class="hud-deckhead">' +
            '<div class="hud-deckhead-art"><img alt="" src="' + art.src + '" data-fallback="' + art.fallback + '"></div>' +
            '<div><span class="hud-label">Commander deck · ' + DECK.commander + "</span><h1>" + DECK.name + "</h1>" +
            '<div class="hud-deckhead-meta">' + HUD.pips(DECK.colors, "sm") + HUD.statusChip(DECK.status) +
            '<span class="hud-freshness">' + DECK.cards + " cards · " + DECK.lands + " lands</span></div></div>" +
            '<div class="hud-deckhead-actions">' + (actionsHtml || "") + "</div></header>" +
            '<nav class="hud-tabs hud-deck-tabs" aria-label="Deck sections">' + tabs + "</nav>";
    }

    // Tab hotkeys 1-5 (UX_SPEC §4): only when focus is not in a text field.
    document.addEventListener("keydown", function (e) {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        var t = e.target, tag = t && t.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
        var a = document.querySelector('.hud-deck-tabs [data-key="' + e.key + '"]');
        if (a && a.getAttribute("href") !== "#") location.href = a.getAttribute("href");
    });

    // ---- Fixture: deck cards (real) ----------------------------------------
    // [name, scryfall uid, cmc, colours, type]
    var CARDS = [
        ["Kaalia of the Vast", "ef099f25-2b0b-4028-8490-fbc859e35f6c", 4, "WBR", "Creature"],
        ["Akroma, Angel of Wrath", "6c8cc721-7a7e-4cda-817e-30e5e6cb4929", 8, "W", "Creature"],
        ["Angel of Despair", "6b229da4-609a-44e6-bf70-fb18bae25ed5", 7, "WB", "Creature"],
        ["Angel of Serenity", "60301fbc-1bd4-4c37-ad19-660d625a090a", 7, "W", "Creature"],
        ["Angelic Arbiter", "6f2833f5-283d-4e86-8caf-f4f97432622e", 7, "W", "Creature"],
        ["Animate Dead", "1489943b-c010-488e-9c9d-87f4af67a4e4", 2, "B", "Enchantment"],
        ["Archfiend of Depravity", "0ec85db7-c8ec-4730-b692-c140977436aa", 5, "B", "Creature"],
        ["Aurelia, the Warleader", "4ec18e35-05e4-4bfc-b32b-c3e71c95a71d", 6, "WR", "Creature"],
        ["Avacyn, Angel of Hope", "ddb92ef6-0ef8-4b1d-8a45-3064fea23926", 8, "W", "Creature"],
        ["Balefire Dragon", "468d5308-2a6c-440e-a8d0-1c5e084afb82", 7, "R", "Creature"],
        ["Baneslayer Angel", "c24806a1-c3b0-40ba-ac51-dbf0b29b35e7", 5, "W", "Creature"],
        ["Basandra, Battle Seraph", "6e5baa27-3f6b-49bb-9927-5afc21b9cc3f", 5, "WR", "Creature"],
        ["Blast-Furnace Hellkite", "dc81402a-5ce5-43b9-9e97-1ada9ee9c1e3", 9, "R", "Creature"],
        ["Bloodgift Demon", "6a2e1d4a-8df4-472f-a98f-d6b71c1dd1c4", 5, "B", "Creature"],
        ["Bloodthirster", "ccbb3a6d-7e75-46c7-a91f-db4cf8d003af", 6, "R", "Creature"],
        ["Burning-Rune Demon", "f358a52b-8044-404e-8d04-2ec5903386cc", 6, "B", "Creature"],
        ["Deflecting Swat", "45e70109-0352-45fc-a2fd-892ca65cac2f", 3, "R", "Instant"],
        ["Demonic Tutor", "a24b4cb6-cebb-428b-8654-74347a6a8d63", 2, "B", "Sorcery"],
        ["Dragon Tempest", "f1933d08-07fe-45ea-9b60-d9afb98d5753", 2, "R", "Enchantment"],
        ["Drana and Linvala", "29a5fe67-beaa-4ee3-b22c-0b8aed6f6f4d", 4, "WB", "Creature"],
        ["Firemane Commando", "4f8226bd-3e9b-474b-b2d5-95049762824b", 4, "W", "Creature"],
        ["Gisela, Blade of Goldnight", "4d039ff5-907f-4c27-8941-83e5e75bd19d", 7, "WR", "Creature"],
        ["Grand Abolisher", "b4c4f694-618a-43ed-8781-2a51a234ba8e", 2, "W", "Creature"],
        ["Hellkite Charger", "7d069748-1969-471e-bb13-b575eb5f2616", 6, "R", "Creature"],
        ["Hellkite Tyrant", "16937c7d-72c8-4640-8000-e4d2dc664a93", 6, "R", "Creature"],
        ["Helm of the Host", "e8d3865a-cea9-4985-84ed-89534f8e3f7c", 4, "", "Artifact"],
        ["Isshin, Two Heavens as One", "a062a004-984e-4b62-960c-af7288f7a3e9", 3, "WBR", "Creature"],
        ["Liesa, Forgotten Archangel", "532fca6b-f788-43f8-b29f-7273e7a48449", 5, "WB", "Creature"],
        ["Lightning Greaves", "331013f6-976a-4dac-9939-1006e474e108", 2, "", "Artifact"],
        ["Lord of the Void", "75b83fe5-fd00-4532-bc67-07836abfc99c", 7, "B", "Creature"],
        ["Lyra Dawnbringer", "93be6799-7b9d-44d4-84dc-2961692b5a85", 5, "W", "Creature"],
        ["Master of Cruelties", "6e4af66a-17fc-4579-bf30-d7f79df6354d", 5, "BR", "Creature"],
        ["Mother of Runes", "62d898e8-0b85-43eb-ae62-d33147135513", 1, "W", "Creature"],
        ["Necropotence", "c89c6895-b0f8-444a-9c89-c6b4fd027b3e", 3, "B", "Enchantment"],
        ["Ob Nixilis, Unshackled", "109b28dc-c0d1-4b88-b18e-074ac0464e03", 6, "B", "Creature"],
        ["Phyrexian Arena", "81c3099f-cea5-41d8-a753-096f0021b11b", 3, "B", "Enchantment"],
        ["Platinum Angel", "94765871-5785-429b-8980-e2b1667278a5", 7, "", "Creature"],
        ["Razaketh, the Foulblooded", "e14adff9-33cc-467e-b782-068854c5e7b7", 8, "B", "Creature"],
        ["Reya Dawnbringer", "44b45bdd-829a-4fc1-ad37-17c2bd57fac8", 9, "W", "Creature"],
        ["Rising of the Day", "7a06a653-eca1-4fee-add5-45d2594885c3", 3, "R", "Enchantment"],
        ["Rune-Scarred Demon", "700b44b8-6f47-4f38-8351-e4908cf23d3e", 7, "B", "Creature"],
        ["Sephara, Sky's Blade", "729ee848-6c27-4202-b2bc-605f45209468", 7, "W", "Creature"],
        ["Serra's Emissary", "b6534049-3045-4546-b1e8-e5b1b0df5f56", 7, "W", "Creature"],
        ["Steel Hellkite", "559d3dfb-b5c0-4f1b-880d-4182028fe97c", 6, "", "Creature"],
        ["Strionic Resonator", "88326f60-1c86-4aca-8e6a-b1882a048635", 2, "", "Artifact"],
        ["Swiftfoot Boots", "1ae84b5c-d3ad-48a3-9ccc-1dd76cee392c", 2, "", "Artifact"],
        ["Teferi's Protection", "b7210c3a-bbd3-4f3a-933b-a5618a4f7a52", 3, "W", "Instant"],
        ["Utvara Hellkite", "f2ad4005-a511-4151-90c9-ace197b86759", 8, "R", "Creature"],
        ["Vilis, Broker of Blood", "ecdf2bd9-87b9-470a-ad2e-0ebf98560f87", 8, "B", "Creature"],
        ["Wrath of God", "2d9b12cc-f616-4b52-91eb-a430e70f9251", 4, "W", "Sorcery"],
        ["Austere Command", "5215c18d-90cd-45df-ac14-99909c97c69e", 6, "W", "Sorcery"],
        ["Path to Exile", "f37b9440-64fb-4467-9840-92610f19ccd7", 1, "W", "Instant"],
        ["Terminate", "4bab7816-8fe2-47b2-a0ba-5cf2dbe5c838", 2, "BR", "Instant"],
        ["Mortify", "cbe6ef85-b8aa-46aa-8641-2cd502048cd7", 3, "WB", "Instant"],
        ["Crackling Doom", "2ab2b411-912f-4cb6-b4ca-f545ca8820d5", 3, "WBR", "Instant"],
        ["Anguished Unmaking", "90ced4fa-6509-4f7a-9da7-efc70de6f90c", 3, "WB", "Instant"]
    ];
    var BY = {};
    CARDS.forEach(function (c) { BY[c[0]] = { name: c[0], uid: c[1], cmc: c[2], colors: c[3], type: c[4] }; });

    // Produces / consumes tags (AN-1 tagging output, illustrative).
    var IO = {
        "Kaalia of the Vast": [["cheat into play", "attack trigger"], ["haste", "protection", "extra combat"]],
        "Isshin, Two Heavens as One": [["trigger doubling"], ["attack trigger"]],
        "Strionic Resonator": [["trigger copy"], ["attack trigger", "ETB"]],
        "Aurelia, the Warleader": [["extra combat"], ["cheat into play"]],
        "Hellkite Charger": [["extra combat"], ["mana", "cheat into play"]],
        "Helm of the Host": [["token copy", "haste"], ["legendary creature"]],
        "Angel of Despair": [["ETB removal"], ["cheat into play", "trigger copy"]],
        "Vilis, Broker of Blood": [["cards"], ["life loss"]],
        "Necropotence": [["life loss", "cards"], ["life"]],
        "Animate Dead": [["reanimate"], ["graveyard creature"]]
    };

    // Edges: [from, to, reasons[]]. Mana/lands are excluded by default (§3.1).
    var ADD = ["Akroma, Angel of Wrath", "Angel of Despair", "Angel of Serenity", "Angelic Arbiter", "Archfiend of Depravity",
        "Aurelia, the Warleader", "Avacyn, Angel of Hope", "Balefire Dragon", "Baneslayer Angel", "Basandra, Battle Seraph",
        "Blast-Furnace Hellkite", "Bloodgift Demon", "Bloodthirster", "Burning-Rune Demon", "Drana and Linvala",
        "Gisela, Blade of Goldnight", "Hellkite Charger", "Hellkite Tyrant", "Liesa, Forgotten Archangel", "Lord of the Void",
        "Lyra Dawnbringer", "Master of Cruelties", "Ob Nixilis, Unshackled", "Platinum Angel", "Razaketh, the Foulblooded",
        "Reya Dawnbringer", "Rune-Scarred Demon", "Sephara, Sky's Blade", "Serra's Emissary", "Steel Hellkite",
        "Utvara Hellkite", "Vilis, Broker of Blood", "Firemane Commando"];
    var ETB = ["Angel of Despair", "Angel of Serenity", "Archfiend of Depravity", "Rune-Scarred Demon", "Burning-Rune Demon",
        "Hellkite Tyrant", "Balefire Dragon", "Bloodgift Demon", "Razaketh, the Foulblooded", "Blast-Furnace Hellkite"];
    var EDGES = [];
    ADD.forEach(function (n) {
        if (n === "Firemane Commando") return;
        EDGES.push(["Kaalia of the Vast", n, ETB.indexOf(n) !== -1 ? ["cheat into play", "ETB value"] : ["cheat into play"]]);
    });
    [["Lightning Greaves", ["haste", "shroud"]], ["Swiftfoot Boots", ["haste", "hexproof"]], ["Mother of Runes", ["protection"]],
     ["Teferi's Protection", ["protection"]], ["Grand Abolisher", ["safe attack"]], ["Deflecting Swat", ["redirect removal"]],
     ["Rising of the Day", ["haste"]], ["Isshin, Two Heavens as One", ["doubles attack trigger"]],
     ["Strionic Resonator", ["copies attack trigger"]], ["Aurelia, the Warleader", ["extra combat", "second trigger"]],
     ["Hellkite Charger", ["extra combat", "second trigger"]], ["Gisela, Blade of Goldnight", ["double damage"]],
     ["Demonic Tutor", ["tutor"]], ["Drana and Linvala", ["stax own"]], ["Master of Cruelties", ["finisher"]]
    ].forEach(function (e) { EDGES.push([e[0], "Kaalia of the Vast", e[1]]); });
    [
        ["Isshin, Two Heavens as One", "Aurelia, the Warleader", ["doubles attack trigger"]],
        ["Isshin, Two Heavens as One", "Hellkite Charger", ["doubles attack trigger"]],
        ["Isshin, Two Heavens as One", "Utvara Hellkite", ["doubles attack trigger"]],
        ["Helm of the Host", "Aurelia, the Warleader", ["token copy", "haste"]],
        ["Helm of the Host", "Hellkite Charger", ["token copy", "haste"]],
        ["Strionic Resonator", "Angel of Despair", ["copies ETB"]],
        ["Strionic Resonator", "Rune-Scarred Demon", ["copies ETB", "tutor"]],
        ["Gisela, Blade of Goldnight", "Aurelia, the Warleader", ["double damage"]],
        ["Gisela, Blade of Goldnight", "Hellkite Charger", ["double damage"]],
        ["Dragon Tempest", "Hellkite Charger", ["haste"]], ["Dragon Tempest", "Balefire Dragon", ["haste", "dragon ETB damage"]],
        ["Dragon Tempest", "Hellkite Tyrant", ["haste", "dragon ETB damage"]], ["Dragon Tempest", "Utvara Hellkite", ["dragon ETB damage"]],
        ["Utvara Hellkite", "Dragon Tempest", ["dragon tokens"]], ["Dragon Tempest", "Blast-Furnace Hellkite", ["haste"]],
        ["Necropotence", "Vilis, Broker of Blood", ["life loss → cards"]], ["Phyrexian Arena", "Vilis, Broker of Blood", ["life loss → cards"]],
        ["Bloodgift Demon", "Vilis, Broker of Blood", ["life loss → cards"]], ["Necropotence", "Bloodgift Demon", ["life loss"]],
        ["Animate Dead", "Angel of Serenity", ["reanimate", "ETB value"]], ["Animate Dead", "Archfiend of Depravity", ["reanimate"]],
        ["Animate Dead", "Rune-Scarred Demon", ["reanimate", "ETB value"]], ["Liesa, Forgotten Archangel", "Razaketh, the Foulblooded", ["sac → return"]],
        ["Razaketh, the Foulblooded", "Liesa, Forgotten Archangel", ["sacrifice outlet"]], ["Reya Dawnbringer", "Angel of Despair", ["reanimate", "ETB value"]],
        ["Reya Dawnbringer", "Angel of Serenity", ["reanimate"]], ["Liesa, Forgotten Archangel", "Angel of Despair", ["death → hand"]],
        ["Avacyn, Angel of Hope", "Wrath of God", ["one-sided wipe"]], ["Avacyn, Angel of Hope", "Austere Command", ["one-sided wipe"]],
        ["Lyra Dawnbringer", "Baneslayer Angel", ["angel lord"]], ["Lyra Dawnbringer", "Serra's Emissary", ["angel lord"]],
        ["Lyra Dawnbringer", "Sephara, Sky's Blade", ["angel lord"]], ["Lyra Dawnbringer", "Akroma, Angel of Wrath", ["angel lord"]],
        ["Demonic Tutor", "Razaketh, the Foulblooded", ["tutor"]], ["Teferi's Protection", "Avacyn, Angel of Hope", ["protection"]]
    ].forEach(function (e) { EDGES.push(e); });

    // Engines (AN-1 detection, named by the LLM; membership is deterministic).
    var ENGINES = [
        { id: "cheat", name: "Kaalia cheat-in", flow: "attack → put A/D/D in → ETB value", kind: "loop", pillar: "Payoffs worth cheating",
          cards: ["Kaalia of the Vast", "Isshin, Two Heavens as One", "Strionic Resonator", "Angel of Despair", "Angel of Serenity", "Archfiend of Depravity",
                  "Rune-Scarred Demon", "Razaketh, the Foulblooded", "Hellkite Tyrant", "Balefire Dragon", "Bloodgift Demon", "Burning-Rune Demon"] },
        { id: "combat", name: "Swing twice", flow: "attack → extra combat → attack trigger again", kind: "loop", pillar: "Swing twice",
          cards: ["Kaalia of the Vast", "Aurelia, the Warleader", "Hellkite Charger", "Helm of the Host", "Isshin, Two Heavens as One", "Gisela, Blade of Goldnight"] },
        { id: "life", name: "Life into cards", flow: "pay life → Vilis draws → more payoffs", kind: "loop", pillar: "Life is a resource",
          cards: ["Vilis, Broker of Blood", "Necropotence", "Phyrexian Arena", "Bloodgift Demon"] },
        { id: "grave", name: "Graveyard insurance", flow: "payoff dies → returns → ETB again", kind: "loop", pillar: null,
          cards: ["Animate Dead", "Liesa, Forgotten Archangel", "Reya Dawnbringer", "Razaketh, the Foulblooded", "Angel of Serenity", "Angel of Despair"] },
        { id: "dragons", name: "Dragon storm", flow: "dragon enters → haste + damage", kind: "cluster", pillar: null,
          cards: ["Dragon Tempest", "Utvara Hellkite", "Hellkite Charger", "Balefire Dragon", "Hellkite Tyrant", "Blast-Furnace Hellkite"] },
        { id: "protect", name: "Keep her alive", flow: "haste · shroud · safe attack → Kaalia", kind: "cluster", pillar: "Keep her alive",
          cards: ["Kaalia of the Vast", "Lightning Greaves", "Swiftfoot Boots", "Mother of Runes", "Teferi's Protection", "Grand Abolisher", "Deflecting Swat", "Rising of the Day"] }
    ];

    window.MOCK = { DECK: DECK, CARDS: BY, CARD_LIST: CARDS, EDGES: EDGES, ENGINES: ENGINES, IO: IO, shell: shell, deckhead: deckhead,
        art: function (name, v) { var c = BY[name]; return c ? HUD.scryfall(c.uid, v || "art_crop") : { src: "", fallback: "" }; } };
})();
