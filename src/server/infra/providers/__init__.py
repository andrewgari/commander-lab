"""External providers for Commander Lab.

Folds together the old top-level ``providers`` package (deck-source providers:
Archidekt/Moxfield import + explicit-push) and the old ``src/integrations``
package (read-only card-data integrations: Scryfall, EDHRec, CommanderSalt,
plus the unified card service), per docs/ARCHITECTURE.md slice 0.

NORMALIZED_DECK_SHAPE = {
    "source": "archidekt" | "moxfield",
    "source_id": str,                 # provider-native id (Archidekt int as str, Moxfield publicId)
    "name": str,
    "color": str,                     # e.g. "WUBRG" subset or "C"
    "commanders": [str, ...],
    "commander_uids": [str, ...],     # scryfall printing uids, for card images
    "folder": str,
    "description": str,
    "cards": [{"name": str, "quantity": int}, ...],   # mainboard only, structural boards excluded
    "slots": {slot: [{"name": str, "quantity": int}, ...], ...},
                                    # named deck slots (see deck_slots.DeckSlot)
    "parse_flags": [str, ...],
    "url": str,                       # canonical provider URL
}
"""
from . import archidekt, moxfield  # noqa: F401
from .errors import PushError, PushNotConfigured  # noqa: F401

from .card_service import (
    ALL_PROVIDERS,
    CardNotFoundError,
    CardServiceError,
    UnifiedCardService,
)
from .commandersalt import (
    CommanderSaltClient,
    CommanderSaltError,
    CommanderSaltNotFoundError,
    CommanderSaltRateLimitError,
    CommanderSaltRequestError,
    CommanderSaltResponseError,
)
from .edhrec import (
    EDHRecClient,
    EDHRecError,
    EDHRecNotFoundError,
    EDHRecRateLimitError,
    EDHRecRequestError,
    EDHRecResponseError,
    commander_slug,
    slugify_card_name,
)
from .models import (
    CardMetadata,
    CardSalt,
    CardSynergy,
    ProviderStatus,
    UnifiedCardData,
)
from .scryfall import (
    ScryfallCard,
    ScryfallClient,
    ScryfallError,
    ScryfallNotFoundError,
    ScryfallRateLimitError,
    ScryfallRequestError,
    ScryfallSearchResult,
)

PROVIDERS = {
    "archidekt": archidekt,
    "moxfield": moxfield,
}


class ProviderError(ValueError):
    """Raised for unrecognized providers or unparsable identifiers/URLs."""


def fetch_deck(provider: str, identifier: str) -> dict:
    mod = PROVIDERS.get(provider)
    if not mod:
        raise ProviderError(f"unknown provider: {provider}")
    return mod.fetch_deck(identifier)


def list_decks(provider: str, username: str) -> list:
    """Return every deck identifier owned by `username` on `provider`.

    Dispatches to providers/{archidekt,moxfield}.list_decks(username), each
    of which raises a clear provider-specific error (not a silent empty
    list) for a wrong/nonexistent username. See docs/LINKED_ACCOUNTS.md.
    """
    mod = PROVIDERS.get(provider)
    if not mod:
        raise ProviderError(f"unknown provider: {provider}")
    return mod.list_decks(username)


def registry_id(deck: dict) -> str:
    return f"{deck['source']}:{deck['source_id']}"


def push_deck(provider: str, identifier: str, cards: list, commanders=None) -> dict:
    """Write `cards` to the provider's deck (commander.lab -> external).
    Only called from deck_sync.push_deck, i.e. an explicit user Push.
    Raises PushNotConfigured when the provider's credential env var is unset."""
    mod = PROVIDERS.get(provider)
    if not mod:
        raise ProviderError(f"unknown provider: {provider}")
    if not hasattr(mod, "push_deck"):
        raise PushNotConfigured(f"{provider} does not support push")
    return mod.push_deck(identifier, cards, commanders)


def create_deck(provider: str, name: str, cards: list, commanders=None, description: str = "", private: bool = True) -> dict:
    """Create a NEW deck on `provider` from a decklist. Explicit user export only."""
    mod = PROVIDERS.get(provider)
    if not mod:
        raise ProviderError(f"unknown provider: {provider}")
    if not hasattr(mod, "create_deck"):
        raise PushNotConfigured(f"{provider} does not support deck creation")
    return mod.create_deck(name, cards, commanders, description, private)


def push_configured(provider: str) -> bool:
    mod = PROVIDERS.get(provider)
    return bool(mod and hasattr(mod, "push_configured") and mod.push_configured())
