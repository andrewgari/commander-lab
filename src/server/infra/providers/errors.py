"""Shared provider exceptions for the explicit push path (deck_sync.push_deck).

Kept in their own module so providers/archidekt.py and providers/moxfield.py
can raise them without importing the package __init__ (which imports them).
"""


class PushNotConfigured(RuntimeError):
    """The provider supports push in principle, but the credential it needs
    (an env var) isn't set. Surfaced to the UI as 501 push_not_configured."""


class PushError(RuntimeError):
    """The provider rejected or failed a push (auth expired, unresolvable
    card names, non-2xx response). Nothing is retried automatically."""
