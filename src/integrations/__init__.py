"""Third-party MTG API integrations for Commander Lab."""

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

__all__ = [
    # Unified Service
    "UnifiedCardService",
    "CardNotFoundError",
    "CardServiceError",
    "ALL_PROVIDERS",
    # Domain Models
    "UnifiedCardData",
    "CardMetadata",
    "CardSynergy",
    "CardSalt",
    "ProviderStatus",
    # Scryfall
    "ScryfallClient",
    "ScryfallCard",
    "ScryfallSearchResult",
    "ScryfallError",
    "ScryfallNotFoundError",
    "ScryfallRateLimitError",
    "ScryfallRequestError",
    # EDHRec
    "EDHRecClient",
    "EDHRecError",
    "EDHRecNotFoundError",
    "EDHRecRateLimitError",
    "EDHRecRequestError",
    "EDHRecResponseError",
    "slugify_card_name",
    "commander_slug",
    # Commander Salt
    "CommanderSaltClient",
    "CommanderSaltError",
    "CommanderSaltNotFoundError",
    "CommanderSaltRateLimitError",
    "CommanderSaltRequestError",
    "CommanderSaltResponseError",
]
