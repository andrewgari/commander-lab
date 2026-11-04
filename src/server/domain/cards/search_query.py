"""Card search: filters -> Scryfall query, hit shaping, paging, pairing
(docs/ui/card-search.md §3, §3.1.3, §5). Pure: no I/O.

Translation table (§3). All filters are ANDed; values inside one multi-select
filter are ORed. ``owned`` never reaches Scryfall.

    name        each token            -> name:<tok>
    ci          W U B R G (C)         -> id<=wub | id=wub | id>=wub ; C alone -> id=c
    type        chips (OR)            -> (t:creature or t:artifact)
    type_text   free words            -> t:<word> ...
    mv          0..6, 7+ (OR)         -> (mv=1 or mv=2 or mv>=7)
    rarity      chips (OR)            -> (r:rare or r:mythic)
    oracle      terms / "phrases"     -> o:<term> / o:"a phrase"
    cmdr        flag                  -> is:commander
    (always)                          -> legal:commander game:paper
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from src.server.domain.draft.deck_validator import CardInfo, PartnerProfile, partner_profile

ALWAYS = "legal:commander game:paper"
COLOR_ORDER = "WUBRG"
TYPES = ("creature", "instant", "sorcery", "artifact", "enchantment",
         "planeswalker", "land", "battle")
RARITIES = ("common", "uncommon", "rare", "mythic")
MV_CHIPS = ("0", "1", "2", "3", "4", "5", "6", "7+")
CI_MODES = {"within": "<=", "exact": "=", "includes": ">="}
OWNED_MODES = ("any", "owned", "not_owned")
SCRYFALL_PAGE = 175          # unique=cards page size


class SearchParamError(ValueError):
    """A request parameter is malformed (HTTP 400)."""


@dataclass(frozen=True)
class SearchFilters:
    name_tokens: Tuple[str, ...] = ()
    colors: str = ""                 # canonical WUBRG-ordered letters, or "C"
    ci_mode: str = "within"
    types: Tuple[str, ...] = ()
    type_words: Tuple[str, ...] = ()
    mv: Tuple[str, ...] = ()
    rarities: Tuple[str, ...] = ()
    oracle_terms: Tuple[str, ...] = ()
    owned: str = "any"
    cmdr: bool = False
    pair: Optional[str] = None       # oracle_id of the first commander


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _csv(value) -> list:
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    return [p.strip().lower() for p in str(value or "").split(",") if p.strip()]


def _clean(token: str) -> str:
    return token.replace('"', "").replace("\\", "").strip()


def _words(value) -> Tuple[str, ...]:
    return tuple(t for t in (_clean(p) for p in re.split(r"[\s,]+", str(value or ""))) if t)


def _terms(value) -> Tuple[str, ...]:
    """Whitespace-separated terms; a "quoted phrase" stays one term."""
    out = []
    for m in re.finditer(r'"([^"]*)"|(\S+)', str(value or "")):
        term = _clean(m.group(1) if m.group(1) is not None else m.group(2))
        if term:
            out.append(term)
    return tuple(out)


def _one_of(values, allowed, label) -> Tuple[str, ...]:
    out = []
    for v in values:
        if v not in allowed:
            raise SearchParamError(f"unknown {label} {v!r}")
        if v not in out:
            out.append(v)
    return tuple(out)


def parse_filters(*, name: str = "", ci: str = "", ci_mode: str = "within", type: str = "",
                  type_text: str = "", mv: str = "", rarity: str = "", oracle: str = "",
                  owned: str = "any", cmdr: bool = False,
                  pair: Optional[str] = None) -> SearchFilters:
    mode = (ci_mode or "within").lower()
    if mode not in CI_MODES:
        raise SearchParamError(f"ci_mode must be one of {sorted(CI_MODES)}")
    owned_mode = (owned or "any").lower()
    if owned_mode not in OWNED_MODES:
        raise SearchParamError(f"owned must be one of {list(OWNED_MODES)}")
    letters = []
    for ch in str(ci or "").upper().replace(",", ""):
        if ch not in "WUBRGC":
            raise SearchParamError(f"unknown color {ch!r}")
        if ch not in letters:
            letters.append(ch)
    real = [c for c in COLOR_ORDER if c in letters]
    colors = "".join(real) if real else ("C" if letters else "")
    return SearchFilters(
        name_tokens=_words(name),
        colors=colors,
        ci_mode=mode,
        types=_one_of(_csv(type), TYPES, "type"),
        type_words=_words(type_text),
        mv=_one_of(_csv(mv), MV_CHIPS, "mana value"),
        rarities=_one_of(_csv(rarity), RARITIES, "rarity"),
        oracle_terms=_terms(oracle),
        owned=owned_mode,
        cmdr=bool(cmdr),
        pair=(pair or None),
    )


# ---------------------------------------------------------------------------
# Scryfall query
# ---------------------------------------------------------------------------

def _bare_or_quoted(prefix: str, token: str) -> str:
    token = _clean(token)
    if re.fullmatch(r"[A-Za-z0-9]+", token):
        return f"{prefix}{token}"
    return f'{prefix}"{token}"'


def _group(parts) -> str:
    return "(" + " or ".join(parts) + ")"


def _mv_clause(chip: str) -> str:
    return f"mv>={chip[:-1]}" if chip.endswith("+") else f"mv={chip}"


def color_clause(colors: str, mode: str) -> str:
    if not colors:
        return ""
    if colors == "C":
        return "id=c"
    return f"id{CI_MODES[mode]}{colors.lower()}"


def build_scryfall_query(f: SearchFilters, pair_clause: Optional[str] = None) -> str:
    parts = [ALWAYS]
    parts += [_bare_or_quoted("name:", t) for t in f.name_tokens]
    if f.colors:
        parts.append(color_clause(f.colors, f.ci_mode))
    if f.types:
        parts.append(_group([f"t:{t}" for t in f.types]))
    parts += [_bare_or_quoted("t:", w) for w in f.type_words]
    if f.mv:
        parts.append(_group([_mv_clause(c) for c in f.mv]))
    if f.rarities:
        parts.append(_group([f"r:{r}" for r in f.rarities]))
    parts += [_bare_or_quoted("o:", t) for t in f.oracle_terms]
    if f.cmdr:
        parts.append("is:commander")
    if pair_clause:
        parts.append(pair_clause)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# pairing (§3.1.3)
# ---------------------------------------------------------------------------

def _quote_name(name: str) -> str:
    return _clean(name)


def pair_clause(profile: PartnerProfile, first_name: str) -> Optional[str]:
    """The Scryfall clause that narrows a second-commander search, plus the
    exclusion of the first card itself. None when the card cannot take a
    partner."""
    clauses = []
    if profile.partner_with:
        clauses.append(f'!"{_quote_name(profile.partner_with)}"')
    if profile.partner_group:
        clauses.append(f'o:"partner\u2014{_quote_name(profile.partner_group)}"')
    if profile.partner:
        clauses.append("o:/^partner$/")
    if profile.friends_forever:
        clauses.append('o:"friends forever"')
    if profile.choose_background:
        clauses.append("t:background")
    if profile.is_background:
        clauses.append('kw:"choose a background"')
    if profile.doctors_companion:
        clauses.append("t:doctor")
    if profile.is_doctor:
        clauses.append('kw:"doctor\'s companion"')
    if not clauses:
        return None
    body = clauses[0] if len(clauses) == 1 else _group(clauses)
    return f'{body} -!"{_quote_name(first_name)}"'


def pair_sql_hints(profile: PartnerProfile) -> list:
    """(column, substring) pairs, ORed, that cheaply pre-narrow the local
    (Postgres) candidates; check_partner_pair stays the authority."""
    hints = []
    if profile.partner_with:
        hints.append(("oracle_text", profile.partner_with.casefold()))
    if profile.partner or profile.partner_group:
        hints.append(("oracle_text", "partner"))
    if profile.friends_forever:
        hints.append(("oracle_text", "friends forever"))
    if profile.choose_background:
        hints.append(("type_line", "background"))
    if profile.is_background:
        hints.append(("oracle_text", "choose a background"))
    if profile.doctors_companion:
        hints.append(("type_line", "doctor"))
    if profile.is_doctor:
        hints.append(("oracle_text", "doctor's companion"))
    return hints


_PAIRING_ORDER = (
    ("partner_with", "partner_with"), ("partner_group", "partner_group"),
    ("partner", "partner"), ("friends_forever", "friends_forever"),
    ("choose_background", "choose_background"), ("is_background", "background"),
    ("doctors_companion", "doctors_companion"), ("is_doctor", "doctor"),
)


def pairing_label(profile: PartnerProfile) -> Optional[str]:
    for attr, label in _PAIRING_ORDER:
        if getattr(profile, attr):
            return label
    return None


def card_info(facts: Mapping[str, Any]) -> CardInfo:
    from src.server.domain.cards.commander_eligibility import front_face
    return CardInfo.from_record({
        "name": facts.get("name", ""),
        "type_line": front_face(facts.get("type_line") or ""),
        "oracle_text": facts.get("oracle_text") or "",
        "keywords": facts.get("_keywords") or [],
        "color_identity": facts.get("color_identity") or [],
    })


# ---------------------------------------------------------------------------
# paging (§5.1): our pages are slices of Scryfall's 175-card pages
# ---------------------------------------------------------------------------

def map_page(page: int, page_size: int) -> Tuple[int, int]:
    """(upstream page, offset inside it). Every request needs exactly one
    upstream page: each Scryfall page is cut into ceil(175/page_size) pages
    (60 -> 60, 60, 55), so a page never straddles two upstream pages."""
    per_upstream = math.ceil(SCRYFALL_PAGE / page_size)
    return (page - 1) // per_upstream + 1, ((page - 1) % per_upstream) * page_size


# ---------------------------------------------------------------------------
# hit shaping
# ---------------------------------------------------------------------------

def facts_from_scryfall(card: Mapping[str, Any]) -> dict:
    """Card facts for the cache and the response, from a Scryfall card object.
    Underscore keys are internal and stripped before the response."""
    faces = card.get("card_faces") or []
    front = faces[0] if faces else {}
    iu = card.get("image_uris") or front.get("image_uris") or {}
    text = card.get("oracle_text")
    if text is None:
        text = "\n".join(f.get("oracle_text") or "" for f in faces).strip()
    return {
        "oracle_id": card.get("oracle_id") or front.get("oracle_id") or "",
        "name": card.get("name", ""),
        "mana_cost": card.get("mana_cost") or front.get("mana_cost") or "",
        "cmc": float(card.get("cmc") or 0),
        "type_line": card.get("type_line", ""),
        "color_identity": card.get("color_identity") or [],
        "rarity": card.get("rarity", ""),
        "oracle_text": text or "",
        "image": iu.get("art_crop") or iu.get("small") or "",
        "_keywords": card.get("keywords") or [],
        "_legal": (card.get("legalities") or {}).get("commander", ""),
    }


def eligibility_row(facts: Mapping[str, Any]) -> dict:
    return {"oracle_id": facts.get("oracle_id"), "type_line": facts.get("type_line"),
            "oracle_text": facts.get("oracle_text"),
            "legalities": {"commander": facts.get("_legal", "")}}


PUBLIC_KEYS = ("oracle_id", "name", "mana_cost", "cmc", "type_line", "color_identity",
               "rarity", "oracle_text", "image")


def public_hit(facts: Mapping[str, Any], *, owned_qty: int, free_qty: int,
               can_be_commander: bool, pairing: Optional[str]) -> dict:
    hit = {k: facts.get(k) for k in PUBLIC_KEYS}
    hit.update(owned_qty=owned_qty, free_qty=free_qty,
               can_be_commander=can_be_commander, pairing=pairing)
    return hit


def identity_matches(card_identity, colors: str, mode: str) -> bool:
    """Local (SQL-less) color-identity test, mirroring id<=, id=, id>=."""
    if not colors:
        return True
    have = {c.upper() for c in (card_identity or [])}
    if colors == "C":
        return not have
    want = set(colors)
    if mode == "exact":
        return have == want
    if mode == "includes":
        return want <= have
    return have <= want
