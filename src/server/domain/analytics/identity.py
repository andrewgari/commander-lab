"""
Deck identity (AN-3): thesis + 3-5 pillars, drafted by the LLM, owned by
the user.

Storage: identity:deck:{registry_id} (JSON, no TTL)
    thesis   {text, emphasis, state: draft|pinned, updated_by: generator|user,
              updated_at, draft_text, draft_emphasis, draft_at}
    pillars  [{id, name, description, state, order, origin, updated_by,
               created_at, updated_at}]
             state:  draft     proposed by the generator, not yet reviewed
                     accepted  kept but unpinned: a later card may let re-sync rename/merge it
                     pinned    re-sync never touches it (Edit and Keep pin automatically)
                     rejected  never proposed again (matched by embedding similarity)
             origin: generated (first draft) | resync (proposed later) | user
    generator {provider, model, generated_at, cost_usd, cards_sig}
    proposal  {new, dropped[], at} from the last re-sync, or null
    cards_sig sha1 of the decklist the generator last saw

Contract (UX_SPEC §3.2):
- The first generation writes the thesis (draft) and the pillars (drafts).
- A re-sync never edits or removes existing pillars or the thesis text. It
  only ADDS draft pillars, up to 5 live (non-rejected) pillars. A proposal is
  dropped when it matches any existing pillar, live or rejected, by
  embedding similarity >= embedder.dup_sim (0.85) or by name. The new thesis
  draft is stored as thesis.draft_text, next to the user's text.
- Rebuilds regenerate a deck only when its decklist changed (cards_sig), at
  most `max_decks` decks per rebuild. Every call goes through the AN-1
  LLMChain (Gemini -> Nous) and is logged with purpose "pillars".
- The LLM only writes labels and prose. All scores are computed.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional

from .embeddings import Embedder

KEY_PREFIX = "identity:deck:"
MAX_PILLARS = 5
MIN_PILLARS = 3
NAME_MAX = 40
DESC_MAX = 400
THESIS_MAX = 300
LIVE = ("pinned", "accepted", "draft")
ACTIONS = ("pin", "unpin", "keep", "reject", "restore")
DEFAULT_MAX_DECKS_PER_REBUILD = 10


class IdentityError(ValueError):
    pass


class PillarNotFound(IdentityError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registry_id(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def identity_key(deck: dict) -> str:
    return KEY_PREFIX + _registry_id(deck)


def deck_names(deck: dict) -> List[str]:
    names = {c.get("name") for c in deck.get("cards") or [] if c.get("name")}
    names.update(n for n in deck.get("commanders") or [] if n)
    return sorted(names)


def cards_signature(deck: dict) -> str:
    return hashlib.sha1("\n".join(deck_names(deck)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def new_identity(deck: dict) -> dict:
    return {"deck": _registry_id(deck), "thesis": None, "pillars": [], "generator": None,
            "proposal": None, "cards_sig": None, "created_at": _now()}


def load_identity(r, deck: dict) -> Optional[dict]:
    raw = r.get(identity_key(deck))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_identity(r, deck: dict, ident: dict) -> dict:
    ident["updated_at"] = _now()
    r.set(identity_key(deck), json.dumps(ident))
    return ident


def _require(r, deck: dict) -> dict:
    ident = load_identity(r, deck)
    if ident is None:
        raise IdentityError("this deck has no identity yet; POST .../identity/generate or add a pillar")
    return ident


def live_pillars(ident: Optional[dict]) -> List[dict]:
    return sorted([p for p in (ident or {}).get("pillars") or [] if p.get("state") in LIVE],
                  key=lambda p: p.get("order") or 0)


def _next_order(ident: dict) -> int:
    return max([p.get("order") or 0 for p in live_pillars(ident)] or [0]) + 1


def find_pillar(r, deck: dict, pillar_id: str) -> dict:
    ident = _require(r, deck)
    for p in ident["pillars"]:
        if p["id"] == pillar_id:
            return p
    raise PillarNotFound(f"no pillar '{pillar_id}'")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _clean_name(name, required=True) -> str:
    if name is None and not required:
        return ""
    if not isinstance(name, str) or not name.strip():
        raise IdentityError("pillar name is required")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise IdentityError(f"pillar name is at most {NAME_MAX} characters")
    return name


def _clean_desc(desc) -> str:
    if desc is None:
        return ""
    if not isinstance(desc, str):
        raise IdentityError("description must be text")
    desc = " ".join(desc.split())
    if len(desc) > DESC_MAX:
        raise IdentityError(f"description is at most {DESC_MAX} characters")
    return desc


def _new_pillar(name: str, description: str, state: str, origin: str, order: int, by: str) -> dict:
    now = _now()
    return {"id": uuid.uuid4().hex[:10], "name": name, "description": description, "state": state,
            "order": order, "origin": origin, "updated_by": by, "created_at": now, "updated_at": now}


# ---------------------------------------------------------------------------
# User edits
# ---------------------------------------------------------------------------


def add_pillar(r, deck: dict, payload: Mapping, embedder: Optional[Embedder] = None) -> dict:
    ident = load_identity(r, deck) or new_identity(deck)
    if len(live_pillars(ident)) >= MAX_PILLARS:
        raise IdentityError(f"a deck has at most {MAX_PILLARS} pillars; reject one first")
    p = _new_pillar(_clean_name(payload.get("name")), _clean_desc(payload.get("description")),
                    "pinned", "user", _next_order(ident), "user")
    ident["pillars"].append(p)
    save_identity(r, deck, ident)
    return p


def update_pillar(r, deck: dict, pillar_id: str, payload: Mapping, embedder: Optional[Embedder] = None) -> dict:
    """payload: {"action": pin|unpin|keep|reject|restore} and/or {"name", "description"}.
    Any edit pins the pillar (UX_SPEC: edited and kept pillars are pinned)."""
    if not isinstance(payload, Mapping) or not payload:
        raise IdentityError("send an action or a name/description")
    ident = _require(r, deck)
    p = next((x for x in ident["pillars"] if x["id"] == pillar_id), None)
    if p is None:
        raise PillarNotFound(f"no pillar '{pillar_id}'")
    action = payload.get("action")
    if action is not None and action not in ACTIONS:
        raise IdentityError(f"unknown action '{action}'; use one of {', '.join(ACTIONS)}")
    edits = {}
    if "name" in payload:
        edits["name"] = _clean_name(payload.get("name"))
    if "description" in payload:
        edits["description"] = _clean_desc(payload.get("description"))
    if not edits and action is None:
        raise IdentityError("nothing to change")

    if action == "restore":
        if p["state"] != "rejected":
            raise IdentityError("only rejected pillars can be restored")
        if len(live_pillars(ident)) >= MAX_PILLARS:
            raise IdentityError(f"a deck has at most {MAX_PILLARS} pillars; reject one first")
        p["state"] = "accepted"
        p["order"] = _next_order(ident)
    elif action == "reject":
        p["state"] = "rejected"
    elif action in ("pin", "keep"):
        if p["state"] == "rejected":
            raise IdentityError("restore the pillar first")
        p["state"] = "pinned"
    elif action == "unpin":
        if p["state"] == "rejected":
            raise IdentityError("restore the pillar first")
        p["state"] = "accepted"

    if edits:
        if p["state"] == "rejected":
            raise IdentityError("restore the pillar before editing it")
        p.update(edits)
        p["state"] = "pinned"
    p["updated_by"] = "user"
    p["updated_at"] = _now()
    save_identity(r, deck, ident)
    return p


def reorder_pillars(r, deck: dict, ids: List[str]) -> List[dict]:
    ident = _require(r, deck)
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise IdentityError("send {\"order\": [pillar ids]}")
    by_id = {p["id"]: p for p in ident["pillars"]}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise PillarNotFound(f"no pillar '{unknown[0]}'")
    rest = [p["id"] for p in live_pillars(ident) if p["id"] not in ids]
    for n, pid in enumerate(list(ids) + rest, start=1):
        by_id[pid]["order"] = n
    save_identity(r, deck, ident)
    return live_pillars(ident)


def update_thesis(r, deck: dict, payload: Mapping) -> dict:
    """{"text", "emphasis"} sets the user's thesis (pinned); {"action": "accept_draft"}
    adopts the latest generated draft. A re-sync never overwrites the thesis text;
    it only refreshes draft_text next to it."""
    ident = load_identity(r, deck) or new_identity(deck)
    thesis = ident.get("thesis") or {}
    action = payload.get("action") if isinstance(payload, Mapping) else None
    if action == "accept_draft":
        if not thesis.get("draft_text"):
            raise IdentityError("there is no generated draft")
        text, emphasis = thesis["draft_text"], thesis.get("draft_emphasis") or ""
    elif action is not None:
        raise IdentityError("thesis actions: accept_draft")
    else:
        text = payload.get("text") if isinstance(payload, Mapping) else None
        if not isinstance(text, str) or not text.strip():
            raise IdentityError("thesis text is required")
        text = " ".join(text.split())
        if len(text) > THESIS_MAX:
            raise IdentityError(f"thesis is at most {THESIS_MAX} characters")
        emphasis = payload.get("emphasis") or ""
        if emphasis and emphasis not in text:
            raise IdentityError("emphasis must be a phrase from the thesis")
    thesis.update({"text": text, "emphasis": emphasis, "state": "pinned", "updated_by": "user",
                   "updated_at": _now()})
    ident["thesis"] = thesis
    save_identity(r, deck, ident)
    return thesis


# ---------------------------------------------------------------------------
# Generator (LLM: labels + prose only)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a Magic: The Gathering Commander deck analyst. From a decklist you write the deck's THESIS "
    "(one sentence: what the deck is trying to do and how it wins) and its PILLARS: 3 to 5 distinct "
    "strategic themes that most non-land cards serve.\n"
    "Rules:\n"
    f"- Pillar name: 2-4 words, at most {NAME_MAX} characters, plain words (no colons, no emoji).\n"
    "- Pillar description: one or two sentences naming the card roles and mechanics that serve it "
    "(cards are matched to pillars by embedding this description against card text, so be concrete: "
    "mechanics, card types, triggers).\n"
    "- Pillars must not overlap. Do not make a pillar for generic deck glue (lands, mana rocks, generic "
    "removal, generic card draw) unless the deck is built around it.\n"
    "- emphasis: a short phrase copied exactly from the thesis text that carries its core idea.\n"
    "- Return JSON only: {\"thesis\": {\"text\": str, \"emphasis\": str}, "
    "\"pillars\": [{\"name\": str, \"description\": str}]}"
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thesis": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "emphasis": {"type": "STRING"}},
                   "required": ["text", "emphasis"]},
        "pillars": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"}, "description": {"type": "STRING"}}, "required": ["name", "description"]}},
    },
    "required": ["thesis", "pillars"],
}

_REMINDER = re.compile(r"\s*\([^()]*\)")


def _short_text(card: Optional[dict], limit: int = 220) -> str:
    if not card:
        return ""
    t = _REMINDER.sub("", card.get("oracle_text") or "").replace("\n", " / ").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def build_prompt(deck: dict, card_texts: Mapping[str, dict], ident: Optional[dict] = None,
                 engines: Optional[List[str]] = None) -> str:
    commanders = deck.get("commanders") or []
    lines = [f"Deck: {deck.get('name') or ''}", f"Commander(s): {', '.join(commanders) or 'none'}", ""]
    for n in commanders:
        c = card_texts.get(n) or {}
        lines.append(f"COMMANDER {n} | {c.get('type_line', '')} | {_short_text(c, 400)}")
    lines.append("")
    lines.append("Cards (lands omitted):")
    for n in deck_names(deck):
        if n in commanders:
            continue
        c = card_texts.get(n) or {}
        tl = c.get("type_line") or ""
        if "Land" in tl.split("//")[0] and "Creature" not in tl:
            continue
        lines.append(f"- {n} | {tl} | {_short_text(c)}")
    if engines:
        lines += ["", "Detected engines (from the interaction graph): " + "; ".join(engines[:8])]
    live = live_pillars(ident)
    rejected = [p for p in (ident or {}).get("pillars") or [] if p.get("state") == "rejected"]
    if live or rejected:
        lines += ["", "The user already has these pillars. Propose only NEW pillars that are clearly different "
                      "from them (it is fine to propose none). Also write a fresh thesis draft."]
        for p in live:
            lines.append(f"EXISTING: {p['name']}: {p.get('description', '')}")
        for p in rejected:
            lines.append(f"REJECTED (never propose again): {p['name']}: {p.get('description', '')}")
    return "\n".join(lines)


def _parse_response(data) -> tuple:
    if not isinstance(data, dict):
        raise IdentityError("LLM response is not an object")
    th = data.get("thesis")
    if isinstance(th, str):
        th = {"text": th, "emphasis": ""}
    th = th if isinstance(th, dict) else {}
    text = " ".join(str(th.get("text") or "").split())[:THESIS_MAX]
    emphasis = str(th.get("emphasis") or "").strip()
    if emphasis and emphasis not in text:
        emphasis = ""
    pillars = []
    seen = set()
    for item in data.get("pillars") or []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split()).strip(" :.-")
        desc = " ".join(str(item.get("description") or "").split())[:DESC_MAX]
        if not name:
            continue
        if len(name) > NAME_MAX:
            name = name[:NAME_MAX].rstrip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        pillars.append({"name": name, "description": desc})
    return {"text": text, "emphasis": emphasis}, pillars


def _pillar_text(p: dict) -> str:
    return f"{p['name']}: {p.get('description', '')}" if p.get("description") else p["name"]


def _dedupe(proposals: List[dict], existing: List[dict], embedder: Optional[Embedder]) -> tuple:
    """Split proposals into (kept, dropped_names) against every existing pillar
    (live or rejected): same name, or embedding similarity >= dup_sim."""
    names = {p["name"].lower() for p in existing}
    use_emb = bool(existing) and bool(proposals) and embedder is not None and embedder.available()
    ex_vecs: List[List[float]] = []
    pr_vecs: List[List[float]] = []
    if use_emb and embedder is not None:
        ex_vecs = embedder.embed([_pillar_text(p) for p in existing], kind="query")
        pr_vecs = embedder.embed([_pillar_text(p) for p in proposals], kind="query")
    dup_sim = embedder.dup_sim if embedder is not None else 1.0
    kept, dropped = [], []
    for i, p in enumerate(proposals):
        dup = p["name"].lower() in names
        if not dup and use_emb:
            dup = any(sum(a * b for a, b in zip(pr_vecs[i], v)) >= dup_sim for v in ex_vecs)
        (dropped if dup else kept).append(p)
    return kept, [p["name"] for p in dropped]


def generate_identity(r, deck: dict, chain, card_texts: Mapping[str, dict], embedder: Optional[Embedder] = None,
                      on_call: Optional[Callable[[dict], None]] = None, engines: Optional[List[str]] = None) -> dict:
    """Draft (first run) or propose new pillars (re-sync). Returns {status, ...}:
    generated | proposed | no_llm | llm_failed | bad_response."""
    if chain is None or not chain.available():
        return {"status": "no_llm", "deck": _registry_id(deck)}
    ident = load_identity(r, deck)
    first = ident is None or (not ident.get("pillars") and not ident.get("thesis"))
    ident = ident or new_identity(deck)
    prompt = build_prompt(deck, card_texts, None if first else ident, engines)
    cost = {"usd": 0.0}

    def _on_call(call):
        cost["usd"] += call.get("cost_usd") or 0.0
        if on_call:
            on_call({**call, "deck": _registry_id(deck)})

    data, provider, model = chain.generate_json(SYSTEM_PROMPT, prompt, schema=RESPONSE_SCHEMA,
                                                purpose="pillars", on_call=_on_call)
    if data is None:
        return {"status": "llm_failed", "deck": _registry_id(deck)}
    try:
        thesis, proposals = _parse_response(data)
    except IdentityError as exc:
        return {"status": "bad_response", "deck": _registry_id(deck), "error": str(exc)}
    now = _now()
    sig = cards_signature(deck)

    if first:
        if not thesis["text"] or not proposals:
            return {"status": "bad_response", "deck": _registry_id(deck), "error": "no thesis or no pillars"}
        ident["thesis"] = {"text": thesis["text"], "emphasis": thesis["emphasis"], "state": "draft",
                           "updated_by": "generator", "updated_at": now, "draft_text": thesis["text"],
                           "draft_emphasis": thesis["emphasis"], "draft_at": now}
        ident["pillars"] = [_new_pillar(p["name"], p["description"], "draft", "generated", i, "generator")
                            for i, p in enumerate(proposals[:MAX_PILLARS], start=1)]
        ident["proposal"] = None
        status, added, dropped = "generated", len(ident["pillars"]), [n["name"] for n in proposals[MAX_PILLARS:]]
    else:
        kept, dropped = _dedupe(proposals, ident.get("pillars") or [], embedder)
        room = MAX_PILLARS - len(live_pillars(ident))
        added_p = kept[: max(0, room)]
        dropped += [p["name"] for p in kept[max(0, room):]]
        for p in added_p:
            ident["pillars"].append(_new_pillar(p["name"], p["description"], "draft", "resync",
                                                _next_order(ident), "generator"))
        if thesis["text"]:
            t = ident.get("thesis")
            if t is None:
                ident["thesis"] = {"text": thesis["text"], "emphasis": thesis["emphasis"], "state": "draft",
                                   "updated_by": "generator", "updated_at": now}
                t = ident["thesis"]
            t.update({"draft_text": thesis["text"], "draft_emphasis": thesis["emphasis"], "draft_at": now})
        ident["proposal"] = {"new": len(added_p), "dropped": dropped, "at": now}
        status, added = "proposed", len(added_p)

    ident["generator"] = {"provider": provider, "model": model, "generated_at": now,
                          "cost_usd": round(cost["usd"], 6), "cards_sig": sig}
    ident["cards_sig"] = sig
    save_identity(r, deck, ident)
    return {"status": status, "deck": _registry_id(deck), "added": added, "dropped": dropped,
            "provider": provider, "model": model, "cost_usd": round(cost["usd"], 6)}


def generate_for_rebuild(r, decks: List[dict], chain, card_texts: Mapping[str, dict],
                         embedder: Optional[Embedder] = None, on_call: Optional[Callable[[dict], None]] = None,
                         max_decks: int = DEFAULT_MAX_DECKS_PER_REBUILD,
                         engines_by_deck: Optional[Dict[str, List[str]]] = None) -> dict:
    """Rebuild hook: (re)generate decks whose decklist changed since the last
    generation, at most `max_decks` per rebuild (the rest wait for the next one)."""
    stats = {"decks": len(decks), "generated": 0, "skipped_unchanged": 0, "deferred": 0, "failed": 0,
             "new_pillars": 0, "provider": None}
    if chain is None or not chain.available():
        stats["skipped_no_llm"] = len(decks)
        return stats
    for deck in decks:
        ident = load_identity(r, deck)
        if ident and ident.get("cards_sig") == cards_signature(deck):
            stats["skipped_unchanged"] += 1
            continue
        if stats["generated"] + stats["failed"] >= max_decks:
            stats["deferred"] += 1
            continue
        out = generate_identity(r, deck, chain, card_texts, embedder=embedder, on_call=on_call,
                                engines=(engines_by_deck or {}).get(_registry_id(deck)))
        if out["status"] in ("generated", "proposed"):
            stats["generated"] += 1
            stats["new_pillars"] += out.get("added", 0)
            stats["provider"] = out.get("provider")
        else:
            stats["failed"] += 1
    return stats
