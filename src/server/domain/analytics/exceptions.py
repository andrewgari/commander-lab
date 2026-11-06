"""
Exception hierarchy for deck analytics and provider integration.
"""


class AnalyticsError(Exception):
    """Base exception for all deck analytics errors."""


class AnalyticsProviderError(AnalyticsError):
    """Base exception for errors raised by analytics providers or registration."""


class ProviderNotFoundError(AnalyticsProviderError, KeyError):
    """Raised when an analytics provider name is not found in the registry."""


class ProviderRegistrationError(AnalyticsProviderError, ValueError):
    """Raised when a provider fails registration validation or duplicates an existing entry."""


class UnsupportedAnalyticsQueryError(AnalyticsProviderError, NotImplementedError):
    """Raised when an analytics provider does not support the requested query."""
