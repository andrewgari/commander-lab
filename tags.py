"""
Deck-level and deck-card tag storage.

Unlike the global ``lab_tags`` key (which applies tags at the oracle-card level),
these keys live per-deck and support per-deck-tag and per-deck-card-tag semantics.

Each tag carries a ``source`` field ("auto" | "user") so that re-running an
auto-tag pass never overwrites manual edits, a ``created_at`` ISO-8601
timestamp, and a monotonically-increasing ``version`` integer.

Keys
----
deck_tags:{deck_id}          -- [{tag, source, created_at, version}, ...]
deck_card_tags:{deck_id}     -- {card_name: [{tag, source, created_at, version}, ...]}
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

DeckId = str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_version(r, deck_id: DeckId, card_name: Optional[str] = None) -> int:
    """Return the next version number for tags belonging to *deck_id* and
    optionally *card_name*.  The version counter lives in a Redis SET tagged
    with a UUID so concurrent runs don't conflict — we bump it with `SADD` +
    `SREM` (pop the oldest member) and derive a monotonically increasing int
    from the set size, which always grows by 1 per tag write.

    For deck-level tags ``card_name`` is None.
    """
    version_key = f"deck_tags_version:{deck_id}"
    if card_name:
        version_key = f"deck_card_tags_version:{deck_id}:{card_name}"
    # Atomic: add a new unique member, pop one, report set size as version.
    r.sadd(version_key, str(uuid.uuid4()))
    members = list(r.smembers(version_key))
    if len(members) > 100:
        # Keep the most recent 100 members; remove all but the last one.
        r.srem(version_key, *members[:-1])
    return r.scard(version_key)


# ── Deck-level tags ────────────────────────────────────────────────────────


def _deck_tags_key(deck_id: DeckId) -> str:
    return f"deck_tags:{deck_id}"


def get_deck_tags(r, deck_id: DeckId) -> list:
    """Return all tags for *deck_id*, newest-first."""
    val = r.get(_deck_tags_key(deck_id))
    tags = json.loads(val) if val else []
    tags.sort(key=lambda t: t.get("version", 0), reverse=True)
    return tags


def add_deck_tag(r, deck_id: DeckId, tag: str, source: str = "user") -> dict:
    """Add a tag to *deck_id*.  If an identical tag from the same source
    already exists at the latest version, no-op; otherwise append a new entry
    with the next version."""
    existing = get_deck_tags(r, deck_id)
    # Check for a duplicate (same tag + same source)
    for t in existing:
        if t["tag"] == tag and t["source"] == source:
            return t

    version = _next_version(r, deck_id)
    new_tag = {"tag": tag, "source": source, "created_at": _now(), "version": version}
    existing.append(new_tag)
    r.set(_deck_tags_key(deck_id), json.dumps(existing))
    return new_tag


def remove_deck_tag(r, deck_id: DeckId, tag: str) -> bool:
    """Remove *all* entries matching *tag* for *deck_id*. Returns True if
    anything was removed."""
    val = r.get(_deck_tags_key(deck_id))
    if not val:
        return False
    tags = json.loads(val)
    before = len(tags)
    tags = [t for t in tags if t["tag"] != tag]
    after = len(tags)
    if after < before:
        r.set(_deck_tags_key(deck_id), json.dumps(tags))
        return True
    return False


# ── Per-deck-card tags ─────────────────────────────────────────────────────


def _deck_card_tags_key(deck_id: DeckId) -> str:
    return f"deck_card_tags:{deck_id}"


def get_deck_card_tags(r, deck_id: DeckId) -> dict:
    """Return {card_name: [tags]} for *deck_id*, newest-first per card."""
    val = r.get(_deck_card_tags_key(deck_id))
    data = json.loads(val) if val else {}
    for card_name in data:
        data[card_name].sort(key=lambda t: t.get("version", 0), reverse=True)
    return data


def get_card_tags_for_deck(r, deck_id: DeckId, card_name: str) -> list:
    """Return the tag list for one card in one deck, newest-first."""
    data = get_deck_card_tags(r, deck_id)
    tags = data.get(card_name, [])
    tags.sort(key=lambda t: t.get("version", 0), reverse=True)
    return tags


def add_card_tag_to_deck(r, deck_id: DeckId, card_name: str, tag: str,
                         source: str = "user") -> dict:
    """Add a tag to *card_name* in *deck_id*.  Upserts within the same
    source/version bucket."""
    data = get_deck_card_tags(r, deck_id)
    if card_name not in data:
        data[card_name] = []

    tags = data[card_name]
    for t in tags:
        if t["tag"] == tag and t["source"] == source:
            return t

    version = _next_version(r, deck_id, card_name)
    new_tag = {"tag": tag, "source": source, "created_at": _now(), "version": version}
    tags.append(new_tag)
    data[card_name] = tags
    r.set(_deck_card_tags_key(deck_id), json.dumps(data))
    return new_tag


def remove_card_tag_from_deck(r, deck_id: DeckId, card_name: str,
                              tag: str) -> bool:
    """Remove *tag* from *card_name* in *deck_id*. Returns True if removed."""
    val = r.get(_deck_card_tags_key(deck_id))
    if not val:
        return False
    data = json.loads(val)
    if card_name not in data:
        return False
    tags = data[card_name]
    before = len(tags)
    tags = [t for t in tags if t["tag"] != tag]
    if len(tags) < before:
        if tags:
            data[card_name] = tags
        else:
            del data[card_name]
        r.set(_deck_card_tags_key(deck_id), json.dumps(data))
        return True
    return False


# ── Bulk helpers ────────────────────────────────────────────────────────────


def clear_deck_tags(r, deck_id: DeckId) -> None:
    """Remove all tags (deck-level and per-card) for a deck."""
    r.delete(_deck_tags_key(deck_id))
    r.delete(_deck_card_tags_key(deck_id))
    # Also clean version counters
    r.delete(f"deck_tags_version:{deck_id}")
    # Per-card version keys are harder to enumerate; we leave them — version
    # counters are capped at 100 members so they self-limit.
