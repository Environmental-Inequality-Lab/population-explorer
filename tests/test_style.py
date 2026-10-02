"""The shared brand, held in one place.

`assets/css/tokens.css` is vendored verbatim from gridded-eif. Its own header
says it is the only file that should need editing to rebrand — which is only
true if this copy tracks it and no component sneaks a literal colour in
underneath.

The sibling-repo comparison skips when that checkout is absent, so the suite
still runs anywhere; the rules that do not need it always run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOKENS = REPO / "assets" / "css" / "tokens.css"
COMPONENTS = REPO / "pipeline" / "explorer.css"
SIBLING = REPO.parent.parent / "gridded-eif" / "site" / "css" / "tokens.css"

VENDOR_BANNER_LINES = 5


def _vendored_body() -> str:
    return "".join(TOKENS.read_text().splitlines(keepends=True)[VENDOR_BANNER_LINES:])


def test_tokens_match_the_sibling_repo():
    """A drifted copy is worse than no copy — it looks shared and is not."""
    if not SIBLING.exists():
        pytest.skip(f"gridded-eif checkout not found at {SIBLING}")
    assert _vendored_body() == SIBLING.read_text(), (
        "assets/css/tokens.css has drifted from gridded-eif's. Re-copy it; do not "
        "edit the copy. A brand change belongs upstream, where both products read it."
    )


def test_vendored_file_declares_where_it_came_from():
    head = TOKENS.read_text()[:400]
    assert "VENDORED from gridded-eif" in head
    assert "unmodified" in head


def test_components_hold_no_literal_colour():
    """tokens.css's rule: no component may hardcode a colour.

    White on the maroon header is the one exception, and it is a token role
    (--header-fg) rather than a value, so it should not appear here either.
    """
    css = COMPONENTS.read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)          # comments carry hex in prose
    hexes = re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)
    named = re.findall(r":\s*(?:red|blue|green|black|white|grey|gray)\b", css)
    assert not hexes, f"literal colours in explorer.css: {sorted(set(hexes))}"
    assert not named, f"named colours in explorer.css: {sorted(set(named))}"


def test_every_token_used_by_components_is_defined():
    """A misspelled var() silently renders as nothing at all.

    The logo marks are the one pair defined at render time rather than in the
    token file: they carry a base64 payload, which belongs in the generated
    page once, not in a stylesheet checked into two repos.
    """
    from pipeline import render

    defined = set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", TOKENS.read_text(), re.MULTILINE))
    defined |= set(re.findall(r"(--[a-z0-9-]+):", render.logo_vars()))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", COMPONENTS.read_text()))
    missing = used - defined
    assert not missing, f"explorer.css uses undefined tokens: {sorted(missing)}"


def test_the_logo_payload_appears_once_per_page():
    """Referenced, not repeated.

    Nine figures each inlining the same ~10 KB data URI made a page that is
    otherwise text mostly logo. One declaration, referenced by class.
    """
    from pipeline import render

    css = render.css()
    assert css.count("data:image/webp;base64,") == 2, (
        "expected exactly two inlined marks — the white one for the header and "
        "the maroon one for figures"
    )
    assert "--logo-white" in css and "--logo-brand" in css


def test_brand_and_data_scales_stay_separate():
    """The token file's second rule, asserted rather than trusted.

    Changing the brand must never change what a colour means in a chart, so the
    data scale may not be defined in terms of a brand token.
    """
    text = TOKENS.read_text()
    for line in text.splitlines():
        if re.match(r"\s*--data-", line):
            assert "--brand" not in line, f"data token derives from the brand: {line.strip()}"


def test_the_page_is_light_only_on_purpose():
    """gridded-eif sets `color-scheme: light` deliberately.

    Without it the browser renders native widgets and scrollbars dark against a
    light page when the OS is set to dark. Matching that is the point of
    sharing the token file; a dark theme here would diverge the two products.
    """
    assert "color-scheme: light" in TOKENS.read_text()
    css = COMPONENTS.read_text()
    assert "prefers-color-scheme" not in css, (
        "explorer.css defines a dark theme, which the shared token file does not"
    )


def test_pages_can_share_one_stylesheet():
    """37 KB of CSS and logos per page, or once for the whole site.

    A self-contained page is right for a one-off; across four thousand of them
    the same bytes ship four thousand times. `render.page(assets=...)` links
    the shared sheet instead, and the browser caches it across every place.
    """
    from pipeline import render

    sheet = render.stylesheet()
    assert "data:image/webp;base64," not in sheet, (
        "the shared stylesheet inlines a logo — it should reference the files"
    )
    for name in render.LOGO_FILES.values():
        assert name in sheet, f"the stylesheet does not reference {name}"
        assert (render.ASSETS / name).exists(), f"{name} is missing from assets/"
    assert "--brand-deep" in sheet and ".fig-logo" in sheet, (
        "the shared sheet must carry both the tokens and the components"
    )
