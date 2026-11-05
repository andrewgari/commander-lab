"""Enumerable composition refs for the Ref combobox (built from the validator constants + registry)."""
from __future__ import annotations

from typing import Any, List, Optional

from src.server.domain.analytics import categories as categories_mod
from src.server.domain.analytics import composition_models as cm

KIND_ORDER = ("core", "group", "type", "facet", "theme", "cat")


def _human(ident: str) -> str:
    return ident.replace("_", " ").title()


def _entry(ref: str, label: str, aliases=()) -> dict:
    kind = ref.partition(":")[0]
    return {"ref": ref, "label": label, "kind": kind, "group": kind, "aliases": sorted(set(aliases))}


def enumerate_refs(scope: str = "preset", r: Any = None, registry_id: Optional[str] = None) -> List[dict]:
    """Every enumerable ref ``check_ref`` accepts in ``scope``. kindred:<type> is a pattern, not listed."""
    out: List[dict] = []
    for cid in cm.CORE_IDS:
        core = categories_mod.CORE_BY_ID.get(cid, {})
        out.append(_entry(f"core:{cid}", core.get("name") or _human(cid),
                          (categories_mod.ALIASES.get(cid, ()) + (cid.replace("_", " "),))))
    for gid in cm.GROUP_IDS:
        extra = categories_mod.GROUP_ALIASES if gid == "interaction" else ()
        out.append(_entry(f"group:{gid}", _human(gid), extra))
    out += [_entry(f"type:{t}", _human(t)) for t in cm.TYPE_IDS]
    out += [_entry(f"facet:{f}", _human(f)) for f in cm.FACET_IDS]
    out += [_entry(f"theme:{t}", _human(t)) for t in cm._themes()]
    if r is not None:
        out += [_entry(f"cat:{c['id']}", c.get("name") or c["id"]) for c in categories_mod.list_global(r)]
        if scope == "deck" and registry_id:
            out += [_entry(f"cat:{c['id']}", c.get("name") or c["id"]) for c in categories_mod.list_deck(r, registry_id)]
    return [e for e in out if cm.check_ref(e["ref"], scope) is None]


def patterns() -> List[dict]:
    return [{"pattern": "theme:kindred:<type>", "label": "Kindred", "example": "theme:kindred:Elf"}]
