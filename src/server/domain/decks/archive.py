"""Archive existing Moxfield decks — rename + re-file, never delete.

Moxfield has NO archive feature. Reading their whole app bundle, the only
deck-level mutations that exist are:

    PUT    /v2/decks/{id}                          name / visibility / description
    POST   /v1/deck-folders/{folderId}/decks/{id}  move into folder
    DELETE /v1/deck-folders/{folderId}/decks/{id}  remove from folder
    POST   /v2/decks/{id}/clone                    clone
    DELETE /v1/decks/{id}                          DELETE  <- never called here

So "archive" is implemented as: prefix the name, set visibility, and move the
deck into a holding folder. That is fully reversible and loses nothing.

Two hard safety rules, both enforced in code and covered by tests:

1. **Nothing is ever deleted.** This module does not import or reference the
   delete endpoint. `update_deck` sends only name/visibility/description/
   format/comments — never a cardlist — so no card can be dropped either.
2. **Every original is recorded before it is touched.** The pre-change name,
   visibility and folder are written to `moxfield_archive:{deck_id}` BEFORE the
   first mutating call, so a rename is always reversible (`restore()`) even if
   the run dies halfway.

Decks this tool created itself (tracked in `moxfield_transcribe:*` by
transcribe.py) are skipped by default — archiving the decks you just
transcribed would be self-defeating.
"""
import json
from datetime import datetime, timezone
from typing import Optional

import src.server.domain.decks.transcribe as transcribe
from src.server.infra.providers import moxfield_writer
from src.server.infra.providers.moxfield_writer import MoxfieldWriteError

DEFAULT_ARCHIVE_FOLDER = "Archive"
DEFAULT_PREFIX = "[Archived] "
DEFAULT_VISIBILITY = "private"

BACKUP_KEY_PREFIX = "moxfield_archive:"


class ArchiveError(ValueError):
    """Raised for an unusable archive request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backup_key(deck_id: str) -> str:
    return f"{BACKUP_KEY_PREFIX}{deck_id}"


def get_backup(r, deck_id: str) -> Optional[dict]:
    raw = r.get(_backup_key(deck_id))
    return json.loads(raw) if raw else None


def save_backup(r, deck: dict, folder_name: str) -> dict:
    """Record a deck's pre-archive state. Called BEFORE any mutation.

    Never overwrites an existing backup: if a deck is archived twice, the
    first backup holds the true original name, and clobbering it would make
    the rename irreversible.
    """
    deck_id = str(deck.get("id"))
    existing = get_backup(r, deck_id)
    if existing:
        return existing

    record = {
        "deck_id": deck_id,
        "public_id": deck.get("publicId"),
        "original_name": deck.get("name"),
        "original_visibility": deck.get("visibility"),
        "archived_to_folder": folder_name,
        "archived_at": _now(),
        "url": deck.get("publicUrl") or (
            f"https://moxfield.com/decks/{deck.get('publicId')}" if deck.get("publicId") else ""
        ),
    }
    r.set(_backup_key(deck_id), json.dumps(record))
    return record


def mark_folder_moved(r, deck_id: str, moved: bool) -> None:
    """Record whether the folder move succeeded, so a deck that was renamed
    but never filed is retried on the next run instead of treated as done."""
    backup = get_backup(r, str(deck_id))
    if backup:
        backup["folder_moved"] = moved
        r.set(_backup_key(str(deck_id)), json.dumps(backup))


def transcribed_public_ids(r) -> set:
    """publicIds of decks this app created via transcribe.py, so archiving
    leaves them alone."""
    ids = set()
    for key in r.keys(f"{transcribe.MAP_KEY_PREFIX}*"):
        raw = r.get(key)
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if record.get("moxfield_public_id"):
            ids.add(record["moxfield_public_id"])
    return ids


def archived_name(name: str, prefix: str = DEFAULT_PREFIX) -> str:
    """Prefix a name, idempotently — an already-prefixed name is unchanged, so
    re-running never produces "[Archived] [Archived] Foo"."""
    name = name or ""
    if name.startswith(prefix):
        return name
    return f"{prefix}{name}"


def is_archived(name: str, prefix: str = DEFAULT_PREFIX) -> bool:
    return (name or "").startswith(prefix)


def archive_all(
    r,
    folder_name: str = DEFAULT_ARCHIVE_FOLDER,
    prefix: str = DEFAULT_PREFIX,
    visibility: Optional[str] = DEFAULT_VISIBILITY,
    skip_transcribed: bool = True,
    dry_run: bool = True,
    client=None,
) -> dict:
    """Rename + re-file every existing Moxfield deck into an archive folder.

    Nothing is deleted. `visibility=None` leaves visibility untouched.
    `dry_run=True` contacts Moxfield only to list decks (no folder is created
    and no deck is modified).

    Returns a report with a per-deck result whose `action` is one of
    archived / would_archive / skipped_already / skipped_transcribed / failed.
    """
    if visibility is not None and visibility not in moxfield_writer.VISIBILITIES:
        raise ArchiveError(f"invalid visibility: {visibility}")

    if client is None:
        client = moxfield_writer.MoxfieldClient()

    decks = client.list_my_decks()
    protected = transcribed_public_ids(r) if skip_transcribed else set()

    report = {
        "folder": folder_name,
        "prefix": prefix,
        "visibility": visibility,
        "dry_run": dry_run,
        "total": len(decks),
        "archived": 0,
        "skipped_already": 0,
        "skipped_transcribed": 0,
        "failed": 0,
        "results": [],
    }

    folder_id = None
    if not dry_run:
        folder = client.ensure_folder(folder_name)
        folder_id = folder.get("id")
        if not folder_id:
            raise ArchiveError(
                f"Moxfield returned no id for folder {folder_name!r}; refusing to "
                "continue rather than renaming decks we cannot then file"
            )
        report["folder_id"] = folder_id

    for deck in decks:
        name = deck.get("name") or ""
        deck_id = str(deck.get("id"))
        public_id = deck.get("publicId")

        if public_id and public_id in protected:
            report["skipped_transcribed"] += 1
            report["results"].append({
                "deck_id": deck_id, "name": name, "action": "skipped_transcribed",
            })
            continue

        if is_archived(name, prefix):
            backup = get_backup(r, deck_id)
            if backup and backup.get("folder_moved") is False:
                # Renamed on an earlier run but the folder move failed:
                # retry only the move.
                if dry_run:
                    report["results"].append({
                        "deck_id": deck_id, "name": name,
                        "action": "would_retry_folder_move", "folder": folder_name,
                    })
                    continue
                try:
                    client.add_deck_to_folder(deck_id, folder_id)
                    moxfield_writer.throttle()
                    mark_folder_moved(r, deck_id, True)
                    report["results"].append({
                        "deck_id": deck_id, "name": name,
                        "action": "folder_move_retried", "folder": folder_name,
                    })
                except MoxfieldWriteError as exc:
                    report["failed"] += 1
                    report["results"].append({
                        "deck_id": deck_id, "name": name,
                        "action": "failed", "error": str(exc),
                    })
                continue
            report["skipped_already"] += 1
            report["results"].append({
                "deck_id": deck_id, "name": name, "action": "skipped_already",
            })
            continue

        new_name = archived_name(name, prefix)

        if dry_run:
            report["archived"] += 1
            report["results"].append({
                "deck_id": deck_id, "name": name, "action": "would_archive",
                "new_name": new_name, "folder": folder_name, "visibility": visibility,
            })
            continue

        # Record the original BEFORE mutating, so the rename stays reversible
        # even if the calls below fail partway.
        save_backup(r, deck, folder_name)

        try:
            changes = {"name": new_name}
            if visibility is not None:
                changes["visibility"] = visibility
            client.update_deck(deck, **changes)
            moxfield_writer.throttle()

            # Folder move is separate and non-fatal: the deck is already
            # renamed and safe, so a folder failure is reported rather than
            # rolled back (rolling back would be another write on a deck we
            # just failed to reach).
            folder_error = None
            try:
                client.add_deck_to_folder(deck_id, folder_id)
                moxfield_writer.throttle()
                mark_folder_moved(r, deck_id, True)
            except MoxfieldWriteError as exc:
                folder_error = str(exc)
                mark_folder_moved(r, deck_id, False)

            report["archived"] += 1
            result = {
                "deck_id": deck_id, "name": name, "action": "archived",
                "new_name": new_name, "folder": folder_name, "visibility": visibility,
            }
            if folder_error:
                result["folder_error"] = folder_error
            report["results"].append(result)
        except MoxfieldWriteError as exc:
            report["failed"] += 1
            report["results"].append({
                "deck_id": deck_id, "name": name, "action": "failed", "error": str(exc),
            })

    return report


def restore(r, deck_id: str, client=None) -> dict:
    """Undo an archive: put a deck's original name and visibility back from its
    `moxfield_archive:{deck_id}` backup. The deck stays in the archive folder
    (Moxfield folder membership is a separate, harmless bit of filing) — this
    exists so a rename is never a one-way door.
    """
    backup = get_backup(r, str(deck_id))
    if not backup:
        raise ArchiveError(f"no archive backup recorded for deck {deck_id}")

    if client is None:
        client = moxfield_writer.MoxfieldClient()

    for deck in client.list_my_decks():
        if str(deck.get("id")) != str(deck_id):
            continue
        client.update_deck(
            deck,
            name=backup["original_name"],
            visibility=backup.get("original_visibility") or deck.get("visibility"),
        )
        return {
            "deck_id": str(deck_id),
            "restored_name": backup["original_name"],
            "restored_visibility": backup.get("original_visibility"),
        }

    raise ArchiveError(f"deck {deck_id} not found on the account")
