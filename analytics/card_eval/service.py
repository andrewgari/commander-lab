"""
Card evaluation service: evaluate_card(r, card, deck) -> EvaluationResult. See docs/CARD_EVAL.md.

Flow: resolve card (Scryfall) -> EDHREC commander page (cached) -> role signal (HF zero-shot,
heuristic fallback) -> strength -> candidate pool (EDHREC cards for this commander, filtered by
colour identity + Commander legality, same-role first). Every external dependency is injectable.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import cards as cards_orm
from analytics.composition import classify_card
from analytics.edhrec_inclusion import BASICS, name_key
from analytics.providers.edhrec import EDHRecClient, EDHRecError

from . import config as cfg
from . import edhrec_data
from .card_source import CardSourceError, fetch_cards
from .hf_classifier import HFClassifier, HFError
from .schema import (Candidate, CardInfo, DeckContext, EdhrecStats, EvaluationResult, ModelSignal, RoleScore,
                     StrengthBreakdown)

log = logging.getLogger("commander_lab.card_eval")

CardLookup = Callable[[Iterable[str]], Mapping[str, dict]]  # names -> {name_key: card record}
OwnedLookup = Callable[[Sequence[str]], Set[str]]           # names -> owned name_keys


class CardEvalError(Exception):
    """Base class for evaluation failures the caller should surface."""


class UnknownCardError(CardEvalError):
    """The card name/ID could not be resolved to a real card."""


class InvalidDeckError(CardEvalError):
    """The deck context is unusable (e.g. no commander)."""


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def heuristic_roles(name: str, card: Optional[dict]) -> List[str]:
    roles = [cfg.COMPOSITION_ROLES[row] for row in classify_card(name, card) if row in cfg.COMPOSITION_ROLES]
    tl = ((card or {}).get("type_line") or "").split("//")[0]
    if "land" in tl.lower() and "creature" not in tl.lower():
        roles.append("utility_land")
    return roles


def _resolve_name(r, card: str) -> str:
    card = (card or "").strip()
    if not card:
        raise UnknownCardError("no card given")
    if cards_orm.is_oracle_id(card):
        rec = cards_orm.get_card(r, card)
        if not rec or not rec.get("name"):
            raise UnknownCardError(f"no card with oracle id {card}")
        return rec["name"]
    return card


def _merge_roles(model_scores: Mapping[str, float], heur: Iterable[str], tags: Iterable[str]) -> List[RoleScore]:
    best: Dict[str, RoleScore] = {}

    def offer(role, conf, source):
        if role not in best or conf > best[role].confidence:
            best[role] = RoleScore(role=role, confidence=round(_clamp01(conf), 4), source=source)

    for role, conf in model_scores.items():
        if conf >= cfg.MIN_ROLE_CONFIDENCE:
            offer(role, conf, "model")
    for role in heur:
        offer(role, 0.6, "heuristic")
    for tag in tags:
        for role in cfg.EDHREC_TAG_ROLES.get(tag, ()):
            offer(role, 0.5, "edhrec")
    return sorted(best.values(), key=lambda s: (-s.confidence, s.role))[: cfg.MAX_ROLES]


def compute_strength(inclusion, synergy, role_conf, role_need, w: cfg.ScoringWeights):
    """Weighted mean (0..100) over the components that have data. Returns (score, breakdown)."""
    comps = {
        "inclusion": None if inclusion is None else _clamp01(inclusion / w.inclusion_full),
        "synergy": None if synergy is None else _clamp01((synergy - w.synergy_floor) / (w.synergy_ceil - w.synergy_floor)),
        "role_confidence": role_conf,
        "role_need": role_need,
    }
    weights = {"inclusion": w.inclusion, "synergy": w.synergy, "role_confidence": w.role_confidence,
               "role_need": w.role_need}
    live = {k: v for k, v in comps.items() if v is not None and weights[k] > 0}
    total = sum(weights[k] for k in live)
    score = 100.0 * sum(weights[k] * live[k] for k in live) / total if total else 0.0
    return round(score, 1), StrengthBreakdown(**{k: (None if v is None else round(v, 4)) for k, v in comps.items()})


def _ci_ok(card_ci: str, allowed: Set[str]) -> bool:
    return set(card_ci or "") - {"C"} <= allowed


def evaluate_card(r, card: str, deck: DeckContext, *, edhrec_client: Optional[EDHRecClient] = None,
                  classifier: Optional[HFClassifier] = None, card_lookup: Optional[CardLookup] = None,
                  owned_lookup: Optional[OwnedLookup] = None,
                  weights: Optional[cfg.ScoringWeights] = None) -> EvaluationResult:
    commanders = [c for c in deck.commanders if c and c.strip()]
    if not commanders:
        raise InvalidDeckError("deck context needs at least one commander")
    weights = weights or cfg.ScoringWeights.from_env()
    classifier = classifier or HFClassifier()
    lookup = card_lookup or (lambda names: fetch_cards(r, names))
    warnings: List[str] = []

    # 1. Resolve the card and the commanders' colour identity.
    name = _resolve_name(r, card)
    deck_names = [c for c in deck.cards if c]
    try:
        facts = lookup([name, *commanders])
    except CardSourceError as exc:
        raise CardEvalError(f"card data source unavailable: {exc}") from exc
    rec = facts.get(name_key(name))
    if not rec:
        raise UnknownCardError(f"unknown card: {name!r}")
    info = CardInfo(name=rec["name"], oracle_id=rec.get("oracle_id"), type_line=rec.get("type_line", ""),
                    oracle_text=rec.get("oracle_text", ""), color_identity=rec.get("color_identity", ""),
                    legal=bool(rec.get("legal", True)))
    allowed: Optional[Set[str]] = None
    cmd_recs = [facts.get(name_key(c)) for c in commanders]
    if all(cmd_recs):
        allowed = set().union(*(set(c.get("color_identity") or "") for c in cmd_recs)) - {"C"}
    else:
        warnings.append("commander not found in card source; colour-identity filter skipped")
    if not info.legal:
        warnings.append("card is not legal in Commander")
    if allowed is not None and not _ci_ok(info.color_identity, allowed):
        warnings.append("card is outside the commander's colour identity")

    # 2. EDHREC.
    page = None
    ed = EdhrecStats(status="unavailable", commander_slug=None)
    entry = None
    try:
        page = edhrec_data.get_page(r, commanders, edhrec_client)
        entry = edhrec_data.lookup(page, info.name)
        ed = EdhrecStats(status="ok" if entry else "card_not_listed", commander_slug=page["slug"],
                         decks_sampled=page.get("decks_sampled"), stale=bool(page.get("stale")),
                         error=page.get("error"))
        if entry:
            ed = ed.model_copy(update={"inclusion": entry["inclusion"], "synergy": entry["synergy"],
                                       "num_decks": entry["num_decks"], "slot": (entry["tags"] or [None])[0],
                                       "slot_peers": edhrec_data.slot_peers(page, entry)})
        else:
            warnings.append("card is not on the commander's EDHREC page (rare with this commander)")
    except edhrec_data.CommanderPageMissing as exc:
        ed = EdhrecStats(status="no_commander_page", error=str(exc)[:200])
        warnings.append("EDHREC has no page for this commander; scoring without EDHREC data")
    except (EDHRecError, ValueError) as exc:
        ed = EdhrecStats(status="unavailable", error=str(exc)[:200])
        warnings.append("EDHREC unavailable; scoring without EDHREC data")

    # 3. Role signal (HF, heuristic fallback).
    card_dict = {"type_line": info.type_line, "oracle_text": info.oracle_text}
    model = ModelSignal(status="disabled", model_id=classifier.model_id if classifier.enabled else None)
    scores: Dict[str, float] = {}
    if classifier.enabled:
        try:
            scores = classifier.classify(f"{info.name}. {info.type_line}. {info.oracle_text}")
            model = ModelSignal(status="ok", model_id=classifier.model_id, scores={k: round(v, 4) for k, v in scores.items()})
        except HFError as exc:
            model = ModelSignal(status="unavailable", model_id=classifier.model_id, error=str(exc)[:200])
            warnings.append("HF model unavailable; roles are heuristic")
    heur = heuristic_roles(info.name, card_dict)
    roles = _merge_roles(scores, heur, (entry or {}).get("tags") or [])

    # 4. Strength.
    role_conf = roles[0].confidence if roles and roles[0].source == "model" else None
    role_need = None
    deck_recs: Dict[str, dict] = {}
    if roles and deck_names:
        try:
            deck_recs = dict(lookup(deck_names))
        except CardSourceError:
            warnings.append("deck card data unavailable; role-need not scored")
        if deck_recs:
            top = roles[0].role
            have = sum(1 for n in deck_names if name_key(n) != name_key(info.name)
                       and top in heuristic_roles(n, deck_recs.get(name_key(n))))
            if top in cfg.COMPOSITION_ROLES.values():
                role_need = _clamp01(1.0 - have / cfg.ROLE_SATURATION)
    strength, breakdown = compute_strength(ed.inclusion, ed.synergy, role_conf, role_need, weights)

    # 5. Candidate pool.
    candidates: List[Candidate] = []
    if page:
        candidates = _build_pool(r, page, info, roles, deck_names, commanders, allowed, lookup,
                                 owned_lookup if deck.kind == "physical" else None, weights, warnings)

    return EvaluationResult(card=info, deck_kind=deck.kind, commanders=commanders, roles=roles, strength=strength,
                            strength_breakdown=breakdown, edhrec=ed, model=model, candidates=candidates,
                            warnings=warnings)


def _build_pool(r, page, info, roles, deck_names, commanders, allowed, lookup, owned_lookup, w, warnings):
    exclude = {name_key(n) for n in [*deck_names, *commanders, info.name]} | {name_key(b) for b in BASICS}
    pool = [c for k, c in page["cards"].items() if k not in exclude]

    def base(c):
        syn = c.get("synergy")
        syn_c = 0.0 if syn is None else _clamp01((syn - w.synergy_floor) / (w.synergy_ceil - w.synergy_floor))
        return w.pool_synergy * syn_c + w.pool_inclusion * _clamp01(c["inclusion"] / w.inclusion_full)

    pool.sort(key=lambda c: (-base(c), c["name"]))
    pool = pool[: cfg.POOL_SCAN_LIMIT]
    try:
        facts = lookup([c["name"] for c in pool])
    except CardSourceError:
        warnings.append("card data unavailable; candidate pool skipped")
        return []
    owned: Optional[Set[str]] = None
    if owned_lookup is not None:
        try:
            owned = {name_key(n) for n in owned_lookup([c["name"] for c in pool])}
        except Exception as exc:  # noqa: BLE001 - ownership is a nicety
            log.warning("owned lookup failed: %s", exc)
            warnings.append("collection lookup failed; ownership not annotated")
    want = {s.role for s in roles}
    same: List[Candidate] = []
    other: List[Candidate] = []
    for c in pool:
        f = facts.get(name_key(c["name"]))
        if not f or not f.get("legal", True):
            continue
        if allowed is not None and not _ci_ok(f.get("color_identity", ""), allowed):
            continue
        croles = []
        for role in [*heuristic_roles(c["name"], f), *(x for t in c["tags"] for x in cfg.EDHREC_TAG_ROLES.get(t, ()))]:
            if role not in croles:
                croles.append(role)
        is_owned = None if owned is None else name_key(c["name"]) in owned
        score = base(c) + (w.physical_owned_bonus if is_owned else 0.0)
        cand = Candidate(name=f.get("name") or c["name"], roles=croles, same_role=bool(want & set(croles)),
                         inclusion=c["inclusion"], synergy=c["synergy"], score=round(score, 4),
                         color_identity=f.get("color_identity", ""), owned=is_owned)
        (same if cand.same_role else other).append(cand)
    key = lambda x: (-x.score, x.name)  # noqa: E731
    return sorted(same, key=key)[: cfg.POOL_SAME_ROLE] + sorted(other, key=key)[: cfg.POOL_OTHER_ROLE]
