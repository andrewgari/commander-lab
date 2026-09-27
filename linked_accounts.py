"""
Linked-accounts registry — provider accounts (Archidekt/Moxfield username)
that Commander Lab pulls decks from on a recurring/manual basis, on top of
the existing single-deck import flow.

Mirrors instances.py/registry.py/cards.py in shape: all reads/writes to the
`linked_accounts` key should go through this module.

Storage: a new Redis key `linked_accounts` (JSON array). Each entry:
    id                 f"{provider}:{username}" -- stable, dedup key
    provider           "archidekt" | "moxfield"
    username           provider-native username, as given
    enabled            bool -- sync_all() only syncs enabled accounts
    created_at         ISO8601 UTC
    last_synced_at     ISO8601 UTC | None
    last_sync_result   {"decks_synced": int, "failures": [...]} | None

Design (see docs/LINKED_ACCOUNTS.md): pull-only, non-destructive. Unlinking
an account (remove_account) never touches decks already pulled from it --
those decks and their linked_account_id stamp are left exactly as-is.
"""
import json
from datetime import datetime, timezone
from typing import Optional

import registry
from providers import PROVIDERS

LINKED_ACCOUNTS_KEY = "linked_accounts"


class LinkedAccountError(ValueError):
    """Raised for unknown accounts/providers or duplicate links."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(r) -> list:
    raw = r.get(LINKED_ACCOUNTS_KEY)
    return json.loads(raw) if raw else []


def _save(r, accounts: list) -> None:
    r.set(LINKED_ACCOUNTS_KEY, json.dumps(accounts))


def _account_id(provider: str, username: str) -> str:
    return f"{provider}:{username}"


def list_accounts(r) -> list:
    return _load(r)


def find_account(r, account_id: str) -> Optional[dict]:
    for account in _load(r):
        if account.get("id") == account_id:
            return account
    return None


def _provider_module(provider: str):
    mod = PROVIDERS.get(provider)
    if not mod:
        raise LinkedAccountError(f"unknown provider: {provider}")
    return mod


def add_account(r, provider: str, username: str) -> dict:
    """Link a new provider account. Dedups on id=f'{provider}:{username}' --
    raises LinkedAccountError if already linked, rather than silently
    re-adding or double-syncing. Probes with a single list_decks call
    first so a typo'd/nonexistent username fails fast with a clear error
    (providers raise *UserNotFound for that), instead of persisting a
    bad account. New accounts start enabled=true.
    """
    provider = (provider or "").strip()
    username = (username or "").strip()
    if not username:
        raise LinkedAccountError("username must not be empty")

    mod = _provider_module(provider)
    account_id = _account_id(provider, username)

    accounts = _load(r)
    if any(a.get("id") == account_id for a in accounts):
        raise LinkedAccountError(f"account already linked: {account_id}")

    # Probe: one list_decks call so a typo fails fast, before persisting.
    # Provider-specific errors (e.g. ArchidektUserNotFound/MoxfieldUserNotFound)
    # are ValueError subclasses but not LinkedAccountError -- wrap them so the
    # FastAPI route (which only catches LinkedAccountError) returns a clean
    # 400 instead of an unhandled 500.
    try:
        mod.list_decks(username)
    except LinkedAccountError:
        raise
    except ValueError as exc:
        raise LinkedAccountError(str(exc)) from exc

    account = {
        "id": account_id,
        "provider": provider,
        "username": username,
        "enabled": True,
        "created_at": _now(),
        "last_synced_at": None,
        "last_sync_result": None,
    }
    accounts.append(account)
    _save(r, accounts)

    return account


def remove_account(r, account_id: str) -> bool:
    """Remove a linked-account registry entry only. Does NOT touch decks
    already synced from it, and does NOT null their linked_account_id --
    per the explicit pull-only/non-destructive design, unlinking is purely
    a registry-bookkeeping operation. Raises LinkedAccountError if the
    account_id is unknown.
    """
    accounts = _load(r)
    remaining = [a for a in accounts if a.get("id") != account_id]
    if len(remaining) == len(accounts):
        raise LinkedAccountError(f"linked account not found: {account_id}")

    _save(r, remaining)
    return True


def _stamp_linked_account(r, deck_registry_id: str, account_id: str) -> None:
    """Set linked_account_id on the stored deck matching deck_registry_id.
    Small helper local to this module rather than registry.py, since
    linked_account_id is a linked-accounts concern, not core deck data.
    """
    raw = r.get(registry.DECKS_KEY)
    decks = json.loads(raw) if raw else []
    for deck in decks:
        if registry.registry_id_of(deck) == deck_registry_id:
            deck["linked_account_id"] = account_id
            break
    r.set(registry.DECKS_KEY, json.dumps(decks))


def sync_account(r, account_id: str) -> dict:
    """Pull every deck for one linked account: list_decks + fetch_deck +
    registry.upsert_deck per deck, stamping linked_account_id on each
    upserted deck. Collects per-deck failures instead of aborting the
    whole sync -- one bad deck doesn't block the rest. Updates
    last_synced_at/last_sync_result on the account record and returns it.
    """
    accounts = _load(r)
    account = None
    idx = None
    for i, a in enumerate(accounts):
        if a.get("id") == account_id:
            account, idx = a, i
            break
    if account is None:
        raise LinkedAccountError(f"linked account not found: {account_id}")

    mod = _provider_module(account["provider"])

    failures = []
    synced = 0
    try:
        deck_ids = mod.list_decks(account["username"])
    except Exception as exc:  # noqa: BLE001 - record and stop, nothing to sync
        deck_ids = []
        failures.append({"deck_id": None, "error": f"list_decks failed: {exc}"})

    for deck_id in deck_ids:
        try:
            normalized = mod.fetch_deck(deck_id)
            deck = registry.upsert_deck(r, normalized)
            _stamp_linked_account(r, registry.registry_id_of(deck), account_id)
            synced += 1
        except Exception as exc:  # noqa: BLE001 - one bad deck must not abort the sync
            failures.append({"deck_id": deck_id, "error": str(exc)})

    account["last_synced_at"] = _now()
    account["last_sync_result"] = {"decks_synced": synced, "failures": failures}
    accounts[idx] = account
    _save(r, accounts)

    return account


def sync_all(r) -> list:
    """Run sync_account for every enabled linked account. Returns the list
    of updated account records. A failure syncing one account (e.g. the
    provider itself being unreachable) doesn't stop the others.

    Every element of the returned list is a standard account record (the
    same shape persisted by add_account/sync_account), even when the sync
    itself raised: in that case the account's last_synced_at/
    last_sync_result are updated in place to reflect the failure and the
    updated record is persisted, exactly as a successful sync would.
    """
    accounts = _load(r)
    results = []
    for idx, account in enumerate(accounts):
        if not account.get("enabled", True):
            continue
        try:
            updated = sync_account(r, account["id"])
            accounts[idx] = updated
            results.append(updated)
        except Exception as exc:  # noqa: BLE001 - one bad account must not abort sync_all
            account["last_synced_at"] = _now()
            account["last_sync_result"] = {
                "decks_synced": 0,
                "failures": [{"deck_id": None, "error": str(exc)}],
            }
            accounts[idx] = account
            results.append(account)

    _save(r, accounts)
    return results
