"""Transcribe Archidekt decks to Moxfield.

Scope guard: only decks whose registry `status == "physical"` are transcribed.
Non-physical decks (digital/testing/retired) are hypothetical decklists — per
the data model in docs/PHYSICAL_RESYNC_ADJUDICATION.md they have no paper
footprint, so they have no business being pushed to a paper-decks folder.
`TRANSCRIBE_STATUSES` is the single place that decision lives.

Every transcribed deck lands:
  - visibility `private`
  - in one Moxfield folder (default "Physical", configurable)
  - with a generated description: commander(s), color identity, card count
    and a link back to the Archidekt deck

Idempotency: a Redis map `moxfield_transcribe:{registry_id}` records which
Moxfield deck a given Archidekt deck became. An already-transcribed deck is
skipped unless `force=True`, so re-running never duplicates decks on
Moxfield (which, unlike the collection import, we could not undo from here).
"""
import json
from datetime import datetime, timezone
from typing import Optional

import src.server.domain.decks.registry as registry
from src.server.infra.providers import moxfield_writer
from src.server.infra.providers.moxfield_writer import MoxfieldWriteError

# Only these registry lifecycle statuses are eligible. Deliberately narrow:
# widen it here, in one place, if paper-adjacent statuses ever qualify.
TRANSCRIBE_STATUSES = {"physical"}

DEFAULT_FOLDER_NAME = "Physical"
DEFAULT_VISIBILITY = "private"

MAP_KEY_PREFIX = "moxfield_transcribe:"

WUBRG_NAMES = {
    "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless",
}


class TranscribeError(ValueError):
    """Raised for an unusable transcribe request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _map_key(registry_id: str) -> str:
    return f"{MAP_KEY_PREFIX}{registry_id}"


def get_mapping(r, registry_id: str) -> Optional[dict]:
    raw = r.get(_map_key(registry_id))
    return json.loads(raw) if raw else None


def save_mapping(r, registry_id: str, deck: dict, folder_name: str) -> dict:
    record = {
        "registry_id": registry_id,
        "moxfield_id": deck.get("id"),
        "moxfield_public_id": deck.get("publicId"),
        "moxfield_url": deck.get("publicUrl") or (
            f"https://moxfield.com/decks/{deck.get('publicId')}" if deck.get("publicId") else ""
        ),
        "folder": folder_name,
        "visibility": deck.get("visibility", DEFAULT_VISIBILITY),
        "transcribed_at": _now(),
    }
    r.set(_map_key(registry_id), json.dumps(record))
    return record


def eligible_decks(r) -> list:
    """Physical decks only, in registry order."""
    return [
        deck for deck in registry.list_decks(r)
        if deck.get("status") in TRANSCRIBE_STATUSES
    ]


def color_identity_label(color: str) -> str:
    """"WUB" -> "White / Blue / Black"; "C"/"" -> "Colorless"."""
    color = (color or "").strip().upper()
    if not color or color == "C":
        return "Colorless"
    names = [WUBRG_NAMES[c] for c in "WUBRG" if c in color]
    return " / ".join(names) if names else "Colorless"


def build_import_text(deck: dict) -> str:
    """Archidekt decklist -> MTGO text format Moxfield's importText accepts.

    Commanders go under a `// Commander` header so Moxfield puts them on the
    commander board instead of the mainboard; everything else follows under
    `// Mainboard`. Deck `cards` is already mainboard-only (providers strip
    Sideboard/Maybeboard on fetch).
    """
    commanders = set(deck.get("commanders") or [])
    commander_lines, mainboard_lines = [], []

    for card in deck.get("cards", []):
        name = (card.get("name") or "").strip()
        if not name:
            continue
        quantity = card.get("quantity", 1)
        if quantity <= 0:
            continue
        line = f"{quantity} {name}"
        if name in commanders:
            commander_lines.append(line)
        else:
            mainboard_lines.append(line)

    # A commander listed in `commanders` but absent from `cards` still belongs
    # on the commander board — don't silently drop it.
    listed = {line.split(" ", 1)[1] for line in commander_lines}
    for name in sorted(commanders - listed):
        if name:
            commander_lines.append(f"1 {name}")

    sections = []
    if commander_lines:
        sections.append("// Commander")
        sections.extend(commander_lines)
        sections.append("")
    sections.append("// Mainboard")
    sections.extend(mainboard_lines)

    return "\n".join(sections)


def card_count(deck: dict) -> int:
    return sum(max(c.get("quantity", 1), 0) for c in deck.get("cards", []) if c.get("name"))


def build_description(deck: dict) -> str:
    """Basic description: commander(s), color identity, card count, and a link
    back to the Archidekt source deck."""
    lines = []

    commanders = [c for c in (deck.get("commanders") or []) if c]
    if commanders:
        label = "Commander" if len(commanders) == 1 else "Commanders"
        lines.append(f"{label}: {' + '.join(commanders)}")

    lines.append(f"Color identity: {color_identity_label(deck.get('color', ''))}")
    lines.append(f"Cards: {card_count(deck)}")

    url = deck.get("url") or ""
    if url:
        lines.append("")
        lines.append(f"Transcribed from Archidekt: {url}")

    return "\n".join(lines)


def transcribe_deck(
    r,
    client,
    deck: dict,
    folder_id: Optional[str],
    folder_name: str = DEFAULT_FOLDER_NAME,
    visibility: str = DEFAULT_VISIBILITY,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Transcribe one deck. Returns a per-deck result dict with `action` one of
    skipped_not_physical / skipped_already / created / would_create / failed.
    """
    registry_id = registry.registry_id_of(deck)
    name = deck.get("name") or f"Deck {registry_id}"

    if deck.get("status") not in TRANSCRIBE_STATUSES:
        return {
            "registry_id": registry_id, "name": name,
            "action": "skipped_not_physical", "status": deck.get("status"),
        }

    existing = get_mapping(r, registry_id)
    if existing and not force:
        return {
            "registry_id": registry_id, "name": name,
            "action": "skipped_already", "moxfield_url": existing.get("moxfield_url"),
        }

    import_text = build_import_text(deck)
    description = build_description(deck)

    if not import_text.replace("// Commander", "").replace("// Mainboard", "").strip():
        return {
            "registry_id": registry_id, "name": name,
            "action": "failed", "error": "deck has no cards to transcribe",
        }

    if dry_run:
        return {
            "registry_id": registry_id, "name": name, "action": "would_create",
            "visibility": visibility, "folder": folder_name,
            "cards": card_count(deck), "description": description,
        }

    try:
        created = client.create_deck(
            name=name,
            import_text=import_text,
            visibility=visibility,
            folder_id=folder_id,
        )
        moxfield_writer.throttle()

        # Description is a separate PUT: /v3/decks create has no description
        # field (confirmed in Moxfield's own CREATE_DECK payload). A failure
        # here must not lose the deck we just made, so it's reported on an
        # otherwise-successful result instead of raising.
        description_error = None
        try:
            client.update_deck_description(created, description)
            moxfield_writer.throttle()
        except MoxfieldWriteError as exc:
            description_error = str(exc)

        record = save_mapping(r, registry_id, created, folder_name)
        result = {
            "registry_id": registry_id, "name": name, "action": "created",
            "moxfield_url": record["moxfield_url"], "visibility": visibility,
            "folder": folder_name, "cards": card_count(deck),
        }
        if description_error:
            result["description_error"] = description_error
        return result
    except MoxfieldWriteError as exc:
        return {"registry_id": registry_id, "name": name, "action": "failed", "error": str(exc)}


def transcribe_all(
    r,
    folder_name: str = DEFAULT_FOLDER_NAME,
    visibility: str = DEFAULT_VISIBILITY,
    force: bool = False,
    dry_run: bool = False,
    client=None,
) -> dict:
    """Transcribe every physical deck to Moxfield.

    A per-deck failure is collected and the run continues, mirroring
    linked_accounts.sync_account — one bad deck must not abort the batch.
    `client` is injectable for tests; otherwise a MoxfieldClient is built from
    the saved Playwright session (never from a password).
    """
    decks = eligible_decks(r)

    report = {
        "folder": folder_name,
        "visibility": visibility,
        "dry_run": dry_run,
        "eligible": len(decks),
        "created": 0,
        "skipped_already": 0,
        "failed": 0,
        "results": [],
    }

    if not decks:
        return report

    folder_id = None
    if not dry_run:
        if client is None:
            client = moxfield_writer.MoxfieldClient()
        folder = client.ensure_folder(folder_name)
        folder_id = folder.get("id")
        report["folder_id"] = folder_id

    for deck in decks:
        result = transcribe_deck(
            r, client, deck,
            folder_id=folder_id,
            folder_name=folder_name,
            visibility=visibility,
            force=force,
            dry_run=dry_run,
        )
        report["results"].append(result)

        action = result.get("action")
        if action in ("created", "would_create"):
            report["created"] += 1
        elif action == "skipped_already":
            report["skipped_already"] += 1
        elif action == "failed":
            report["failed"] += 1

    return report
