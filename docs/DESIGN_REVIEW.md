# Commander Lab: design review and redesign directions

Status: proposal. No app code, templates or CSS were changed. Card: `t_aa7b5659`.
Base: `ui-hud-overhaul` @ `5b1260c` (HUD-1…4).

How this was reviewed: I ran the HUD branch locally (`uvicorn app:app`)
against a read-only snapshot of production Redis on Tower: 21,370 keys,
57 decks, 11,216 card instances. I looked at every page at 1280 and 390px,
read the templates and kit, and checked the token contrast ratios. Prod
itself runs `main` (v0.4.4), and it shows the same data problems.

Screenshots of the current state are in `docs/design/current/`. Mockups are
in `docs/design/*.html`, rendered to `docs/design/shots/`. Open the HTML
files directly in a browser. They only depend on Google Fonts and Scryfall
art.

---

## TL;DR

- The HUD kit is good. The app it dresses is mostly empty. With real data,
  three of the five nav destinations render no rows: **Inventory, Deck view
  and the Tags card table**. All three read `/api/inventory`, which scans
  `card:*` keys, but those keys no longer exist. The data has moved to
  `instance:*` and `card_meta:*`. The populated HUD-4 deck screenshots must
  have been rendered against older or fixture data.
- **Instances** works, and it is the only page that does. It renders all
  11,216 rows at once (448,890 DOM nodes, about 814,000px tall), and every
  instance appears twice. Two imports, on 09-08 and 09-25, each wrote 5,608
  rows. There are zero `in_collection` instances, so "free agents" exist in
  the model and not in the data.
- The product's stated goal is to give each EDHREC-built deck a sharper
  identity. **No screen does this.** `Review` is `console.log("Review deck
  triggered")`. The primer is boilerplate: it recommends Farseek, a green
  card, to a Mardu deck. The Salvager, Intake and Review APIs exist with no
  UI.
- Recommendation: **Direction C, "Identity Lab"**. Each deck gets a written
  thesis and 2–4 pillars, every card is scored against them, and swaps come
  from your own box first. Get there in phases. Phase 0 is the data fixes.
  Nothing visual matters until the pages have rows.

---

## 1. Critique of the current state

### What works (keep it)

- **The token system.** `hud.css` has one warm accent (`--signal`), one cool
  data colour, mana colours that only ever mean mana, a 4px grid, mono for
  numbers and Garamond for card names. It is disciplined, and the
  `/styleguide` page (`docs/screenshots/after-hud3/styleguide.png`) is a
  real asset. Every direction below is built from these tokens.
- **The deck view structure from HUD-4** (`after-hud4/deck_holy_hellfire.jpg`):
  a stats-first hero, framed commander art, curve and type panels, and a
  sortable list/visual decklist. With data behind it, this is the best page
  in the app.
- **The kit macros** (`partials/hud.html`) and the `hud.js` builders give
  server-rendered and client-rendered pages one vocabulary.
- **Keyboard and a11y groundwork.** A skip link, `aria-current` on nav, and
  a solid 1px focus ring.

### What's broken (P0: data, not design)

| Page | What you see on real data | Why |
|---|---|---|
| `/inventory` | Filters, headers, 0 rows (`current/inventory.jpg`) | `/api/inventory` scans `card:*`, but 0 such keys exist |
| `/deck/{name}` | Every deck shows 0 cards, empty curve/types/colour panels, "No cards in this deck" (`current/deck.jpg`) | Builds its list from `/api/inventory?deck=` |
| `/tags` | Tag cloud is fine, card table is empty (`current/tags.jpg`) | Same call |
| `/deck/{name}/manage` | "Resync pending" lists **all ~90 cards** as missing physically, then says "No cards bound to this deck yet" (`current/deck_manage.jpg`) | 200 instances carry `deck_id 417707` (Holy Hellfire), but the manage aggregate finds none |
| `/instances` | 11,216 rows, each card twice, 448k DOM nodes (`current/instances.jpg`) | Duplicate import, no virtualization, no grouping |
| Deck → Review | Nothing happens | `reviewDeck()` is a `console.log` stub |

These belong to coder/backend, not design. I've listed them because they
decide which design is even viewable.

### What's confusing

- **Three status vocabularies.** The card brief and `AGENTS.md` say
  *Have / Virtual / Pending / Possible*. `registry.VALID_STATUSES` and the
  deck modal say *physical / digital / retired / testing*. The Inventory
  filter says *Have / Possible / Pending*. The deck chips say `PHYSICAL`.
  Pick one set and use it everywhere. I'd go with Have / Testing / Possible /
  Retired.
- **"Inventory" vs "Instances".** These are two nav items for one concept,
  "my cards", at two granularities. Neither one answers the real question:
  where is this card, and is it free?
- **Every deck card shows a red `NO REPO` badge** (`current/decks.jpg`). It's
  a git-status error ("Path not specified") about a feature you don't use.
  The badge is red 56 times, so the red now means nothing.
- **Tags: right-click to toggle Core.** This is invisible, can't be done
  from the keyboard and doesn't work on touch. The cloud also mixes
  archetypes (Aristocrats) with to-do states (Need to Buy, Maybe Not) and
  near-duplicates (Wincon / Win-Con / Win Condition, Clone / Clones,
  Equipment Matter / Matters).
- **A `test-deck` with no source** sits in the production registry.

### What's redundant or misplaced

- **Home** is a welcome splash with two links and no data
  (`current/home.jpg`). Andrew is the only user, and he doesn't need to be
  welcomed.
- **Linked Accounts** take the first 320px of `/decks`, above the decks
  (`current/decks.jpg`). You set them up once. They belong in Settings, with
  sync freshness shown in the top bar.
- **Deck identity is spread over three surfaces:** the deck card modal on
  `/decks` (status, links, sync, "Trade"), the deck page, and the manage
  page.
- **The deck page repeats facts.** "Imported" appears three times (crumb,
  eyebrow, Profile) and Archidekt three times.
- **Styleguide and Changelog get top-level nav slots** next to the actual
  work.

### Visual system drift

The HUD covers the shell and the deck view. Everything else is still legacy:

- `decks.html` (65 KB) has its own card grid, a serif "Linked Accounts" H2,
  round avatars and solid red delete buttons.
- `deck_manage.html` uses a navy panel with a blue border and blue
  subheads. Blue is not a HUD token.
- `tags.html` uses blue pills.
- Deck cards on `/decks` put a 55%-width round portrait next to a narrow
  text column. Partner commanders squeeze the title into a one-character
  column (`current/decks.jpg`, row 5), and names break mid-word
  ("Metalbende/r"). At 390px the grid becomes about 11,000px of scrolling
  (`current/decks_phone.jpg`).

### Accessibility (measured)

| Pair | Ratio | Verdict |
|---|---|---|
| `--text-3` `#76828e` on `--surface-1` | 4.47:1 | Fails AA for 11–13px micro-labels |
| `--text-3` on `--surface-2` (hover rows, panel heads) | 4.14:1 | Fails |
| `--danger` on `--danger-dim` (danger chip) | 4.45:1 | Fails |
| `--text-4` on `--surface-1` | 2.49:1 | Only OK for disabled or decorative use. Audit the axis-tick usage |

Fix: `--text-3 → #8a96a2` (5.8:1 on surface-1, 5.4:1 on surface-2) and
`--danger → #ee6a61`. The mockups already use both.

---

## 2. Jobs Andrew does, and whether the IA serves them

| # | Job | Frequency | Served today? |
|---|---|---|---|
| J1 | **Sharpen a deck**: find what makes it *this* deck, cut the generic EDHREC filler, add cards that serve the plan | The stated goal | **No.** Review is a stub, the primer is boilerplate, and EDHREC is only an outbound link |
| J2 | **Find a card**: where is my Dockside, is it sleeved, which deck can give it up | Often | **No.** Inventory is empty, and Instances is an unfiltered 11k-row dump with dupes |
| J3 | **Reconcile drift**: Archidekt changed, so what has to move physically | After every Archidekt edit | **Partly.** The manage page has the logic, but it shows the whole deck as "missing" and is buried two clicks deep |
| J4 | **Triage the roster**: 17 decks in Testing, so what to promote, retire or build next | Weekly | **No.** A 3-column art grid with no sort, no filters beyond "show all", and every card looks the same |
| J5 | **Can I build this?** Paste a list and check it against the box (Salvager) | Occasionally | **No UI.** The API exists |
| J6 | **Tag hygiene**: keep the Lab tag vocabulary clean, since it drives categories | Occasionally | **Weak.** View-only cloud, hidden right-click, no merge or rename |

The current IA is organized around **data stores** (decks, inventory,
instances, tags). The jobs are organized around **a deck and the cards that
could go in it**. Every job except J6 starts with "this deck", or ends with
it.

---

## 3. Redesign directions

All three share the following:

- **Shell:** a 64px icon rail (replacing the 232px sidebar), a top command
  bar (`/` or `⌘K`: jump to deck, card or tag), and sync freshness in the
  bar. On phones, the rail becomes a bottom bar.
- **Tokens:** HUD tokens, plus `--fs-display` 72px, `--dur-slow` 320ms and
  `--stagger` 40ms. Motion is staggered tile entrance, rows sliding in from
  the left, and a tilt-on-press tile. All of it stops under
  `prefers-reduced-motion`. Only `transform` and `opacity` are animated. See
  `docs/design/mockup.css`.
- **Merges:** Home is removed. Inventory and Instances become
  **Collection**. Linked Accounts, Styleguide and Changelog move to
  **Settings**. Manage becomes a tab on the deck.

### Direction A: "Finish the HUD" (conservative)

> Same product, one design language, every page actually working.

```
Rail:  Decks · Collection · Tags · ─ · Settings
/decks              dense roster table + "exceptions" strip (resync, short, stale testing)
/deck/:id           HUD-4 deck view, tabs: Overview · List · Physical (was /manage) · Primer
/collection         one row per card (grouped instances), virtualized, facets: owned/free/in-deck/wishlist
/tags               table: tag · kind (archetype|role|workflow) · cards · decks · core ★ · merge
/settings           linked accounts, sync, styleguide, changelog
```

Key screen: `docs/design/a-roster.html` → `shots/a-roster.jpg`. It shows 56
decks in about 1.5 screens instead of about 11,000px, a status/colour
segmented filter, j/k navigation, and owned% per deck. The top strip shows
only exceptions, never totals.

Tradeoffs:
- ✅ Lowest risk. Mostly template migration onto the existing kit, and every
  card is small.
- ✅ Fixes every P0/P1 problem in §1.
- ❌ Doesn't touch J1. You get a tidier way to look at EDHREC lists.
- ❌ Still five places to go. Sharpening a deck still means bouncing
  between Deck, Collection and EDHREC.

### Direction B: "Workbench" (bold)

> One surface: the roster, the deck, and everything that could go in it.

```
┌rail┐┌ roster ──────┐┌ deck ───────────────────────────┐┌ context drawer ─────────┐
│ WB ││ Have · 39  ▾ ││ [art hero] Holy Hellfire   WBR  ││ Free agents that fit    │
│ CO ││ ▸ Holy Hellf●││ Have · 3 resync   [Review Δ]    ││ EDHREC synergy you own  │
│ TG ││   Magic in St││ List · Visual · Identity ·      ││ Weakest links (cut?)    │
│    ││   I cast…    ││ Primer · Physical(3)            ││ Card detail on select   │
│ ST ││   …          ││ cmc · lands · ramp · curve · own││                         │
└────┘└──────────────┘│ grouped list, ownership dot/row ││   ] toggles drawer      │
                      └─────────────────────────────────┘└─────────────────────────┘
URL: /w/:deckId?tab=list&card=:name     Collection and Tags remain full pages.
```

Key screen: `docs/design/b-workbench.html` → `shots/b-workbench.jpg`. Every
row carries an ownership dot: green means in this deck, amber means owned
but sleeved elsewhere, a hollow red square means not owned. The drawer
always answers the question of what could go in this deck from what you
already own.

Tradeoffs:
- ✅ Serves J1–J4 in one place, with keyboard-first navigation (j/k
  roster, tab views, `]` drawer).
- ✅ Free agents finally have a home in the drawer, not in a table you
  have to search.
- ❌ It's a real client-side app. Three panes of state are hard on
  Jinja-plus-fetch, and it probably needs a small state module or htmx.
- ❌ At 390px the three panes collapse to one, so the phone experience is
  a plain deck page.
- ❌ It still treats "identity" as a tab, not as the product.

### Direction C: "Identity Lab" (radical, recommended)

> A deck is a thesis. Every card is for it, glue, or generic, and the app's
> job is to shrink "generic".

The new core loop:

```
  write thesis  ──►  name 2–4 pillars  ──►  every card auto-classified
  (1 sentence)       (built from Lab tags)    pillar · glue (ramp/lands) · generic
        ▲                                             │
        │                                             ▼
   play / tweak  ◄──  swap: generic OUT → on-thesis IN, sourced from
                      Box (free agent) → owned-elsewhere → buy (EDHREC as a reference)
```

Sitemap:

```
Rail:  Lab · Deck · Box · Tags · ─ · Settings
/                 THE LAB: decks as identity tiles, sorted by "least distinct first"
/deck/:id         IDENTITY (default): thesis, on-thesis %, pillar tiles, Sharpen, Drift
                  tabs: List · Visual · Primer · Physical
/box              free agents + "where is X", grouped by card; facets: free / sleeved / wishlist
/box/salvage      paste a list → "buildable from box: 82/100" (Salvager API, finally with a UI)
/tags             vocabulary: pillar-eligible tags vs workflow tags, merge/rename, core ★
/settings         accounts · sync · styleguide · changelog
```

Key screens:
- `docs/design/c-lab.html` → `shots/c-lab.jpg`. The home page's headline
  is the state of the collection ("51 decks are still just EDHREC."). Decks
  appear as metro tiles. The tile size encodes urgency, the top bar encodes
  colour identity, and a live tile cycles through the reasons a deck needs
  attention. The Box is a solid-amber tile, the single loudest object on
  the page.
- `docs/design/c-identity.html` → `shots/c-identity.jpg`. The deck page
  leads with the thesis at 72px Garamond and an on-thesis meter (pillar /
  glue / generic). Pillars are tiles, with Pillar 1 in the hero amber
  colour. Below that, **Sharpen** suggests generic → on-thesis swaps,
  labelled with where each card would come from, and **Drift** handles
  Archidekt vs physical box.

Tradeoffs:
- ✅ It's the only direction that does J1, which is the reason the app
  exists. EDHREC becomes a distinctness reference ("31% overlap with the
  average Kaalia deck; lower is more yours") instead of the template.
- ✅ The strongest visual identity. Type-led, tile-driven, with one
  dominant accent per surface. Nobody would mistake it for Archidekt.
- ✅ Swaps that prefer the Box turn physical inventory from bookkeeping
  into the thing that drives decisions.
- ❌ Needs new backend: thesis and pillar storage, a card→pillar classifier
  (it can start as "card has a tag that's in the pillar's tag set"), a
  dedupe of instances, and an EDHREC overlap metric. That isn't my lane.
  I've flagged it for triage below.
- ❌ It's empty until you write a thesis. Mitigation: seed a draft thesis
  and pillars from the top three Lab tags per deck, marked as a draft. A
  51-deck backlog is also a nice way to work through the roster.
- ❌ Scores can feel like homework. Keep them advisory: no red, no streaks.

---

## 4. Recommendation and phased path

**Ship C, built on A's plumbing, and steal B's drawer later if it's still
wanted.** A alone polishes the wrong thing. B is a big client-side build for
an IA that still doesn't answer "what makes this deck mine". C changes the
question the app asks, and most of its screens are composed from kit pieces
that already exist.

| Phase | Scope | Owner | Done when |
|---|---|---|---|
| **0. Data truth** | Point the deck view, tags and collection at `instance:*` + `card_meta:*` (retire the `card:*` reader). Dedupe the 09-25 instance import. Fix the manage aggregate's deck-id match. Pick one status vocabulary. Delete `test-deck`. Drop or neutralize the repo badge | coder | Holy Hellfire shows 100 cards, and `/instances` count ≈ 5,608 |
| **1. Shell + merges** (A) | Icon rail, command bar, sync in the bar. Remove Home, move accounts to Settings, merge Inventory and Instances into a virtualized `/collection` grouped by card. Manage becomes a deck tab. Contrast token fixes. Migrate decks/manage/tags off legacy CSS | designer | No page uses legacy aliases (`grep var(--accent templates/` is empty), and every page passes AA |
| **2. Identity model** | Store `thesis` and `pillars[{name, tags[]}]` per deck. Classify cards as pillar / glue / generic. Seed draft pillars from the top Lab tags. Wire `Review` to the existing `/api/decks/{id}/review` | coder | The API returns on-thesis % per deck |
| **3. Identity page + Lab** (C) | `/deck/:id` Identity tab as the default, then the Lab home with urgency-sized tiles and live tiles | designer | Matches `c-identity` and `c-lab`, with reduced-motion verified |
| **4. Sharpen + Box** | Swap suggestions ranked Box → owned elsewhere → buy. `/box` and `/box/salvage` on the Salvager API. EDHREC overlap metric | coder + designer | One click moves a free agent into a deck and updates the instance |
| 5. (optional) Drawer | B's context drawer on the deck page, if Sharpen proves it wants to be persistent | designer | n/a |

Things I deliberately chose against:
- **No light theme.** The dark HUD is right for card art. Make dark mode
  excellent rather than shipping two average themes.
- **No visual-grid-first decklist.** At thumbnail size, card text is
  unreadable. List view is the default, and Visual stays a tab.
- **No parallax or hover zoom on phones.** Card-art scale-on-hover is
  pointer-only.
- **No gradients**, except a single legibility scrim behind art on the B
  hero. The C identity bar uses hard colour stops.

Open taste call for Andrew (one question): **should the Lab home rank decks
by "least distinct first" (a sharpening backlog) or by "last played"?** I'd
ship least-distinct first. It makes the home page a to-do list for the one
job the app exists for.

---

## Appendix: evidence index

| File | What it shows |
|---|---|
| `design/current/home.jpg` | Splash page, no data |
| `design/current/decks.jpg`, `decks_phone.jpg` | Accounts above the fold, NO REPO everywhere, partner title collapse, phone overflow |
| `design/current/deck.jpg`, `deck_phone.jpg` | Deck view on real data: 0 cards |
| `design/current/deck_manage.jpg` | Whole deck listed as "missing physically", off-system blue panel |
| `design/current/inventory.jpg` | Empty table |
| `design/current/instances.jpg` | Duplicated rows, low-information columns |
| `design/current/tags.jpg` | Cloud dominates, empty table, duplicate tags |
| `screenshots/after-hud4/*` | HUD-4 deck view rendered against non-prod data |
| `design/shots/*.jpg` | Mockups at 1440 and 390px |
