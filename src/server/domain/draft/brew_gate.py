"""Final gate for every Brew output: no illegal or incomplete deck leaves Brew.

``release_draft`` is the ONLY way a Brew draft reaches a caller (build result,
cached session read, API response, accept). It validates, repairs bounded times
(drop offenders -> ``fill_deck`` top-up -> re-validate) and otherwise raises
``BrewGateError``. It never returns a draft that failed validation.

Checks (``check_draft``): exactly 100 cards incl. commander(s); every card name
resolved and in the catalog; no UUID-shaped string in any output text; singleton
(basics and "any number of" cards exempt); colour identity; banned / not-legal;
commander eligibility incl. partner pairs. The rules live in deck_validator;
this module adds the UUID check and the repair loop.

A draft the user hand-edited (``user_edited``) may legitimately be short or long
mid-edit: size is not enforced on it (nothing is auto-filled), but every other
rule still is, and ``accept`` always demands the full 100.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import src.server.domain.draft.deck_fill as deck_fill
import src.server.domain.draft.deck_repair as deck_repair
import src.server.domain.draft.deck_validator as dv
from src.server.domain.draft.card_resolution import looks_like_id, resolve_card

log = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 3
STAPLES = deck_fill.STAPLES  # fallback fill order after the sources (module-level so tests can pin it)
UUID_RE = re.compile(
    r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}(?![0-9a-fA-F])")
# Keys whose values are legitimately ids (never shown as card names).
_ID_KEYS = frozenset({
    "id", "oracle_id", "scryfall_id", "deck_id", "registry_id", "source_id", "session_id",
    "source", "dropped_ids", "gate", "commander_uids", "uid", "uids", "instance_id",
})
UUID_CODE = "UUID_IN_OUTPUT"
UNRESOLVED_CODE = "UNRESOLVED_NAME"
COMMANDER_ENTRY_CODE = "COMMANDER_ENTRY_MISSING"


class BrewGateError(ValueError):
    """The draft could not be made legal and complete; it must not be returned."""

    def __init__(self, message: str, result: Optional[dv.ValidationResult] = None, attempts: int = 0):
        super().__init__(message)
        self.result = result
        self.attempts = attempts


# -- helpers ------------------------------------------------------------------

def _qty(c: dict) -> int:
    q = c.get("quantity", 1)
    return q if isinstance(q, int) and not isinstance(q, bool) else 0


def fingerprint(draft: dict) -> str:
    payload = [list(draft.get("commanders") or []),
               [(c.get("name"), c.get("quantity")) for c in draft.get("cards") or []],
               bool(draft.get("user_edited"))]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def find_uuid_strings(obj: Any, _key: str = "") -> List[str]:
    """Every UUID-shaped string in `obj`, skipping fields that are ids by design."""
    found: List[str] = []
    if isinstance(obj, str):
        if UUID_RE.search(obj):
            found.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in _ID_KEYS:
                continue
            found += find_uuid_strings(v, k)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found += find_uuid_strings(v, _key)
    return found


def _to_deck(draft: dict) -> dv.DraftDeck:
    cards = [c for c in draft.get("cards") or [] if isinstance(c.get("name"), str) and c["name"].strip()]
    return dv.DraftDeck.from_builder_draft({
        "name": draft.get("name") or "", "commanders": list(draft.get("commanders") or []),
        "cards": [{"name": c["name"], "quantity": c.get("quantity", 1)} for c in cards]})


def check_draft(draft: dict, catalog: dv.CardCatalog, allow_incomplete: bool = False) -> dv.ValidationResult:
    """Validate a brew draft dict without changing it."""
    errors: List[dv.ValidationError] = []
    blank = [c for c in draft.get("cards") or []
             if not isinstance(c.get("name"), str) or not c["name"].strip()]
    if blank:
        errors.append(dv.ValidationError(UNRESOLVED_CODE, [], f"{len(blank)} card(s) have no resolved name.", 0))
    present = {dv.normalize_name(c["name"]) for c in draft.get("cards") or []
               if isinstance(c.get("name"), str)}
    absent = [c for c in draft.get("commanders") or [] if dv.normalize_name(c) not in present]
    if absent:
        errors.append(dv.ValidationError(
            COMMANDER_ENTRY_CODE, absent, "Commander missing from the card list: " + ", ".join(absent), 0))
    res = dv.validate_deck(_to_deck(draft), catalog)
    for e in res.errors:
        if allow_incomplete and str(getattr(e.code, "value", e.code)) == "DECK_SIZE":
            continue
        errors.append(e)
    bad = find_uuid_strings(draft)
    if bad:
        errors.append(dv.ValidationError(
            UUID_CODE, bad[:10], "Output contains GUID/UUID-like strings: " + ", ".join(bad[:5]), 0))
    return dv.ValidationResult(errors)


def _summary(result: dv.ValidationResult) -> str:
    return "; ".join(f"[{str(getattr(e.code, 'value', e.code))}] {e.message}" for e in result.errors)


# -- name resolution ------------------------------------------------------------

def _resolve_names(draft: dict, r: Any) -> Tuple[dict, List[str]]:
    """Copy of draft with ids resolved to real names; unresolvable cards dropped."""
    out = dict(draft)
    dropped: List[str] = []
    cmds = []
    for c in draft.get("commanders") or []:
        rc = resolve_card(c, r)
        if rc is None:
            raise BrewGateError(f"commander {c!r} cannot be resolved to a real card name")
        cmds.append(rc.name)
    out["commanders"] = cmds
    cards = []
    for c in draft.get("cards") or []:
        name = c.get("name")
        rc = resolve_card(name, r)
        if rc is None:
            dropped.append(str(name))
            continue
        cards.append({**c, "name": rc.name})
    out["cards"] = cards
    return out, dropped


# -- sources ---------------------------------------------------------------------

def default_sources(r: Any, session: Optional[dict], draft: dict) -> List[Tuple[str, Callable[[], List[str]]]]:
    """Donor-deck cards, then cached EDHREC picks (cache only: never fetches)."""
    def donors() -> List[str]:
        names: List[str] = []
        for sd in (session or {}).get("donor_decks") or []:
            names += [c.get("name") for c in sd.get("cards", []) if c.get("name")]
        return names

    def edhrec() -> List[str]:
        from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX
        from src.server.domain.analytics.providers.edhrec import commander_slug

        raw = r.get(CACHE_PREFIX + commander_slug(list(draft.get("commanders") or [])))
        cards = ((json.loads(raw) if raw else {}).get("cards") or {}).values()
        ranked = sorted((c for c in cards if isinstance(c, dict) and c.get("name")),
                        key=lambda c: -(c.get("inclusion") or 0))
        return [c["name"] for c in ranked]

    return [("donors", donors), ("edhrec", edhrec)]


# -- repair ------------------------------------------------------------------------

def _pair_catalog(catalog: dv.CardCatalog, commanders: Sequence[str]) -> Tuple[dv.CardCatalog, str]:
    """Catalog with a synthetic single commander carrying the pair's combined
    identity, so fill_deck can fill a partner/background pair."""
    infos = [i for i in (catalog.get(c) for c in commanders) if i is not None]
    ident = frozenset().union(*(i.color_identity for i in infos))
    synth = dv.CardInfo(name="\x00commander-pair", color_identity=ident, types=("Creature",),
                        supertypes=("Legendary",), commander_legality="legal")
    cat = dv.CardCatalog(list({id(c): c for c in catalog._by_key.values()}.values()))
    cat.add(synth)
    return cat, synth.name


def _repair(draft: dict, catalog: dv.CardCatalog, sources: List[Tuple[str, List[str]]],
            allow_incomplete: bool) -> dict:
    """One repair pass: drop offenders, then (unless user-edited) top up to 100."""
    deck = _to_deck(draft)
    meta: Dict[str, dict] = {}
    for c in draft.get("cards") or []:
        if isinstance(c.get("name"), str):
            meta.setdefault(dv.normalize_name(c["name"]), c)
    cmds = list(draft.get("commanders") or [])
    if allow_incomplete:
        deck, _ = deck_repair.drop_invalid_cards(deck, catalog)
        deck, _ = deck_repair.dedupe_singleton(deck, catalog)
        names = [c.name for c in deck.cards for _ in range(c.quantity)]
    else:
        deck, _ = deck_repair.drop_invalid_cards(deck, catalog)
        deck, _ = deck_repair.dedupe_singleton(deck, catalog)
        if len(cmds) + sum(c.quantity for c in deck.cards) > dv.DECK_SIZE:
            deck, _ = deck_repair.fit_to_size(deck, catalog)  # oversize: trim basics first, keep real picks
        names = [c.name for c in deck.cards for _ in range(c.quantity)]
        if len(cmds) == 1:
            filled = deck_fill.fill_deck(cmds[0], names, sources, catalog, staples=STAPLES)
            names = filled[1:]
        elif len(cmds) == 2:
            for c in cmds:
                if catalog.get(c) is None:
                    raise BrewGateError(f"commander {c!r} is not in the card catalog")
            cat2, synth = _pair_catalog(catalog, cmds)
            keep = {dv.normalize_name(c) for c in cmds}
            names = [n for n in names if dv.normalize_name(n) not in keep]
            sources = [(lbl, [n for n in ns if dv.normalize_name(n) not in keep]) for lbl, ns in sources]
            staples = [n for n in STAPLES if dv.normalize_name(n) not in keep]
            filled = deck_fill.fill_deck(synth, names, sources, cat2, staples=staples)
            names = filled[1:1 + dv.DECK_SIZE - 2]
        else:
            raise BrewGateError(f"a deck needs 1 or 2 commanders, got {len(cmds)}")

    cards: List[dict] = []
    index: Dict[str, dict] = {}
    for n in names:
        key = dv.normalize_name(n)
        if key in index:
            index[key]["quantity"] += 1
            continue
        base = meta.get(key)
        if base is not None:
            entry = {**base, "name": n, "quantity": 1}
        else:
            entry = {"name": n, "quantity": 1, "tier": 3, "tier_label": "new", "cost": 0.0,
                     "rationale": "Added to complete the deck"}
        index[key] = entry
        cards.append(entry)
    # commanders stay in the list (builder format) at the front
    cmd_cards = []
    for c in cmds:
        base = meta.get(dv.normalize_name(c)) or {"tier": 3, "tier_label": "new", "cost": 0.0,
                                                 "rationale": "Designated commander"}
        cmd_cards.append({**base, "name": c, "quantity": 1})
    out = dict(draft)
    out["cards"] = cmd_cards + [c for c in cards if dv.normalize_name(c["name"]) not in
                                {dv.normalize_name(x) for x in cmds}]
    return out


def _catalog_for(r: Any, names: Iterable[str]) -> dv.CardCatalog:
    return dv.catalog_from_redis(r, list(dict.fromkeys(n for n in names if isinstance(n, str))))


# -- public API --------------------------------------------------------------------------

def release_draft(draft: dict, r: Any = None, *, session: Optional[dict] = None,
                  catalog: Optional[dv.CardCatalog] = None,
                  sources: Optional[Sequence[Tuple[str, Any]]] = None,
                  max_attempts: int = MAX_REPAIR_ATTEMPTS,
                  allow_incomplete: Optional[bool] = None,
                  recompute: Optional[Callable[[dict], None]] = None,
                  repair: bool = True) -> dict:
    """The single gate. Returns a validated (possibly repaired) copy of `draft`
    stamped ``gate.ok``; raises BrewGateError if it cannot be made legal."""
    if allow_incomplete is None:
        allow_incomplete = bool(draft.get("user_edited"))
    # No stamp short-circuit: catalog facts (bans) and output text can change
    # after a draft was stamped, so every release re-validates.
    current, dropped = _resolve_names(draft, r)
    changed = bool(dropped)
    own_catalog = catalog is None
    cat = catalog if catalog is not None else _catalog_for(
        r, [*current["commanders"], *(c["name"] for c in current["cards"])])
    result = check_draft(current, cat, allow_incomplete)
    attempts = 0
    loaded: Optional[List[Tuple[str, List[str]]]] = None

    while repair and not result.valid and attempts < max_attempts:
        attempts += 1
        if loaded is None:
            raw_sources = sources if sources is not None else default_sources(r, session, current)
            loaded = [(label, deck_fill._load_source(label, src, deck_fill.DEFAULT_SOURCE_TIMEOUT))
                      for label, src in raw_sources]
            if own_catalog:
                extra = [n for _, ns in loaded for n in ns] + list(STAPLES)
                cat = _catalog_for(r, [*current["commanders"], *(c["name"] for c in current["cards"]), *extra])
        try:
            current = _repair(current, cat, loaded, allow_incomplete)
        except ValueError as exc:  # fill_deck: unknown / ineligible commander
            if isinstance(exc, BrewGateError):
                raise
            raise BrewGateError(f"cannot repair brew deck: {exc}", result, attempts) from exc
        changed = True
        result = check_draft(current, cat, allow_incomplete)

    if not result.valid:
        if not repair:
            raise BrewGateError(f"Brew deck failed validation: {_summary(result)}", result, 0)
        log.error("brew gate: draft rejected after %d repair attempt(s): %s", attempts, _summary(result))
        raise BrewGateError(f"Brew deck failed validation: {_summary(result)}", result, attempts)

    out = dict(current)
    if changed:
        out["missing_slots"] = 0 if not allow_incomplete else max(dv.DECK_SIZE - sum(_qty(c) for c in out["cards"]), 0)
        out["gate_repaired"] = True
        if recompute is not None:
            recompute(out)
    out["gate"] = {"ok": True, "fp": fingerprint(out), "inc": bool(allow_incomplete)}
    return out
