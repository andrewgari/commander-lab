"""Provenance (learned vs manual) side-channel for the global Lab Tags.

Lab Tags (Redis key ``lab_tags`` -> ``{card_name: [tag, ...]}``) are oracle-level,
flat lists of tag strings used widely (inventory_view.py, deck_manage.html,
sync.py) as plain categories. Changing that storage shape would ripple through
every one of those call sites, so provenance is tracked in a separate key
instead: ``lab_tags_source`` -> ``{card_name: {tag: "learned" | "manual"}}``.

Rules:
- A tag written by ``sync.py`` from Archidekt confidence data is "learned".
- A tag added by a human through the Tag Management page or the deck page's
  Lab Tags editor is "manual".
- A tag with no entry in ``lab_tags_source`` (i.e. it predates this feature)
  defaults to "learned", since every pre-existing Lab tag originated from the
  sync-time high-confidence seeding described in docs/LAB_TAGS_IMPLEMENTATION.md.
"""
import json

SOURCE_KEY = "lab_tags_source"
LEARNED = "learned"
MANUAL = "manual"


def _load(r) -> dict:
    raw = r.get(SOURCE_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_source_map(r) -> dict:
    """Return the full {card_name: {tag: source}} map."""
    return _load(r)


def source_of(r, card_name: str, tag: str) -> str:
    """Return the source of *tag* on *card_name*, defaulting to "learned"."""
    data = _load(r)
    return data.get(card_name, {}).get(tag, LEARNED)


def with_source(r, card_name: str, tags: list) -> list:
    """Return [{"tag": t, "source": s}, ...] for a flat list of tag strings."""
    data = _load(r)
    card_sources = data.get(card_name, {})
    return [{"tag": t, "source": card_sources.get(t, LEARNED)} for t in tags]


def set_source(r, card_name: str, tag: str, source: str) -> None:
    """Record that *tag* on *card_name* came from *source* ("learned"|"manual")."""
    if source not in (LEARNED, MANUAL):
        source = MANUAL
    data = _load(r)
    data.setdefault(card_name, {})[tag] = source
    r.set(SOURCE_KEY, json.dumps(data))


def clear_source(r, card_name: str, tag: str) -> None:
    """Drop the provenance entry for *tag* on *card_name* (e.g. on removal)."""
    data = _load(r)
    card_sources = data.get(card_name)
    if not card_sources or tag not in card_sources:
        return
    del card_sources[tag]
    if card_sources:
        data[card_name] = card_sources
    else:
        del data[card_name]
    r.set(SOURCE_KEY, json.dumps(data))
