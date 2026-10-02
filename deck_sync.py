"""
Dual-source deck sync -- fetches a deck's cardlist from every provider
linked on its record (see deck_links.py) and merges the results into one
local decklist, in a single operation.

Design (see docs/DUAL_SOURCE_SYNC.md):

- A deck can carry a link for more than one syncable provider at once
  (currently Archidekt and Moxfield; Commander Salt is link-only -- it has
  no cardlist to merge, see deck_links.SYNCABLE_PROVIDERS).
- Each linked provider is fetched independently. One provider being
  unreachable (network error, 404, rate limit, etc.) does NOT abort the
  sync for the other -- that provider's failure is collected and surfaced
  as a warning, and the sync still completes using whatever source(s)
  succeeded.
- If zero of the linked providers are reachable, the sync fails outright
  (nothing to merge) and the deck's cardlist is left untouched.
- Merge strategy: union of both cardlists by card name, taking the MAX
  quantity seen across sources for any card that appears on both. This
  favors a complete decklist over an undercount when sources disagree
  (e.g. one side not yet reflecting a recent copy added on the other).
  Commanders/commander_uids/color are unioned too (dedup by name, in the
  order first seen with Archidekt before Moxfield), so either platform's
  commander entry is honored even if only one source lists it.
- A conflict is logged whenever two sources both list the same card but
  with different quantities, or when a card appears on only one side --
  the whole report (per-source pulled counts, conflicts, failures) is
  returned so callers can show the user exactly what happened.
"""
from dataclasses import dataclass, field
from typing import Optional

import deck_links
import registry
from providers import fetch_deck as provider_fetch_deck
from providers import ProviderError


class DeckSyncError(ValueError):
    """Raised when a sync cannot produce any usable result at all (e.g. no
    syncable provider is linked, or every linked provider failed)."""


@dataclass
class SourceResult:
    provider: str
    status: str  # "ok" | "error"
    card_count: int = 0
    error: Optional[str] = None


@dataclass
class CardConflict:
    card_name: str
    quantities: dict  # provider -> quantity
    resolved_quantity: int


@dataclass
class SyncReport:
    registry_id: str
    sources: list = field(default_factory=list)       # list[SourceResult]
    conflicts: list = field(default_factory=list)      # list[CardConflict]
    merged_card_count: int = 0
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "registry_id": self.registry_id,
            "sources": [
                {
                    "provider": s.provider,
                    "status": s.status,
                    "card_count": s.card_count,
                    "error": s.error,
                }
                for s in self.sources
            ],
            "conflicts": [
                {
                    "card_name": c.card_name,
                    "quantities": c.quantities,
                    "resolved_quantity": c.resolved_quantity,
                }
                for c in self.conflicts
            ],
            "merged_card_count": self.merged_card_count,
            "warnings": self.warnings,
        }


def _fetch_one(provider: str, deck_id: str) -> dict:
    """Fetch+normalize one provider's deck. Raises on failure -- callers
    catch broadly so one bad source never takes down the whole sync."""
    return provider_fetch_deck(provider, deck_id)


def merge_normalized_decks(fetched: dict) -> tuple:
    """Merge `fetched` (provider -> normalized deck dict, successes only)
    into one cardlist. Returns (cards, commanders, commander_uids, color,
    conflicts) where conflicts is a list[CardConflict].

    Merge strategy: union of card names; quantity = max across sources.
    Commanders/commander_uids: union, first-seen order, Archidekt before
    Moxfield when both list a deck (dict iteration order follows the
    providers.PROVIDERS/SYNCABLE_PROVIDERS declaration order upstream).
    Color: union of every WUBRG letter seen across sources, "C" if none.
    """
    quantities_by_card = {}  # card_name -> {provider: qty}
    commanders, commander_uids = [], []
    color_letters = set()

    for provider_name, normalized in fetched.items():
        for card in normalized.get("cards", []):
            name = card.get("name")
            if not name:
                continue
            qty = card.get("quantity", 1)
            quantities_by_card.setdefault(name, {})[provider_name] = qty

        for commander_name in normalized.get("commanders", []) or []:
            if commander_name not in commanders:
                commanders.append(commander_name)
        for uid in normalized.get("commander_uids", []) or []:
            if uid not in commander_uids:
                commander_uids.append(uid)

        for letter in normalized.get("color", "") or "":
            if letter != "C":
                color_letters.add(letter)

    cards = []
    conflicts = []
    for name, by_provider in quantities_by_card.items():
        resolved_qty = max(by_provider.values())
        cards.append({"name": name, "quantity": resolved_qty})

        distinct_qtys = set(by_provider.values())
        if len(by_provider) < len(fetched) or len(distinct_qtys) > 1:
            # Either not every source listed this card, or sources
            # disagreed on quantity -- both are worth flagging.
            conflicts.append(
                CardConflict(
                    card_name=name,
                    quantities=dict(by_provider),
                    resolved_quantity=resolved_qty,
                )
            )

    cards.sort(key=lambda c: c["name"])
    color = "".join(c for c in "WUBRG" if c in color_letters) or "C"

    return cards, commanders, commander_uids, color, conflicts


def sync_deck(r, registry_id: str) -> SyncReport:
    """Sync one deck from every syncable provider linked on its record.

    Fetches each linked provider independently (a failure on one doesn't
    stop the others), merges whatever succeeded via `merge_normalized_decks`,
    writes the result with `registry.update_deck_cards`, and returns a
    `SyncReport` describing what was pulled from each source, any merge
    conflicts, and any source failures.

    Raises DeckSyncError if the deck has no syncable provider links, or if
    every linked provider failed (nothing to merge, deck left untouched).
    """
    deck = registry.find_deck(r, registry_id)
    if deck is None:
        raise DeckSyncError(f"deck not found: {registry_id}")

    links = deck_links.get_links(deck)
    syncable_links = {
        provider: entry
        for provider, entry in links.items()
        if provider in deck_links.SYNCABLE_PROVIDERS
    }

    if not syncable_links:
        raise DeckSyncError(
            f"deck {registry_id} has no linked Archidekt/Moxfield source to sync from"
        )

    report = SyncReport(registry_id=registry_id)
    fetched = {}

    for provider, entry in syncable_links.items():
        deck_identifier = entry.get("deck_id") or entry.get("identifier")
        try:
            normalized = _fetch_one(provider, deck_identifier)
            fetched[provider] = normalized
            report.sources.append(
                SourceResult(provider=provider, status="ok", card_count=len(normalized.get("cards", [])))
            )
        except ProviderError as exc:
            report.sources.append(SourceResult(provider=provider, status="error", error=str(exc)))
            report.warnings.append(f"{provider}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the sync
            report.sources.append(SourceResult(provider=provider, status="error", error=str(exc)))
            report.warnings.append(f"{provider}: {exc}")

    if not fetched:
        raise DeckSyncError(
            f"all linked sources failed for deck {registry_id}: "
            + "; ".join(report.warnings)
        )

    cards, commanders, commander_uids, color, conflicts = merge_normalized_decks(fetched)
    report.conflicts = conflicts
    report.merged_card_count = len(cards)

    registry.update_deck_cards(
        r,
        registry_id,
        cards=cards,
        commanders=commanders,
        commander_uids=commander_uids,
        color=color,
    )

    return report
