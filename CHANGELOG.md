# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Web app: a shared dark theme (colors, type scale, spacing, radii) now applies across all pages, with a visible keyboard focus ring on links, buttons and inputs, and animations are turned off for people who prefer reduced motion. Pages look the same apart from using the shared colors.
- Card search API: new `GET /api/v2/cards/search` filters by name, color identity (within/exact/includes), card type, mana value, rarity, oracle text, owned / not owned / any, and "Can be commander" (with partner pairing). Results show how many copies you own and whether each card can be a commander; if Scryfall is unavailable it falls back to your local card library and says so. The card autocomplete results now also include each card's `oracle_id`. No page uses it yet.
- Brew draft editing (internal API): a brewed deck can now have cards added and removed (`POST /api/v2/brew/{session_id}/cards`, `DELETE /api/v2/brew/{session_id}/cards/{oracle_id}`). Each edit re-checks where the card comes from (donor deck, owned, or buy) and updates the buy cost; a second copy of a non-basic card is refused, and the commander cannot be removed. Accepting an unedited brew is unchanged. No user-visible change yet.
- Deck review page: each deck now has a Deck review page. Generate a review (it runs in the background and shows what it is doing; you can leave and come back), read the latest one, browse earlier reviews, export it as a PDF that matches the print view, and mark every suggested swap Accept, Maybe or Reject with an optional reason; your answers feed your taste profile. Card pictures are saved on the server after the first view, so the PDF needs no internet. The PDF export uses an optional extra container (`docker compose --profile pdf up -d`, about 1.4 GB); without it the button explains that PDF export is not running. The existing Markdown, JSON and CLI exports are unchanged.
- Deck reviewer groundwork (internal): the review pipeline now has its recommendation and verification passes — swaps are chosen only from the vetted candidate pool (with price, owned flag, bracket and budget effect, and one alternative each), then every finding and swap is checked by code (legality, color identity, singleton, budget, bracket, cards you already rejected for this deck) and by a second AI model that looks for unsupported claims, generic advice, contradictions with the brief and hand-waved trade-offs. Failing items are rewritten at most twice and otherwise dropped into an omitted list instead of being shown. No user-visible change yet.
- Deck reviewer groundwork (internal): the review pipeline now has its first two passes — a brief of what the deck is trying to do (with questions for the owner where intent is unclear) and a seven-axis analysis with 1–10 scores — plus per-pass model configuration, structured-output enforcement with bounded repair, prompt caching and a stored trace of every run. Findings that cite cards outside the deck or facts that do not exist are dropped and logged. No user-visible change yet.
- Deck reviewer eval harness: deterministic review gates with good/bad fixtures (`make eval-gates`), an LLM judge, calibration tooling, golden decks, and `scripts/eval_compare.py`.
- Deck reviewer eval: `make eval-live` (or `python -m tests.review_evals.run_eval --live --tier <provider:model>`) runs every golden deck through the full review pipeline with real model calls and writes, per deck, the review JSON, trace, gate results, cost, latency and judge scores (including rubric recall). It exits non-zero with a clear message when a model id or API key is missing; the offline `make eval` is unchanged. Internal tooling; no user-visible change.

### Fixed
- Brew commander search: pressing Esc right after typing no longer lets the pending search re-open the suggestion list, and on narrow screens the sticky Generate bar now says "Pick a commander to brew" until a commander is chosen.
- Quick reviews now use real card data instead of showing cards as unknown.
- Brew/Reconstruct/Manage card search fields now share one autocomplete; the Brew commander field selects the top suggestion on Enter so Generate enables.
- Staple Themes card field and the Deck Tuner overlay's add-card boxes now use the same shared card autocomplete as Brew, Reconstruct, Manage and Journal; misspellings such as "spidr" or "atraxxa" now find the intended card in every field, including Brew's commander search.

### Added
- Taste profile: the Profile page now has a section that stores what the deck reviewer should know about you (identity, color and archetype leanings, pet cards, dislikes, budget, review style) in Postgres, seeded from `profiles/andrew.json`. Every value records whether you stated it or it was learned from your swap feedback, and your own statements are never overwritten. Accept/reject/maybe answers on suggested swaps are stored; patterns in them (e.g. a card you keep rejecting) show up as proposals that only change your profile after you confirm them.
- Deck reviewer groundwork (internal): the fact pack now covers curve, mana/colored sources vs published norms, role counts, bracket reasons, Commander Spellbook combos and near-misses, EDHREC deviation, salt, synergy clusters/orphans and chart data, each with its source and fetch time or an explicit "unavailable" reason; a candidate pool of legal, in-identity, in-budget swap options (owned cards first) is built for later review passes. No user-visible change yet.
- Deck reviewer groundwork (internal): approved design decisions and the `review_document` v1 JSON Schema, with a fixture and validation tests. No user-visible change yet.
- Show human-relative release age (e.g. "3 hours ago") next to release dates on the changelog page.
- Deck pages: a Copy decklist control copies the deck as paste-ready text in one click — Plain by default, with Archidekt, Moxfield, Forge and TCGplayer formats, optional sideboard/maybeboard/printing toggles, your last format remembered between visits, a "Copied!" confirmation, and a select-all manual-copy fallback when the clipboard is unavailable.
- Card dossier: opening a single card (`/card/{oracle_id}`, optional `?printing=` to switch art) shows every printing, oracle text/mana/type, your tags, which of your decks play it, ranked synergy neighbors with the reason for each link, an editable affinity/notes field, and whether you own a copy — all from Postgres, with printings lazily fetched from Scryfall and cached on first view.
- Deck reviews (API): build a stored, versioned review for any deck — deterministic fact pack (mana math, curve/tempo, composition, bracket/salt, combos, have/buy), candidate retrieval from EDHREC + your collection, and LLM-written section explanations where every card name is validated against Scryfall before it is stored. Rebuilding an unchanged deck reproduces identical facts under a new version; if the LLM is offline the review still renders with "explanation offline" in place of prose, and document sections and dashboard panels always show the same numbers.
- Deck review report: view the verdict, prioritized swaps, evidence, and mana/synergy charts in a responsive scouting report; print uses the same report with clean page breaks and page numbering.
- Similarity API backed by a rebuildable pgvector index: `/api/v2/analytics/similar-cards/{oracle_id}` and `/similar-decks/{deck_id}` answer from bge-small embeddings of oracle text + learned tags, `/api/v2/analytics/derived-index-status` reports whether the index is fresh or stale, and `python -m src.server.workers.index_rebuild vector` rebuilds the whole index from Postgres in one transaction — the index is a disposable cache, never the source of truth.
- Draft mode: combine two or more decks (or dismantle one to build another) in a staging session where nothing touches your decks, deck lists or physical collection until you press Commit — committing applies the new deck, its card list and every physical-copy move in one transaction, and each copy records a provenance trail ("this copy moved A → B") you can look up per card instance. Abandoning a draft deletes every staged row with zero residue. A new Drafts page hosts the flow, with add/cut suggestions that prefer copies you already own and only pull from a donor deck when the donor is left no worse; scoring signals that aren't available yet are labelled "unavailable" with the reason instead of showing a fake 0.
- Your whole collection now lives in Postgres: every deck and every physical card copy was migrated with its provenance (counts reconciled 1:1 with the old store), and a new Inventory page plus a Profile page show your collection stats, mana curve, archetype mix, and power level. Profile edits are saved as versioned snapshots you can browse back through.
- Games page: paste or upload a verbose Forge log for one of your decks and Commander Lab imports it on demand — the game (result, turns, seats), per-seat curves and per-card cast timings are stored, a game report renders right away, and your profile gets a new snapshot. Draw/stuck data Forge never records is shown as "unavailable" with the reason, and re-importing the same log is a no-op instead of creating duplicate games.
- Decks now have a Postgres-backed home: importing an Archidekt deck stores it alongside your physical card collection, and the new deck list and deck pages show a have/buy split (copies you own, have incoming, or still need to buy) plus a plain-language profile summary of the deck's mana curve and archetype mix.

### Fixed
- Forge Honcho sync: pushing the play-style profile without HONCHO_API_KEY set, or with sync disabled/absent in ~/.honcho/config.json, now exits cleanly with a message saying exactly what to set instead of crashing — and nothing is sent or written when configuration is missing.
- Brew page on phones: the step bar and donor deck details no longer run off the right edge of the screen (steps wrap onto two rows, donor details wrap under the deck name), and the Generate button is now tall enough to tap comfortably.
- Brew deck page: its brew.js script tag is now versioned for cache-busting, matching the same fix applied to the Brew console page.
- Brew page: the Commander field could keep showing no suggestions after an update because the page's script was the one asset loaded without the app's cache-busting version tag, so browsers could keep running the previous version; it is now versioned like every other asset.
- Brew page: the Commander and Donor decks fields now behave as real autocomplete pickers — typing shows clickable matches, a query matching no deck says so instead of silently showing nothing, and Arrow keys/Enter/Escape work in both fields. Brew also appears in the sidebar next to Reconstruct.

### Changed
- Accessibility: charts and the power panel now use solid AA-contrast greys instead of faded text, and wide tables can scroll inside their own frame so the page no longer scrolls sideways on phones. Pages adopt the table frame as they are updated.
- Archetype and genre Required Roles are no longer picked from a fixed produces/consumes list: every role is now a tag you define yourself, where each tag is one Scryfall query shown and editable in the editor. The Add Role picker groups tags by facet (IS / TALKS ABOUT / MAKES / AMPLIFIES / FREE) under a target (e.g. "Birds"), offers starter queries per facet, and previews "N copies · M cards in this deck" against a real deck before you save. Archetype scoring now reports exactly which cards matched each role's query, and a role whose query matches nothing (or fails) is shown as "empty"/"blocked" instead of a silent 0. Saved archetypes and genres keep scoring as before — old produces:/consumes: roles resolve to equivalent migrated tags.

### Fixed
- Archetype role "Instants & sorceries (produces)" scored 0 for every deck; it now matches cards that are instants or sorceries.

### Changed
- Deck page navigation is now a ten-tab workflow — Overview, Manage, Tune, Stats, Identity, Primer, Journal, Ideas, Review, Simulate — in the order you actually work a deck, with number-key shortcuts (1-9, 0) to jump between tabs and a `?` legend overlay that lists every shortcut. Manage is now a full tab instead of a header button, the Primer has its own tab with a dedicated reading page (it no longer appears inside the Overview's Briefing), and the Composition view has moved into the Stats tab as a full-width section — the old Composition address now permanently redirects to Stats.

### Added
- Brew a deck: a new "/brew" flow (button on the Decks page) builds a virtual deck around a chosen commander — pick donor decks (only decks you name are sourced), choose Owned-first or Best build, add a power level, idea, themes and notes, and generate a virtual deck plus an availability report showing which cards come from donors, which from free inventory, and a buy list with a TCGplayer cart button. Nothing moves at generation: donor decks stay fully intact, and every card records its provenance.
- Tune tab Decklist view: every card chip now has a printing picker — click the set badge to browse every printing of that card (set, collector number, finish, price) and switch to the one you actually own. Changing the printing counts as a new card: if that exact printing isn't in your inventory the card is flagged "needs purchase" on the chip, and the flag is verified against your collection on save.
- Tune tab: a new Decklist view (Tuner/Decklist toggle) layers a deckbuilding board over the decklist — one column per card tag plus Commander/Maybeboard/Considering. Drag cards between columns to re-tag them, add cards with per-column search, remove cards, and flag any category "not in decklist" to drop its cards from the deck count. Edits collect in a paper-only draft (Save draft) that survives reloads and never touches the real deck or your card instances; writing the draft back to the deck comes with the apply step.
- Tune tab, Decklist view: a Recommendations panel floats at the right of the board listing the cards your tune review suggests adding. Cards already in your collection and free to use are hidden; cards you don't own (or don't own in the suggested printing) show a Purchase flag. Drag any card from the panel into a column to add it to the draft.
- Tune: every swap recommendation now shows a short explanation, plus two new actions per row — "Reconsider" (the tuner answers the challenge and stands its ground) and "Recommend something else" (the suggested card is ignored for the rest of the review and the next-best card for the same slot takes its place, with its own explanation).
- Deck Tuner: a "TCGplayer cart" panel in the change list lists every card you accepted as a buy with a checkbox; uncheck any you don't want and the button opens a TCGplayer Mass Entry cart with exactly the checked cards, pinning the chosen printing (e.g. `[SLD]`) when a set code is known.
- Deck journal: a "Deck journey" timeline lists the deck's major changes and milestones newest-first, with a short note on how the deck played at each point. You can add milestones, edit any entry's title and note in place, and redraft an auto-written note to regenerate it (redrafted entries are marked). Tune-up applications and syncs will record their card diffs here automatically.
- Deck Tuner: the change list now has two save paths. "Save for later" stores the tune-up as a draft you can reload or discard, and changes no cards or inventory. "Save to deck" performs the swap: cut cards go back to your collection as unused, added cards are marked as in this deck (cards you don't own yet are flagged still to buy), and you are then offered to update the linked Moxfield and Archidekt decks.
- Archetype editor: a Draft/Enhance button beside the Description field drafts a 2-4 sentence description for an empty field or improves your existing text; the result fills the field but nothing saves until you hit Save. Drafts that name a specific card or state a hard count are rejected with a plain message, and the button reports "unavailable" when no AI model is configured.
- Decks now have named card slots: alongside Commander and the main 99, a deck can carry a Maybeboard, a Considering list, a Planar deck (Planechase), a Scheme deck (Archenemy) and an Attraction deck (Unfinity), synced from Archidekt and Moxfield and understood by the decklist paste/import parser.
- Deck validation now enforces slot legality: the Planar deck accepts only planes and phenomenons, the Scheme deck only schemes, the Attraction deck only attractions, and none of those cards are legal in the commander slot or the main 99. Violations are flagged NOT_LEGAL with a plain-language reason (e.g. "plane not legal in mainboard slot") that review suggestions can quote, cards whose type data is missing fail closed in variant decks, and Maybeboard/Considering stay unrestricted.

### Changed
- Commander Salt links and the Commander Salt sync-source field are now hidden while the integration is unfinished; they return when the `COMMANDER_SALT_ENABLED` feature flag is switched on. No salt data, sync routes or analytics were removed.
- Deck page: the card debate view opened from a card's detail modal is now called "Quick look" instead of "Card review", and the header button that starts the journal check-in is now "Check in"; on Tune, the dismiss hint now says "suggestion" — the Review tab is now the only surface that says "review".
- Sidebar navigation: the flat 11-item list is grouped into labeled sections (Decks, Collection, Taxonomy, Tools, Reference, Configure) under a Command Center home item; "Tag Management" is now labeled "Card Tags", Staple Themes sits under Taxonomy, and Player Profile moved into the sidebar footer next to the version badge.
- Deck Overview: the decklist now leads the page instead of sitting below the analytics panels — the order is decklist, Briefing, a compact "Stats at a glance" strip (curve, types, colours) linking through to the Stats tab, then deck utilities. The Visual/List decklist toggle is now a segmented control that no longer looks like the navigation tabs, and the large commander showcase is retired in favour of the commander art in the deck header shown on every tab.
- Reconstruct page: controls, chips and colors now use the shared HUD component styling (the Font Awesome icon font is no longer loaded); the workflow and its labels are unchanged.
- **BREAKING:** The "Sideboard" concept is removed. Deck lists or linked decks that contain a Sideboard section now have those cards placed in the Maybeboard instead, and the import flags this mapping so it can be reviewed.
- Deck Tuner: cards recommended for removal now render in red, and dismissing a cut is permanent — dismissed cuts are never flagged again (with an "Undo dismiss" option per card).

### Fixed
- Commander Salt now works for decks never viewed on commandersalt.com: when the salt cache lookup reports a deck missing, the app now asks Commander Salt to import and score the deck (the same import their own site uses), so salt scores and salt-based analytics appear instead of silently showing nothing.
- Deck page on mobile: a long linked-deck URL (e.g. a Moxfield deck link) in the sync panel no longer forces the whole page to scroll horizontally; long URLs and sync notes now wrap inside the panel.
- Card review and evaluation suggestions are now gated to mainboard slots: planar, scheme and attraction cards can never be suggested for the main 99 (e.g. no planar card is ever recommended for Missy's main deck), candidates must pass full mainboard legality (Commander-legal, known legality, colour identity, singleton) at proposal time, and every suggestion carries its slot-gate result with a plain-language pass reason. Card facts now expose the mainboard slot gate alongside Commander legality so the two can't be confused.
- Deck validation and review: cards with no synced Commander legality (e.g. newly printed or planar cards) no longer silently pass as legal; they are flagged UNKNOWN_LEGALITY and must be re-synced or replaced.
- Archetypes: a bullet or numbered list typed directly after an intro line (e.g. "Key engines:" then "- item") now renders as a real list in the description preview and registry list, instead of showing literal dashes.
- Composition Presets and Deck Composition: the category suggestion list no longer covers the "Add category target" / "Add override" buttons.
- Composition Presets: "Add category target" now uses the shared category pill input (target/min/max/why stay per category) instead of the broken slot row.
- Added the missing category ref input script and styles, so "Add category target" and "Add override" work again; also adds a reusable category pill input.

### Changed
- Archetypes: the description field is now a multi-line markdown editor (bold/italic/code/lists) with a live Preview tab and character counter, the limit is raised from 1200 to 4000 characters, and the registry list shows a rendered excerpt of the first paragraph. Existing descriptions are unchanged; single line breaks in them are now displayed as typed.
- Deck Overview: the Update/Push sync controls, external links and Lifecycle status now live in a "Deck utilities" section at the end of the summary column instead of beside the commander.

- Deck Composition page: category overrides now use the shared text input with removable category pills (target/min/max/why stay on each pill); saved data is unchanged.

- The release notes and changelog page now list curated, user-facing changes only, instead of raw change descriptions.
- Primer builder: generated primers and per-section redrafts now come back with at least 500 characters of concrete detail instead of occasionally returning very short drafts.

## [v0.4.164] - 2026-10-22

Maintenance release, no user-facing changes.

## [v0.4.163] - 2026-10-21

### Added

- Simulate tab: a new "Diagnosis" section (05) explains the latest simulation run with evidence-backed findings on what is working, what is not, and which pillars or stragglers stand out. Below 10 games it makes no claims, and missing telemetry shows as "unavailable" rather than 0%.

## [v0.4.162] - 2026-10-21

### Added

- Composition rules: reference fields on the Presets page and the deck override form now suggest real category references as you type, and offer click-to-apply "Did you mean" fixes for mistyped references.

## [v0.4.161] - 2026-10-21

### Fixed

- Simulate tab: a failed run now shows failure wording and a clock frozen at the time it failed, instead of "until this run finishes" and 0:00.
- Simulate tab: the report header no longer shows the literal text "undefined" when no focus line is set.

## [v0.4.160] - 2026-10-21

### Fixed

- Simulate tab: a failed run restored after a reload now shows its failed state, error, and the parameters it was submitted with.
- Simulate tab: focus moves to the job panel when a run starts, the elapsed clock and ETA are more accurate, and the Run button and notes are disabled while a run is active.
- Simulate tab: the "Specific" opponent picker is prefilled from the run's saved opponents.

[Unreleased]: https://github.com/andrewgari/commander-lab/compare/v0.4.164...HEAD
[v0.4.164]: https://github.com/andrewgari/commander-lab/compare/v0.4.163...v0.4.164
[v0.4.163]: https://github.com/andrewgari/commander-lab/compare/v0.4.162...v0.4.163
[v0.4.162]: https://github.com/andrewgari/commander-lab/compare/v0.4.161...v0.4.162
[v0.4.161]: https://github.com/andrewgari/commander-lab/compare/v0.4.160...v0.4.161
[v0.4.160]: https://github.com/andrewgari/commander-lab/compare/v0.4.159...v0.4.160
