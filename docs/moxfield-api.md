# Moxfield Deck API — Integration Spec

Status: unofficial / reverse-engineered from the public web app bundle
(`moxfield.5019acd34631b361c7da.js`, build `moxfield-web-20260930.4`) and
live testing against `https://moxfield.com/decks/*` on 2026-10-01.
Moxfield publishes no official public API or API keys. Everything below is
the same API the moxfield.com SPA calls from the browser. It can change
without notice — treat this as a best-effort integration, not a stable
contract.

## 1. Base URLs

| Service | Base URL | Purpose |
|---|---|---|
| Primary API | `https://api2.moxfield.com` | decks, cards, account, auth |
| Social API | `https://social-api.moxfield.com` | comments/social feed (not needed for deck sync) |

All deck/card/account endpoints below are relative to `https://api2.moxfield.com`.

### Cloudflare / bot protection

- The API sits behind Cloudflare. **A default/empty `User-Agent` (e.g. bare
  `curl` or `urllib` defaults) is rejected with `403 Forbidden`.** Any
  non-empty, plausible `User-Agent` string works — tested with
  `curl/8.5.0` (blocked), `Python-urllib/3.13` (blocked),
  `decksync/1.0` (200 OK), and a full desktop Chrome UA (200 OK). In
  practice: **set a custom, identifiable User-Agent and avoid the empty
  default** of your HTTP client.
- No cookies/session required for read-only public deck GETs.
- The web app also sends `x-moxfield-version: <9-digit date code>` (derived
  from the build id, e.g. `moxfield-web-20260930.4` → `260930004`) on every
  request. Not observed to be required for API calls to succeed, but worth
  mirroring to look like the real client.
- Sign-in (`/v2/account/token`) is additionally guarded by a Cloudflare
  Turnstile challenge (sitekey `0x4AAAAAAA03TNFQFNsvAhca`) embedded in the
  moxfield.com login page. This means **programmatic username/password
  login is not practical** without solving a Turnstile challenge (see §3).

## 2. Reading a deck (no auth required)

### 2.1 `GET /v3/decks/all/{publicId}` (preferred — "boards" shape)

```bash
curl -s 'https://api2.moxfield.com/v3/decks/all/IpCYKyFFjkGu_aKeQjcZVg' \
  -H 'User-Agent: decksync/1.0' \
  -H 'Accept: application/json'
```

Response (trimmed):

```jsonc
{
  "id": "MBV4O6",
  "publicId": "IpCYKyFFjkGu_aKeQjcZVg",
  "name": "Poly Couples Only",
  "format": "commander",
  "visibility": "public",
  "version": 56,                 // bump on every server-side mutation; echo back on writes (see §4)
  "boards": {
    "mainboard":   { "count": 91, "cards": { "<uniqueCardId>": { ...cardEntry } } },
    "sideboard":   { "count": 0,  "cards": {} },
    "maybeboard":  { "count": 0,  "cards": {} },
    "commanders":  { "count": 1,  "cards": { "YvBg8": { ...cardEntry } } },
    "companions":  { "count": 0,  "cards": {} },
    "signatureSpells": { "count": 0, "cards": {} },
    "attractions": { "count": 0,  "cards": {} },
    "stickers":    { "count": 0,  "cards": {} },
    "contraptions":{ "count": 0,  "cards": {} },
    "planes":      { "count": 0,  "cards": {} },
    "schemes":     { "count": 0,  "cards": {} },
    "tokens":      { "count": 0,  "cards": {} }
  }
}
```

Board keys present for every deck format (commander decks additionally use
`commanders`, `companions`, `signatureSpells`; non-EDH formats generally
only populate `mainboard`/`sideboard`/`maybeboard`).

`cardEntry` object (key = `uniqueCardId`, a Moxfield-internal id that is
**stable per printing+finish combination within the deck**, not the global
card id):

```jsonc
{
  "quantity": 3,
  "boardType": "mainboard",
  "finish": "nonFoil",        // "nonFoil" | "foil" | "etched" (authoritative finish field)
  "isFoil": false,            // legacy/derived mirror of finish — don't rely on for writes
  "isAlter": false,
  "isProxy": false,
  "card": {
    "id": "Lw3Z4",                                   // Moxfield's internal card (printing) id — use for ADD/UPDATE/REMOVE
    "uniqueCardId": "kaN49",                          // = key under boards[].cards
    "scryfall_id": "7014b9fc-a906-4ffd-a482-22ba8dbe3b4a",
    "set": "war",
    "set_name": "War of the Spark",
    "name": "Island",
    "cn": "253",                                      // collector number
    "type_line": "Basic Land — Island",
    "colors": [], "color_identity": ["U"],
    "rarity": "common", "lang": "en"
    // ...large block of scryfall-style metadata (legalities, prices, images) follows
  }
}
```

Card identity you need for sync logic: `card.scryfall_id` (cross-platform
join key), `card.set` + `card.cn` (exact printing), `card.id` (Moxfield's
printing id, required for write calls), `quantity`, `finish`
(`nonFoil`/`foil`/`etched`).

### 2.2 `GET /v2/decks/all/{publicId}` (legacy — flat board shape)

Same deck, older response shape: top-level keys `mainboard`, `sideboard`,
`maybeboard`, `commanders`, `companions`, `attractions`, `stickers`,
`signatureSpells` directly on the deck object (no `boards` wrapper), plus
`version` differs numerically from the v3 response's `version` (they are
independent counters — **do not mix version numbers across v2/v3 calls**).
Card entry shape is identical to v3's `cardEntry`. Prefer v3 for new
integrations; v2 exists for back-compat with older exports.

### 2.3 `GET /v3/decks/all/alt/{publicId}`

Same as 2.1 but served from an alternate read replica/cache tier
(`GET_PUBLIC_DECK` action with `useAlt=true` in the web client — used by
the SPA to retry after a write when the primary replica hasn't caught up
yet). Same response shape.

### 2.4 Pagination / size limits on read

No pagination — `boards[x].cards` is returned in full in one response.
Tested deck had 91 mainboard entries with no truncation. Expect this to
scale fine into the hundreds of unique cards; no documented hard cap.

## 3. Authentication (write access)

### 3.1 Token-based sign-in — NOT practically usable by a script

```
POST /v2/account/token
Body: { "userName": "...", "password": "...", "token": "<turnstile-token>" }
```

The `token` field is a Cloudflare Turnstile solution token obtained by
rendering the `#turnstile-container` widget in a real browser session and
waiting for its `callback`. There is no headless/programmatic path to
obtain a valid Turnstile token without either (a) a real browser session
a human completes, or (b) a third-party CAPTCHA-solving service. **Do not
attempt to automate this login flow** — it is explicitly bot-gated.

Response on success (`SIGN_IN` action):
```jsonc
{ "access_token": "<jwt>", "refresh_token"?: "...", "userId": "...", "expiration": "<iso8601>" }
```

Token refresh: `POST /v1/account/token/refresh`, `authenticate: true`,
body `{ "userId": "<id>", "isAppLogin": false }` — this call itself needs
`withCredentials: true` (a valid session cookie), so it only works
following a real interactive login, not standalone.

### 3.2 Realistic workaround: authenticated session replay

Because the sign-in itself requires solving Turnstile, the practical path
for programmatic write access is:

1. A human logs into moxfield.com once in a real browser (Hermes browser
   tool, or any browser with devtools).
2. Capture the resulting **bearer access token** (`Authorization: Bearer
   <jwt>`) and/or the **session cookie** from a subsequent authenticated
   XHR (open devtools → Network → any `/v2/decks/...` request while
   logged in → copy the `Authorization` header value). The JWT is short
   lived (see `expiration` in the sign-in response, typically a few hours)
   so this must be refreshed periodically — refreshing also requires a
   valid session cookie (`withCredentials: true`), which browsers keep
   automatically but a script must capture and resend explicitly
   (`Cookie: <moxfield session cookie>`).
3. Store the captured `access_token` (and cookie jar, for refresh) as
   `MOXFIELD_TOKEN` env var / secret, and send it as:
   ```
   Authorization: Bearer <MOXFIELD_TOKEN>
   ```
   on every authenticated request (every endpoint marked
   `authenticate:true` below).
4. When a call 401s, treat the token as expired and re-prompt a human to
   refresh it (there is no way for an unattended script to refresh past
   a dead session cookie, since that also needs a prior interactive
   login). This is the documented trade-off for any Moxfield write
   integration: **it cannot be made fully unattended**; a human must
   periodically re-auth in a browser.

There is no official API-key / OAuth app flow — Moxfield has not published
one as of 2026-10-01.

### 3.3 Required headers on every authenticated (write) request

```
Authorization: Bearer <access_token>
X-Public-Deck-ID: <deck.publicId>
X-Deck-Version: <deck.version>          # from the most recent GET; see §5 optimistic concurrency
User-Agent: <non-empty UA>
```

`X-Session-User: <userId>` is also sent by the web client on every
request when authenticated but does not appear to be required — the
bearer token is sufficient. Include it anyway for parity if you have the
user id.

### 3.4 Rate limits

No documented/published numeric rate limit. The client surfaces a generic
"Woah, slow down!" UI message keyed off `isRateLimit` on `429` responses:
> "You are performing too many actions in a short period of time. Please
> wait about 30 secs and try again."

Treat any `429` as: back off ~30s, retry with exponential backoff, cap
retries (e.g. 5), honor `Retry-After` if present (not confirmed to be
sent, so don't rely on it being there).

## 4. Writing a deck — the available mutation endpoints

All are authenticated (`authenticate: true` ⇒ needs the headers in §3.3).
Base: `https://api2.moxfield.com`.

| Action | Method & URL | Body | Notes |
|---|---|---|---|
| Add card to board | `POST /v2/decks/{deckId}/cards/{board}` | `{ cardId, quantity, finish?, usePrefPrinting }` | `quantity` is clamped client-side to `min(99, qty)`. `cardId` = Moxfield printing id (`card.id`, **not** `scryfall_id`). |
| Set exact quantity in board | `PUT /v2/decks/{deckId}/cards/{board}/{cardId}` | `{ quantity, printingData? }` | Used for both increment and decrement by the UI (computes `quantity:currentQty±1` client-side and PUTs the new absolute value). |
| Remove card entirely (qty→0) | `DELETE /v2/decks/{deckId}/cards/{board}/{cardId}` | — | Used when quantity would drop to 0. |
| Move card between boards | `POST /v4/decks/{deckId}/{sourceBoard}/cards/{cardId}/move-to/{destBoard}?quantity={n}` | `{ sourcePrintingData, targetPrintingData }` | |
| Change printing/edition of a card | `PUT /v2/decks/{deckId}/cards/{cardId}/edition` | `{ newEditionCardId, finish, isAlter }` | |
| Set finish (foil/nonfoil/etched) only | `POST /v2/decks/{deckId}/cards/{cardId}/finish` | `{ finish }` | |
| Set commanders | `PUT /v4/decks/{deckId}/commanders` | `{ commanderCardId, partnerCardId, commanderSignatureSpellCardId, partnerSignatureSpellCardId, removeNewFromOtherBoards: true, returnOldToMainboard: true }` | |
| Set/unset companion | `PUT` / `DELETE /v2/decks/{deckId}/cards/companions` | `{ cardId }` (PUT only) | |
| Set per-card tags | `PUT /v2/decks/{deckId}/cards/{cardId}/tags` | `{ tags: [...] }` | |
| **Bulk text import (replace or merge)** | `POST /v3/decks/{deckId}/import` | `{ importText, replaceAll, pricingProvider?, usePrefPrintings: true }` | **Recommended path for full-deck sync** — see §4.1. |
| Bulk file import | `POST /v3/decks/{deckId}/import-file` | multipart file body; query params `pricingProvider`, `replaceAll`, `usePrefPrintings` | Same engine as text import, for `.txt`/`.dek`/etc file uploads. |
| **Bulk structured board replace** | `PUT /v3/decks/{deckId}/bulk-edit` | `{ boards: {...}, pricingProvider?, usePrefPrintings: true, ignoreFlavorNames?, allowMultiplePrintings? }` | Full per-board structured replace — see §4.2. Requires `X-Deck-Version` to match current server version or it is rejected. |
| Preview a bulk edit before saving | `GET /v2/decks/{publicId}/bulk-edit?ignoreFlavorNames=&allowMultiplePrintings=` | — | Returns a preview/diff; **401 Unauthorized without a valid bearer token** (confirmed live). |
| Update deck metadata (name/desc/visibility/commander/format) | `PUT /v2/decks/{deckId}` | `{ name, format, description, visibility, mainCardId, ... }` | |
| Delete deck | `DELETE /v1/decks/{deckId}` | — | |
| Clone deck | `POST /v2/decks/{publicId}/clone` | `{ name, includePrimer, includeTags }` | |
| Export deck (text) | `GET /v2/decks/all/{publicId}/export?format={fmt}&...` | — | read-only, returns `text/plain` |
| Remote preview (import by URL, e.g. mirror another Moxfield deck) | `POST /v2/decks/remote-preview` | `{ remoteUrl, pricingProvider }` | Used by the "Import from Moxfield URL" dialog; fetches another deck's board contents to seed a local import, not useful for syncing *your own* target deck. |

### 4.1 Recommended write path: bulk text import

`POST /v3/decks/{deckId}/import`

```bash
curl -s -X POST 'https://api2.moxfield.com/v3/decks/MBV4O6/import' \
  -H 'Authorization: Bearer <MOXFIELD_TOKEN>' \
  -H 'X-Public-Deck-ID: IpCYKyFFjkGu_aKeQjcZVg' \
  -H 'X-Deck-Version: 56' \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: decksync/1.0' \
  -d '{
    "importText": "1 Sol Ring\n1 Command Tower\n1 Jace, Multiverse Architect *CMDR*\n",
    "replaceAll": true,
    "usePrefPrintings": true
  }'
```

- `importText` uses Moxfield's text decklist grammar: `<qty> <name> [(SET) [cn]] [*F*|*E*] [*CMDR*]` per line, blank line or `Sideboard`/`Maybeboard`/`Commander` section headers to switch target board (same grammar accepted by the "Edit deck as text" UI and the export endpoint — round-trippable with `GET /v2/decks/all/{id}/export?format=text`).
- `replaceAll: true` wipes and replaces the whole deck's boards with the
  parsed import text (closest thing to "make remote match local" in one
  call). `replaceAll: false` merges/adds on top of the existing list.
- `usePrefPrintings: true` lets Moxfield auto-pick a printing per
  card name rather than requiring an exact set/cn — set this `false` and
  include `(SET) cn` on every line if you need exact-printing fidelity
  (important for foil/alt-art matching).
- This is a **text-grammar interface, not strict JSON board data** — the
  tradeoff is you lose per-card structured fields you don't encode in the
  line syntax (tags, isAlter/isProxy flags aren't expressible here; use
  the per-card PUT endpoints for those after the bulk import if needed).
- Response: updated deck object (same shape as GET) plus
  `isSuccessful: bool`, `errors: [...]` for any lines that failed to
  resolve to a card, and updated `X-Deck-Version` response header — capture
  it for the next write.

### 4.2 Alternative: structured bulk-edit (closer to the GET `boards` shape)

`PUT /v3/decks/{deckId}/bulk-edit`

```bash
curl -s -X PUT 'https://api2.moxfield.com/v3/decks/MBV4O6/bulk-edit' \
  -H 'Authorization: Bearer <MOXFIELD_TOKEN>' \
  -H 'X-Public-Deck-ID: IpCYKyFFjkGu_aKeQjcZVg' \
  -H 'X-Deck-Version: 56' \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: decksync/1.0' \
  -d '{
    "boards": {
      "mainboard": { "count": 2, "cards": {
        "<uniqueCardId-or-new>": { "quantity": 1, "boardType": "mainboard", "finish": "nonFoil", "card": { "id": "<moxfieldCardId>" } }
      }},
      "commanders": { "count": 1, "cards": { "...": {"...": "..."} } }
    },
    "usePrefPrintings": true,
    "allowMultiplePrintings": false,
    "ignoreFlavorNames": false
  }'
```

This mirrors the structured `boards` object from the v3 GET response, so
in principle you can take your canonical `Deck` model, build the same
nested `boards`/`cards` shape, and PUT the whole thing. **Not fully
verified live** (requires an authenticated token we did not have during
this research pass — the anonymous preview `GET .../bulk-edit` call
returned `401 Unauthorized`, confirming it needs a valid bearer token).
Treat the exact accepted card-entry sub-shape as **best-effort inferred
from the client source**, cross-check against the `cardEntry` shape in
§2.1, and validate once real credentials are available before relying on
it in production. If it diverges, fall back to §4.1 (bulk text import),
which is the lower-risk, better-understood path.

## 5. Optimistic concurrency (`X-Deck-Version`)

Every authenticated write expects `X-Deck-Version: <n>` matching the
version last read via GET. The server increments `version` on every
successful mutation and the response carries the new version so the next
write can chain off it. If you're batching multiple sequential writes,
**re-read the returned deck/version after each call** rather than reusing
a stale version from before the batch — mismatched versions are expected
to produce a conflict/error (exact error shape not confirmed live; expect
a `4xx` with a body indicating staleness — treat any non-2xx here as
"re-GET and retry once").

## 6. Error codes observed / expected

| Status | Meaning | When |
|---|---|---|
| 200 | OK | successful GET/mutation |
| 401 | Unauthorized | missing/expired/invalid bearer token on an `authenticate:true` endpoint |
| 403 | Forbidden | blocked at Cloudflare edge — almost always an empty/bot-flagged `User-Agent`, not an app-level auth issue |
| 404 | Not found | bad `publicId`/`deckId`, or deck set to private and you're unauthenticated/not the owner |
| 429 | Rate limited | too many requests in a short window; client advises ~30s backoff |
| 5xx | Server error | retry with backoff |

## 7. Card identity fields — cross-platform mapping cheat sheet

| Field | Where | Use for |
|---|---|---|
| `card.scryfall_id` | every card entry | primary join key against Archidekt/Scryfall/your canonical model |
| `card.set` + `card.cn` | every card entry | exact printing fallback when scryfall_id is absent/stale |
| `card.id` | every card entry | **required** as `cardId` in Moxfield ADD/UPDATE/move/edition write calls — Moxfield-internal, not portable |
| `uniqueCardId` (dict key under `boards[x].cards`) | GET response only | Moxfield's per-deck-entry identity (printing+finish combo); needed to address a specific row, e.g. in bulk-edit payloads |
| `quantity` | card entry | count in that board |
| `finish` | card entry | `"nonFoil" \| "foil" \| "etched"` — authoritative; `isFoil` is a derived legacy boolean, don't write to it |
| `boardType` | card entry | redundant with the board name it's nested under; present for convenience |

## 8. Constraints summary

- Quantity per card per board clamped client-side to 99 (`Math.min(99,
  qty)`); unknown if server enforces independently — assume yes.
- No documented max cards-per-request / payload-size limit for
  `/import` or `/bulk-edit`; tested deck (91 mainboard cards) round-trips
  fine via GET. Assume low-to-mid thousands of lines is safe for
  `importText`; batch very large collections defensively if unsure.
- No official pagination on deck card reads — whole board comes back in
  one response.
- `X-Deck-Version` must be current or the write is expected to fail —
  build retry logic that re-fetches on any write error before retrying.

## 9. Minimal sync client design implication

Given §3's auth constraints, a realistic `MoxfieldTarget.apply(diff)`
implementation should:

1. Require `MOXFIELD_TOKEN` (and optionally `MOXFIELD_COOKIE` for future
   refresh support) from the environment; fail with a clear
   "re-authenticate in browser" error if missing/expired (401).
2. `fetch_deck()` via `GET /v3/decks/all/{publicId}`, map
   `boards.{mainboard,sideboard,maybeboard,commanders,companions,...}` →
   canonical boards, using `card.scryfall_id` as the join key and
   `finish`/`quantity` as the mutable fields.
3. `apply(diff)`: build a full `importText` decklist representing the
   desired end state per board (one send per board, or a combined import
   using section headers) and call `POST /v3/decks/{deckId}/import` with
   `replaceAll: true`, `usePrefPrintings: true` — this is the only write
   path that doesn't require per-card Moxfield-internal ids up front,
   which is important since the local canonical model won't have
   Moxfield's `card.id`/`uniqueCardId` for cards that don't yet exist in
   the remote deck. Document in code that this is a coarse "replace the
   whole deck" approach, not a minimal-diff PUT, and that per-card
   `tags`/`isAlter`/`isProxy` flags are not expressible via this path and
   would need follow-up PUT calls if the sync needs them.
