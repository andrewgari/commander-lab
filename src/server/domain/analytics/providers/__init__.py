"""
Concrete adapter implementations of `analytics.provider.BaseAnalyticsProvider`
for third-party deck analytics data sources.
"""
from .commandersalt import (
    CommanderSaltClient,
    CommanderSaltError,
    CommanderSaltNotFoundError,
    CommanderSaltNotIngestedError,
    CommanderSaltProvider,
    CommanderSaltRequestError,
    CommanderSaltResponseError,
    resolve_source_url,
)
from .edhrec import (
    EDHRecClient,
    EDHRecError,
    EDHRecNotFoundError,
    EDHRecProvider,
    EDHRecRequestError,
    EDHRecResponseError,
    commander_slug,
    slugify_card_name,
)

__all__ = [
    "CommanderSaltClient",
    "CommanderSaltProvider",
    "CommanderSaltError",
    "CommanderSaltNotFoundError",
    "CommanderSaltNotIngestedError",
    "CommanderSaltRequestError",
    "CommanderSaltResponseError",
    "resolve_source_url",
    "EDHRecClient",
    "EDHRecProvider",
    "EDHRecError",
    "EDHRecNotFoundError",
    "EDHRecRequestError",
    "EDHRecResponseError",
    "commander_slug",
    "slugify_card_name",
]
