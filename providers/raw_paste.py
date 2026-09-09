"""Raw-paste decklist parser.

Parses plain-text decklists (as pasted from Archidekt/Moxfield exports or
typed by hand) into the normalized card-line shape used throughout the
Deck Salvager feature: ``[{"name": str, "quantity": int}, ...]``.

See docs/DECK_SALVAGER.md for the full design.
"""
import re

# "1 Sol Ring", "1x Sol Ring", "1X Sol Ring" -> qty=1, name="Sol Ring"
_QTY_NAME_RE = re.compile(r"^(\d+)\s*[xX]?\s+(.+)$")

# Strip a trailing bracketed category suffix, e.g. "Sol Ring [Ramp]" -> "Sol Ring"
_BRACKET_SUFFIX_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def parse_decklist(text: str) -> list[dict]:
    """Parse raw decklist text into [{"name": str, "quantity": int}, ...].

    Line handling:
    - "1 Sol Ring", "1x Sol Ring", "1X Sol Ring" -> qty=1, name="Sol Ring"
    - bare "Sol Ring" (no leading number) -> qty=1
    - trailing bracket suffix stripped, e.g. "1 Sol Ring [Ramp]" -> "Sol Ring"
    - blank lines and lines starting with "#" or "//" are skipped
    """
    if not text:
        return []

    cards = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        match = _QTY_NAME_RE.match(line)
        if match:
            quantity = int(match.group(1))
            name = match.group(2).strip()
        else:
            quantity = 1
            name = line

        name = _BRACKET_SUFFIX_RE.sub("", name).strip()
        if not name:
            continue

        cards.append({"name": name, "quantity": quantity})

    return cards
