"""Static guards for the shared a11y/responsive layer (src/web/src/app.css + shared components).

Pure file/maths checks, no browser: runs in the normal pytest suite. The 375px layout checks live
in src/web/tests/a11y_shared.py (Playwright)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "web" / "src"
APP_CSS = (WEB / "app.css").read_text()
COMPONENTS = WEB / "lib" / "components"


def _token(name: str) -> str:
    m = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", APP_CSS)
    assert m, f"--{name} missing from app.css"
    return m.group(1)


def _lum(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_text_tokens_meet_aa_on_surface():
    surface = _token("surface")
    for name in ("text", "text-muted", "text-dim", "text-faint", "danger", "ok", "warn-solid"):
        assert contrast(_token(name), surface) >= 4.5, name


def test_warn_is_large_text_only():
    surface = _token("surface")
    assert 3.0 <= contrast(_token("warn"), surface) < 4.5
    assert "large bold text only" in APP_CSS


def test_shared_components_have_no_opacity_dimmed_text():
    offenders = [
        p.name for p in COMPONENTS.glob("*.svelte") if re.search(r"opacity\s*:\s*0?\.\d", p.read_text())
    ]
    assert offenders == []


def test_chart_muted_fallback_meets_aa():
    # Shared components use var(--text-3, var(--text-muted)); outside the review surface that
    # resolves to --text-muted.
    assert contrast(_token("text-muted"), _token("surface")) >= 4.5


def test_table_scroll_contract():
    assert re.search(r"\.table-scroll\s*\{[^}]*overflow-x:\s*auto", APP_CSS)
    assert re.search(r"\.table-scroll\s*>\s*table\s*\{[^}]*min-width", APP_CSS)
    comp = (COMPONENTS / "TableScroll.svelte").read_text()
    assert 'class="table-scroll"' in comp and 'tabindex="0"' in comp and "aria-label" in comp


def test_touch_target_floor_and_focus_ring():
    m = re.search(r"\.text-link\s*\{([^}]*)\}", APP_CSS)
    assert m and "min-height: 44px" in m.group(1) and "min-width: 44px" in m.group(1)
    assert ":focus-visible" in APP_CSS
    assert "prefers-reduced-motion: reduce" in APP_CSS
    assert ".visually-hidden" in APP_CSS


def test_layout_imports_app_css():
    assert "import '../app.css'" in (WEB / "routes" / "+layout.svelte").read_text()
