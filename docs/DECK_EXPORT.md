# Deck export (create new decks on Moxfield / Archidekt)

Explicit user action only; never invoked by sync, timers or page loads.

- `POST /api/export/deck` body `{name, cards:[{name,quantity}], commanders, providers, description, private, dry_run}`
- `POST /api/reconstruct/{session_id}/drafts/{draft_index}/export` body `{providers, description, private, dry_run}`
- Response: `results[provider].status` is `created | dry_run | not_configured | failed`. HTTP 200 if all succeeded, 207 if any did not, 400 for invalid input.
- Credentials: `MOXFIELD_TOKEN`, `ARCHIDEKT_TOKEN` (same as push). Missing token => `not_configured`.
- Creation is two steps per provider (create empty deck, then fill via the existing push path). If fill fails the empty deck remains remotely; the error carries its URL.
- Moxfield `POST /v2/decks` and Archidekt `POST /api/decks/v2/` are unofficial and unverified against live accounts.
