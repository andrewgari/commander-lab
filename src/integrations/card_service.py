"""Unified card data service aggregating Scryfall, EDHREC, and Commander Salt.

Provides:
- `UnifiedCardService`: Main interface orchestrating concurrent queries across
  Scryfall (card metadata), EDHREC (synergy & recommendations), and Commander Salt
  (salt ratings) into standardized `UnifiedCardData` domain models.
- Graceful partial failure isolation, automatic community salt fallbacks,
  and network error recovery.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from src.integrations.commandersalt import (
    CommanderSaltClient,
    CommanderSaltError,
    CommanderSaltNotFoundError,
)
from src.integrations.edhrec import EDHRecClient, EDHRecError, EDHRecNotFoundError
from src.integrations.models import (
    CardMetadata,
    CardSalt,
    CardSynergy,
    ProviderStatus,
    UnifiedCardData,
)
from src.integrations.scryfall import (
    ScryfallCard,
    ScryfallClient,
    ScryfallError,
    ScryfallNotFoundError,
)

logger = logging.getLogger(__name__)

ALL_PROVIDERS = ("scryfall", "edhrec", "commandersalt")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CardServiceError(Exception):
    """Base exception for UnifiedCardService errors."""


class CardNotFoundError(CardServiceError):
    """Raised when a card cannot be found in Scryfall or registered providers."""

    def __init__(self, message: str, card_name: str) -> None:
        super().__init__(message)
        self.card_name = card_name


# ---------------------------------------------------------------------------
# UnifiedCardService
# ---------------------------------------------------------------------------


class UnifiedCardService:
    """Unified service that aggregates card metadata and analytics across MTG providers."""

    def __init__(
        self,
        scryfall_client: Optional[ScryfallClient] = None,
        edhrec_client: Optional[EDHRecClient] = None,
        commandersalt_client: Optional[CommanderSaltClient] = None,
        *,
        timeout: float = 10.0,
        enable_salt_fallback: bool = True,
    ) -> None:
        """Initialize UnifiedCardService.

        Parameters
        ----------
        scryfall_client : Optional[ScryfallClient]
            Client instance for Scryfall card metadata.
        edhrec_client : Optional[EDHRecClient]
            Client instance for EDHRec synergy metrics.
        commandersalt_client : Optional[CommanderSaltClient]
            Client instance for Commander Salt ratings.
        timeout : float
            Per-provider query timeout in seconds (default: 10.0).
        enable_salt_fallback : bool
            Whether to fallback to EDHRec community salt rating if Commander Salt
            is unreachable or missing data (default: True).
        """
        self._scryfall_client = scryfall_client or ScryfallClient()
        self._owns_scryfall = scryfall_client is None

        self._edhrec_client = edhrec_client or EDHRecClient()
        self._owns_edhrec = edhrec_client is None

        self._commandersalt_client = commandersalt_client or CommanderSaltClient()
        self._owns_commandersalt = commandersalt_client is None

        self._timeout = timeout
        self._enable_salt_fallback = enable_salt_fallback

    async def __aenter__(self) -> UnifiedCardService:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self.close()

    async def aclose(self) -> None:
        """Close any owned HTTP clients."""
        await asyncio.gather(
            self._scryfall_client.aclose() if self._owns_scryfall else asyncio.sleep(0),
            self._edhrec_client.aclose() if self._owns_edhrec else asyncio.sleep(0),
            self._commandersalt_client.aclose() if self._owns_commandersalt else asyncio.sleep(0),
            return_exceptions=True,
        )

    async def close(self) -> None:
        """Alias for aclose."""
        await self.aclose()

    async def get_card_data(
        self,
        card_name: str,
        *,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
        fuzzy: bool = False,
        providers: Optional[Sequence[str]] = None,
        raise_on_not_found: bool = True,
        raise_on_error: bool = False,
    ) -> UnifiedCardData:
        """Fetch unified card data from Scryfall, EDHRec, and Commander Salt.

        Parameters
        ----------
        card_name : str
            Name of the card to lookup.
        commander_name : Optional[Union[str, Sequence[str]]]
            Optional commander name(s) for contextual synergy evaluation.
        fuzzy : bool
            Allow fuzzy name matching on Scryfall if exact match fails.
        providers : Optional[Sequence[str]]
            Subset of providers to query ('scryfall', 'edhrec', 'commandersalt').
            Defaults to all three.
        raise_on_not_found : bool
            If True, raises `CardNotFoundError` when Scryfall does not find the card.
        raise_on_error : bool
            If True, raises `CardServiceError` if all requested providers fail.

        Returns
        -------
        UnifiedCardData
            Standardized domain model containing metadata, synergy, and salt rating.
        """
        if not card_name or not card_name.strip():
            raise ValueError("card_name cannot be empty")

        clean_name = card_name.strip()
        requested_providers = set(p.lower() for p in (providers or ALL_PROVIDERS))

        provider_statuses: Dict[str, ProviderStatus] = {}
        successful_providers: List[str] = []
        failed_providers: List[str] = []
        warnings: List[str] = []

        # -------------------------------------------------------------------
        # 1. Scryfall Card Metadata
        # -------------------------------------------------------------------
        metadata: Optional[CardMetadata] = None
        canonical_name = clean_name
        oracle_id: Optional[str] = None
        scryfall_id: Optional[str] = None

        if "scryfall" in requested_providers:
            t0 = time.monotonic()
            try:
                scry_card: Optional[ScryfallCard] = None
                if fuzzy:
                    scry_card = await self._scryfall_client.get_card_by_name(fuzzy=clean_name)
                else:
                    scry_card = await self._scryfall_client.get_card_by_name(exact=clean_name)

                latency_ms = (time.monotonic() - t0) * 1000.0

                if scry_card is not None:
                    metadata = CardMetadata.from_scryfall_card(scry_card)
                    canonical_name = metadata.name
                    oracle_id = metadata.oracle_id
                    scryfall_id = metadata.scryfall_id
                    provider_statuses["scryfall"] = ProviderStatus(
                        provider="scryfall",
                        status="success",
                        latency_ms=latency_ms,
                    )
                    successful_providers.append("scryfall")
                else:
                    provider_statuses["scryfall"] = ProviderStatus(
                        provider="scryfall",
                        status="not_found",
                        latency_ms=latency_ms,
                    )
                    failed_providers.append("scryfall")
                    if raise_on_not_found:
                        raise CardNotFoundError(
                            f"Card not found on Scryfall: '{clean_name}'",
                            card_name=clean_name,
                        )
                    warnings.append(f"Scryfall: Card '{clean_name}' not found")

            except CardNotFoundError:
                raise
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000.0
                err_msg = str(exc)
                logger.warning(f"Scryfall lookup error for '{clean_name}': {err_msg}")
                provider_statuses["scryfall"] = ProviderStatus(
                    provider="scryfall",
                    status="error",
                    latency_ms=latency_ms,
                    error=err_msg,
                )
                failed_providers.append("scryfall")
                warnings.append(f"Scryfall query failed: {err_msg}")
        else:
            provider_statuses["scryfall"] = ProviderStatus(provider="scryfall", status="skipped")

        # -------------------------------------------------------------------
        # 2. EDHRec Synergy and Commander Salt in Parallel
        # -------------------------------------------------------------------
        query_edhrec = "edhrec" in requested_providers
        query_salt = "commandersalt" in requested_providers

        async def _query_edhrec() -> tuple[Optional[CardSynergy], Optional[CardSalt], float, Optional[Exception]]:
            start = time.monotonic()
            try:
                synergy_res = await asyncio.wait_for(
                    self._edhrec_client.get_card_synergy(canonical_name, commander_name=commander_name),
                    timeout=self._timeout,
                )
                # Also fetch salt from EDHRec for potential fallback
                salt_res = await asyncio.wait_for(
                    self._edhrec_client.get_card_salt(canonical_name, commander_name=commander_name),
                    timeout=self._timeout,
                )
                elapsed = (time.monotonic() - start) * 1000.0
                return synergy_res, salt_res, elapsed, None
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000.0
                return None, None, elapsed, e

        async def _query_commandersalt() -> tuple[Optional[CardSalt], float, Optional[Exception]]:
            start = time.monotonic()
            try:
                salt_res = await asyncio.wait_for(
                    self._commandersalt_client.get_card_salt(canonical_name),
                    timeout=self._timeout,
                )
                elapsed = (time.monotonic() - start) * 1000.0
                return salt_res, elapsed, None
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000.0
                return None, elapsed, e

        tasks: List[Any] = []
        if query_edhrec:
            tasks.append(_query_edhrec())
        if query_salt:
            tasks.append(_query_commandersalt())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        res_idx = 0
        synergy: Optional[CardSynergy] = None
        edhrec_salt: Optional[CardSalt] = None
        if query_edhrec:
            edh_res = results[res_idx]
            res_idx += 1
            if isinstance(edh_res, tuple):
                synergy_res, salt_res, latency, err = edh_res
                edhrec_salt = salt_res
                if err is not None:
                    provider_statuses["edhrec"] = ProviderStatus(
                        provider="edhrec",
                        status="error",
                        latency_ms=latency,
                        error=str(err),
                    )
                    failed_providers.append("edhrec")
                    warnings.append(f"EDHRec synergy query failed: {err}")
                elif synergy_res is not None:
                    synergy = synergy_res
                    provider_statuses["edhrec"] = ProviderStatus(
                        provider="edhrec",
                        status="success",
                        latency_ms=latency,
                    )
                    successful_providers.append("edhrec")
                else:
                    provider_statuses["edhrec"] = ProviderStatus(
                        provider="edhrec",
                        status="not_found",
                        latency_ms=latency,
                    )
                    successful_providers.append("edhrec")
            else:
                provider_statuses["edhrec"] = ProviderStatus(
                    provider="edhrec",
                    status="error",
                    error=str(edh_res),
                )
                failed_providers.append("edhrec")
                warnings.append(f"EDHRec query encountered unexpected error: {edh_res}")
        else:
            provider_statuses["edhrec"] = ProviderStatus(provider="edhrec", status="skipped")

        # Commander Salt result handling
        salt: Optional[CardSalt] = None
        if query_salt:
            cs_res = results[res_idx]
            res_idx += 1
            if isinstance(cs_res, tuple):
                salt_res, latency, err = cs_res
                if err is not None:
                    # Commander Salt failed
                    logger.warning(f"Commander Salt query failed for '{canonical_name}': {err}")
                    failed_providers.append("commandersalt")
                    warnings.append(f"Commander Salt rating query failed: {err}")

                    # Attempt fallback to EDHRec salt
                    if self._enable_salt_fallback and edhrec_salt is not None:
                        salt = CardSalt(
                            card_name=canonical_name,
                            score=edhrec_salt.score,
                            description="EDHRec community salt score (fallback from Commander Salt)",
                            source="edhrec",
                            is_fallback=True,
                            raw=edhrec_salt.raw,
                        )
                        provider_statuses["commandersalt"] = ProviderStatus(
                            provider="commandersalt",
                            status="fallback",
                            latency_ms=latency,
                            error=str(err),
                        )
                        successful_providers.append("commandersalt")
                        warnings.append(
                            f"Fell back to EDHRec salt rating ({salt.score:.2f}) due to Commander Salt error"
                        )
                    else:
                        provider_statuses["commandersalt"] = ProviderStatus(
                            provider="commandersalt",
                            status="error",
                            latency_ms=latency,
                            error=str(err),
                        )
                elif salt_res is not None:
                    salt = salt_res
                    provider_statuses["commandersalt"] = ProviderStatus(
                        provider="commandersalt",
                        status="success",
                        latency_ms=latency,
                    )
                    successful_providers.append("commandersalt")
                else:
                    # Salt rating not found in Commander Salt, try EDHRec fallback
                    if self._enable_salt_fallback and edhrec_salt is not None:
                        salt = CardSalt(
                            card_name=canonical_name,
                            score=edhrec_salt.score,
                            description="EDHRec community salt score (fallback from Commander Salt)",
                            source="edhrec",
                            is_fallback=True,
                            raw=edhrec_salt.raw,
                        )
                        provider_statuses["commandersalt"] = ProviderStatus(
                            provider="commandersalt",
                            status="fallback",
                            latency_ms=latency,
                        )
                        successful_providers.append("commandersalt")
                        warnings.append(
                            f"Fell back to EDHRec salt rating ({salt.score:.2f}) because Commander Salt had no record"
                        )
                    else:
                        provider_statuses["commandersalt"] = ProviderStatus(
                            provider="commandersalt",
                            status="not_found",
                            latency_ms=latency,
                        )
                        successful_providers.append("commandersalt")
            else:
                provider_statuses["commandersalt"] = ProviderStatus(
                    provider="commandersalt",
                    status="error",
                    error=str(cs_res),
                )
                failed_providers.append("commandersalt")
                warnings.append(f"Commander Salt encountered unexpected error: {cs_res}")
        else:
            provider_statuses["commandersalt"] = ProviderStatus(
                provider="commandersalt", status="skipped"
            )

        # Check total failure scenario
        if raise_on_error and len(successful_providers) == 0 and len(failed_providers) > 0:
            raise CardServiceError(
                f"All requested providers failed for '{clean_name}': {'; '.join(warnings)}"
            )

        return UnifiedCardData(
            name=canonical_name,
            oracle_id=oracle_id,
            scryfall_id=scryfall_id,
            metadata=metadata,
            synergy=synergy,
            salt=salt,
            provider_statuses=provider_statuses,
            successful_providers=successful_providers,
            failed_providers=failed_providers,
            warnings=warnings,
        )

    async def get_card_by_id(
        self,
        scryfall_id: str,
        *,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
        providers: Optional[Sequence[str]] = None,
    ) -> UnifiedCardData:
        """Fetch unified card data starting from a Scryfall card ID."""
        if not scryfall_id or not scryfall_id.strip():
            raise ValueError("scryfall_id cannot be empty")

        clean_id = scryfall_id.strip()
        scry_card = await self._scryfall_client.get_card_by_id(clean_id)
        if scry_card is None:
            raise CardNotFoundError(
                f"Card with Scryfall ID '{clean_id}' not found",
                card_name=clean_id,
            )

        return await self.get_card_data(
            scry_card.name,
            commander_name=commander_name,
            providers=providers,
        )

    async def get_cards_batch(
        self,
        card_names: Sequence[str],
        *,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
        concurrency: int = 5,
        providers: Optional[Sequence[str]] = None,
        raise_on_not_found: bool = False,
    ) -> List[UnifiedCardData]:
        """Fetch unified card data for a batch of card names concurrently."""
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _fetch_one(name: str) -> UnifiedCardData:
            async with semaphore:
                try:
                    return await self.get_card_data(
                        name,
                        commander_name=commander_name,
                        providers=providers,
                        raise_on_not_found=raise_on_not_found,
                    )
                except CardNotFoundError:
                    return UnifiedCardData(
                        name=name,
                        provider_statuses={
                            "scryfall": ProviderStatus(provider="scryfall", status="not_found")
                        },
                        failed_providers=["scryfall"],
                        warnings=[f"Card '{name}' not found"],
                    )

        tasks = [_fetch_one(name) for name in card_names if name and name.strip()]
        return await asyncio.gather(*tasks)

    async def search_cards(
        self,
        query: str,
        *,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
        limit: int = 15,
        include_analytics: bool = True,
        providers: Optional[Sequence[str]] = None,
    ) -> List[UnifiedCardData]:
        """Search cards on Scryfall and optionally enrich top results with analytics."""
        search_result = await self._scryfall_client.search_cards(query)
        cards = search_result.data[:limit]

        if not include_analytics:
            return [
                UnifiedCardData(
                    name=c.name,
                    oracle_id=c.oracle_id,
                    scryfall_id=c.id,
                    metadata=CardMetadata.from_scryfall_card(c),
                    provider_statuses={
                        "scryfall": ProviderStatus(provider="scryfall", status="success")
                    },
                    successful_providers=["scryfall"],
                )
                for c in cards
            ]

        card_names = [c.name for c in cards]
        return await self.get_cards_batch(
            card_names,
            commander_name=commander_name,
            providers=providers,
        )

    def get_card_data_sync(
        self,
        card_name: str,
        *,
        commander_name: Optional[Union[str, Sequence[str]]] = None,
        fuzzy: bool = False,
        providers: Optional[Sequence[str]] = None,
    ) -> UnifiedCardData:
        """Synchronous wrapper for get_card_data."""
        return asyncio.run(
            self.get_card_data(
                card_name,
                commander_name=commander_name,
                fuzzy=fuzzy,
                providers=providers,
            )
        )
