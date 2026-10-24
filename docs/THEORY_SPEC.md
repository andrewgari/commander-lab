# Commander Lab — Theory (thematic overhaul docs)

## Goal
On demand, ask for a massive thematic overhaul of a deck. oracle writes a formatted doc describing
the rebuilt deck, reusing the current cards where possible. Docs are stored on Tower and can be
re-opened from the deck page's "Ideas" section. Theory never modifies the decklist.

## Request form (deck page → Ideas → New theory)
- Theme (free text), e.g. "superfriends"
- Commander: (•) Keep (default) / ( ) Open to swap

## Card pool order
1. Cards in this deck → 2. free-agent owned cards → 3. buys (Scryfall USD) under the per-card
cap (global default + Journal override; hard = never, soft = flagged).
Never pull from other decks, so the steal rule does not apply. Pet cards from the Journal are kept
unless the commander swap makes them illegal (then called out).

## Doc v1 sections
thesis_pillars · decklist (100, grouped by role, each card tagged kept/owned/buy+$) · cuts ·
buy_total · commander_change (only if swapped: reason + reuse count) · salt flags inline (oracle's
existing policy).

## Extensibility (hard requirement)
The doc is versioned JSON `{schema_version, deck_id, theme, created_at, sections:[{type,title,data}]}`.
One Jinja renderer per section type, plus a generic markdown fallback for unknown types. Old docs
never break. Future features add new section types, with no migration.

## Storage
Redis: `theory:{deck_id}:{doc_id}` (JSON) + `theory_index:{deck_id}` (sorted set by created_at).
No new DB. Docs can be deleted from the UI. No editing in v1.

## Generation
App → oracle API server (Framework, OpenAI-compatible /v1/chat/completions, oracle key), with a
new oracle skill `deck-theory` (fixed rubric + JSON schema). The app validates the schema and
checks the facts afterward: card legality/color identity, kept/owned/buy tags against the real
instance registry, and prices recomputed from Scryfall. Oracle's claims are never trusted for
ownership or price. Async job with a status poll (30s–minutes). If oracle is unreachable → show
"oracle offline, try again later".

## Prerequisites
- oracle API listening (Framework gateway restart; :8645 down as of 2026-10-12).
- App → oracle client (provider-style, config via env: ORACLE_URL, ORACLE_KEY).

## Non-goals (v1)
Writing to Archidekt · saving as a deck/copy · pulling from other decks · editing docs ·
diffing two theories · extra sections (Andrew will add later via other features).

## Acceptance
- Generate a theory for a real deck end-to-end on prod (:8020). It shows under Ideas and re-opens
  after a reload.
- Every card tag matches the instance registry; total buy cost = sum of the Scryfall USD buy prices.
- The commander-swap toggle produces the commander_change section only when the commander was swapped.
- An unknown section type renders via the fallback (unit test).
- No test data written into real decks (use a fixture deck).
