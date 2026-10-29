"""Build deterministic, source-backed card facts for deck reviews.

Fetchers are injected so this module never performs network I/O itself. The
Scryfall fetcher is required to use the application's existing Scryfall path.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from src.server.domain.analytics import fact_sections as fs

SCHEMA = "fact_pack/2"


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
            # Entries are either {"name": ...} mappings or plain name strings
            # (e.g. deck["commanders"] in recorded deck fixtures).
            name = entry if isinstance(entry, str) else _value(entry, "name")
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


def _strip_times(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_times(v) for k, v in obj.items() if k not in ("fetched_at", "generated_at")}
    if isinstance(obj, list):
        return [_strip_times(v) for v in obj]
    return obj


class FactPack(dict):
    """JSON-serializable fact pack. ``.hash`` is the sha256 of the canonical JSON
    (sorted keys) with every ``fetched_at``/``generated_at`` removed, so re-running against the same
    source data gives the same hash; it is what ``meta.fact_pack_hash`` carries."""

    @property
    def hash(self) -> str:
        blob = json.dumps(_strip_times(dict(self)), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_fact_pack(deck: Any, fetchers: Mapping[str, Callable[..., Any]] | None = None, *,
                    refresh: bool = False, now: datetime | None = None) -> FactPack:
    """Assemble card facts via provider callables.

    Fetcher convention: ``scryfall(name)`` and ``tags(name)`` are per-card;
    ``edhrec(deck)``, ``salt(deck)`` and ``spellbook(deck)`` provide deck-level
    provider payloads. With ``fetchers=None`` the cached live fetchers from
    ``fact_sources`` are used (``refresh=True`` bypasses their caches); tests inject
    recorded fixtures instead, so CI never touches the network.
    Missing providers, exceptions, malformed results, and absent fields remain
    explicit unavailable markers rather than silently becoming ``None``.
    """
    if fetchers is None:
        from src.server.domain.analytics.fact_sources import default_fetchers

        fetchers = default_fetchers(refresh=refresh)
    fetched_at = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    names = _names(deck)
    edhrec_raw = salt_raw = spellbook_raw = None
    errors: dict[str, str] = {}
    for key in ("edhrec", "salt", "spellbook"):
        fetcher = fetchers.get(key)
        if not callable(fetcher):
            errors[key] = f"{key} fetcher not provided"
            continue
        try:
            value = fetcher(deck)
            if key == "edhrec":
                edhrec_raw = value
            elif key == "salt":
                salt_raw = value
            else:
                spellbook_raw = value
        except Exception as exc:  # provider boundaries are intentionally isolated
            errors[key] = f"{key} fetch failed: {exc}"
    commander_meta, edhrec_cards = _edhrec_cards(edhrec_raw)
    deck_salt, salt_cards = _salt_by_name(salt_raw)

    cards: list[dict[str, Any]] = []
    raws: dict[str, Any] = {}
    batch: Mapping[str, Any] | None = None
    batch_error: str | None = None
    if callable(fetchers.get("scryfall_batch")) and not callable(fetchers.get("scryfall")):
        try:
            batch = fetchers["scryfall_batch"](names)
        except Exception as exc:
            batch_error = f"Scryfall lookup failed: {exc}"
    for name in names:
        scryfall = fetchers.get("scryfall")
        try:
            if batch is not None:
                raw = batch.get(name.lower()) or batch.get(name.split(" // ")[0].strip().lower())
            else:
                raw = scryfall(name) if callable(scryfall) else None
        except Exception as exc:
            raw = None
            scryfall_reason = f"Scryfall lookup failed: {exc}"
        else:
            scryfall_reason = (batch_error or _UNAVAILABLE_SCRYFALL) if (callable(scryfall) or batch is not None or batch_error) \
                else "Scryfall fetcher not provided"
        if isinstance(raw, Mapping):
            raws[name] = raw
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
                      "legalities": legalities, "price": price, "salt": salt, "edhrec": edhrec, "tags": tags,
                      "oracle_text": fs._text(raw) if isinstance(raw, Mapping) else _unavailable(scryfall_reason),
                      "oracle_id": fs.oracle_id_of(raw) if isinstance(raw, Mapping) else _unavailable(scryfall_reason),
                      "game_changer": (raw["game_changer"] if isinstance(raw, Mapping) and isinstance(raw.get("game_changer"), bool)
                                       else _unavailable("Scryfall game_changer flag unavailable")),
                      "source": "scryfall", "fetched_at": fetched_at})

    commanders = {n for n in (_commander_names(deck))}
    rows = [{"name": n, "raw": raws[n], "qty": 1 if n in commanders else fs._qty(deck, n), "commander": n in commanders}
            for n in names if n in raws]
    identity = _identity(rows, commanders)
    no_data = "Scryfall data unavailable for every card"
    curve = fs.curve_section(rows, fetched_at)
    mana = fs.mana_section(rows, identity, fetched_at) if rows else fs.unavailable(no_data)
    roles = fs.roles_section(rows, fetched_at)
    if spellbook_raw is None:
        spellbook_raw = fs.unavailable(errors.get("spellbook", "spellbook fetcher not provided"))
    elif isinstance(spellbook_raw, Mapping) and spellbook_raw.get("status") == "unavailable":
        pass
    combos, included = fs.combos_section(spellbook_raw, set(names), fetched_at)
    bracket = fs.bracket_section(deck, rows, included, fetched_at)
    edhrec_cmp = fs.edhrec_section(edhrec_raw, rows, errors.get("edhrec", "edhrec fetcher not provided"), fetched_at)
    salt_sec = fs.salt_section(salt_raw, fetched_at) if salt_raw is not None else fs.unavailable(errors.get("salt", "salt fetcher not provided"))
    synergy = fs.synergy_section(rows, fetched_at)
    pack = FactPack(
        schema=SCHEMA, generated_at=fetched_at, cards=cards,
        deck={"commanders": sorted(commanders), "color_identity": identity, "card_count": sum(r["qty"] for r in rows)},
        curve=curve, mana=mana, roles=roles, bracket=bracket, combos=combos, edhrec=edhrec_cmp,
        salt=salt_sec, synergy=synergy, charts=fs.charts_section(curve, mana, roles, synergy),
    )
    return pack


def _commander_names(deck: Any) -> list[str]:
    out = []
    for entry in _value(deck, "commanders", []) or []:
        n = entry if isinstance(entry, str) else _value(entry, "name")
        if isinstance(n, str) and n.strip():
            out.append(n)
    return out


def _identity(rows: list[dict], commanders: set[str]) -> list[str]:
    src = [r for r in rows if r["name"] in commanders] or rows
    ci = {c for r in src for c in (r["raw"].get("color_identity") or [])}
    return [c for c in fs.COLORS if c in ci]
