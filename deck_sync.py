"""
Explicit, user-initiated deck sync with external deckbuilders.

Two operations, never run on a timer, schedule, page load, or save hook --
only from the Update / Push API routes (see docs/SYNC_UPDATE_PUSH.md):

- update_deck(r, id, provider=None, dry_run=False): external -> commander.lab
- push_deck(r, id, provider, dry_run=False):        commander.lab -> external

Update details: dual-source sync -- fetches a deck's cardlist from every provider
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
from datetime import datetime, timezone
from typing import Optional

import deck_links
import deck_slots
import registry
from providers import fetch_deck as provider_fetch_deck
from providers import push_configured as provider_push_configured
from providers import push_deck as provider_push_deck
from providers import ProviderError, PushError, PushNotConfigured


class DeckSyncError(ValueError):
    """Raised when a sync cannot produce any usable result at all (e.g. no
    syncable provider is linked, or every linked provider failed).
    `code`/`status` map it to the API error shape (see app.py)."""

    code = "sync_failed"
    status = 400


class DeckNotFound(DeckSyncError):
    code = "deck_not_found"
    status = 404


class PushUnsupported(DeckSyncError):
    """Push to this provider can't run: the provider has no write path, or
    its credential isn't configured. Never a silent no-op."""

    code = "push_unsupported"
    status = 501

    def __init__(self, message, code=None):
        super().__init__(message)
        if code:
            self.code = code


class ProviderFailure(DeckSyncError):
    """The external provider failed or rejected the request."""

    code = "provider_error"
    status = 502


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
    changes: dict = field(default_factory=dict)       # see diff_cardlists
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
            "changes": self.changes,
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
    commander_uids: index-aligned with commanders (one printing per name,
    first source wins); empty when any commander has no pairable printing.
    Commanders: union, first-seen order, Archidekt before
    Moxfield when both list a deck (dict iteration order follows the
    providers.PROVIDERS/SYNCABLE_PROVIDERS declaration order upstream).
    Color: union of every WUBRG letter seen across sources, "C" if none.
    """
    quantities_by_card = {}  # card_name -> {provider: qty}
    slot_quantities = {}  # slot -> card_name -> {provider: qty}
    parse_flags: list = []
    commanders, commander_uids = [], []
    uid_by_name, loose_uids = {}, []
    color_letters = set()

    for provider_name, normalized in fetched.items():
        for flag in normalized.get("parse_flags") or []:
            if flag not in parse_flags:
                parse_flags.append(flag)
        for slot, entries in (normalized.get("slots") or {}).items():
            for card in entries or []:
                name = card.get("name")
                if name:
                    slot_quantities.setdefault(slot, {}).setdefault(name, {})[provider_name] = (
                        card.get("quantity", 1)
                    )
        for card in normalized.get("cards", []):
            name = card.get("name")
            if not name:
                continue
            qty = card.get("quantity", 1)
            quantities_by_card.setdefault(name, {})[provider_name] = qty

        names = normalized.get("commanders", []) or []
        uids = normalized.get("commander_uids", []) or []
        for commander_name in names:
            if commander_name not in commanders:
                commanders.append(commander_name)
        # Providers list names and uids in parallel; pair them only when the
        # lengths agree, so a printing never lands on the wrong commander.
        if len(names) == len(uids):
            for commander_name, uid in zip(names, uids):
                if uid:
                    uid_by_name.setdefault(commander_name, uid)
        for uid in uids:
            if uid and uid not in loose_uids:
                loose_uids.append(uid)

        for letter in normalized.get("color", "") or "":
            if letter != "C":
                color_letters.add(letter)

    # commander_uids stays index-aligned with commanders: one printing per
    # name, first source wins. If any commander's printing can't be paired,
    # drop the uids so views fall back to name-based images, never mis-pair.
    if commanders:
        if all(n in uid_by_name for n in commanders):
            commander_uids = [uid_by_name[n] for n in commanders]
    else:
        commander_uids = loose_uids

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

    # Merge slots the same way: union of card names per slot, max quantity
    # across sources. Provenance markers (remapped_from) are kept when every
    # source listing the card carries one.
    slots = deck_slots.empty_slots()
    for slot, by_card in slot_quantities.items():
        canonical = slot if slot in deck_slots.DeckSlot.VALID else deck_slots.normalize_slot(slot)
        if canonical is None:
            canonical = deck_slots.DeckSlot.MAYBEBOARD
        for name, by_provider in by_card.items():
            entry = {"name": name, "quantity": max(by_provider.values())}
            remapped_everywhere = True
            for provider_name in by_provider:
                rows = (fetched[provider_name].get("slots") or {}).get(slot) or []
                if not any(r.get("name") == name and r.get("remapped_from") == "sideboard" for r in rows):
                    remapped_everywhere = False
                    break
            if remapped_everywhere:
                entry["remapped_from"] = "sideboard"
            slots[canonical].append(entry)
        slots[canonical].sort(key=lambda c: c["name"])

    return cards, commanders, commander_uids, color, conflicts, slots, parse_flags


DIRECTION = {"update": "external_to_local", "push": "local_to_external"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def diff_cardlists(before: list, after: list) -> dict:
    """What changes going from `before` to `after` ({name, quantity} lists):
    {"added": [{name, quantity}], "removed": [{name, quantity}],
     "changed": [{name, from, to}], "counts": {added, removed, changed}}."""
    def totals(cards):
        out = {}
        for c in cards or []:
            if c.get("name"):
                out[c["name"]] = out.get(c["name"], 0) + int(c.get("quantity") or 1)
        return out

    b, a = totals(before), totals(after)
    added = [{"name": n, "quantity": a[n]} for n in sorted(a) if n not in b]
    removed = [{"name": n, "quantity": b[n]} for n in sorted(b) if n not in a]
    changed = [{"name": n, "from": b[n], "to": a[n]} for n in sorted(a) if n in b and a[n] != b[n]]
    return {"added": added, "removed": removed, "changed": changed,
            "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)}}


def _syncable_links(deck: dict) -> dict:
    return {p: e for p, e in deck_links.get_links(deck).items() if p in deck_links.SYNCABLE_PROVIDERS}


def _link_id(entry: dict) -> str:
    return entry.get("deck_id") or entry.get("identifier")


def _stamp(r, registry_id: str, providers: list, field_name: str, ts: str) -> None:
    """Record last_updated_at / last_pushed_at on the deck record: per provider
    under deck["sync_state"][provider] and deck-wide at deck[field_name]."""
    decks = registry._load_decks(r)
    for d in decks:
        if registry.registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            state = dict(d.get("sync_state") or {})
            for p in providers:
                state[p] = {**(state.get(p) or {}), field_name: ts}
            d["sync_state"] = state
            d[field_name] = ts
            break
    registry._save_decks(r, decks)


def _find(r, registry_id: str) -> dict:
    deck = registry.find_deck(r, registry_id)
    if deck is None:
        raise DeckNotFound(f"deck not found: {registry_id}")
    return deck


def _check_provider(deck: dict, provider: str, links: dict) -> None:
    if provider not in deck_links.SYNCABLE_PROVIDERS:
        raise DeckSyncError(
            f"unknown provider: {provider} (supported: {', '.join(sorted(deck_links.SYNCABLE_PROVIDERS))})"
        )
    if provider not in links:
        raise DeckSyncError(f"deck {registry.registry_id_of(deck)} has no {provider} link")


def update_deck(r, registry_id: str, provider: Optional[str] = None, dry_run: bool = False) -> dict:
    """UPDATE: pull the linked external deck(s) INTO the commander.lab deck.
    Modifies the commander.lab deck only. Runs only when a user asks for it.

    provider: "archidekt" | "moxfield" to pull from just that link, or None to
    pull every syncable link and merge (union, max quantity -- see module doc).
    dry_run: fetch and diff, but write nothing (confirmation-modal preview).

    Returns a UI result dict (see docs/SYNC_UPDATE_PUSH.md). Raises
    DeckNotFound / DeckSyncError (no link, unknown provider, every source failed).
    """
    deck = _find(r, registry_id)
    registry_id = registry.registry_id_of(deck)
    links = _syncable_links(deck)
    if provider:
        _check_provider(deck, provider, links)
        links = {provider: links[provider]}
    if not links:
        raise DeckSyncError(f"deck {registry_id} has no linked Archidekt/Moxfield source to update from")

    report = SyncReport(registry_id=registry_id)
    fetched = {}
    for name, entry in links.items():
        try:
            normalized = _fetch_one(name, _link_id(entry))
            fetched[name] = normalized
            report.sources.append(SourceResult(provider=name, status="ok", card_count=len(normalized.get("cards", []))))
        except Exception as exc:  # noqa: BLE001 - one bad source must not abort the others
            report.sources.append(SourceResult(provider=name, status="error", error=str(exc)))
            report.warnings.append(f"{name}: {exc}")

    if not fetched:
        raise ProviderFailure(f"all linked sources failed for deck {registry_id}: " + "; ".join(report.warnings))

    cards, commanders, commander_uids, color, conflicts, slots, parse_flags = (
        merge_normalized_decks(fetched)
    )
    report.conflicts = conflicts
    report.merged_card_count = len(cards)
    report.changes = diff_cardlists(deck.get("cards"), cards)
    if deck_slots.SIDEBOARD_MAPPED_FLAG in parse_flags:
        report.warnings.append(
            "A 'Sideboard' board was found on a linked source and mapped to the "
            "Maybeboard slot by default; rename it to 'Maybeboard' on the source "
            "to make this explicit."
        )

    ts = _now()
    if not dry_run:
        registry.update_deck_cards(r, registry_id, cards=cards, commanders=commanders,
                                   commander_uids=commander_uids, color=color,
                                   slots=slots, parse_flags=parse_flags)
        # The Archidekt pull carries the deck's folder: refresh it and apply the
        # physical-folder rule (registry.apply_folder_rule -> set_status).
        arch = fetched.get("archidekt")
        if arch and "parent_folder_id" in arch:
            registry.set_folder_data(r, registry_id, arch.get("parent_folder_id"), arch.get("theorycrafted"),
                                     folder_name=arch.get("folder") if arch.get("folder") != "Imported" else None)
        _stamp(r, registry_id, list(fetched), "last_updated_at", ts)

    out = report.to_dict()
    out.update({
        "success": True, "operation": "update", "direction": DIRECTION["update"],
        "provider": provider, "providers": list(fetched), "dry_run": dry_run, "timestamp": ts,
    })
    return out


def push_deck(r, registry_id: str, provider: Optional[str], dry_run: bool = False) -> dict:
    """PUSH: send the commander.lab deck's current cardlist TO one linked
    external deck. Modifies the external deck only. Runs only when a user asks.

    The remote deck is read first so the result carries an accurate diff
    (remote before -> commander.lab). dry_run returns that diff without
    writing, and works without push credentials.

    Raises DeckNotFound, DeckSyncError (no provider / not linked),
    PushUnsupported (provider can't push / credential unset, code
    push_unsupported | push_not_configured), ProviderFailure.
    """
    deck = _find(r, registry_id)
    registry_id = registry.registry_id_of(deck)
    if not provider:
        raise DeckSyncError("push needs a provider: archidekt or moxfield")
    links = _syncable_links(deck)
    _check_provider(deck, provider, links)
    if not provider_supports_push(provider):
        raise PushUnsupported(f"{provider} does not support push")

    identifier = _link_id(links[provider])
    try:
        remote = _fetch_one(provider, identifier)
    except Exception as exc:  # noqa: BLE001
        raise ProviderFailure(f"{provider}: could not read the linked deck before pushing: {exc}") from exc

    local_cards = deck.get("cards") or []
    changes = diff_cardlists(remote.get("cards"), local_cards)
    ts = _now()
    remote_info = {}
    if not dry_run:
        if not provider_push_configured(provider):
            raise PushUnsupported(f"{provider} push is not configured on this server", code="push_not_configured")
        try:
            remote_info = provider_push_deck(provider, identifier, local_cards, deck.get("commanders") or [])
        except PushNotConfigured as exc:
            raise PushUnsupported(str(exc), code="push_not_configured") from exc
        except (PushError, ProviderError) as exc:
            raise ProviderFailure(f"{provider}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - network/HTTP errors
            raise ProviderFailure(f"{provider}: push failed: {exc}") from exc
        _stamp(r, registry_id, [provider], "last_pushed_at", ts)

    return {
        "success": True, "operation": "push", "direction": DIRECTION["push"],
        "registry_id": registry_id, "provider": provider, "providers": [provider],
        "dry_run": dry_run, "timestamp": ts, "changes": changes,
        "remote_url": remote_info.get("url") or remote.get("url"), "warnings": [],
    }


# Which directions each provider supports. Update (read) works for both
# unauthenticated on public decks; Push needs a per-provider credential.
PUSH_PROVIDERS = {"archidekt", "moxfield"}


def provider_supports_push(provider: str) -> bool:
    return provider in PUSH_PROVIDERS


def sync_status(r, registry_id: str) -> dict:
    """Per-provider capabilities + timestamps for the deck's Update/Push UI.
    Read-only: never contacts a provider."""
    deck = _find(r, registry_id)
    links = deck_links.get_links(deck)
    state = deck.get("sync_state") or {}
    rows = []
    for provider in sorted(deck_links.SYNCABLE_PROVIDERS):
        linked = provider in links
        configured = provider_push_configured(provider)
        reason = None
        if not provider_supports_push(provider):
            reason = f"{provider} does not support push"
        elif not configured:
            reason = f"{provider} push is not configured on this server"
        rows.append({
            "provider": provider, "linked": linked,
            "identifier": (links.get(provider) or {}).get("identifier"),
            "can_update": linked,
            "can_push": linked and reason is None,
            "push_unavailable_reason": reason,
            "last_updated_at": (state.get(provider) or {}).get("last_updated_at"),
            "last_pushed_at": (state.get(provider) or {}).get("last_pushed_at"),
        })
    return {
        "success": True, "registry_id": registry.registry_id_of(deck), "providers": rows,
        "last_updated_at": deck.get("last_updated_at"), "last_pushed_at": deck.get("last_pushed_at"),
    }


def sync_deck(r, registry_id: str) -> SyncReport:
    """DEPRECATED alias kept for POST /api/decks/{id}/sync: an Update from
    every linked provider. Returns the legacy SyncReport. Prefer update_deck."""
    result = update_deck(r, registry_id)
    report = SyncReport(registry_id=result["registry_id"], changes=result["changes"],
                        merged_card_count=result["merged_card_count"], warnings=result["warnings"])
    report.sources = [SourceResult(**s) for s in result["sources"]]
    report.conflicts = [CardConflict(**c) for c in result["conflicts"]]
    return report
