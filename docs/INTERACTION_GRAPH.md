# Card interaction graph (AN-1)

Every card is tagged with the resources it PRODUCES and CONSUMES. When card A
produces something card B consumes, the graph gets an edge A -> B. Each edge
carries its reasons (the shared resources) and a weight (how many reasons).
Code: `analytics/interactions/`. Spec: `docs/ANALYTICS_SPEC.md` Layer 1.

## Vocabulary

The vocabulary is fixed in `analytics/interactions/vocab.py` (`GET /api/interactions/vocabulary`):
tokens, sacrifice, death, etb, counters, graveyard, mana, cards, life_gain,
life_loss, attacks, spells, artifacts, enchantments, lands. The LLM can only
answer with these keys, and anything else it returns is dropped. If you add,
remove or redefine a key, bump `VOCAB_VERSION`. Cached tags from an older
version are then retagged on the next rebuild.

## Tagging pipeline (`tagger.py`)

1. **Cache.** Look up `card_interactions:{name}`, which has no TTL. A cached card
   on the current vocab version costs nothing.
2. **Scryfall.** `POST /cards/collection` (75 names per request) fetches the
   oracle text of unseen cards. If Scryfall doesn't know a name, the card falls
   back to `card_meta:*`.
3. **Deterministic pass.** This is the union of:
   - the Scryfall Tagger index: one `otag:` search per resource side, built from
     the otags mapped in `vocab.py` and cached for a week in `interactions:otag_index`;
   - oracle-text rules (`oracle_rules.py`).
   Basic lands, plain mana lands and vanilla/keyword-only cards are stored as
   `source: none` and never go to the LLM.
4. **LLM pass.** Cards still untagged go to the LLM in batches of 25
   (`llm.py`, `LLMChain`):
   - Gemini is primary (`GOOGLE_API_KEY` / `GEMINI_API_KEY`). The model is
     `gemini-3.8-flash`; override it with `INTERACTIONS_GEMINI_MODEL`. Gemini
     2.5 models return 404 for new keys.
   - Nous is the fallback (`NOUS_API_KEY`, `INTERACTIONS_NOUS_MODEL`, default
     `Hermes-4-70B`, OpenAI-compatible).
   - Anthropic is an empty slot (`AnthropicProvider`) that is never configured.
   If every provider fails, the card is stored with `llm_pending: true` and
   retried on the next rebuild. Nothing is lost.

Each tag record keeps evidence (`otag`, `oracle` and `llm` contributions), so
you can see why a card has a given tag.

## Graphs (`graph.py`)

- **Deck graph** (`interaction_graph:deck:{registry_id}`): nodes (produces,
  consumes, in/out/total degree, weighted degree, per-reason in/out counts),
  edges (`source`, `target`, `reasons`, `weight`, `label`, `explanations`),
  resource labels, and the 10 most-connected cards (`top_connected`).
- **Collection graph** (`interaction_graph:collection`): owned cards only
  (`in_deck`, `in_collection`, `in_mail`). It holds ~300k edges, so it stores
  nodes with exact degrees plus a per-resource producer/consumer index. Edges
  for a single card are derived on demand (`?card=`).

## Rebuild triggers (no polling)

| Trigger | Where |
| --- | --- |
| sync | `POST /api/decks/{id}/sync` (that deck), `POST /api/linked-accounts/{id}/sync`, `POST /api/linked-accounts/sync-all`, `POST /api/import` (that deck) |
| manual | `POST /api/interactions/refresh` (background, 202; `?wait=true` runs inline and returns the run log), `POST /api/decks/{id}/interactions/refresh` |
| nightly | an app-startup thread that sleeps until `INTERACTIONS_NIGHTLY_HOUR_UTC` (default 8, i.e. 04:00 EDT), rebuilds, and repeats. It sleeps once per day and doesn't poll. Disable with `INTERACTIONS_NIGHTLY=0`. |

Rebuilds are single-flight (`interactions:rebuild_lock`). A trigger that
arrives while a rebuild is running sets `interactions:rebuild_rerun`, and the
lock holder runs one more full pass when it finishes. `INTERACTIONS_DISABLED=1`
turns off every background trigger; tests and CI set it.

## API

- `GET /api/decks/{id|registry_id|name}/interactions`: the deck graph. If no
  graph is stored, or the stored one is stale, it's rebuilt from cached tags
  without calling Scryfall or the LLM.
- `GET /api/interactions/collection[?card=NAME]`
- `GET /api/interactions/runs?limit=N`: per-run call/cost log. Each entry has
  trigger, cache hits, newly tagged cards by source, Scryfall request count,
  each LLM call (provider, model, ok, tokens, `cost_usd`), totals, and graphs built.
- `POST /api/interactions/refresh`, `POST /api/decks/{id}/interactions/refresh`
- `GET /api/interactions/vocabulary`

Costs are estimates from list prices in `llm.PRICING` (USD per 1M tokens), not
billing data.

## Prod keys

The app reads LLM keys from the environment, or from one file per key in
`/run/commander-lab-secrets` (bind-mounted read-only from
`/mnt/user/appdata/commander-lab/secrets` on Tower, root-only). See docs/GITOPS.md.
