# Scope: Moxfield-lite deck builder, inventory-aware building, buy lists

Status: DRAFT FOR BOARD APPROVAL — no build work is authorized before approval.
Board direction (2026-10-27, priority 4): "deck management, inventory: rudimentary
deck building tools like Moxfield. Plus personalized inventory and purchasing
options."

## Goal

Let a user build a Commander deck from scratch in Commander Lab — add and remove
cards by search, see which cards they already own, and get a per-deck buy list
with prices — without leaving the app or touching Archidekt/Moxfield. Decks are
saved to Postgres like every other deck in the registry.

## What already exists (reuse map)

| Need | Existing code | Reuse assessment |
|---|---|---|
| Staged deck editing | Draft mode: `domain/draft/draft_mode.py`, `api/draft.py`, tables `drafts` / `draft_cards` / `draft_instance_moves`; add/remove cards, qty, boards, commit-as-one-transaction. Spec: `docs/DRAFT_MODE.md` | **Direct reuse.** This is the builder's spine. Gap: it requires 1+ source decks (combine=2+, dismantle=1) — needs a new `scratch` kind with zero sources. |
| Card search | Shared card-search component: `static/js/card_autocomplete.js` + `GET /api/cards/autocomplete` (spec: `docs/architecture/shared-card-autocomplete.md`), with Scryfall hover preview | **Direct reuse, unchanged.** |
| Owned/missing math | `domain/inventory/have_buy.py::compute_have_buy` — pure `deck_cards ⋈ card_instances` have/pending/buy split (bound / free / mailed / elsewhere) | **Direct reuse.** Needs only a "no deck_id yet" entry point for draft lists. |
| Prices | `printings.prices` (Scryfall USD, refreshed by the sync worker); theory `buy_total` section (`theory.py` / `partials/theory/buy_total.html`) already computes per-card cheapest-price buys with a total and "Cards to buy" stat | **Direct reuse** of the pricing model and the `hud.tcg_cart` / `tcgplayer_cart.js` TCGplayer cart export. |
| Deck persistence | `decks` + `deck_cards` tables; registry statuses `physical` / `digital` / `retired` / `testing` (`docs/DECK_IMPORT.md`) | **Direct reuse.** Builder decks commit as `digital` (decklist only, not assembled). |
| Legality data | `cards.legalities` (Scryfall), color-identity check logic in `domain/analytics/theory.py` (`_ci_ok` / `can_command`) | **Reuse the data; new small service** to run checks over a list. |
| Reconstruct / brew | `domain/draft/reconstruct*.py`, brew UI (`brew_deck.html`, `brew.js`) — LLM-assisted generation from pools/ideas | **Not reused as machinery.** It is a different product (generate vs. manual build); we only follow its UI patterns (buy rows with per-card price). Both flows converge on the same draft tables. |

New code is therefore mostly: a `scratch` draft kind, a legality check service,
an owned-status field on draft state, a per-deck buy-list endpoint + view, and
builder UI.

## Phase 1 — In-app deck builder (v1 core)

- "Build a deck" opens a scratch draft: no source decks, optional prefill by
  pasting a decklist (reuse the decklist text parsing from import tooling).
- Add/remove cards through the shared card-search component; qty edit; boards:
  commander / main / sideboard (matches existing `board` vocabulary).
- Legality checks, advisory not blocking: singleton (max 1 per name except
  basics), 100-card main for Commander, color identity vs. commander, format
  legality from `cards.legalities`. Warnings render inline in the builder.
- **Commit** creates the deck in Postgres (`decks`, `deck_cards`), status
  `digital`; the deck then appears in the normal Decks page and is eligible for
  the existing analytics/review flows.
- Editing an existing deck's list is **not** in v1 (see "Later") — v1 builds new
  decks only; existing imported decks stay managed by import refresh.
- Effort: **M** (~1–2 eng-weeks). Backend small (draft kind + legality service
  + commit path); most work is builder UI on top of existing draft endpoints.

## Phase 2 — Inventory awareness while building

- Draft state response gains per-line owned status from `compute_have_buy`
  semantics against the full `card_instances` collection: owned-free, owned-but-
  committed-elsewhere, in-mail, not-owned.
- Builder UI: "owned" flag per card line; a "Missing cards" panel per draft
  with count and names; owned status also shown on every saved deck's detail
  view (the same math already backs the inventory headline view — this just
  surfaces it per deck, including drafts).
- Effort: **S–M** (~3–5 eng-days). The math is a finished pure function; work is
  wiring, response schemas, and UI.

## Phase 3 — Per-deck buy list

- "Buy list" view on every deck: the missing (buy > 0) lines with the best
  available price from existing `printings.prices` data (cheapest printing),
  quantities, and a USD total — same model the theory `buy_total` section
  already renders. Includes the existing TCGplayer cart export button.
- Reuses the Phase 2 buy-qty computation; refresh of prices stays with the
  existing sync worker (footnote the price date, as current UI does).
- **No checkout/purchase integration in v1** unless the board asks.
- Effort: **S** (~2–4 eng-days).

Total rough v1 effort: **~2–3 eng-weeks** across the three phases; phases can
ship independently (1 → 2 → 3 order, 2 and 3 depend on 1's scratch decks).

## Explicitly later (post-v1, needs board ask or design)

- Editing existing imported decks' lists (conflict policy vs. re-import
  refresh from Archidekt/Moxfield needs its own design).
- Printing/variant selection and condition tracking on buy lines.
- Purchase intent tracking ("bought → in_mail" instance status already exists;
  a purchase workflow on top is future).
- Checkout integration with any store; non-Scryfall price sources.
- Budget caps per buy list (theory already has per-card caps to borrow from).
- Sharing/publishing decks publicly.
