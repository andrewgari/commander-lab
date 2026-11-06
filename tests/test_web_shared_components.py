"""Static guards for the shared-component foundation (C0): new tokens, their contrast, .btn/.meta and
the no-hex / no-opacity rule for shared components. Pure file/maths checks, no browser; the browser
runner is src/web/tests/components_a11y.py."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "web" / "src"
APP_CSS = (WEB / "app.css").read_text()
CSS_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
COMPONENTS = WEB / "lib" / "components"
EXCLUDED_DIRS = {"review", "review-page"}
# Pre-existing chart components that still carry the data-viz blue (#4a7dbd) as a literal. New shared
# components must not; the guard fails for anything not listed here. Remove entries as they migrate.
LEGACY_HEX = {"ArchetypeMixChart.svelte", "CurveChart.svelte", "PowerPanel.svelte"}

SURFACES = ("color-bg", "color-surface", "color-surface-raised")
NEW_TOKENS = {
    "color-warning-solid": "#8a5a12",
    "color-info": "#7fb3e0",
    "color-danger-bg": "#271f22",
    "color-warning-bg": "#2e2b22",
    "color-info-bg": "#212c36",
}
TINTS = {  # tint token -> hue token drawn as the rule/icon on it
    "color-danger-bg": "color-danger",
    "color-warning-bg": "color-warning",
    "color-info-bg": "color-info",
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


def test_new_tokens_have_exact_values():
    for name, value in NEW_TOKENS.items():
        assert _token(name).lower() == value, name


def test_white_on_warning_solid_meets_aa():
    assert contrast(_token("color-on-solid"), _token("color-warning-solid")) >= 4.5


def test_info_text_meets_aa_on_every_surface():
    for surface in SURFACES:
        assert contrast(_token("color-info"), _token(surface)) >= 4.5, surface


def test_tints_hold_text_and_non_text_contrast():
    for tint, hue in TINTS.items():
        bg = _token(tint)
        for text in ("color-text", "color-text-body", "color-muted"):
            assert contrast(_token(text), bg) >= 4.5, f"{text} on {tint}"
        assert contrast(_token(hue), bg) >= 3, f"{hue} on {tint}"
        assert contrast(_token("color-focus"), bg) >= 3, f"focus on {tint}"


def test_button_fills_meet_aa():
    assert contrast(_token("color-on-accent"), _token("color-accent")) >= 4.5
    assert contrast(_token("color-on-solid"), _token("color-success-solid")) >= 4.5
    assert contrast(_token("color-on-solid"), _token("color-danger-solid")) >= 4.5


def test_link_danger_text_meets_aa_on_every_surface():
    for surface in SURFACES:
        assert contrast(_token("color-danger"), _token(surface)) >= 4.5, surface


def _rule(selector: str) -> str:
    # The selector may head a list (".btn[aria-disabled] , .btn[aria-disabled]:hover { ... }").
    m = re.search(rf"(?<![\w-]){re.escape(selector)}(?:[^{{}}]*)\{{([^}}]*)\}}", CSS_NO_COMMENTS)
    assert m, f"{selector} rule missing from app.css"
    return m.group(1)


def test_meta_and_btn_contract():
    meta = _rule(".meta")
    assert "color: var(--color-muted)" in meta and "font-size: var(--text-sm)" in meta
    base = _rule(".btn")
    for decl in (
        "display: inline-flex",
        "align-items: center",
        "min-height: var(--touch-min)",
        "padding: var(--space-2) var(--space-4)",
        "border-radius: var(--radius-sm)",
        "font: inherit",
        "text-decoration: none",
        "cursor: pointer",
    ):
        assert decl in base, decl
    for variant in ("secondary", "primary", "success", "danger", "link-danger"):
        assert f".btn--{variant}" in APP_CSS, variant
    assert "cursor: progress" in _rule('.btn[aria-disabled="true"]')
    assert "border-style: dashed" in _rule('.btn[aria-disabled="true"]')
    assert re.search(r"forced-colors:\s*active\)\s*\{[^@]*ButtonText[^@]*ButtonFace[^@]*ButtonBorder", APP_CSS)


def test_btn_css_has_no_opacity_or_hex():
    start = APP_CSS.index("shared-component foundation (C0)")
    block = APP_CSS[start:]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    block = re.sub(r":root\s*\{[^}]*\}", "", block)  # token definitions are the only place hex is allowed
    assert not re.search(r"opacity\s*:", block)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)


def _shared_svelte():
    return [
        p
        for p in COMPONENTS.rglob("*.svelte")
        if not (set(p.relative_to(COMPONENTS).parts[:-1]) & EXCLUDED_DIRS)
    ]


def test_shared_components_have_no_hex_literals_or_opacity():
    files = _shared_svelte()
    assert files, "no shared components found"
    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    op_re = re.compile(r"opacity\s*:\s*0?\.\d")
    offenders = []
    for p in files:
        text = p.read_text()
        if op_re.search(text) or (hex_re.search(text) and str(p.relative_to(COMPONENTS)) not in LEGACY_HEX):
            offenders.append(str(p.relative_to(COMPONENTS)))
    assert offenders == []


def test_legacy_hex_allowlist_only_lists_real_offenders():
    # Keeps the allowlist honest: once a file is cleaned up it has to leave the list.
    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    for name in LEGACY_HEX:
        assert hex_re.search((COMPONENTS / name).read_text()), f"{name} no longer has a hex literal; drop it from LEGACY_HEX"
