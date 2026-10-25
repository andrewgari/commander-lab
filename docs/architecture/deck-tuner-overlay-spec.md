# Deck Tuner Overlay — Shell + Data Model Spec (DT-0)

Status: spec only (no implementation). Verified against repo @ 34d7aaa.
Scope: overlay on `/deck/{deck_name}/tune` for deckbuilding (moxfield/archidekt-level basics).
Non-goals: changing existing Tune tab behavior outside the overlay; any code in this card.

## 1. What exists today (verified anchors)

- Route: `GET /deck/{deck_name}/tune` → `_deck_tab(..., "deck_tune.html", "tune")` (`app.py:301`). The overlay is a new mode of this page, not a new route.
- Tuner recommendations: `analytics/tuner.py` (`compute`, `decide`, `set_adds`); per-deck mutable state at `tuner:state:{registry_id}` = `{review_id, decisions{cut: {decision, add}}, adds[]}` with decision vocabulary `("own", "buy", "dismissed")` (`tuner.py:56,94,674+`).
- Saved recommendation records: `analytics/tune_store.py` — `tunerec:rec:{id}`, `tunerec:active:{deck_id}`, `tunerec:index:{deck_id}`, archive-on-recompute in MULTI/EXEC. Reuse this pattern.
- Per-deck card tags (overlay categories): `deck_card_tags:{deck_id}` = `{card_name: [{tag, source, created_at, version}]}` in `tags.py`; suppression key pattern already exists (`tags.py:175`). Category counts already support exclusion: `analytics/categories.py:380 member_names(card_tags, cat_id, exclude=...)`.
- Physical ownership source of truth: `instances.py` — `instance:{id}` records with `ownership_status ∈ {not_owned, in_mail, in_collection, in_deck}`, printing fields (`set`, `collector_number`, `finish`, `condition`), transitions enforced by `ALLOWED_TRANSITIONS` (`in_deck → in_collection` for freed cards; `in_collection → in_deck` for added cards), indexes `idx:instances_by_{card,status,deck,set}`.
- Inventory reads: `inventory_view.py` joins `instance:*` with `card_meta:*`.
- Export: `deck_export.py export_decklist(name, cards, ...)` — exports take a card list, so "not in decklist" filtering happens *before* export.
- Journal (timeline precedent): `journal:deck:{registry_id}`, `journal:reviews:{registry_id}` LIST (`analytics/journal.py:13-17`).

## 2. Overlay UI shell (DT-1)

Overlay mode of `deck_tune.html` layered over the decklist (Moxfield-style):

```
+--------------------------------------------------------------+
| Deck name · Tune overlay            [Save draft] [Save deck] |
+----------------------+----------------------+----------------+
| Category columns     | Category columns…   | Float panel:   |
| (one per tag)        |                     | Recommendations|
| - card chips (drag)  |                     | (DT-6)         |
| - [+ add card]       |                     |                |
| - [x] remove         |                     |                |
| - ⚑ not-in-decklist  |                     |                |
+----------------------+----------------------+----------------+
```

- Categories = the deck's existing per-card tags (`deck_card_tags:{deck_id}`), one column per tag. Dragging a card chip between columns = `remove_card_tag_from_deck` + `add_card_tag_to_deck` (`app.py:1919/1937` patterns).
- Add/remove card: per-column `[+]` (search box, card added to draft decklist + tag assigned) and per-chip `[x]` (removed from draft).
- Every column header has a "not in decklist" toggle (`⚑`). See §3.2.
- Frontend: HUD component kit only (`templates/partials/hud.html`, `static/css/hud.css`, tokens in `docs/DESIGN_TOKENS.md`). Drag via native HTML5 DnD in a new `static/js/tune_overlay.js`; no new dependencies.

## 3. Data model

All storage is Redis (no SQL). New keys are prefixed `tuneup:` / `journey:` and versioned like `tunerec:schema_version`.

### 3.1 Tune-up draft state — `tuneup:draft:{deck_id}` (JSON, one active per deck)

```json
{
  "id": "uuid", "deck_id": "archidekt:417707", "schema_version": 1,
  "status": "active",
  "created_at": "iso", "updated_at": "iso",
  "base_decklist_hash": "sha1 of card list the draft started from",
  "cards": [
    {"name": "Sol Ring", "qty": 1, "tags": ["Ramp"],
     "printing": {"set": "cmm", "collector_number": "410", "finish": "nonfoil"},
     "in_decklist": true, "needs_purchase": false, "source": "deck|recommendation|manual"}
  ],
  "tag_flags": {"Maybeboard": {"in_decklist": false}},
  "recommendations": ["rec ids, see 3.3"],
  "notes": ""
}
```

- `tuneup:draft:index:{deck_id}` — LIST of past draft ids (archive on apply, same MULTI/EXEC pattern as `tune_store.save_active`, `tune_store.py:113`).
- `tuneup:lock:{deck_id}` — short NX lock around apply (copy `tune_store.py:95-111`).
- **Paper-only save** ("save for later"): write the draft key only. No `instance:*` writes. Draft survives reload; reopening the overlay loads draft over the synced decklist (diff badge if `base_decklist_hash` drifted).

### 3.2 "Not in decklist" tag flags — `tuneup:tag_flags:{deck_id}` (JSON)

`{tag: {"in_decklist": bool, "set_at": iso}}`. When false, cards whose *only* categories are excluded tags are dropped from deck counts (`categories.py member_names(..., exclude=)` precedent) and from the card list passed to `deck_export.export_decklist`. Stored separately from the draft so the flag is stable deck config, not draft state. Structural categories (`Commander`, `Sideboard`, `Maybeboard`, `Considering` per AGENTS.md) are never toggled off by default; `Considering` maps naturally to `in_decklist: false`.

### 3.3 Recommendation entries — `tunerec:rec:{id}` extended payload

Reuse `tune_store` records; each recommendation inside the payload gets:

```json
{"id": "uuid", "kind": "add|cut|printing", "card": "name",
 "printing": {"set": "...", "collector_number": "...", "finish": "nonfoil"},
 "reason": "short explanation text",
 "status": "open|accepted|dismissed|ignored",
 "needs_purchase": false,
 "created_at": "iso", "resolved_at": null}
```

- `status`: extends the existing `decide()` vocabulary (`tuner.py:94`) with `ignored` (don't show again this review) — `dismissed` already exists and is honored in compute (`tuner.py:539-540`).
- **Inventory filter rule** (DT-6): before surfacing an `add`, join against `idx:instances_by_card:{name}` + printing fields. Owned with a free (`in_collection`) instance of *any* printing → suppress. Printing change → treated as a NEW card: if that exact `(set, collector_number)` printing has no owned instance, surface with `needs_purchase: true`.
- Explanations and reconsider/alternative flows (DT-7) write back `reason` and, on "recommend something else", mark the old entry `ignored` and append the replacement with a new id.

### 3.4 Deck journey timeline — `journey:entry:{id}` + `journey:entries:{registry_id}` (LIST, newest first)

```json
{"id": "uuid", "deck_id": "archidekt:417707",
 "kind": "milestone|tune_applied|sync|manual",
 "title": "Swapped 6 cards after tune-up",
 "description": "how the deck played at this point (editable)",
 "diff": {"added": ["..."], "cut": ["..."]},
 "source_draft_id": "tuneup draft id|null",
 "created_at": "iso", "updated_at": "iso", "redrafted_from": null}
```

- Entries are appended by the apply path (§4b) and by manual user entry; editable description + `redrafted_from` points at the regenerated-from entry id (DT-8). LIST capped like `journal:reviews` (`HISTORY_MAX` precedent).

## 4. Save paths

**(a) Save draft ("for later")** — `POST /api/decks/{deck_id}/tuneup/draft`: validates card names against `card_meta:*`, writes `tuneup:draft:{deck_id}`. Paper-only; zero `instance:*` mutation.

**(b) Save to main deck ("apply")** — `POST /api/decks/{deck_id}/tuneup/apply`:
1. Acquire `tuneup:lock:{deck_id}`; compute diff draft vs synced decklist.
2. For each cut card with a bound physical instance: `transition_status(r, id, "in_collection")` (freed back to collection, unused). For each added card: bind a free `in_collection` instance → `in_deck` (prefer exact printing); none free → create `not_owned` instance + `needs_purchase` flag in the response (no fake ownership).
3. In the same MULTI/EXEC: write decklist change, archive the draft to `tuneup:draft:index`, append a `journey` entry with the diff.
4. Response includes `sync_prompt: true` → UI prompts to push to Moxfield/Archidekt via the existing provider write path (`POST /api/sync-tags` / provider-integration-spec `push` capability — see `docs/architecture/provider-integration-spec.md`).

## 5. Component breakdown → child cards (each one coder sitting)

| Card | Scope | In-scope files (verified paths; NEW = create) |
|---|---|---|
| DT-1 | Overlay shell: tag columns, drag, add/remove, not-in-decklist toggle | `templates/deck_tune.html`, `static/js/tune_overlay.js` NEW, `static/css/hud.css` (overlay section), `app.py` (draft GET/POST + tag_flags endpoints) |
| DT-2 | Dual save paths (draft save + apply/swap with instance transitions) | `analytics/tuneup_store.py` NEW (model after `analytics/tune_store.py`), `instances.py` (reuse, no edits expected), `app.py` (apply endpoint) |
| DT-3 | Printing picker; change printing = new card + purchase flag | `static/js/tune_overlay.js`, `app.py` (printing search endpoint over `card_meta:*`/Scryfall cache), `instances.py` lookups only |
| DT-4 | Cart / purchase list from `needs_purchase` flags | `analytics/tuneup_store.py`, `templates/deck_tune.html` (cart drawer), reuse `tcgplayer.py` |
| DT-5 | Cut recommendations in overlay | `analytics/tuner.py` (existing cuts), `static/js/tune_overlay.js` |
| DT-6 | Add-recommendations float panel + inventory filter | `analytics/tuner.py` (filter vs `idx:instances_by_card`), `templates/deck_tune.html`, `static/js/tune_overlay.js` |
| DT-7 | Explanations + reconsider/alternative buttons | `app.py` (why/reconsider endpoints, extend `/tune/why` at `app.py:3613`), `analytics/tuner.py` (`generate_prose` precedent), overlay JS |
| DT-8 | Deck journey timeline (editable, redraftable) | `analytics/journey.py` NEW (model after `analytics/journal.py`), `templates/deck_journal.html` or overlay section, `app.py` (journey endpoints) |

## 6. Risks / boundaries

- **Do not fork the tag system.** Overlay categories MUST reuse `deck_card_tags:{deck_id}`; a parallel tag store will drift from the Categories page. Tag *flags* live in the new `tuneup:tag_flags` key only.
- **Instance transitions must go through `instances.py`** (`transition_status`, `ALLOWED_TRANSITIONS`) — never write `ownership_status` directly or the `idx:*` indexes drift (`instances.py` module docstring).
- `in_deck → in_collection` and `in_collection → in_deck` are the only transitions the apply path needs; both are already legal (`instances.py:23-29`).
- Apply path must be atomic (MULTI/EXEC) — partial swaps corrupt the physical inventory story.
- Existing Tune tab (`/tune` fixes, decisions, recompute) stays untouched; the overlay is additive on the same page.
- Exports filter "not in decklist" cards in `app.py` before calling `deck_export.export_decklist`; do not change the exporter's interface.

## 7. Unverified

- Exact drag-and-drop lib choice left to DT-1 (recommend native HTML5 DnD; no repo precedent found for drag interactions).
- Whether Moxfield push exists today — the provider spec (`docs/architecture/provider-integration-spec.md`) defines the write capability; concrete Moxfield provider availability unverified in this pass.
