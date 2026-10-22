# Auto Tag UI: UX spec

Deck page (`/deck/{name}`), HUD kit. Code: `static/js/auto_tag.js` (+ `.at-*` block at the end of `static/css/hud.css`, hooks in `templates/deck.html`). Data model + endpoints: `docs/AUTO_TAG_DESIGN.md`.

## System

| | |
|---|---|
| Who wrote it | **Auto** = cyan outline chip, `◇`. **Yours** = amber filled chip, `◆`. Glyph + screen-reader text carry it, never hue alone. |
| Chips | `hud-chip` at `--fs-xs`, sentence case. Max **3** per tile / table cell, then a neutral `+N` chip (title lists the rest). |
| Vocabulary | Fixed list, no free text. Roles: Ramp, Draw, Removal, Board Wipe, Counterspell, Tutor, Protection, Recursion, Fixing, Finisher. Themes: Tokens, +1/+1 Counters, Aristocrats, Voltron, Reanimator, Spellslinger, Landfall, Blink, Lifegain, Artifacts. |
| Motion | Chips new from a run fade/rise 3px in, staggered `--stagger` per chip (opacity + transform, `--dur-base`). Spinner is the existing `sync-spin`. All off under `prefers-reduced-motion`. |
| Tokens only | No new colours, radii or shadows. |

## Where things live

```
┌ BRACKET banner ───────────────────────────────────────────────┐
├ TAGS ───────────────────────────── ◇ AUTO ◆ YOURS [⌘ AUTO TAG] ┤  ← new strip, under the deck header
│ DECK   ◆ Aristocrats ×  ◇ Ramp ×  ◇ Tokens ×   [+ Add tag]     │
│ CARDS  Tokens 18 · Ramp 10 · Fixing 9 · Draw 7 · … +4 more     │  ← each count jumps to the filtered decklist
│ ✔ DONE  Added 74 auto tags · 63 of 66 cards tagged · 4 kept    │  ← status line (aria-live)
└────────────────────────────────────────────────────────────────┘
DECKLIST 66 cards                                   VISUAL | LIST
[BY TYPE|BY TAG]  [Tokens 18][Ramp 10][Fixing 9]… [+2 more][Untagged 3]   [Clear filter]
┌────┐ ┌────┐
│art │ │art │   Birds of Paradise
│    │ │    │   CMC 1            ●
└────┘ └────┘   [◆ Ramp]  [◇ Draw]  [◇ Tokens] [+2]
```

## Auto Tag button

| State | Button | Status line |
|---|---|---|
| No tags yet | **Primary** (amber) | "No tags yet. Auto Tag labels each card by role… and the deck by theme." |
| Tags exist | Default | "63 of 66 cards tagged · 74 auto · 4 yours" |
| Running | Disabled, `aria-busy`, spinner, "Tagging…" | `RUNNING` chip + "Tagging 66 cards…" |
| Success | Default | `DONE` chip: "Added N auto tags · X of Y cards tagged · your K tags kept". New chips animate in. |
| 501 (engine not shipped) | Re-enabled | `NOT AVAILABLE YET` chip (amber, not red: nothing is broken) + "Your tags are untouched." |
| Error | Re-enabled | `FAILED` chip + server message + **Retry** |

**Re-run confirm** (only when tags already exist; first run goes straight through): alertdialog, focus on the primary button, Tab is trapped, Esc / Cancel / scrim return focus to the Auto Tag button.

> **Re-run Auto Tag?**
> Auto Tag will replace the **68 auto tags** on this deck with a fresh pass.
> **4 tags** you added or kept stay exactly as they are.
> An auto tag you removed earlier can be applied again.

## Editing tags

- **Per card:** open the card (click, or focus a tile and press **T**; **T** also works inside the open card detail). "Role tags · this deck" sits under the oracle text:
  - `◇` on an auto chip = **keep as yours** (becomes `◆`, survives re-runs).
  - `✕` removes a tag (auto or yours).
  - Picker below lists only tags not already on the card. Adding makes a **yours** tag.
  - Every edit saves immediately. Focus stays inside the editor.
- **Per deck:** same chips in the strip; **+ Add tag** opens the same picker inline (Esc closes).
- Removing an auto tag is a plain delete. The API has no tombstone, so a later re-run can put it back. The confirm dialog says so.

## Filter, group, list

- Filter chips = tags in this deck with card counts (top 8, then "+N more"), plus dashed **Untagged**. One active at a time (`aria-pressed`, amber when on). **Clear filter** appears while active. The decklist heading reads "6 of 66 cards".
- **Group by: Type | Tag** (visual view only; hidden in the list view). By tag: groups by size, **Untagged** last. A card with several tags appears in each of its groups (a note says so).
- State is in the URL (`?tag=Ramp`, `?tag=untagged`, `?group=tag`) so a view can be linked.
- List view gets a sortable **Role tags** column (same chips).
- The strip's "Cards" counts are the summary line (`Ramp 10 · Draw 8 · Removal 7`) and double as filter links.

## Responsive (≤640px)

Strip rows stack (label above), legend moves below the status line, dot separators drop, chip/pin hit areas grow to 28px, the filter bar wraps full width, the group seg stays one row. Verified at 390px: no horizontal scroll with the strip, filter, tiles or the card modal.

## Accessibility

- Auto vs yours: glyph + "(auto)" / "(yours)" for screen readers, not colour alone.
- Everything is a real `<button>` with a visible `--signal` focus ring. Chip `✕` and `◇` have explicit `aria-label`s.
- Status, filter result and errors are `aria-live` / `role="alert"`.
- Cyan on slate and amber on dark amber are the kit's own pairs (AA).

## Screenshots

`docs/design/auto-tag/shots/` (captured against the real storage endpoints with `scripts/auto_tag_preview.py`; the engine there is a keyword stand-in, not the real one).
