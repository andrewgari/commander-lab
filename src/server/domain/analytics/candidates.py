"""Candidate pool: the ONLY source of recommendations for a review (CONTRACT §2.3).

    candidate_pool(deck, intent, profile, *, sources=None, owned=None) -> CandidatePool

Every card in ``pool.cards`` is, by construction:
  - legal in Commander (Scryfall ``legalities.commander`` == "legal"),
  - inside the deck's color identity (Scryfall ``color_identity`` ⊆ commander identity),
  - priced at or under ``profile.budget_norms.hard_cap`` (unknown price is excluded, not guessed;
    no cap configured -> no price filter, said so in ``pool.notes``),
  - not already in the deck, not a basic land,
  - not on ``profile.dislikes.cards`` and not matching ``profile.dislikes.strategies``,
  - not a Game Changer when the intent's target bracket is below 3 (brackets.game_changers_allowed).
Everything dropped is counted by reason in ``pool.excluded`` so nothing disappears silently.

Drawn from EDHREC (the commander page) plus Scryfall searches per role. Owned cards are flagged
and ranked first. ``CURATED_CARD_CATALOG`` is NOT an input (DECISIONS D6: retired from the new
pipeline); a test pins that this module never imports it.

All I/O is injected through ``sources`` (a ``CandidateSources``), so tests run on recorded
fixtures. ``PlayerProfile`` (card 04) is typed structurally: only ``pet_cards``,
``dislikes.cards``, ``dislikes.strategies`` and ``budget_norms.hard_cap`` are read.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from src.server.domain.analytics import brackets
from src.server.domain.analytics import fact_sections as fs
from src.server.domain.decks.commander_roles import FUNCTION_ROLES, function_roles

if TYPE_CHECKING:  # pragma: no cover
    pass

MAX_PER_QUERY = 40
MIN_EDHREC_SYNERGY_FOR_POOL = -1.0  # EDHREC cards are all considered; ranking handles quality
ROLE_QUERIES = {
    "ramp": "otag:ramp",
    "draw": "otag:draw",
    "targeted_removal": "otag:removal",
    "wipes": "otag:sweeper",
    "protection": "otag:protects-creature",
    "recursion": "otag:recursion",
    "tutors": "otag:tutor -otag:tutor-land",
}


class _Dislikes(Protocol):
    cards: Sequence[str]
    strategies: Sequence[str]


class _BudgetNorms(Protocol):
    hard_cap: float | None


class PlayerProfile(Protocol):
    """The slice of card 04's PlayerProfile this module reads."""

    pet_cards: Sequence[str]
    dislikes: _Dislikes
    budget_norms: _BudgetNorms


@dataclass
class CandidateSources:
    """Injected I/O. Each callable may raise; failure becomes an explicit unavailable source."""

    edhrec: Callable[[Sequence[str]], Any] | None = None            # commander names -> raw EDHREC page
    scryfall_batch: Callable[[list[str]], Mapping[str, dict]] | None = None   # names -> {name_lower: card}
    scryfall_search: Callable[[str], Sequence[dict]] | None = None  # query -> cards


@dataclass
class CandidatePool:
    cards: list[dict] = field(default_factory=list)
    by_role: dict[str, list[str]] = field(default_factory=dict)
    by_theme: dict[str, list[str]] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    generated_at: str = ""
    only_source_of_recommendations: bool = True

    def names(self) -> set[str]:
        return {c["name"] for c in self.cards}

    def to_dict(self) -> dict:
        return {"cards": self.cards, "by_role": self.by_role, "by_theme": self.by_theme, "excluded": self.excluded,
                "sources": self.sources, "notes": self.notes, "generated_at": self.generated_at,
                "only_source_of_recommendations": True, "hash": self.hash}

    @property
    def hash(self) -> str:
        d = {"cards": self.cards, "by_role": self.by_role, "by_theme": self.by_theme, "excluded": self.excluded}
        return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _key(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


def _price(card: Mapping) -> float | None:
    p = card.get("prices") or {}
    for k in ("usd", "usd_foil", "usd_etched"):
        if p.get(k) not in (None, ""):
            try:
                return float(p[k])
            except (TypeError, ValueError):
                pass
    return None


def _attr(obj: Any, path: str, default: Any = None) -> Any:
    for part in path.split("."):
        if obj is None:
            return default
        obj = obj.get(part) if isinstance(obj, Mapping) else getattr(obj, part, None)
    return default if obj is None else obj


def owned_from_rows(rows: Iterable[Mapping]) -> dict[str, dict]:
    """inventory_view.build_inventory() rows -> {name_key: {copies, available, decks}}.
    `available` = copies not sleeved in a deck (status 'have' with no real deck)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("ownership_status") == "not_owned":
            continue
        rec = out.setdefault(_key(r["name"]), {"name": r["name"], "copies": 0, "available": 0, "decks": []})
        rec["copies"] += 1
        if r.get("ownership_status") == "in_deck":
            if r.get("deck") and r["deck"] not in rec["decks"]:
                rec["decks"].append(r["deck"])
        else:
            rec["available"] += 1
    return out


def _edhrec_views(raw: Any) -> dict[str, dict]:
    jd = raw["container"]["json_dict"]
    out: dict[str, dict] = {}
    for cl in jd["cardlists"]:
        for cv in cl.get("cardviews") or []:
            if isinstance(cv.get("name"), str) and "num_decks" in cv and "potential_decks" in cv:
                cur = out.get(cv["name"])
                if cur is None:
                    out[cv["name"]] = {**cv, "lists": [cl.get("tag") or cl.get("header")]}
                else:
                    cur["lists"].append(cl.get("tag") or cl.get("header"))
    if not out:
        raise ValueError("no cardviews")
    return out


def candidate_pool(deck: Any, intent: Any, profile: PlayerProfile | None, *,
                   sources: CandidateSources | None = None, owned: Mapping[str, Mapping] | None = None,
                   now: datetime | None = None) -> CandidatePool:
    """Build the pool. `owned`: {name_key: {copies, available, decks}} (see owned_from_rows);
    None means ownership is unavailable and no card is flagged owned (said so in notes)."""
    from src.server.domain.analytics.fact_pack import _commander_names, _names

    src = sources or CandidateSources()
    pool = CandidatePool(generated_at=(now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat())
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    deck_names = _names(deck)
    commanders = _commander_names(deck)
    in_deck = {_key(n) for n in deck_names}

    # --- deck facts (identity, themes) from Scryfall -------------------------------------------
    deck_cards: Mapping[str, dict] = {}
    if src.scryfall_batch:
        try:
            deck_cards = src.scryfall_batch(deck_names)
        except Exception as exc:  # noqa: BLE001
            pool.sources["scryfall_deck"] = fs.unavailable(f"Scryfall lookup of the deck failed: {exc}")
    ident_cards = [deck_cards.get(c.lower()) or deck_cards.get(_key(c)) for c in commanders]
    ident_cards = [c for c in ident_cards if c]
    if not ident_cards:
        pool.notes.append("Commander color identity unavailable: pool is empty (cannot prove any card is in identity).")
        pool.sources.setdefault("scryfall_deck", fs.unavailable("commander not resolved"))
        pool.excluded = excluded
        return pool
    identity = {c for card in ident_cards for c in card.get("color_identity") or []}
    pool.sources["scryfall_deck"] = {"status": "ok", "source": "scryfall", "color_identity": [c for c in fs.COLORS if c in identity]}

    # --- profile/intent constraints -----------------------------------------------------------
    dislike_cards = {_key(n) for n in (_attr(profile, "dislikes.cards", []) or [])}
    dislike_strats = [str(s).lower().strip() for s in (_attr(profile, "dislikes.strategies", []) or []) if str(s).strip()]
    pets = {_key(n) for n in (_attr(profile, "pet_cards", []) or [])}
    hard_cap = _attr(profile, "budget_norms.hard_cap", None)
    if profile is None:
        pool.notes.append("No player profile: dislike list, pet cards and budget cap were not applied.")
    elif hard_cap is None:
        pool.notes.append("No budget hard cap in the profile: price was not filtered.")
    target_bracket = _attr(intent, "bracket", None) or _attr(intent, "target_bracket", None)
    block_gc = False
    if target_bracket is not None:
        try:
            block_gc = not brackets.game_changers_allowed(target_bracket, 1)
        except (ValueError, TypeError):
            pool.notes.append(f"Unrecognized target bracket {target_bracket!r}: Game Changers not filtered.")

    # --- deck themes (so candidates can be grouped by the deck's own plan) -------------------
    deck_theme_tags: set[str] = set()
    if deck_cards:
        rows = [{"name": n, "raw": deck_cards[n.lower()], "qty": 1, "commander": n in commanders}
                for n in deck_names if n.lower() in deck_cards]
        syn = fs.synergy_section(rows, pool.generated_at)
        if not fs._is_unavail(syn):
            deck_theme_tags = {c["name"] for c in syn["clusters"]}

    # --- gather raw candidates -------------------------------------------------------------------
    raw: dict[str, dict] = {}   # name_key -> {name, edhrec, roles_from_search, card}
    if src.edhrec:
        try:
            views = _edhrec_views(src.edhrec(commanders))
            pool.sources["edhrec"] = {"status": "ok", "source": "edhrec (unofficial json.edhrec.com)", "cards_listed": len(views)}
            for name, cv in views.items():
                inc = cv["num_decks"] / cv["potential_decks"] if cv["potential_decks"] else None
                raw.setdefault(_key(name), {"name": name, "sources": set(), "search_roles": set()}).update(
                    edhrec={"inclusion": round(inc, 4) if inc is not None else None, "synergy": cv.get("synergy"), "lists": cv["lists"]})
                raw[_key(name)]["sources"].add("edhrec")
        except Exception as exc:  # noqa: BLE001
            pool.sources["edhrec"] = fs.unavailable(f"EDHREC unavailable or changed shape: {exc}")
    else:
        pool.sources["edhrec"] = fs.unavailable("EDHREC source not provided")

    searched: dict[str, dict] = {}
    if src.scryfall_search:
        ci = "".join(c for c in fs.COLORS if c in identity) or "c"
        cap_q = f" usd<={hard_cap:g}" if hard_cap is not None else ""
        for role, otag in ROLE_QUERIES.items():
            q = f"f:commander id<={ci}{cap_q} {otag}"
            try:
                found = list(src.scryfall_search(q))[:MAX_PER_QUERY]
            except Exception as exc:  # noqa: BLE001
                pool.sources[f"scryfall_search:{role}"] = fs.unavailable(f"Scryfall search failed: {exc}")
                continue
            pool.sources[f"scryfall_search:{role}"] = {"status": "ok", "source": "scryfall", "query": q, "returned": len(found)}
            for card in found:
                k = _key(card.get("name", ""))
                searched[k] = card
                r = raw.setdefault(k, {"name": card.get("name"), "sources": set(), "search_roles": set()})
                r["sources"].add("scryfall_search")
                r["search_roles"].add(role)
    else:
        pool.sources["scryfall_search"] = fs.unavailable("Scryfall search source not provided")

    # --- resolve Scryfall facts for EDHREC-only names ------------------------------------------
    need = [r["name"] for k, r in raw.items() if k not in searched]
    resolved: Mapping[str, dict] = {}
    if need and src.scryfall_batch:
        try:
            resolved = src.scryfall_batch(need)
        except Exception as exc:  # noqa: BLE001
            pool.sources["scryfall_candidates"] = fs.unavailable(f"Scryfall lookup of candidates failed: {exc}")

    # --- filter ----------------------------------------------------------------------------------
    from src.server.domain.tags import engine

    kept: list[dict] = []
    for k, r in sorted(raw.items()):
        name = r["name"]
        card = searched.get(k) or resolved.get(name.lower()) or resolved.get(k)
        if k in in_deck:
            drop("already_in_deck"); continue
        if name in fs.BASIC_NAMES:
            drop("basic_land"); continue
        if card is None:
            drop("no_scryfall_data"); continue
        if k in dislike_cards:
            drop("disliked_card"); continue
        if (card.get("legalities") or {}).get("commander") != "legal":
            drop("not_commander_legal"); continue
        if not set(card.get("color_identity") or []) <= identity:
            drop("outside_color_identity"); continue
        price = _price(card)
        if hard_cap is not None:
            if price is None:
                drop("price_unknown_with_budget_cap"); continue
            if price > hard_cap:
                drop("over_budget_hard_cap"); continue
        if block_gc and card.get("game_changer") is True:
            drop("game_changer_above_target_bracket"); continue
        text = fs._text(card)
        roles = set(r["search_roles"]) | set(function_roles({"name": name, "type_line": card.get("type_line"), "oracle_text": text}))
        scores = engine.pass1_card_score({**card, "oracle_text": text})
        themes = sorted(t for t, v in scores.items() if v.get("score", 0) >= 2 and (t in deck_theme_tags))
        strategy_hay = {s.lower() for s in roles} | {t.lower() for t in scores} | {str(x).lower() for x in (r.get("edhrec") or {}).get("lists", [])}
        if any(s in strategy_hay for s in dislike_strats):
            drop("disliked_strategy"); continue
        own = (owned or {}).get(k)
        kept.append({
            "name": card.get("name") or name, "oracle_id": fs.oracle_id_of(card), "type_line": card.get("type_line"),
            "cmc": card.get("cmc"), "price_usd": price, "game_changer": card.get("game_changer"),
            # Carried so later passes read card text from data (CONTRACT §2.1) and the verifier can re-check the
            # pool's own guarantees (identity, legality) instead of trusting them.
            "oracle_text": text, "color_identity": sorted(card.get("color_identity") or []),
            "legalities": {"commander": (card.get("legalities") or {}).get("commander")},
            "roles": sorted(r_ for r_ in FUNCTION_ROLES if r_ in roles), "themes": themes,
            "sources": sorted(r["sources"]), "edhrec": r.get("edhrec"),
            "owned": bool(own), "owned_copies": own.get("copies") if own else 0,
            "owned_available": own.get("available") if own else 0,
            "owned_in_decks": list(own.get("decks") or []) if own else [],
            "pet_card_of_player": k in pets,
        })
    if owned is None:
        pool.notes.append("Ownership unavailable: no card is flagged owned.")

    def rank(c: dict) -> tuple:
        e = c["edhrec"] or {}
        return (not c["owned_available"], not c["owned"], -(e.get("synergy") or 0.0), -(e.get("inclusion") or 0.0), c["name"])

    kept.sort(key=rank)
    for i, c in enumerate(kept, 1):
        c["rank"] = i
    pool.cards = kept
    for c in kept:
        for role in c["roles"]:
            pool.by_role.setdefault(role, []).append(c["name"])
        for th in c["themes"]:
            pool.by_theme.setdefault(th, []).append(c["name"])
    pool.excluded = dict(sorted(excluded.items()))
    pool.by_role = dict(sorted(pool.by_role.items()))
    pool.by_theme = dict(sorted(pool.by_theme.items()))
    return pool
