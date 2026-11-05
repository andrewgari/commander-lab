"""
Card ORM — canonical, unique card records keyed by Scryfall oracle_id.

Mirrors instances.py/registry.py in shape: all reads/writes to `card:*` and
its secondary index should go through this module.

This is an ADDITIVE keyspace layered on top of the existing name-keyed
`card_meta:{name}` records (see docs/CARD_DATABASE.md, "Why not key oracle
data by name long-term" and docs/DECK_MANAGEMENT_VIEW.md ADDENDUM). Nothing
here reads or writes `card_meta:*` — sync.py keeps both in sync going
forward, and scripts/migrate_card_meta_to_oracle_id.py backfills this
keyspace from existing `card_meta:*` data. `card_meta:{name}` keys are left
untouched by this module.

Keys:
    card:{oracle_id}                    -- canonical unique card record
    idx:card_name_to_oracle:{name}      -- name -> oracle_id, so name-keyed
                                            call sites can resolve through a
                                            thin helper instead of learning
                                            a new lookup.

Note on the `card:*` prefix: this deliberately reuses the same Redis key
prefix the legacy (pre-instances.py) `card:{name}` inventory blobs used.
`oracle_id` values are Scryfall UUIDs, which never collide with a card name,
so the two are safely distinguishable by key suffix shape. Callers that
still do a raw `r.keys("card:*")` scan (e.g. sync.py's full-resync cleanup)
must exclude UUID-shaped suffixes to avoid deleting these ORM records — see
the guard in sync.py.
"""
import json
import re
from typing import Optional

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def is_oracle_id(value: str) -> bool:
    """True if `value` looks like a Scryfall oracle_id (UUID), as opposed to
    a legacy card name. Used by callers that need to distinguish the two
    within a shared `card:*` keyspace scan."""
    return bool(value) and bool(_UUID_RE.match(value))


def _card_key(oracle_id: str) -> str:
    return f"card:{oracle_id}"


def _idx_name_key(name: str) -> str:
    return f"idx:card_name_to_oracle:{name}"


def get_card(r, oracle_id: str) -> Optional[dict]:
    """Fetch the canonical card record by oracle_id, or None if unknown."""
    if not oracle_id:
        return None
    val = r.get(_card_key(oracle_id))
    return json.loads(val) if val else None


def get_card_by_name(r, name: str) -> Optional[dict]:
    """Resolve a card name to its canonical record via the secondary index.
    Returns None if the name has no known oracle_id yet (e.g. not migrated /
    not seen by sync.py since this module was introduced) — callers should
    fall back to `card_meta:{name}` in that case, same as today."""
    if not name:
        return None
    oracle_id = r.get(_idx_name_key(name))
    if not oracle_id:
        return None
    return get_card(r, oracle_id)


def upsert_card(r, record: dict) -> dict:
    """Insert or update the canonical card record. `record` must include
    `oracle_id` and `name`. Keeps the name->oracle_id index in sync.

    Returns the stored record unchanged (this module doesn't merge with any
    prior version — callers pass the full record they want stored, mirroring
    upsert_deck's "caller assembles the full payload" contract)."""
    oracle_id = record.get("oracle_id")
    name = record.get("name")
    if not oracle_id:
        raise ValueError("upsert_card requires record['oracle_id']")
    if not name:
        raise ValueError("upsert_card requires record['name']")

    pipe = r.pipeline()
    pipe.set(_card_key(oracle_id), json.dumps(record))
    pipe.set(_idx_name_key(name), oracle_id)
    pipe.execute()

    return record
