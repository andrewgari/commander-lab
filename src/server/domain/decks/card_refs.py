"""Printing identity ("cardRef") for every place that renders a card name or thumbnail.

The hover preview must show the exact printing, so each render site needs more
than a name. One shape travels everywhere (API payloads, the embedded per-deck
map, `data-card-ref` attributes, HUD.cardRef() in static/js/hud.js):

    CardRef = {
        name:             str            card name as displayed
        scryfallId:       str | None     Scryfall printing id (Archidekt `uid`)
        oracleId:         str | None     Scryfall oracle_id (printing-independent)
        set:              str | None     set code, lowercase
        collectorNumber:  str | None     collector number within the set
        layout:           str | None     Scryfall layout, when known
        imageUris:        dict | None    Scryfall image_uris, when known
        cardFaces:        list | None    Scryfall card_faces, when known
        printingSpecific: bool           True when scryfallId, or set + collectorNumber, is known
        source:           str            where the printing came from:
                                         "instance" | "deck" | "owned" | "commander" | "name"
    }

Identifier precedence for a consumer (see docs/CARD_RENDER_AUDIT.md section 6):
scryfallId, then set + collectorNumber, then oracleId, then name. A ref with
`printingSpecific` False is a name-only fallback: it is NOT printing-specific
and a preview built from it may show any printing of the card.

`imageUris` / `cardFaces` are None today: nothing stored in Redis carries them
(Archidekt and the instance registry keep ids, not Scryfall image JSON). The
fields are part of the shape so a server-side resolver can fill them in later
without changing any consumer; until then the client builds the image URL from
`scryfallId` (HUD.scryfall) and a double-faced card shows its front face.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, TypedDict

from jinja2 import pass_context
from markupsafe import Markup

import src.server.domain.inventory.instances as instance_store
import src.server.domain.decks.registry as registry


class CardRef(TypedDict):
    """Every key is always present (None when unknown); see the module docstring."""

    name: str
    scryfallId: Optional[str]
    oracleId: Optional[str]
    set: Optional[str]
    collectorNumber: Optional[str]
    layout: Optional[str]
    imageUris: Optional[Dict[str, str]]
    cardFaces: Optional[List[Dict[str, Any]]]
    printingSpecific: bool
    source: str


# Ownership preference when one card name has several instances: the copy that
# sits in a deck beats a binder copy, which beats one still in the mail.
_STATUS_RANK = {"in_deck": 0, "in_collection": 1, "in_mail": 2}


def card_key(name: Any) -> str:
    """Normalised lookup key for a card name: front face, lowercase.

    Mirrors cardKey() in templates/deck.html and HUD.cardKey() in hud.js, so a
    split / double-faced name ("A // B") finds the same ref as "A".
    """
    return str(name or "").split(" // ")[0].strip().lower()


def _clean(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def make_ref(
    name: str,
    *,
    scryfall_id: Any = None,
    oracle_id: Any = None,
    set_code: Any = None,
    collector_number: Any = None,
    layout: Any = None,
    image_uris: Optional[Mapping[str, str]] = None,
    card_faces: Optional[List[Mapping[str, Any]]] = None,
    source: Optional[str] = None,
) -> CardRef:
    """Build a CardRef. Every key is always present (None when unknown)."""
    sid = _clean(scryfall_id)
    sid = sid.lower() if sid else None
    set_ = _clean(set_code)
    set_ = set_.lower() if set_ else None
    cn = _clean(collector_number)
    specific = bool(sid or (set_ and cn))
    return {
        "name": name or "",
        "scryfallId": sid,
        "oracleId": _clean(oracle_id),
        "set": set_,
        "collectorNumber": cn,
        "layout": _clean(layout),
        "imageUris": dict(image_uris) if image_uris else None,
        "cardFaces": [dict(f) for f in card_faces] if card_faces else None,
        "printingSpecific": specific,
        "source": source or ("instance" if specific else "name"),
    }


def name_only_ref(name: str) -> CardRef:
    """Fallback for a card with no known printing (suggested adds, buys, combo
    pieces, EDHREC lists, user-entered decklists). NOT printing-specific."""
    return make_ref(name, source="name")


def ref_from_instance(inst: Mapping[str, Any], oracle_id: Any = None, source: str = "instance") -> CardRef:
    """Ref for one owned copy (instances.py record): `archidekt_uid` is the
    Scryfall printing id; `set` + `collector_number` are the fallback."""
    return make_ref(
        inst.get("card_name") or "",
        scryfall_id=inst.get("archidekt_uid"),
        oracle_id=oracle_id,
        set_code=inst.get("set"),
        collector_number=inst.get("collector_number"),
        source=source,
    )


def ref_from_commander(name: str, uid: Any) -> CardRef:
    """Ref for a commander known only by name + printing uid (deck record)."""
    return make_ref(name, scryfall_id=uid, source="commander")


def compact(ref: Mapping[str, Any]) -> Dict[str, Any]:
    """Ref without its None fields: what goes into data-card-ref attributes and
    the embedded per-deck map. Consumers normalise through HUD.cardRef()."""
    return {k: v for k, v in ref.items() if v is not None}


def _instance_sort_key(inst: Mapping[str, Any], deck_refs: set) -> tuple:
    status = inst.get("ownership_status")
    in_this_deck = status == "in_deck" and str(inst.get("deck_id")) in deck_refs
    has_printing = bool(inst.get("archidekt_uid") or (inst.get("set") and inst.get("collector_number")))
    return (0 if in_this_deck else 1, 0 if has_printing else 1, _STATUS_RANK.get(status or "", 9))


def build_index(
    all_instances: Iterable[Mapping[str, Any]],
    deck: Optional[Mapping[str, Any]] = None,
) -> Dict[str, CardRef]:
    """{card_key: CardRef} from instance records, deck first.

    Per card name the best copy wins: one bound to `deck`, then one with a known
    printing, then in_deck / in_collection / in_mail. `not_owned` wishlist
    placeholders are skipped (no real printing is attached to them). The deck's
    commanders fill any name the instances do not cover (a companion sits
    outside the 100 and may have no instance).
    """
    deck_refs = ({str(i) for i in registry.deck_instance_ids(dict(deck))} | {str(deck.get("name") or "")}) - {""} if deck else set()
    best: Dict[str, Mapping[str, Any]] = {}
    for inst in sorted(all_instances, key=lambda i: _instance_sort_key(i, deck_refs)):
        if inst.get("ownership_status") == "not_owned" or not inst.get("card_name"):
            continue
        best.setdefault(card_key(inst["card_name"]), inst)
    index: Dict[str, CardRef] = {}
    for key, inst in best.items():
        in_deck = inst.get("ownership_status") == "in_deck" and str(inst.get("deck_id")) in deck_refs
        index[key] = ref_from_instance(inst, source="deck" if in_deck else "owned")
    if deck:
        names = list(deck.get("commanders") or [])
        uids = [u for u in (deck.get("commander_uids") or []) if u]
        # Same pairing rule as commander_roles.deck_commanders: several names
        # with a different uid count means the pairing is unknown.
        if len(names) > 1 and len(uids) != len(names):
            uids = []
        for i, nm in enumerate(names):
            key = card_key(nm)
            if key and key not in index:
                index[key] = ref_from_commander(nm, uids[i] if i < len(uids) else None)
    return index


def deck_instances(all_instances: Iterable[Mapping[str, Any]], deck: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Instances bound to `deck` (in_deck under any of its ids or its name)."""
    refs = {str(i) for i in registry.deck_instance_ids(dict(deck))} | {str(deck.get("name") or "")}
    refs.discard("")
    return [i for i in all_instances
            if i.get("ownership_status") == "in_deck" and str(i.get("deck_id") or "") in refs]


def deck_card_refs(r, deck: Mapping[str, Any]) -> Dict[str, CardRef]:
    """Printing map for one deck page: {card_key: CardRef}, built from the
    deck's own instances plus its commanders. Kept to the deck (not the whole
    collection) because it is embedded in every deck page; a card the deck
    does not hold (tune adds, theory buys, EDHREC suggestions) resolves to the
    name-only fallback, which is NOT printing-specific."""
    return build_index(deck_instances(instance_store.load_all_instances(r), deck), deck)


def lookup(refs: Optional[Mapping[str, Any]], name: Any) -> CardRef:
    """Ref for `name` from a deck map, else the name-only fallback."""
    hit = (refs or {}).get(card_key(name))
    return hit if hit else name_only_ref(str(name or ""))


def html_attrs(
    name: Any,
    refs: Optional[Mapping[str, Any]] = None,
    ref: Optional[Mapping[str, Any]] = None,
    uid: Any = None,
) -> Markup:
    """Jinja global `card_ref_attrs(name, refs, ref)`: the data attributes that mark
    a rendered card name or thumbnail for the hover preview.

        <span class="hud-card-name" {{ card_ref_attrs(c.name, head.card_refs) }}>…</span>

    `ref` (an explicit CardRef) wins over the deck map lookup; `uid` (a known
    Scryfall printing id, e.g. a commander role's) builds a ref directly. With
    none of them the ref is the name-only fallback.
    """
    if ref:
        use = dict(ref)
    elif uid:
        use = dict(ref_from_commander(str(name or ""), uid))
    else:
        use = dict(lookup(refs, name))
    if not use.get("name"):
        use["name"] = str(name or "")
    payload = json.dumps(compact(use), separators=(",", ":"), sort_keys=True)
    return Markup('data-card-name="{}" data-card-ref="{}"').format(use["name"], payload)


@pass_context
def preview_attrs(ctx: Any, name: Any) -> Markup:
    """Jinja global `card_preview_attrs(name)`: hover-preview trigger attributes for
    a card name, joined to the page's deck printing map (`head.card_refs`) when the
    page has one, else a name-only ref (e.g. a partial rendered stand-alone)."""
    head = ctx.get("head")
    refs = head.get("card_refs") if isinstance(head, Mapping) else None
    return Markup("data-card-preview ") + html_attrs(name, refs)
