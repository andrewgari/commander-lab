"""Build deterministic, source-backed card facts for deck reviews.

Fetchers are injected so this module never performs network I/O itself. The
Scryfall fetcher is required to use the application's existing Scryfall path.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


_UNAVAILABLE_SCRYFALL = "Scryfall data unavailable"


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _unavailable(reason: str) -> dict[str, str]:
    return {"status": "unavailable", "reason": reason}


def _names(deck: Any) -> list[str]:
    names: list[str] = []
    for group in (_value(deck, "commanders", []) or [], _value(deck, "cards", []) or []):
        for entry in group:
            name = _value(entry, "name")
            if isinstance(name, str) and name.strip() and name not in names:
                names.append(name)
    return names


def _edhrec_cards(raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read only documented EDHREC commander-page fields from the fixture/provider shape."""
    if not isinstance(raw, Mapping):
        return {}, {}
    container = raw.get("container", {})
    json_dict = container.get("json_dict", {}) if isinstance(container, Mapping) else {}
    if not isinstance(json_dict, Mapping):
        return {}, {}
    commander = json_dict.get("card", {})
    commander = commander if isinstance(commander, Mapping) else {}
    by_name: dict[str, Any] = {}
    for listing in json_dict.get("cardlists", []) or []:
        if not isinstance(listing, Mapping):
            continue
        for card in listing.get("cardviews", []) or []:
            if isinstance(card, Mapping) and isinstance(card.get("name"), str):
                by_name.setdefault(card["name"], card)
    return dict(commander), by_name


def _salt_by_name(raw: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(raw, Mapping):
        cards = raw.get("cards", {})
        if isinstance(cards, Mapping):
            return raw.get("salt_score"), dict(cards)
        return raw.get("salt_score"), {}
    return _value(raw, "salt_score"), {}


def build_fact_pack(deck: Any, fetchers: Mapping[str, Callable[..., Any]]) -> dict[str, Any]:
    """Assemble card facts via injected provider callables, without I/O.

    Fetcher convention: ``scryfall(name)`` and ``tags(name)`` are per-card;
    ``edhrec(deck)`` and ``salt(deck)`` provide deck-level provider payloads.
    Missing providers, exceptions, malformed results, and absent fields remain
    explicit unavailable markers rather than silently becoming ``None``.
    """
    names = _names(deck)
    edhrec_raw = salt_raw = None
    errors: dict[str, str] = {}
    for key in ("edhrec", "salt"):
        fetcher = fetchers.get(key)
        if not callable(fetcher):
            errors[key] = f"{key} fetcher not provided"
            continue
        try:
            value = fetcher(deck)
            if key == "edhrec":
                edhrec_raw = value
            else:
                salt_raw = value
        except Exception as exc:  # provider boundaries are intentionally isolated
            errors[key] = f"{key} fetch failed: {exc}"
    commander_meta, edhrec_cards = _edhrec_cards(edhrec_raw)
    deck_salt, salt_cards = _salt_by_name(salt_raw)

    cards: list[dict[str, Any]] = []
    for name in names:
        scryfall = fetchers.get("scryfall")
        try:
            raw = scryfall(name) if callable(scryfall) else None
        except Exception as exc:
            raw = None
            scryfall_reason = f"Scryfall lookup failed: {exc}"
        else:
            scryfall_reason = _UNAVAILABLE_SCRYFALL if callable(scryfall) else "Scryfall fetcher not provided"
        if not isinstance(raw, Mapping):
            oracle: Any = _unavailable(scryfall_reason)
            price: Any = _unavailable(scryfall_reason)
            legalities: Any = _unavailable(scryfall_reason)
        else:
            oracle_fields = {field: raw[field] for field in ("name", "cmc", "type_line", "colors", "color_identity") if field in raw}
            missing = [field for field in ("name", "cmc", "type_line", "colors", "color_identity") if field not in raw]
            oracle = oracle_fields if not missing else _unavailable("Scryfall fields missing: " + ", ".join(missing))
            legality_value = raw.get("legalities")
            legalities = legality_value if legality_value is not None else _unavailable("Scryfall legalities unavailable")
            prices = raw.get("prices")
            price_value = prices.get("usd") if isinstance(prices, Mapping) else None
            price = price_value if price_value not in (None, "") else _unavailable("Scryfall USD price unavailable")
        try:
            tag_fetcher = fetchers.get("tags")
            tags = tag_fetcher(name) if callable(tag_fetcher) else _unavailable("tags fetcher not provided")
        except Exception as exc:
            tags = _unavailable(f"tags fetch failed: {exc}")
        edhrec = edhrec_cards.get(name)
        if edhrec is None and name == commander_meta.get("name"):
            edhrec = {key: commander_meta[key] for key in ("num_decks", "rank", "salt") if key in commander_meta}
        if edhrec is None:
            edhrec = _unavailable(errors.get("edhrec", "EDHREC card aggregate unavailable"))
        salt = salt_cards.get(name, deck_salt if name == commander_meta.get("name") else None)
        if salt is None:
            salt = _unavailable(errors.get("salt", "Salt score unavailable"))
        cards.append({"name": name, "oracle": oracle, "cmc": oracle.get("cmc") if isinstance(oracle, Mapping) and "status" not in oracle else _unavailable("Scryfall CMC unavailable"),
                      "type_line": oracle.get("type_line") if isinstance(oracle, Mapping) and "status" not in oracle else _unavailable("Scryfall type line unavailable"),
                      "colors": oracle.get("colors") if isinstance(oracle, Mapping) and "status" not in oracle else _unavailable("Scryfall colors unavailable"),
                      "color_identity": oracle.get("color_identity") if isinstance(oracle, Mapping) and "status" not in oracle else _unavailable("Scryfall color identity unavailable"),
                      "legalities": legalities, "price": price, "salt": salt, "edhrec": edhrec, "tags": tags})
    return {"cards": cards}
