"""
User Intent and Playstyle Configuration Data Model & Validator.

Captures a user's deck-review intent so downstream agents can tailor recommendations.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "user_intent.schema.json"

ALLOWED_DECK_INTENTS: Set[str] = {"new_build", "tune_up", "overhaul"}

ALLOWED_POWER_TIERS: Set[str] = {"casual", "focused", "optimized", "competitive"}

ALLOWED_CURRENCIES: Set[str] = {"USD", "EUR", "GBP", "CAD", "AUD"}

ALLOWED_WIN_CONDITIONS: Set[str] = {
    "combat",
    "combo",
    "attrition",
    "commander_damage",
    "alternate_wincon",
    "mill",
    "burn",
    "lockout",
}

ALLOWED_ARCHETYPES: Set[str] = {
    "aggro",
    "midrange",
    "control",
    "combo",
    "stax",
    "spellslinger",
    "aristocrats",
    "tokens",
    "graveyard",
    "voltron",
    "ramp",
    "tribal",
    "group_hug",
    "group_slug",
}

# Mapping between numeric scale and named tier
POWER_SCALE_TIER_MAP = {
    1: "casual",
    2: "casual",
    3: "casual",
    4: "casual",
    5: "focused",
    6: "focused",
    7: "optimized",
    8: "optimized",
    9: "competitive",
    10: "competitive",
}


def load_schema() -> Dict[str, Any]:
    """Load the JSON Schema file as a dictionary."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class DeckVision:
    intent: str = "tune_up"
    description: str = ""
    target_turn_win: Optional[int] = None


@dataclass
class TargetPowerLevel:
    scale: int = 7
    tier: str = "optimized"


@dataclass
class BudgetConstraints:
    no_budget: bool = False
    total_budget: Optional[float] = None
    max_card_price: Optional[float] = None
    currency: str = "USD"


@dataclass
class PreferredWinConditions:
    primary: str = "combat"
    secondary: List[str] = field(default_factory=list)
    disliked_or_excluded: List[str] = field(default_factory=list)
    custom_win_conditions: List[str] = field(default_factory=list)


@dataclass
class ArchetypePreferences:
    primary_archetype: str = "midrange"
    secondary_archetypes: List[str] = field(default_factory=list)
    excluded_archetypes: List[str] = field(default_factory=list)
    custom_themes: List[str] = field(default_factory=list)


@dataclass
class UserDeckIntent:
    deck_vision: DeckVision = field(default_factory=DeckVision)
    target_power_level: TargetPowerLevel = field(default_factory=TargetPowerLevel)
    budget_constraints: BudgetConstraints = field(default_factory=BudgetConstraints)
    preferred_win_conditions: PreferredWinConditions = field(
        default_factory=PreferredWinConditions
    )
    archetype_preferences: ArchetypePreferences = field(
        default_factory=ArchetypePreferences
    )
    freeform_notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the UserDeckIntent dataclass tree to a dictionary."""
        return asdict(self)


def resolve_intent_defaults(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Given a partial or empty raw intent payload, resolve and backfill all missing
    fields with their documented default fallback values.
    """
    if payload is None:
        payload = {}

    resolved: Dict[str, Any] = {}

    # 1. deck_vision
    dv_in = payload.get("deck_vision") or {}
    resolved["deck_vision"] = {
        "intent": dv_in.get("intent", "tune_up"),
        "description": dv_in.get("description", ""),
        "target_turn_win": dv_in.get("target_turn_win", None),
    }

    # 2. target_power_level
    pl_in = payload.get("target_power_level") or {}
    scale = pl_in.get("scale")
    tier = pl_in.get("tier")
    if scale is None and tier is None:
        scale = 7
        tier = "optimized"
    elif scale is not None and tier is None:
        tier = POWER_SCALE_TIER_MAP.get(scale, "optimized")
    elif scale is None and tier is not None:
        # Default scale for given tier
        tier_to_scale = {
            "casual": 3,
            "focused": 5,
            "optimized": 7,
            "competitive": 9,
        }
        scale = tier_to_scale.get(tier, 7)
    resolved["target_power_level"] = {
        "scale": scale,
        "tier": tier,
    }

    # 3. budget_constraints
    bc_in = payload.get("budget_constraints") or {}
    resolved["budget_constraints"] = {
        "no_budget": bool(bc_in.get("no_budget", False)),
        "total_budget": bc_in.get("total_budget", None),
        "max_card_price": bc_in.get("max_card_price", None),
        "currency": bc_in.get("currency", "USD"),
    }

    # 4. preferred_win_conditions
    wc_in = payload.get("preferred_win_conditions") or {}
    resolved["preferred_win_conditions"] = {
        "primary": wc_in.get("primary", "combat"),
        "secondary": list(wc_in.get("secondary", [])),
        "disliked_or_excluded": list(wc_in.get("disliked_or_excluded", [])),
        "custom_win_conditions": list(wc_in.get("custom_win_conditions", [])),
    }

    # 5. archetype_preferences
    ap_in = payload.get("archetype_preferences") or {}
    resolved["archetype_preferences"] = {
        "primary_archetype": ap_in.get("primary_archetype", "midrange"),
        "secondary_archetypes": list(ap_in.get("secondary_archetypes", [])),
        "excluded_archetypes": list(ap_in.get("excluded_archetypes", [])),
        "custom_themes": list(ap_in.get("custom_themes", [])),
    }

    # Freeform & metadata
    resolved["freeform_notes"] = payload.get("freeform_notes", "")
    metadata = dict(payload.get("metadata", {}))

    # Gracefully collect any unknown top-level fields into metadata so no user input is lost
    known_keys = {
        "deck_vision",
        "target_power_level",
        "budget_constraints",
        "preferred_win_conditions",
        "archetype_preferences",
        "freeform_notes",
        "metadata",
    }
    for k, v in payload.items():
        if k not in known_keys:
            metadata[k] = v
    resolved["metadata"] = metadata

    return resolved


def validate_intent(
    payload: Dict[str, Any], strict_coherence: bool = True
) -> Tuple[bool, List[str]]:
    """
    Validate a user intent payload against business rules and constraints.
    Returns (is_valid, errors_list).

    Validation rules covered:
    - Missing optional fields are permitted (and resolved to defaults).
    - Out-of-range numeric values are rejected with descriptive messages.
    - Invalid enum choices are rejected with descriptive messages.
    - Mutual-exclusivity constraints (e.g. preferred vs excluded win conditions/archetypes, no_budget vs limits).
    - Cross-field constraints:
      - max_card_price cannot exceed total_budget.
      - Power level vs budget coherence (competitive target with low total budget).
      - Power level scale vs tier correspondence.
    - Graceful handling of free-text/unknown entries.
    """
    errors: List[str] = []

    if not isinstance(payload, dict):
        return False, ["Payload must be a JSON object (dict)."]

    # 1. deck_vision validation
    dv = payload.get("deck_vision")
    if dv is not None:
        if not isinstance(dv, dict):
            errors.append("deck_vision must be an object.")
        else:
            intent = dv.get("intent")
            if intent is not None and intent not in ALLOWED_DECK_INTENTS:
                errors.append(
                    f"deck_vision.intent '{intent}' is invalid. Allowed: {sorted(ALLOWED_DECK_INTENTS)}"
                )
            target_turn_win = dv.get("target_turn_win")
            if target_turn_win is not None:
                if not isinstance(target_turn_win, int) or isinstance(target_turn_win, bool):
                    errors.append(
                        f"deck_vision.target_turn_win must be an integer, got {type(target_turn_win).__name__}"
                    )
                elif target_turn_win < 1 or target_turn_win > 20:
                    errors.append(
                        f"deck_vision.target_turn_win must be between 1 and 20, got {target_turn_win}"
                    )

    # 2. target_power_level validation
    pl = payload.get("target_power_level")
    scale_val = None
    tier_val = None
    if pl is not None:
        if not isinstance(pl, dict):
            errors.append("target_power_level must be an object.")
        else:
            scale = pl.get("scale")
            if scale is not None:
                if not isinstance(scale, int) or isinstance(scale, bool):
                    errors.append(
                        f"target_power_level.scale must be an integer between 1 and 10, got {type(scale).__name__}"
                    )
                elif scale < 1 or scale > 10:
                    errors.append(
                        f"target_power_level.scale must be an integer between 1 and 10, got {scale}"
                    )
                else:
                    scale_val = scale

            tier = pl.get("tier")
            if tier is not None:
                if tier not in ALLOWED_POWER_TIERS:
                    errors.append(
                        f"target_power_level.tier '{tier}' is invalid. Allowed: {sorted(ALLOWED_POWER_TIERS)}"
                    )
                else:
                    tier_val = tier

            # Cross-field check: scale vs tier coherence
            if scale_val is not None and tier_val is not None:
                expected_tier = POWER_SCALE_TIER_MAP.get(scale_val)
                # Flag extreme mismatches, e.g. scale 9/10 with casual, or scale 1/2 with competitive
                if (scale_val >= 9 and tier_val in {"casual", "focused"}) or (
                    scale_val <= 4 and tier_val == "competitive"
                ):
                    errors.append(
                        f"target_power_level coherence error: scale {scale_val} conflicts with tier '{tier_val}' (expected '{expected_tier}')."
                    )

    # 3. budget_constraints validation
    bc = payload.get("budget_constraints")
    no_budget = False
    total_budget: Optional[float] = None
    max_card_price: Optional[float] = None
    currency_val = "USD"
    if bc is not None:
        if not isinstance(bc, dict):
            errors.append("budget_constraints must be an object.")
        else:
            no_budget_val = bc.get("no_budget")
            if no_budget_val is not None:
                if not isinstance(no_budget_val, bool):
                    errors.append("budget_constraints.no_budget must be a boolean.")
                else:
                    no_budget = no_budget_val

            tb = bc.get("total_budget")
            if tb is not None:
                if (not isinstance(tb, (int, float))) or isinstance(tb, bool):
                    errors.append(
                        f"budget_constraints.total_budget must be a number, got {type(tb).__name__}"
                    )
                elif tb < 0:
                    errors.append(
                        f"budget_constraints.total_budget cannot be negative, got {tb}"
                    )
                else:
                    total_budget = float(tb)

            mcp = bc.get("max_card_price")
            if mcp is not None:
                if (not isinstance(mcp, (int, float))) or isinstance(mcp, bool):
                    errors.append(
                        f"budget_constraints.max_card_price must be a number, got {type(mcp).__name__}"
                    )
                elif mcp < 0:
                    errors.append(
                        f"budget_constraints.max_card_price cannot be negative, got {mcp}"
                    )
                else:
                    max_card_price = float(mcp)

            curr = bc.get("currency")
            if curr is not None:
                if curr not in ALLOWED_CURRENCIES:
                    errors.append(
                        f"budget_constraints.currency '{curr}' is invalid. Allowed: {sorted(ALLOWED_CURRENCIES)}"
                    )
                else:
                    currency_val = curr

            # Mutual exclusivity constraint: no_budget vs explicit limits
            if no_budget and (total_budget is not None or max_card_price is not None):
                errors.append(
                    "budget_constraints conflict: no_budget is true, but total_budget or max_card_price was specified."
                )

            # Cross-field constraint: max_card_price cannot exceed total_budget
            if (
                total_budget is not None
                and max_card_price is not None
                and max_card_price > total_budget
            ):
                errors.append(
                    f"budget_constraints.max_card_price ({max_card_price}) cannot exceed total_budget ({total_budget})."
                )

    # Cross-field constraint: Power level vs Budget coherence
    if strict_coherence:
        effective_scale = scale_val if scale_val is not None else 7
        effective_tier = tier_val if tier_val is not None else POWER_SCALE_TIER_MAP.get(effective_scale, "optimized")
        if (effective_scale >= 9 or effective_tier == "competitive") and not no_budget:
            if total_budget is not None and total_budget < 200:
                errors.append(
                    f"Power level / budget incoherence: competitive tier (scale {effective_scale}) requires staples "
                    f"incompatible with total_budget of {currency_val} {total_budget:.2f}."
                )

    # 4. preferred_win_conditions validation
    wc = payload.get("preferred_win_conditions")
    if wc is not None:
        if not isinstance(wc, dict):
            errors.append("preferred_win_conditions must be an object.")
        else:
            primary_wc = wc.get("primary")
            if primary_wc is not None and primary_wc not in ALLOWED_WIN_CONDITIONS:
                errors.append(
                    f"preferred_win_conditions.primary '{primary_wc}' is invalid. Allowed: {sorted(ALLOWED_WIN_CONDITIONS)}"
                )

            sec_wc = wc.get("secondary", [])
            if not isinstance(sec_wc, list):
                errors.append("preferred_win_conditions.secondary must be a list.")
            else:
                for item in sec_wc:
                    if item not in ALLOWED_WIN_CONDITIONS:
                        errors.append(
                            f"preferred_win_conditions.secondary item '{item}' is invalid. Allowed: {sorted(ALLOWED_WIN_CONDITIONS)}"
                        )

            excl_wc = wc.get("disliked_or_excluded", [])
            if not isinstance(excl_wc, list):
                errors.append(
                    "preferred_win_conditions.disliked_or_excluded must be a list."
                )
            else:
                for item in excl_wc:
                    if item not in ALLOWED_WIN_CONDITIONS:
                        errors.append(
                            f"preferred_win_conditions.disliked_or_excluded item '{item}' is invalid. Allowed: {sorted(ALLOWED_WIN_CONDITIONS)}"
                        )

            # Mutual-exclusivity constraint
            excl_set = set(excl_wc) if isinstance(excl_wc, list) else set()
            pref_set = set(sec_wc) if isinstance(sec_wc, list) else set()
            if primary_wc:
                pref_set.add(primary_wc)

            overlap = pref_set.intersection(excl_set)
            if overlap:
                for conflict in sorted(overlap):
                    errors.append(
                        f"Win condition conflict: '{conflict}' cannot be both preferred and excluded."
                    )

            # Graceful handling of custom win conditions: must be list of strings
            custom_wc = wc.get("custom_win_conditions", [])
            if not isinstance(custom_wc, list):
                errors.append(
                    "preferred_win_conditions.custom_win_conditions must be a list of strings."
                )

    # 5. archetype_preferences validation
    ap = payload.get("archetype_preferences")
    if ap is not None:
        if not isinstance(ap, dict):
            errors.append("archetype_preferences must be an object.")
        else:
            prim_arch = ap.get("primary_archetype")
            if prim_arch is not None and prim_arch not in ALLOWED_ARCHETYPES:
                errors.append(
                    f"archetype_preferences.primary_archetype '{prim_arch}' is invalid. Allowed: {sorted(ALLOWED_ARCHETYPES)}"
                )

            sec_arch = ap.get("secondary_archetypes", [])
            if not isinstance(sec_arch, list):
                errors.append("archetype_preferences.secondary_archetypes must be a list.")
            else:
                for item in sec_arch:
                    if item not in ALLOWED_ARCHETYPES:
                        errors.append(
                            f"archetype_preferences.secondary_archetypes item '{item}' is invalid. Allowed: {sorted(ALLOWED_ARCHETYPES)}"
                        )

            excl_arch = ap.get("excluded_archetypes", [])
            if not isinstance(excl_arch, list):
                errors.append("archetype_preferences.excluded_archetypes must be a list.")
            else:
                for item in excl_arch:
                    if item not in ALLOWED_ARCHETYPES:
                        errors.append(
                            f"archetype_preferences.excluded_archetypes item '{item}' is invalid. Allowed: {sorted(ALLOWED_ARCHETYPES)}"
                        )

            # Mutual-exclusivity constraint
            excl_arch_set = set(excl_arch) if isinstance(excl_arch, list) else set()
            pref_arch_set = set(sec_arch) if isinstance(sec_arch, list) else set()
            if prim_arch:
                pref_arch_set.add(prim_arch)

            arch_overlap = pref_arch_set.intersection(excl_arch_set)
            if arch_overlap:
                for conflict in sorted(arch_overlap):
                    errors.append(
                        f"Archetype conflict: '{conflict}' cannot be both preferred and excluded."
                    )

            # Graceful handling of custom themes: must be list of strings
            custom_th = ap.get("custom_themes", [])
            if not isinstance(custom_th, list):
                errors.append(
                    "archetype_preferences.custom_themes must be a list of strings."
                )

    return (len(errors) == 0, errors)


def parse_user_intent(
    payload: Optional[Dict[str, Any]] = None, strict_coherence: bool = True
) -> UserDeckIntent:
    """
    Validate, resolve defaults, and parse a raw payload into a strongly-typed
    UserDeckIntent dataclass. Raises ValueError with descriptive messages if
    validation fails.
    """
    if payload is None:
        payload = {}

    is_valid, errors = validate_intent(payload, strict_coherence=strict_coherence)
    if not is_valid:
        raise ValueError(f"Invalid user intent configuration: {'; '.join(errors)}")

    resolved = resolve_intent_defaults(payload)

    dv_data = resolved["deck_vision"]
    pl_data = resolved["target_power_level"]
    bc_data = resolved["budget_constraints"]
    wc_data = resolved["preferred_win_conditions"]
    ap_data = resolved["archetype_preferences"]

    return UserDeckIntent(
        deck_vision=DeckVision(
            intent=dv_data["intent"],
            description=dv_data["description"],
            target_turn_win=dv_data["target_turn_win"],
        ),
        target_power_level=TargetPowerLevel(
            scale=pl_data["scale"],
            tier=pl_data["tier"],
        ),
        budget_constraints=BudgetConstraints(
            no_budget=bc_data["no_budget"],
            total_budget=bc_data["total_budget"],
            max_card_price=bc_data["max_card_price"],
            currency=bc_data["currency"],
        ),
        preferred_win_conditions=PreferredWinConditions(
            primary=wc_data["primary"],
            secondary=wc_data["secondary"],
            disliked_or_excluded=wc_data["disliked_or_excluded"],
            custom_win_conditions=wc_data["custom_win_conditions"],
        ),
        archetype_preferences=ArchetypePreferences(
            primary_archetype=ap_data["primary_archetype"],
            secondary_archetypes=ap_data["secondary_archetypes"],
            excluded_archetypes=ap_data["excluded_archetypes"],
            custom_themes=ap_data["custom_themes"],
        ),
        freeform_notes=resolved["freeform_notes"],
        metadata=resolved["metadata"],
    )
