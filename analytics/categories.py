"""Category registry (CT): card roles, in three tiers. Spec: categories-v1 + docs/design/categories/SPEC.md.

* **Core** (locked, versioned, defined here): the 11 built-in roles plus an Interaction group header.
  Membership is *not stored here*: it reuses the auto-tag function tags (``deck_card_tags`` entries whose
  tag equals the core id) and, for Lands, the front-face type line. Only the per-deck *target* can be
  overridden (the ``composition:template:{registry_id}`` override).
* **Global custom**: ``categories:global`` (JSON list) + ``categories:version``. Apply to every deck.
* **Deck custom**: ``categories:deck:{registry_id}`` (JSON list), capped at DECK_CAP per deck.

Members of a custom category are ``cat:<id>`` entries (source ``user``) in ``deck_card_tags``. A category
``rule`` only feeds *suggestions*; nothing is ever auto-assigned. Counts exclude the commander (the
autotag convention) and count distinct card names.

Pure functions where possible (``core_rows``, ``suggest_members``); storage helpers take a redis client.
"""
from __future__ import annotations

import functools
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from analytics import composition as comp
from autotag.engine import THEME_TAGS

SPEC_VERSION = "categories-v1"
GLOBAL_KEY = "categories:global"
VERSION_KEY = "categories:version"
DECK_KEY_PREFIX = "categories:deck:"
CAT_TAG_PREFIX = "cat:"
NAME_MAX = 32
DESC_MAX = 400
DECK_CAP = 10
RULE_VALUE_MAX = 30
MIN_STATUS_DECK = 90
TARGET_MAX = 99

GROUPS: Tuple[dict, ...] = (
    {"id": "mana", "label": "Mana", "note": ""},
    {"id": "cards", "label": "Cards", "note": ""},
    {"id": "interaction", "label": "Interaction", "note": "Removal · Counterspells · Board Wipes"},
    {"id": "defense", "label": "Defense", "note": ""},
    {"id": "closing", "label": "Closing", "note": ""},
)
GROUP_IDS = tuple(g["id"] for g in GROUPS)
INTERACTION_IDS = ("removal", "counterspell", "board_wipe")


def _core(cid, group, name, description, source, template_row=None, floor=None, shared=False, floor_note=""):
    return {"id": cid, "scope": "core", "group": group, "name": name, "description": description,
            "source": source, "template_row": template_row, "floor": floor, "shared": shared,
            "floor_note": floor_note}


CORE: Tuple[dict, ...] = (
    _core("lands", "mana", "Lands",
          "Cards whose front face is a land. A modal double-faced card with a spell front isn't counted here.",
          "front-face type line", template_row="lands"),
    _core("ramp", "mana", "Ramp",
          "Makes extra mana, puts extra lands onto the battlefield, or makes spells cost less. Lands are never ramp.",
          "autotag ramp", template_row="ramp", floor=10),
    _core("mana_fixing", "mana", "Fixing",
          "Produces two or more of the deck's colors. Nonland cards only count in decks with 3+ colors.",
          "autotag mana_fixing"),
    _core("card_advantage", "cards", "Card Draw",
          "Draws more than one card, draws repeatedly, or lets you play cards from exile (impulse draw). "
          "A one-off cantrip doesn't count.", "autotag card_advantage", template_row="card_advantage", floor=7),
    _core("tutor", "cards", "Tutors",
          "Searches your library for a nonland card. Land searches are Ramp.", "autotag tutor"),
    _core("recursion", "cards", "Recursion",
          "Returns cards from your graveyard to hand or battlefield, or lets you cast them from there.",
          "autotag recursion"),
    _core("removal", "interaction", "Removal",
          "Answers one opposing permanent: destroy, exile, damage, -X/-X, bounce, tuck, edict or fight.",
          "autotag removal", template_row="targeted_disruption", floor=10,
          floor_note="Floor 10 covers Removal + Counterspells together."),
    _core("counterspell", "interaction", "Counterspells",
          "Counters a spell or ability on the stack.", "autotag counterspell", shared=True),
    _core("board_wipe", "interaction", "Board Wipes",
          "Answers many permanents at once.", "autotag board_wipe", template_row="mass_disruption", floor=2),
    _core("protection", "defense", "Protection",
          "Keeps your own things alive: hexproof, shroud, indestructible, protection, phasing, or fogs.",
          "autotag protection"),
    _core("finisher", "closing", "Win Cons",
          "Ends the game, or turns your board into lethal: alt-wins, mass pump, extra combats, poison, repeated drain.",
          "autotag finisher", floor=2),
)
CORE_BY_ID: Dict[str, dict] = {c["id"]: c for c in CORE}

# Core aliases (Oracle 4.2), normalised: lowercase, spaces collapsed, one trailing "s" ignored.
# "Counters" and "Reanimation" are deliberately NOT aliases.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "ramp": ("ramp", "mana", "acceleration", "mana rock", "rock", "dork", "mana dork"),
    "card_advantage": ("draw", "card draw", "card advantage", "ca", "card"),
    "removal": ("removal", "spot removal", "targeted removal", "kill spell"),
    "board_wipe": ("wipe", "board wipe", "sweeper", "wrath", "mass removal"),
    "counterspell": ("counterspell", "counter spell", "counter magic"),
    "tutor": ("tutor", "search"),
    "recursion": ("recursion", "graveyard recursion"),
    "protection": ("protection", "protect"),
    "finisher": ("finisher", "win con", "wincon", "win condition"),
    "lands": ("land",),
    "mana_fixing": ("fixing", "mana fixing", "color fixing"),
}
GROUP_ALIASES = ("interaction", "disruption")
RULE_KINDS = ("autotag", "kindred", "type")
FLAG_NAMES = ("game_changer", "stax", "mass_land_denial", "extra_turns")


class CategoryError(ValueError):
    """Invalid payload. ``status`` is the HTTP code; ``kind`` / ``extra`` ride into the JSON body."""

    def __init__(self, message: str, status: int = 400, kind: str = "invalid", **extra):
        super().__init__(message)
        self.status, self.kind, self.extra = status, kind, extra


class CategoryNotFound(KeyError):
    pass


# -- names, aliases, rules ------------------------------------------------------------------

def norm_name(s: str) -> str:
    return re.sub(r"s$", "", " ".join((s or "").split()).lower())


def alias_hit(name: str) -> Optional[dict]:
    """{"core_id": id} for a core alias, {"group": "interaction"} for the group names, else None."""
    n = norm_name(name)
    if not n:
        return None
    if n in GROUP_ALIASES:
        return {"group": "interaction"}
    for cid, words in ALIASES.items():
        if n in words:
            return {"core_id": cid}
    return None


def check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise CategoryError("Give it a name.", 400)
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise CategoryError(f"Name is longer than {NAME_MAX} characters.", 400)
    hit = alias_hit(name)
    if hit and "core_id" in hit:
        c = CORE_BY_ID[hit["core_id"]]
        raise CategoryError(f"That's the core category {c['name']}. Use it instead.", 422, "alias", core_id=c["id"])
    if hit:
        raise CategoryError("That's the Interaction group (Removal, Counterspells, Board Wipes). "
                            "Pick a narrower name, or open one of them.", 422, "alias", core_id="removal", group=True)
    return name


def validate_rule(rule: Any) -> str:
    """'' or 'kind:value' with kind in RULE_KINDS. autotag values must be a theme tag."""
    if rule in (None, ""):
        return ""
    if not isinstance(rule, str) or ":" not in rule:
        raise CategoryError("A rule looks like autotag:sacrifice, kindred:Vampire or type:Equipment.", 400, "rule")
    kind, _, value = rule.partition(":")
    kind, value = kind.strip().lower(), " ".join(value.split())
    if kind not in RULE_KINDS:
        raise CategoryError(f"unknown rule kind “{kind}”", 400, "rule")
    if not value or len(value) > RULE_VALUE_MAX:
        raise CategoryError(f"A rule value is 1-{RULE_VALUE_MAX} characters.", 400, "rule")
    if kind == "autotag":
        value = value.lower()
        if value not in THEME_TAGS:
            raise CategoryError(f"“{value}” is not an autotag theme.", 400, "rule")
    return f"{kind}:{value}"


def _clean_fields(body: Any, base: Optional[dict] = None) -> dict:
    if not isinstance(body, dict):
        raise CategoryError("Body must be a JSON object.", 400)
    base = base or {}

    def pick(k, default=""):
        return body[k] if k in body else base.get(k, default)

    name = check_name(pick("name"))
    desc = pick("description")
    if desc is None:
        desc = ""
    if not isinstance(desc, str):
        raise CategoryError("description must be text.", 400)
    desc = " ".join(desc.split())
    if len(desc) > DESC_MAX:
        raise CategoryError(f"Description is longer than {DESC_MAX} characters.", 400)
    group = pick("group") or ""
    if group not in ("",) + GROUP_IDS:
        raise CategoryError("Unknown group.", 400)
    pillar = pick("pillar_id") or ""
    if not isinstance(pillar, str) or len(pillar) > 64:
        raise CategoryError("pillar_id must be a short string.", 400)
    return {"name": name, "description": desc, "group": group, "rule": validate_rule(pick("rule")),
            "pillar_id": pillar}


# -- storage ----------------------------------------------------------------------------------

_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _LOCK:
            return fn(*a, **kw)
    return wrapper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_list(r, key: str) -> List[dict]:
    try:
        data = json.loads(r.get(key) or "[]")
    except (TypeError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("id")]


def _save_list(r, key: str, items: List[dict]) -> None:
    r.set(key, json.dumps(items, ensure_ascii=False))
    r.incr(VERSION_KEY)


def get_version(r) -> int:
    try:
        return int(r.get(VERSION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def deck_key(registry_id: Optional[str]) -> str:
    return DECK_KEY_PREFIX + (registry_id or "")


def list_global(r) -> List[dict]:
    return sorted(_load_list(r, GLOBAL_KEY), key=lambda c: (c["name"].lower(), c["id"]))


def list_deck(r, registry_id: str) -> List[dict]:
    return sorted(_load_list(r, deck_key(registry_id)), key=lambda c: (c["name"].lower(), c["id"]))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "category"


def find(r, cat_id: str, registry_id: Optional[str] = None) -> Tuple[dict, str, str]:
    """(category, storage key, scope). Deck ids (``d-…``) need the deck's registry id."""
    if cat_id in CORE_BY_ID:
        raise CategoryError("Core categories are locked.", 403, "core")
    if cat_id.startswith("d-"):
        if not registry_id:
            raise CategoryError("deck_id is required for a deck category.", 400)
        key, scope = deck_key(registry_id), "deck"
    else:
        key, scope = GLOBAL_KEY, "global"
    for c in _load_list(r, key):
        if c["id"] == cat_id:
            return c, key, scope
    raise CategoryNotFound(cat_id)


def _name_clash(r, name: str, scope: str, registry_id: Optional[str], skip_id: Optional[str]) -> Optional[dict]:
    lc = name.lower()
    pools = [list_global(r)] + ([list_deck(r, registry_id)] if registry_id else [])
    if scope == "global":
        pools = [list_global(r)]
    for pool in pools:
        for c in pool:
            if c["id"] != skip_id and c["name"].lower() == lc:
                return c
    return None


@_locked
def create(r, body: Any, registry_id: Optional[str] = None) -> dict:
    if not isinstance(body, dict):
        raise CategoryError("Body must be a JSON object.", 400)
    scope = body.get("scope")
    if scope not in ("global", "deck"):
        raise CategoryError("scope must be global or deck.", 400)
    if scope == "deck" and not registry_id:
        raise CategoryError("A deck category needs a deck.", 400)
    f = _clean_fields(body)
    if scope == "global":
        f["pillar_id"] = ""
    if _name_clash(r, f["name"], scope, registry_id, None):
        raise CategoryError("A category with that name already exists" + (" in this deck." if scope == "deck" else "."),
                            409, "duplicate")
    if scope == "deck":
        items = _load_list(r, deck_key(registry_id))
        if len(items) >= DECK_CAP:
            raise CategoryError(f"This deck already has {DECK_CAP} categories. Past that they stop being roles.",
                                409, "cap")
        cat = {"id": f"d-{_slug(f['name'])}-{uuid.uuid4().hex[:6]}", "scope": "deck", "deck_id": registry_id, **f}
        key = deck_key(registry_id)
    else:
        items = _load_list(r, GLOBAL_KEY)
        cat = {"id": f"g-{_slug(f['name'])}-{uuid.uuid4().hex[:6]}", "scope": "global", **f}
        key = GLOBAL_KEY
    cat["updated_at"] = _now()
    items.append(cat)
    _save_list(r, key, items)
    return cat


@_locked
def update(r, cat_id: str, body: Any, registry_id: Optional[str] = None) -> dict:
    if isinstance(body, dict) and "scope" in body:
        raise CategoryError("A category's scope can't change. Use Make global.", 400)
    cat, key, scope = find(r, cat_id, registry_id)
    f = _clean_fields(body, base=cat)
    if scope == "global":
        f["pillar_id"] = ""
    if _name_clash(r, f["name"], scope, registry_id, cat_id):
        raise CategoryError("A category with that name already exists" + (" in this deck." if scope == "deck" else "."),
                            409, "duplicate")
    items = _load_list(r, key)
    for i, c in enumerate(items):
        if c["id"] == cat_id:
            items[i] = {**c, **f, "updated_at": _now()}
            _save_list(r, key, items)
            return items[i]
    raise CategoryNotFound(cat_id)


@_locked
def delete(r, cat_id: str, registry_id: Optional[str] = None) -> dict:
    """Remove the definition. Returns it; the caller strips ``cat:<id>`` tags (cards keep other tags)."""
    cat, key, _ = find(r, cat_id, registry_id)
    _save_list(r, key, [c for c in _load_list(r, key) if c["id"] != cat_id])
    return cat


@_locked
def promote(r, cat_id: str, registry_id: str) -> Tuple[dict, str]:
    """Deck category -> global. Returns (new category, old id); the caller retags this deck's members."""
    if not cat_id.startswith("d-"):
        raise CategoryError("Only a deck category can be made global.", 400)
    cat, key, _ = find(r, cat_id, registry_id)
    if _name_clash(r, cat["name"], "global", None, None):
        raise CategoryError("A global category with that name already exists. Rename this one first.", 409, "duplicate")
    new = {k: v for k, v in cat.items() if k not in ("deck_id", "pillar_id")}
    new.update({"id": f"g-{_slug(cat['name'])}-{uuid.uuid4().hex[:6]}", "scope": "global", "pillar_id": "",
                "updated_at": _now()})
    _save_list(r, key, [c for c in _load_list(r, key) if c["id"] != cat_id])
    items = _load_list(r, GLOBAL_KEY)
    items.append(new)
    _save_list(r, GLOBAL_KEY, items)
    return new, cat_id


# -- membership -------------------------------------------------------------------------------

def _tag_names(entries: Optional[Iterable[dict]]) -> set:
    return {str(e.get("tag", "")).lower() for e in entries or []}


def member_names(card_tags: Mapping[str, list], cat_id: str, exclude: Iterable[str] = ()) -> List[str]:
    """Cards carrying the category's tag (core: the autotag function tag; custom: cat:<id>)."""
    want = (cat_id if cat_id in CORE_BY_ID else CAT_TAG_PREFIX + cat_id).lower()
    skip = set(exclude)
    return sorted(n for n, entries in card_tags.items() if n not in skip and want in _tag_names(entries))


def _front_face_is_land(card: Optional[dict]) -> bool:
    return bool(card) and "land" in ((card.get("type_line") or "").split("//")[0]).lower()


def deck_card_names(deck: dict) -> List[str]:
    cmdr = {n for n in deck.get("commanders") or [] if isinstance(n, str)}
    return sorted({c["name"] for c in deck.get("cards") or [] if isinstance(c, dict) and c.get("name")} - cmdr)


def deck_total(deck: dict) -> int:
    return sum(int(c.get("quantity") or 1) for c in deck.get("cards") or [] if isinstance(c, dict)) \
        + len([n for n in deck.get("commanders") or [] if n])


def flags_for(card: Optional[dict]) -> List[str]:
    if not card:
        return []
    out = []
    if card.get("game_changer"):
        out.append("game_changer")
    text = (card.get("oracle_text") or "").lower()
    if re.search(r"\btakes? an extra turn|\bextra turn after this one", text):
        out.append("extra_turns")
    if re.search(r"\bdestroy all lands|\beach player sacrifices [^.]*lands", text):
        out.append("mass_land_denial")
    if re.search(r"(don't|doesn't) untap during|\b(each player|your opponents|players) can't|\bcosts? \{\d+\} more to cast", text):
        out.append("stax")
    return out


def core_members(deck: dict, texts: Mapping[str, dict], card_tags: Mapping[str, list], cat_id: str) -> List[str]:
    cmdr = [n for n in deck.get("commanders") or [] if isinstance(n, str)]
    if cat_id == "lands":
        return sorted(n for n in deck_card_names(deck) if _front_face_is_land(texts.get(n)))
    return member_names(card_tags, cat_id, exclude=cmdr)


def commander_does(deck: dict, card_tags: Mapping[str, list], cat_id: str) -> bool:
    cmdr = [n for n in deck.get("commanders") or [] if isinstance(n, str)]
    ids = (cat_id,) if cat_id != "removal" else ("removal", "counterspell")
    return any(_tag_names(card_tags.get(n)) & set(ids) for n in cmdr)


def commander_mv(deck: dict, texts: Mapping[str, dict]) -> int:
    mvs = []
    for n in deck.get("commanders") or []:
        c = texts.get(n) or {}
        v = c.get("cmc", c.get("mana_value"))
        if isinstance(v, (int, float)):
            mvs.append(int(v))
    return max(mvs) if mvs else 0


def _status(count: int, target: Optional[int], floor: Optional[int]) -> str:
    if floor is not None and count < floor:
        return "low"
    if target is None:
        return ""
    return comp.delta_status(count - target)


def core_rows(deck: Optional[dict], texts: Mapping[str, dict], card_tags: Mapping[str, list],
              deck_tag_names: Iterable[str], template: Mapping[str, Any]) -> Tuple[List[dict], dict]:
    """Core rows + {interaction_distinct, built, cards, commander_mv}.

    Global scope (``deck`` None): template target + base floor, no counts. Deck scope: live counts, the
    deck's target override, modified floors and one status scale. Statuses are off below MIN_STATUS_DECK.
    """
    rows: List[dict] = []
    default = comp.DEFAULT_TEMPLATE
    meta: Dict[str, Any] = {"interaction_distinct": None, "built": None, "cards": None, "commander_mv": None}
    members: Dict[str, List[str]] = {}
    inter_union: set = set()
    mv, tokens_active = 0, False
    if deck is not None:
        total = deck_total(deck)
        mv = commander_mv(deck, texts)
        meta.update(cards=total, built=total >= MIN_STATUS_DECK, commander_mv=mv)
        tokens_active = "tokens" in {str(t).lower() for t in deck_tag_names}
        members = {c["id"]: core_members(deck, texts, card_tags, c["id"]) for c in CORE}
        for cid in INTERACTION_IDS:
            inter_union.update(members[cid])
        meta["interaction_distinct"] = len(inter_union)
    for c in CORE:
        row_key = c["template_row"]
        template_target = int(template.get(row_key, default[row_key])) if row_key else None
        default_target = int(default[row_key]) if row_key else None
        row = {"id": c["id"], "scope": "core", "group": c["group"], "name": c["name"], "description": c["description"],
               "source": c["source"], "shared": c["shared"], "floor_note": c["floor_note"],
               "target": template_target, "default_target": default_target,
               "overridden": bool(row_key) and template_target != default_target,
               "editable_target": bool(row_key) and not c["shared"] and deck is not None,
               "floor": c["floor"], "count": None, "gc": 0, "commander_does_this": False, "status": ""}
        if deck is not None:
            mem = members[c["id"]]
            row["count"] = len(mem)
            row["gc"] = sum(1 for n in mem if "game_changer" in flags_for(texts.get(n))) if c["id"] == "tutor" else 0
            does = commander_does(deck, card_tags, c["id"])
            row["commander_does_this"] = does
            floor = c["floor"]
            if floor is not None:
                if c["id"] == "ramp" and mv >= 5:
                    floor += 2
                if does:
                    floor -= 3
                if c["id"] == "board_wipe" and tokens_active:
                    floor = 0
                floor = max(0, floor)
            row["floor"] = floor
            if c["shared"]:
                row["target"], row["floor"] = None, None
            elif meta["built"]:
                # The removal floor/target cover Removal + Counterspells together (distinct cards).
                count = len(set(members["removal"]) | set(members["counterspell"])) if c["id"] == "removal" \
                    else row["count"]
                row["status"] = _status(count, row["target"], floor)
        rows.append(row)
    return rows, meta


# -- suggestions (rule -> cards; never auto-added) --------------------------------------------

def _subtypes(card: dict) -> List[str]:
    tl = (card.get("type_line") or "").split("//")[0]
    return [s.lower() for s in re.split(r"\s+", tl.split("—")[1].strip())] if "—" in tl else []


def suggest_members(deck: dict, texts: Mapping[str, dict], card_tags: Mapping[str, list], cat: dict) -> List[str]:
    rule = cat.get("rule") or ""
    if not rule:
        return []
    kind, _, value = rule.partition(":")
    have = set(member_names(card_tags, cat["id"]))
    out = []
    for n in deck_card_names(deck):
        if n in have:
            continue
        card = texts.get(n) or {}
        tl = (card.get("type_line") or "").lower()
        if kind == "autotag":
            hit = value in _tag_names(card_tags.get(n))
        elif kind == "kindred":
            hit = value.lower() in _subtypes(card) or "changeling" in [k.lower() for k in card.get("keywords") or []]
        else:
            hit = value.lower() in tl
        if hit:
            out.append(n)
    return out


def theme_suggestions(deck_tags: Iterable[str], deck_cats: Iterable[dict]) -> List[dict]:
    """One-click suggestions from the deck's auto-tag themes, minus those a deck category already uses."""
    used = {c.get("rule") for c in deck_cats}
    out, seen = [], set()
    for t in deck_tags:
        t = str(t)
        if t in THEME_TAGS:
            theme, rule = t, f"autotag:{t}"
        elif t.startswith("kindred:") and t[8:]:
            theme, rule = t, f"kindred:{t[8:].title()}"
        else:
            continue
        if rule in used or rule in seen:
            continue
        seen.add(rule)
        out.append({"theme": theme, "rule": rule, "name": theme.split(":")[-1].replace("_", " ").title()})
    return out


# -- custom rows ------------------------------------------------------------------------------

def custom_row(cat: dict, card_tags: Optional[Mapping[str, list]] = None, deck: Optional[dict] = None,
               pillar_names: Optional[Mapping[str, str]] = None, decks_using: Optional[int] = None) -> dict:
    row = {"id": cat["id"], "scope": cat["scope"], "group": cat.get("group") or "", "name": cat["name"],
           "description": cat.get("description", ""), "rule": cat.get("rule", ""),
           "pillar_id": cat.get("pillar_id", ""), "deck_id": cat.get("deck_id"),
           "pillar": (pillar_names or {}).get(cat.get("pillar_id", ""), ""), "updated_at": cat.get("updated_at"),
           "count": None, "decks": decks_using}
    if deck is not None and card_tags is not None:
        row["count"] = len(member_names(card_tags, cat["id"], exclude=deck.get("commanders") or []))
    return row
