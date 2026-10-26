# Sync Controls UX Spec: Update vs Push

## Context
The single "Sync to External" button is being replaced with two distinct, directional actions to clarify exactly which deck is being modified. 
- **Update**: External → commander.lab (overwrites local)
- **Push**: commander.lab → External (overwrites external)

## 1. Placement & Layout
**Location:** `.footer-controls` on the deck page (replacing the current "Sync to External" button).

Instead of a single button, use a flex container `.sync-group` aligned to the right (or alongside other footer buttons).

```html
<div class="sync-group" style="display: flex; flex-direction: column; align-items: flex-end; gap: 0.25rem;">
    <div style="display: flex; gap: 0.75rem;">
        <!-- Update Button -->
        <!-- Push Button -->
    </div>
    <div class="sync-meta" style="font-size: 0.75rem; color: var(--text-muted);">
        Last synced: 2 hours ago
    </div>
</div>
```

## 2. Button Design

### "Update" (External to Local)
- **Label:** `Update from [Provider]` (e.g. "Update from Archidekt")
- **Icon:** Download / Cloud-Arrow-Down (`<svg viewBox="0 0 24 24" ...><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>`)
- **Coloring:** Subtle accent. 
  - Text/Icon: `var(--accent)` (#3b82f6)
  - Background: `rgba(59, 130, 246, 0.05)`
  - Border: `rgba(59, 130, 246, 0.3)`
  - Hover: Background 10% opacity, border `var(--accent)`.
- **Tooltip (`title`):** "Pull the latest changes from [Provider]. This overwrites your local commander.lab deck."

### "Push" (Local to External)
- **Label:** `Push to [Provider]` (e.g. "Push to Archidekt")
- **Icon:** Upload / Cloud-Arrow-Up (`<svg viewBox="0 0 24 24" ...><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>`)
- **Coloring:** Warning / Amber (indicates a destructive remote action).
  - Text/Icon: `#f59e0b` (var(--warning))
  - Background: `rgba(245, 158, 11, 0.05)`
  - Border: `rgba(245, 158, 11, 0.3)`
  - Hover: Background 10% opacity, border `#f59e0b`.
- **Tooltip (`title`):** "Send your current commander.lab deck to [Provider]. This overwrites the external deck."

## 3. States

| State | UI Behavior |
| :--- | :--- |
| **No external deck linked** | Single button reading **"Link External Deck"**. Standard `.btn` styling (no custom colors), link icon. Clicking opens the deck linking modal. |
| **Provider doesn't support push** | Push button is present but `disabled`. Tooltip reads: "[Provider] API does not support pushing changes." |
| **In-progress (Updating/Pushing)** | Button text changes to "Updating..." / "Pushing...". Icon becomes a spinning circle. Button is `disabled`. |
| **Success** | Button text briefly changes to "Updated!" / "Pushed!" (for 2s) with a checkmark icon in `var(--success)` (#10b981). The `.sync-meta` text updates to "Last synced: Just now". |
| **Error** | Button text briefly changes to "Update Failed" / "Push Failed" in `var(--danger)` (#ef4444) with an `X` icon. Standard error toast appears. |

## 4. Confirmation Modals
Clicking either button triggers a confirmation modal to prevent accidental data loss.

### Update Modal
- **Title:** Overwrite local deck?
- **Body:** This will pull the latest changes from **[Provider]** into commander.lab. Any local changes you haven't pushed will be permanently lost.
- **Diff Summary (if available):** A monospace `.diff-summary` block showing `+1 Rhystic Study`, `-1 Cancel`.
- **Primary Action:** "Update from [Provider]" (styled with `.btn-update`).
- **Secondary Action:** "Cancel".

### Push Modal
- **Title:** Overwrite [Provider] deck?
- **Body:** This will replace your external **[Provider]** list with your current commander.lab deck. Any changes made directly on [Provider] since the last sync will be lost.
- **Diff Summary (if available):** `.diff-summary` block showing expected changes.
- **Primary Action:** "Push to [Provider]" (styled with `.btn-push`).
- **Secondary Action:** "Cancel".

## CSS Additions

```css
.btn-update {
    color: var(--accent);
    border-color: rgba(59, 130, 246, 0.3);
    background: rgba(59, 130, 246, 0.05);
}
.btn-update:hover:not(:disabled) {
    background: rgba(59, 130, 246, 0.1);
    border-color: var(--accent);
}

.btn-push {
    color: #f59e0b; /* warning */
    border-color: rgba(245, 158, 11, 0.3);
    background: rgba(245, 158, 11, 0.05);
}
.btn-push:hover:not(:disabled) {
    background: rgba(245, 158, 11, 0.1);
    border-color: #f59e0b;
}

.diff-summary {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.75rem;
    margin-bottom: 1.5rem;
    font-family: monospace;
    font-size: 0.8rem;
}
.diff-add { color: #10b981; }
.diff-sub { color: #ef4444; }
```
