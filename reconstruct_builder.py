"""
Reconstruct deck builder implementation.

Orchestrates the Reconstruct deck-building pipeline:
1. Builds candidate pools (Tier 1 source decks, Tier 2 owned unassigned, Tier 3 market buys).
2. Calls the LLM agent (reconstruct_agent.call_agent) with preferences and pools.
3. Deterministically validates draft decks (deck_validator.validate_decks), including shared-pool quantity conflicts.
4. On failure, applies deterministic repair (deck_repair.auto_repair) for trivial issues.
5. If issues remain, re-calls the agent in repair mode with the validation errors and previous drafts (capped retries).
6. Tags every card with its source tier, computes per-card and total new-card costs (reconstruct_pools.compute_deck_cost).
7. Persists drafts on the session: cards with tier, cost, and rationale; explanation; total new cost; and sets drafts_ready.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import deck_repair
import deck_validator as dv
import reconstruct
import reconstruct_agent as ra
import reconstruct_pools as rp
from reconstruct_pools import (
    CandidatePools,
    CardCost,
    CatalogCard,
    DeckCost,
    PoolCard,
    PoolConfig,
    PriceLookup,
    Tier,
    build_candidate_pools,
    compute_deck_cost,
    consume_pools,
    norm_name,
)

logger = logging.getLogger("commander_lab.reconstruct_builder")


class ReconstructBuildError(ValueError):
    """Raised when Reconstruct deck building fails or retries are exhausted."""
    pass


def _commander_mismatch_errors(
    decks: Sequence[Any],
    preferences: Sequence[Mapping],
) -> List[dict]:
    """Errors for drafts whose commanders differ from the requested ones."""
    errors: List[dict] = []
    for i, (deck, pref) in enumerate(zip(decks, preferences)):
        wanted = {norm_name(c) for c in (pref.get("commanders") or [])}
        if not wanted:
            continue
        got = {norm_name(c) for c in deck.commanders}
        if got != wanted:
            errors.append({
                "code": "COMMANDER_MISMATCH",
                "message": (
                    f"Deck {i + 1} commanders {sorted(deck.commanders)} do not match "
                    f"requested commanders {list(pref.get('commanders') or [])}"
                ),
                "cards": list(deck.commanders),
            })
    return errors


class ReconstructBuilder(reconstruct.BaseReconstructBuilder):
    """Builder implementation behind BaseReconstructBuilder interface."""

    def __init__(
        self,
        max_retries: int = 3,
        client: Any = None,
        client_factory: Optional[Callable[[], Any]] = None,
        pool_config: Optional[PoolConfig] = None,
        catalog: Optional[Any] = None,
        price_lookup: Optional[PriceLookup] = None,
        owned_instances: Optional[Iterable[Mapping]] = None,
        structured: bool = True,
        guide: Optional[str] = None,
    ):
        self.max_retries = max_retries
        self.client = client
        self.client_factory = client_factory
        self.pool_config = pool_config
        self.catalog = catalog
        self.price_lookup = price_lookup
        self.owned_instances = owned_instances
        self.structured = structured
        self.guide = guide

    def _resolve_catalogs(
        self,
        r: Any,
        names: Iterable[str],
    ) -> Tuple[Optional[rp.CardCatalog], Optional[dv.CardCatalog]]:
        """Resolve pools catalog and validator catalog from override or Redis."""
        if self.catalog is not None:
            if isinstance(self.catalog, dv.CardCatalog):
                val_cat = self.catalog
                # Adapt for pools catalog
                class _DvToPoolsCatalog:
                    def __init__(self, cat: dv.CardCatalog):
                        self.cat = cat

                    def iter_cards(self) -> Iterable[CatalogCard]:
                        seen = set()
                        for k, info in self.cat._by_key.items():
                            if info.name in seen:
                                continue
                            seen.add(info.name)
                            legal = (
                                False if info.commander_legality in ("banned", "not_legal")
                                else (True if info.commander_legality == "legal" else None)
                            )
                            type_line = " ".join([*info.supertypes, *info.types])
                            if info.subtypes:
                                type_line += " — " + " ".join(info.subtypes)
                            yield CatalogCard(
                                name=info.name,
                                color_identity=info.color_identity,
                                legal=legal,
                                type_line=type_line,
                            )

                pools_cat = _DvToPoolsCatalog(self.catalog)
                return pools_cat, val_cat

            if isinstance(self.catalog, (rp.InMemoryCatalog, rp.RedisCardCatalog)):
                pools_cat = self.catalog
                val_cat = dv.CardCatalog()
                for cc in self.catalog.iter_cards():
                    legality = "legal" if cc.legal is True else ("banned" if cc.legal is False else None)
                    types = tuple(cc.type_line.split()) if getattr(cc, "type_line", None) else ()
                    val_cat.add(
                        dv.CardInfo(
                            name=cc.name,
                            color_identity=cc.color_identity,
                            commander_legality=legality,
                            types=types,
                        )
                    )
                return pools_cat, val_cat

        pools_cat = rp.RedisCardCatalog(r) if r is not None else None
        val_cat = dv.catalog_from_redis(r, names) if r is not None else None
        return pools_cat, val_cat

    def build(self, session: dict, r: Any) -> list[dict]:
        """Given a session record and Redis client, generate up to 2 draft decks."""
        preferences = session.get("preferences")
        if not preferences:
            raise ValueError("preferences must be submitted before triggering build")

        num_decks = len(preferences)
        if not (1 <= num_decks <= 2):
            raise ValueError(f"preferences must contain 1 or 2 target decks, got {num_decks}")

        # 1. Build candidate pools
        pools_cat, val_catalog_base = self._resolve_catalogs(r, [])
        pools = build_candidate_pools(
            session,
            r,
            catalog=pools_cat,
            owned_instances=self.owned_instances,
            price_lookup=self.price_lookup,
            config=self.pool_config,
            target_deck_count=num_decks,
        )

        tier1_available = {c.name: c.available_qty or 0 for c in pools.tier1}
        tier2_available = {c.name: c.available_qty or 0 for c in pools.tier2}

        # 2. Agent call and validation loop
        attempt = 0
        validation_errors: Optional[Sequence[Any]] = None
        previous_drafts: Optional[Sequence[Any]] = None
        final_agent_decks: Optional[List[ra.DraftDeck]] = None
        final_validator_decks: Optional[List[dv.DraftDeck]] = None
        last_error_summary = ""

        while attempt <= self.max_retries:
            attempt += 1

            try:
                agent_result = ra.call_agent(
                    preferences=preferences,
                    tier1=pools.tier1,
                    tier2=pools.tier2,
                    tier3=pools.tier3,
                    num_decks=num_decks,
                    validation_errors=validation_errors,
                    previous_drafts=previous_drafts,
                    client=self.client,
                    client_factory=self.client_factory,
                    structured=self.structured,
                    guide=self.guide,
                )
            except ra.AgentOutputError as exc:
                last_error_summary = str(exc)
                if attempt > self.max_retries:
                    raise ReconstructBuildError(
                        f"Agent output failed after {self.max_retries} retries: {exc}"
                    ) from exc
                validation_errors = [{"code": "MALFORMED_OUTPUT", "message": str(exc), "cards": []}]
                continue
            except Exception as exc:
                raise ReconstructBuildError(f"Agent call failed: {exc}") from exc

            # Adapt agent draft decks to validator draft decks
            validator_decks = [
                dv.DraftDeck.from_builder_draft({
                    "name": d.name,
                    "commanders": d.commanders,
                    "cards": [c.model_dump() for c in d.cards],
                })
                for d in agent_result.decks
            ]

            # Resolve catalog for validator
            if val_catalog_base is not None:
                catalog = val_catalog_base
            elif r is not None:
                all_names = dv.draft_card_names(validator_decks)
                catalog = dv.catalog_from_redis(r, all_names)
            else:
                catalog = dv.CardCatalog()

            # Deterministic validation
            val_result = dv.validate_decks(
                validator_decks,
                catalog,
                tier1_available=tier1_available,
                tier2_available=tier2_available,
                exempt_basics_from_pool=True,
            )

            accepted_decks = None
            if val_result.valid:
                accepted_decks = validator_decks
            else:
                # Apply deterministic repair for trivial issues
                repaired_decks = []
                for d in validator_decks:
                    repaired, _ = deck_repair.auto_repair(d, catalog)
                    repaired_decks.append(repaired)

                val_result = dv.validate_decks(
                    repaired_decks,
                    catalog,
                    tier1_available=tier1_available,
                    tier2_available=tier2_available,
                    exempt_basics_from_pool=True,
                )
                if val_result.valid:
                    accepted_decks = repaired_decks

            # Commanders must match what the user asked for (BUG-2)
            cmd_errors = _commander_mismatch_errors(
                accepted_decks if accepted_decks is not None else validator_decks,
                preferences,
            ) if accepted_decks is not None else []

            if accepted_decks is not None and not cmd_errors:
                final_validator_decks = accepted_decks
                final_agent_decks = agent_result.decks
                break

            # Not valid: prepare for agent retry
            error_dicts = [e.to_dict() for e in val_result.errors] + cmd_errors
            last_error_summary = "; ".join(
                f"[{e.get('code')}] {e.get('message')}" for e in error_dicts
            )
            if attempt > self.max_retries:
                raise ReconstructBuildError(
                    f"Draft validation failed after {self.max_retries} retries: {last_error_summary}"
                )

            validation_errors = error_dicts
            previous_drafts = [d.model_dump() for d in agent_result.decks]

        if final_validator_decks is None or final_agent_decks is None:
            raise ReconstructBuildError(
                f"Draft validation failed after {self.max_retries} retries: {last_error_summary}"
            )

        # 3. Tagging, Costing, and Assembling final drafts
        drafts: List[dict] = []
        remaining = pools
        for i in range(num_decks):
            v_deck = final_validator_decks[i]
            a_deck = final_agent_decks[i]
            pref = preferences[i]

            why_map: Dict[str, str] = {norm_name(c.name): c.why for c in a_deck.cards}

            deck_card_inputs: List[Tuple[str, int]] = []
            for cmd in v_deck.commanders:
                deck_card_inputs.append((cmd, 1))
            for c in v_deck.cards:
                deck_card_inputs.append((c.name, c.quantity))

            deck_cost = compute_deck_cost(
                deck_card_inputs,
                remaining,
                price_lookup=self.price_lookup,
                free_basics=True,
            )

            remaining = consume_pools(remaining, deck_card_inputs)

            cmd_keys = {norm_name(cmd) for cmd in v_deck.commanders}
            draft_cards = []
            for card_cost in deck_cost.cards:
                k = norm_name(card_cost.name)
                if k in cmd_keys:
                    rationale = why_map.get(k) or "Designated commander from preferences"
                elif card_cost.name in dv.BASIC_LAND_NAMES:
                    rationale = why_map.get(k) or "Basic land for mana base"
                else:
                    rationale = why_map.get(k) or "Included in draft"

                draft_cards.append(card_cost.to_draft_card(rationale=rationale))

            deck_name = a_deck.name or pref.get("name") or (
                f"{v_deck.commanders[0]} Draft" if v_deck.commanders else f"Reconstructed Draft {i + 1}"
            )

            drafts.append({
                "id": f"draft_{i}",
                "name": deck_name,
                "commanders": list(v_deck.commanders),
                "themes": list(pref.get("themes", [])),
                "strategies": list(pref.get("strategies", [])),
                "art_style": pref.get("art_style", ""),
                "flavor": pref.get("flavor", ""),
                "notes": pref.get("notes", ""),
                "cards": draft_cards,
                "total_new_cost": deck_cost.total_new_cost,
                "is_complete": deck_cost.is_complete,
                "unpriced_cards": list(deck_cost.unpriced_cards),
                "explanation": a_deck.explanation,
            })

        return drafts
