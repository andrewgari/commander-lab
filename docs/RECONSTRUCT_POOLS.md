# Reconstruct tiered candidate pools (`reconstruct_pools.py`)

Pool construction and costing for the Reconstruct deck builder. No LLM, no validator.
Redis, the card catalog and the price source are injectable, so everything is testable on fixtures.

## Tiers (each card is in exactly one: the lowest it qualifies for)

| Tier | Label | Contents | Quantity |
|---|---|---|---|
| 1 | `previous_deck` | cards in `session["source_decks"]` snapshots (+ commanders) | copies summed across source decks |
| 2 | `owned` | copies with `ownership_status == "in_collection"` (`instances.py`), minus Tier 1 names | free copies |
| 3 | `new` | other Commander-legal cards in the card DB, priced | unlimited (`None`) |

### Tier 2 ownership rule
`instances.py` is the ownership source of truth. A copy is free only if it is `in_collection`.
`in_deck` (dedicated to an active deck), `in_mail` and `not_owned` are excluded.
Decks being reconstructed: `start_reconstruct` already runs the decommission step, which moves
their instances `in_deck -> in_collection`, so the model itself treats them as free. No extra
rule is added. Those cards are Tier 1 by precedence, so they are not double counted in Tier 2.
Note: Tier 1 quantity is the decklist count; Tier 2 quantity counts only free copies of cards
*not* in Tier 1. Extra copies of a Tier 1 card are not offered.

### Tier 3
Source: `RedisCardCatalog` (`card:{oracle_id}` records, plus legacy `card_meta:{name}`).
The card DB has no legality field today: `legal=None` is treated as legal unless
`PoolConfig.require_known_legal=True`; `legal=False` (or `legalities.commander != "legal"`) is always excluded.
Prices come from the existing price source (`analytics/prices.py`, `price:card:{name}`) via `redis_price_lookup`.
Only the cards that survive filtering and the cap are priced.

## API
```python
from reconstruct_pools import (
    PoolConfig, build_candidate_pools, tag_cards, compute_card_costs,
    compute_deck_cost, compute_deck_costs, to_llm_payload, Tier,
)

pools = build_candidate_pools(session, r, config=PoolConfig(color_identity="UG", tier3_cap=300))
pools.tier1 / .tier2 / .tier3          # List[PoolCard]
pools.available_quantities()           # {norm_name: Availability(name, tier, quantity)}, Tier 1 + 2, for the validator
tag_cards(deck_cards, pools)           # List[TaggedCard] (tier, tier_label, in_pool, exceeds_available)
compute_deck_cost(deck_cards, pools)   # DeckCost(total_new_cost, unpriced_cards, is_complete, cards=[CardCost])
to_llm_payload(pools, color_identity="UG", tier3_cap=150)   # compact lists for the prompt
```
Card lists accept names, `(name, qty)` tuples, or `{"name", "quantity"}` dicts.

### PoolConfig
`color_identity` (Tier 3 subset filter; `"C"` = colorless only), `tier3_cap` (default 300, `None` = unlimited),
`ranker` (key function; lowest kept first when capped; default alphabetical), `require_known_legal`,
`include_basics` (default False), `excluded_names`.

### Cost rules
Tier 1 = 0, Tier 2 = 0, Tier 3 = market price x quantity. A missing Tier 3 price is
`price_known=False`, `cost=None` (never 0). `DeckCost.total_new_cost` sums known prices only and
`is_complete=False` plus `unpriced_cards` say it is a lower bound. A price of `0.0` is known. Tier 3 basics cost 0 (`free_basics`).
A card outside every pool is tagged Tier 3 with `in_pool=False` and priced from `prices=` / `price_lookup=` if given.

### Shared-pool conflicts
`available_quantities()` is never narrowed by the Tier 3 filter/cap or `to_llm_payload`. Sum each card's
quantity across the two target decks (`tag_cards`) and compare with `Availability.quantity`.

## Dry run (`session["dry_run"] == True`)
A dry run skips decommission, so source-deck copies are still `in_deck` in Redis. `build_candidate_pools`
therefore also counts instances that are `in_deck` on the session's source decks
(`_source_deck_keys(session)` = `source_deck_ids` plus each snapshot's `registry_id`/`id`) as free:
`free_owned_counts(instances, source_deck_keys=...)`. Instances `in_deck` on any other deck, `in_mail` and
`not_owned` stay excluded. The existing `n -= tier1_qty` subtraction is unchanged, so `tier1`, `tier2`, `tier3` and
`available_quantities()` match a real (decommissioned) run. `GET /api/reconstruct/{session_id}/pools` exposes Tier 1/2.

## Tests
`pytest tests/test_reconstruct_pools.py` (fixtures, factories, in-memory Redis; no network).
