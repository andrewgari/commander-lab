"""
Engine detection on a deck interaction graph (AN-2).

An engine is a resource chain inside one deck:

    sources  --r1-->  converters  --r2-->  payoffs
    (make r1)         (spend r1, make r2)   (spend r2)

plus an optional "fuel" stage (e.g. token makers feeding sac outlets) and
an optional second converter step (r1 -> r2 -> r3). A chain needs at least
one source and one converter, and either a payoff or a last resource that
is itself value (drain, cards, counters, tokens, mana, life). Two-card
cycles (A feeds B and B feeds A) are reported separately as loops.

Everything here is deterministic math on the AN-1 graph nodes
(produces/consumes) and edges. The LLM only supplies a NAME for engine
signatures the built-in catalog doesn't cover, and those names are cached
per signature (service.py), so a signature costs at most one call ever.
"""
from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from typing import Callable, Dict, Iterable, List, Optional

from .vocab import RESOURCE_BY_KEY, RESOURCE_KEYS

MAX_CHAINS = 8
MAX_LOOPS = 4

# Resources that are worth something on their own: a converter that
# produces one of these is a payoff, no further consumer needed.
VALUE_RANK = {"life_loss": 6, "cards": 5, "counters": 4, "tokens": 3, "mana": 2, "life_gain": 1}

# Resources a card "produces" by its card type (an artifact produces
# artifacts). Valid as a chain's first resource, never as a later step.
TYPE_RESOURCES = {"artifacts", "enchantments", "spells"}

# Resources that typically feed the SOURCES of a chain starting at r1
# (token makers feed sac outlets). A fuel card only counts when the graph
# has an edge from it into one of the chain's sources on that resource.
FUEL = {
    "death": ("tokens",),
    "sacrifice": ("tokens",),
}

# Short words used in engine names ("tokens → sac → drain").
SHORT = {
    "tokens": "tokens", "sacrifice": "sac", "death": "death", "etb": "ETB",
    "counters": "counters", "graveyard": "graveyard", "mana": "mana", "cards": "cards",
    "life_gain": "lifegain", "life_loss": "drain", "attacks": "attack", "spells": "spells",
    "artifacts": "artifacts", "enchantments": "enchantments", "lands": "landfall",
}

# Deterministic archetype names by resource path. Steps override SHORT
# for this archetype (aristocrats say "sac", not "death").
CATALOG: Dict[str, dict] = {
    "death>life_loss": {"archetype": "Aristocrats", "steps": ["sac", "drain"]},
    "sacrifice>life_loss": {"archetype": "Aristocrats", "steps": ["sac", "drain"]},
    "death>cards": {"archetype": "Death draw", "steps": ["death", "cards"]},
    "death>tokens": {"archetype": "Death recursion", "steps": ["death", "tokens"]},
    "death>counters": {"archetype": "Death counters", "steps": ["death", "counters"]},
    "tokens>life_loss": {"archetype": "Token drain", "steps": ["tokens", "drain"]},
    "tokens>cards": {"archetype": "Token draw", "steps": ["tokens", "cards"]},
    "tokens>mana": {"archetype": "Token ramp", "steps": ["tokens", "mana"]},
    "etb>life_loss": {"archetype": "ETB drain", "steps": ["ETB", "drain"]},
    "etb>cards": {"archetype": "ETB value", "steps": ["ETB", "cards"]},
    "etb>tokens": {"archetype": "ETB tokens", "steps": ["ETB", "tokens"]},
    "etb>counters": {"archetype": "ETB counters", "steps": ["ETB", "counters"]},
    "attacks>etb": {"archetype": "Attack cheat", "steps": ["attack", "ETB"]},
    "attacks>etb>life_loss": {"archetype": "Attack cheat", "steps": ["attack", "ETB", "drain"]},
    "attacks>etb>attacks": {"archetype": "Attack cheat", "steps": ["attack", "ETB", "attack"]},
    "attacks>life_loss": {"archetype": "Combat drain", "steps": ["attack", "drain"]},
    "attacks>cards": {"archetype": "Combat draw", "steps": ["attack", "cards"]},
    "attacks>tokens": {"archetype": "Attack tokens", "steps": ["attack", "tokens"]},
    "attacks>mana": {"archetype": "Combat ramp", "steps": ["attack", "mana"]},
    "attacks>counters": {"archetype": "Combat counters", "steps": ["attack", "counters"]},
    "mana>attacks": {"archetype": "Mana into combat", "steps": ["mana", "attack"]},
    "graveyard>etb": {"archetype": "Reanimator", "steps": ["graveyard", "reanimate"]},
    "graveyard>cards": {"archetype": "Graveyard value", "steps": ["graveyard", "cards"]},
    "graveyard>tokens": {"archetype": "Graveyard tokens", "steps": ["graveyard", "tokens"]},
    "cards>life_loss": {"archetype": "Draw punisher", "steps": ["draw", "drain"]},
    "cards>tokens": {"archetype": "Draw tokens", "steps": ["draw", "tokens"]},
    "spells>tokens": {"archetype": "Spellslinger tokens", "steps": ["spells", "tokens"]},
    "spells>cards": {"archetype": "Spellslinger", "steps": ["spells", "cards"]},
    "spells>life_loss": {"archetype": "Spellslinger burn", "steps": ["spells", "drain"]},
    "spells>counters": {"archetype": "Spellslinger counters", "steps": ["spells", "counters"]},
    "spells>mana": {"archetype": "Spell rituals", "steps": ["spells", "mana"]},
    "life_gain>life_loss": {"archetype": "Lifegain drain", "steps": ["lifegain", "drain"]},
    "life_gain>counters": {"archetype": "Lifegain counters", "steps": ["lifegain", "counters"]},
    "life_gain>tokens": {"archetype": "Lifegain tokens", "steps": ["lifegain", "tokens"]},
    "life_gain>cards": {"archetype": "Lifegain draw", "steps": ["lifegain", "cards"]},
    "lands>tokens": {"archetype": "Landfall tokens", "steps": ["landfall", "tokens"]},
    "lands>life_loss": {"archetype": "Landfall drain", "steps": ["landfall", "drain"]},
    "lands>counters": {"archetype": "Landfall counters", "steps": ["landfall", "counters"]},
    "lands>mana": {"archetype": "Landfall ramp", "steps": ["landfall", "mana"]},
    "lands>cards": {"archetype": "Landfall draw", "steps": ["landfall", "cards"]},
    "artifacts>cards": {"archetype": "Artifact value", "steps": ["artifacts", "cards"]},
    "artifacts>mana": {"archetype": "Artifact ramp", "steps": ["artifacts", "mana"]},
    "artifacts>life_loss": {"archetype": "Artifact drain", "steps": ["artifacts", "drain"]},
    "artifacts>counters": {"archetype": "Artifact counters", "steps": ["artifacts", "counters"]},
    "artifacts>tokens": {"archetype": "Artifact tokens", "steps": ["artifacts", "tokens"]},
    "enchantments>cards": {"archetype": "Enchantress", "steps": ["enchantments", "cards"]},
    "enchantments>tokens": {"archetype": "Constellation tokens", "steps": ["enchantments", "tokens"]},
    "enchantments>life_loss": {"archetype": "Constellation drain", "steps": ["enchantments", "drain"]},
    "counters>cards": {"archetype": "Counters value", "steps": ["counters", "cards"]},
    "counters>life_loss": {"archetype": "Counters drain", "steps": ["counters", "drain"]},
    "sacrifice>cards": {"archetype": "Sac draw", "steps": ["sac", "cards"]},
    "sacrifice>tokens": {"archetype": "Sac tokens", "steps": ["sac", "tokens"]},
    "sacrifice>counters": {"archetype": "Sac counters", "steps": ["sac", "counters"]},
    "etb>attacks": {"archetype": "ETB haste", "steps": ["ETB", "attack"]},
}


def _path(resources: Iterable[str]) -> str:
    return ">".join(resources)


def catalog_entry(engine: dict) -> Optional[dict]:
    """Exact catalog match on the resource path; otherwise a 3-step chain
    takes its archetype from its first two steps (graveyard>etb>attacks is
    still Reanimator) with the extra step appended."""
    if engine.get("kind") != "chain":
        return None
    res = engine.get("resources") or []
    hit = CATALOG.get(_path(res))
    if hit:
        return hit
    if len(res) == 3:
        head = CATALOG.get(_path(res[:2]))
        if head:
            return {"archetype": head["archetype"], "steps": list(head["steps"]) + [SHORT.get(res[2], res[2])]}
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _index(graph: dict):
    nodes = {n["name"]: n for n in graph.get("nodes", [])}
    producers = defaultdict(set)
    consumers = defaultdict(set)
    for n in nodes.values():
        for k in n.get("produces", []):
            producers[k].add(n["name"])
        for k in n.get("consumes", []):
            consumers[k].add(n["name"])
    adj = defaultdict(dict)
    for e in graph.get("edges", []):
        adj[e["source"]][e["target"]] = e.get("reasons", [])
    return nodes, producers, consumers, adj


def _metrics(cards: List[str], adj) -> dict:
    s = set(cards)
    internal = sum(1 for a in s for b in adj.get(a, {}) if b in s)
    n = len(s)
    return {
        "card_count": n,
        "internal_edges": internal,
        "density": round(internal / (n * (n - 1)), 3) if n > 1 else 0.0,
    }


def _chain(resources: List[str], producers, consumers, adj, commanders) -> Optional[dict]:
    # A card "produces" artifacts/enchantments/spells by BEING one; that's a
    # type, not something a converter makes out of the previous resource.
    if any(k in TYPE_RESOURCES for k in resources[1:]):
        return None
    steps = []  # converter sets per step
    for a, b in zip(resources, resources[1:]):
        conv = consumers.get(a, set()) & producers.get(b, set())
        if not conv:
            return None
        steps.append(conv)
    all_conv = set().union(*steps)
    # one card can't be both converters in a 3-step chain
    if len(steps) == 2 and len(steps[0] | steps[1]) < 2:
        return None
    sources = producers.get(resources[0], set()) - all_conv
    if not sources:
        return None
    last = resources[-1]
    payoffs = consumers.get(last, set()) - all_conv
    if not payoffs and last not in VALUE_RANK:
        return None
    fuel = set()
    for fk in FUEL.get(resources[0], ()):
        # token makers fuel sacrifice OUTLETS (not board wipes); otherwise
        # require a real graph edge into a source on the fuel resource
        outlets = {s for s in sources if s in producers.get("sacrifice", set())}
        for f in producers.get(fk, set()):
            if outlets or any(fk in adj.get(f, {}).get(s, ()) for s in sources):
                fuel.add(f)
    fuel -= all_conv | sources

    converters = []
    for conv in steps:
        converters.extend(sorted(conv - set(converters)))
    cards = sorted(sources | all_conv | payoffs | fuel)
    paths = len(sources)
    for conv in steps:
        paths *= len(conv)
    paths *= max(1, len(payoffs))
    eng = {
        "signature": "chain:" + _path(resources),
        "kind": "chain",
        "resources": list(resources),
        "stages": {
            "fuel": sorted(fuel),
            "sources": sorted(sources),
            "converters": converters,
            "converter_steps": [sorted(c) for c in steps],
            "payoffs": sorted(payoffs),
        },
        "cards": cards,
        "commanders": [c for c in cards if c in commanders],
        "paths": paths,
    }
    eng.update(_metrics(cards, adj))
    eng["score"] = round(paths ** 0.5 * len(resources) + eng["internal_edges"] * 0.25 + 2 * len(eng["commanders"]), 2)
    return eng


def _loops(nodes, adj, commanders) -> List[dict]:
    groups: Dict[str, dict] = {}
    for a, b in combinations(sorted(nodes), 2):
        ab, ba = adj.get(a, {}).get(b), adj.get(b, {}).get(a)
        if not ab or not ba:
            continue
        res = sorted(set(ab) | set(ba))
        sig = "loop:" + "+".join(res)
        g = groups.setdefault(sig, {"signature": sig, "kind": "loop",
                                    "resources": [k for k in RESOURCE_KEYS if k in res], "cycles": []})
        g["cycles"].append([a, b])
    out = []
    for g in groups.values():
        cards = sorted({c for cyc in g["cycles"] for c in cyc})
        g["cards"] = cards
        g["commanders"] = [c for c in cards if c in commanders]
        g["paths"] = len(g["cycles"])
        g.update(_metrics(cards, adj))
        # a loop through two different resources is a real engine; a same-resource
        # loop (two attack-trigger cards that both grant attacks) is weaker
        g["score"] = round(len(g["cycles"]) * len(g["resources"]) + g["internal_edges"] * 0.1 + 2 * len(g["commanders"]), 2)
        out.append(g)
    return out


def detect_engines(graph: dict, max_chains: int = MAX_CHAINS, max_loops: int = MAX_LOOPS,
                   diverse: bool = True) -> List[dict]:
    """Chains (ranked, overlapping ones folded into `variants` unless
    diverse=False) followed by loops."""
    nodes, producers, consumers, adj = _index(graph)
    commanders = {n["name"] for n in nodes.values() if n.get("is_commander")}
    commanders |= set((graph.get("deck") or {}).get("commanders") or [])
    keys = [k for k in RESOURCE_KEYS if producers.get(k) or consumers.get(k)]

    chains = []
    for a in keys:
        for b in keys:
            if a == b:
                continue
            c2 = _chain([a, b], producers, consumers, adj, commanders)
            if c2:
                chains.append(c2)
            for c in keys:
                if c == b:
                    continue
                c3 = _chain([a, b, c], producers, consumers, adj, commanders)
                if c3:
                    chains.append(c3)

    # collapse chains that are the same cards over the same start/length
    # (Blood Artist drains AND gains life: keep the better-named one)
    best: Dict[tuple, tuple] = {}
    for e in chains:
        key = (len(e["resources"]), e["resources"][0], tuple(e["cards"]))
        rank = (e["score"], catalog_entry(e) is not None, VALUE_RANK.get(e["resources"][-1], 0))
        cur = best.get(key)
        if cur is None or rank > cur[0]:
            best[key] = (rank, e)
    ranked = sorted((e for _, e in best.values()), key=lambda e: (-e["score"], e["signature"]))
    chains = _diverse(ranked, max_chains) if diverse else ranked[:max_chains]
    loops = sorted(_loops(nodes, adj, commanders), key=lambda e: (-e["score"], e["signature"]))[:max_loops]
    return chains + loops


OVERLAP = 0.5


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def _diverse(ranked: List[dict], limit: int) -> List[dict]:
    """Greedy pick by score; a chain whose cards overlap a kept chain by
    >= OVERLAP (Jaccard) is folded into it as a variant instead of being
    listed as a separate engine."""
    kept: List[dict] = []
    for e in ranked:
        host = next((k for k in kept if _jaccard(k["cards"], e["cards"]) >= OVERLAP), None)
        if host is not None:
            host.setdefault("variants", []).append(e["signature"])
            continue
        if len(kept) < limit:
            kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def _steps(engine: dict, entry: Optional[dict]) -> List[str]:
    res = engine.get("resources") or []
    steps = list(entry["steps"]) if entry and entry.get("steps") else [SHORT.get(k, k) for k in res]
    fuel_keys = FUEL.get(res[0], ()) if res else ()
    if engine.get("kind") == "chain" and (engine.get("stages") or {}).get("fuel") and fuel_keys:
        steps = [SHORT.get(fuel_keys[0], fuel_keys[0])] + steps
    return steps


def _generic(engine: dict) -> str:
    if engine.get("kind") == "loop":
        return "Loop: " + " ↔ ".join(SHORT.get(k, k) for k in engine.get("resources") or [])
    words = _steps(engine, None)
    if not words:
        return engine.get("signature", "engine")
    words[0] = words[0][:1].upper() + words[0][1:]
    return " → ".join(words)


def _arrow(engine: dict, entry: Optional[dict]) -> str:
    if engine.get("kind") == "loop":
        return " ↔ ".join(SHORT.get(k, k) for k in engine.get("resources") or [])
    return " → ".join(_steps(engine, entry))


def _names(cards: List[str], n: int = 3) -> str:
    if not cards:
        return ""
    head = ", ".join(cards[:n])
    return head + (f" +{len(cards) - n} more" if len(cards) > n else "")


def summarize(engine: dict) -> str:
    """Deterministic one-paragraph description of the engine's stages."""
    if engine.get("kind") == "loop":
        labels = " and ".join(RESOURCE_BY_KEY[k].label.lower() for k in engine.get("resources") or [] if k in RESOURCE_BY_KEY)
        return (f"{len(engine.get('cycles', []))} card pair(s) feed each other through {labels}: "
                + "; ".join(" ⇄ ".join(c) for c in engine.get("cycles", [])[:4]))
    st = engine.get("stages") or {}
    res = engine.get("resources") or []
    parts = []
    if st.get("fuel"):
        parts.append(f"Fuel: {_names(st['fuel'])}")
    parts.append(f"{RESOURCE_BY_KEY[res[0]].produce} — {_names(st.get('sources', []))}")
    for i, conv in enumerate(st.get("converter_steps") or [st.get("converters", [])]):
        a, b = res[i], res[i + 1]
        parts.append(f"turn {RESOURCE_BY_KEY[a].label.lower()} into {RESOURCE_BY_KEY[b].label.lower()} — {_names(conv)}")
    if st.get("payoffs"):
        parts.append(f"payoff — {_names(st['payoffs'])}")
    return "; ".join(parts) + f". {engine.get('paths', 0)} path(s)."


def name_engines(engines: List[dict], cache: Optional[Dict[str, dict]] = None) -> List[dict]:
    """Attach archetype/name/name_source/summary. Precedence: catalog,
    cached (LLM) name for the signature, generic."""
    cache = cache or {}
    out = []
    for e in engines:
        e = dict(e)
        entry = catalog_entry(e)
        cached = cache.get(e.get("signature"))
        if entry:
            e["archetype"] = entry["archetype"]
            e["name_source"] = "catalog"
            e["name"] = f"{entry['archetype']}: {_arrow(e, entry)}"
        elif cached and cached.get("archetype"):
            e["archetype"] = cached["archetype"]
            e["name_source"] = cached.get("source", "llm")
            e["name"] = f"{cached['archetype']}: {_arrow(e, None)}"
            if cached.get("blurb"):
                e["blurb"] = cached["blurb"]
        else:
            e["archetype"] = None
            e["name_source"] = "generic"
            e["name"] = _generic(e)
        if e.get("stages") or e.get("cycles"):
            e["summary"] = summarize(e)
        out.append(e)
    return out


def unnamed_signatures(engines: List[dict], cache: Optional[Dict[str, dict]] = None) -> List[str]:
    cache = cache or {}
    seen = []
    for e in engines:
        sig = e.get("signature")
        if sig and sig not in seen and not catalog_entry(e) and sig not in cache:
            seen.append(sig)
    return seen


# ---------------------------------------------------------------------------
# LLM naming (labels only)
# ---------------------------------------------------------------------------

NAMING_SYSTEM = (
    "You name Magic: The Gathering Commander deck engines. Each engine is a resource chain "
    "detected from card text (cards that MAKE a resource, cards that SPEND it to make the next one, "
    "and payoffs) or a two-card loop. Give each a short archetype name in the style players use "
    "(2-3 words, e.g. 'Aristocrats', 'Reanimator', 'Enchantress', 'Landfall tokens') and a "
    "one-sentence blurb. Return JSON only: "
    "{\"engines\": [{\"signature\": <exact signature given>, \"archetype\": str, \"blurb\": str}]}"
)

NAMING_SCHEMA = {
    "type": "OBJECT",
    "properties": {"engines": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"signature": {"type": "STRING"}, "archetype": {"type": "STRING"},
                       "blurb": {"type": "STRING"}},
        "required": ["signature", "archetype", "blurb"],
    }}},
    "required": ["engines"],
}

MAX_SIGNATURES_PER_CALL = 40


def naming_prompt(engines: List[dict]) -> str:
    lines = []
    for e in engines:
        res = " -> ".join(RESOURCE_BY_KEY[k].label for k in e.get("resources", []) if k in RESOURCE_BY_KEY)
        st = e.get("stages") or {}
        example = st.get("converters") or [c for cyc in e.get("cycles", []) for c in cyc]
        lines.append(f"- signature: {e['signature']}\n  kind: {e.get('kind')}\n  resources: {res}\n"
                     f"  example cards: {', '.join((example or e.get('cards') or [])[:4])}")
    return "Name these engines:\n" + "\n".join(lines)


def llm_name_signatures(chain, engines: List[dict], on_call: Optional[Callable[[dict], None]] = None) -> Dict[str, dict]:
    """One LLM call (Gemini -> Nous fallback via the AN-1 chain) for up to
    MAX_SIGNATURES_PER_CALL engine signatures. Returns {signature: name record}
    for the signatures the model answered; others stay unnamed."""
    by_sig = {}
    for e in engines:
        by_sig.setdefault(e["signature"], e)
    todo = list(by_sig.values())[:MAX_SIGNATURES_PER_CALL]
    if not todo or chain is None:
        return {}
    data, provider, model = chain.generate_json(NAMING_SYSTEM, naming_prompt(todo), schema=NAMING_SCHEMA,
                                                purpose="engine_names", on_call=on_call)
    if not isinstance(data, dict):
        return {}
    out = {}
    for item in data.get("engines") or []:
        if not isinstance(item, dict):
            continue
        sig = item.get("signature")
        arch = (item.get("archetype") or "").strip()
        if sig not in by_sig or not arch:
            continue
        out[sig] = {"archetype": arch[:40], "blurb": (item.get("blurb") or "").strip()[:240],
                    "source": "llm", "provider": provider, "model": model}
    return out


def dumps(engines: List[dict]) -> str:  # pragma: no cover - debugging helper
    return json.dumps(engines, indent=1)
