"""Static guards for StatusPill (C3): role x variant pairs are parsed from status.ts, token values from
app.css, and the WCAG contrast floors are asserted for every pair. Pure file/maths checks, no browser;
the browser runner is src/web/tests/components_a11y.py against /dev/components/status-pill."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "web" / "src"
APP_CSS = (WEB / "app.css").read_text()
STATUS_TS = (WEB / "lib" / "status.ts").read_text()
PILL = (WEB / "lib" / "components" / "StatusPill.svelte").read_text()
PILL_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", re.sub(r"(?m)^\s*//.*$", "", PILL), flags=re.S)

SURFACES = ("color-bg", "color-surface", "color-surface-raised")
# role -> (text-variant label token, solid-variant fill token); mirrors the .pill--{role} rules.
ROLE_TOKENS = {
    "success": ("color-success", "color-success-solid"),
    "warning": ("color-warning", "color-warning-solid"),
    "danger": ("color-danger", "color-danger-solid"),
    "info": ("color-info", "color-info-bg"),
    "neutral": ("color-muted", "color-surface-raised"),
}


def _token(name: str) -> str:
    m = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", APP_CSS)
    assert m, f"--{name} missing from app.css"
    return m.group(1)


def _lum(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _catalog_pairs() -> dict[str, tuple[str, str]]:
    block = STATUS_TS[STATUS_TS.index("export const STATUS_CATALOG") :]
    block = block[: block.index("};")]
    pairs = {}
    for m in re.finditer(r"^\s*'?([\w-]+)'?:\s*\{[^}]*role:\s*'(\w+)',\s*variant:\s*'(\w+)'", block, re.M):
        pairs[m.group(1)] = (m.group(2), m.group(3))
    return pairs


def test_catalog_parses_all_sixteen_keys():
    pairs = _catalog_pairs()
    assert len(pairs) == 16, sorted(pairs)
    assert {r for r, _ in pairs.values()} <= set(ROLE_TOKENS)
    assert {v for _, v in pairs.values()} == {"solid", "text"}


def test_css_role_rules_match_the_token_table():
    for role, (fg, fill) in ROLE_TOKENS.items():
        m = re.search(rf"\.pill--{role}\s*\{{([^}}]*)\}}", PILL)
        assert m, f".pill--{role} rule missing"
        assert f"--pill-fg: var(--{fg})" in m.group(1), role
        assert f"--pill-fill: var(--{fill})" in m.group(1), role


def test_solid_label_meets_aa_on_its_fill_for_every_used_role():
    on_solid = _token("color-on-solid")
    used = {role for role, variant in _catalog_pairs().values() if variant == "solid"}
    assert used == {"success", "warning", "danger"}, used
    for role in used:
        fill = ROLE_TOKENS[role][1]
        assert contrast(on_solid, _token(fill)) >= 4.5, f"{role}: on-solid on {fill}"


def test_text_label_meets_aa_on_every_surface_for_every_used_role():
    used = {role for role, variant in _catalog_pairs().values() if variant == "text"}
    assert used == {"warning", "neutral", "info"}, used
    for role in used:
        fg = ROLE_TOKENS[role][0]
        for surface in SURFACES:
            assert contrast(_token(fg), _token(surface)) >= 4.5, f"{role}: {fg} on {surface}"


def test_variant_overrides_stay_legible():
    """`variant` is a public prop, so every role x variant a caller can request must clear AA."""
    on_solid = _token("color-on-solid")
    for role, (fg, fill) in ROLE_TOKENS.items():
        for surface in SURFACES:
            assert contrast(_token(fg), _token(surface)) >= 4.5, f"{role} text on {surface}"
        assert contrast(on_solid, _token(fill)) >= 4.5, f"{role} solid"


def test_pill_uses_only_color_tokens_no_hex_no_opacity():
    css = PILL_NO_COMMENTS[PILL_NO_COMMENTS.index("<style>") :]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", PILL_NO_COMMENTS)
    assert not re.search(r"opacity\s*:", PILL_NO_COMMENTS)
    assert not re.search(r"\b(rgb|rgba|hsl|hsla|oklch|color-mix)\(", css)
    allowed_kw = {"transparent", "inherit", "currentcolor", "canvas", "canvastext"}
    for decl in re.finditer(r"(?<![\w-])(color|background|background-color|border-color|border|--pill-fg|--pill-fill)\s*:\s*([^;}]+)", css):
        value = decl.group(2).strip()
        for part in re.findall(r"var\(--[\w-]+\)|\d+px|[A-Za-z]+", value):
            if part.startswith("var("):
                assert part.startswith("var(--color-") or part.startswith("var(--pill-"), (decl.group(0), part)
            elif part in {"solid", "none"} or re.fullmatch(r"\d+px", part):
                continue
            else:
                assert part.lower() in allowed_kw, (decl.group(0), part)


def test_forced_colors_and_markup_contract():
    assert re.search(
        r"@media \(forced-colors: active\)\s*\{[^@]*border: 1px solid CanvasText[^@]*background: Canvas[^@]*color: CanvasText", PILL
    )
    assert "text-transform: uppercase" in PILL
    assert "white-space: normal" in PILL and "nowrap" not in PILL and "ellipsis" not in PILL
    for banned in ("aria-label", "role=", "tabindex", "{@html"):
        assert banned not in PILL_NO_COMMENTS, banned
