"""
Deck source links -- per-deck registry of external source references
(Archidekt / Moxfield / Commander Salt) so a single local deck can be kept
in sync with more than one provider at once.

This is distinct from `linked_accounts.py` (which links a whole provider
*account* by username and pulls every deck that account owns). This module
links individual *decks* already in the registry to one or more provider
deck URLs/ids, so e.g. a deck that's tracked on both Moxfield and Archidekt
can be synced from both in a single operation (see deck_sync.py).

Storage: links live directly on the deck record (the existing `decks`
Redis key / registry.py), under a `links` dict:

    deck["links"] = {
        "archidekt":     {"identifier": "<id or url as given>", "deck_id": "<parsed id>"},
        "moxfield":      {"identifier": "<id or url as given>", "deck_id": "<parsed id>"},
        "commandersalt": {"identifier": "<url as given>"},
    }

Only providers with a registered `parse_identifier` (archidekt, moxfield)
get their identifier validated/normalized up front, so a typo'd URL is
rejected at link-time rather than at the next sync. Commander Salt has no
such parser (it keys off a raw source URL -- see
analytics/providers/commandersalt.py resolve_source_url), so its link is
stored as-is.
"""
import json
from typing import Optional

import registry
from providers import PROVIDERS

LINKABLE_PROVIDERS = {"archidekt", "moxfield", "commandersalt"}

# Providers that participate in deck-cardlist sync (deck_sync.py). Commander
# Salt is link-only: it's an analytics source, not a deck/cardlist source.
SYNCABLE_PROVIDERS = {"archidekt", "moxfield"}


class DeckLinkError(ValueError):
    """Raised for an unknown provider or an unparsable identifier/URL."""


def _parse_for_provider(provider: str, identifier: str) -> str:
    """Return the normalized provider-native deck id for `identifier`,
    using that provider's own parse_identifier when available. Commander
    Salt has no parser -- its identifier is stored verbatim.
    """
    mod = PROVIDERS.get(provider)
    if mod is not None and hasattr(mod, "parse_identifier"):
        return mod.parse_identifier(identifier)
    return identifier.strip()


def get_links(deck: dict) -> dict:
    return dict(deck.get("links") or {})


def set_link(r, registry_id: str, provider: str, identifier: Optional[str]) -> dict:
    """Add, edit, or clear (identifier=None/empty) one provider's link on
    the deck identified by `registry_id`. This is a sync-process operation,
    not copy/paste: the normalized/validated link is written to the deck
    record itself, and a later call with a different identifier for the
    same provider edits it in place (no duplicate links per provider).

    Raises DeckLinkError for an unknown provider, an unparsable identifier,
    or an unknown deck.
    """
    provider = (provider or "").strip().lower()
    if provider not in LINKABLE_PROVIDERS:
        raise DeckLinkError(
            f"unknown provider: {provider} (supported: {', '.join(sorted(LINKABLE_PROVIDERS))})"
        )

    decks = registry._load_decks(r)
    target = None
    for d in decks:
        if registry.registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if target is None:
        raise DeckLinkError(f"deck not found: {registry_id}")

    links = dict(target.get("links") or {})

    identifier = (identifier or "").strip()
    if not identifier:
        links.pop(provider, None)
    else:
        try:
            deck_id = _parse_for_provider(provider, identifier)
        except ValueError as exc:
            raise DeckLinkError(str(exc)) from exc
        entry = {"identifier": identifier}
        if deck_id:
            entry["deck_id"] = deck_id
        links[provider] = entry

    target["links"] = links
    registry._save_decks(r, decks)
    return target


def remove_link(r, registry_id: str, provider: str) -> dict:
    return set_link(r, registry_id, provider, None)
