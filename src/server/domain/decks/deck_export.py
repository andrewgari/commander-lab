"""Export a generated/reconstructed decklist as a NEW deck on Moxfield and/or Archidekt.

Explicit user action only (never called from sync, timers or page loads).
See docs/DECK_EXPORT.md. Per-provider failures are isolated: one provider
failing never blocks or hides the result of the other.
"""
from typing import Any, Iterable, Optional

import src.server.infra.providers as providers
from src.server.infra.providers import PushError, PushNotConfigured, ProviderError

EXPORT_PROVIDERS = ("moxfield", "archidekt")
MAX_CARD_QTY = 99


class ExportError(ValueError):
    """The decklist/request itself is invalid (nothing was sent anywhere)."""

    code = "invalid_export"
    status = 400


def normalize_decklist(name: str, cards: Iterable[dict], commanders: Optional[list] = None) -> dict:
    """Validate and normalize a decklist into {name, cards, commanders}.

    Merges duplicate names, drops zero-quantity rows, and guarantees every
    commander appears exactly once in `cards`. Raises ExportError on a blank
    name, no cards, an unparsable quantity or an oversized quantity.
    """
    name = (name or "").strip()
    if not name:
        raise ExportError("deck name is required")
    merged: dict = {}
    for row in cards or []:
        card_name = ((row or {}).get("name") or "").strip()
        if not card_name:
            continue
        try:
            qty = int(row.get("quantity", 1))
        except (TypeError, ValueError):
            raise ExportError(f"invalid quantity for {card_name!r}: {row.get('quantity')!r}")
        if qty < 0 or qty > MAX_CARD_QTY:
            raise ExportError(f"quantity for {card_name!r} out of range: {qty}")
        if qty:
            merged[card_name] = merged.get(card_name, 0) + qty
    cmdrs = []
    for c in commanders or []:
        c = (c or "").strip()
        if c and c not in cmdrs:
            cmdrs.append(c)
            merged[c] = 1  # commanders are always singletons
    if not merged:
        raise ExportError("decklist has no cards")
    return {
        "name": name,
        "commanders": cmdrs,
        "cards": [{"name": n, "quantity": q} for n, q in merged.items()],
    }


def _resolve_providers(requested: Optional[Iterable[str]]) -> list:
    if requested is None:
        return list(EXPORT_PROVIDERS)
    out = []
    for p in requested:
        p = (p or "").strip().lower()
        if p not in EXPORT_PROVIDERS:
            raise ExportError(f"unsupported export provider: {p!r} (expected one of {', '.join(EXPORT_PROVIDERS)})")
        if p not in out:
            out.append(p)
    if not out:
        raise ExportError("no export providers requested")
    return out


def export_decklist(name: str, cards: Iterable[dict], commanders: Optional[list] = None,
                    providers_requested: Optional[Iterable[str]] = None,
                    description: str = "", private: bool = True, dry_run: bool = False) -> dict:
    """Create the deck on each requested provider.

    Returns {"success": bool (all ok), "dry_run", "deck": normalized, "results":
    {provider: {"status": "created"|"dry_run"|"not_configured"|"failed",
    "url"?, "code"?, "error"?}}}. Raises ExportError for invalid input only.
    dry_run validates and reports credential readiness without any network call.
    """
    deck = normalize_decklist(name, cards, commanders)
    targets = _resolve_providers(providers_requested)
    results = {}
    for provider in targets:
        configured = providers.push_configured(provider)
        if dry_run:
            results[provider] = {"status": "dry_run", "configured": configured,
                                 "card_count": sum(c["quantity"] for c in deck["cards"])}
            continue
        if not configured:
            results[provider] = {"status": "not_configured", "code": "push_not_configured",
                                 "error": f"{provider} export is not configured on this server"}
            continue
        try:
            info = providers.create_deck(provider, deck["name"], deck["cards"], deck["commanders"],
                                         description, private)
            results[provider] = {"status": "created", "url": info.get("url"), "source_id": info.get("source_id")}
        except PushNotConfigured as exc:
            results[provider] = {"status": "not_configured", "code": "push_not_configured", "error": str(exc)}
        except (PushError, ProviderError) as exc:
            results[provider] = {"status": "failed", "code": "provider_error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - network/HTTP errors must not abort other providers
            results[provider] = {"status": "failed", "code": "provider_error", "error": f"{provider}: export failed: {exc}"}
    ok = all(r["status"] in ("created", "dry_run") for r in results.values())
    return {"success": ok, "dry_run": dry_run, "deck": deck, "results": results}


def export_draft(r: Any, session_id: str, draft_index: int, providers_requested=None,
                 description: str = "", private: bool = True, dry_run: bool = False) -> dict:
    """Export one Reconstruct draft by index. Raises ExportError (bad session/index)."""
    import src.server.domain.draft.reconstruct as reconstruct
    try:
        drafts = reconstruct.get_drafts(r, session_id)
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    if not isinstance(draft_index, int) or isinstance(draft_index, bool) or not 0 <= draft_index < len(drafts):
        raise ExportError(f"draft index out of range: {draft_index!r} ({len(drafts)} drafts)")
    d = drafts[draft_index]
    return export_decklist(d.get("name"), d.get("cards") or [], d.get("commanders") or [],
                           providers_requested, description or d.get("explanation") or "", private, dry_run)
