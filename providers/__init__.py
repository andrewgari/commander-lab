"""
Deck-source providers for Commander Lab.

Each provider module exposes a `fetch_deck(identifier)` function that returns
a normalized deck dict (see NORMALIZED_DECK_SHAPE below) so the rest of the
app (registry.py, sync.py, app.py) never has to know provider-specific
payload shapes.

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
    "url": str,                       # canonical provider URL
}
"""
from . import archidekt, moxfield  # noqa: F401

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
