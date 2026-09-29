"""Multi-format Commander decklist parser.

Parses MTG Arena, MTGO, and plain-text decklist formats into normalized
structured card objects with at minimum ``quantity``, ``name``, and ``section``
fields.

Supported formats:
- MTG Arena (with set codes, collector numbers, and section headers like
  ``Deck``, ``Commander``, ``Sideboard``).
- MTGO (with section headers like ``// Commander``, ``// Deck``,
  ``// Sideboard``, or ``Sideboard``, and ``SB:`` line prefixes).
- Plain text (simple ``N CardName`` lines, bare card names, ``Commander:``
  headers/prefixes, and ``CMDR`` tags).
- Split cards (e.g., ``Fire // Ice``) preserving full split names.
- Gracefully skips blank lines, comment lines, and unrecognized formatting.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class DeckSection:
    COMMANDER = "commander"
    MAINBOARD = "mainboard"
    SIDEBOARD = "sideboard"

    VALID = {COMMANDER, MAINBOARD, SIDEBOARD}


class ParsedCard(dict):
    """Normalized structured card object.

    Subclasses ``dict`` so it is directly serializable, behaves as
    ``{"name": str, "quantity": int, "section": str, ...}``, and also
    supports attribute access (``.name``, ``.quantity``, ``.section``).
    """

    def __init__(
        self,
        name: str,
        quantity: int,
        section: str = DeckSection.MAINBOARD,
        set_code: Optional[str] = None,
        collector_number: Optional[str] = None,
        foil: bool = False,
        **kwargs: Any,
    ):
        data: Dict[str, Any] = {
            "name": name,
            "quantity": quantity,
            "section": section,
            "set_code": set_code,
            "collector_number": collector_number,
            "foil": foil,
        }
        data.update(kwargs)
        super().__init__(data)

    @property
    def name(self) -> str:
        return self["name"]

    @name.setter
    def name(self, val: str) -> None:
        self["name"] = val

    @property
    def quantity(self) -> int:
        return self["quantity"]

    @quantity.setter
    def quantity(self, val: int) -> None:
        self["quantity"] = val

    @property
    def section(self) -> str:
        return self["section"]

    @section.setter
    def section(self, val: str) -> None:
        self["section"] = val

    @property
    def set_code(self) -> Optional[str]:
        return self.get("set_code")

    @property
    def collector_number(self) -> Optional[str]:
        return self.get("collector_number")

    @property
    def foil(self) -> bool:
        return bool(self.get("foil", False))

    def __repr__(self) -> str:
        parts = [f"name={self.name!r}", f"quantity={self.quantity}", f"section={self.section!r}"]
        if self.set_code:
            parts.append(f"set_code={self.set_code!r}")
        if self.collector_number:
            parts.append(f"collector_number={self.collector_number!r}")
        if self.foil:
            parts.append(f"foil={self.foil}")
        return f"ParsedCard({', '.join(parts)})"


class ParsedDeck(list):
    """Collection of ParsedCard items representing a parsed deck.

    Subclasses ``list`` so it can be indexed, sliced, iterated, and compared
    like a normal list of card objects. Also provides convenient section-level
    accessors and dict mapping.
    """

    @property
    def commander(self) -> List[ParsedCard]:
        return [c for c in self if c.get("section") == DeckSection.COMMANDER]

    @property
    def mainboard(self) -> List[ParsedCard]:
        return [c for c in self if c.get("section") == DeckSection.MAINBOARD]

    @property
    def sideboard(self) -> List[ParsedCard]:
        return [c for c in self if c.get("section") == DeckSection.SIDEBOARD]

    @property
    def cards(self) -> List[ParsedCard]:
        return list(self)

    @property
    def by_section(self) -> Dict[str, List[ParsedCard]]:
        return {
            DeckSection.COMMANDER: self.commander,
            DeckSection.MAINBOARD: self.mainboard,
            DeckSection.SIDEBOARD: self.sideboard,
        }

    def to_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            DeckSection.COMMANDER: [dict(c) for c in self.commander],
            DeckSection.MAINBOARD: [dict(c) for c in self.mainboard],
            DeckSection.SIDEBOARD: [dict(c) for c in self.sideboard],
        }

    def get(self, section: str, default: Any = None) -> Any:
        try:
            return self[section]
        except KeyError:
            return default

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            key = item.lower()
            if key in (DeckSection.COMMANDER, "cmdr"):
                return self.commander
            elif key in (DeckSection.MAINBOARD, "main", "deck", "maindeck"):
                return self.mainboard
            elif key in (DeckSection.SIDEBOARD, "side", "sb"):
                return self.sideboard
            raise KeyError(f"Unknown section: {item}")
        return super().__getitem__(item)

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, str) and item.lower() in (
            DeckSection.COMMANDER, "cmdr",
            DeckSection.MAINBOARD, "main", "deck", "maindeck",
            DeckSection.SIDEBOARD, "side", "sb",
        ):
            return True
        return super().__contains__(item)


# Standalone section headers
_COMMANDER_HEADER_RE = re.compile(
    r"^(?://|#)?\s*(?:\[\s*)?(?:(?:\d+\s+)?(?:commander|commanders|cmdr|general|generals|leader))\s*(?:\])?\s*:?\s*$",
    re.IGNORECASE,
)
_SIDEBOARD_HEADER_RE = re.compile(
    r"^(?://|#)?\s*(?:\[\s*)?(?:(?:\d+\s+)?(?:sideboard|sideboards|side\b|sb\b))\s*(?:\])?\s*:?\s*$",
    re.IGNORECASE,
)
_MAINBOARD_HEADER_RE = re.compile(
    r"^(?://|#)?\s*(?:\[\s*)?(?:(?:\d+\s+)?(?:deck|mainboard|maindeck|main\b|library))\s*(?:\])?\s*:?\s*$",
    re.IGNORECASE,
)

# Inline line prefixes
_INLINE_COMMANDER_PREFIX_RE = re.compile(
    r"^(?:commander|commanders|cmdr|general)\s*:\s*(.+)$|^\[(?:commander|commanders|cmdr|general)\]\s+(.+)$",
    re.IGNORECASE,
)
_INLINE_SIDEBOARD_PREFIX_RE = re.compile(
    r"^(?:sideboard|side|sb)\s*:\s*(.+)$|^\[(?:sideboard|side|sb)\]\s+(.+)$",
    re.IGNORECASE,
)
_INLINE_MAINBOARD_PREFIX_RE = re.compile(
    r"^(?:deck|mainboard|maindeck|main)\s*:\s*(.+)$|^\[(?:deck|mainboard|maindeck|main)\]\s+(.+)$",
    re.IGNORECASE,
)

# Quantity extraction: "1 Sol Ring", "1x Sol Ring", "1X Sol Ring", "1. Sol Ring", "1 - Sol Ring", "-1 Card"
_QTY_PREFIX_RE = re.compile(r"^([+-]?\d+)\s*(?:[xX]|\.|\-)?\s+(.+)$")

# CMDR tags: *CMDR*, [CMDR], (CMDR), *Commander*, [Commander], (Commander), or trailing " CMDR"
_CMDR_TAG_RE = re.compile(
    r"[\*\[\(]\s*(?:cmdr|commander)\s*[\*\]\)]|\bcmdr\b\s*$",
    re.IGNORECASE,
)

# SB tags: *SB*, [SB], (SB), *Sideboard*, [Sideboard], (Sideboard), or trailing " SB"
_SB_TAG_RE = re.compile(
    r"[\*\[\(]\s*(?:sb|sideboard)\s*[\*\]\)]|\bsb\b\s*$",
    re.IGNORECASE,
)

# Foil tags: *F*, *Foil*, [F], [Foil], (F), (Foil), or trailing standalone " Foil" / " F"
_FOIL_TAG_RE = re.compile(
    r"[\*\[\(]\s*(?:foil|f)\s*[\*\]\)]|\b(?:foil|f)\b\s*$",
    re.IGNORECASE,
)

# Set code & collector number:
# e.g. (NEO) 123, (NEO) 123a, (NEO:123), [NEO:123], [NEO] 123, (NEO), (40K) 215
_SET_COLL_RE = re.compile(
    r"\s*(?:"
    r"\((?P<set1>[A-Za-z0-9_]{2,6})(?:[:\s]+(?P<num1>[A-Za-z0-9★#/\-]+))?\)"
    r"|"
    r"\[(?P<set2>[A-Za-z0-9_]{2,6})(?:[:\s]+(?P<num2>[A-Za-z0-9★#/\-]+))?\]"
    r")"
    r"(?:\s+(?P<num3>[A-Za-z0-9★#/\-]+))?\s*$"
)

# Category bracket suffix, e.g. "Sol Ring [Ramp]" -> "Sol Ring"
_BRACKET_CATEGORY_RE = re.compile(r"\s*\[[^\]]*\]\s*$")

# Split card separator normalizer
_SPLIT_CARD_RE = re.compile(r"\s*//\s*")


def parse_decklist(text: str) -> ParsedDeck:
    """Parse raw decklist text into a normalized ``ParsedDeck`` of ``ParsedCard`` items.

    Handles MTG Arena, MTGO, and plain-text formats.
    Returns a ``ParsedDeck`` list containing ``ParsedCard`` dicts with at minimum
    ``name``, ``quantity``, and ``section`` fields.
    """
    deck = ParsedDeck()
    if not text or not isinstance(text, str):
        return deck

    current_section = DeckSection.MAINBOARD

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Check for section headers
        if _COMMANDER_HEADER_RE.match(line):
            current_section = DeckSection.COMMANDER
            continue
        if _SIDEBOARD_HEADER_RE.match(line):
            current_section = DeckSection.SIDEBOARD
            continue
        if _MAINBOARD_HEADER_RE.match(line):
            current_section = DeckSection.MAINBOARD
            continue

        # Skip comment lines that are not section headers (e.g. "// Lands", "# Notes")
        if line.startswith("//") or line.startswith("#"):
            continue

        # Skip markdown decorative lines, HTML tags, or JSON-like blocks
        if line.startswith("<") or line.endswith(">"):
            continue
        if line.startswith("{") or line.endswith("}"):
            continue
        if re.match(r"^(=+|-+|\*+|_+|~+)", line) and not _QTY_PREFIX_RE.match(line):
            continue

        # Check for inline line prefixes
        card_section = current_section
        cmdr_prefix = _INLINE_COMMANDER_PREFIX_RE.match(line)
        if cmdr_prefix:
            card_section = DeckSection.COMMANDER
            line = (cmdr_prefix.group(1) or cmdr_prefix.group(2)).strip()
        else:
            sb_prefix = _INLINE_SIDEBOARD_PREFIX_RE.match(line)
            if sb_prefix:
                card_section = DeckSection.SIDEBOARD
                line = (sb_prefix.group(1) or sb_prefix.group(2)).strip()
            else:
                mb_prefix = _INLINE_MAINBOARD_PREFIX_RE.match(line)
                if mb_prefix:
                    card_section = DeckSection.MAINBOARD
                    line = (mb_prefix.group(1) or mb_prefix.group(2)).strip()

        # Extract quantity
        qty_match = _QTY_PREFIX_RE.match(line)
        if qty_match:
            try:
                quantity = int(qty_match.group(1))
            except ValueError:
                quantity = 1
            line = qty_match.group(2).strip()
        else:
            quantity = 1

        # Skip lines that were only numbers (e.g. "4", "4x")
        if not line or re.fullmatch(r"\d+\s*[xX]?", line):
            continue

        if quantity <= 0:
            continue

        # Check for CMDR tags
        if _CMDR_TAG_RE.search(line):
            card_section = DeckSection.COMMANDER
            line = _CMDR_TAG_RE.sub("", line).strip()

        # Check for SB tags
        if _SB_TAG_RE.search(line):
            card_section = DeckSection.SIDEBOARD
            line = _SB_TAG_RE.sub("", line).strip()

        # Check for Foil tags
        foil = False
        if _FOIL_TAG_RE.search(line):
            foil = True
            line = _FOIL_TAG_RE.sub("", line).strip()

        # Strip a trailing bracketed category suffix that is NOT a set-code
        # bracket, e.g. "Sol Ring [Ramp]" -> "Sol Ring", while leaving
        # set/collector-style brackets (e.g. "[NEO:123]") alone so the
        # annotation parser below can still match them.
        cat_match = re.search(r"\[(?P<cat>[^\]]*)\]\s*$", line)
        if cat_match:
            cat = cat_match.group("cat")
            looks_like_set_bracket = bool(
                re.match(r"^[A-Za-z0-9_]{2,6}(?:[:\s]+[A-Za-z0-9★#/\-]+)?$", cat)
            ) and (":" in cat or re.search(r"\d", cat) or (cat.isupper() and 2 <= len(cat) <= 5))
            if not looks_like_set_bracket:
                line = line[: cat_match.start()].strip()

        # Extract set code and collector number
        set_code: Optional[str] = None
        collector_number: Optional[str] = None

        set_match = _SET_COLL_RE.search(line)
        if set_match:
            s1 = set_match.group("set1")
            s2 = set_match.group("set2")
            n1 = set_match.group("num1")
            n2 = set_match.group("num2")
            n3 = set_match.group("num3")

            num = n3 or n1 or n2
            if num:
                num = num.lstrip("#")

            if s1:
                # Parentheses: always set code
                set_code = s1
                collector_number = num
                line = line[:set_match.start()].strip()
            elif s2:
                # Brackets: only set code if collector number is present
                # or if all uppercase 3-4 chars
                if num or (s2.isupper() and 2 <= len(s2) <= 5):
                    set_code = s2
                    collector_number = num
                    line = line[:set_match.start()].strip()

        # Strip any bracketed category suffix, e.g. "[Ramp]"
        line = _BRACKET_CATEGORY_RE.sub("", line).strip()

        # Normalize split cards (e.g. "Fire // Ice" or "Fire//Ice")
        if "//" in line:
            line = _SPLIT_CARD_RE.sub(" // ", line).strip()

        # Validate that we have a valid card name remaining
        if not line:
            continue

        # Skip unrecognized formatting / non-card noise (e.g. lines with no letters)
        if not re.search(r"[A-Za-z\u00C0-\u024F]", line):
            continue

        card = ParsedCard(
            name=line,
            quantity=quantity,
            section=card_section,
            set_code=set_code,
            collector_number=collector_number,
            foil=foil,
        )
        deck.append(card)

    return deck


def parse_deck(text: str) -> Dict[str, List[ParsedCard]]:
    """Convenience helper returning parsed cards grouped by deck section.

    Returns:
        {
            "commander": [...],
            "mainboard": [...],
            "sideboard": [...],
        }
    """
    return parse_decklist(text).by_section
