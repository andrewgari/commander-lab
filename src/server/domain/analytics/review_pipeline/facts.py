"""The fact index: the set of facts an LLM may cite, rendered for prompts.

Built from the deterministic fact pack (``analytics/fact_pack.py``) plus a few counts computed by
code. Every renderable leaf has a dotted *fact key*; findings cite those keys as evidence and the
pipeline drops any finding whose key is not in the index (CONTRACT §2.1, card 08). Unavailable
nodes are listed as such and are NOT citable.

Key conventions
---------------
* dicts descend by key; lists of dicts descend by an identifying field (``role``, ``rule``, ``id``,
  ``mv``, ``color``, ``name``), so ``roles.ramp.count`` and ``edhrec.present.Sol Ring.inclusion``
  are keys. Names may contain dots; keys are matched as whole strings, never split.
* ``card.<exact deck name>.<field>`` for per-card facts (price_usd, cmc, type_line,
  edhrec_inclusion, edhrec_synergy, game_changer).
* ``derived.*`` for counts computed here (type counts, price totals).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from src.server.domain.analytics.review_pipeline.names import normalize

MAX_NEAR_MISSES = 10
CARD_TABLE_TEXT_CHARS = 320
BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest", "wastes", "snow-covered plains",
               "snow-covered island", "snow-covered swamp", "snow-covered mountain", "snow-covered forest"}


@dataclass(frozen=True)
class BudgetCaps:
    """Per-card USD caps (profile.budget_norms with deck intent applied). None = no cap set."""
    soft: float | None = None
    hard: float | None = None

_ID_FIELDS = ("role", "rule", "id", "mv", "color", "name", "tag")
_SKIP_KEYS = {"source", "fetched_at", "generated_at", "url", "oracle_id", "schema", "norm_source"}
_SKIP_TOP = {"cards", "charts", "schema", "generated_at"}   # per-card data is exposed via card.* / the card table
_TYPES = ("creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker", "battle", "land")
_UNAVAILABLE = "UNAVAILABLE"


def _is_unavail(x: Any) -> bool:
    return isinstance(x, Mapping) and x.get("status") == "unavailable"


def _front(type_line: str) -> str:
    return (type_line or "").split("//")[0]


def _owned_rec(owned: Mapping[str, Mapping] | None, name: str) -> Mapping | None:
    if not owned:
        return None
    for key in (normalize(name), normalize(name.split(" // ")[0])):
        if key in owned:
            return owned[key]
    return None


def _prepare(pack: Mapping[str, Any], deck_names: set[str]) -> dict[str, Any]:
    """Prompt-facing copy of the pack: noise removed, and no card name that is not in the deck.

    Absent-card names (EDHREC staples the deck lacks, the missing half of a near-miss combo) are
    withheld on purpose: recommendations come only from the candidate pool (CONTRACT §2.3), and an
    analysis pass that sees them will suggest them."""
    out = copy.deepcopy(dict(pack))
    combos = out.get("combos")
    if isinstance(combos, Mapping) and not _is_unavail(combos):
        for c in combos.get("complete") or []:
            for k in ("popularity", "identity"):
                c.pop(k, None)
        near = sorted(combos.get("near_misses") or [], key=lambda c: (-(c.get("popularity") or 0), str(c.get("id"))))
        kept = []
        for c in near[:MAX_NEAR_MISSES]:
            names = c.get("card_names") or []
            kept.append({"id": c.get("id"), "present_cards": [n for n in names if n in deck_names],
                         "missing_count": len(c.get("missing") or []), "produces": c.get("produces") or [],
                         "bracket_tag": c.get("bracket_tag")})
        combos["near_misses"] = kept
    ed = out.get("edhrec")
    if isinstance(ed, Mapping) and not _is_unavail(ed):
        for row in ed.get("present") or []:
            row.pop("num_decks", None)
        ed["high_synergy_absent_count"] = len(ed.pop("high_synergy_absent", None) or [])
    return out


class FactIndex:
    def __init__(self) -> None:
        self.leaves: dict[str, Any] = {}          # key -> value (insertion ordered)
        self.nodes: set[str] = set()              # every non-leaf path that exists
        self.unavailable: dict[str, str] = {}     # path -> reason

    # ---- construction ------------------------------------------------------------------
    @classmethod
    def build(cls, pack: Mapping[str, Any], *, deck_cards: Sequence[tuple[str, int]],
              caps: BudgetCaps | None = None, owned: Mapping[str, Mapping] | None = None) -> "FactIndex":
        """``deck_cards``: (exact deck name, quantity) for every card in the deck incl. commanders.
        ``caps``: budget caps, or None when none are set. ``owned``: ``{name_key: {copies, available,
        decks}}`` as built by ``candidates.owned_from_rows``, or None when inventory is unavailable."""
        idx = cls()
        deck_names = {n for n, _ in deck_cards}
        prepared = _prepare(pack, deck_names)
        for top, value in prepared.items():
            if top in _SKIP_TOP:
                continue
            idx._walk(top, value)
        by_name = {c.get("name"): c for c in pack.get("cards", []) if isinstance(c, Mapping)}
        idx._add_card_keys(by_name, deck_cards, owned)
        idx._add_derived(by_name, deck_cards, caps, owned)
        return idx

    def _walk(self, path: str, value: Any) -> None:
        if _is_unavail(value):
            self.unavailable[path] = str(value.get("reason") or "unavailable")
            return
        if isinstance(value, Mapping):
            self.nodes.add(path)
            for k, v in value.items():
                if k in _SKIP_KEYS:
                    continue
                # "roles.roles" reads badly as a fact key: collapse a child that repeats its parent's name.
                child = path if path.rsplit(".", 1)[-1] == k else f"{path}.{k}"
                self._walk(child, v)
            return
        if isinstance(value, list):
            ident = self._ident_field(value)
            if ident is None:
                self.leaves[path] = value
                return
            self.nodes.add(path)
            bare = True
            for item in value:
                rest = {k: v for k, v in item.items() if k != ident and k not in _SKIP_KEYS}
                if rest:
                    bare = False
                    self._walk(f"{path}.{item[ident]}", rest)
            if bare:          # a list of bare names / refs: cite the list as a whole
                self.nodes.discard(path)
                self.leaves[path] = [item[ident] for item in value]
            return
        self.leaves[path] = value

    @staticmethod
    def _ident_field(items: list) -> str | None:
        if not items or not all(isinstance(i, Mapping) for i in items):
            return None
        for f in _ID_FIELDS:
            if all(f in i and not isinstance(i[f], (dict, list)) for i in items):
                vals = [str(i[f]) for i in items]
                if len(set(vals)) == len(vals):
                    return f
        return None

    def _add_card_keys(self, by_name: Mapping[str, Mapping], deck_cards: Sequence[tuple[str, int]],
                       owned: Mapping[str, Mapping] | None) -> None:
        for name, _qty in deck_cards:
            c = by_name.get(name)
            if not c:
                continue
            base = f"card.{name}"
            self.nodes.add(base)
            price = self._price(c)
            fields: list[tuple[str, Any]] = [("cmc", c.get("cmc")), ("type_line", c.get("type_line")), ("price_usd", price)]
            ed = c.get("edhrec")
            if isinstance(ed, Mapping) and not _is_unavail(ed) and ed.get("potential_decks"):
                fields.append(("edhrec_inclusion", round(ed["num_decks"] / ed["potential_decks"], 4)))
                if isinstance(ed.get("synergy"), (int, float)):
                    fields.append(("edhrec_synergy", round(ed["synergy"], 4)))
            if isinstance(c.get("game_changer"), bool):
                fields.append(("game_changer", c["game_changer"]))
            salt = c.get("salt")
            if isinstance(salt, (int, float)) and not isinstance(salt, bool):
                fields.append(("salt", salt))
            rec = _owned_rec(owned, name)
            if rec:
                fields.append(("owned_copies", rec.get("copies", 0)))
            for f, v in fields:
                if v is None or _is_unavail(v):
                    self.unavailable[f"{base}.{f}"] = "unavailable"
                else:
                    self.leaves[f"{base}.{f}"] = v

    @staticmethod
    def _price(card: Mapping) -> float | None:
        p = card.get("price")
        if p in (None, "") or _is_unavail(p):
            return None
        try:
            return float(p)
        except (TypeError, ValueError):
            return None

    def _add_derived(self, by_name: Mapping[str, Mapping], deck_cards: Sequence[tuple[str, int]],
                     caps: BudgetCaps | None, owned: Mapping[str, Mapping] | None) -> None:
        counts = {t: 0 for t in _TYPES}
        total, unpriced, priced = 0.0, [], []
        for name, qty in deck_cards:
            c = by_name.get(name)
            if not c:
                continue
            tl = c.get("type_line")
            if isinstance(tl, str):
                low = _front(tl).split("—")[0].lower()
                for t in _TYPES:
                    if re.search(rf"\b{t}\b", low):
                        counts[t] += qty
            p = self._price(c)
            if p is None:
                unpriced.append(name)
            else:
                total += p * qty
                priced.append((p, name))
        self.nodes.add("derived")
        self.nodes.add("derived.type_counts")
        for t, n in counts.items():
            self.leaves[f"derived.type_counts.{t}"] = n
        self.nodes.add("derived.price")
        self.leaves["derived.price.total_usd"] = round(total, 2)
        self.leaves["derived.price.unpriced_cards"] = sorted(unpriced)
        priced.sort(key=lambda x: (-x[0], x[1]))
        self.nodes.add("derived.price.most_expensive")
        for p, n in priced[:8]:
            self.nodes.add(f"derived.price.most_expensive.{n}")
            self.leaves[f"derived.price.most_expensive.{n}.usd"] = p
        price_of = {n: p for p, n in priced}
        for label, cap in (("soft", caps.soft if caps else None), ("hard", caps.hard if caps else None)):
            if cap is None:
                self.unavailable[f"derived.price.over_{label}_cap"] = f"no {label} budget cap set in the profile or deck intent"
            else:
                self.leaves[f"derived.price.{label}_cap_usd"] = cap
                self.leaves[f"derived.price.over_{label}_cap"] = sorted(n for n, p in price_of.items() if p > cap)
        self.nodes.add("derived.inventory")
        if owned is None:
            self.nodes.discard("derived.inventory")
            self.unavailable["derived.inventory"] = "inventory not provided for this review"
            return
        names = [n for n, _ in deck_cards if normalize(n) not in BASIC_LANDS and n in by_name]
        have = [n for n in names if _owned_rec(owned, n)]
        lack = [n for n in names if not _owned_rec(owned, n)]
        self.leaves["derived.inventory.owned_cards"] = sorted(have)
        self.leaves["derived.inventory.owned_count"] = len(have)
        self.leaves["derived.inventory.not_owned_count"] = len(lack)
        self.leaves["derived.inventory.not_owned_cards"] = sorted(lack)
        self.leaves["derived.inventory.not_owned_price_usd"] = round(sum(price_of.get(n, 0.0) for n in lack), 2)
        self.leaves["derived.inventory.not_owned_unpriced"] = sorted(n for n in lack if n not in price_of)

    # ---- queries -------------------------------------------------------------------------
    @property
    def keys(self) -> list[str]:
        return list(self.leaves)

    def exists(self, key: str) -> bool:
        return bool(key) and (key in self.leaves or key in self.nodes)

    def get(self, key: str) -> Any:
        return self.leaves[key]

    @staticmethod
    def _under(key: str, prefix: str) -> bool:
        if "*" in prefix:
            rx = r"[^.]+".join(re.escape(part) for part in prefix.split("*"))
            return re.match(rx + r"(\.|$)", key) is not None
        return key == prefix or key.startswith(prefix + ".")

    def unavailable_reason(self, key: str) -> str | None:
        """Reason if ``key`` is, or lies under, a node the data source marked unavailable."""
        for path, reason in self.unavailable.items():
            if key == path or key.startswith(path + "."):
                return reason
        return None

    def render(self, prefixes: Iterable[str]) -> str:
        """Lines ``key = value`` for every leaf under any prefix, plus explicit UNAVAILABLE lines.
        Deterministic: same index, same bytes."""
        pref = list(prefixes)
        lines: list[str] = []
        for k, v in self.leaves.items():
            if any(self._under(k, p) for p in pref):
                lines.append(f"{k} = {self._fmt(v)}")
        for k, reason in sorted(self.unavailable.items()):
            if any(self._under(k, p) for p in pref):
                lines.append(f"{k} = {_UNAVAILABLE} ({reason})")
        return "\n".join(lines)

    @staticmethod
    def _fmt(v: Any) -> str:
        if isinstance(v, list):
            return "[" + "; ".join(str(x) for x in v) + "]"
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)


# ---------------------------------------------------------------------------------------------
# Card table: the deck's own cards with their oracle text, so the model never recalls card text.
# ---------------------------------------------------------------------------------------------
def render_card_table(pack: Mapping[str, Any], deck_cards: Sequence[tuple[str, int]], *,
                      owned: Mapping[str, Mapping] | None, commanders: Sequence[str]) -> str:
    by_name = {c.get("name"): c for c in pack.get("cards", []) if isinstance(c, Mapping)}
    roles_of: dict[str, list[str]] = {}
    roles = pack.get("roles")
    if isinstance(roles, Mapping) and not _is_unavail(roles):
        for r in roles.get("roles") or []:
            for n in r.get("cards") or []:
                roles_of.setdefault(n, []).append(r.get("role"))
    cmd = set(commanders)
    head = "NAME | MV | TYPE | ROLES | PRICE | " + ("OWNED | " if owned is not None else "") + "ORACLE TEXT"
    lines = [head]
    for name, qty in deck_cards:
        c = by_name.get(name)
        if c is None:
            lines.append(f"{name} | data unavailable")
            continue
        price = FactIndex._price(c)
        mv = c.get("cmc")
        text = c.get("oracle_text")
        text = text if isinstance(text, str) else "oracle text unavailable"
        text = re.sub(r"\s+", " ", text).strip().replace("|", "/")
        if len(text) > CARD_TABLE_TEXT_CHARS:
            text = text[:CARD_TABLE_TEXT_CHARS].rstrip() + "…"
        tl = c.get("type_line") if isinstance(c.get("type_line"), str) else "type n/a"
        cols = [
            name + (" [COMMANDER]" if name in cmd else "") + (f" x{qty}" if qty > 1 else ""),
            "mv n/a" if not isinstance(mv, (int, float)) else f"{mv:g}",
            tl,
            ",".join(sorted(roles_of.get(name, []))) or "-",
            f"${price:.2f}" if price is not None else "price n/a",
        ]
        if owned is not None:
            rec = _owned_rec(owned, name)
            cols.append(f"owned x{rec.get('copies', 0)}" if rec else "not owned")
        cols.append(text)
        lines.append(" | ".join(cols))
    return "\n".join(lines)
