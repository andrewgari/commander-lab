# Engines + Commander Spellbook combos (AN-2)

Built on the AN-1 interaction graph (docs/INTERACTION_GRAPH.md). Spec:
docs/ANALYTICS_SPEC.md Layer 1 ("Engines", "Combos"). JSON only, no UI yet.

## Engines (`analytics/interactions/engines.py`)

An engine is a resource chain inside one deck, read off the graph's
produce/consume tags:

    fuel -> sources --r1--> converters --r2--> [converters --r3-->] payoffs

- **sources** make r1, **converters** consume r1 and make r2 (optionally a
  second converter step r2 -> r3), and **payoffs** consume the last resource.
  If the last resource is worth something on its own (drain, cards, counters,
  tokens, mana, life), the converters are the payoff and no extra consumer is
  needed. Example: Viscera Seer (death) -> Blood Artist (drain).
- **fuel** is only used for `death`/`sacrifice` chains: token makers feeding
  sacrifice outlets.
- Artifacts, enchantments and spells are card *types*, so they can start a
  chain but can't be a later step.
- **loops**: two cards that feed each other (A -> B and B -> A), grouped by
  the resources involved (Kaalia ⇄ Dragon Tempest on attacks + ETB).

Scoring is deterministic: `sqrt(paths) * steps + 0.25 * internal_edges +
2 * commanders_in_engine` for chains (paths = product of the stage sizes).
Chains are picked greedily by score. A chain that shares at least 50% of its
cards (Jaccard) with one already kept is folded into that chain's
`variants` instead of being listed again. The defaults are 8 chains and 4
loops.

Each engine has `signature` (e.g. `chain:death>life_loss`), `kind`,
`resources`, `stages` (fuel / sources / converters / converter_steps /
payoffs) or `cycles` for loops, `cards`, `commanders`, `paths`,
`card_count`, `internal_edges`, `density`, `score`, `name`, `archetype`,
`name_source`, `summary` (deterministic prose), and `blurb` (LLM, optional).

### Naming

1. **catalog**: `CATALOG` in engines.py maps resource paths to archetypes
   (`death>life_loss` -> "Aristocrats: tokens → sac → drain"). A 3-step chain
   inherits the archetype of its first two steps.
2. **cache**: `interactions:engine_names` (no TTL) maps signature ->
   `{archetype, blurb, provider, model, named_at}`.
3. **LLM**: during a rebuild (sync / manual / nightly), signatures that
   neither the catalog nor the cache covers go to the AN-1 provider chain in
   one call (`LLMChain.generate_json`, Gemini first, then Nous). Each attempt
   is logged in the run with `purpose: "engine_names"`, tokens and
   `cost_usd`, and the run gets an `engine_naming` summary. A signature costs
   at most one call ever. If naming fails, the engine keeps its generic name
   and is retried on the next rebuild.
4. **generic**: "Mana → cards", "Loop: ETB ↔ attack".

Reads (`GET /api/decks/{id}/engines`) never call the LLM.

## Combos (`analytics/interactions/combos.py`)

Source: `POST https://backend.commanderspellbook.com/find-my-combos` with
the deck's commanders and main deck. Pagination (`next`) is followed.

- `in_deck`: Spellbook `included` (every card is in the deck).
- `one_away`: `almostIncluded` (exactly one card missing, inside the
  commander's colour identity). Each entry has `missing`, `missing_owned`
  and `owned[card] = {copies, available, decks}`. `available` counts copies
  in_collection / in_mail; copies in another deck are listed under `decks`.
  Owned near-misses sort first, then by spare copies, then by Spellbook
  popularity.
- `off_identity_one_away`: `almostIncludedByAddingColors`.
- `counts`: everything above (pre-filter), plus the "by changing commanders"
  buckets.
- `top_missing_cards`: the missing cards that complete the most one-away
  combos, owned first.

Ownership uses `instance:*` with ownership_status in_deck / in_collection /
in_mail, the same rule as the AN-1 collection graph.

### Cache

`spellbook:fmc:{sha1(sorted commanders, sorted main)}` (no TTL) holds the
normalized response plus `fetched_at`. Entries younger than
`SPELLBOOK_MAX_AGE_DAYS` (default 7) are served without a request. Older
entries are refetched; if Spellbook is unreachable, the old copy is served
with `stale: true` and `error`. Any card change gives a new key.
`?refresh=true` forces a fetch.

## API

- `GET /api/decks/{id|registry_id|name}/engines`
- `GET /api/decks/{id|registry_id|name}/combos[?refresh=true][&owned_only=true][&limit=N]`.
  This returns 502 if Spellbook fails and there's no cached copy.
