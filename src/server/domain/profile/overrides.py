"""Profile overrides: user steering over the computed profile
(ARCHITECTURE.md §4.2).

Pure logic: validation of override keys/values, the effective-profile merge
(latest snapshot metrics + active overrides), and the snapshot triple built
when an override changes (every override write appends a profile_snapshots
row so history is complete).

Override keys are a closed registry: an unknown key is a validation error,
not a silently stored arbitrary blob, so the profile page and future slices
can rely on the value shapes below. Values are typed per key; ``None``
clears the override (the row is kept with value null so the history of "the
user unset this" is preserved in the appended snapshot).
"""
from __future__ import annotations

from typing import Any, Optional


class OverrideError(ValueError):
    """Unknown override key or a value that fails its type/range check."""


# key -> (human label, validator). Validators return the normalized value or
# raise OverrideError. None always passes (it clears the override).
def _v_number(lo: float, hi: float):
    def check(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OverrideError(f"must be a number in [{lo:g}, {hi:g}]")
        v = float(value)
        if not lo <= v <= hi:
            raise OverrideError(f"must be a number in [{lo:g}, {hi:g}]")
        return v
    return check


def _v_string(max_len: int = 120):
    def check(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise OverrideError("must be a non-empty string")
        v = value.strip()
        if len(v) > max_len:
            raise OverrideError(f"must be at most {max_len} characters")
        return v
    return check


OVERRIDE_KEYS: dict[str, tuple[str, Any]] = {
    "power_level_target": (
        "Target power level (1-10)", _v_number(1, 10)),
    "curve_avg_mv_target": (
        "Target average mana value (non-land)", _v_number(0, 10)),
    "archetype_focus": (
        "Archetype/theme to lean into", _v_string()),
}


def validate(key: str, value: Any) -> Any:
    """Normalize one override value; raises OverrideError on any problem."""
    if key not in OVERRIDE_KEYS:
        known = ", ".join(sorted(OVERRIDE_KEYS))
        raise OverrideError(f"unknown override key {key!r} (known: {known})")
    if value is None:
        return None
    return OVERRIDE_KEYS[key][1](value)


def apply_overrides(metrics: Optional[dict], overrides: dict) -> dict:
    """Effective profile = latest snapshot metrics + active overrides.

    ``overrides`` maps key -> stored row dict ({"value": ..., "set_at": ...}).
    Overrides with value None are treated as unset. The merged view adds an
    ``"overrides"`` block; the computed metrics are never rewritten, so what
    was measured vs what the user steered stays distinguishable.
    """
    active = {
        key: row["value"]
        for key, row in (overrides or {}).items()
        if row.get("value") is not None
    }
    merged = dict(metrics or {})
    merged["overrides"] = active
    return merged


def override_snapshot(
    latest_metrics: Optional[dict],
    overrides: dict,
    changed_key: str,
) -> dict:
    """The (inputs, metrics, summary_text) triple for trigger=override."""
    metrics = apply_overrides(latest_metrics, overrides)
    label = OVERRIDE_KEYS[changed_key][0]
    new_value = overrides.get(changed_key, {}).get("value")
    if latest_metrics is None:
        metrics.setdefault("unavailable", {
            "count": 1,
            "reason": "no computed profile snapshot yet; override stored",
        })
    if new_value is None:
        change = f"Cleared the {label.lower()} override."
    else:
        change = f"Set {label.lower()} to {new_value}."
    summary = change + " Profile history keeps every override as its own snapshot."
    return {
        "inputs": {
            "override_key": changed_key,
            "override_value": new_value,
            "based_on_latest_snapshot": latest_metrics is not None,
        },
        "metrics": metrics,
        "summary_text": summary,
    }
