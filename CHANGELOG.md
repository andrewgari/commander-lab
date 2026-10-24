# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Composition Presets and Deck Composition: the category suggestion list no longer covers the "Add category target" / "Add override" buttons.
- Composition Presets: "Add category target" now uses the shared category pill input (target/min/max/why stay per category) instead of the broken slot row.
- Added the missing category ref input script and styles, so "Add category target" and "Add override" work again; also adds a reusable category pill input.

### Changed
- Archetypes: the description field is now a multi-line markdown editor (bold/italic/code/lists) with a live Preview tab and character counter, the limit is raised from 1200 to 4000 characters, and the registry list shows a rendered excerpt of the first paragraph. Existing descriptions are unchanged; single line breaks in them are now displayed as typed.
- Deck Overview: the Update/Push sync controls, external links and Lifecycle status now live in a "Deck utilities" section at the end of the summary column instead of beside the commander.

- Deck Composition page: category overrides now use the shared text input with removable category pills (target/min/max/why stay on each pill); saved data is unchanged.

- The release notes and changelog page now list curated, user-facing changes only, instead of raw change descriptions.

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
