"""Render a place page in the EIL house style.

The figure frame is carried over from the published explorer: a FIGURE label in
maroon, a bold title, a subtitle, the logo top-right, then Sources and Notes
beneath a rule with the lab URL at the far right. Chart marks are #003f5c,
horizontal gridlines only, legend above the plot.

What changed is how the frame is built. `03_add_figure_wrappers.R` composited
it into a 2912x1800 PNG with cowplot and wrote one of 62,860 image files, every
one of which had to be re-rendered to fix a caption. Here it is CSS around
inline SVG: the text is selectable and searchable, the figure reflows on a
phone, and a caption change costs nothing.

Formatting lives here and only here. `figures.py` returns numbers,
`narrative.py` returns numbers plus the word that describes them, and this is
the first place a value becomes a string.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import html
import math
import re
from functools import cache
from math import ceil, floor
from pathlib import Path

from pipeline import config, narrative
from pipeline.figures import CrossShares, PlaceFigures, Series, Shares

BASE_YEAR = 2000
# Where stated growth and change are measured from. Voorheis et al. (2026, REEP
# 20(2)) report coverage increases in 2004 (information returns) and 2015
# (commercial data), so counts jump in those years. Measuring from 2015 starts
# after the second. Our own check: Albemarle +59% from 2000 to 2024 here, +40%
# in the Census Bureau's estimates; from 2015 the two agree within a point.
# Figures still plot from BASE_YEAR.
GROWTH_BASE = 2015



ASSETS = Path(__file__).resolve().parent.parent / "assets"

LAB_URL = "www.environmental-inequality-lab.org"
SOURCE = ("EIL calculations from the U.S. Census Bureau's Privacy-Protected Gridded "
          "Environmental Impacts Frame (Gridded EIF).")


@cache
def _asset(name: str, mime: str) -> str:
    """Inline as a data URI. No second request, and the page is portable."""
    p = ASSETS / name
    if not p.exists():
        return ""
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


# The marks as files beside the stylesheet, not data URIs inside it. Inlined,
# they were 37 KB of every page; shared, the browser fetches them once for the
# whole site and caches them across every place page.
LOGO_FILES = {"--logo-white": "eil-logo.webp", "--logo-brand": "eil-logo-brand.webp"}
# The same browser-tab icon as the gridded-eif site, copied from its
# `site/assets/`, so the two products read as one family in a tab strip too.
FAVICON = "favicon.ico"
# The front-page graphic, drawn once by tools/build_hero.py and committed.
HERO_IMAGE = "hero-texture.webp"

# Who and where, for the shared footer. Mirrors gridded-eif's `SITE` config.
LAB_HOME = "https://environmental-inequality-lab.org"
GRIDDED_EIF_SITE = "https://environmental-inequality-lab.github.io/gridded-eif/"
CENSUS_PAGE = "https://www.census.gov/data/experimental-data-products/gridded-eif.html"
SOURCE_CODE = "https://github.com/Environmental-Inequality-Lab/population-explorer"
# The public address, for the citation. Grant's call (2026-10-01): the lab's
# GitHub Pages path, beside gridded-eif's. Set to None to have each reader's
# browser fill in whatever address it loaded instead.
SITE_URL: str | None = "https://environmental-inequality-lab.github.io/population-explorer/"

# Fills the citation's address (when SITE_URL is unset) and the reader's own
# access date. Without JavaScript the citation simply omits both.
CITE_JS = """
(function () {
  document.querySelectorAll('.cite-url:empty').forEach(function (e) {
    e.textContent = location.origin + '/';
  });
  var d = new Date().toLocaleDateString('en-US',
    {year: 'numeric', month: 'long', day: 'numeric'});
  document.querySelectorAll('.cite-accessed').forEach(function (e) {
    e.querySelector('.cite-date').textContent = d;
    e.hidden = false;
  });
})();
"""

LOGO_TAG = '<div class="brand-mark" role="img" aria-label="Environmental Inequality Lab"></div>'


def _icon(assets: str | None) -> str:
    if assets is None:
        return ""
    return f'<link rel="icon" href="{assets}{FAVICON}" sizes="16x16 32x32">'


def _header(root: str = "", active: str | None = None) -> str:
    """The shared header: the lab mark and two links, as on gridded-eif.

    No product name beside the mark. gridded-eif carries none either, and
    every page already names itself in its own heading.
    """
    home = root or "./"
    links = (("search", "Search Place Profiles", home), ("about", "About", f"{root}about.html"))
    current = ' class="active" aria-current="page"'
    nav = "".join(f'<a href="{href}"{current if key == active else ""}>{label}</a>'
                  for key, label, href in links)
    return (f'<header class="site-header"><div class="wrap">\n'
            f'  <a class="brand" href="{home}">{LOGO_TAG}</a>\n'
            f'  <nav class="site-nav">{nav}</nav>\n</div></header>')


@cache
def explorer_version() -> str:
    import tomllib
    return tomllib.loads((ASSETS.parent / "pyproject.toml").read_text())["project"]["version"]


def _version_line() -> str:
    """Which build of this site, on which build of the data. As gridded-eif's
    footer does: a reader reporting a number can say exactly where it came from.

    "Updated" is the upstream data's own date, not the build's, so a rebuild
    that changes nothing does not change every page (and re-upload them all).
    """
    try:
        cat = config.upstream_catalog()
    except Exception:  # noqa: BLE001 -- a footer without versions is still a footer
        return f"Explorer v{explorer_version()}"
    return (f"Explorer v{explorer_version()} &middot; Gridded EIF data "
            f"{e(cat.get('derived_version', ''))} &middot; pipeline "
            f"{e(cat.get('pipeline_version', ''))} &middot; updated "
            f"{e(str(cat.get('generated_at', ''))[:10])}")


def _footer(extra: str = "") -> str:
    """The shared footer, mirroring gridded-eif's: credit and source on the
    left, how to cite on the right, the Census disclaimer across the bottom."""
    year = datetime.datetime.now(datetime.UTC).year
    return f"""<footer class="site-footer"><div class="wrap">
  {extra}
  <div class="footer-cols">
    <div>
      <p><strong>Population Data Explorer</strong> &mdash; an
      <a href="{LAB_HOME}">Environmental Inequality Lab</a> project.</p>
      <p>Built from the U.S. Census Bureau&rsquo;s Gridded Environmental Impacts
      Frame, an experimental data product. Counts derive from administrative records
      and may not match the Decennial Census or Population Estimates Program.</p>
      <p><a href="{GRIDDED_EIF_SITE}">Gridded EIF Data Explorer</a> &middot;
      <a href="{SOURCE_CODE}">Source code</a></p>
    </div>
    <div>
      <p><strong>Citation</strong></p>
      <p class="muted">Please cite both this site, which produced these profiles, and
      the article introducing the underlying data.</p>
      <p>Environmental Inequality Lab. {year}. <em>Population Data Explorer</em>.
      <span class="cite-url">{SITE_URL or ""}</span><span class="cite-accessed"
      hidden> (accessed <span class="cite-date"></span>)</span>.</p>
      <p>Voorheis, John, Jonathan Colmer, Kendall Houghton, Eva Lyubich, Mary Munro,
      Cameron Scalera, and Jennifer Withrow. 2026. &ldquo;The Census Environmental
      Impacts Frame.&rdquo; <em>Review of Environmental Economics and Policy</em>
      20 (2): 304&ndash;312.</p>
      <p class="muted">{_version_line()}</p>
    </div>
  </div>
  <p class="footer-disclaimer"><strong>Disclaimer:</strong> This website presents and
  aggregates data from a U.S. Census Bureau data product. The presentation, analysis,
  and interpretation of these data are the responsibility of the site authors and do
  not reflect the views of the U.S. Census Bureau.</p>
</div></footer>
<script>{CITE_JS}</script>"""


@cache
def density_vars() -> str:
    steps = ";".join(f"{tok}:{hexv}" for tok, hexv in zip(DENSITY_STEPS, DENSITY_RAMP))
    return ":root{" + steps + f";--dens-empty:{DENSITY_EMPTY}" + "}"


@cache
def logo_vars(inline: bool = True) -> str:
    """Both marks, declared once as custom properties."""
    if inline:
        return (":root{--logo-white:url('" + _asset("eil-logo.webp", "image/webp") + "');"
                "--logo-brand:url('" + _asset("eil-logo-brand.webp", "image/webp") + "')}")
    return ":root{" + ";".join(f"{k}:url('{v}')" for k, v in LOGO_FILES.items()) + "}"


def stylesheet() -> str:
    """The shared stylesheet, for writing once into the built site."""
    tokens = (ASSETS / "css" / "tokens.css").read_text()
    return (tokens + "\n" + logo_vars(inline=False) + "\n" + density_vars() + "\n"
            + (Path(__file__).parent / "explorer.css").read_text())


@cache
def css() -> str:
    """Shared brand tokens, then this product's components.

    tokens.css is vendored verbatim from gridded-eif — one brand, one token
    file, rather than two that drift. Its own header says it is the only file
    that should need editing to rebrand, and tests/test_style.py holds this
    copy to that promise.
    """
    tokens = (ASSETS / "css" / "tokens.css").read_text()
    return (tokens + "\n" + logo_vars() + "\n" + density_vars() + "\n"
            + (Path(__file__).parent / "explorer.css").read_text())


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def num(v: float | None, places: int = 0) -> str:
    return "—" if v is None else f"{v:,.{places}f}"


def density_num(v: float | None) -> str:
    """A density, with enough precision to be a number rather than a zero.

    Whole people per square kilometer is right for a city and useless for the
    Aleutians: 356 people over 11,000 square kilometers rounded to "0 people
    per square kilometer of land", which reads as nobody living there at all.
    """
    if v is None:
        return "—"
    if v >= 10:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:,.1f}"
    if v >= 0.01:
        return f"{v:,.2f}"
    return "under 0.01"


def pct(v: float | None, places: int = 1) -> str:
    return "—" if v is None else f"{v:,.{places}f}%"


def e(s) -> str:
    return html.escape(str(s), quote=True)


# --------------------------------------------------------------------------
# The EIL figure frame
# --------------------------------------------------------------------------

def figure(*, label: str = "", title: str, subtitle: str, body: str,
           notes: str, source: str = SOURCE) -> str:
    # The shipped mark is white artwork for the maroon header bar, so on a white
    # figure ground it is invisible. `assets/eil-logo-brand.webp` is the same
    # artwork recoloured to #601215, generated from it rather than redrawn.
    #
    # Referenced, not repeated: the data URI is declared once in the stylesheet,
    # where it cost ~10 KB nine times over on a page that is otherwise text.
    logo_tag = ('<div class="fig-logo" role="img" '
                'aria-label="Environmental Inequality Lab"></div>')
    # The number is filled in once the page is assembled — a figure does not
    # know its own position, and hardcoding one means the classic pages, which
    # carry fewer figures, skip numbers.
    return f"""<figure class="eil">
  <div class="fig-head">
    <p class="fig-label">{e(label) or "FIGURE_N"}</p>
    <p class="fig-title">{e(title)}</p>
    <p class="fig-sub">{e(subtitle)}</p>
    {logo_tag}
  </div>
  <div class="fig-body">{body}</div>
  <figcaption class="fig-foot">
    <p><b>Sources:</b> {e(source)}</p>
    <p><b>Notes:</b> {e(notes)}</p>
    <span class="fig-url"><a href="https://environmental-inequality-lab.org">{LAB_URL}</a></span>
  </figcaption>
</figure>"""


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

def _gridlines(x0: float, x1: float, y0: float, y1: float, n: int = 4) -> str:
    """Horizontal only — `theme_county()` blanked the vertical grid."""
    step = (y0 - y1) / n
    return "".join(
        f'<line class="gridline" x1="{x0:.1f}" y1="{y0 - i * step:.1f}" '
        f'x2="{x1:.1f}" y2="{y0 - i * step:.1f}"/>' for i in range(1, n + 1)
    )


def line_chart(s: Series, *, w: int = 900, h: int = 330, pad: int = 46) -> str:
    if not s.years:
        return ""
    lo, hi = min(s.values), max(s.values)
    span = (hi - lo) or 1
    x0, x1 = pad + 14, w - pad
    y0, y1 = h - pad, 30

    def px(yr): return x0 + (x1 - x0) * (yr - s.years[0]) / max(1, s.years[-1] - s.years[0])
    def py(v):  return y0 - (y0 - y1) * (v - lo) / span

    final = list(zip(s.years, s.values))
    line = " ".join(f"{px(y):.1f},{py(v):.1f}" for y, v in final)
    area = f"{px(final[0][0]):.1f},{y0} {line} {px(final[-1][0]):.1f},{y0}"
    dash = ""

    anchors = {s.years[0]: "start", final[-1][0]: "end"}
    step = max(1, (final[-1][0] - s.years[0]) // 5)
    tick_years = sorted({s.years[0], *range(s.years[0], final[-1][0], step), final[-1][0]})
    ticks = "".join(
        f'<text x="{px(y):.1f}" y="{h - 16}" class="tick" '
        f'text-anchor="{anchors.get(y, "middle")}">{y}</text>' for y in tick_years
    )
    fy, fv = final[0]
    ly, lv = final[-1]

    # Invisible hit areas, one per year, spanning the plot height. The trace
    # stays a clean line — no dots — while every year is still hoverable, and
    # a full-height band is far easier to hit than a 3px point.
    band = (x1 - x0) / max(1, len(final) - 1)
    hover = "".join(
        f'<g class="mark" data-k="Population" data-v="{num(v)}" data-x="{y}">'
        f'<rect x="{max(x0, px(y) - band / 2):.1f}" y="{y1:.1f}" '
        f'width="{band:.1f}" height="{y0 - y1:.1f}" fill="transparent"/>'
        f'<circle class="hover-dot" cx="{px(y):.1f}" cy="{py(v):.1f}" r="4.5" '
        f'fill="var(--data-1)"/>'
        f'<title>{y}: {num(v)}</title></g>'
        for y, v in final
    )
    return f"""<svg viewBox="0 0 {w} {h}" role="img" aria-label="Population over time">
  {_gridlines(x0, x1, y0, y1)}
  <line class="axis" x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}"/>
  <polygon points="{area}" fill="var(--data-1)" opacity=".12"/>
  <polyline points="{line}" fill="none" stroke="var(--data-1)" stroke-width="2.4"/>{dash}
  <circle cx="{px(ly):.1f}" cy="{py(lv):.1f}" r="4" fill="var(--data-1)"/>
  <text x="{px(fy):.1f}" y="{py(fv) - 11:.1f}" class="tick">{num(fv)}</text>
  <text x="{px(ly) - 9:.1f}" y="{py(lv) - 12:.1f}" class="tick end strong">{num(lv)}</text>
  {hover}{ticks}
</svg>"""


# The published palette, mapped onto shared tokens. Age reads as ordered levels
# of one thing, so it keeps the original light-blue / maroon / navy; race are
# genuinely different groups and take the categorical scale.
AGE_LINE_TOKENS = {"Under 18": "--data-seq-2", "19-65": "--brand-deep",
                   "18-64": "--brand-deep", "Over 65": "--data-1",
                   "65 and over": "--data-1"}
RACE_LINE_TOKENS = {"White": "--data-1", "Black": "--data-2", "Hispanic": "--data-3",
                    "Asian": "--data-4", "AIAN": "--data-5", "Other/Unknown": "--data-6"}


def share_trend(trends: dict[str, Series], labels: dict[str, str], residual: str | None,
                tokens: dict[str, str], *, top: float = 80.0, drop_residual: bool = True,
                counts: dict[str, float] | None = None,
                w: int = 900, h: int = 380, pad: int = 52) -> str:
    """One line per category, shares over time.

    For age the residual stays in the denominator and off the chart, exactly as
    `fig_age_trend` did: `total` is the row sum over all four groups while only
    three lines are drawn. So those lines do not sum to 100, and are not meant
    to. For race every group is drawn, so they do.
    """
    # Every group is drawn, however small. `counts` is still taken so the
    # caller can caveat the small ones; it is not a filter.
    drawn = [c for c in trends if not (drop_residual and c == residual)]
    if not drawn:
        return ""
    years = trends[drawn[0]].years
    x0, x1 = pad + 16, w - pad + 20
    y0, y1 = h - pad, 40

    def px(y): return x0 + (x1 - x0) * (y - years[0]) / max(1, years[-1] - years[0])
    def py(v): return y0 - (y0 - y1) * v / top

    body, legend = [], []
    for i, code in enumerate(drawn):
        ser = trends[code]
        tok = tokens.get(code, "--data-1")
        pts = " ".join(f"{px(y):.1f},{py(v):.1f}" for y, v in zip(ser.years, ser.values))
        body.append(f'<polyline points="{pts}" fill="none" stroke="var({tok})" stroke-width="2.4"/>')
        body += [f'<circle class="mark" cx="{px(y):.1f}" cy="{py(v):.1f}" r="3.2" '
                 f'fill="var({tok})" opacity=".9" data-k="{e(_chart_label(labels.get(code, code)))}" '
                 f'data-v="{pct(v)}" data-x="{y}">'
                 f'<title>{e(_chart_label(labels.get(code, code)))} — {y}: {pct(v)}</title></circle>'
                 for y, v in zip(ser.years, ser.values)]
        lx = x0 + i * (150 if len(drawn) <= 4 else 118)
        legend.append(f'<rect x="{lx:.1f}" y="12" width="11" height="11" rx="2" fill="var({tok})"/>'
                      f'<text x="{lx + 17:.1f}" y="22" class="legend">{e(_chart_label(labels.get(code, code)))}</text>')

    ticks = "".join(f'<text x="{px(y):.1f}" y="{y0 + 18:.1f}" class="tick mid">{y}</text>'
                    for y in range(years[0], years[-1] + 1, 5))
    axis = "".join(f'<text x="{x0 - 10:.1f}" y="{py(v) + 4:.1f}" class="tick end">{v:.0f}%</text>'
                   for v in [top * i / 4 for i in range(5)])
    # `data-palette="published"` marks a chart that reproduces the published
    # explorer's colours, which used the EIL maroon as a chart colour. Charts
    # without it must stay on the data scale — see tests/test_figures.py.
    return f"""<svg viewBox="0 0 {w} {h}" data-palette="published"
     role="img" aria-label="Composition over time">
  {_gridlines(x0, x1, y0, y1)}
  {"".join(body)}
  <line class="axis" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y0:.1f}"/>
  {"".join(legend)}{ticks}{axis}
  <text x="{(x0 + x1) / 2:.1f}" y="{h - 8}" class="tick mid axis-title">Year</text>
  <text x="14" y="{(y0 + y1) / 2:.1f}" class="tick axis-title"
        transform="rotate(-90 14 {(y0 + y1) / 2:.1f})" text-anchor="middle">Share of population</text>
</svg>"""


def bar_chart(sh: Shares, *, reference_pct: float | None = None,
              reference_label: str = "", comparison: Shares | None = None,
              comparison_label: str = "United States", subject_label: str = "This place",
              hide: str | None = None,
              w: int = 900, h: int = 360, pad: int = 46) -> str:
    """Grouped bars where a comparison exists, single bars otherwise."""
    # A category can be left off the chart while staying in the denominator and
    # in the table beneath it. Age not reported is 1.6% of residents and no
    # reader is comparing it to anything — on the chart it is a bar that costs
    # attention and returns nothing.
    codes = [c for c in sh.order if c != hide]
    if not codes:
        return ""
    vals = [sh.share(c) or 0 for c in codes]
    cmp_vals = None
    if comparison is not None and set(comparison.order) == set(sh.order):
        cmp_vals = [comparison.share(c) or 0 for c in codes]

    top = max([*vals, *(cmp_vals or []), reference_pct or 0]) * 1.16
    x0, x1 = pad + 20, w - pad
    y0, y1 = h - pad - 6, 36
    slot = (x1 - x0) / len(codes)
    bw = slot * (0.34 if cmp_vals else 0.6)

    def bar(x, v, fill, op, who, code):
        bh = (y0 - y1) * v / top
        return (f'<rect class="mark" x="{x:.1f}" y="{y0 - bh:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{fill}" opacity="{op}" data-k="{e(who)}" '
                f'data-v="{pct(v)}" data-x="{e(sh.labels.get(code, code))}">'
                f'<title>{e(who)} — {e(sh.labels.get(code, code))}: {pct(v)}</title></rect>')

    bars, labels = [], []
    for i, code in enumerate(codes):
        cx = x0 + slot * (i + .5)
        # The residual reads as a neutral, never as another series.
        fill = "var(--data-6)" if code == sh.residual else "var(--data-1)"
        if cmp_vals:
            bars.append(bar(cx - bw - 1, vals[i], fill, ".95", subject_label, code))
            bars.append(bar(cx + 1, cmp_vals[i], "var(--data-seq-2)", ".95", comparison_label, code))
        else:
            bars.append(bar(cx - bw / 2, vals[i], fill, ".95", subject_label, code))
        short = _chart_label(sh.labels.get(code, code))
        words = short.split()
        if len(short) > 13 and len(words) > 1:
            # Two lines rather than an ellipsis: "Other or unk..." names nothing.
            cut = min(range(1, len(words)),
                      key=lambda i: abs(len(" ".join(words[:i])) - len(short) / 2))
            labels.append(
                f'<text x="{cx:.1f}" y="{h - 20}" class="tick mid">'
                f'<tspan x="{cx:.1f}">{e(" ".join(words[:cut]))}</tspan>'
                f'<tspan x="{cx:.1f}" dy="12">{e(" ".join(words[cut:]))}</tspan></text>')
        else:
            labels.append(f'<text x="{cx:.1f}" y="{h - 14}" class="tick mid">{e(short)}</text>')

    ref = ""
    if reference_pct is not None:
        y = y0 - (y0 - y1) * reference_pct / top
        ref = (f'<line class="refline" x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}"'
               f'/>'
               f'<text x="{x1:.1f}" y="{y - 6:.1f}" class="tick end">{e(reference_label)}</text>')

    legend = ""
    if cmp_vals:
        legend = (
            f'<rect x="{x0:.1f}" y="9" width="11" height="11" rx="2" fill="var(--data-1)" opacity=".95"/>'
            f'<text x="{x0 + 17:.1f}" y="19" class="legend">{e(subject_label)}</text>'
            f'<rect x="{x0 + 34 + 6.6 * len(subject_label):.1f}" y="9" width="11" height="11" '
            f'rx="2" fill="var(--data-seq-2)"/>'
            f'<text x="{x0 + 51 + 6.6 * len(subject_label):.1f}" y="19" class="legend">'
            f'{e(comparison_label)}</text>'
        )

    axis = "".join(
        f'<text x="{x0 - 10:.1f}" y="{y0 - (y0 - y1) * i / 4 + 4:.1f}" class="tick end">'
        f'{top * i / 4:.0f}%</text>' for i in range(5)
    )
    return f"""<svg viewBox="0 0 {w} {h}" role="img" aria-label="Category shares">
  {_gridlines(x0, x1, y0, y1)}
  <line class="axis" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y0:.1f}"/>
  {"".join(bars)}{ref}{legend}{axis}{"".join(labels)}
</svg>"""


def income_dist_chart(sh: Shares, *, w: int = 900, h: int = 360, pad: int = 46) -> str:
    """The published explorer's income figure, rebuilt.

    Single series in the data blue, the share printed in white inside each bar,
    and a dashed line at 10% — an even spread across national deciles. A bar
    above the line means this place has more than its share of residents in
    that tenth of the national distribution.

    The one departure: a label only goes inside its bar when it fits. The
    original printed white text at a fixed offset regardless of bar height, so
    on a short bar it landed on the background.
    """
    codes = [c for c in sh.order if c != sh.residual]
    if not codes:
        return ""
    vals = [sh.share(c) or 0 for c in codes]
    top = max([*vals, 10.0]) * 1.18
    x0, x1 = pad + 22, w - pad
    y0, y1 = h - pad - 10, 22
    slot = (x1 - x0) / len(codes)
    bw = slot * 0.9

    bars, labels, ticks = [], [], []
    for i, (code, v) in enumerate(zip(codes, vals)):
        bh = (y0 - y1) * v / top
        x = x0 + slot * i + (slot - bw) / 2
        bars.append(
            f'<rect class="mark" x="{x:.1f}" y="{y0 - bh:.1f}" width="{bw:.1f}" '
            f'height="{bh:.1f}" fill="var(--data-1)" data-k="Share of residents" '
            f'data-v="{pct(v)}" data-x="Decile {e(code)}">'
            f'<title>{e(sh.labels.get(code, code))}: {pct(v)}</title></rect>'
        )
        inside = bh > 26
        labels.append(
            f'<text x="{x + bw / 2:.1f}" y="{(y0 - bh + 17) if inside else (y0 - bh - 7):.1f}" '
            f'class="tick mid {"on-bar" if inside else "strong"}">{v:.0f}%</text>'
        )
        ticks.append(f'<text x="{x + bw / 2:.1f}" y="{y0 + 17:.1f}" class="tick mid">{e(code)}</text>')

    axis = "".join(
        f'<text x="{x0 - 10:.1f}" y="{y0 - (y0 - y1) * i / 4 + 4:.1f}" class="tick end">'
        f'{top * i / 4:.0f}%</text>' for i in range(5)
    )
    return f"""<svg viewBox="0 0 {w} {h}" role="img" aria-label="Share of residents by national income decile">
  {_gridlines(x0, x1, y0, y1)}
  {"".join(bars)}
  <line class="axis" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y0:.1f}"/>
  {"".join(labels)}{"".join(ticks)}{axis}
  <text x="{(x0 + x1) / 2:.1f}" y="{h - 8}" class="tick mid axis-title">National income decile</text>
  <text x="14" y="{(y0 + y1) / 2:.1f}" class="tick axis-title"
        transform="rotate(-90 14 {(y0 + y1) / 2:.1f})" text-anchor="middle">Share of residents</text>
</svg>"""


def income_race_panels(cx: CrossShares, comparison: CrossShares | None,
                       comparison_label: str = "United States",
                       subject_label: str = "This place",
                       *, w: int = 900, cols: int = 3) -> str:
    """Small multiples, one panel per group, each normalized within its group.

    This is the published explorer's income-by-race figure. Because every panel
    sums to 100% inside its own group, the comparison is between the SHAPES of
    the distributions — which is the only comparison the data supports, since
    the groups are wildly different sizes.

    One change: the original dropped "Other or unknown" and used the free sixth
    cell for the legend. That category is 10-24% of population depending on the
    place, and dropping a group that large from a figure about groups is the
    same omission that inverted the old race comparison. It gets a panel; the
    legend moves to the top.
    """
    groups = list(cx.order_outer)
    if not groups:
        return ""
    rows = (len(groups) + cols - 1) // cols
    pw = (w - 16) / cols
    ph = 186
    head = 26
    h = head + rows * ph + 16

    top = 0.0
    for g in groups:
        for d in cx.order_inner:
            top = max(top, cx.share(g, d) or 0)
            if comparison:
                top = max(top, comparison.share(g, d) or 0)
    top = max(top, 12.0) * 1.16

    out = []
    if comparison:
        out.append(
            f'<rect x="8" y="6" width="11" height="11" rx="2" fill="var(--data-1)"/>'
            f'<text x="25" y="16" class="legend">{e(subject_label)}</text>'
            f'<rect x="{34 + 6.6 * len(subject_label):.1f}" y="6" width="11" height="11" '
            f'rx="2" fill="var(--data-seq-2)"/>'
            f'<text x="{51 + 6.6 * len(subject_label):.1f}" y="16" class="legend">'
            f'{e(comparison_label)}</text>'
        )

    for k, g in enumerate(groups):
        gx = 8 + (k % cols) * pw
        gy = head + (k // cols) * ph
        px0, px1 = gx + 36, gx + pw - 6
        py0, py1 = gy + ph - 34, gy + 24
        slot = (px1 - px0) / len(cx.order_inner)
        bw = slot * (0.38 if comparison else 0.72)

        out.append(
            f'<text x="{gx + pw / 2:.1f}" y="{gy + 13:.1f}" class="panel-title">'
            f'{e(_chart_label(cx.labels_outer.get(g, g)))}</text>'
        )
        # A single number floating at the top of a panel is not an axis. Label
        # the baseline, the midpoint and the top, on every panel, so a bar can
        # be read without counting gridlines.
        for i in range(1, 5):
            yy = py0 - (py0 - py1) * i / 4
            out.append(f'<line class="gridline" x1="{px0:.1f}" y1="{yy:.1f}" '
                       f'x2="{px1:.1f}" y2="{yy:.1f}"/>')
        for frac in (0, 0.5, 1):
            yy = py0 - (py0 - py1) * frac
            out.append(f'<text x="{px0 - 6:.1f}" y="{yy + 4:.1f}" class="tick end">'
                       f'{top * frac:.0f}%</text>')

        for i, d in enumerate(cx.order_inner):
            cxx = px0 + slot * (i + .5)
            v = cx.share(g, d) or 0
            bh = (py0 - py1) * v / top
            if comparison:
                cv = comparison.share(g, d) or 0
                ch = (py0 - py1) * cv / top
                lbl = f"{_chart_label(cx.labels_outer.get(g, g))} · decile {d}"
                out.append(f'<rect class="mark" x="{cxx - bw - .8:.1f}" y="{py0 - bh:.1f}" '
                           f'width="{bw:.1f}" height="{bh:.1f}" fill="var(--data-1)" '
                           f'data-k="{e(subject_label)}" data-v="{pct(v)}" data-x="{e(lbl)}">'
                           f'<title>{e(subject_label)} — decile {e(d)}: {pct(v)}</title></rect>')
                out.append(f'<rect class="mark" x="{cxx + .8:.1f}" y="{py0 - ch:.1f}" '
                           f'width="{bw:.1f}" height="{ch:.1f}" fill="var(--data-seq-2)" '
                           f'data-k="{e(comparison_label)}" data-v="{pct(cv)}" data-x="{e(lbl)}">'
                           f'<title>{e(comparison_label)} — decile {e(d)}: {pct(cv)}</title></rect>')
            else:
                out.append(f'<rect class="mark" x="{cxx - bw / 2:.1f}" y="{py0 - bh:.1f}" '
                           f'width="{bw:.1f}" height="{bh:.1f}" fill="var(--data-1)" '
                           f'data-k="{e(cx.labels_outer.get(g, g))}" data-v="{pct(v)}" '
                           f'data-x="Decile {e(d)}"><title>decile {e(d)}: {pct(v)}</title></rect>')
            # Every decile labelled: a reader tracing a bar back to a number
            # should not have to count from the nearest tick.
            out.append(f'<text x="{cxx:.1f}" y="{py0 + 14:.1f}" '
                       f'class="tick mid panel-tick">{e(d)}</text>')

        out.append(f'<line class="axis" x1="{px0:.1f}" y1="{py0:.1f}" x2="{px1:.1f}" y2="{py0:.1f}"/>')
        out.append(f'<text x="{gx + pw / 2:.1f}" y="{py0 + 28:.1f}" class="tick mid axis-title">'
                   f'Income decile</text>')

    return (f'<svg viewBox="0 0 {w} {h:.0f}" role="img" '
            f'aria-label="Income distribution by race and ethnicity">{"".join(out)}</svg>')


# --------------------------------------------------------------------------
# Maps
# --------------------------------------------------------------------------
#
# What a reader is asking: where in this place do people live. Three things
# make that answerable, and the first draft of this had none of them.
#
#   The outline. Cells floating in space are not a map. With the county drawn,
#   a reader can find the town, the corridor, the empty half.
#
#   Full cell resolution. Aggregating for display made the emptiest areas into
#   the largest, most prominent rectangles — backwards, since the big block
#   means "almost nobody here". Cells are aggregated for their VALUE, to damp
#   the noise, then painted back at their own size, so settlement reads as
#   texture instead of a pixelated grid.
#
#   A log scale. Density spans three or four orders of magnitude inside one
#   county. On quantile breaks every place looks equally varied, which tells
#   the reader nothing; on a log ramp a city reads as a city.

# A dedicated density ramp. The shared sequential blue is right for a bar chart
# with a handful of marks, but a map asks more of a palette: five levels have to
# stay apart across thousands of small squares, and a single hue's lightest
# steps cannot do it.
#
# YlGnBu, the ColorBrewer multi-hue sequential — pale yellow through green and
# teal into deep blue. Multi-hue separates the middle of the range far better
# than a single ramp, it is colorblind-safe, and it lands in the same blue
# family the charts already use, so the map belongs to the page rather than
# arriving from somewhere else.
#
# Generated alongside the logo variables rather than written into explorer.css,
# which forbids literal colors. A candidate to move upstream into tokens.css if
# the Gridded EIF site ever draws a choropleth.
# The published explorer's maps used `tm_scale_continuous(values = "turbo")`
# — Google's Turbo colormap — and that vibrancy is what made them readable at a
# glance. This is Turbo with the dark-purple head trimmed (a white "nobody
# lives here" cell next to near-black would invert the reading) and chroma
# pulled to 90% so it sits beside the site's blues without shouting over them.
#
# Turbo is not monotone in lightness, which is the usual objection to it. Here
# the ordering is carried by hue — the familiar cool-to-warm heat ramp — and
# stated in a labelled legend, exactly as the published figures did.
DENSITY_RAMP = ["#496ad2", "#47adec", "#47dcb2", "#70f572",
                "#c5e654", "#f0a242", "#9d331b"]
# A cell absent from the source is a TRUE ZERO, not missing data. The user
# guide is explicit: "Only cells which exist in the underlying EIF microdata
# will be populated in the gridded EIF files - if a demographic group by grid
# cell entry does not exist, this is a true zero", and §7.2.2 calls such a
# combination "structurally zero (no noise is added)". Albemarle bears it out:
# the crosswalk assigns 1,556 cells, 1,440 carry data, and not one row comes
# back non-positive -- the file is sparse by construction, never censored.
#
# So zero belongs ON the scale, at its low end, rather than in a white void
# beside it. Drawing it white made empty land the most prominent thing on the
# map; drawing it as the scale's blue makes the map a continuous surface, which
# is what a population density surface is.
DENSITY_EMPTY = DENSITY_RAMP[0]
DENSITY_STEPS = [f"--dens-{i}" for i in range(len(DENSITY_RAMP))]
# Cells inside the place with nobody in them. Drawn, not omitted: an absent
# cell reads as "outside the county", and the difference between empty land and
# somewhere else entirely is most of what a map of settlement is saying.
EMPTY_FILL = "var(--dens-empty)"
GROUND_FILL = "var(--surface)"


def _cell_key(lon: float, lat: float, step: float | None = None) -> tuple[int, int]:
    """A stable integer key for a cell centre.

    Centres land on .005, so `round(lon * 100)` sits exactly on a .5 boundary
    and Python rounds half to even -- -78.225 and -78.235 can land on the same
    key. Keying off the cell INDEX instead is exact.

    `step` is the lattice being keyed against, which is the source cell size
    for a full-resolution map and a multiple of it for a coarsened panel.
    """
    from pipeline import grid
    step = grid.CELL_DEG if step is None else step
    return (floor(lon / step), floor(lat / step))


def zero_fill(cells_xy, outline, step: float = 0.01):
    """Every cell inside the outline, populated or not.

    The source lists only cells where someone lives -- an absent cell is a true
    zero, not missing data -- so a map drawn straight from it shows settlement
    with no ground under it. This fills in the rest.

    The lattice is taken FROM THE DATA, not from the bounding box. Source
    coordinates are cell centres (`centroid_offset: 0.005`; cell edges land on
    exact multiples of 0.01), so a lattice built on multiples of 0.01 is offset
    from the data by half a cell and the two sets interleave instead of tiling
    -- which showed up as white speckle between the squares.
    """
    from pipeline import grid, shapes
    if not outline:
        return cells_xy, []
    have = {_cell_key(lon, lat, step) for lon, lat, _, _ in cells_xy}
    w0, e0, s0, n0 = shapes.bbox(outline)
    # Phase the lattice onto the cell centres the data itself uses.
    ox = cells_xy[0][0] if cells_xy else step / 2
    oy = cells_xy[0][1] if cells_xy else step / 2
    x0 = ox + step * floor((w0 - ox) / step)
    y0 = oy + step * floor((s0 - oy) / step)
    empties = []
    j = 0
    while (y := y0 + j * step) <= n0 + step:
        i = 0
        while (x := x0 + i * step) <= e0 + step:
            if _cell_key(x, y, step) not in have and shapes.contains(outline, x, y):
                empties.append((x, y))
            i += 1
        j += 1
    return cells_xy, empties


# Fine enough for an independent city a few kilometres across, coarse enough
# for an Alaskan census area twenty degrees wide. Ascending, and the first
# step that fits the target wins, so the frame gets as many meridians as it
# can carry without crowding.
GRAT_STEPS = (0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20)


def grat_step(extent, target: int = 6, axis: str = "both") -> float:
    """A meridian spacing that yields about `target` lines across the frame.

    Fixed at half a degree, this drew forty labelled meridians across
    Yukon-Koyukuk and the labels collapsed into an unreadable smear along the
    bottom edge -- and none at all across Manassas Park, which is a thirtieth
    of a degree wide and contained no multiple of the step. The step has to
    follow the span in both directions.
    """
    w0, e0, s0, n0 = extent
    span = {"x": e0 - w0, "y": n0 - s0}.get(axis, max(e0 - w0, n0 - s0))
    for step in GRAT_STEPS:
        if span / step <= target:
            return step
    return GRAT_STEPS[-1]


def _grat_places(step: float) -> int:
    """Decimals a label needs: enough to distinguish adjacent meridians."""
    return 0 if step >= 1 else (1 if step >= 0.1 else 2)


def _graticule(px, py, extent, w, h):
    """Whole and half degrees, labelled. Cheap orientation, and the published
    figures carried coordinates too."""
    w0, e0, s0, n0 = extent
    # Each axis picks its own spacing, and picks it in PIXELS. Apache County
    # is three and a half degrees tall and under one wide, so the projection
    # fits it by height and draws it about a hundred pixels across: spacing
    # judged on the degree span put four meridian labels in that hundred
    # pixels and they overlapped into a smear.
    drawn_w = max(1.0, px(e0) - px(w0))
    drawn_h = max(1.0, py(s0) - py(n0))
    # A meridian label is about 50px wide and a parallel about 12px tall, but
    # the limit is legibility rather than collision: fifteen parallels down a
    # county map is clutter, not orientation.
    step = grat_step(extent, target=max(1, int(drawn_w / 110)), axis="x")
    lat_step = grat_step(extent, target=max(1, int(drawn_h / 90)), axis="y")
    lines, labels = [], []
    v = math.ceil(w0 / step) * step
    while v <= e0:
        x = px(v)
        lines.append(f'<line class="grat" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h}"/>')
        # Wrapped back into [-180, 180) so a frame drawn east of the
        # antimeridian still labels its meridians the way an atlas does.
        d = wrap_lon(v)
        labels.append(f'<text x="{x:.1f}" y="{h - 6}" class="tick mid grat-label">'
                      f'{abs(d):.{_grat_places(step)}f}°{"W" if d < 0 else "E"}</text>')
        v += step
    v = math.ceil(s0 / lat_step) * lat_step
    while v <= n0:
        y = py(v)
        lines.append(f'<line class="grat" x1="0" y1="{y:.1f}" x2="{w}" y2="{y:.1f}"/>')
        labels.append(f'<text x="6" y="{y + 12:.1f}" class="tick grat-label">'
                      f'{abs(v):.{_grat_places(lat_step)}f}°{"N" if v > 0 else "S"}</text>')
        v += lat_step
    return "".join(lines), "".join(labels)


def _project(extent, w, h, pad):
    """Equirectangular, with the longitude squeeze that keeps a county its own shape."""
    import math
    w0, e0, s0, n0 = extent
    k = math.cos(math.radians((s0 + n0) / 2))
    span_x, span_y = max(1e-9, (e0 - w0) * k), max(1e-9, n0 - s0)
    scale = min((w - 2 * pad) / span_x, (h - 2 * pad) / span_y)
    ox = (w - span_x * scale) / 2
    oy = (h - span_y * scale) / 2

    def px(lon): return ox + (lon - w0) * k * scale
    def py(lat): return h - oy - (lat - s0) * scale
    return px, py, scale, k


def _log_bins(values, n):
    """Break points on a log scale, spanning the data actually present."""
    import math
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return []
    lo = max(vals[int(len(vals) * 0.02)], 0.5)
    hi = max(vals[-1], lo * 10)
    l0, l1 = math.log10(lo), math.log10(hi)
    return [10 ** (l0 + (l1 - l0) * (i + 1) / n) for i in range(n - 1)]


# Height of the band above a map that holds its color key.
KEY_BAND = 52
_CLIP_N = 0


def map_chart(cells_xy, *, label: str, extent, outline=(), context=(), roads=(),
              towns=(), w: int = 900, h: int = 560, interactive: bool = True,
              pad: int = 10, bins=None, legend: bool = True,
              graticule: bool = True, span: float = 0.01,
              empty_fill: str | None = None, palette: list[str] | None = None) -> str:
    """Cells painted inside the place's own outline.

    `cells_xy` is (lon, lat, display_density, true_count) per cell.
    """
    if not cells_xy:
        return ""
    px, py, scale, k = _project(extent, w, h, pad)
    # Source coordinates are cell CENTRES, so a square is drawn half a cell to
    # each side of its point. Treating them as corners shifted every map half a
    # kilometre north-east and left the zero-fill lattice out of phase.
    half = span / 2
    cw = max(1.0, span * k * scale)
    ch = max(1.0, span * scale)

    cuts = bins if bins is not None else _log_bins([c[2] for c in cells_xy], len(DENSITY_STEPS))

    def bucket(d):
        for i, c in enumerate(cuts):
            if d <= c:
                return i
        return len(cuts)

    def ring_path(ring):
        return " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}"
                        for i, (x, y) in enumerate(ring)) + " Z"

    # Places around this one, so the map sits somewhere rather than floating.
    around = "".join(
        f'<path d="{ring_path(r)}" fill="none" stroke="var(--line)" stroke-width=".8"/>'
        for rs in context for r in rs)

    paths = [ring_path(r) for r in outline]
    # Cells are assigned to a place by their centre, so an edge square can
    # stick out past the outline. Clip the cells to it.
    global _CLIP_N
    _CLIP_N += 1
    clip_id = f"clip{_CLIP_N}"
    clip = (f'<clipPath id="{clip_id}">' + "".join(f'<path d="{d}"/>' for d in paths)
            + "</clipPath>") if paths else ""
    clipped = f' clip-path="url(#{clip_id})"' if paths else ""
    ground = "".join(f'<path d="{d}" fill="{GROUND_FILL}" stroke="none"/>' for d in paths)
    edge = "".join(f'<path d="{d}" fill="none" stroke="var(--ink-muted)" '
                   f'stroke-width="1.3" stroke-linejoin="round"/>' for d in paths)

    grat_lines, grat_labels = _graticule(px, py, extent, w, h) if graticule else ("", "")

    # Unpopulated cells inside the place, drawn first so settlement sits on top.
    _, empties = zero_fill(cells_xy, outline, step=span)
    blank = ""
    if empties:
        # One flat field at the low end of the scale. These are true zeros, so
        # they read as the bottom of the density surface rather than as holes
        # in it -- no stroke, nothing to distinguish them from the cells that
        # hold one or two people, because on a log density scale nothing does.
        blank = ('<path class="cell empty" fill="' + (empty_fill or EMPTY_FILL) + '" d="'
                 + "".join(f"M{px(x - half):.1f},{py(y + half):.1f}"
                           f"h{cw:.2f}v{ch:.2f}h-{cw:.2f}Z"
                           for x, y in empties) + '"/>')

    # One path per colour, never one element per cell. At 200 bytes a rect a
    # county map ran to 283 KB; five paths and a lookup table run to a fifth of
    # that, and the DOM goes from thousands of nodes to five.
    buckets: dict[int, list[str]] = {}
    for lon, lat, dens, _count in cells_xy:
        x, y = px(lon - half), py(lat + half)
        buckets.setdefault(bucket(dens), []).append(
            f"M{x:.1f},{y:.1f}h{cw:.2f}v{ch:.2f}h-{cw:.2f}Z")
    marks = [f'<path class="cell" fill="{palette[i] if palette else f"var({DENSITY_STEPS[i]})"}" '
             f'd="{"".join(d)}"/>' for i, d in sorted(buckets.items())]

    # Hover is arithmetic, not hit-testing: the cells sit on a regular lattice,
    # so a pointer position converts straight to a column and row. The table
    # below is what the script looks the answer up in.
    hover = ""
    if interactive:
        import json as _json
        xs = [px(c[0]) for c in cells_xy]
        ys = [py(c[1] + span) for c in cells_xy]
        x0, y0 = min(xs), min(ys)
        table = {}
        for (lon, lat, dens, count), x, y in zip(cells_xy, xs, ys):
            col, row = round((x - x0) / cw), round((y - y0) / ch)
            table[f"{col},{row}"] = [round(dens), round(count)]
        band_y = KEY_BAND if (legend and cuts) else 0
        hover = (' data-hover="' + e(_json.dumps({
            "x0": round(x0, 1), "y0": round(y0 + band_y, 1),
            "cw": round(cw, 3), "ch": round(ch, 3),
            "label": label, "cells": table}, separators=(",", ":"))) + '"')

    key = ""
    if legend and cuts:
        # Top-left, clear of the longitude labels along the bottom and the
        # latitude labels down the left edge, which the legend used to sit on.
        # In a band of its own above the map, never over it: a rectangular
        # county fills its frame, and Randall's Amarillo sat under the key.
        sw, sh_ = 46, 11
        x0, ly = w - 12 - sw * len(DENSITY_STEPS), 20
        swatches = "".join(
            f'<rect x="{x0 + i * sw:.0f}" y="{ly}" width="{sw}" height="{sh_}" '
            f'fill="var({tok})"/>' for i, tok in enumerate(DENSITY_STEPS))
        # The first swatch starts at zero, so say so: it is the one number on
        # this scale a reader is most likely to want and least likely to guess.
        ticks = (f'<text x="{x0}" y="{ly + sh_ + 12}" class="tick mid">0</text>'
                 + "".join(
                     f'<text x="{x0 + (i + 1) * sw:.0f}" y="{ly + sh_ + 12}" '
                     f'class="tick mid">{num(c)}</text>' for i, c in enumerate(cuts)))
        key = (f'<rect x="{x0 - 9}" y="{ly - 17}" width="{sw * len(DENSITY_STEPS) + 18}" '
               f'height="47" fill="var(--surface)" opacity=".93" rx="4"/>'
               f'<text x="{x0}" y="{ly - 5}" class="tick">People per square kilometer</text>'
               f'{swatches}{ticks}')

    # Landmarks over the data, not under it: a reader locates the interstate
    # and the towns first, then reads the pattern against them. Kept thin and
    # grey so they never compete with the thing being shown.
    road_paths = "".join(
        '<path class="road" d="'
        + " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}"
                   for i, (x, y) in enumerate(line)) + '"/>'
        for line in roads)
    town_marks = "".join(
        f'<circle class="town-dot" cx="{px(lon):.1f}" cy="{py(lat):.1f}" r="2.6"/>'
        f'<text class="town" x="{px(lon) + 5:.1f}" y="{py(lat) + 3.5:.1f}">{e(name)}</text>'
        for name, lon, lat in towns)

    band = KEY_BAND if key else 0
    return (f'<svg viewBox="0 0 {w} {h + band}" role="img" class="mapfig"{hover} '
            f'aria-label="{e(label)}">{key}<g transform="translate(0,{band})">'
            f'<defs>{clip}</defs>{grat_lines}{around}{ground}'
            f'<g{clipped}>{blank}{"".join(marks)}</g>'
            f'{road_paths}{edge}{town_marks}{grat_labels}</g></svg>')


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def _headline_year(f: PlaceFigures, ds_id: str) -> int | None:
    return next((d.headline_year for s in f.geo.sections for d in s.datasets
                 if d.id == ds_id), None)


# Population over total US land area, 2025. Computed from the same TIGER areas
# the pages use, not carried as a constant — the published site hardcoded 37
# and it went stale silently.
US_DENSITY = 36.6


def _density(f: PlaceFigures, year: int) -> str:
    pop = f.population.at(year) if f.population else None
    if pop is None:
        return ""
    said = [f"In {year}, {_prose_name(f)} was home to <strong>{num(pop)}</strong> people."]
    if f.land_km2:
        conv = pop / f.land_km2
        c = narrative.Claim.of(conv, US_DENSITY, tolerance=US_DENSITY * 0.15,
                               higher="above", lower="below", similar="close to")
        said.append(
            f"That is <strong>{density_num(conv)}</strong> people per square kilometer of land, "
            f"{c.word if c else 'against'} the national average of {num(US_DENSITY, 1)}.")
    else:
        said.append('Land area is unavailable, so density over total area is '
                    'not shown (<span class="tbd">QQ</span>).')
    if f.grid is not None and len(f.grid) and f.grid.land_km2:
        lived = f.grid.total / f.grid.land_km2
        said.append(
            f"Counting only the {num(f.grid.land_km2)} square kilometers where people "
            f"actually live, density is <strong>{density_num(lived)}</strong> per square "
            "kilometer.")
    return " ".join(said)


def _population(f: PlaceFigures, extra: str = "") -> str:
    s = f.population
    if s is None or not s.years:
        return ""
    end = max(s.years)
    since = GROWTH_BASE if GROWTH_BASE in s.years else min(s.years)
    cagr = s.cagr(since, end)
    change = narrative.describe_change(s, since, end)

    # The count opens "Size and density" directly above, so this paragraph
    # starts with the change rather than repeating the level.
    said = []
    start, finish = s.at(since), s.at(end)
    if change and start is not None and finish is not None:
        said.append(
            f"The population {change.word} from <strong>{num(start)}</strong> in "
            f"{since} to <strong>{num(finish)}</strong> in {end}"
            # The verb carries the sign: "shrank ... 0.10% a year", not -0.10%.
            + (f", {pct(abs(cagr) * 100, 2)} a year." if cagr is not None else ".")
        )
    # State and nation in one sentence. Two sentences of "That is faster than
    # X, which grew Y a year" read as two findings when they are one.
    against = []
    for key, label in (("state", "the state"), ("nation", "the nation")):
        ref = f.comparisons.get(key)
        if ref is None:
            continue
        c = narrative.compare_growth(f, ref, since, end)
        if c:
            against.append((c, label))
    # One sentence however the two comparisons come out: "faster than the
    # state (1.55% a year) and the nation (0.82%)", or "about the same pace as
    # the state (1.55% a year) and faster than the nation (0.82%)".
    parts, prev = [], None
    for i, (c, label) in enumerate(against):
        rate = f"({pct(c.reference, 2)}{' a year' if i == 0 else ''})"
        word = "about the same pace as" if c.is_similar else f"{c.word} than"
        parts.append(f"{label} {rate}" if word == prev else f"{word} {label} {rate}")
        prev = word
    if parts:
        said.append(f"That is {' and '.join(parts)}.")
    # No growth-by-decade sentence: before 2015 the decades mostly measured
    # the records' coverage catching up, and read as a slowdown that wasn't.

    notes = (f"The line shows annual total population from {BASE_YEAR} through {end}. "
             "Counts come from administrative records with privacy noise infused, and "
             "may not match the Decennial Census or the Population Estimates Program. "
             "Coverage of those records is high and has improved over time, with "
             "increases in 2004, when information returns became available, and in 2015, "
             "when commercial data were first incorporated (Voorheis et al. 2026).")
    fig_intro = (f"FIGREF_NEXT plots the population of {_prose_name(f)} in every year from "
                 f"{BASE_YEAR} to {end}.")

    body = line_chart(s)

    return f"""<section class="section" id="population"><h2>Population</h2>
  <h3>Size and density</h3>
  <p class="lead">{_density(f, end)}</p>

  <h3>Growth since {since}</h3>
  <p class="lead">{" ".join(said)}</p>
  <p class="lead">{fig_intro}</p>
  {figure(title=f"Population Trend in {f.name}",
          subtitle=f"Annual Population Counts, {BASE_YEAR}–{end}",
          body=body, notes=notes)}
  {extra}</section>"""


def _table(sh: Shares, f: PlaceFigures, ds_id: str, title: str,
           caption: str = "") -> str:
    """The table, headed by what it holds.

    A table arriving with no title makes the reader infer its subject from the
    column heads; the published pages titled theirs, and so do these.
    """
    caption = caption or f"{title} in {f.name}, {_headline_year(f, ds_id)}"
    rows = []
    ref_head = "United States" if "nation" in f.comparisons else "State"
    for c in sh.order:
        claim, ref_cell = None, "<td class='num'>—</td>"
        for key in ("state", "nation"):
            ref = f.comparisons.get(key)
            if ref is None or ds_id not in ref.shares:
                continue
            try:
                claim = narrative.compare_share(sh, ref.shares[ds_id], c)
            except narrative.IncomparableError:
                claim = None
            if claim:
                ref_cell = f"<td class='num'>{pct(claim.reference)}</td>"
        word = f"<td class='word'>{claim.word if claim else '—'}</td>"
        cls = " class='residual'" if c == sh.residual else ""
        rows.append(f"<tr{cls}><td>{e(sh.labels.get(c, c))}</td>"
                    f"<td class='num'>{num(sh.categories[c])}</td>"
                    f"<td class='num'>{pct(sh.share(c))}</td>{ref_cell}{word}</tr>")
    return f"""<p class="table-title">{e(caption)}</p>
  <div class="panel"><div class="table-scroll"><table>
    <thead><tr><th>{e(title)}</th><th class="num">People</th><th class="num">Share</th>
      <th class="num">{e(ref_head)}</th><th>Compared</th></tr></thead>
    <tbody>{"".join(rows)}</tbody></table></div></div>"""


def _shares_section(f: PlaceFigures, ds_id: str, title: str, *,
                    fig_title: str, subtitle: str, notes: str,
                    reference_pct: float | None = None, reference_label: str = "",
                    intro: str = "", body: str | None = None,
                    hide: str | None = None, extra: str = "",
                    table_intro: str = "") -> str:
    sh = f.shares.get(ds_id)
    if sh is None:
        return ""
    comparison, comparison_label = None, ""
    for key in ("nation", "state"):
        ref = f.comparisons.get(key)
        if ref and ds_id in ref.shares and ref.shares[ds_id].universe == sh.universe:
            comparison = ref.shares[ds_id]
            comparison_label = "United States" if key == "nation" else "State"
            break

    if body is None:
        body = bar_chart(sh, reference_pct=reference_pct, reference_label=reference_label,
                         comparison=comparison, comparison_label=comparison_label,
                         subject_label=f.name, hide=hide)
    return f"""<section class="section" id="{e(ds_id)}"><h2>{e(title)}</h2>
  {f'<p class="lead">{intro}</p>' if intro else ""}
  {figure(title=fig_title, subtitle=subtitle,
          body=body, notes=notes)}
  {f'<p class="lead">{table_intro}</p>' if table_intro else ""}
  {_table(sh, f, ds_id, title)}
  {extra}</section>"""


def _age(f: PlaceFigures, extra: str = "") -> str:
    sh = f.shares.get("age_structure")
    if sh is None:
        return ""
    residual = e(sh.labels.get(sh.residual, "")) if sh.residual else ""
    year = _headline_year(f, "age_structure")
    trends = f.trends.get("age_structure")


    # A paragraph per group: share, how it compares, and which way it has moved.
    # The published pages reported the last of these as a percentage "growing
    # by X%", which read as a rate; it is a change in share, so it is stated in
    # percentage points here.
    ref_sh = None
    for key in ("nation", "state"):
        other = f.comparisons.get(key)
        if other and "age_structure" in other.shares:
            ref_sh = other.shares["age_structure"]
            break
    paras = []
    for code in sh.order:
        if code == sh.residual:
            continue
        share = sh.share(code)
        if share is None:
            continue
        bits = [(f"In {year}, <strong>{pct(share)}</strong> of {e(f.name)} residents were "
                 f"{e(sh.labels.get(code, code)).lower()}")]
        if ref_sh:
            try:
                cl = narrative.compare_share(sh, ref_sh, code)
            except narrative.IncomparableError:
                cl = None
            if cl:
                bits.append(
                    f", about the same as the country's {pct(cl.reference)}"
                    if cl.is_similar else
                    f", a {cl.word} share than the country's {pct(cl.reference)}")
        bits.append(".")
        ser = trends.get(code) if trends else None
        since = GROWTH_BASE if ser is not None and GROWTH_BASE in ser.years else None
        if ser is not None and since is not None and since != ser.years[-1]:
            d = ser.values[-1] - ser.at(since)
            if abs(d) > 0.3:
                # Percentage POINTS. A share moving from 13% to 21% has not
                # "grown 8%" — the published pages said exactly that, and it is
                # the same conflation that made their growth figures unreadable.
                bits.append(f" That share has {'risen' if d > 0 else 'fallen'} "
                            f"{num(abs(d), 1)} percentage points since {since}.")
            else:
                bits.append(f" That share has barely moved since {since}.")
        paras.append(f'<p class="lead">{"".join(bits)}</p>')
    detail = "".join(paras)

    trend_fig = ""
    if trends:
        first = next(iter(trends.values()))
        trend_fig = figure(
                        title=f"Age Composition of {f.name}",
            subtitle=f"Population Shares by Age Group, {first.years[0]}–{first.years[-1]}",
            body=share_trend(trends, sh.labels, sh.residual, AGE_LINE_TOKENS, top=80.0,
                             counts=sh.categories),
            notes=("Lines show the share of residents in each age group, year by year. "
                   "Residents whose age is not reported stay in the denominator but are "
                   "not drawn, so the lines do not sum to 100 percent."))
        trend_fig = (f'<p class="lead">FIGREF_NEXT shows the share of {e(f.name)} '
                     f'residents in each age group in every year from {first.years[0]} '
                     f'to {first.years[-1]}.</p>\n  ' + trend_fig)

    # The trend answers a question the snapshot cannot — whether a place is
    # ageing — so both are shown.
    snapshot = _shares_section(
        f, "age_structure", "Age",
        fig_title=f"Age Composition of {f.name} Compared to the U.S.",
        subtitle=f"Population Shares by Age Group, {year}",
        hide=sh.residual,
        intro=(f"FIGREF_NEXT compares the age composition of {_prose_name(f)} in {year} "
               "with the nation&rsquo;s."),
        table_intro=(f"The table below gives the number of residents in each age group in {year}, "
                     "their share of all residents, and the same share nationwide."),
        notes=("Bars show the share of residents in each age group. Residents whose age "
               f"is not reported — {pct(sh.share(sh.residual))} here — stay in the "
               "denominator but are not drawn, so the bars total slightly under 100 "
               "percent."
               if sh.residual and sh.share(sh.residual) else
               "Bars show the share of residents in each age group."))
    return snapshot.replace('<h2>Age</h2>', f'<h2>Age</h2>\n  {detail}\n  {trend_fig}', 1)


def _prose_name(f: PlaceFigures) -> str:
    """The place's name as it sits in a sentence.

    "Charlottesville, VA Metro Area was home to..." needs an article; a county
    or state does not. The registry says which geographies take one.
    """
    article = config.registry()["geographies"][f.geo.id].get("prose_article", "")
    return f"{article} {e(f.name)}" if article else e(f.name)


def _kind(f: PlaceFigures) -> str:
    """What the place is, for the eyebrow and in a sentence.

    The cbsa geography holds micropolitan areas as well as metros, and calling
    "Vernal, UT Micro Area" a metro area is simply wrong.
    """
    if f.geo.id == "cbsa" and f.name.endswith("Micro Area"):
        return "Micro area"
    return f.geo.label


def _chart_label(label: str) -> str:
    """The name a chart prints: the catalog's abbreviation where it gives one.

    "American Indian & Alaska Native (AIAN)" collides with its neighbors in a
    legend and is truncated on an axis, so charts say "AIAN" and the figure's
    note spells it out (AIAN_NOTE).
    """
    m = re.search(r"\(([^)]+)\)$", label)
    return m.group(1) if m else label


AIAN_NOTE = " AIAN is American Indian and Alaska Native."

# Share maps: a 0 to 100 percent scale on a square-root stretch, so the low
# shares where most squares fall get most of the color, in many fine steps
# that read as a gradient. Exactly zero has a color of its own.
SHARE_STEPS = 32


def _share_pos(v: float) -> float:
    """Where a share (percent) sits along the color ramp, 0 to 1."""
    return math.sqrt(max(0.0, min(100.0, v)) / 100)


SHARE_BREAKS = [0.0] + [100 * ((i + 1) / SHARE_STEPS) ** 2 for i in range(SHARE_STEPS - 1)]
SHARE_TICKS = [1, 5, 10, 25, 50, 75, 100]
SHARE_ZERO = "#d5d9e0"
# Light to dark and warm throughout, so a share map reads in the brand's
# colors: cream through gold, orange and red to the EIL maroon and past it.
# Viridis read as clearly but sat outside the scheme; inferno is the stock
# alternative with the same shape.
# The same blue-to-red ramp as the density maps, interpolated into fine steps
# so it reads as a gradient. Ember (cream to maroon) was tried and dropped.
SHARE_RAMPS = {
    "density": DENSITY_RAMP,
    "ember": ["#fdf3dc", "#fbd98c", "#f6b14e", "#ec8435", "#d4562a",
              "#b03024", "#861b1f", "#601215", "#3a0b0d"],
}
SHARE_RAMP = "density"


def _interp(anchors: list[str], n: int) -> list[str]:
    rgb = [tuple(int(a[i:i + 2], 16) for i in (1, 3, 5)) for a in anchors]
    out = []
    for k in range(n):
        t = k / (n - 1) * (len(rgb) - 1)
        i = min(int(t), len(rgb) - 2)
        f = t - i
        out.append("#" + "".join(f"{round(rgb[i][j] + (rgb[i + 1][j] - rgb[i][j]) * f):02x}"
                                 for j in range(3)))
    return out


def share_palette() -> list[str]:
    """Zero's own color, then the ramp in SHARE_STEPS steps."""
    return [SHARE_ZERO] + _interp(SHARE_RAMPS[SHARE_RAMP], SHARE_STEPS)


def _race(f: PlaceFigures, extra: str = "") -> str:
    sh = f.shares.get("race_ethnicity")
    if sh is None:
        return ""
    residual = e(sh.labels.get(sh.residual, "")) if sh.residual else ""
    trends = f.trends.get("race_ethnicity")

    ranked = sorted(((sh.share(c) or 0, c) for c in sh.order if c != sh.residual),
                    reverse=True)
    # No list of the largest groups: the bullets directly above give every
    # group's share against the national one, in order. What they cannot do is
    # say which gap is the widest, so that is all this adds.
    said = []
    ref_fig = f.comparisons.get("nation")
    if ref_fig and "race_ethnicity" in ref_fig.shares:
        diffs = []
        for _v, c in ranked:
            try:
                cl = narrative.compare_share(sh, ref_fig.shares["race_ethnicity"], c)
            except narrative.IncomparableError:
                continue
            if cl and not cl.is_similar:
                diffs.append((abs(cl.difference), c, cl))
        if diffs:
            _, c, cl = max(diffs, key=lambda t: t[0])
            said.append(f"The sharpest difference from the country is the "
                        f"{sh.labels.get(c, c).split(' (')[0]} share, {pct(cl.value)} "
                        f"here against {pct(cl.reference)} nationally.")
    if sh.residual and (rv := sh.share(sh.residual)):
        said.append(
            f"{pct(rv)} of residents fall in &ldquo;{e(sh.labels.get(sh.residual, ''))}.&rdquo;")
    lead = f'<p class="lead">{" ".join(said)}</p>' if said else ""

    trend_fig = ""
    if trends:
        first = next(iter(trends.values()))
        top = max(20.0, min(100.0, 10 * (1 + max(max(s.values) for s in trends.values()) // 10)))
        trend_fig = figure(
                        title=f"Racial and Ethnic Composition of {f.name}",
            subtitle=f"Population Shares, {first.years[0]}–{first.years[-1]}",
            body=share_trend(trends, sh.labels, sh.residual, RACE_LINE_TOKENS,
                             top=top, drop_residual=False, counts=sh.categories),
            notes=("Lines show each group's share of all residents, year by year. "
                   "Hispanic takes precedence; all other categories are non-Hispanic."
                   + AIAN_NOTE))
        trend_fig = (f'<p class="lead">FIGREF_NEXT shows each group&rsquo;s share of '
                     f'{e(f.name)} residents in every year from {first.years[0]} to '
                     f'{first.years[-1]}.</p>\n  ' + trend_fig)

    snapshot = _shares_section(
        f, "race_ethnicity", "Race and ethnicity", extra=extra,
        fig_title=f"Racial and Ethnic Composition of {f.name} Compared to the U.S.",
        subtitle=f"Population Shares, {_headline_year(f, 'race_ethnicity')}",
        notes=("Hispanic takes precedence; all other categories are non-Hispanic. Shares "
               "are of all residents, including those whose race is other or unknown, so "
               "they total 100 percent." + AIAN_NOTE),
        intro=(f"FIGREF_NEXT compares each group&rsquo;s share of {e(f.name)} residents "
               f"in {_headline_year(f, 'race_ethnicity')} with its share nationwide."),
        table_intro=(f"The table below gives the number of residents in each group in "
                     f"{_headline_year(f, 'race_ethnicity')}, their share of all residents, "
                     "and the same share nationwide."),
        )
    bullets = []
    listed = ranked + ([(sh.share(sh.residual) or 0, sh.residual)] if sh.residual else [])
    ref_sh = ref_fig.shares.get("race_ethnicity") if ref_fig else None
    for _v, c in listed:
        v = sh.share(c)
        if v is None:
            continue
        nat = ""
        if ref_sh:
            try:
                cl = narrative.compare_share(sh, ref_sh, c)
            except narrative.IncomparableError:
                cl = None
            if cl:
                nat = (f", compared with <strong>{pct(cl.reference)}</strong> nationwide"
                       + (" — about the same" if cl.is_similar
                          else f" — a {cl.word} share"))
        bullets.append(f"<li><b>{e(sh.labels.get(c, c))}</b> made up "
                       f"<strong>{pct(v)}</strong> of residents{nat}.</li>")

    intro = f"""<p class="lead">Residents are characterized as Hispanic, non-Hispanic White,
  non-Hispanic Black, non-Hispanic Asian, and non-Hispanic American Indian and
  Alaska Native. Hispanic takes precedence: anyone recorded as Hispanic is counted
  there regardless of race. A residual group covers everyone whose race is other
  or not recorded.</p>
  <ul class="findings">{"".join(bullets)}</ul>"""
    return snapshot.replace('<h2>Race and ethnicity</h2>',
                            f'<h2>Race and ethnicity</h2>\n  {intro}\n  {lead}\n  {trend_fig}', 1)


def _income(f: PlaceFigures, extra: str = "") -> str:
    sh = f.shares.get("income_composition")
    if sh is None:
        return ""
    year = _headline_year(f, "income_composition")
    bits = []
    # The upper half as a number against the nation's, not a label: "concentrated
    # in the upper half" fired at any gap over 2 points and called Vermont, at
    # 51 to 49, concentrated.
    nat = f.comparisons.get("nation")
    nat_sh = nat.shares.get("income_composition") if nat else None
    half = narrative.upper_half(sh, nat_sh)
    if half:
        word = ("about the same as" if half.is_similar else
                f"{half.word} than")
        bits.append(f"In {_prose_name(f)}, {pct(half.value)} of residents are in the upper half "
                    f"of the national income distribution, {word} the national "
                    f"{pct(half.reference)}.")
    top = narrative.concentration(sh, "10")
    if top and not top.is_similar:
        # A clear tilt toward the upper half beside a top decile under 10% read
        # as a contradiction; it is a real pattern, so say it is one.
        against = half and not half.is_similar and (
            (half.difference > 0 and top.difference < 0)
            or (half.difference < 0 and top.difference > 0))
        lead_in = ("Even so, only " if top.difference < 0 else "Even so, ") if against else ""
        bits.append(f"{lead_in}{pct(top.value)} are in the highest "
                    f"national income decile, against {pct(top.reference, 0)} nationally.")

    dist = figure(
                title=f"Income Distribution in {f.name}",
        subtitle=f"Share by National Adjusted Gross Income Deciles, {year}",
        body=income_dist_chart(sh),
        notes=("Bars show the share of residents in each decile of the national adjusted "
               "gross income (AGI) distribution. A value above 10 percent means this place "
               "has a higher-than-average share of residents in that national decile; below "
               "10 percent, a lower-than-average share."))

    by_race = ""
    cx = f.cross.get("income_composition")
    if cx:
        ref = None
        ref_label = ""
        for key in ("nation", "state"):
            other = f.comparisons.get(key)
            if other and "income_composition" in other.cross:
                ref = other.cross["income_composition"]
                ref_label = "United States" if key == "nation" else "State"
                break
        by_race = figure(
                        title=f"Income Distribution by Race in {f.name} Compared to the U.S."
                  if ref_label == "United States" else
                  f"Income Distribution by Race in {f.name}",
            subtitle=f"Share by National Adjusted Gross Income Deciles, {year}",
            body=income_race_panels(cx, ref, ref_label or "Comparison",
                                    subject_label=f.name),
            notes=("Bars show the share of each group's residents in each decile of the "
                   "national adjusted gross income (AGI) distribution, so every group's "
                   "bars sum to 100 percent. Hispanic takes precedence; all other "
                   "categories are non-Hispanic." + AIAN_NOTE))
        by_race = (f'<p class="lead">FIGREF_NEXT shows the same distribution for each racial '
                   f'and ethnic group, in {_prose_name(f)} and nationwide.</p>\n  ' + by_race)

    return f"""<section class="section" id="income_composition"><h2>Income</h2>
  <h3>What is measured</h3>
  <p class="lead">Adjusted Gross Income (AGI) is total income from all sources minus
  the <a href="https://www.irs.gov/e-file-providers/definition-of-adjusted-gross-income">specific
  adjustments</a> listed on Schedule 1 of IRS Form 1040, aggregated to the household.
</p>

  <h3>Distribution across national deciles</h3>
  <p class="lead">Income deciles divide U.S. households into ten equally sized groups by
  AGI. The first decile is the tenth of households with the lowest income; the tenth is
  the highest. Nationally, about 10 percent of residents fall in each decile, so a share
  above 10 percent means that part of the national income distribution is
  over-represented in {_prose_name(f)}.</p>
  <p class="lead">{" ".join(bits)}</p>
  <p class="lead">FIGREF_NEXT shows the share of {e(f.name)} residents in each national
  income decile in {year}.</p>
  {dist}
  <p class="lead">The table below gives the number of residents in each national income
  decile in {year}, their share of residents with an income record, and the same share
  nationwide.</p>
  {_table(sh, f, "income_composition", "Income distribution")}
  {by_race}
  {extra}</section>"""


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

SCRIPT = """
// Tabs and tooltips. Progressive enhancement on both: without this the panels
// are all visible and every mark still has a native <title>.
(function () {
  var strip = document.querySelector('.tabs');
  if (strip) {
    var tabs = [].slice.call(strip.querySelectorAll('.tab'));
    var show = function (id, push) {
      tabs.forEach(function (t) {
        var on = t.dataset.tab === id;
        t.classList.toggle('on', on);
        t.setAttribute('aria-selected', on);
        t.tabIndex = on ? 0 : -1;
      });
      [].forEach.call(document.querySelectorAll('.tabpanel'), function (p) {
        p.hidden = p.dataset.tab !== id;
      });
      if (push && history.replaceState) history.replaceState(null, '', '#' + id);
    };
    tabs.forEach(function (t) {
      t.addEventListener('click', function () { show(t.dataset.tab, true); });
      t.addEventListener('keydown', function (ev) {
        var i = tabs.indexOf(t), d = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return;
        ev.preventDefault();
        var next = tabs[(i + d + tabs.length) % tabs.length];
        next.focus(); show(next.dataset.tab, true);
      });
    });
    [].forEach.call(document.querySelectorAll('.tab-end-next'), function (a) {
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        show(a.dataset.goto, true);
        strip.scrollIntoView({behavior: 'smooth', block: 'start'});
      });
    });
    // A link into a section inside a hidden panel should open that panel.
    var hash = location.hash.slice(1);
    var target = hash && document.getElementById(hash);
    var panel = target && target.closest ? target.closest('.tabpanel') : null;
    show(panel ? panel.dataset.tab : (tabs[0] && tabs[0].dataset.tab));
    if (panel && target) target.scrollIntoView();
  }

  var tip = document.createElement('div');
  tip.className = 'tip';
  tip.setAttribute('role', 'status');
  document.body.appendChild(tip);
  var move = function (ev) {
    var x = ev.clientX + 14, y = ev.clientY + 16;
    var r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  };
  document.addEventListener('mouseover', function (ev) {
    var m = ev.target.closest && ev.target.closest('.mark');
    if (!m) return;
    tip.innerHTML = '<b>' + (m.dataset.v || '') + '</b><span>' +
      [m.dataset.k, m.dataset.x].filter(Boolean).join(' \u00b7 ') + '</span>';
    tip.classList.add('on'); move(ev);
  });
  document.addEventListener('mousemove', function (ev) {
    if (tip.classList.contains('on')) move(ev);
  });
  document.addEventListener('mouseout', function (ev) {
    if (ev.target.closest && ev.target.closest('.mark')) tip.classList.remove('on');
  });

  // Maps carry a lookup table instead of an element per cell. The cells sit on
  // a regular lattice, so a pointer position converts straight to a column and
  // row — no hit-testing, and five paths in the DOM instead of a few thousand
  // rects.
  document.addEventListener('mousemove', function (ev) {
    var svg = ev.target.closest && ev.target.closest('.mapfig[data-hover]');
    if (!svg) {
      if (tip.dataset.map) { tip.classList.remove('on'); delete tip.dataset.map; }
      return;
    }
    if (!svg._h) svg._h = JSON.parse(svg.dataset.hover);
    var h = svg._h, box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
    var s = box.width / vb.width;
    var mx = (ev.clientX - box.left) / s, my = (ev.clientY - box.top) / s;
    var hit = h.cells[Math.round((mx - h.x0) / h.cw) + ',' + Math.round((my - h.y0) / h.ch)];
    if (!hit) { tip.classList.remove('on'); delete tip.dataset.map; return; }
    tip.dataset.map = '1';
    // The count only: a square is one cell of about a square kilometer, so
    // its density was the same number again.
    tip.innerHTML = '<b>' + hit[1].toLocaleString() + ' people</b><span>in this square</span>';
    tip.classList.add('on');
    move(ev);
  });
})();
"""


def _number_figures(html: str) -> str:
    """Replace the placeholders with a running count, in document order.

    FIGURE_N is a figure's own label. FIGREF_NEXT is prose pointing at the
    figure that follows it, so an introduction names the right number however
    the figures above it come and go.
    """
    out, n = [], 0
    for part in re.split(r"(FIGURE_N|FIGREF_NEXT)", html):
        if part == "FIGURE_N":
            n += 1
            out.append(f"Figure {n}")
        elif part == "FIGREF_NEXT":
            out.append(f"Figure {n + 1}")
        else:
            out.append(part)
    return "".join(out)


def _tab_strip(sections: dict[str, str]) -> str:
    """Group the page's sections into the declared tab strip."""
    from pipeline import config as _cfg
    live = [(tid, label) for tid, label in _cfg.tabs() if sections.get(tid)]
    if len(live) < 2:
        return "".join(sections.values())
    strip = "".join(
        f'<button class="tab" role="tab" data-tab="{e(tid)}" '
        f'aria-controls="tab-{e(tid)}" aria-selected="false">{e(label)}</button>'
        for tid, label in live
    )
    # Each panel closes on the same maroon rule the strip opens with, so a
    # tab reads as finished rather than trailing off into the footer, and
    # offers the next tab. Plain anchors: without JavaScript every panel is
    # visible and the link simply scrolls to the next one.
    def end(i: int) -> str:
        nxt = (f'<a class="tab-end-next" href="#tab-{e(live[i + 1][0])}" '
               f'data-goto="{e(live[i + 1][0])}">Next: {e(live[i + 1][1])} &rarr;</a>'
               if i + 1 < len(live) else "")
        return (f'<nav class="tab-end" aria-label="End of section">'
                f'<a class="tab-end-top" href="#">&uarr; Back to top</a>{nxt}</nav>')
    panels = "".join(
        f'<div class="tabpanel" id="tab-{e(tid)}" role="tabpanel" data-tab="{e(tid)}">'
        f'{sections[tid]}{end(i)}</div>' for i, (tid, _) in enumerate(live)
    )
    return f'<div class="tabs" role="tablist">{strip}</div>{panels}'


def _cells_for(f: PlaceFigures, layer: str, mult: int = 1):
    """Cells with a noise-damped density, painted at their own size.

    Blocks are formed to average the injected noise out of small counts, but
    the block's DENSITY is then written back onto each cell inside it. The
    reader sees kilometre-scale texture — the shape of settlement — carrying a
    value that is stable enough to trust.
    """
    from pipeline import grid
    conv = config.registry()["conventions"]
    if not conv.get("map_min_block_population"):
        # Raw: each cell painted with its own published count over its own
        # area. Chosen 2026-10-01 over grouping, which made rural areas blocky.
        raw = [(lon, lat, v / grid.cell_km2(lat), v)
               for lon, lat, v in zip(f.grid.lons, f.grid.lats, f.grid.layer(layer)) if v > 0]
        return _coarsen(raw, mult) if mult > 1 else raw
    blocks = grid.aggregate(f.grid, layer,
                            min_count=conv["map_min_block_population"],
                            max_level=conv["map_max_aggregation_level"])
    if not blocks:
        return []
    # Blocks partition the cells, so each cell is painted exactly once. Keyed
    # at cell centres: an edge sits exactly on a boundary, where floor can
    # slip a cell into its neighbor.
    dens = {}
    for b in blocks:
        n = round(b.span / grid.CELL_DEG)
        for i in range(n):
            for j in range(n):
                dens[_cell_key(b.lon + (i + .5) * grid.CELL_DEG,
                               b.lat + (j + .5) * grid.CELL_DEG)] = b.density

    vals = f.grid.layer(layer)
    out = []
    for lon, lat, v in zip(f.grid.lons, f.grid.lats, vals):
        if v <= 0:
            continue
        d = dens.get(_cell_key(lon, lat))
        # A cell with residents whose block never formed still holds those
        # residents; fall back to its own density rather than dropping it.
        out.append((lon, lat, d if d else v / grid.cell_km2(lat), v))
    return _coarsen(out, mult) if mult > 1 else out


def _lattice_mult(extent, w_px: int, h_px: int = 560, pad: int = 10,
                  min_px: float = 2.0) -> int:
    """How many source cells to a drawn square, at this render size.

    A 0.01-degree cell across a metro drawn 290px wide is under a pixel: it
    costs about forty bytes of path data and shows the reader nothing. The
    small multiples are where this bites first -- six panels of the same
    geometry were 3.7 MB of Atlanta's 7 MB page -- but so do the very large
    counties: Yukon-Koyukuk was a 23 MB page.

    The size comes from the PROJECTION, not from the width alone. `_project`
    fits the frame to whichever axis binds, so a tall narrow county like
    Apache, AZ is drawn far narrower than the box it is given; judging it by
    the box said its cells were a comfortable two pixels when they were a
    fifth of one.

    Ordinary counties and metros come back 1, and nothing about them changes.
    """
    from pipeline import grid
    if w_px <= 0 or h_px <= 0:
        return 1
    _px, _py, scale, k = _project(extent, w_px, h_px, pad)
    # Pixels per degree on the tighter axis: longitude carries the cos(lat)
    # squeeze, so it is the smaller of the two everywhere off the equator.
    px_per_deg = min(scale * k, scale)
    if px_per_deg <= 0:
        return 1
    return max(1, min(16, ceil(min_px / (grid.CELL_DEG * px_per_deg))))


def _coarsen(cells, mult: int):
    """Merge cells onto a lattice `mult` times coarser, preserving people.

    Counts are summed and density recomputed from the summed area, so a
    coarsened panel holds exactly the residents the fine one did. Averaging
    the densities instead would quietly under-weight the big cells at high
    latitude.
    """
    from pipeline import grid
    step = grid.CELL_DEG * mult
    groups: dict[tuple[int, int], list] = {}
    for lon, lat, _d, v in cells:
        groups.setdefault(_cell_key(lon, lat, step), []).append((lon, lat, v))
    out = []
    for (ix, iy), members in groups.items():
        count = sum(v for _lo, _la, v in members)
        lon = (ix + 0.5) * step
        lat = (iy + 0.5) * step
        area = sum(grid.cell_km2(la) for _lo, la, _v in members)
        out.append((lon, lat, count / area if area else 0.0, count))
    return out


def _square_sentence(mult: int = 1) -> str:
    """What one drawn square is. Shared by every map note."""
    if mult == 1:
        return "Each square is one 0.01-degree grid cell, about a square kilometer. "
    return (f"Each square is a {mult}-by-{mult} block of 0.01-degree grid cells, about "
            f"{mult * mult} square kilometers; single cells would be too small to see at "
            "this size. ")


def _agg_note(mult: int = 1) -> str:
    conv = config.registry()["conventions"]
    # A place the size of an Alaskan borough is drawn on a coarser lattice,
    # because a 0.01-degree cell there is far under a pixel. The note has to
    # say which square the reader is actually looking at.
    square = _square_sentence(mult)
    grouped = (" To damp the privacy noise in small counts, neighboring cells are "
               "grouped, up to 32 by 32 cells, until a group holds at least "
               f"{conv['map_min_block_population']} people."
               if conv.get("map_min_block_population") else "")
    return (square + "Color is population density on a logarithmic scale." + grouped
            + " The scale runs blue to red, low to high, and starts at zero.")


def crosses_antimeridian(lons) -> bool:
    """True when a place's longitudes wrap through 180.

    The Aleutians run from about 172°E to 172°W, so their longitudes occupy
    both ends of the [-180, 180] range and nothing in between. Read naively
    that is a place 359 degrees wide: the frame spans the whole planet, and
    the zero-fill then tests tens of millions of lattice points against the
    outline and never finishes. Aleutians West hung the first full build.
    """
    lo, hi = min(lons), max(lons)
    return hi - lo > 180


def unwrap_lon(lon: float) -> float:
    """Put a longitude on a continuous line east of the antimeridian."""
    return lon + 360 if lon < 0 else lon


def wrap_lon(lon: float) -> float:
    """Back to [-180, 180) for display."""
    return (lon + 180) % 360 - 180


def _unwrap_rings(rings):
    return [[(unwrap_lon(x), y) for x, y in ring] for ring in rings]


def _unwrap_cells(cells):
    return [(unwrap_lon(lo), la, d, v) for lo, la, d, v in cells]


def _extent(f: PlaceFigures, outline=()):
    """The frame: the outline where there is one, else the cells.

    Taken from the boundary rather than the cells so uninhabited parts of a
    place stay in the picture — a county that is half forest should look half
    empty, not be cropped to the half that is settled.

    Longitudes arrive already unwrapped for a place that crosses the
    antimeridian, so the frame is continuous and min/max mean what they say.
    """
    from pipeline import grid
    g = f.grid
    lons = list(g.lons) + [x for ring in outline for x, _ in ring]
    lats = list(g.lats) + [y for ring in outline for _, y in ring]
    if crosses_antimeridian(lons):
        lons = [unwrap_lon(x) for x in lons]
    return (min(lons), max(lons) + grid.CELL_DEG, min(lats), max(lats) + grid.CELL_DEG)


def _built_maps(f: PlaceFigures, tab: str) -> str:
    """The maps this place actually has, drawn from its cells."""
    if f.grid is None or not len(f.grid):
        return ""
    year = _headline_year(f, "population")

    from pipeline import basemap, shapes
    outline = shapes.rings(f.geo.upstream, f.geo_id)
    context = shapes.neighbours(f.geo.upstream, f.geo_id)
    ext = _extent(f, outline)

    # A place that wraps through 180 is drawn on a continuous frame east of
    # the antimeridian, so every longitude it uses has to move with it.
    flip = crosses_antimeridian(list(f.grid.lons)
                                + [x for ring in outline for x, _ in ring])
    if flip:
        outline = _unwrap_rings(outline)
        context = [_unwrap_rings(rs) for rs in context]

    def cells(layer):
        cs = _cells_for(f, layer)
        return _unwrap_cells(cs) if flip else cs

    pop_cells = cells("pop")
    # The same sub-pixel rule the panels use, applied to the wide map. Most
    # places come back at 1; the Alaskan boroughs span twenty degrees, where
    # single cells are invisible and cost tens of megabytes. Yukon-Koyukuk was
    # a 23 MB page, past the 10 MB ceiling on CloudFront's auto-compression.
    main_mult = _lattice_mult(ext, 900, 560)
    main_cells = _coarsen(pop_cells, main_mult) if main_mult > 1 else pop_cells
    # Roads and labels only on the main map. On a 250px panel they bury the
    # very pattern the panel exists to show. TIGER indexes them in the
    # ordinary [-180, 180] frame, so a wrapped place gets neither rather than
    # a silently empty search against coordinates that cannot match.
    rds = () if flip else basemap.roads(ext)
    # Every main map is labeled, counties and metros alike. Places come from
    # every state the frame touches and are ranked by the residents around
    # them, so the labels land inside this place, where people are.
    tws = () if flip else basemap.towns(basemap.states_in(ext), ext,
                                        cells=tuple(pop_cells))

    if tab == "population":
        return figure(
            title=f"Where People Live in {f.name}",
            subtitle=f"Population Density, {year}",
            body=map_chart(main_cells, label="Population", extent=ext,
                           outline=outline, context=context, roads=rds, towns=tws,
                           span=0.01 * main_mult),
            notes=_agg_note(main_mult))

    if tab == "race":
        sh = f.shares.get("race_ethnicity")
        if sh is None:
            return ""
        # Each group's SHARE of a square's residents, not its density: the
        # question is whether groups live in the same parts of the place, and
        # density panels mostly redrew the total-population map. Shares are
        # computed after any coarsening, as summed group over summed total, so
        # a coarse square is never an average of fine ones.
        mult = panel_mult(ext, len(sh.labels))
        step = 0.01 * mult
        total = {_cell_key(lo, la, step): (lo, la, v)
                 for lo, la, _d, v in (_coarsen(pop_cells, mult) if mult > 1 else pop_cells)}
        panels, thin = [], []
        for code, label in sh.labels.items():
            name = _chart_label(label)
            layer = f"race_{code}"
            if layer not in f.grid.values:
                continue
            gc = cells(layer)
            if not gc:
                thin.append(name)
                continue
            got = {_cell_key(lo, la, step): v
                   for lo, la, _d, v in (_coarsen(gc, mult) if mult > 1 else gc)}
            panels.append((name, [(lo, la, min(100.0, 100 * got.get(k, 0.0) / v), got.get(k, 0.0))
                                  for k, (lo, la, v) in total.items() if v > 0]))
        if not panels:
            return ""
        square = _square_sentence(mult)
        return figure(
            title=f"Where Racial and Ethnic Groups Live in {f.name}",
            subtitle=f"Share of Each Square's Residents by Group, {year}",
            body=_map_panels(panels, "Share of residents", extent=ext, outline=outline,
                             bins=SHARE_BREAKS, palette=share_palette(),
                             ticks_at=SHARE_TICKS, position=_share_pos,
                             key_label="Share of the square's residents",
                             tick=lambda c: f"{c:.0f}%", coarsened=True,
                             empty_fill="none"),
            notes=(square + "Color is the group's share of the square's residents, from "
                   "0 to 100 percent on the same scale in every panel. The scale is "
                   "stretched at low shares, where most squares fall. Gray squares hold "
                   "none of the group; blank squares have no residents. Hispanic takes precedence; all other categories are "
                   "non-Hispanic." + AIAN_NOTE
                   + (f" {', '.join(thin)} has no residents anywhere in this area, so no "
                      "panel is drawn." if thin else "")))

    if tab == "income":
        pairs = [("Bottom national decile", "decile_low"),
                 ("Top national decile", "decile_high")]
        panels = [(lbl, cells(layer)) for lbl, layer in pairs
                  if layer in f.grid.values]
        panels = [(lbl, b) for lbl, b in panels if b]
        if len(panels) < 2:
            return ""
        return figure(
            title=f"Where the Highest and Lowest Earners Live in {f.name}",
            subtitle=f"Residents in the Top and Bottom National Income Deciles, {year}",
            body=_map_panels(panels, "Residents", extent=ext, outline=outline),
            notes=_agg_note(panel_mult(ext, len(panels)))
                  + " Both panels share one color scale.")
    return ""


def panel_cols(n: int) -> int:
    return 3 if n > 2 else 2


def panel_mult(extent, n_panels: int, w: int = 900) -> int:
    """The lattice a small multiple will be drawn on.

    Shared with `_built_maps` so the figure's notes describe the square the
    reader is looking at rather than the one the wide map used.
    """
    return _lattice_mult(extent, int(w / panel_cols(n_panels)) - 10, 250 - 34, pad=4)


def _map_panels(panels: list, label: str, *, extent, outline=(), w: int = 900,
                bins=None, key_label: str = "People per square kilometer",
                tick=None, coarsened: bool = False, empty_fill: str | None = None,
                palette: list[str] | None = None, ticks_at=None, position=None) -> str:
    """Small multiples, one per group, all framing the same ground.

    Every panel shares one color scale as well as one frame — built from all
    the panels together — so a square of a given color means the same people per
    square kilometer wherever it appears. Independent scales would make a group
    of two hundred look like a group of twenty thousand.
    """
    cols = panel_cols(len(panels))
    pw, ph = w / cols, 250
    rows = -(-len(panels) // cols)
    mult = panel_mult(extent, len(panels), w)
    if mult > 1 and not coarsened:
        panels = [(name, _coarsen(cs, mult)) for name, cs in panels]
    span = 0.01 * mult
    # Bins come from the coarsened values, which are what the reader sees.
    shared = bins if bins is not None else _log_bins(
        [c[2] for _, cs in panels for c in cs], len(DENSITY_STEPS))
    tick = tick or num
    out = []
    for i, (name, cs) in enumerate(panels):
        gx, gy = (i % cols) * pw, (i // cols) * ph
        # No graticule or neighbours on the panels: at this size they crowd
        # the very thing the panel exists to show, and the reader has already
        # taken their bearings from the map above.
        inner = map_chart(cs, label=f"{label} — {name}", w=int(pw) - 10,
                          h=ph - 34, interactive=False, pad=4, extent=extent,
                          outline=outline, bins=shared, legend=False,
                          graticule=False, span=span, empty_fill=empty_fill,
                          palette=palette)
        inner = inner[inner.index(">") + 1:inner.rindex("</svg>")]
        out.append(f'<g transform="translate({gx + 5:.0f},{gy + 24:.0f})">{inner}</g>'
                   f'<text x="{gx + pw / 2:.0f}" y="{gy + 16:.0f}" class="panel-title">'
                   f'{e(name)}</text>')
    # One key for the set, since the scale is shared.
    sw, sh_, x0, ly = 52, 12, 14, rows * ph + 6
    if palette:
        # A continuous bar: the first color is exactly zero and gets its own
        # swatch; the rest run as a gradient, ticked where `position` puts them.
        zw, gw = 30, 330
        stops = "".join(f'<stop offset="{i / (len(palette) - 2):.4f}" stop-color="{c}"/>'
                        for i, c in enumerate(palette[1:]))
        ticks = "".join(
            f'<text x="{x0 + zw + 8 + gw * position(v):.0f}" y="{ly + sh_ + 13}" '
            f'class="tick mid">{tick(v)}</text>' for v in ticks_at)
        key = (f'<defs><linearGradient id="share-ramp">{stops}</linearGradient></defs>'
               f'<text x="{x0}" y="{ly - 5}" class="tick">{e(key_label)} '
               f'(same scale in every panel)</text>'
               f'<rect x="{x0}" y="{ly}" width="{zw}" height="{sh_}" fill="{palette[0]}"/>'
               f'<text x="{x0 + zw / 2:.0f}" y="{ly + sh_ + 13}" class="tick mid">0%</text>'
               f'<rect x="{x0 + zw + 8}" y="{ly}" width="{gw}" height="{sh_}" '
               f'fill="url(#share-ramp)"/>{ticks}')
    else:
        swatches = "".join(f'<rect x="{x0 + i * sw:.0f}" y="{ly}" width="{sw}" height="{sh_}" '
                           f'fill="var({tok})"/>' for i, tok in enumerate(DENSITY_STEPS))
        # The first swatch starts at zero, as on the single maps.
        ticks = (f'<text x="{x0}" y="{ly + sh_ + 13}" class="tick mid">0</text>'
                 + "".join(f'<text x="{x0 + (i + 1) * sw:.0f}" y="{ly + sh_ + 13}" '
                           f'class="tick mid">{tick(c)}</text>' for i, c in enumerate(shared)))
        key = (f'<text x="{x0}" y="{ly - 5}" class="tick">{e(key_label)} '
               f'(same scale in every panel)</text>{swatches}{ticks}')
    return (f'<svg viewBox="0 0 {w} {rows * ph + 46:.0f}" role="img" class="mapfig" '
            f'aria-label="{e(label)} maps">{"".join(out)}{key}</svg>')


MAP_INTRO = {
    "population": (
        "People are not evenly distributed across {place}. FIGREF_NEXT shows "
        "population density in each square kilometer of the {kind}."),
    "race": (
        "Racial and ethnic groups do not all live in the same parts of {place}. "
        "FIGREF_NEXT maps each group's share of the residents of every square "
        "kilometer."),
    "income": (
        "Residents in the highest and lowest national income deciles do not "
        "necessarily live in the same parts of {place}. FIGREF_NEXT maps where "
        "each group lives."),
}


def _map_block(f: PlaceFigures, tab: str) -> str:
    """The map as a subsection of the topic it belongs to.

    It used to be a section of its own headed "Where people live", sitting
    directly below a subsection of the same name -- the reader met the heading
    twice before reaching anything new.
    """
    # No map section unless there is a map. A geography can declare `maps:
    # false`, and a place can simply be too small to draw one -- Kalawao
    # County holds sixteen people in three cells, enough for a density map and
    # not enough for the per-group panels. Both used to render a "Map in
    # progress" placeholder, which promised a figure that was never coming.
    if config.registry()["geographies"][f.geo.id].get("maps", True) is False:
        return ""

    body = _built_maps(f, tab)
    if not body:
        return ""
    label = {"population": "Where people live",
             "race": "Where each group lives",
             "income": "Where the highest and lowest earners live"}.get(tab, "Maps")
    intro = (MAP_INTRO.get(tab, "").replace("{place}", _prose_name(f))
             .replace("{kind}", e(_kind(f).lower())))
    intro = f'<p class="lead">{intro}</p>' if intro else ""
    return f'<h3 id="maps-{e(tab)}">{e(label)}</h3>{intro}{body}'



def _here(f: PlaceFigures) -> str:
    """This place's own URL, relative to the site root, with a trailing slash."""
    prefix = config.registry()["geographies"][f.geo.id].get("url_prefix") or f.geo.id
    return f"{prefix}/{f.geo_id}/"


def page(f: PlaceFigures, *, assets: str | None = None, root: str = "") -> str:
    """`assets` is a relative path to the shared asset directory.

    When given, the page links the stylesheet instead of inlining it — 37 KB
    per page that the browser then fetches once for the whole site. Passing
    None keeps a page self-contained, which is what the tests use.

    `root` is the relative path back to the site root. It used to be a
    hardcoded "../", which was right for the metro pages and wrong for every
    other one: a county page at the root pointed its Home link outside the
    site entirely.
    """
    coverage = (f'<p class="callout">{e(f.geo.coverage_note.strip())}</p>'
                if f.geo.coverage_note else "")
    year = _headline_year(f, "population")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(f.name)} — Population Data Explorer</title>
<meta name="description" content="Demographic and income profile for {e(f.name)} from the U.S. Census Bureau's Gridded Environmental Impacts Frame.">
{_styles(assets)}{_icon(assets)}</head><body>
{_header(root)}
<main class="wrap">
  <div class="page-head">
    <p class="eyebrow">{e(_kind(f))}</p>
    <h1>{e(f.name)}</h1>
    <div class="head-row">
      <p class="subhead">Demographic and Income Profile{f", {year}" if year else ""}</p>
      <button type="button" class="pdf-btn" onclick="window.print()">Save as PDF</button>
    </div>
    <p class="print-only print-url">{SITE_URL or ""}{_here(f)}</p>
    {coverage}
  </div>
  {_number_figures(_tab_strip({
      "population": _population(f, _map_block(f, "population")) + _age(f),
      "race": _race(f, _map_block(f, "race")),
      "income": _income(f, _map_block(f, "income")),
  }))}
</main>
{_footer()}
<script>{SCRIPT}</script>
</body></html>"""


@cache
def stylesheet_name() -> str:
    """The stylesheet's filename, carrying eight characters of its own hash.

    A changed stylesheet is then a changed URL, so no browser and no CDN can
    serve the old one against a new page. Without it a reader who visited
    before a restyle keeps their cached copy until it expires and the page
    renders with half its design missing -- which is exactly what happened
    here while iterating locally.

    The hash is in the FILENAME rather than a `?v=` query, because
    CloudFront's CachingOptimized policy leaves query strings out of the cache
    key: the CDN would have gone on serving the old bytes to everyone. It also
    lets the published asset be cached hard instead of for the five minutes a
    rewritable URL has to settle for.
    """
    return f"explorer.{hashlib.sha256(stylesheet().encode()).hexdigest()[:8]}.css"


def _styles(assets: str | None) -> str:
    if assets is None:
        return f"<style>{css()}</style>"
    return f'<link rel="stylesheet" href="{assets}{stylesheet_name()}">' 


SEARCH_JS = """
(function () {
  var box = document.getElementById('q'), list = document.getElementById('results'),
      count = document.getElementById('count'), places = null, timer = null;
  if (!box || !list) return;
  document.getElementById('search').hidden = false;
  document.getElementById('nojs').hidden = true;

  fetch('places.json').then(function (r) { return r.json(); })
    .then(function (d) {
      // Each entry is [href, kind, name]; search the kind too, so "chicago
      // metro" or "orange county" narrows the way a reader expects.
      places = d.map(function (p) { return [p, (p[2] + ' ' + p[1]).toLowerCase()]; });
      box.disabled = false; box.placeholder = box.dataset.hint; render(box.value); })
    .catch(function () { document.getElementById('nojs').hidden = false; });

  function render(q) {
    if (!places) return;
    // Every word must appear somewhere, in any order: "albemarle va" and
    // "va albemarle" find the same county.
    var words = q.trim().toLowerCase().split(/\\s+/).filter(Boolean), hits = [];
    if (words.length) {
      for (var i = 0; i < places.length && hits.length < 60; i++) {
        var hay = places[i][1], ok = true;
        for (var w = 0; w < words.length; w++) {
          if (hay.indexOf(words[w]) === -1) { ok = false; break; }
        }
        if (ok) hits.push(places[i][0]);
      }
    }
    count.textContent = words.length ? hits.length + (hits.length === 60 ? '+ matches' :
      hits.length === 1 ? ' match' : ' matches') : '';
    list.innerHTML = hits.map(function (p) {
      return '<li><a class="place" href="' + p[0] + '">' +
        '<span class="place-kind">' + p[1] + '</span>' +
        '<span class="place-name">' + p[2] + '</span></a></li>';
    }).join('');
  }
  box.addEventListener('input', function () {
    clearTimeout(timer); timer = setTimeout(function () { render(box.value); }, 90);
  });
})();
"""

# What the box suggests once places have loaded. Real names, one of each
# kind, so a reader learns both what can be searched and how it is spelled.
SEARCH_HINT = "A county, metro area or state &mdash; e.g. Albemarle County, VA &middot; Chicago metro &middot; Texas"



def index(entries: list[tuple[str, str, str]], *, assets: str | None = None) -> str:
    """The front door: a search box over every place.

    `entries` is (href, geography label, place name).

    Listing four thousand places here would be a megabyte of HTML nobody
    reads. The page ships a search box instead and fetches `places.json`,
    with a link to the full A-Z listing for anyone without JavaScript -- which
    also gives a crawler a path to every page, since these URLs are meant to
    be found and cited.

    Laid out like the gridded-eif landing page: display title, lead and
    search in a hero band, with the graphic beside them.
    """
    n = len(entries)
    # Decorative, so empty alt text. Without the image (a fresh checkout that
    # never ran tools/build_hero.py) the hero runs as one column, not a hole.
    art = (f'<div class="hero-art"><img src="{assets}{HERO_IMAGE}" alt="" '
           f'width="1206" height="744"></div>'
           if assets is not None and (ASSETS / HERO_IMAGE).exists() else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Population Data Explorer</title>
<meta name="description" content="Demographic and income profiles for every U.S. state, county and metro area, from the Census Bureau's Gridded Environmental Impacts Frame.">
{_styles(assets)}{_icon(assets)}</head><body>
{_header("")}
<main class="home">
  <section class="hero"><div class="wrap">
    <div class="{'hero-grid' if art else 'hero-solo'}">
      <div>
        <h1 class="hero-title">Population Data Explorer</h1>
        <p class="lead">Explore demographic and income profiles for every U.S. state,
        county and metro area &mdash; {n:,} places. Each profile gives interactive
        access to data on population, age, race and ethnicity, and household income,
        with maps of where people live at the scale of a square kilometer. The profiles
        are built from the Gridded Environmental Impacts Frame (Gridded EIF), an
        innovative, privacy-protected dataset developed by the Environmental Inequality
        Lab in collaboration with the U.S. Census Bureau.</p>
        <div class="search-box" id="search" hidden>
          <label for="q">Search for a place</label>
          <input id="q" type="search" autocomplete="off" disabled
                 placeholder="Loading places&hellip;" data-hint="{SEARCH_HINT}">
        </div>
        <p class="note" id="nojs">{n:,} places.
          <a href="./places.html">Browse the full list</a>.</p>
      </div>
      {art}
    </div>
    <p class="note" id="count"></p>
    <ul class="place-list" id="results"></ul>
  </div></section>

</main>
{_footer()}
<script>{SEARCH_JS}</script>
</body></html>"""


def about_page(*, assets: str | None = None, n_places: int = 0) -> str:
    """How the numbers were made, and what they do not say.

    Every page links here, so this is where the conventions live in full
    rather than being repeated in ten sets of figure notes. The figures that
    can drift -- the noise measure, the small-group floor, the map's minimum
    block -- are read from the registry and the upstream catalog rather than
    typed, so this page cannot quietly disagree with the pages it explains.
    """
    reg = config.registry()
    conv = reg["conventions"]
    cat = config.upstream_catalog()
    src = cat.get("source", {})
    measure = conv["measure"]
    m = cat.get("measures", {}).get(measure, {})

    body = f"""
  <h2>The lab</h2>
  <p>The <a href="{LAB_HOME}">Environmental Inequality Lab</a> is a nonpartisan
  research group that applies a rigorous, data-driven approach to understanding how
  our environment shapes economic opportunity and well-being. The lab builds new
  data infrastructure, publishes research, invests in early-career researchers, and
  creates public tools that widen access to information and inform decisions.</p>

  <h2>This website</h2>
  <p>The Population Data Explorer profiles every U.S. state, county and metro area
  &mdash; {n_places:,} places &mdash; using the Census Bureau&rsquo;s Gridded
  Environmental Impacts Frame. Each profile reports how many people live in a place
  and how that has changed, its age structure and racial and ethnic composition,
  and how its residents are spread across the national income distribution.
  County and metro profiles also map where in the place people live.</p>
  <p>Every profile has a permanent address built from its FIPS code, such as
  <code>/county/51003/</code>.</p>

  <h2>The data</h2>
  <p>The <a href="{e(src.get('landing_page', CENSUS_PAGE))}">Gridded Environmental
  Impacts Frame</a> (Gridded EIF) is an <strong>experimental</strong> Census Bureau
  product built from the confidential Environmental Impacts Frame, which links
  administrative records such as tax filings to precise residential locations. Each
  person is assigned to a fixed grid of 0.01-degree cells, about one square
  kilometer, and counted there by race and ethnicity, sex and age group, and by race
  and ethnicity and household income decile.</p>
  <p>Because it is built from administrative records rather than a survey or a
  census, the Gridded EIF may not fully capture every group, and its totals can
  differ from the Decennial Census and the Census Bureau&rsquo;s population
  estimates. The data are described in full in Voorheis, Colmer, Houghton, Lyubich,
  Munro, Scalera and Withrow, &ldquo;The Census Environmental Impacts Frame,&rdquo;
  <em>Review of Environmental Economics and Policy</em> 20 (2): 304&ndash;312
  (2026).</p>
  <p>The figures on these pages are sums of grid cells within each place. The
  aggregation is done by the
  <a href="{GRIDDED_EIF_SITE}">Gridded EIF Data Explorer</a>, which documents it and
  publishes the results for download.</p>

  <h3>Privacy noise</h3>
  <p>Before release, the Census Bureau adds a small amount of random noise to every
  count, in the spirit of the methods used for the 2020 Census. Most of it is
  within plus or minus three people, &ldquo;on the order of rounding,&rdquo; and it
  largely cancels out when cells are added up to a county or a state. It does not
  cancel out for <strong>small numbers</strong>: a single map square, or a group of
  a few hundred people, can carry noise that is a large share of the value
  shown.</p>
  <p>The Census Bureau publishes two versions of each count: the raw noisy count,
  which can be negative, and a
  <strong>{e(m.get('label', measure).lower().replace(' counts', ''))}</strong> version
  that is never negative. These pages use the second everywhere. The
  source&rsquo;s documentation recommends it for places under about 600,000 people;
  using one version throughout keeps every series and every comparison on the same
  footing.</p>

  <h2>Reading the profiles</h2>
  <h3>Change over time</h3>
  <p>According to the EIF&rsquo;s authors, coverage of the underlying administrative
  records is high and has improved over time, particularly in recent years. Coverage
  increases in 2004, when information returns become available, and in 2015, when
  commercial data are first incorporated (Voorheis et al. 2026). Because counts can
  jump in those years, the text on these pages measures growth and change from
  <strong>{GROWTH_BASE}</strong>; the figures show every year from {BASE_YEAR}.</p>

  <h3>Race and ethnicity</h3>
  <p>Groups are mutually exclusive. Anyone recorded as Hispanic is counted as
  Hispanic; everyone else is counted as non-Hispanic White, Black, Asian, or
  American Indian and Alaska Native (AIAN). &ldquo;Other or unknown&rdquo; covers
  people with no race recorded, another race, or more than one race. Every group is
  shown in every figure, however small.</p>

  <h3>Income</h3>
  <p>The Gridded EIF records which <strong>national decile</strong> of Adjusted
  Gross Income a household falls into. A place is therefore described by how its
  residents are spread across the ten national deciles: about 10 percent of all U.S.
  residents fall in each, so a share above 10 percent means that part of the
  distribution is over-represented. Shares are of residents with an income
  record.</p>

  <h3>Maps</h3>
  <p>Each square on a map is one published grid cell, drawn from its own count.
  Population maps show density on a logarithmic scale; race and ethnicity maps show
  each group&rsquo;s share of a square&rsquo;s residents.</p>

  <h2>Limitations</h2>
  <ul>
    <li>Characteristics beyond age, sex, race and ethnicity, and income decile are
    not available.</li>
    <li>The most recent year is built from the latest available records and may be
    revised.</li>
    <li>Researchers who need more detail can apply to use the confidential
    microdata in a Federal Statistical Research Data Center.</li>
  </ul>

  <h2>Contributors</h2>
  <p>The Population Data Explorer was developed by researchers at the Environmental
  Inequality Lab. Arnav Dharmagadda (University of Virginia), Josie Fischman (Bowdoin
  College) and Elizabeth Shiker (University of Virginia) contributed to the preliminary
  development of the county-level analysis and the preparation of the Gridded EIF for
  county-level applications. Web design and development were led by
  <a href="https://grantseiter.com">Grant M. Seiter</a>.</p>
"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>About &mdash; Population Data Explorer</title>
<meta name="description" content="How the Population Data Explorer is built: the Gridded EIF source, privacy noise, the measure used, and what these pages deliberately do not report.">
{_styles(assets)}{_icon(assets)}</head><body>
{_header("", "about")}
<main class="wrap prose">
  <div class="page-head">
    <h1>About</h1>
  </div>
  <section class="section">{body}</section>
</main>
{_footer()}
</body></html>"""


def places_page(entries: list[tuple[str, str, str]], *,
                assets: str | None = None) -> str:
    """Every place, grouped by geography. The no-JavaScript path, and the one
    a crawler follows to reach four thousand pages it would otherwise never
    see."""
    groups: dict[str, list] = {}
    for href, kind, name in entries:
        groups.setdefault(kind, []).append((href, name))
    blocks = []
    for kind, items in groups.items():
        links = "".join(f'<li><a href="{e(href)}">{e(name)}</a></li>'
                        for href, name in sorted(items, key=lambda t: t[1]))
        blocks.append(f'<h2>{e(kind)} <span class="note">({len(items):,})</span></h2>'
                      f'<ul class="all-places">{links}</ul>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All places — Population Data Explorer</title>
{_styles(assets)}{_icon(assets)}</head><body>
{_header("")}
<main class="wrap">
  <div class="page-head"><h1>All places</h1>
  <p class="subhead">{len(entries):,} pages.</p></div>
  <section class="section">{"".join(blocks)}</section>
</main>
{_footer()}
</body></html>"""
