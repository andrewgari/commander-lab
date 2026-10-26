# UX Spec: Bulk Decommission

This document specifies the interaction model and UI for selecting and decommissioning multiple decks at once from the Commander Decks list. A functional mock of this design is available in `mock.html`.

## 1. Selection Model

**Entry & Controls:**
- A "Select decks" mode toggle (`S` keyboard shortcut) reveals a selection checkbox on every Physical deck tile.
- **Why explicit mode?** The deck tile is already densely packed with interactive elements (Quick View overlay, Inventory link, Repo badge). Making the entire card a toggle by default would break navigation. Instead, entering selection mode shifts the primary action of the tile to selection.
- A "Select All" / "Clear Selection" toggle is provided at the top of the grid.
- **Filter Behavior:** If the user filters the view (e.g., searches or switches to a different folder), the selection state is preserved. "Select All" applies only to the currently visible Physical decks.

**Visuals:**
- Unselected deck: Checkbox visible but unchecked.
- Selected deck: Checkbox checked, subtle `--signal-dim` outline or border highlight.
- Non-physical decks cannot be selected and appear disabled or faded while selection mode is active.

## 2. Contextual Action Bar

- When at least one deck is selected, a contextual action bar appears.
- Contains the selection count: "X decks selected".
- Contains the primary destructive action: **"Decommission (N)"** button (`.hud-btn--danger` style).
- Contains a "Cancel" button to exit selection mode and clear selections.

## 3. Confirmation Dialog

- Clicking "Decommission (N)" opens a modal dialog (`role="dialog"`, `aria-modal="true"`).
- **Title:** "Decommission N decks?"
- **Warning:** "These decks will be taken apart for good. Their cards will be returned to your collection, and the decks will be moved to Retired. This cannot be undone from here."
- **List:** A scrollable list displaying the names of the selected decks to ensure the user knows exactly what is being destroyed.
- **Action:** A solid `--danger-solid` button reading "Decommission N decks".

## 4. Result Feedback & States

**In-flight (Loading):**
- The "Decommission" button in the dialog changes to "Decommissioning..." and becomes disabled (`aria-disabled="true"`).
- The dialog cannot be closed via Escape or backdrop click while the request is in flight.

**Success:**
- Dialog closes.
- A success toast appears: "N decks decommissioned. X cards returned to your collection."
- The decommissioned deck tiles fade and shrink out (160ms animation, respecting `prefers-reduced-motion`) and are removed from the Physical grid.

**Partial Failure / Errors:**
- If some decks fail, the dialog remains open and updates to a results view: "X of N decommissioned. Y failed."
- A detailed list shows which decks succeeded and which failed, including the error reason (e.g., "Deck was already retired", "Network error").
- A "Close" button replaces the action buttons.
- Successful decks disappear from the grid behind the modal; failed decks remain selected so the user can investigate or retry.

## 5. Accessibility & Keyboard

- **Keyboard:** The `S` key toggles selection mode. `Space` toggles the focused card's checkbox. `Esc` clears selection and exits mode.
- **Screen Readers:**
  - The action bar uses `aria-live="polite"` to announce selection count changes ("3 decks selected").
  - The "Decommission (N)" button uses `aria-describedby` pointing to the count if disabled.
  - Checkboxes use `aria-label="Select [Deck Name]"`.
