# Deck identity, composition, pillar fit, weak performers (AN-3)

Spec: docs/ANALYTICS_SPEC.md Layer 1 ("Composition", "Theme", local HF) and
Layer 3 ("weak performer"). UI contract: docs/design/analytics/UX_SPEC.md
§3.1.7, §3.1.9, §3.2 and §6. JSON only, no UI yet (AN-4 builds it).

Modules (all under `analytics/`):

| module | what | I/O |
|---|---|---|
| `composition.py` | template + deterministic classifier | Redis (override) |
| `identity.py` | thesis + pillars store, edit contract, LLM generator | Redis, LLM chain |
| `embeddings.py` | local bge-small ONNX embedder (CPU) | model files |
| `pillar_fit.py` | card ↔ pillar scoring, buckets | Redis (vector cache) |
| `edhrec_inclusion.py` | per-card EDHREC inclusion for the commander | Redis cache, EDHREC |
| `weak_performers.py` | computed weak score | none (pure) |
| `insights.py` | glue: card text lookup, reads, generate, rebuild hook | Redis |

## Composition vs template

Rows: Lands · Ramp · Card advantage · Targeted disruption · Mass disruption · Plan.

**Default template: Command Zone 2025 = 38 / 10 / 12 / 12 / 6 / 22.** Source:
The Command Zone ep. 658, "Commander Deckbuilding Template for the New Era"
(2025-02-18, show notes on EDHREC). Secondary write-ups of the episode list
38 lands, 10 ramp, 12 card advantage, 12 targeted disruption and 6 mass
disruption. ANALYTICS_SPEC said "plan ~25" (some write-ups say "~30 plan
cards", counting the commander and double-counted cards). The designer's
UX_SPEC confirms 22, and 22 is what makes the template total 100, so the
default plan target is 22.

Classification is deterministic: type line + oracle text, reminder text
stripped (`composition.classify_card`). A card can count in two rows (Vilis:
card advantage + targeted). Plan = in none of the five functional rows.
Counts are by quantity, commander included. A land is only ever `lands`.
Ramp only counts mana for you (Path to Exile's basic is the opponent's).
Tutors aren't card advantage (CZ 2025 rule).

Delta status (UX_SPEC D8): within ±1 `ok`; short by 2 or more `short`
(amber); over by 2 or more `over` (neutral).

Override: `composition:template:{registry_id}` holds only the overridden
rows. Saving a row equal to its default drops it, so saving all defaults is
a reset.

## Identity: thesis + pillars

Stored at `identity:deck:{registry_id}` (no TTL). Pillar states:

| state | meaning | re-sync |
|---|---|---|
| `draft` | proposed by the generator | never changed |
| `accepted` | kept, unpinned | never changed (a later card may allow rename/merge) |
| `pinned` | Edit or Keep pins automatically | never changed |
| `rejected` | in the Rejected tray | never proposed again |

**Generator.** `analytics.identity.generate_identity` sends the commander,
every non-land card (name, type, oracle text) and the AN-2 engine names to
the AN-1 `LLMChain.generate_json` (Gemini first, Nous fallback) with a JSON
schema. The LLM returns `{thesis: {text, emphasis}, pillars: [{name,
description}]}` and writes prose only; every score is computed.

- **First run:** the thesis becomes a draft, plus 3-5 draft pillars.
- **Re-sync:** the prompt lists the existing and REJECTED pillars and asks
  only for new ones. Each proposal is dropped if its name matches an existing
  pillar (live or rejected), or its embedding cosine to one is at least 0.85
  (bge-small: distinct pillars sit at 0.48-0.59, a reworded duplicate at 0.92).
  Survivors are added as drafts (`origin: resync`) up to 5 live pillars. The
  thesis text is never replaced: the new draft goes into `thesis.draft_text`
  next to it. `proposal = {new, dropped[], at}` drives the UI banner.
- **Triggers:** an explicit `POST .../identity/generate`, or the
  interaction-graph rebuild (sync / manual / nightly). A rebuild regenerates
  only decks whose decklist changed (`cards_sig`), at most
  `IDENTITY_MAX_DECKS_PER_REBUILD` (default 10) per rebuild; the rest wait for
  the next one. `IDENTITY_ON_REBUILD=0` turns the hook off.
- **Cost log:** every attempt is logged as `{purpose: "pillars", deck,
  provider, model, input_tokens, output_tokens, cost_usd}`, in the rebuild's
  run, or in a separate `trigger: "identity"` run for explicit generations.
  Both are visible in `GET /api/interactions/runs`.

## Pillar fit (local embeddings)

Model: **BAAI bge-small-en-v1.5**, an int8 ONNX export (Xenova, pinned
revision `ea104da…`, sha256-checked, 34 MB). It runs on CPU through
`onnxruntime` + HF `tokenizers`, with no torch, no GPU and no Framework
dependency. The Dockerfile downloads it at build time into
`/opt/models/bge-small-en-v1.5` (its own cached layer) via
`scripts/fetch_embedding_model.py`. The model is loaded lazily on first use,
in about 0.2 s.

- **Card text:** `"{name}. {type line}. {oracle text}"`. Pillar text:
  `"{name}: {description}"`, with the bge query prefix. The description is
  what cards are embedded against.
- **Fit:** fit = best pillar cosine, rescaled so 0.48 → 0 and 0.78 → 1.
  Calibrated on Holy Hellfire: card cosines span 0.49-0.77.
- **Buckets:** `pillar` when the best cosine is at least 0.62 (not a land);
  else `glue` when the card is in a functional composition row; else
  `generic`. Counts are by quantity. `on_plan_pct = (pillar + glue) / total`.
- **Cache:** card vectors live in the hash `embeddings:card:{model_id}`,
  keyed by sha1(text)[:16], so a card is embedded once per model. Pillars are
  re-embedded on every read (at most 5), so an edit rescores immediately.

## EDHREC inclusion

`edhrec:commander:{slug}` caches the parsed commander page. It is fresh for
`EDHREC_MAX_AGE_DAYS` (default 7); after that the stale copy is served if
EDHREC is down. inclusion = num_decks / potential_decks. A card not listed on
the page counts as 0% for weak performers. The histogram buckets are <5,
5–14, 15–29, 30–49, 50–74 and 75+ (%), over non-land cards.

## Weak performers (computed only)

Three equally weighted parts, each 0..1, where 1 is weak:

- **edges** = `1 - min(1, degree / p75)`, where degree counts AN-1 deck-graph
  edges that have at least one non-mana/lands reason, and p75 is that
  degree's 75th percentile over the scored cards;
- **pillar** = `1 - fit` (0 when the deck has no live pillars yet);
- **edhrec** = `1 - min(1, inclusion / 0.5)`.

weak_score is the mean of the three. Lands and commanders aren't scored. Pet
cards (`journal:deck:{id}.pet_cards`, written by the Journal card) are
flagged, sorted last, and the weakest pet becomes the single `pet_notice`.

## API

    GET    /api/decks/{id}/composition
    PUT    /api/decks/{id}/composition/template          {"lands": 36, ...}
    DELETE /api/decks/{id}/composition/template
    GET    /api/decks/{id}/identity[?cards=false]        thesis, pillars (+count, top_cards), rejected, fit, proposal
    POST   /api/decks/{id}/identity/generate             LLM draft / re-sync proposal (502 if every provider fails)
    PUT    /api/decks/{id}/identity/thesis               {"text", "emphasis"} | {"action": "accept_draft"}
    POST   /api/decks/{id}/identity/pillars              {"name", "description"}  (user pillar, pinned)
    PATCH  /api/decks/{id}/identity/pillars/{pid}        {"action": pin|unpin|keep|reject|restore} and/or {"name","description"}
    PUT    /api/decks/{id}/identity/pillars/order        {"order": [pid, ...]}
    GET    /api/decks/{id}/edhrec[?refresh=true]
    GET    /api/decks/{id}/weak-performers[?limit=N]

`{id}` can be the id, registry_id or deck name.
