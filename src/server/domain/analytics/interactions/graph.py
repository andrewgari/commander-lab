"""
Interaction graph construction (pure functions, no I/O).

Edge A -> B exists when A PRODUCES a resource B CONSUMES. reasons = the
shared resource keys, weight = len(reasons). No self loops.

Deck graphs are materialized in full (nodes + edges). The collection graph
can have hundreds of thousands of edges, so it's stored as nodes with
degrees plus a resource index (producers/consumers per resource); edges
for any card are derived on demand from the index (collection_neighbors).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from .vocab import RESOURCE_BY_KEY, RESOURCE_KEYS

TOP_N = 10


def _tags_for(name: str, tags: Dict[str, dict]) -> dict:
    rec = tags.get(name) or {}
    return {
        "produces": [k for k in rec.get("produces", []) if k in RESOURCE_BY_KEY],
        "consumes": [k for k in rec.get("consumes", []) if k in RESOURCE_BY_KEY],
        "source": rec.get("source") or ("missing" if not rec else "unknown"),
    }


def _edge_label(reasons: List[str]) -> str:
    return ", ".join(RESOURCE_BY_KEY[k].label for k in reasons)


def _explain(src: str, dst: str, key: str) -> str:
    res = RESOURCE_BY_KEY[key]
    return f"{src} {res.produce}; {dst} {res.consume}"


def build_graph(names: Iterable[str], tags: Dict[str, dict], meta: Optional[dict] = None,
                commanders: Iterable[str] = ()) -> dict:
    names = sorted({n for n in names if n})
    commanders = set(commanders or ())
    t = {n: _tags_for(n, tags) for n in names}

    producers = defaultdict(list)
    for n in names:
        for k in t[n]["produces"]:
            producers[k].append(n)

    pair_reasons: Dict[tuple, List[str]] = defaultdict(list)
    for dst in names:
        for k in t[dst]["consumes"]:
            for src in producers.get(k, ()):
                if src != dst:
                    pair_reasons[(src, dst)].append(k)

    order = {k: i for i, k in enumerate(RESOURCE_KEYS)}
    edges = []
    in_deg = defaultdict(int)
    out_deg = defaultdict(int)
    in_w = defaultdict(int)
    out_w = defaultdict(int)
    reason_counts = defaultdict(lambda: defaultdict(lambda: {"in": 0, "out": 0}))
    neighbors = defaultdict(set)
    for (src, dst), reasons in sorted(pair_reasons.items()):
        reasons = sorted(set(reasons), key=lambda k: order[k])
        w = len(reasons)
        edges.append({
            "source": src,
            "target": dst,
            "reasons": reasons,
            "weight": w,
            "label": _edge_label(reasons),
            "explanations": [_explain(src, dst, k) for k in reasons],
        })
        out_deg[src] += 1
        in_deg[dst] += 1
        out_w[src] += w
        in_w[dst] += w
        neighbors[src].add(dst)
        neighbors[dst].add(src)
        for k in reasons:
            reason_counts[src][k]["out"] += 1
            reason_counts[dst][k]["in"] += 1

    nodes = []
    for n in names:
        nodes.append({
            "name": n,
            "is_commander": n in commanders,
            "produces": t[n]["produces"],
            "consumes": t[n]["consumes"],
            "tag_source": t[n]["source"],
            "in_degree": in_deg[n],
            "out_degree": out_deg[n],
            "degree": in_deg[n] + out_deg[n],
            "weighted_degree": in_w[n] + out_w[n],
            "neighbors": len(neighbors[n]),
            "reason_counts": {k: dict(v) for k, v in sorted(reason_counts[n].items(), key=lambda kv: order[kv[0]])},
        })

    top = sorted(nodes, key=lambda x: (-x["degree"], -x["weighted_degree"], x["name"]))[:TOP_N]
    top_connected = [{
        "name": x["name"],
        "degree": x["degree"],
        "in_degree": x["in_degree"],
        "out_degree": x["out_degree"],
        "weighted_degree": x["weighted_degree"],
        "reasons": [
            {"resource": k, "label": RESOURCE_BY_KEY[k].label, "in": v["in"], "out": v["out"]}
            for k, v in sorted(x["reason_counts"].items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))
        ],
    } for x in top if x["degree"] > 0]

    resources_used = sorted({k for e in edges for k in e["reasons"]}, key=lambda k: order[k])
    labels = {k: RESOURCE_BY_KEY[k].label for k in RESOURCE_KEYS}
    untagged = sum(1 for n in names if t[n]["source"] in ("missing", "pending", "unknown"))

    return {
        **(meta or {}),
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "total_weight": sum(e["weight"] for e in edges),
            "isolated": sum(1 for x in nodes if x["degree"] == 0),
            "untagged": untagged,
        },
        "labels": labels,
        "resources_used": resources_used,
        "nodes": nodes,
        "edges": edges,
        "top_connected": top_connected,
    }


def build_collection_graph(names: Iterable[str], tags: Dict[str, dict], meta: Optional[dict] = None) -> dict:
    """Whole-collection graph as nodes (with degrees) + resource index.
    Degrees are computed exactly without materializing every edge."""
    names = sorted({n for n in names if n})
    t = {n: _tags_for(n, tags) for n in names}
    index = {k: {"producers": [], "consumers": []} for k in RESOURCE_KEYS}
    for n in names:
        for k in t[n]["produces"]:
            index[k]["producers"].append(n)
        for k in t[n]["consumes"]:
            index[k]["consumers"].append(n)
    prod_sets = {k: set(v["producers"]) for k, v in index.items()}
    cons_sets = {k: set(v["consumers"]) for k, v in index.items()}

    nodes = []
    edge_count = 0
    total_weight = 0
    for n in names:
        outs, ins = set(), set()
        w_out = w_in = 0
        for k in t[n]["produces"]:
            targets = cons_sets[k] - {n}
            outs |= targets
            w_out += len(targets)
        for k in t[n]["consumes"]:
            sources = prod_sets[k] - {n}
            ins |= sources
            w_in += len(sources)
        edge_count += len(outs)
        total_weight += w_out
        nodes.append({
            "name": n,
            "produces": t[n]["produces"],
            "consumes": t[n]["consumes"],
            "tag_source": t[n]["source"],
            "in_degree": len(ins),
            "out_degree": len(outs),
            "degree": len(ins) + len(outs),
            "weighted_degree": w_in + w_out,
        })
    return {
        **(meta or {}),
        "counts": {
            "nodes": len(nodes),
            "edges": edge_count,
            "total_weight": total_weight,
            "isolated": sum(1 for x in nodes if x["degree"] == 0),
            "untagged": sum(1 for n in names if t[n]["source"] in ("missing", "pending", "unknown")),
        },
        "labels": {k: RESOURCE_BY_KEY[k].label for k in RESOURCE_KEYS},
        "index": index,
        "nodes": nodes,
        "top_connected": [
            {"name": x["name"], "degree": x["degree"], "weighted_degree": x["weighted_degree"]}
            for x in sorted(nodes, key=lambda x: (-x["degree"], x["name"]))[:TOP_N]
        ],
    }


def collection_neighbors(coll: dict, name: str) -> dict:
    """Labeled in/out edges for one card from a collection graph's index."""
    node = next((x for x in coll.get("nodes", []) if x["name"] == name), None)
    if node is None:
        return {"name": name, "in": [], "out": []}
    index = coll.get("index", {})
    order = {k: i for i, k in enumerate(RESOURCE_KEYS)}
    outs = defaultdict(list)
    ins = defaultdict(list)
    for k in node["produces"]:
        for dst in index.get(k, {}).get("consumers", []):
            if dst != name:
                outs[dst].append(k)
    for k in node["consumes"]:
        for src in index.get(k, {}).get("producers", []):
            if src != name:
                ins[src].append(k)

    def fmt(d, out):
        rows = []
        for other, reasons in d.items():
            reasons = sorted(set(reasons), key=lambda k: order[k])
            rows.append({"source": name if out else other, "target": other if out else name,
                         "reasons": reasons, "weight": len(reasons), "label": _edge_label(reasons)})
        rows.sort(key=lambda e: (-e["weight"], e["target"] if out else e["source"]))
        return rows

    return {"name": name, "out": fmt(outs, True), "in": fmt(ins, False)}
