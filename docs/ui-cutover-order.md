# UI Cutover Order: legacy Jinja HUD -> SvelteKit

Status: proposal (STA-8), 2026-10-27. Time-boxed plan for retiring the legacy
Jinja UI in favor of `src/web/` (SvelteKit), one route group at a time, with
the legacy UI as the fallback until each step verifies.

## Principles

- Both UIs run side-by-side against the same FastAPI backend; the API is the
  contract, so no backend changes are required to cut a route over.
- Cut by route group, not by feature rewrite: the SvelteKit page is live at a
  new path first, verified against the Jinja page, then the legacy route is
  removed in a separate, revertible step.
- Each step is one PR and is independently shippable and revertible.
- Time-box: 1-2 days per step; if a step exceeds its box, stop and re-scope
  rather than letting the two UIs drift further apart.

## Order (smallest risk first)

| Step | Route group    | Why this order                                        | Box   |
| ---- | -------------- | ----------------------------------------------------- | ----- |
| 1    | `/settings`    | Lowest traffic, no card data; proves the shell/layout | 1 day |
| 2    | `/tags`        | Read-mostly; exercises inventory-adjacent API reads   | 1 day |
| 3    | `/inventory`   | High read volume; validates list/pagination parity    | 2 days|
| 4    | `/decks`       | Core list view; entry point of the app                | 1 day |
| 5    | `/deck/{name}` | Most complex page (detail, tabs); done last           | 2 days|

Total: ~1 week of focused work; at most 2 weeks if every box is fully spent.

## Per-step checklist

1. SvelteKit route serves the same data from the same API endpoints.
2. Visual/interaction check against the Jinja page (screenshots side by side).
3. Redirect or nav switch to the SvelteKit route; legacy template kept one
   step behind as instant rollback.
4. Remove the legacy template + static assets for that group in a follow-up PR.

## Non-goals

- Redesigning pages during cutover (parity first; redesigns are separate PRs).
- Touching the `/styleguide` HUD reference until step 5 completes.
