"use strict";
// Offline editorial mockups. Observed deck examples, not registry defaults.
const query = new URLSearchParams(location.search);
const direction = ["a", "b", "c"].includes(query.get("direction"))
  ? query.get("direction")
  : "b";
const screens = [
  "cards",
  "stats",
  "identity",
  "journal",
  "tune",
  "ideas",
  "review",
  "simulate",
  "settings",
];
const page =
  document.body.dataset.screen === "overview"
    ? "overview"
    : screens.includes(query.get("page"))
      ? query.get("page")
      : "cards";
const names = {
  overview: "Overview",
  cards: "Cards",
  stats: "Stats",
  identity: "Identity",
  journal: "Journal",
  tune: "Tune",
  ideas: "Ideas",
  review: "Deep Review",
  simulate: "Simulate",
  settings: "Settings",
};
const url = (key) =>
  key === "overview"
    ? "overview.html?direction=b"
    : `workspaces.html?page=${key}`;
const link = (key, text, cls = "button") =>
  `<a class="${cls}" href="${url(key)}">${text || names[key]} →</a>`;
const chip = (text, type = "") => `<span class="chip ${type}">${text}</span>`;
const section = (num, title, tail = "") =>
  `<div class="subhead"><h2><span class="num">${num}</span>${title}</h2>${tail}</div>`;
const plan =
  "Cheat terrifying Angels, Demons, and Dragons directly into combat using Kaalia to overwhelm opponents with lethal aerial assaults.";
const pillars = [
  "No Mercy",
  "Dragon, Angel, Demon",
  "Haste and Extra Combats",
  "Commander Protection",
  "Trigger Doubling and Cheating",
];
const counts = [2, 35, 8, 4, 4];
const hero = (compact) =>
  `<section class="hero" aria-label="Deck identity"><div class="hero-copy"><div class="eyebrow">Kaalia of the Vast <span aria-label="White black red">· W / B / R</span></div><h1>Holy Hellfire</h1><div class="row">${chip("100 cards")}${chip("Physical")}${chip("Game Plan · saved by you", "data")}</div><p class="plan ${compact ? "compact-plan" : ""}">${plan}</p><div class="small">Saved Game Plan, not inferred from role counts.</div><div class="actions">${link("identity", "Edit identity")}${link("journal", "Check in")}</div></div><figure class="commander"><img src="commander.jpg" alt="Kaalia of the Vast illustration, cropped from the card" width="260" height="190"><figcaption>Kaalia of the Vast<br><span class="small">Commander · mana value 4</span></figcaption></figure></section>`;
const pillarStrip = () =>
  `<div class="pillars" aria-label="Saved pillars in your order">${pillars.map((p, i) => `<article class="pillar"><span class="eyebrow">Pillar ${i + 1}</span><h3>${p}</h3><span class="meta">${counts[i]} cards · pinned</span></article>`).join("")}</div><div class="pets"><span class="meta">Pets · protected</span><strong>♥ Dragon Tempest</strong><strong>♥ Lyra Dawnbringer</strong>${link("journal", "Edit pets", "small")}</div>`;
const composition = () =>
  `<div>${[
    ["Lands", 35, 38],
    ["Ramp", 6, 10],
    ["Card advantage", 5, 12],
    ["Targeted disruption", 9, 12],
  ]
    .map(
      ([n, a, b]) =>
        `<div class="metric"><span>${n}</span><meter min="0" max="${b}" value="${a}" aria-label="${n}: ${a} of target ${b}"></meter><b>${a}/${b}</b></div>`,
    )
    .join(
      "",
    )}</div><p class="small">Compared with this deck’s resolved preset. Targets are preferences, not rules; categories can overlap.</p>`;
const curve = () =>
  `<div class="curve" role="img" aria-label="Nonland mana curve: 3 at zero, 12 at one, 10 at two, 6 at three, 7 at four, 9 at five, zero at six, 18 at seven or higher.">${[3, 12, 10, 6, 7, 9, 0, 18].map((n, i) => `<div class="bar" style="height:${n * 5}px"><span>${i === 7 ? "7+" : i}</span></div>`).join("")}</div><p class="small">Average mana value <strong>4.69</strong> · nonland. Kaalia’s plan intentionally includes expensive threats.</p>`;
const shape = () =>
  `${section("02", "The shape", link("stats", "Inspect shape", "small"))}<div class="grid2"><section class="panel"><div class="statrow"><div class="stat"><span>Cards</span><b>100</b></div><div class="stat"><span>Lands</span><b>35</b></div><div class="stat"><span>Ramp</span><b>6</b></div><div class="stat"><span>Avg MV</span><b>4.69</b></div></div><h3>Composition vs your preset</h3><p class="small">Observed snapshot · Command Zone 2025</p>${composition()}</section><section class="panel"><h3>Mana curve · nonland</h3>${curve()}<details><summary>Classification, not a health grade</summary><div>53 pillar · 47 glue · 0 generic. 100% classified on-plan does not mean the deck has no problems. ${link("identity", "See saved plan", "small")}</div></details></section></div>`;
const concerns = () =>
  `${section("03", "Needs attention", chip("Proposed grouping"))}<div class="panel"><article class="concern"><span class="n">01</span><div><h3>Early resources trail your preset</h3><p class="small">Ramp 6/10 · card advantage 5/12. Check the tradeoff against Kaalia’s cheat-into-combat plan.</p><span class="meta">Source: saved shape snapshot · not a verdict</span></div>${link("stats", "See evidence")}</article><article class="concern"><span class="n">02</span><div><h3>Test whether Kaalia arrives early enough</h3><p class="small">Saved sim: first cast T5.73, versus static T4; only 20 games. Hypothesis to inspect, not a proven fix.</p><span class="meta">Source: saved simulation · Specific 3</span></div>${link("simulate", "Inspect run")}</article><article class="concern"><span class="n">03</span><div><h3>Deep Review needs a refresh</h3><p class="small">The registry changed since the saved report. Its derived labels do not replace your saved identity.</p><span class="meta">Source: stale report · excluded from current diagnosis</span></div>${link("review", "Open last report")}</article></div><p class="footnote">Mockup uses observed saved values. Production must check each source’s freshness before composing this section.</p>`;
const utility = () =>
  `<footer class="utility"><span class="small">The deck first. The wiring down here.</span><div class="actions">${link("settings", "Deck settings", "small")}<a href="evidence.html">Current-state evidence</a></div></footer>`;
const nav = () =>
  `<nav class="section-nav" aria-label="Proposed deck sections">${["overview", ...screens].map((k) => `<a href="${url(k)}" ${page === k ? 'aria-current="page"' : ""}>${names[k]}</a>`).join("")}</nav><div class="mobile-nav"><label for="sections">Deck section</label><select id="sections">${["overview", ...screens].map((k) => `<option value="${url(k)}" ${page === k ? "selected" : ""}>${names[k]}</option>`).join("")}</select></div>`;
const heading = (title) =>
  `<div class="between"><div><p class="eyebrow">Holy Hellfire · Kaalia of the Vast</p><h1>${title}</h1></div>${chip("Offline proposal")}</div>`;
const cards = () =>
  `<div class="search"><label for="card-search">Find a card</label><input id="card-search" type="search" placeholder="Filter the example cards…"><a class="button" href="workspaces.html?page=cards&view=${query.get("view") === "list" ? "visual" : "list"}">${query.get("view") === "list" ? "Art view" : "List view"}</a></div><p class="small">Proposed Cards destination · existing visual/list choice. This preview contains six named examples, not the entire deck.</p><div class="note">Auto Tag and tag filtering belong alongside the cards. Tag choices must load from your editable registry; none are seeded here.</div>${query.get("view") === "list" ? `<table class="table"><thead><tr><th>Card</th><th>Context</th></tr></thead><tbody>${["Kaalia of the Vast", "Dragon Tempest", "Lyra Dawnbringer", "Lightning Greaves", "Demonic Tutor", "Sol Ring"].map((n) => `<tr data-example-card><td>${n}</td><td>${n === "Kaalia of the Vast" ? "Commander" : n === "Dragon Tempest" || n === "Lyra Dawnbringer" ? "Pet · protected" : "In deck"}</td></tr>`).join("")}</tbody></table>` : `<div class="artgrid">${["Kaalia of the Vast", "Dragon Tempest", "Lyra Dawnbringer", "Lightning Greaves", "Demonic Tutor", "Sol Ring"].map((n, i) => `<article class="card" data-example-card><img src="${["commander", "dragon-tempest", "lyra", "greaves", "tutor", "sol-ring"][i]}.jpg" alt="${n} illustration" class="card-art" loading="lazy"><h3>${n}</h3>${chip(i === 0 ? "Commander" : i === 1 || i === 2 ? "Pet · protected" : "In deck", i === 1 || i === 2 ? "warn" : "")}</article>`).join("")}</div>`}<p id="filter-status" role="status" class="footnote"></p><div class="continue">${link("settings", "Physical cards & categories")}</div>`;
function overview() {
  let content = "";
  if (direction === "a")
    content = `${hero(true)}${section("01", "What makes it yours")}${pillarStrip()}${shape()}${concerns()}${section("04", "Cards remain below the summary", link("cards", "Jump to Cards", "small"))}${cards()}<details><summary>Deck utilities · moved below the deck</summary><div>External source links, directional Update/Push, lifecycle and folder. ${link("settings", "See utility layout")}</div></details>`;
  if (direction === "b")
    content = `${hero(false)}${section("01", "Built around", link("identity", "Edit pillars", "small"))}${pillarStrip()}${shape()}${concerns()}<div class="continue">${link("cards", "Browse 100 cards", "button primary")}${link("tune", "Continue tuning")}${link("simulate", "Test the deck")}</div><details><summary>Primer & personality · supporting reads</summary><div>No saved primer in the observed snapshot. Ability scores describe the deck’s personality, not whether its plan works. These remain secondary to authored identity. ${link("journal", "Edit deck context", "small")}</div></details>`;
  if (direction === "c")
    content = `${hero(false)}${section("01", "Your non-negotiables")}${pillarStrip()}${section("02", "Choose the question")}<nav class="taskbar" aria-label="Decision desk focus">${["shape", "plan", "test"].map((k, i) => `<a class="button" href="?direction=c&focus=${k}" ${(query.get("focus") || "shape") === k ? 'aria-current="page"' : ""}>${["Inspect the shape", "Refine the plan", "Test a claim"][i]}</a>`).join("")}</nav><div class="decision"><aside class="panel"><p class="eyebrow">Decision context</p><h2>Protect the plan.<br>Question the evidence.</h2><p class="small" style="margin-top:var(--s4)">Pets stay protected. Suggestions never update the deck until you explicitly act.</p><div class="note">This is optional workspace state, not a replacement for the deck’s identity.</div></aside><section class="panel">${query.get("focus") === "plan" ? `<h2>Does each pillar still describe your deck?</h2><p class="explanation">Start from the saved words, then inspect card membership. Do not replace your pillars with model categories.</p>${link("identity", "Refine saved identity", "button primary")}` : query.get("focus") === "test" ? `<h2>Does Kaalia attack soon enough?</h2><p class="explanation">The saved run’s commander first cast is T5.73. Start with the test notes, sample and opponent setup; don’t turn a small sample into a verdict.</p>${link("simulate", "Inspect test evidence", "button primary")}` : `<h2>What does your selected preset reveal?</h2><p class="small">Command Zone 2025 · observed resolved preset</p>${composition()}<div class="continue">${link("stats", "Inspect full shape", "button primary")}${link("tune", "Review fixes")}</div>`}</section></div>`;
  const descriptions = {
    a: "A · Safe reorder. Same long page; identity finally leads. Admin goes to the bottom.",
    b: "B · Recommended dossier. Identity → shape → problems. Cards and settings are destinations.",
    c: "C · Bold decision desk. Identity stays anchored; choose one question and focus its evidence.",
  };
  return `<div class="topline"><a href="../../DESIGN_DECK_VIEW.md">← Ranked audit</a><nav class="directions" aria-label="Overview direction">${["a", "b", "c"].map((k) => `<a class="button" href="?direction=${k}" ${direction === k ? 'aria-current="page"' : ""}>${k.toUpperCase()} · ${k === "a" ? "Reorder" : k === "b" ? "Dossier" : "Desk"}</a>`).join("")}</nav></div><p class="explanation">${descriptions[direction]}</p>${nav()}${content}${utility()}`;
}
function workspace() {
  let body = "";
  if (page === "cards") body = `${heading("Cards")}${cards()}`;
  if (page === "stats")
    body = `${heading("Inspect the shape")}<p class="explanation">Proposed order: shape, plan fit, interactions, comparisons. Graph detail is available, not compulsory.</p>${shape()}${section("03", "Plan classification")}<section class="panel"><h2>53 pillar / 47 glue / 0 generic</h2><p class="small">Coverage, not quality. Low EDHREC inclusion is not a reason to cut a card.</p></section><details><summary>Interactions · inspect one engine</summary><div><h3>Attack cheat · 19 cards</h3><p>Attack → ETB → attack. The existing text inspector and graph remain deeper tools. Reveal one engine rather than leading with all 190 edges.</p></div></details><details><summary>Comparison models & combo evidence</summary><div>Keep model coverage, unknown cards, ownership and pet caveats beside the numbers. Do not rank originality as deck quality.</div></details>`;
  if (page === "identity")
    body = `${heading("Identity")}${section("01", "Saved Game Plan")}<section class="panel"><p class="plan">${plan}</p><p class="footnote">Pinned · edited by you. Copied to Overview as a read-only projection.</p><details><summary>Edit controls · reveal deliberately</summary><div>Game Plan and pillar editor remain here. Unpin and Reject are scoped editing actions, not permanent peers of the reading view.</div></details></section>${section("02", "Pillars · your order")}${pillarStrip()}<details><summary>Why do these cards fit?</summary><div>Show computed membership with card-level evidence, separately from the pillar wording you saved. Membership is not a guaranteed statement about a card’s mechanics.</div></details>`;
  if (page === "journal")
    body = `${heading("Journal")}<p class="explanation">A writing surface, not another dashboard. Identity inputs first; tuning constraints later. Fields in this mockup are read-only examples.</p><section class="panel"><h2>01 · What you want this deck to be</h2><div class="field"><label for="vision">Vision · saved example</label><textarea id="vision" readonly>Kaalia cheats a fatty into play each turn, the table should have to sweat every attack.</textarea></div><p class="meta">Your selected Deck Tags</p><p class="small">No saved selections in the observed snapshot. Search your editable registry; no generated options applied.</p><div class="pets"><span class="meta">Pets</span><strong>Dragon Tempest · Lyra Dawnbringer</strong></div><div class="continue">${link("identity", "Edit saved pillars")}</div></section><details><summary>02 · Win approach</summary><div>Primary / secondary / excluded win conditions remain explicit preferences; derived coverage never changes them.</div></details><details><summary>03 · Power, bracket & budget constraints</summary><div>Keep separate labels for target and estimate. Hard and soft caps have visible scope and inheritance.</div></details><details><summary>Check in · not Deep Review</summary><div>The existing “Is this still true?” flow ends at Tune. Rename its entry point without changing its save behavior.</div></details>`;
  if (page === "tune")
    body = `${heading("Tune")}<div class="note">Proposed mobile queue pattern · staging only. This offline demo has no production connection.</div><div class="row">${chip("Scope: owned + buy")}${chip("Hard $40 / soft $15")}${link("journal", "Edit constraints", "small")}</div>${section("01", "Shape fixes first")}<section class="panel"><h3>Below your selected preset</h3>${composition()}</section>${section("02", "One decision at a time")}<section class="panel"><span class="eyebrow">Observed suggestion · not design endorsement</span><div class="swap"><div><span class="meta">Cut</span><h3>Merciless Eviction</h3></div><span class="arrow" aria-hidden="true">→</span><div><span class="meta">Buy · $3.46 snapshot</span><h3>Archangel Avacyn</h3></div></div><p>Suggestion’s rationale: Kaalia can cheat Avacyn into combat and protect your board, rather than exile your own threats.</p><details><summary>Compare alternatives & evidence</summary><div>Owned alternative: Everything Comes to Dust, currently in another physical deck. Any physical move has consequences for that deck. Show score, source, price age and pet protection here, not above the decision.</div></details><div class="continue"><button id="stage-swap" class="primary">Stage this example swap</button><button id="clear-swap">Clear example</button></div></section><details><summary>Weak performers · computed, not simulated</summary><div>Full list remains available, without forcing it between a staged change and the change list. Pets are protected regardless of score.</div></details><div class="staging" role="status"><div class="between"><strong id="staged-count">Change list · 0</strong><a href="#change-list">Review staged changes ↓</a></div><span class="small">Demo only · actual deck unchanged</span></div><section id="change-list" class="panel" style="margin-top:var(--s6)"><h2>Change list</h2><p id="staged-detail">Nothing staged. Try the example button above.</p><p class="small">Copy/change-source flow stays explicit. No imaginary Apply API.</p></section>`;
  if (page === "ideas")
    body = `${heading("Ideas")}${section("01", "Saved experiments")}<section class="panel"><p class="eyebrow">Proposal only · not applied</p><h2>angels</h2><div class="row">${chip("88 kept / 12 buy")}${chip("$40.25 snapshot")}${chip("Commander kept")}</div><p class="explanation">Keep the proposed Game Plan first, then show the delta rather than 88 unchanged cards.</p><details open><summary>Proposed changes first</summary><div>Add examples: Arena of Glory, Giada, Font of Hope. Cut example: Temple of the False God. Full theory remains separate; these examples are not an implementation-ready swap bundle.</div></details><details><summary>Full retained decklist</summary><div>All 100 proposed cards remain available here. Unchanged basics do not hide the change proposal.</div></details><details><summary>Delete experiment · secondary action</summary><div>Production must show a named confirmation and return focus. No deletion is connected in this prototype.</div></details></section><details><summary>Explore a new idea</summary><div>Free-text prompt, not a seeded theme selector. Saved deck identity supplies context, not a set of mandatory values.</div></details>`;
  if (page === "review")
    body = `${heading("Deep Review")}<div class="note tone"><strong>Last saved report · stale</strong><br>Registry changed since this review. Do not present its inferred labels as current diagnosis.</div><section class="panel"><h2>No current summary to trust</h2><p class="explanation">Keep the old report available. Rebuilding is an explicit job, not an automatic consequence of opening the page.</p><p class="small">Proposed current-report order: takeaways → composition/tempo → focus/cards → model/method details.</p></section><details><summary>Open last saved composition evidence</summary><div>${composition()}<p>This is historical report context, not a newly verified deck diagnosis.</p></div></details><details><summary>Model role coverage · not your selected Style</summary><div>The captured stale report called this Group hug. That is a model read, not the identity you wrote. Put its version and evidence beside it; do not save it as a preference.</div></details><details><summary>Strong cards / stragglers / methods</summary><div>Keep card evidence, protected pets, coverage caveats and report provenance accessible without leading with a wall of chips.</div></details>`;
  if (page === "simulate")
    body = `${heading("Simulate")}<details><summary>Run new simulation · configuration stays collapsed</summary><div>This mockup does not replace SM-4R’s parameter controls. Standard/random/specific, job progress and opponent correctness remain governed by that spec and review.</div></details>${section("01", "The question & the limits")}<section class="panel"><h2>Does Kaalia get to attack?</h2><p class="small">Paraphrase of saved tester notes, not a new authored setting.</p><div class="row">${chip("Saved run · 20 games")}${chip("Specific 3")}${chip("Deck up to date", "data")}${chip("Oracle unavailable · fallback", "warn")}</div><div class="statrow" style="margin-top:var(--s6)"><div class="stat"><span>Wins</span><b>5/20</b></div><div class="stat"><span>Win rate</span><b>25%</b></div><div class="stat"><span>First cast</span><b>T5.73</b></div><div class="stat"><span>Mana use</span><b>57%</b></div></div><p class="small">Small sample; no real-world win-rate guarantee. Exact uncertainty presentation is an SM-4R follow-up, not a fabricated confidence interval here.</p></section>${section("02", "Biggest paper-vs-sim differences")}<section class="panel"><h3>Commander arrives later than static T4</h3><p class="explanation">Mana used T2–T6 is 57% versus static 74%. Read the setup and individual games before deciding the cause.</p><p class="small">Source: saved simulation snapshot; fallback interpretation, not Oracle analysis.</p></section><details><summary>Curve, card performance & per-game log</summary><div>Keep the existing section destinations. The per-game opponents remain available; do not duplicate twenty rows on Overview.</div></details>`;
  if (page === "settings")
    body = `${heading("Deck settings")}<p class="explanation">Proposed home for deck wiring. Account credentials stay in global Settings. Physical reconciliation remains a separate card task, not mixed into identity.</p><section class="panel"><h2>External source & sync</h2><p class="explanation">Archidekt #417707 · Moxfield not linked. Preserve existing directional Update / Push confirmations and provider configuration checks.</p><details><summary>Update from Archidekt</summary><div>Pull external edits into Commander Lab. A production action requires the existing preview/confirmation; nothing runs in this mockup.</div></details><details><summary>Push to Archidekt</summary><div>Send local changes outward. The observed server does not have Archidekt push configured; show that reason rather than a working-looking action.</div></details><details><summary>Source links & last sync</summary><div>EDHREC, Archidekt, Moxfield, Commander Salt and source freshness live here. Their availability is data-driven, not hardcoded as required integrations.</div></details></section><div class="grid2" style="margin-top:var(--s6)"><section class="panel"><h2>Lifecycle & folder</h2><p class="explanation">Physical · currently controlled by the external folder. Display the actual source and allowed transitions; no new folder or lifecycle vocabulary proposed.</p></section><section class="panel"><h2>Comparison baseline</h2><p class="explanation">Using Command Zone 2025 · Auto in the observed snapshot. One deck-scoped editor, with targets and resolution reason.</p><details><summary>Preset editor location</summary><div>Search actual editable presets. Pin/Auto remains explicit. Do not seed the observed example as a universal default.</div></details></section></div><details><summary>Physical cards & categories</summary><div>Shared deck navigation, searchable copies, category source, overrides on selection. Removing an override and unbinding a copy are different actions.</div></details><details><summary>Danger zone · decommission / reconstruct</summary><div>Move these out of the hero. Keep named confirmation, affected copies and scope visible. No destructive actions are wired in this prototype.</div></details>`;
  return `<p class="eyebrow">Holy Hellfire / deck workspace</p>${nav()}${body}${utility()}`;
}
document.getElementById("app").innerHTML =
  page === "overview" ? overview() : workspace();
document.title = `${page === "overview" ? `Direction ${direction.toUpperCase()}` : names[page]} · deck-view proposal`;
document.getElementById("sections")?.addEventListener("change", (ev) => {
  location.href = ev.target.value;
});
const input = document.getElementById("card-search");
input?.addEventListener("input", () => {
  const value = input.value.toLocaleLowerCase();
  let n = 0;
  document.querySelectorAll("[data-example-card]").forEach((el) => {
    el.hidden = !el.textContent.toLocaleLowerCase().includes(value);
    if (!el.hidden) n++;
  });
  document.getElementById("filter-status").textContent =
    `${n} of 6 example cards shown`;
});
document.getElementById("stage-swap")?.addEventListener("click", () => {
  document.getElementById("staged-count").textContent =
    "Change list · 1 · $3.46 snapshot";
  document.getElementById("staged-detail").textContent =
    "DEMO: Merciless Eviction → Archangel Avacyn. Nothing changed outside this page.";
});
document.getElementById("clear-swap")?.addEventListener("click", () => {
  document.getElementById("staged-count").textContent = "Change list · 0";
  document.getElementById("staged-detail").textContent =
    "Nothing staged. Actual deck unchanged.";
});
