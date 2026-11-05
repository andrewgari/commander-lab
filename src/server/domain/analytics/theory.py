"""
TH-2 Theory: thematic overhaul docs written by oracle (docs/THEORY_SPEC.md, docs/THEORY.md).

Flow: build_request() -> oracle (skill `deck-theory`) -> parse_reply() -> TheoryDoc
(pydantic) -> verify() -> store(). Theory never touches the decklist or the Journal.

Doc (schema_version "1"):
    {schema_version, doc_id, deck_id, theme, commander_mode, created_at,
     sections: [{type, title, data}], verification: {...}}
Known v1 section types: thesis_pillars, decklist, cuts, buy_total, commander_change.
Any other type is kept as-is (extensibility: old docs never break, new types need no
migration).

Verification never trusts oracle on facts the app can check:
    - every card resolves on Scryfall, is Commander-legal and inside the (new)
      commander's colour identity; otherwise it is dropped;
    - source is recomputed: in this deck -> kept; a free owned copy (not in any deck)
      -> owned; anything else -> buy (cards sleeved in other decks are never pulled);
      basic lands are always kept/owned;
    - prices come from Scryfall USD (analytics.prices); buys over the hard cap or with
      no price are dropped; buys over the soft cap are flagged;
    - cuts = this deck's cards missing from the new list; buy_total = sum of buy prices;
    - commander_change only exists when the commander really changed (reuse count
      recomputed); pets made illegal by a swap are called out there;
    - every fix is recorded in doc.verification.corrections.

Redis:
    theory:{deck_id}:{doc_id}   doc JSON (deck_id = registry_id)
    theory_index:{deck_id}      sorted set, member doc_id, score created_at (epoch)
    theory_job:{job_id}         job state JSON, 7-day TTL
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import journal as journal_mod
from . import mech_similar as mech_similar_mod

log = logging.getLogger("commander_lab.theory")

SCHEMA_VERSION = "1"
DOC_PREFIX = "theory:"
INDEX_PREFIX = "theory_index:"
JOB_PREFIX = "theory_job:"
JOB_TTL_S = 7 * 24 * 3600
THEME_MAX = 200
KNOWN_TYPES = ("thesis_pillars", "decklist", "cuts", "buy_total", "commander_change")
SOURCES = ("kept", "owned", "buy")
COMMANDER_MODES = ("keep", "open")
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains",
          "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest",
          "Snow-Covered Wastes"}
# Cards that may appear more than once in a Commander deck besides basics.
ANY_NUMBER = {"Relentless Rats", "Rat Colony", "Persistent Petitioners", "Shadowborn Apostle",
              "Dragon's Approach", "Slime Against Humanity", "Templar Knight", "Hare Apparent",
              "Tempest Hawk", "Cid, Timeless Artificer"}
# Bounded exceptions: "up to N".
COPY_LIMITS = {"Seven Dwarves": 7, "Nazgûl": 9}
# In-flight generation limits (single app process): one job per deck, MAX_INFLIGHT overall.
MAX_INFLIGHT = 2
_INFLIGHT: Dict[str, str] = {}  # deck_id -> job_id
_INFLIGHT_LOCK = threading.Lock()

SKILL_NAME = "deck-theory"
SYSTEM_PROMPT = (
    "Commander Lab request. Load and follow your `deck-theory` skill exactly "
    "(skill_view name='deck-theory'). Reply with prose of at most 15 lines, then exactly one "
    "fenced ```json block matching theory.schema.json. Nothing after it."
)


class TheoryError(ValueError):
    pass


class TheoryParseError(TheoryError):
    pass


class TheoryBusy(TheoryError):
    """A theory is already being written for this deck, or too many are in flight."""

    def __init__(self, message: str, per_deck: bool):
        self.per_deck = per_deck
        super().__init__(message)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TheorySection(BaseModel):
    """One section. `type` is open: unknown types are preserved verbatim."""
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    title: str = ""
    data: Any = None


class CardEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    qty: int = 1
    source: Optional[str] = None
    price: Optional[float] = None


class DecklistGroup(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = "Other"
    cards: List[CardEntry] = Field(default_factory=list)


class DecklistData(BaseModel):
    model_config = ConfigDict(extra="allow")

    groups: List[DecklistGroup] = Field(default_factory=list)


class TheoryDoc(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    doc_id: Optional[str] = None
    deck_id: str
    theme: str = ""
    commander_mode: Literal["keep", "open"] = "keep"
    created_at: str
    sections: List[TheorySection] = Field(default_factory=list)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _v(cls, v):
        v = SCHEMA_VERSION if v is None else str(v)
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v!r} (this app reads {SCHEMA_VERSION!r})")
        return v

    def section(self, type_: str) -> Optional[TheorySection]:
        return next((s for s in self.sections if s.type == type_), None)


def validate_doc(raw: Mapping) -> TheoryDoc:
    try:
        return TheoryDoc.model_validate(raw)
    except ValidationError as exc:
        raise TheoryParseError(f"theory doc failed schema validation: {exc.errors()[:3]}") from exc


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rid(deck: Mapping) -> str:
    return journal_mod._registry_id(deck)


def _deck_colors(deck: Mapping) -> str:
    c = deck.get("color") or ""
    return "" if c == "C" else c


def _owned(r) -> Dict[str, dict]:
    from .interactions import combos as combos_mod

    return combos_mod.owned_index(r)


def name_key(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


def free_agents(owned: Mapping[str, dict], deck: Mapping) -> List[str]:
    """Owned cards with a free copy (not sleeved in any deck) that aren't in this deck."""
    in_deck = {name_key(n) for n in journal_mod.deck_entries(deck)} | {name_key(n) for n in deck.get("commanders") or []}
    out = []
    for k, v in owned.items():
        if (v.get("available") or 0) <= 0 or k in in_deck:
            continue
        out.append(v["name"])
    return sorted(out)


def _filter_identity(r, names: List[str], colors: str) -> List[str]:
    """Keep names whose cached card_meta colour fits `colors` (unknown meta is kept)."""
    if not names:
        return []
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    raws = []
    for i in range(0, len(names), 500):
        raws.extend(mget([f"card_meta:{n}" for n in names[i:i + 500]]))
    allowed = set(colors) - {"C"}
    out = []
    for n, raw in zip(names, raws):
        try:
            m = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            m = None
        if m and not (set(m.get("color") or "") - {"C"}) <= allowed:
            continue
        out.append(n)
    return out


def build_request(r, deck: Mapping, theme: str, commander_mode: str, owned: Optional[Mapping] = None) -> dict:
    """The input payload the deck-theory skill expects."""
    from . import identity

    theme = (theme or "").strip()
    if not theme:
        raise TheoryError("theme is required")
    if len(theme) > THEME_MAX:
        raise TheoryError(f"theme is at most {THEME_MAX} characters")
    if commander_mode not in COMMANDER_MODES:
        raise TheoryError(f"commander_mode must be one of {list(COMMANDER_MODES)}")
    owned = _owned(r) if owned is None else owned
    if not journal_mod.deck_entries(deck):
        raise TheoryError("this deck has no decklist synced yet; sync it first")
    rec = journal_mod.public(journal_mod.load_journal(r, deck))
    caps = journal_mod.effective_caps(r, rec)
    ident = identity.load_identity(r, dict(deck)) or {}
    agents = free_agents(owned, deck)
    if commander_mode == "keep":
        agents = _filter_identity(r, agents, _deck_colors(deck))
    commanders = list(deck.get("commanders") or [])
    decklist = [{"name": n, "qty": q} for n, q in sorted(journal_mod.deck_entries(deck).items())]
    pets = list(rec.get("pet_cards") or [])
    mech = mech_similar_mod.oracle_feed(
        list(commanders) + pets + [d["name"] for d in decklist], [d["name"] for d in decklist])
    return {
        "schema_version": SCHEMA_VERSION,
        "deck_id": _rid(deck),
        "deck_name": deck.get("name"),
        "theme": theme,
        "commander_mode": commander_mode,
        "commanders": commanders,
        "color_identity": _deck_colors(deck) or "C",
        "decklist": decklist,
        "journal": {
            "thesis": ((ident.get("thesis") or {}).get("text")) or ((rec.get("deck_vision") or {}).get("description")) or "",
            "pillars": [{"name": p.get("name"), "description": p.get("description")}
                        for p in identity.live_pillars(ident)],
            "pet_cards": pets,
        },
        "mechanically_similar": mech,
        "free_agent_owned": agents,
        "caps": {"no_budget": bool(caps.get("no_budget")), "hard_cap": caps.get("hard_cap"),
                 "soft_cap": caps.get("soft_cap"), "currency": "USD"},
    }


def request_prompt(req: Mapping) -> str:
    return ("deck-theory request (JSON). Build the overhaul doc for this input.\n\n```json\n"
            + json.dumps(req, ensure_ascii=False, indent=1) + "\n```")


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def parse_reply(text: str) -> dict:
    """The last fenced json block (or the whole reply if it is bare JSON)."""
    if not isinstance(text, str) or not text.strip():
        raise TheoryParseError("oracle returned an empty reply")
    blocks = _FENCE.findall(text)
    candidates = list(reversed(blocks)) + [text.strip()]
    for c in candidates:
        try:
            v = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            return v
    raise TheoryParseError("oracle reply has no parseable ```json block")


# ---------------------------------------------------------------------------
# Scryfall facts
# ---------------------------------------------------------------------------

Lookup = Callable[[List[str]], Mapping[str, dict]]
PriceFetch = Callable[[List[str]], Mapping[str, Optional[float]]]


def can_command(type_line: str, oracle_text: str) -> bool:
    """May this card be a commander? Legendary creature, a Background, or 'can be your commander'."""
    t = type_line or ""
    front = t.split(" // ")[0]
    if "Legendary" in front and "Creature" in front:
        return True
    if "Legendary" in front and "Background" in front:
        return True
    return "can be your commander" in (oracle_text or "")


def scryfall_lookup(names: List[str], session=None, timeout: float = 30.0) -> Dict[str, dict]:
    """{requested name: {name, color_identity, legal, commander_ok}} via /cards/collection.
    Unknown names are absent."""
    import requests

    http = session or requests.Session()
    out: Dict[str, dict] = {}
    headers = {"User-Agent": "commander-lab/1.0 (theory)", "Accept": "application/json"}
    for i in range(0, len(names), 75):
        chunk = names[i:i + 75]
        resp = None
        for attempt in range(4):
            resp = http.post("https://api.scryfall.com/cards/collection", headers=headers, timeout=timeout,
                             json={"identifiers": [{"name": n.split(" // ")[0]} for n in chunk]})
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            break
        assert resp is not None
        resp.raise_for_status()
        by_key: Dict[str, dict] = {}
        for c in resp.json().get("data", []):
            faces = c.get("card_faces") or []
            text = c.get("oracle_text") or " ".join(f.get("oracle_text", "") for f in faces)
            card = {"name": c.get("name", ""), "color_identity": "".join(c.get("color_identity") or []),
                    "legal": (c.get("legalities") or {}).get("commander") == "legal",
                    "commander_ok": can_command(c.get("type_line") or "", text)}
            by_key[c.get("name", "").lower()] = card
            for face in c.get("name", "").split(" // "):
                by_key.setdefault(face.lower(), card)
        for n in chunk:
            card = by_key.get(n.lower()) or by_key.get(n.split(" // ")[0].lower())
            if card:
                out[n] = card
        time.sleep(0.1)
    return out


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _ci_ok(card_ci: str, allowed: set) -> bool:
    return set(card_ci or "") - {"C"} <= allowed


def _money(v: float) -> float:
    return round(float(v) + 1e-9, 2)


def _section_dict(s: TheorySection) -> dict:
    return s.model_dump()


def verify(doc: TheoryDoc, deck: Mapping, *, owned: Mapping[str, dict], caps: Mapping,
           pets: Iterable[str] = (), lookup: Lookup, prices: Callable[[List[str]], Mapping[str, Optional[float]]],
           now: Optional[str] = None) -> dict:
    """Check and correct oracle's doc. Returns the stored doc dict (with `verification`)."""
    corrections: List[dict] = []

    def fix(kind: str, message: str, card: Optional[str] = None, **extra):
        c = {"kind": kind, "message": message}
        if card:
            c["card"] = card
        c.update(extra)
        corrections.append(c)

    deck_cards = journal_mod.deck_entries(deck)
    deck_keys = {name_key(n) for n in deck_cards}
    old_cmdrs = list(deck.get("commanders") or [])
    deck_keys |= {name_key(n) for n in old_cmdrs}
    pets = [p for p in pets if p]
    pet_keys = {name_key(p) for p in pets}
    mode = doc.commander_mode

    sections: List[TheorySection] = list(doc.sections)
    dl_sec = next((s for s in sections if s.type == "decklist"), None)
    cc_sec = next((s for s in sections if s.type == "commander_change"), None)

    # --- commander ---------------------------------------------------------
    new_cmdrs = list(old_cmdrs)
    if cc_sec is not None:
        data = cc_sec.data if isinstance(cc_sec.data, dict) else {}
        proposed = data.get("new_commanders") or data.get("to") or []
        if isinstance(proposed, str):
            proposed = [proposed]
        proposed = [p for p in proposed if isinstance(p, str) and p.strip()]
        if mode == "keep":
            fix("commander_kept", "Commander mode is Keep; oracle's commander swap was removed.",
                proposed=proposed)
            sections.remove(cc_sec)
            cc_sec = None
        elif not proposed or {name_key(p) for p in proposed} == {name_key(c) for c in old_cmdrs}:
            if proposed:
                fix("commander_unchanged", "commander_change named the current commander; section removed.")
            else:
                fix("commander_change_invalid", "commander_change had no new commander; section removed.")
            sections.remove(cc_sec)
            cc_sec = None
        else:
            new_cmdrs = proposed

    # --- decklist ----------------------------------------------------------
    if dl_sec is None:
        dl_data = DecklistData()
        fix("decklist_missing", "oracle sent no decklist section.")
    else:
        try:
            dl_data = DecklistData.model_validate(dl_sec.data or {})
        except ValidationError as exc:
            raise TheoryParseError(f"decklist section is malformed: {exc.errors()[:3]}") from exc

    entries = []  # (group_idx, CardEntry)
    for gi, g in enumerate(dl_data.groups):
        for c in g.cards:
            entries.append((gi, c))
    all_names = sorted({c.name for _, c in entries} | set(new_cmdrs) | set(pets))
    facts = dict(lookup(all_names) or {})

    # Commander: a swap must resolve, be Commander-legal, be able to command, and (when it
    # has to be bought) be priced and under the hard cap. Otherwise the swap is reverted
    # and the list is verified under the current commander's identity.
    no_budget = bool(caps.get("no_budget"))
    hard, soft = caps.get("hard_cap"), caps.get("soft_cap")
    if cc_sec is not None:
        cmd_prices = dict(prices(list(new_cmdrs)) or {})
        bad = None
        for cmd in new_cmdrs:
            f = facts.get(cmd)
            k = name_key(cmd)
            o = owned.get(k)
            is_buy = k not in deck_keys and not (o and (o.get("available") or 0) > 0)
            p = cmd_prices.get(cmd)
            if not f:
                bad = ("commander_unknown", f"New commander '{cmd}' does not resolve on Scryfall")
            elif f.get("legal") is not True:
                bad = ("commander_illegal", f"New commander '{cmd}' is not legal in Commander")
            elif not f.get("commander_ok", True):
                bad = ("commander_ineligible", f"'{cmd}' can't be a commander")
            elif is_buy and p is None:
                bad = ("commander_unpriced", f"New commander '{cmd}' has no Scryfall USD price")
            elif is_buy and not no_budget and hard is not None and p is not None and p > float(hard):
                bad = ("commander_hard_cap", f"New commander '{cmd}' costs ${p:.2f}, over the hard cap "
                                             f"${float(hard):.2f}")
            if bad:
                fix(bad[0], bad[1] + "; swap reverted, list checked against the current commander.", card=cmd)
                new_cmdrs = list(old_cmdrs)
                sections.remove(cc_sec)
                cc_sec = None
                missing = [c for c in old_cmdrs if c not in facts]
                if missing:
                    facts.update(lookup(missing) or {})
                break
    allowed: set = set()
    for cmd in new_cmdrs:
        f = facts.get(cmd)
        if f:
            allowed |= set(f.get("color_identity") or "")
    if not any(facts.get(c) for c in new_cmdrs):
        allowed = set(_deck_colors(deck) if new_cmdrs == old_cmdrs else "")
    allowed -= {"C"}
    cmdr_keys = {name_key(c) for c in new_cmdrs}

    kept: List[tuple] = []
    seen: Dict[str, int] = {}
    for gi, c in entries:
        f = facts.get(c.name)
        if not f:
            fix("unknown_card", f"'{c.name}' does not resolve on Scryfall; dropped.", card=c.name)
            continue
        canon = f.get("name") or c.name
        if canon != c.name:
            c.name = canon
        k = name_key(canon)
        if k in cmdr_keys:
            if f.get("legal") is not True:  # only the current commander can get here (swaps are pre-checked)
                fix("commander_illegal", f"Commander '{canon}' is not legal in Commander.", card=canon)
        elif f.get("legal") is not True:
            fix("illegal", f"'{canon}' is not legal in Commander; dropped.", card=canon)
            continue
        elif not _ci_ok(f.get("color_identity") or "", allowed):
            fix("color_identity", f"'{canon}' ({f.get('color_identity') or 'C'}) is outside the commander's colour "
                                  f"identity ({''.join(c for c in 'WUBRG' if c in allowed) or 'C'}); dropped.", card=canon)
            continue
        qty = max(1, int(c.qty or 1))
        if canon not in BASICS and canon not in ANY_NUMBER:
            limit = COPY_LIMITS.get(canon, 1)
            room = limit - seen.get(k, 0)
            if room <= 0:
                fix("duplicate", f"'{canon}' is over its {limit}-copy limit; extra copies dropped.", card=canon)
                continue
            if qty > room:
                fix("duplicate", f"'{canon}' x{qty} is over its {limit}-copy limit; cut to {room}.", card=canon)
                qty = room
        seen[k] = seen.get(k, 0) + qty
        c.qty = qty
        kept.append((gi, c))

    # Commanders must be in the list.
    for cmd in new_cmdrs:
        if name_key(cmd) not in seen:
            f = facts.get(cmd) or {"name": cmd}
            groups = dl_data.groups
            gi = next((i for i, g in enumerate(groups) if g.role.lower() in ("commander", "commanders")), None)
            if gi is None:
                groups.insert(0, DecklistGroup(role="Commander", cards=[]))
                kept = [(i + 1, c) for i, c in kept]
                gi = 0
            kept.insert(0, (gi, CardEntry(name=f.get("name") or cmd)))
            seen[name_key(cmd)] = 1
            fix("commander_added", f"Commander '{f.get('name') or cmd}' was missing from the list; added.", card=cmd)

    # --- sources + prices --------------------------------------------------
    names = sorted({c.name for _, c in kept})
    price_of = dict(prices(names) or {})
    final: List[tuple] = []
    for gi, c in kept:
        k = name_key(c.name)
        claimed = c.source
        o = owned.get(k)
        if k in deck_keys:
            src = "kept"
        elif c.name in BASICS:
            src = "owned"
        elif o and (o.get("available") or 0) > 0:
            src = "owned"
        else:
            src = "buy"
        if claimed != src:
            extra = {}
            if src == "buy" and o and o.get("decks"):
                extra["note"] = "owned but sleeved in " + ", ".join(o["decks"][:3]) + "; other decks are never used"
            fix("source", f"'{c.name}' is {src}, not {claimed or 'untagged'}"
                + (f" ({extra['note']})" if extra else "") + ".", card=c.name, claimed=claimed, actual=src)
        c.source = src
        p = price_of.get(c.name)
        claimed_price = c.price
        c.price = _money(p) if p is not None else None
        if src == "buy":
            if p is None:
                fix("unpriced_buy", f"'{c.name}' has no Scryfall USD price, so it can't be bought; dropped.", card=c.name)
                continue
            if not no_budget and hard is not None and p > float(hard):
                fix("hard_cap", f"'{c.name}' costs ${p:.2f}, over the hard cap ${float(hard):.2f}; dropped.", card=c.name,
                    price=_money(p))
                continue
            if claimed_price is not None and abs(float(claimed_price) - p) >= 0.01:
                fix("price", f"'{c.name}' is ${p:.2f} on Scryfall, not ${float(claimed_price):.2f}.", card=c.name)
            extra_fields = c.model_extra or {}
            if not no_budget and soft is not None and p > float(soft):
                setattr(c, "over_soft_cap", True)
            elif extra_fields.get("over_soft_cap"):
                setattr(c, "over_soft_cap", False)
        final.append((gi, c))

    groups_out = []
    for gi, g in enumerate(dl_data.groups):
        cards = [c.model_dump(exclude_none=True) for i, c in final if i == gi]
        if cards:
            groups_out.append({**g.model_dump(exclude={"cards"}), "cards": cards})
    counts = {"kept": 0, "owned": 0, "buy": 0}
    for _, c in final:
        counts[c.source] += c.qty
    total = sum(counts.values())
    if total != 100:
        fix("card_count", f"The verified list has {total} cards, not 100.", total=total)
    dl_out = dict(dl_data.model_dump(exclude={"groups"}))
    dl_out.update(groups=groups_out, counts={**counts, "total": total})
    if dl_sec is None:
        dl_sec = TheorySection(type="decklist", title="Decklist", data=dl_out)
        sections.append(dl_sec)
    else:
        dl_sec.data = dl_out

    # --- pets --------------------------------------------------------------
    final_keys = {name_key(c.name) for _, c in final}
    pets_lost = []
    for p in pets:
        if name_key(p) in final_keys:
            continue
        f = facts.get(p)
        if f and (f.get("legal") is not True or not _ci_ok(f.get("color_identity") or "", allowed)):
            pets_lost.append(p)
            if cc_sec is None:
                fix("pet_illegal", f"Pet card '{p}' is not legal here.", card=p)
        else:
            fix("pet_missing", f"Pet card '{p}' was cut; pets are kept unless the commander swap makes them "
                               "illegal.", card=p)

    # --- cuts --------------------------------------------------------------
    cuts_sec = next((s for s in sections if s.type == "cuts"), None)
    reasons: Dict[str, Any] = {}
    claimed_cuts: List[str] = []
    cdata = cuts_sec.data if cuts_sec is not None else None
    items = cdata.get("cards") if isinstance(cdata, dict) else cdata
    for it in items or []:
        if isinstance(it, str):
            claimed_cuts.append(it)
        elif isinstance(it, dict) and it.get("name"):
            claimed_cuts.append(it["name"])
            reasons[name_key(it["name"])] = {k: v for k, v in it.items() if k not in ("name", "qty")}
    cut_rows = []
    for n, q in sorted(deck_cards.items()):
        k = name_key(n)
        if k in final_keys:
            have = sum(c.qty for _, c in final if name_key(c.name) == k)
            if have >= q:
                continue
            q = q - have
        row = {"name": n, "qty": q, **reasons.get(k, {})}
        if k in pet_keys:
            row["pet"] = True
        cut_rows.append(row)
    old_cmdr_keys = {name_key(n) for n in old_cmdrs}
    for row in cut_rows:
        if name_key(row["name"]) in old_cmdr_keys:
            row["commander"] = True
    present = {name_key(r_["name"]) for r_ in cut_rows}
    for n in old_cmdrs:  # decks whose cards[] don't carry the command zone
        if name_key(n) not in final_keys and name_key(n) not in present:
            cut_rows.append({"name": n, "qty": 1, "commander": True, **reasons.get(name_key(n), {})})
    cut_rows.sort(key=lambda r_: (not r_.get("commander"), r_["name"]))
    actual_keys = {name_key(c["name"]) for c in cut_rows}
    for n in claimed_cuts:
        if name_key(n) not in actual_keys:
            if name_key(n) in deck_keys:
                fix("cut", f"'{n}' was listed as a cut but is still in the new list.", card=n)
            else:
                fix("cut", f"'{n}' was listed as a cut but isn't in this deck.", card=n)
    for c in cut_rows:
        if name_key(c["name"]) not in {name_key(x) for x in claimed_cuts} and claimed_cuts:
            fix("cut", f"'{c['name']}' is cut but oracle didn't list it.", card=c["name"])
    cuts_data = {"cards": cut_rows, "count": sum(c["qty"] for c in cut_rows)}
    if isinstance(cdata, dict):
        cuts_data = {**{k: v for k, v in cdata.items() if k not in ("cards", "count")}, **cuts_data}
    if cuts_sec is None:
        sections.append(TheorySection(type="cuts", title="Cuts", data=cuts_data))
    else:
        cuts_sec.data = cuts_data

    # --- buy_total ---------------------------------------------------------
    buys = [(c.name, c.price, c.qty) for _, c in final if c.source == "buy"]
    total_usd = _money(sum(p * q for _, p, q in buys)) if buys else 0.0
    bt_sec = next((s for s in sections if s.type == "buy_total"), None)
    bdata = bt_sec.data if bt_sec is not None and isinstance(bt_sec.data, dict) else {}
    claimed_total = bdata.get("total_usd", bdata.get("total"))
    if claimed_total is not None:
        try:
            if abs(float(claimed_total) - total_usd) >= 0.01:
                fix("buy_total", f"Buy total is ${total_usd:.2f} (Scryfall USD), not ${float(claimed_total):.2f}.")
        except (TypeError, ValueError):
            fix("buy_total", f"Buy total is ${total_usd:.2f}; oracle's value was not a number.")
    bt_data = {**{k: v for k, v in bdata.items() if k not in ("total", "total_usd", "count", "cards")},
               "total_usd": total_usd, "count": sum(q for _, _, q in buys), "currency": "USD", "source": "scryfall",
               "cards": [{"name": n, "price": p, "qty": q} for n, p, q in sorted(buys, key=lambda x: -x[1] * x[2])],
               "caps": {"no_budget": no_budget, "hard_cap": hard, "soft_cap": soft}}
    if bt_sec is None:
        sections.append(TheorySection(type="buy_total", title="Buy total", data=bt_data))
    else:
        bt_sec.data = bt_data

    # --- commander_change --------------------------------------------------
    if cc_sec is not None:
        data = dict(cc_sec.data) if isinstance(cc_sec.data, dict) else {}
        reuse = counts["kept"] - sum(1 for _, c in final if name_key(c.name) in cmdr_keys and c.source == "kept")
        claimed_reuse = data.get("reuse_count")
        if claimed_reuse is not None and claimed_reuse != reuse:
            fix("reuse_count", f"{reuse} cards are reused from the current deck, not {claimed_reuse}.")
        data.update(old_commanders=old_cmdrs, new_commanders=[(facts.get(c) or {}).get("name") or c for c in new_cmdrs],
                    reuse_count=reuse, pets_lost=pets_lost)
        data.setdefault("reason", "")
        data.pop("to", None)
        cc_sec.data = data

    # Oracle's section order is kept (unknown types keep their slot); sections the app
    # had to create are appended.
    out = doc.model_dump()
    out["sections"] = [_section_dict(s) for s in sections]
    out["verification"] = {
        "checked_at": now or _now_iso(),
        "corrections": corrections,
        "counts": {**counts, "total": total},
        "buy_total_usd": total_usd,
        "commander_changed": cc_sec is not None,
        "commanders": [(facts.get(c) or {}).get("name") or c for c in new_cmdrs],
    }
    return out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def doc_key(deck_id: str, doc_id: str) -> str:
    return f"{DOC_PREFIX}{deck_id}:{doc_id}"


def index_key(deck_id: str) -> str:
    return f"{INDEX_PREFIX}{deck_id}"


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


def store(r, doc: dict) -> dict:
    deck_id, doc_id = doc["deck_id"], doc["doc_id"]
    r.set(doc_key(deck_id, doc_id), json.dumps(doc))
    r.zadd(index_key(deck_id), {doc_id: _epoch(doc["created_at"])})
    return doc


def load(r, deck_id: str, doc_id: str) -> Optional[dict]:
    raw = r.get(doc_key(deck_id, doc_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def delete(r, deck_id: str, doc_id: str) -> bool:
    n = r.delete(doc_key(deck_id, doc_id))
    m = r.zrem(index_key(deck_id), doc_id)
    return bool(n or m)


def _buy_cards(doc: Mapping) -> list:
    """[{name, qty}] from the app-verified buy_total section (the list behind the BUY count)."""
    for s in doc.get("sections") or []:
        if isinstance(s, Mapping) and s.get("type") == "buy_total":
            data = s.get("data") if isinstance(s.get("data"), Mapping) else {}
            return [{"name": c["name"], "qty": c.get("qty") or 1}
                    for c in data.get("cards") or [] if isinstance(c, Mapping) and c.get("name")]
    return []


def summary(doc: Mapping) -> dict:
    v = doc.get("verification") or {}
    return {"doc_id": doc.get("doc_id"), "deck_id": doc.get("deck_id"), "theme": doc.get("theme"),
            "commander_mode": doc.get("commander_mode"), "created_at": doc.get("created_at"),
            "commander_changed": bool(v.get("commander_changed")), "commanders": v.get("commanders") or [],
            "buy_total_usd": v.get("buy_total_usd"), "counts": v.get("counts") or {},
            "buy_cards": _buy_cards(doc),
            "corrections": len(v.get("corrections") or []),
            "section_types": [s.get("type") for s in doc.get("sections") or []]}


def list_docs(r, deck_id: str) -> List[dict]:
    ids = r.zrevrange(index_key(deck_id), 0, -1)
    out = []
    for doc_id in ids:
        doc = load(r, deck_id, doc_id)
        if doc is None:
            r.zrem(index_key(deck_id), doc_id)  # self-heal a dangling index entry
            continue
        out.append(summary(doc))
    return out


# ---------------------------------------------------------------------------
# Generation + jobs
# ---------------------------------------------------------------------------


class PricesUnavailable(TheoryError):
    """Scryfall prices could not be checked, so buys can't be verified."""


def strict_prices(r, names: List[str], fetch: Optional[PriceFetch] = None) -> Dict[str, Optional[float]]:
    """analytics.prices.card_prices, but a failed Scryfall fetch is an error instead of
    silently looking like "unpriced" (which would drop every buy)."""
    from . import prices as prices_mod

    failed: List[BaseException] = []
    base = fetch or prices_mod.scryfall_fetch

    def guarded(stale):
        try:
            return base(stale)
        except Exception as exc:  # noqa: BLE001
            failed.append(exc)
            raise

    out = prices_mod.card_prices(r, names, fetch=guarded)
    if failed and any(out.get(n) is None for n in names):
        raise PricesUnavailable(f"Scryfall prices unavailable ({type(failed[0]).__name__}); try again later")
    return out


def generate(r, deck: Mapping, theme: str, commander_mode: str, client, *, lookup: Optional[Lookup] = None,
             fetch_prices: Optional[PriceFetch] = None, now: Optional[str] = None,
             doc_id: Optional[str] = None) -> dict:
    """Ask oracle, validate, verify, store. Returns the stored doc."""
    from . import insights

    owned = _owned(r)
    req = build_request(r, deck, theme, commander_mode, owned=owned)
    reply = client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": request_prompt(req)}])
    raw = parse_reply(reply)
    now = now or _now_iso()
    # The app owns identity fields; oracle's values are ignored.
    raw.update(deck_id=req["deck_id"], theme=req["theme"], commander_mode=commander_mode, created_at=now,
               doc_id=doc_id or uuid.uuid4().hex[:12])
    raw.setdefault("schema_version", SCHEMA_VERSION)
    doc = validate_doc(raw)
    rec = journal_mod.public(journal_mod.load_journal(r, deck))
    caps = journal_mod.effective_caps(r, rec)
    out = verify(doc, deck, owned=owned, caps=caps, pets=insights.pet_cards(r, dict(deck)),
                 lookup=lookup or scryfall_lookup,
                 prices=lambda names: strict_prices(r, names, fetch=fetch_prices), now=now)
    return store(r, out)


def job_key(job_id: str) -> str:
    return JOB_PREFIX + job_id


def _save_job(r, job: dict) -> dict:
    job["updated_at"] = _now_iso()
    r.set(job_key(job["job_id"]), json.dumps(job), ex=JOB_TTL_S)
    return job


def load_job(r, job_id: str) -> Optional[dict]:
    raw = r.get(job_key(job_id))
    try:
        return json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        return None


def run_job(r, job: dict, deck: Mapping, client_factory: Callable[[], Any], **kw) -> dict:
    from .oracle_client import OFFLINE_MESSAGE, OracleError, OracleUnavailable

    job.update(status="running", started_at=_now_iso())
    _save_job(r, job)
    try:
        doc = generate(r, deck, job["theme"], job["commander_mode"], client_factory(), doc_id=job["doc_id"], **kw)
        job.update(status="done", doc_id=doc["doc_id"], summary=summary(doc))
    except OracleUnavailable as exc:
        job.update(status="failed", error=OFFLINE_MESSAGE, error_kind="oracle_offline", detail=exc.detail)
    except (OracleError, TheoryError) as exc:
        job.update(status="failed", error=str(exc)[:500], error_kind=type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - a job must always end in a state
        log.exception("theory job %s failed", job["job_id"])
        job.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", error_kind="internal")
    finally:
        release(job["deck_id"], job["job_id"])
    job["finished_at"] = _now_iso()
    return _save_job(r, job)


def reserve(deck_id: str, job_id: str) -> None:
    """Claim the deck's generation slot (one per deck, MAX_INFLIGHT overall) or raise TheoryBusy."""
    with _INFLIGHT_LOCK:
        if deck_id in _INFLIGHT:
            raise TheoryBusy(f"a theory is already being written for this deck (job {_INFLIGHT[deck_id]})",
                             per_deck=True)
        if len(_INFLIGHT) >= MAX_INFLIGHT:
            raise TheoryBusy(f"{MAX_INFLIGHT} theories are already being written; try again in a few minutes",
                             per_deck=False)
        _INFLIGHT[deck_id] = job_id


def release(deck_id: str, job_id: Optional[str] = None) -> None:
    with _INFLIGHT_LOCK:
        if deck_id in _INFLIGHT and (job_id is None or _INFLIGHT[deck_id] == job_id):
            del _INFLIGHT[deck_id]


def new_job(r, deck: Mapping, theme: str, commander_mode: str) -> dict:
    """Validate, reserve the deck's slot, and save a queued job. The caller must start it
    (start_job / run_job release the slot) or call release()."""
    theme = (theme or "").strip()
    if not theme:
        raise TheoryError("theme is required")
    if len(theme) > THEME_MAX:
        raise TheoryError(f"theme is at most {THEME_MAX} characters")
    if commander_mode not in COMMANDER_MODES:
        raise TheoryError(f"commander_mode must be one of {list(COMMANDER_MODES)}")
    if not journal_mod.deck_entries(deck):
        raise TheoryError("this deck has no decklist synced yet; sync it first")
    job = {"job_id": uuid.uuid4().hex[:12], "deck_id": _rid(deck), "deck_name": deck.get("name"),
           "theme": theme, "commander_mode": commander_mode, "status": "queued", "created_at": _now_iso(),
           "doc_id": uuid.uuid4().hex[:12]}
    reserve(job["deck_id"], job["job_id"])
    try:
        return _save_job(r, job)
    except Exception:
        release(job["deck_id"], job["job_id"])
        raise


def start_job(r, job: dict, deck: Mapping, client_factory: Callable[[], Any], **kw) -> threading.Thread:
    t = threading.Thread(target=run_job, args=(r, job, dict(deck), client_factory), kwargs=kw,
                         name=f"theory-{job['job_id']}", daemon=True)
    t.start()
    return t
