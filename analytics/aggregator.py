"""
Deck analytics aggregation service and consolidated reporting.

Orchestrates concurrent queries across enabled deck analytics providers
(e.g., EDHRec, Commander Salt, and future providers) with configurable timeouts,
per-provider status tracking, error isolation, and metrics consolidation.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import AnalyticsError, AnalyticsProviderError, ProviderNotFoundError
from .models import (
    CardCutRecommendation,
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PopularityMetric,
    PowerScore,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
)
from .provider import BaseAnalyticsProvider
from .registry import AnalyticsProviderRegistry, default_registry

# Ensure built-in provider adapters are registered on the default registry
try:
    import analytics.providers  # noqa: F401
except ImportError:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Aggregation Reporting Domain Models
# =============================================================================

class ProviderAnalyticsStatus(BaseModel):
    """
    Execution status, latency, and capability metadata for an individual provider query.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_name": "edhrec",
                "status": "success",
                "execution_time_ms": 145.2,
                "error": None,
                "supported_queries": ["meta_scores", "recommendations", "synergy", "popularity"],
            }
        }
    )

    provider_name: str = Field(..., description="Provider unique identifier")
    status: str = Field(
        ...,
        description="Query status: 'success', 'error', 'timeout', 'not_found', or 'disabled'",
    )
    execution_time_ms: float = Field(..., ge=0.0, description="Latency in milliseconds")
    error: Optional[str] = Field(None, description="Error message if query failed")
    supported_queries: List[str] = Field(
        default_factory=list, description="Queries supported by this provider"
    )


class AnalyticsSummary(BaseModel):
    """
    High-level consolidated metrics summary across all responding providers.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "salt_score": 25.4,
                "power_level": 7.5,
                "power_tier": "Optimized",
                "overall_synergy": 0.48,
                "commander_rank": 3,
                "total_recommendations": 40,
                "total_cuts": 6,
                "total_combos": 2,
            }
        }
    )

    salt_score: Optional[float] = Field(default=None, description="Consolidated or primary salt score")
    power_level: Optional[float] = Field(default=None, description="Consolidated or primary power level (0-10)")
    power_tier: Optional[str] = Field(default=None, description="Power level tier classification")
    overall_synergy: Optional[float] = Field(default=None, description="Consolidated deck synergy (-1.0 to 1.0)")
    commander_rank: Optional[int] = Field(default=None, description="Commander meta popularity rank")
    total_recommendations: int = Field(default=0, description="Count of consolidated card recommendations")
    total_cuts: int = Field(default=0, description="Count of consolidated card cut suggestions")
    total_combos: Optional[int] = Field(default=None, description="Count of detected combos if available")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Additional consolidated summary facts")


class ConsolidatedMetrics(BaseModel):
    """
    Consolidated deck analytics synthesized across all successful provider responses.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "summary": {
                    "salt_score": 25.4,
                    "power_level": 7.5,
                    "power_tier": "Optimized",
                    "overall_synergy": 0.48,
                    "commander_rank": 3,
                    "total_recommendations": 40,
                    "total_cuts": 6,
                    "total_combos": 2,
                }
            }
        }
    )

    meta_scores: Optional[MetaScores] = Field(
        default=None, description="Consolidated meta scores (salt, power, combos, and rankings)"
    )
    recommendations: Optional[CardRecommendations] = Field(
        default=None, description="Consolidated deduplicated card additions and cuts"
    )
    synergy: Optional[DeckSynergy] = Field(
        default=None, description="Consolidated deck and per-card synergy metrics"
    )
    popularity: Optional[DeckPopularity] = Field(
        default=None, description="Consolidated commander and card popularity metrics"
    )
    summary: AnalyticsSummary = Field(
        default_factory=lambda: AnalyticsSummary(),
        description="High-level consolidated metrics summary",
    )


class AggregatedAnalyticsReport(BaseModel):
    """
    Complete aggregated deck analytics report returned by the aggregation service.

    Includes per-provider execution status, consolidated metrics across providers,
    and individual per-provider results.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "deck_id": "archidekt:6862011",
                "deck_name": "Atraxa Proliferate Engine",
                "commanders": ["Atraxa, Praetors' Voice"],
                "timestamp": "2026-09-29T12:00:00Z",
                "providers_queried": ["commandersalt", "edhrec"],
                "successful_providers": ["commandersalt", "edhrec"],
                "failed_providers": [],
                "provider_statuses": {
                    "commandersalt": {
                        "provider_name": "commandersalt",
                        "status": "success",
                        "execution_time_ms": 120.5,
                        "error": None,
                        "supported_queries": ["meta_scores"],
                    },
                    "edhrec": {
                        "provider_name": "edhrec",
                        "status": "success",
                        "execution_time_ms": 190.2,
                        "error": None,
                        "supported_queries": ["meta_scores", "recommendations", "synergy", "popularity"],
                    },
                },
            }
        }
    )

    deck_id: Optional[str] = Field(None, description="External or library deck identifier")
    deck_name: Optional[str] = Field(None, description="Deck display name")
    commanders: List[str] = Field(default_factory=list, description="Commander card names")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of aggregation")
    providers_queried: List[str] = Field(
        default_factory=list, description="List of providers queried"
    )
    successful_providers: List[str] = Field(
        default_factory=list, description="List of providers that returned successfully"
    )
    failed_providers: List[str] = Field(
        default_factory=list, description="List of providers that failed or timed out"
    )
    provider_statuses: Dict[str, ProviderAnalyticsStatus] = Field(
        default_factory=dict, description="Per-provider query status and latency"
    )
    consolidated_metrics: ConsolidatedMetrics = Field(
        default_factory=lambda: ConsolidatedMetrics(), description="Consolidated analytics across providers"
    )
    provider_results: Dict[str, DeckAnalyticsResult] = Field(
        default_factory=dict, description="Full DeckAnalyticsResult per successful provider"
    )


# =============================================================================
# Deck Input Normalization Helper
# =============================================================================

def coerce_to_decklist_input(deck: Any) -> DecklistInput:
    """
    Coerce various deck representations into a validated DecklistInput instance.

    Supported inputs:
    - `DecklistInput` instance (returned as-is)
    - Objects with `.to_analytics_input()` (e.g. `ConsolidatedDeckIntake`)
    - Dictionary with `DecklistInput` fields or library deck fields (commanders, cards)
    - Raw text decklist string (parsed via raw decklist parser if available)
    """
    if isinstance(deck, DecklistInput):
        return deck

    if hasattr(deck, "to_analytics_input") and callable(getattr(deck, "to_analytics_input")):
        return getattr(deck, "to_analytics_input")()

    if isinstance(deck, dict):
        # Case 1: wrapped in a "deck" key
        if "deck" in deck and isinstance(deck["deck"], (dict, DecklistInput)):
            return coerce_to_decklist_input(deck["deck"])

        # Case 2: standard DecklistInput shape with typed dicts
        try:
            return DecklistInput.model_validate(deck)
        except Exception:
            pass

        # Case 3: Library deck or looser dictionary
        deck_id = deck.get("registry_id") or (str(deck.get("id")) if deck.get("id") else deck.get("deck_id"))
        name = deck.get("name")
        format_val = deck.get("format", "commander")

        raw_commanders = deck.get("commanders") or []
        commanders: List[CommanderIdentifier] = []
        for cmd in raw_commanders:
            if isinstance(cmd, CommanderIdentifier):
                commanders.append(cmd)
            elif isinstance(cmd, str):
                cleaned = cmd.strip()
                if cleaned:
                    commanders.append(
                        CommanderIdentifier(
                            name=cleaned,
                            oracle_id=None,
                            scryfall_id=None,
                        )
                    )
            elif isinstance(cmd, dict):
                cmd_name = cmd.get("name")
                if cmd_name:
                    commanders.append(
                        CommanderIdentifier(
                            name=str(cmd_name).strip(),
                            oracle_id=cmd.get("oracle_id"),
                            scryfall_id=cmd.get("scryfall_id"),
                        )
                    )

        raw_cards = deck.get("cards") or []
        cards: List[DeckCardEntry] = []
        for entry in raw_cards:
            if isinstance(entry, DeckCardEntry):
                cards.append(entry)
            elif isinstance(entry, str):
                cleaned = entry.strip()
                if cleaned:
                    cards.append(
                        DeckCardEntry(
                            name=cleaned,
                            quantity=1,
                            oracle_id=None,
                            scryfall_id=None,
                            category=None,
                        )
                    )
            elif isinstance(entry, dict):
                c_name = entry.get("name") or entry.get("card_name")
                if c_name:
                    raw_qty: Any = entry.get("quantity") if "quantity" in entry else (entry.get("count") if "count" in entry else 1)
                    cards.append(
                        DeckCardEntry(
                            name=str(c_name).strip(),
                            quantity=raw_qty,
                            oracle_id=entry.get("oracle_id"),
                            scryfall_id=entry.get("scryfall_id"),
                            category=entry.get("category"),
                        )
                    )

        # Fallback if decklist text was provided in dictionary
        if not cards and "decklist" in deck and isinstance(deck["decklist"], str):
            cmd_names = [c.name for c in commanders] if commanders else None
            try:
                import deck_intake
                intake_res = deck_intake.process_deck_intake(
                    decklist=deck["decklist"],
                    commanders=cmd_names,
                    name=name,
                    deck_id=str(deck_id) if deck_id else None,
                    format=format_val,
                )
                return intake_res.to_analytics_input()
            except Exception:
                pass

            # Fallback to direct parsing while preserving explicit commanders
            parsed_input = coerce_to_decklist_input(deck["decklist"])
            if commanders:
                return DecklistInput(
                    deck_id=str(deck_id) if deck_id else parsed_input.deck_id,
                    name=name or parsed_input.name,
                    commanders=commanders,
                    cards=parsed_input.cards,
                    format=format_val or parsed_input.format,
                )
            return parsed_input

        return DecklistInput(
            deck_id=str(deck_id) if deck_id else None,
            name=name,
            commanders=commanders,
            cards=cards,
            format=format_val,
        )

    if isinstance(deck, str):
        # Attempt to parse raw text decklist via intake
        try:
            import deck_intake
            intake_res = deck_intake.process_deck_intake(decklist=deck)
            return intake_res.to_analytics_input()
        except Exception:
            pass

        # Fallback to providers.raw_paste
        try:
            from providers.raw_paste import parse_decklist
            parsed_cards = parse_decklist(deck)
            commanders: List[CommanderIdentifier] = []
            cards: List[DeckCardEntry] = []

            for c in parsed_cards:
                c_name = c.get("name")
                if not c_name:
                    continue
                cards.append(
                    DeckCardEntry(
                        name=c_name,
                        quantity=c.get("quantity", 1),
                        oracle_id=None,
                        scryfall_id=None,
                        category=None,
                    )
                )

            # If no explicit commanders, take the first card as commander candidate
            if not commanders and cards:
                first_card = cards.pop(0)
                commanders.append(
                    CommanderIdentifier(
                        name=first_card.name,
                        oracle_id=None,
                        scryfall_id=None,
                    )
                )

            return DecklistInput(
                deck_id=None,
                name=None,
                commanders=commanders,
                cards=cards,
                format="commander",
            )
        except Exception as e:
            raise ValueError(f"Could not parse decklist text into DecklistInput: {e}") from e

    raise ValueError(f"Unsupported deck payload type: {type(deck).__name__}")


# =============================================================================
# Deck Analytics Aggregator Service
# =============================================================================

class DeckAnalyticsAggregator:
    """
    Core aggregation service orchestrating concurrent queries across enabled
    deck analytics providers with configurable timeouts and unified reporting.
    """

    def __init__(
        self,
        registry: Optional[AnalyticsProviderRegistry] = None,
        default_timeout: float = 10.0,
        provider_timeouts: Optional[Dict[str, float]] = None,
        enabled_providers: Optional[Sequence[str]] = None,
        max_workers: int = 8,
    ) -> None:
        """
        Initialize the analytics aggregator.

        Args:
            registry: AnalyticsProviderRegistry to resolve providers from.
                      Defaults to `default_registry`.
            default_timeout: Global default timeout in seconds per provider query.
            provider_timeouts: Optional mapping of provider names to provider-specific timeouts.
            enabled_providers: Optional whitelist of enabled provider names. If None,
                               all registered providers in the registry are eligible.
            max_workers: Maximum concurrent thread workers in the thread pool.
        """
        self.registry = registry if registry is not None else default_registry
        self.default_timeout = max(0.1, float(default_timeout))
        self.provider_timeouts: Dict[str, float] = dict(provider_timeouts or {})
        self.enabled_providers: Optional[Set[str]] = (
            set(p.strip().lower() for p in enabled_providers if p.strip())
            if enabled_providers is not None
            else None
        )
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="analytics-aggregator",
        )
        self._provider_locks: Dict[str, threading.Lock] = {}
        self._locks_mutex = threading.Lock()

    def _get_provider_lock(self, provider_name: str) -> threading.Lock:
        """Get or create a mutex for a specific provider to prevent concurrent thread races."""
        key = provider_name.strip().lower()
        with self._locks_mutex:
            if key not in self._provider_locks:
                self._provider_locks[key] = threading.Lock()
            return self._provider_locks[key]

    def get_available_providers(self) -> List[Dict[str, Any]]:
        """
        List all available providers in the registry with their supported queries.
        """
        result = []
        for name in self.registry.list_providers():
            if self.enabled_providers is not None and name.lower() not in self.enabled_providers:
                continue
            try:
                provider = self.registry.get(name)
                result.append(
                    {
                        "name": name,
                        "display_name": self.registry._display_names.get(name.lower(), name),
                        "supported_queries": sorted(list(getattr(provider, "SUPPORTED_QUERIES", []))),
                    }
                )
            except Exception as e:
                logger.warning(f"Could not inspect provider '{name}': {e}")
        return result

    def _has_provider(self, name: str) -> bool:
        """Check whether a provider is registered in the underlying registry (case-insensitive)."""
        if not isinstance(name, str):
            return False
        key = name.strip().lower()
        if hasattr(self.registry, "_providers"):
            return key in self.registry._providers
        return any(p.strip().lower() == key for p in self.registry.list_providers())

    def _resolve_providers_to_query(
        self, providers: Optional[Sequence[str]] = None
    ) -> List[str]:
        """
        Determine the list of provider identifiers to query based on requested
        providers, enabled filters, and registered providers.
        """
        if isinstance(providers, str):
            raise TypeError("'providers' must be a sequence of strings, not a string")

        if providers is not None:
            resolved: List[str] = []
            for p in providers:
                if not isinstance(p, str):
                    continue
                norm = p.strip().lower()
                if norm and norm not in resolved:
                    resolved.append(norm)
            return resolved

        # Default: all registered providers subject to enabled_providers filter
        registered = self.registry.list_providers()
        if self.enabled_providers is not None:
            return [p.lower() for p in registered if p.lower() in self.enabled_providers]
        return [p.lower() for p in registered]

    def _get_timeout_for_provider(
        self,
        provider_name: str,
        call_timeout: Optional[float] = None,
        call_provider_timeouts: Optional[Dict[str, float]] = None,
    ) -> float:
        """Calculate the effective timeout for a given provider query."""
        if call_provider_timeouts and provider_name in call_provider_timeouts:
            return max(0.1, float(call_provider_timeouts[provider_name]))
        if provider_name in self.provider_timeouts:
            return max(0.1, float(self.provider_timeouts[provider_name]))
        if call_timeout is not None:
            return max(0.1, float(call_timeout))
        return self.default_timeout

    def _execute_provider(
        self,
        provider_name: str,
        deck: DecklistInput,
        deadline: Optional[float] = None,
    ) -> DeckAnalyticsResult:
        """
        Execute analyze_deck for a single provider under its provider-level lock.
        Checks deadline before and during execution to terminate early on timeout.
        """
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("Query cancelled before execution: deadline exceeded")

        lock = self._get_provider_lock(provider_name)
        acquired = False
        try:
            if deadline is not None:
                remaining = deadline - time.perf_counter()
                if remaining <= 0 or not lock.acquire(timeout=remaining):
                    raise TimeoutError("Query timed out waiting for provider lock")
            else:
                lock.acquire()
            acquired = True

            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError("Query cancelled: deadline exceeded")

            provider = self.registry.get(provider_name)
            return provider.analyze_deck(deck)
        finally:
            if acquired:
                lock.release()

    def aggregate(
        self,
        deck: Union[DecklistInput, Dict[str, Any], Any],
        providers: Optional[Sequence[str]] = None,
        timeout: Optional[float] = None,
        provider_timeouts: Optional[Dict[str, float]] = None,
    ) -> AggregatedAnalyticsReport:
        """
        Synchronously aggregate analytics across providers using concurrent threads.

        Args:
            deck: Validated DecklistInput or coercible deck object/dict.
            providers: Optional explicit list of provider names to query.
            timeout: Optional override for default query timeout in seconds.
            provider_timeouts: Optional per-provider timeout overrides.

        Returns:
            AggregatedAnalyticsReport containing per-provider status and consolidated metrics.
        """
        deck_input = coerce_to_decklist_input(deck)
        providers_to_query = self._resolve_providers_to_query(providers)
        iso_timestamp = datetime.now(timezone.utc).isoformat()

        if not providers_to_query:
            return AggregatedAnalyticsReport(
                deck_id=deck_input.deck_id,
                deck_name=deck_input.name,
                commanders=[cmd.name for cmd in deck_input.commanders],
                timestamp=iso_timestamp,
                providers_queried=[],
                successful_providers=[],
                failed_providers=[],
                provider_statuses={},
                consolidated_metrics=ConsolidatedMetrics(summary=AnalyticsSummary()),
                provider_results={},
            )

        provider_statuses: Dict[str, ProviderAnalyticsStatus] = {}
        successful_results: Dict[str, DeckAnalyticsResult] = {}
        futures_map = {}

        # Submit queries concurrently to thread pool
        for p_name in providers_to_query:
            if self.enabled_providers is not None and p_name.lower() not in self.enabled_providers:
                provider_statuses[p_name] = ProviderAnalyticsStatus(
                    provider_name=p_name,
                    status="disabled",
                    execution_time_ms=0.0,
                    error=f"Provider '{p_name}' is disabled by service configuration",
                    supported_queries=[],
                )
                continue

            # Check if provider exists in registry first
            if not self._has_provider(p_name):
                provider_statuses[p_name] = ProviderAnalyticsStatus(
                    provider_name=p_name,
                    status="not_found",
                    execution_time_ms=0.0,
                    error=f"Provider '{p_name}' not found in registry",
                    supported_queries=[],
                )
                continue

            t_limit = self._get_timeout_for_provider(p_name, timeout, provider_timeouts)
            start_t = time.perf_counter()
            deadline = start_t + t_limit

            try:
                provider_obj = self.registry.get(p_name)
                supported = sorted(list(getattr(provider_obj, "SUPPORTED_QUERIES", [])))
            except Exception:
                supported = []

            future = self._executor.submit(self._execute_provider, p_name, deck_input, deadline)
            futures_map[p_name] = (future, t_limit, start_t, deadline, supported)

        # Collect results with per-provider timeouts
        for p_name, (future, t_limit, start_t, deadline, supported) in futures_map.items():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                future.cancel()
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                provider_statuses[p_name] = ProviderAnalyticsStatus(
                    provider_name=p_name,
                    status="timeout",
                    execution_time_ms=elapsed_ms,
                    error=f"Query timed out after {t_limit}s",
                    supported_queries=supported,
                )
                continue

            try:
                result = future.result(timeout=remaining)
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                if (time.perf_counter() - start_t) > (t_limit + 0.05):
                    provider_statuses[p_name] = ProviderAnalyticsStatus(
                        provider_name=p_name,
                        status="timeout",
                        execution_time_ms=elapsed_ms,
                        error=f"Query timed out after {t_limit}s",
                        supported_queries=supported,
                    )
                else:
                    provider_statuses[p_name] = ProviderAnalyticsStatus(
                        provider_name=p_name,
                        status="success",
                        execution_time_ms=elapsed_ms,
                        error=None,
                        supported_queries=supported,
                    )
                    successful_results[p_name] = result
            except (FutureTimeoutError, TimeoutError):
                future.cancel()
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                provider_statuses[p_name] = ProviderAnalyticsStatus(
                    provider_name=p_name,
                    status="timeout",
                    execution_time_ms=elapsed_ms,
                    error=f"Query timed out after {t_limit}s",
                    supported_queries=supported,
                )
            except Exception as e:
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                provider_statuses[p_name] = ProviderAnalyticsStatus(
                    provider_name=p_name,
                    status="error",
                    execution_time_ms=elapsed_ms,
                    error=str(e),
                    supported_queries=supported,
                )

        successful_providers = [
            p for p in providers_to_query if provider_statuses.get(p) and provider_statuses[p].status == "success"
        ]
        failed_providers = [
            p for p in providers_to_query if p not in successful_providers
        ]

        consolidated = self._consolidate_metrics(deck_input, successful_results)

        return AggregatedAnalyticsReport(
            deck_id=deck_input.deck_id,
            deck_name=deck_input.name,
            commanders=[cmd.name for cmd in deck_input.commanders],
            timestamp=iso_timestamp,
            providers_queried=providers_to_query,
            successful_providers=successful_providers,
            failed_providers=failed_providers,
            provider_statuses=provider_statuses,
            consolidated_metrics=consolidated,
            provider_results=successful_results,
        )

    async def aggregate_async(
        self,
        deck: Union[DecklistInput, Dict[str, Any], Any],
        providers: Optional[Sequence[str]] = None,
        timeout: Optional[float] = None,
        provider_timeouts: Optional[Dict[str, float]] = None,
    ) -> AggregatedAnalyticsReport:
        """
        Asynchronously aggregate analytics across providers using async execution.

        Args:
            deck: Validated DecklistInput or coercible deck object/dict.
            providers: Optional explicit list of provider names to query.
            timeout: Optional override for default query timeout in seconds.
            provider_timeouts: Optional per-provider timeout overrides.

        Returns:
            AggregatedAnalyticsReport containing per-provider status and consolidated metrics.
        """
        deck_input = coerce_to_decklist_input(deck)
        providers_to_query = self._resolve_providers_to_query(providers)
        iso_timestamp = datetime.now(timezone.utc).isoformat()

        if not providers_to_query:
            return AggregatedAnalyticsReport(
                deck_id=deck_input.deck_id,
                deck_name=deck_input.name,
                commanders=[cmd.name for cmd in deck_input.commanders],
                timestamp=iso_timestamp,
                providers_queried=[],
                successful_providers=[],
                failed_providers=[],
                provider_statuses={},
                consolidated_metrics=ConsolidatedMetrics(summary=AnalyticsSummary()),
                provider_results={},
            )

        provider_statuses: Dict[str, ProviderAnalyticsStatus] = {}
        successful_results: Dict[str, DeckAnalyticsResult] = {}
        tasks = []

        loop = asyncio.get_running_loop()

        async def _query_single_async(p_name: str) -> Tuple[str, str, float, Optional[DeckAnalyticsResult], Optional[str], List[str]]:
            if self.enabled_providers is not None and p_name.lower() not in self.enabled_providers:
                return (
                    p_name,
                    "disabled",
                    0.0,
                    None,
                    f"Provider '{p_name}' is disabled by service configuration",
                    [],
                )

            if not self._has_provider(p_name):
                return (
                    p_name,
                    "not_found",
                    0.0,
                    None,
                    f"Provider '{p_name}' not found in registry",
                    [],
                )

            t_limit = self._get_timeout_for_provider(p_name, timeout, provider_timeouts)
            start_t = time.perf_counter()
            deadline = start_t + t_limit

            try:
                provider_obj = self.registry.get(p_name)
                supported = sorted(list(getattr(provider_obj, "SUPPORTED_QUERIES", [])))
            except Exception as e:
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                return (p_name, "error", elapsed_ms, None, str(e), [])

            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, self._execute_provider, p_name, deck_input, deadline),
                    timeout=t_limit,
                )
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                return (p_name, "success", elapsed_ms, res, None, supported)
            except (asyncio.TimeoutError, TimeoutError):
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                return (
                    p_name,
                    "timeout",
                    elapsed_ms,
                    None,
                    f"Query timed out after {t_limit}s",
                    supported,
                )
            except Exception as e:
                elapsed_ms = round((time.perf_counter() - start_t) * 1000, 2)
                return (p_name, "error", elapsed_ms, None, str(e), supported)

        for p_name in providers_to_query:
            tasks.append(_query_single_async(p_name))

        results = await asyncio.gather(*tasks)

        for p_name, status, elapsed_ms, res, err, supported in results:
            provider_statuses[p_name] = ProviderAnalyticsStatus(
                provider_name=p_name,
                status=status,
                execution_time_ms=elapsed_ms,
                error=err,
                supported_queries=supported,
            )
            if status == "success" and res is not None:
                successful_results[p_name] = res

        successful_providers = [
            p for p in providers_to_query if provider_statuses.get(p) and provider_statuses[p].status == "success"
        ]
        failed_providers = [
            p for p in providers_to_query if p not in successful_providers
        ]

        consolidated = self._consolidate_metrics(deck_input, successful_results)

        return AggregatedAnalyticsReport(
            deck_id=deck_input.deck_id,
            deck_name=deck_input.name,
            commanders=[cmd.name for cmd in deck_input.commanders],
            timestamp=iso_timestamp,
            providers_queried=providers_to_query,
            successful_providers=successful_providers,
            failed_providers=failed_providers,
            provider_statuses=provider_statuses,
            consolidated_metrics=consolidated,
            provider_results=successful_results,
        )

    # =========================================================================
    # Metrics Consolidation Sub-routines
    # =========================================================================

    def _consolidate_metrics(
        self,
        deck: DecklistInput,
        successful_results: Dict[str, DeckAnalyticsResult],
    ) -> ConsolidatedMetrics:
        """
        Consolidate meta scores, recommendations, synergy, and popularity metrics
        from all successful provider responses into a coherent report.
        """
        if not successful_results:
            return ConsolidatedMetrics(summary=AnalyticsSummary())

        meta_scores = self._consolidate_meta_scores(successful_results)
        recommendations = self._consolidate_recommendations(deck, successful_results)
        synergy = self._consolidate_synergy(successful_results)
        popularity = self._consolidate_popularity(successful_results)
        summary = self._compile_summary(meta_scores, recommendations, synergy, popularity)

        return ConsolidatedMetrics(
            meta_scores=meta_scores,
            recommendations=recommendations,
            synergy=synergy,
            popularity=popularity,
            summary=summary,
        )

    def _consolidate_meta_scores(
        self, successful_results: Dict[str, DeckAnalyticsResult]
    ) -> Optional[MetaScores]:
        """Consolidate salt, power, combos, and rankings across providers."""
        salt_scores: List[Tuple[str, SaltScore]] = []
        power_scores: List[Tuple[str, PowerScore]] = []
        meta_ranks: List[float] = []
        merged_metrics: Dict[str, Any] = {}

        for p_name, res in successful_results.items():
            if not res.meta_scores:
                continue
            ms = res.meta_scores
            if ms.salt and ms.salt.score is not None:
                salt_scores.append((p_name, ms.salt))
            if ms.power and ms.power.score is not None:
                power_scores.append((p_name, ms.power))
            if ms.meta_rank is not None:
                meta_ranks.append(float(ms.meta_rank))
            if ms.provider_metrics:
                for k, v in ms.provider_metrics.items():
                    # Preserve combos and provider-specific entries
                    if k == "combos" and "combos" not in merged_metrics:
                        merged_metrics["combos"] = v
                    elif k not in merged_metrics:
                        merged_metrics[k] = v
                    else:
                        merged_metrics[f"{p_name}_{k}"] = v

        if not salt_scores and not power_scores and not meta_ranks and not merged_metrics:
            return None

        # 1. Consolidate Salt Score
        consolidated_salt: Optional[SaltScore] = None
        if salt_scores:
            avg_salt_score = round(
                sum(s.score for _, s in salt_scores) / len(salt_scores), 2
            )
            salt_sums = [s.salt_sum for _, s in salt_scores if s.salt_sum is not None]
            avg_salt_sum = (
                round(sum(salt_sums) / len(salt_sums), 2) if salt_sums else None
            )

            # Deduplicate and combine high salt cards
            high_salt_dict: Dict[str, SaltCardDetail] = {}
            for _, s in salt_scores:
                for card in s.high_salt_cards:
                    key = card.card_name.strip().lower()
                    if key not in high_salt_dict:
                        high_salt_dict[key] = card
                    else:
                        # Keep the higher salt score
                        if card.salt_score > high_salt_dict[key].salt_score:
                            high_salt_dict[key] = card

            sorted_cards = sorted(
                high_salt_dict.values(), key=lambda c: c.salt_score, reverse=True
            )
            # Re-rank high salt cards
            reranked_cards = [
                SaltCardDetail(
                    card_name=c.card_name,
                    salt_score=c.salt_score,
                    rank=idx + 1,
                    oracle_id=c.oracle_id,
                )
                for idx, c in enumerate(sorted_cards)
            ]

            consolidated_salt = SaltScore(
                score=avg_salt_score,
                salt_sum=avg_salt_sum,
                high_salt_cards=reranked_cards,
                description=None,
            )

        # 2. Consolidate Power Score
        consolidated_power: Optional[PowerScore] = None
        if power_scores:
            avg_power = round(
                sum(p.score for _, p in power_scores) / len(power_scores), 2
            )
            # Pick first available tier label and combine breakdowns
            tier_val = next((p.tier for _, p in power_scores if p.tier), None)
            combined_breakdown: Dict[str, Any] = {}
            for _, p in power_scores:
                if p.breakdown:
                    combined_breakdown.update(p.breakdown)

            consolidated_power = PowerScore(
                score=avg_power,
                tier=tier_val,
                breakdown=combined_breakdown,
                description=None,
            )

        best_meta_rank = min(meta_ranks) if meta_ranks else None

        return MetaScores(
            salt=consolidated_salt,
            power=consolidated_power,
            meta_rank=best_meta_rank,
            provider_metrics=merged_metrics,
        )

    def _consolidate_recommendations(
        self,
        deck: DecklistInput,
        successful_results: Dict[str, DeckAnalyticsResult],
    ) -> Optional[CardRecommendations]:
        """Consolidate and deduplicate card additions and cuts."""
        existing_deck_names = {name.strip().lower() for name in deck.all_card_names()}

        rec_dict: Dict[str, Dict[str, Any]] = {}
        cut_dict: Dict[str, Dict[str, Any]] = {}

        for p_name, res in successful_results.items():
            if not res.recommendations:
                continue

            # Process additions
            for item in res.recommendations.items:
                c_name = item.card_name.strip()
                c_key = c_name.lower()
                # Skip cards already in deck
                if c_key in existing_deck_names:
                    continue

                if c_key not in rec_dict:
                    rec_dict[c_key] = {
                        "card_name": c_name,
                        "synergy": item.synergy,
                        "inclusion_rate": item.inclusion_rate,
                        "reason": item.reason,
                        "oracle_id": item.oracle_id,
                        "categories": list(item.categories),
                        "sources": [p_name],
                    }
                else:
                    entry = rec_dict[c_key]
                    entry["sources"].append(p_name)
                    # Take higher synergy
                    if item.synergy is not None:
                        if entry["synergy"] is None or item.synergy > entry["synergy"]:
                            entry["synergy"] = item.synergy
                    # Take higher inclusion rate
                    if item.inclusion_rate is not None:
                        if entry["inclusion_rate"] is None or item.inclusion_rate > entry["inclusion_rate"]:
                            entry["inclusion_rate"] = item.inclusion_rate
                    if not entry["reason"] and item.reason:
                        entry["reason"] = item.reason
                    for cat in item.categories:
                        if cat not in entry["categories"]:
                            entry["categories"].append(cat)

            # Process cuts
            for cut in res.recommendations.cuts:
                c_name = cut.card_name.strip()
                c_key = c_name.lower()

                if c_key not in cut_dict:
                    cut_dict[c_key] = {
                        "card_name": c_name,
                        "synergy": cut.synergy,
                        "reason": cut.reason,
                        "oracle_id": cut.oracle_id,
                        "sources": [p_name],
                    }
                else:
                    entry = cut_dict[c_key]
                    entry["sources"].append(p_name)
                    # Pick most negative synergy
                    if cut.synergy is not None:
                        if entry["synergy"] is None or cut.synergy < entry["synergy"]:
                            entry["synergy"] = cut.synergy
                    if not entry["reason"] and cut.reason:
                        entry["reason"] = cut.reason

        if not rec_dict and not cut_dict:
            return None

        # Sort additions by synergy (descending), then inclusion_rate (descending)
        sorted_recs = sorted(
            rec_dict.values(),
            key=lambda x: (x["synergy"] is not None, x["synergy"] or 0.0, x["inclusion_rate"] or 0.0),
            reverse=True,
        )
        recommendations_list = [
            CardRecommendation(
                card_name=item["card_name"],
                synergy=item["synergy"],
                inclusion_rate=item["inclusion_rate"],
                reason=item["reason"],
                oracle_id=item["oracle_id"],
                score=None,
                categories=item["categories"],
                sources=item.get("sources", []),
            )
            for item in sorted_recs
        ]

        # Sort cuts by negative synergy (ascending)
        sorted_cuts = sorted(
            cut_dict.values(),
            key=lambda x: (x["synergy"] is not None, x["synergy"] or 0.0),
        )
        cuts_list = [
            CardCutRecommendation(
                card_name=cut["card_name"],
                synergy=cut["synergy"],
                reason=cut["reason"],
                oracle_id=cut["oracle_id"],
                sources=cut.get("sources", []),
            )
            for cut in sorted_cuts
        ]

        return CardRecommendations(
            items=recommendations_list,
            cuts=cuts_list,
            total=len(recommendations_list),
        )

    def _consolidate_synergy(
        self, successful_results: Dict[str, DeckAnalyticsResult]
    ) -> Optional[DeckSynergy]:
        """Consolidate overall deck synergy and per-card synergy ratings."""
        overall_synergies: List[float] = []
        synergy_dict: Dict[str, SynergyMetric] = {}

        for _, res in successful_results.items():
            if not res.synergy:
                continue
            if res.synergy.overall_synergy is not None:
                overall_synergies.append(res.synergy.overall_synergy)

            for sm in res.synergy.card_synergies:
                key = sm.card_name.strip().lower()
                if key not in synergy_dict:
                    synergy_dict[key] = sm
                else:
                    if sm.synergy_score > synergy_dict[key].synergy_score:
                        synergy_dict[key] = sm

        if not overall_synergies and not synergy_dict:
            return None

        avg_overall = (
            round(sum(overall_synergies) / len(overall_synergies), 3)
            if overall_synergies
            else None
        )
        sorted_synergies = sorted(
            synergy_dict.values(), key=lambda sm: sm.synergy_score, reverse=True
        )

        return DeckSynergy(
            overall_synergy=avg_overall,
            card_synergies=sorted_synergies,
        )

    def _consolidate_popularity(
        self, successful_results: Dict[str, DeckAnalyticsResult]
    ) -> Optional[DeckPopularity]:
        """Consolidate commander meta rankings, deck counts, and card inclusions."""
        ranks: List[int] = []
        deck_counts: List[int] = []
        percentiles: List[float] = []
        card_pop_dict: Dict[str, PopularityMetric] = {}

        for _, res in successful_results.items():
            if not res.popularity:
                continue
            if res.popularity.rank is not None:
                ranks.append(res.popularity.rank)
            if res.popularity.num_decks is not None:
                deck_counts.append(res.popularity.num_decks)
            if res.popularity.popularity_percentile is not None:
                percentiles.append(res.popularity.popularity_percentile)

            for pm in res.popularity.card_popularity:
                key = pm.card_name.strip().lower()
                if key not in card_pop_dict:
                    card_pop_dict[key] = pm
                else:
                    # Keep higher percentage or deck count
                    curr = card_pop_dict[key]
                    curr_pct = curr.percentage or 0.0
                    new_pct = pm.percentage or 0.0
                    if new_pct > curr_pct:
                        card_pop_dict[key] = pm

        if not ranks and not deck_counts and not percentiles and not card_pop_dict:
            return None

        best_rank = min(ranks) if ranks else None
        max_num_decks = max(deck_counts) if deck_counts else None
        best_percentile = max(percentiles) if percentiles else None

        sorted_card_pop = sorted(
            card_pop_dict.values(),
            key=lambda p: (p.percentage or 0.0, p.deck_count or 0),
            reverse=True,
        )

        return DeckPopularity(
            rank=best_rank,
            num_decks=max_num_decks,
            popularity_percentile=best_percentile,
            card_popularity=sorted_card_pop,
        )

    def _compile_summary(
        self,
        meta_scores: Optional[MetaScores],
        recommendations: Optional[CardRecommendations],
        synergy: Optional[DeckSynergy],
        popularity: Optional[DeckPopularity],
    ) -> AnalyticsSummary:
        """Compile a concise top-level summary for the aggregated report."""
        salt_score = (
            meta_scores.salt.score
            if meta_scores and meta_scores.salt and meta_scores.salt.score is not None
            else None
        )
        power_level = (
            meta_scores.power.score
            if meta_scores and meta_scores.power and meta_scores.power.score is not None
            else None
        )
        power_tier = (
            meta_scores.power.tier
            if meta_scores and meta_scores.power and meta_scores.power.tier
            else None
        )
        overall_synergy = (
            synergy.overall_synergy
            if synergy and synergy.overall_synergy is not None
            else None
        )
        commander_rank = (
            popularity.rank
            if popularity and popularity.rank is not None
            else (int(meta_scores.meta_rank) if (meta_scores and meta_scores.meta_rank is not None) else None)
        )
        total_recs = len(recommendations.items) if recommendations else 0
        total_cuts = len(recommendations.cuts) if recommendations else 0

        # Combo count from provider metrics if present
        combo_count: Optional[int] = None
        if meta_scores and meta_scores.provider_metrics:
            combos_metric = meta_scores.provider_metrics.get("combos")
            if isinstance(combos_metric, dict):
                combo_count = combos_metric.get("count")
            elif isinstance(combos_metric, list):
                combo_count = len(combos_metric)

        return AnalyticsSummary(
            salt_score=salt_score,
            power_level=power_level,
            power_tier=power_tier,
            overall_synergy=overall_synergy,
            commander_rank=commander_rank,
            total_recommendations=total_recs,
            total_cuts=total_cuts,
            total_combos=combo_count,
            extra={},
        )


# =============================================================================
# Global Default Aggregator and Functional Convenience API
# =============================================================================

default_aggregator = DeckAnalyticsAggregator()


def aggregate_deck_analytics(
    deck: Union[DecklistInput, Dict[str, Any], Any],
    providers: Optional[Sequence[str]] = None,
    timeout: Optional[float] = None,
    provider_timeouts: Optional[Dict[str, float]] = None,
) -> AggregatedAnalyticsReport:
    """
    Convenience functional API to synchronously aggregate deck analytics
    using the default global aggregator.
    """
    return default_aggregator.aggregate(
        deck=deck,
        providers=providers,
        timeout=timeout,
        provider_timeouts=provider_timeouts,
    )
