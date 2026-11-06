"""Pure, deterministic fact-pack sections (no I/O, no LLM).

Inputs are the normalized card records produced by ``fact_pack.build_fact_pack``
(each has ``oracle`` fields from Scryfall). Every section is either a dict
carrying ``source`` + ``fetched_at`` or ``{"status": "unavailable", "reason": ...}``
(AGENT_RULES §4: missing data is explicit, never 0, never guessed).

Norms and where they come from (cited in the output as well):
- Land / ramp / draw / removal / wipe targets: The Command Zone ep. 658 template,
  ``analytics.composition.DEFAULT_TEMPLATE`` / ``TEMPLATE_SOURCE``. Range = target ±1,
  the same band ``composition.delta_status`` calls "ok".
- Colored-source recommendations: Frank Karsten, "How Many Sources Do You Need to
  Consistently Cast Your Spells? A 2022 Update", 99-card column (Commander: free
  mulligan + free draw), 41 lands assumed.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.server.domain.analytics import composition as comp
from src.server.domain.decks.commander_roles import FUNCTION_ROLES, function_roles

COLORS = ("W", "U", "B", "R", "G")
KARSTEN_SOURCE = ("Frank Karsten, How Many Sources Do You Need to Consistently Cast Your Spells? A 2022 Update "
                  "(99-card column): https://www.tcgplayer.com/content/article/How-Many-Sources-Do-You-Need-to-"
                  "Consistently-Cast-Your-Spells-A-2022-Update/dc23a7d2-0a16-4c0b-ad36-586fcca03ad8/")
# (generic mana, colored pips of that color) -> colored sources for a 99-card deck
KARSTEN_99 = {
    (5, 1): 14, (4, 1): 15, (3, 1): 16, (2, 1): 18, (5, 2): 20, (1, 1): 19, (4, 2): 22, (0, 1): 19,
    (3, 2): 23, (4, 3): 26, (2, 2): 26, (3, 3): 28, (1, 2): 28, (2, 3): 30, (0, 2): 30, (1, 3): 33,
    (1, 4): 36, (0, 3): 36, (0, 4): 39,
}
ROLE_TO_TEMPLATE_ROW = {"ramp": "ramp", "draw": "card_advantage", "targeted_removal": "targeted_disruption",
                        "wipes": "mass_disruption"}
HIGH_SYNERGY = 0.20  # EDHREC synergy at/above which an absent card is called out
_PIP = re.compile(r"\{([^}]+)\}")
BASIC_NAMES = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains",
               "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}


def unavailable(reason: str) -> dict[str, str]:
    return {"status": "unavailable", "reason": reason}


def _is_unavail(x: Any) -> bool:
    return isinstance(x, Mapping) and x.get("status") == "unavailable"


def _front(s: str) -> str:
    return (s or "").split("//")[0]


def _is_land(raw: Mapping) -> bool:
    tl = _front(raw.get("type_line") or "")
    return "Land" in tl.split("—")[0].split()


def _qty(deck: Any, name: str) -> int:
    cards = deck.get("cards") if isinstance(deck, Mapping) else getattr(deck, "cards", None)
    q = 0
    for c in cards or []:
        n = c if isinstance(c, str) else (c.get("name") if isinstance(c, Mapping) else getattr(c, "name", None))
        if n == name:
            q += 1 if isinstance(c, str) else int((c.get("quantity") if isinstance(c, Mapping) else getattr(c, "quantity", 1)) or 1)
    return q or 1


def prepare(deck: Any, cards: Sequence[dict], commanders: set[str]) -> list[dict]:
    """One row per distinct card with raw Scryfall data attached (only cards with data)."""
    rows = []
    for c in cards:
        raw = c.get("_raw")
        if not isinstance(raw, Mapping):
            continue
        rows.append({"name": c["name"], "raw": raw, "qty": 1 if c["name"] in commanders else _qty(deck, c["name"]),
                     "commander": c["name"] in commanders})
    return rows


def _text(raw: Mapping) -> str:
    if raw.get("oracle_text"):
        return raw["oracle_text"]
    return "\n".join(f.get("oracle_text", "") for f in raw.get("card_faces") or [])


def _mana_cost(raw: Mapping) -> str:
    if raw.get("mana_cost"):
        return raw["mana_cost"]
    faces = raw.get("card_faces") or []
    return faces[0].get("mana_cost", "") if faces else ""


def pips_of(mana_cost: str) -> dict[str, int]:
    out = {c: 0 for c in COLORS}
    for sym in _PIP.findall(mana_cost or ""):
        for part in sym.split("/"):
            if part in out:
                out[part] += 1
    return out


def oracle_id_of(raw: Mapping) -> str | None:
    return raw.get("oracle_id") or next((f.get("oracle_id") for f in raw.get("card_faces") or [] if f.get("oracle_id")), None)


def _stamp(source: str, fetched_at: str, **data: Any) -> dict[str, Any]:
    return {"source": source, "fetched_at": fetched_at, **data}


# ---------------------------------------------------------------------------
def curve_section(rows: list[dict], fetched_at: str) -> dict:
    if not rows:
        return unavailable("no Scryfall card data")
    counts = [0] * 8
    total = n = 0
    for r in rows:
        if r["commander"] or _is_land(r["raw"]):
            continue
        mv = r["raw"].get("cmc")
        if not isinstance(mv, (int, float)):
            continue
        counts[min(int(math.floor(mv)), 7)] += r["qty"]
        total += mv * r["qty"]
        n += r["qty"]
    labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    return _stamp("scryfall", fetched_at,
                  buckets=[{"mv": i, "count": counts[i], "label": labels[i]} for i in range(8)],
                  average_mv=round(total / n, 2) if n else 0.0,
                  nonland_cards=n, note="Lands and commanders excluded; MDFCs count by front face.")


def mana_section(rows: list[dict], identity: list[str], fetched_at: str) -> dict:
    if not rows:
        return unavailable("no Scryfall card data")
    land_rows = [r for r in rows if not r["commander"] and _is_land(r["raw"])]
    lands = sum(r["qty"] for r in land_rows)
    mdfc = sum(r["qty"] for r in rows if not r["commander"] and not _is_land(r["raw"])
               and "land" in (r["raw"].get("type_line") or "").lower().split("//")[-1])
    sources = {c: 0 for c in COLORS}
    fixing = tapped = basics = 0
    for r in land_rows:
        produced = set(r["raw"].get("produced_mana") or []) & set(COLORS)
        for c in produced:
            sources[c] += r["qty"]
        if len(produced) >= 2:
            fixing += r["qty"]
        if r["name"] in BASIC_NAMES:
            basics += r["qty"]
        t = _text(r["raw"]).lower()
        if re.search(r"enters(?: the battlefield)? tapped", t) and not re.search(r"enters(?: the battlefield)? tapped unless|if you control|you may pay", t):
            tapped += r["qty"]
    pips = {c: 0 for c in COLORS}
    need: dict[str, int] = {}
    for r in rows:
        if _is_land(r["raw"]):
            continue
        mc = _mana_cost(r["raw"])
        p = pips_of(mc)
        mv = int(r["raw"].get("cmc") or 0)
        colored = sum(p.values())
        for c in COLORS:
            if p[c]:
                if not r["commander"]:
                    pips[c] += p[c] * r["qty"]
                key = (min(max(mv - colored, 0), 5), min(p[c], 4))
                while key not in KARSTEN_99 and key[0] > 0:
                    key = (key[0] - 1, key[1])
                if key in KARSTEN_99:
                    need[c] = max(need.get(c, 0), KARSTEN_99[key])
    colors = [c for c in COLORS if c in identity] or [c for c in COLORS if pips[c]]
    return _stamp(
        "scryfall", fetched_at, lands=lands, mdfc_spell_lands=mdfc, basic_lands=basics, fixing_lands=fixing,
        unconditional_tapped_lands=tapped, nonbasic_lands=lands - basics,
        pips_vs_sources=[{"color": c, "pips": pips[c], "sources": sources[c],
                          "recommended_sources": need.get(c, 0)} for c in colors],
        fixing_quality={"multicolor_lands": fixing, "share_of_lands": round(fixing / lands, 3) if lands else None,
                        "note": "multicolor_lands = lands whose Scryfall produced_mana has 2+ colors; tapped count ignores conditional ETB-tapped lands."},
        norms={"lands": {"target": comp.DEFAULT_TEMPLATE["lands"], "source": comp.TEMPLATE_SOURCE},
               "colored_sources": {"source": KARSTEN_SOURCE,
                                   "caveat": "recommended_sources = the highest requirement among cards of that color (incl. the commander); lands only, ignores rocks/dorks."}},
    )


def roles_section(rows: list[dict], fetched_at: str) -> dict:
    if not rows:
        return unavailable("no Scryfall card data")
    members: dict[str, list[str]] = {r: [] for r in FUNCTION_ROLES}
    counts = {r: 0 for r in FUNCTION_ROLES}
    for r in rows:
        if r["commander"]:
            continue
        for role in function_roles({"name": r["name"], "type_line": r["raw"].get("type_line"), "oracle_text": _text(r["raw"])}):
            counts[role] += r["qty"]
            members[role].append(r["name"])
    out = []
    for role in FUNCTION_ROLES:
        row = ROLE_TO_TEMPLATE_ROW.get(role)
        entry: dict[str, Any] = {"role": role, "count": counts[role], "cards": sorted(members[role])}
        if row:
            t = comp.DEFAULT_TEMPLATE[row]
            entry.update(target_min=max(t - 1, 0), target_max=t + 1, norm_source=comp.TEMPLATE_SOURCE)
        else:
            entry["norm"] = unavailable("no published Commander norm for this role")
        out.append(entry)
    ramp = counts["ramp"]
    return _stamp("scryfall oracle text + analytics.composition/commander_roles heuristics", fetched_at, roles=out,
                  ramp_vs_norm={"count": ramp, "target": comp.DEFAULT_TEMPLATE["ramp"], "source": comp.TEMPLATE_SOURCE})


def bracket_section(deck: Any, rows: list[dict], combos_included: list[dict] | None, fetched_at: str) -> dict:
    from src.server.domain.analytics import bracket_check

    if not rows:
        return unavailable("no Scryfall card data")
    flags = {r["name"]: r["raw"].get("game_changer") for r in rows}
    gc = None
    notes = []
    if all(isinstance(v, bool) for v in flags.values()):
        gc = [n for n, v in flags.items() if v]
        gc_src = "scryfall game_changer flag"
    else:
        gc_src = "checked-in snapshot (Scryfall game_changer flag missing for some cards)"
        notes.append("Game Changers taken from the bundled snapshot because Scryfall's flag was not present on every card.")
    oracle = {r["name"]: {"oracle_text": _text(r["raw"])} for r in rows}
    f = bracket_check.check_deck_brackets(deck, game_changers=gc, combos=combos_included, oracle=oracle)
    d = f.model_dump()
    if combos_included is None:
        notes.append("Commander Spellbook unavailable: the two-card-combo rule was NOT checked, so this is a floor that may be too low.")
    reasons = [{"rule": x["rule"], "floor": x["floor"], "cards": x["cards"]} for x in d["floors"]]
    return _stamp(f"bracket_check ({gc_src}; bracket_cards.json; Commander Spellbook)", fetched_at,
                  minimum_bracket=d["minimum_bracket"], minimum_bracket_name=d["minimum_bracket_name"],
                  reasons=reasons, game_changers=d["game_changers"]["cards"], notes=notes + list(d.get("notes") or []),
                  meaning="Lowest bracket at which every hard rule passes; intent can only raise it. Bracket 5 is never computed.")


# ---------------------------------------------------------------------------
def combos_section(raw: Any, deck_names: set[str], fetched_at: str) -> tuple[dict, list[dict] | None]:
    """-> (section, included variants for the bracket checker or None)."""
    from src.server.domain.analytics.interactions.combos import name_key, normalize_variant

    if _is_unavail(raw):
        return raw, None
    res = raw.get("results") if isinstance(raw, Mapping) else None
    if not isinstance(res, Mapping) or "included" not in res:
        return unavailable("Commander Spellbook response shape unexpected (no results.included)"), None
    have = {name_key(n) for n in deck_names}
    included = [normalize_variant(v) for v in res.get("included") or []]
    near = []
    for v in res.get("almostIncluded") or []:
        nv = normalize_variant(v)
        missing = [n for n in nv["card_names"] if name_key(n) not in have]
        if len(missing) == 1:
            near.append({**nv, "missing": missing})
    key = lambda c: (-int(c.get("popularity") or 0), str(c.get("id")))  # noqa: E731
    slim = lambda c: {k: c[k] for k in ("id", "url", "card_names", "produces", "bracket_tag", "popularity", "identity") if k in c}  # noqa: E731
    sec = _stamp("commanderspellbook find-my-combos", raw.get("fetched_at") or fetched_at,
                 complete=[slim(c) for c in sorted(included, key=key)],
                 near_misses=[{**slim(c), "missing": c["missing"]} for c in sorted(near, key=key)],
                 counts={"complete": len(included), "near_misses": len(near)})
    return sec, included


def edhrec_section(raw: Any, rows: list[dict], reason_if_missing: str, fetched_at: str) -> dict:
    if raw is None or _is_unavail(raw):
        return raw if _is_unavail(raw) else unavailable(reason_if_missing)
    try:
        jd = raw["container"]["json_dict"]
        lists = jd["cardlists"]
        commander = jd["card"]
        assert isinstance(lists, list) and lists and isinstance(commander, Mapping)
        views: dict[str, dict] = {}
        for cl in lists:
            for cv in cl.get("cardviews") or []:
                if cv["name"] and "num_decks" in cv and "potential_decks" in cv:
                    views.setdefault(cv["name"], cv)
        assert views
    except (KeyError, TypeError, AssertionError, AttributeError):
        return unavailable("EDHREC response shape changed (expected container.json_dict.{card,cardlists[].cardviews[]})")
    in_deck = {r["name"] for r in rows}
    present, scored = [], []
    for r in rows:
        if r["commander"] or r["name"] in BASIC_NAMES or _is_land(r["raw"]) and r["name"] not in views:
            continue
        cv = views.get(r["name"]) or views.get(_front(r["name"]).strip())
        if cv:
            inc = cv["num_decks"] / cv["potential_decks"] if cv["potential_decks"] else None
            present.append({"name": r["name"], "inclusion": round(inc, 4) if inc is not None else None,
                            "synergy": round(cv.get("synergy", 0.0), 4), "num_decks": cv["num_decks"]})
            scored.append(1 - (inc or 0.0))
        else:
            if not _is_land(r["raw"]):
                scored.append(1.0)
    absent = []
    for name, cv in views.items():
        if name in in_deck or name in BASIC_NAMES or cv.get("synergy") is None or cv["synergy"] < HIGH_SYNERGY:
            continue
        inc = cv["num_decks"] / cv["potential_decks"] if cv["potential_decks"] else None
        absent.append({"name": name, "inclusion": round(inc, 4) if inc is not None else None, "synergy": round(cv["synergy"], 4)})
    absent.sort(key=lambda x: (-x["synergy"], x["name"]))
    present.sort(key=lambda x: (-(x["inclusion"] or 0), x["name"]))
    return _stamp("edhrec (unofficial json.edhrec.com commander page)", raw.get("fetched_at") or fetched_at,
                  commander={k: commander.get(k) for k in ("name", "num_decks", "rank", "salt")},
                  present=present, high_synergy_absent=absent[:25],
                  deviation_score=round(sum(scored) / len(scored), 4) if scored else None,
                  deviation_definition=(f"Mean of (1 - inclusion) over the deck's nonland, nonbasic cards; a card EDHREC does not list "
                                        f"for this commander counts as 1.0. 0 = a stock list, 1 = nothing in common. "
                                        f"'high_synergy_absent' = listed cards with synergy >= {HIGH_SYNERGY} that the deck lacks."),
                  cards_scored=len(scored))


def salt_section(raw: Any, fetched_at: str) -> dict:
    if raw is None or _is_unavail(raw):
        return raw if _is_unavail(raw) else unavailable("Salt score unavailable")
    cards = raw.get("cards") if isinstance(raw, Mapping) else None
    cards = {k: v for k, v in (cards or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    score = raw.get("salt_score") if isinstance(raw, Mapping) else None
    if score is None and not cards:
        return unavailable("Salt payload had no scores")
    top = sorted(cards.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return _stamp("commandersalt", raw.get("fetched_at") or fetched_at, deck_salt=score,
                  top_cards=[{"name": k, "salt": v} for k, v in top])


# ---------------------------------------------------------------------------
def synergy_section(rows: list[dict], fetched_at: str) -> dict:
    from src.server.domain.tags import engine

    if not rows:
        return unavailable("no Scryfall card data")
    def as_card(r):
        raw = r["raw"]
        return {**raw, "name": r["name"], "oracle_text": _text(raw), "type_line": raw.get("type_line") or ""}
    cmd = [as_card(r) for r in rows if r["commander"]]
    body = [as_card(r) for r in rows if not r["commander"]]
    deck = {"commanders": cmd, "cards": body}
    scores = {c["name"].lower(): engine.pass1_card_score(c) for c in cmd + body}
    try:
        themes = engine.compute_deck_themes(deck, scores)
    except Exception as exc:  # engine heuristics must never take the pack down
        return unavailable(f"tag engine failed: {exc}")
    nonland = [c for c in body if not engine._is_land(c)]
    n = len(nonland) or 1
    byname = {r["name"]: r for r in rows}
    clusters, tied = [], set()
    for tag, info in sorted(themes.items()):
        if not info.active:
            continue
        members = sorted(c["name"] for c in nonland
                         if scores[c["name"].lower()].get(tag, {}).get("score", 0) >= 2
                         or (tag == "graveyard" and scores[c["name"].lower()].get("recursion", {}).get("score", 0) >= 2))
        if len(members) < 2:
            continue
        tied.update(members)
        clusters.append({"name": tag, "cards": [{"name": m, "oracle_id": oracle_id_of(byname[m]["raw"])} for m in members],
                         "strength": round(min(1.0, len(members) / n), 4), "support": info.support, "threshold": info.threshold,
                         "commander_centered": info.is_commander_centered})
    orphans = sorted(c["name"] for c in nonland if c["name"] not in tied
                     and not function_roles({"name": c["name"], "type_line": c["type_line"], "oracle_text": c["oracle_text"]}))
    return _stamp("tags.engine (oracle-text rules, autotag-v1)", fetched_at, clusters=clusters,
                  density=round(len(tied) / n, 4), orphans=orphans,
                  orphan_definition="Nonland, noncommander cards in no active theme cluster and with no functional role (ramp/draw/removal/wipes/protection/recursion/tutors).")


def charts_section(curve: dict, mana: dict, roles: dict, synergy: dict) -> Any:
    """Exactly the review_document `charts` shape; unavailable when any input is."""
    for name, s in (("curve", curve), ("mana", mana), ("roles", roles), ("synergy", synergy)):
        if _is_unavail(s):
            return unavailable(f"{name} section unavailable: {s['reason']}")
    pvs = mana["pips_vs_sources"]
    chart_roles = [{"role": r["role"], "count": r["count"], "target_min": r["target_min"], "target_max": r["target_max"]}
                   for r in roles["roles"] if "target_min" in r]
    if not pvs:
        return unavailable("deck has no colored identity to chart")
    return {"curve": {"buckets": curve["buckets"], "average_mv": curve["average_mv"]},
            "pips_vs_sources": pvs, "roles": chart_roles,
            "synergy_clusters": [{"name": c["name"], "cards": c["cards"], "strength": c["strength"]} for c in synergy["clusters"]]}
