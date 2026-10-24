# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Deck page navigation is now a ten-tab workflow — Overview, Manage, Tune, Stats, Identity, Primer, Journal, Ideas, Review, Simulate — in the order you actually work a deck, with number-key shortcuts (1-9, 0) to jump between tabs and a `?` legend overlay that lists every shortcut. Manage is now a full tab instead of a header button, the Primer has its own tab with a dedicated reading page (it no longer appears inside the Overview's Briefing), and the Composition view has moved into the Stats tab as a full-width section — the old Composition address now permanently redirects to Stats.

### Added
- Archetype editor: a Draft/Enhance button beside the Description field drafts a 2-4 sentence description for an empty field or improves your existing text; the result fills the field but nothing saves until you hit Save. Drafts that name a specific card or state a hard count are rejected with a plain message, and the button reports "unavailable" when no AI model is configured.
- Decks now have named card slots: alongside Commander and the main 99, a deck can carry a Maybeboard, a Considering list, a Planar deck (Planechase), a Scheme deck (Archenemy) and an Attraction deck (Unfinity), synced from Archidekt and Moxfield and understood by the decklist paste/import parser.
- Deck validation now enforces slot legality: the Planar deck accepts only planes and phenomenons, the Scheme deck only schemes, the Attraction deck only attractions, and none of those cards are legal in the commander slot or the main 99. Violations are flagged NOT_LEGAL with a plain-language reason (e.g. "plane not legal in mainboard slot") that review suggestions can quote, cards whose type data is missing fail closed in variant decks, and Maybeboard/Considering stay unrestricted.

### Changed
- Reconstruct page: controls, chips and colors now use the shared HUD component styling (the Font Awesome icon font is no longer loaded); the workflow and its labels are unchanged.
- **BREAKING:** The "Sideboard" concept is removed. Deck lists or linked decks that contain a Sideboard section now have those cards placed in the Maybeboard instead, and the import flags this mapping so it can be reviewed.

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
