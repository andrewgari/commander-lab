"""Pure decklist text export with per-site paste/import formats.

A single pure seam: ``export_decklist_text(deck, format, ...)`` takes a deck
dict and returns text in one of five clipboard formats — no Redis, no HTTP,
no UI dependency. Syntax per docs/EXPORT_FORMAT_SYNTAX.md (task t_26c77fdf).

Deck input shape (tolerant — missing keys fall back, never throw):

    deck = {
        "name":  str,                      # used only by Forge [metadata]
        "cards": [
            {
                "name":     str,
                "quantity": int,           # default 1
                # board placement: explicit "board" wins; otherwise derived
                # from an Archidekt-style "categories" list; default "main".
                "board":      "commander" | "main" | "sideboard" | "maybeboard",
                "categories": ["Commander" | "Sideboard" | "Maybeboard", ...],
                # printing (optional): set code + collector number. Accepted
                # keys: "set" or "set_code"; "collector_number" or "cn".
                "set":              "CMM",
                "collector_number": "400",
            },
            ...
        ],
    }

Rules (from the parent feature spec):
- The commander is ALWAYS exported, and always first.
- Sideboard / maybeboard are excluded unless their toggle is true.
- include_printing with missing printing data falls back to name-only for
  that card — never raises.
- No branching on deck type: physical and digital decks render identically.

Format-specific decisions where the spec left open questions:
- Moxfield boards: sideboard lines get an ``SB: `` prefix, maybeboard lines
  an ``MB: `` prefix (third-party importer convention; Moxfield's own paste
  box is a flat list).
- Forge maybeboard: the .dck spec has no [maybeboard] section; maybeboard
  cards (when included) fold into ``[sideboard]``.
- TCGplayer: flat list only, no section markers; boards appended flat.
"""
from typing import Any, Iterable, Optional

FORMATS = ("plain", "archidekt", "moxfield", "forge", "tcgplayer")

_BOARDS = ("commander", "main", "sideboard", "maybeboard")
_CATEGORY_TO_BOARD = {
    "commander": "commander",
    "sideboard": "sideboard",
    "maybeboard": "maybeboard",
}


class ExportFormatError(ValueError):
    """Raised for an unrecognized format value."""


def _card_board(card: dict) -> str:
    board = str(card.get("board") or "").strip().lower()
    if board in _BOARDS:
        return board
    for cat in card.get("categories") or []:
        mapped = _CATEGORY_TO_BOARD.get(str(cat).strip().lower())
        if mapped:
            return mapped
    return "main"


def _card_printing(card: dict) -> tuple:
    """(set_code, collector_number); either may be "" when unknown."""
    set_code = (card.get("set") or card.get("set_code") or "").strip()
    cn = (card.get("collector_number") or card.get("cn") or "").strip()
    return set_code, cn


def _normalize(cards: Optional[Iterable[dict]]) -> dict:
    """Partition cards into {board: [(name, qty, set, cn), ...]}.

    Blank names and non-positive quantities are skipped; unparseable
    quantities fall back to 1 rather than raising.
    """
    boards = {b: [] for b in _BOARDS}
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = int(card.get("quantity", 1))
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            continue
        set_code, cn = _card_printing(card)
        boards[_card_board(card)].append((name, qty, set_code, cn))
    return boards


def _paren_printing(set_code: str, cn: str, upper: bool) -> str:
    """` (SET) CN` suffix (Archidekt/Moxfield style), or "" without a set."""
    if not set_code:
        return ""
    code = set_code.upper() if upper else set_code.lower()
    return f" ({code}) {cn}" if cn else f" ({code})"


def export_decklist_text(
    deck: dict,
    format: str = "plain",
    include_sideboard: bool = False,
    include_maybeboard: bool = False,
    include_printing: bool = False,
) -> str:
    """Render `deck` as paste/import text in `format`. See module docstring."""
    fmt = (format or "").strip().lower()
    if fmt not in FORMATS:
        raise ExportFormatError(f"unknown export format: {format!r} (expected one of {FORMATS})")

    deck = deck or {}
    boards = _normalize(deck.get("cards"))
    want_sb = bool(include_sideboard)
    want_mb = bool(include_maybeboard)

    if fmt == "forge":
        return _forge(deck, boards, want_sb, want_mb, include_printing)

    lines = []
    if fmt == "plain":
        render = lambda e: f"{e[1]} {e[0]}" + (_paren_printing(e[2], e[3], upper=True) if include_printing else "")
        lines.extend(render(e) for e in boards["commander"])
        lines.extend(render(e) for e in boards["main"])
        if want_sb and boards["sideboard"]:
            lines.extend(["", "Sideboard"])
            lines.extend(render(e) for e in boards["sideboard"])
        if want_mb and boards["maybeboard"]:
            lines.extend(["", "Maybeboard"])
            lines.extend(render(e) for e in boards["maybeboard"])
    elif fmt == "moxfield":
        def render_mox(e, board):
            line = f"{e[1]} {e[0]}"
            if include_printing:
                line += _paren_printing(e[2], e[3], upper=True)
            if board == "commander":
                line += " *CMDR*"
            if board == "sideboard":
                line = "SB: " + line
            elif board == "maybeboard":
                line = "MB: " + line
            return line
        lines.extend(render_mox(e, "commander") for e in boards["commander"])
        lines.extend(render_mox(e, "main") for e in boards["main"])
        if want_sb:
            lines.extend(render_mox(e, "sideboard") for e in boards["sideboard"])
        if want_mb:
            lines.extend(render_mox(e, "maybeboard") for e in boards["maybeboard"])
    elif fmt == "archidekt":
        def render_arch(e, tag):
            line = f"{e[1]}x {e[0]}"
            if include_printing:
                line += _paren_printing(e[2], e[3], upper=False)
            return line + tag
        lines.extend(render_arch(e, " [Commander{top}]") for e in boards["commander"])
        lines.extend(render_arch(e, "") for e in boards["main"])
        if want_sb:
            lines.extend(render_arch(e, " [Sideboard{noDeck}{noPrice}]") for e in boards["sideboard"])
        if want_mb:
            lines.extend(render_arch(e, " [Maybeboard{noDeck}{noPrice}]") for e in boards["maybeboard"])
    else:  # tcgplayer — flat list, no commander/board markers
        def render_tcg(e):
            line = f"{e[1]} {e[0]}"
            if include_printing and e[2]:
                line += f" [{e[2].upper()}]"
                if e[3]:
                    line += f" {e[3]}"
            return line
        lines.extend(render_tcg(e) for e in boards["commander"])
        lines.extend(render_tcg(e) for e in boards["main"])
        if want_sb:
            lines.extend(render_tcg(e) for e in boards["sideboard"])
        if want_mb:
            lines.extend(render_tcg(e) for e in boards["maybeboard"])

    return "\n".join(lines)


def _forge(deck: dict, boards: dict, want_sb: bool, want_mb: bool, include_printing: bool) -> str:
    def render(e):
        line = f"{e[1]} {e[0]}"
        if include_printing and e[2]:
            line += f"|{e[2].upper()}"  # Forge pipes the set code; no collector number
        return line

    lines = ["[metadata]", f"Name={deck.get('name') or 'Deck'}", "[commander]"]
    lines.extend(render(e) for e in boards["commander"])
    lines.append("[main]")
    lines.extend(render(e) for e in boards["main"])
    # .dck has no [maybeboard] section — maybeboard folds into [sideboard].
    sideboard = list(boards["sideboard"]) if want_sb else []
    if want_mb:
        sideboard += boards["maybeboard"]
    if sideboard:
        lines.append("[sideboard]")
        lines.extend(render(e) for e in sideboard)
    return "\n".join(lines)
