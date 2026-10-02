"""The rebuild, measured against the pinned figures.

This is what phase 01 was for. `figures.py` computes a place's numbers by a
completely different route from `tools/build_golden_fixture.py` — through the
registry, the resolver, and the declared conventions rather than a direct
query. If the two disagree, one of them is wrong, and that is a test failure
rather than something discovered two summers later.

Needs the published data, so it skips when the network is unavailable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import config, figures, narrative, render

GOLDEN = Path(__file__).parent / "fixtures" / "golden-figures.json"
BASE_YEAR, CURRENT_YEAR = 2000, 2025   # headline policy is `latest`
TOL = 0.05          # people; both routes round, neither approximates
PCT_TOL = 0.005     # percentage points


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


@pytest.fixture(scope="module")
def catalog() -> dict:
    try:
        return config.upstream_catalog()
    except Exception as e:  # noqa: BLE001 — any failure here means skip, not fail
        pytest.skip(f"published catalog unreachable: {e}")


@pytest.fixture(scope="module")
def computed(catalog) -> dict:
    try:
        return {gid: figures.figures_for("county", gid, catalog)
                for gid in ("06037", "51003", "51685")}
    except Exception as e:  # noqa: BLE001 — any failure here means skip, not fail
        pytest.skip(f"published data unreachable: {e}")


SHARE_PAIRS = [(ds, f"{key}_{CURRENT_YEAR}") for ds, key in
               (("age_structure", "age_shares"),
                ("race_ethnicity", "race_shares"),
                ("income_composition", "income_decile_shares"))]


def test_population_matches_the_pinned_figures(computed, golden):
    for gid, fig in computed.items():
        pinned = golden["counties"][gid]["population"]["total_by_year"]
        for year_s, expected in pinned.items():
            got = fig.population.at(int(year_s))
            assert got == pytest.approx(expected, abs=TOL), (
                f"{gid} {year_s}: computed {got}, pinned {expected}"
            )


def test_cagr_matches_the_pinned_figure(computed, golden):
    """Same formula, arrived at independently."""
    for gid, fig in computed.items():
        expected = golden["counties"][gid]["population"]["cagr_2000_2025"]
        got = fig.population.cagr(BASE_YEAR, CURRENT_YEAR)
        assert got == pytest.approx(expected, abs=1e-6), f"{gid}: {got} vs {expected}"


def test_shares_match_the_pinned_figures(computed, golden):
    for gid, fig in computed.items():
        for ds_id, key in SHARE_PAIRS:
            sh, pinned = fig.shares[ds_id], golden["counties"][gid][key]
            assert sh.denominator == pytest.approx(pinned["denominator"], abs=TOL), (
                f"{gid}/{ds_id}: denominator {sh.denominator} vs {pinned['denominator']}"
            )
            assert set(sh.order) == set(pinned["categories"]), (
                f"{gid}/{ds_id}: categories {sorted(sh.order)} vs "
                f"{sorted(pinned['categories'])}"
            )
            for code, exp in pinned["categories"].items():
                assert sh.share(code) == pytest.approx(exp["share_pct"], abs=PCT_TOL), (
                    f"{gid}/{ds_id}/{code}: {sh.share(code)} vs {exp['share_pct']}"
                )


def test_income_universe_and_coverage_match(computed, golden):
    for gid, fig in computed.items():
        sh = fig.shares["income_composition"]
        pinned = golden["counties"][gid][f"income_decile_shares_{CURRENT_YEAR}"]
        assert sh.universe == pinned["denominator_universe"]
        assert sh.coverage is not None
        assert sh.coverage["excluded_category"] == pinned["coverage"]["excluded_category"]


def test_shares_still_total_100(computed):
    """The invariant, re-checked on the live path rather than the fixture."""
    for gid, fig in computed.items():
        for ds_id, sh in fig.shares.items():
            assert sh.total_pct == pytest.approx(100.0, abs=0.01), (
                f"{gid}/{ds_id} totals {sh.total_pct}%"
            )


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def test_comparing_across_universes_is_refused(computed):
    """The Arlington bug, made impossible rather than unlikely.

    Race shares over a residual-inclusive denominator compared against a
    figure whose residual was a different size is what inverted the published
    claim. Here the two sides must declare the same universe or no sentence is
    produced at all.
    """
    fig = computed["51003"]
    race, income = fig.shares["race_ethnicity"], fig.shares["income_composition"]
    assert race.universe != income.universe
    with pytest.raises(narrative.IncomparableError):
        narrative.compare_share(race, income, "White")


def test_comparison_uses_unrounded_values():
    """A county at 21.6 against a national 21.9 is lower, not higher.

    The published site rounded the county share to an integer and compared it
    against an unrounded national one, so 21.6 became 22 and was called
    "higher".
    """
    c = narrative.Claim.of(21.6, 21.9, tolerance=0.1)
    assert c.word == "lower", "rounding crept back into the comparison"


def test_similar_is_reachable():
    """With exact comparison it never fires, and near-identical places read as different."""
    assert narrative.Claim.of(21.6, 21.9).is_similar
    assert not narrative.Claim.of(21.6, 25.0).is_similar


def test_concentration_claim_is_about_shares_not_dollars(computed):
    """The honest income statement.

    The share is measured, so its distance from an even 10% is a real fact.
    The dollar figure the old site published was the local decile mix times
    national decile means, which said nothing about local earnings.
    """
    sh = computed["51003"].shares["income_composition"]
    claim = narrative.concentration(sh, "10")
    assert claim and claim.word == "over-represented"
    assert claim.reference == 10.0


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pages(catalog, computed) -> dict:
    return {gid: render.page(figures.with_comparisons(fig, catalog))
            for gid, fig in computed.items()}



def test_no_page_states_a_dollar_figure(pages):
    """`income_composition` may not emit dollars — verified on the output, not the config.

    The registry forbids it and a registry test checks the declaration. This
    checks the thing a reader actually sees.
    """
    for gid, html in pages.items():
        assert "$" not in html, f"{gid}: a dollar figure reached the page"


def test_no_page_uses_a_forbidden_phrase(pages):
    banned = config.registry()["datasets"]["income_composition"]["never_call_it"]
    for gid, html in pages.items():
        low = html.lower()
        for phrase in banned:
            assert phrase.lower() not in low, f"{gid}: page says {phrase!r}"


def test_income_section_states_its_universe(pages):
    for gid, html in pages.items():
        assert "residents with an income record" in html, (
            f"{gid}: the income denominator is not disclosed — this is exactly "
            "what the old site left out"
        )


def test_metro_page_has_no_coverage_callout(catalog):
    """Dropped 2026-10-01: a profile of one metro is not summed to anything."""
    fig = figures.with_comparisons(figures.figures_for("cbsa", "16820", catalog), catalog)
    assert "do not cover the entire country" not in render.page(fig)


def test_metro_page_does_not_leak_its_unbuilt_comparison(catalog):
    """A metro has no peers yet. The page used to say "No comparison to
    peer_size yet" -- an internal id, in a reader's footer. Absent stays
    absent; it is just not announced."""
    fig = figures.with_comparisons(figures.figures_for("cbsa", "16820", catalog), catalog)
    assert "peer_size" not in fig.comparisons
    assert "peer_size" not in render.page(fig)


def test_page_leads_with_the_newest_year(pages, computed):
    """The registry's policy is `latest`, and the page must actually follow it.

    2025 is flagged preliminary upstream and used anyway — a deliberate choice
    recorded in `vintages.headline`. What the page must never do is claim a
    year it did not use, so the headline sentence has to name the year the
    figures came from.
    """
    for gid, html in pages.items():
        newest = max(computed[gid].population.years)
        assert newest == CURRENT_YEAR, f"{gid}: newest year is {newest}"
        # The first sentence of prose on the page, whatever its shape. Pinning
        # the exact phrase "in 2025." made an ordinary rewording look like a
        # policy violation.
        lead = re.search(r'<p class="lead">(.*?)</p>', html, re.DOTALL)
        assert lead, f"{gid}: the page opens with no lead paragraph"
        first = re.split(r"(?<=[.!?])\s", re.sub(r"<[^>]+>", "", lead.group(1)).strip())[0]
        assert str(newest) in first, (
            f"{gid}: the headline sentence does not name {newest}: {first!r}"
        )
        assert f", {newest}" in html, f"{gid}: no figure is dated {newest}"


def test_page_does_not_claim_a_year_it_did_not_use(pages, computed):
    """Every year named in a figure subtitle is a year with data behind it."""
    import re
    for gid, html in pages.items():
        have = set(computed[gid].population.years)
        for sub in re.findall(r'<p class="fig-sub">([^<]*)</p>', html):
            for yr in re.findall(r"\b(19|20)\d{2}\b", sub):
                pass
            for yr in {int(m) for m in re.findall(r"\b((?:19|20)\d{2})\b", sub)}:
                assert yr in have, f"{gid}: figure subtitle names {yr}, which has no data"


# ---------------------------------------------------------------------------
# EIL house style
# ---------------------------------------------------------------------------

def test_every_chart_sits_in_a_complete_eil_frame(pages):
    """The frame is the branding, and an incomplete one looks like a mistake.

    Carried over from `03_add_figure_wrappers.R`: a FIGURE label, a title, a
    subtitle, the logo, then Sources and Notes. Previously composited into a
    PNG; here it is markup, so it can be asserted on.
    """
    for gid, html in pages.items():
        frames = html.count('<figure class="eil">')
        assert frames >= 4, f"{gid}: only {frames} figures"
        for part in ('class="fig-label"', 'class="fig-title"', 'class="fig-sub"',
                     'class="fig-logo"', "<b>Sources:</b>", "<b>Notes:</b>"):
            assert html.count(part) >= frames, (
                f"{gid}: {part} appears {html.count(part)} times across {frames} figures"
            )
        assert "environmental-inequality-lab.org" in html


def test_figures_are_numbered_in_order(pages):
    import re
    for gid, html in pages.items():
        labels = re.findall(r'<p class="fig-label">Figure (\d+)</p>', html)
        assert labels == [str(i + 1) for i in range(len(labels))], (
            f"{gid}: figure numbering is {labels}"
        )


def test_page_carries_the_shared_site_header(pages):
    """Same header markup as the gridded-eif site, so the two read as one family."""
    for gid, html in pages.items():
        assert 'class="site-header"' in html, f"{gid}: no site header"
        assert 'class="brand"' in html and 'class="brand-mark"' in html, (
            f"{gid}: the brand lockup is incomplete"
        )
        assert 'class="site-nav"' in html
        # As on gridded-eif: the mark alone, no product name beside it, and no
        # outbound link in the nav.
        assert 'class="brand-name"' not in html, f"{gid}: product name back in the header"
        nav = html.split('class="site-nav">', 1)[1].split("</nav>", 1)[0]
        assert ">Search Place Profiles</a>" in nav and ">About</a>" in nav
        assert "gridded-eif" not in nav, f"{gid}: Gridded EIF is back in the nav"


def test_no_even_spread_reference_line(pages):
    """Dropped deliberately.

    The published figure drew `geom_hline(yintercept = 0.10)`. The national
    comparison it stood in for is now in the table beside the chart, column by
    column, so the line was restating a number the reader already has.
    """
    for gid, html in pages.items():
        assert "Even spread" not in html, f"{gid}: the even-spread line is back"
        assert 'class="refline"' not in html, f"{gid}: a reference line is back"


def test_only_published_palette_charts_use_a_brand_colour(pages):
    """tokens.css: "Brand colours and DATA colours are separate scales."

    A rebrand must not silently change what a colour means in a chart. The one
    exemption is a chart that deliberately reproduces the published explorer's
    palette, which used the EIL maroon as a chart colour — and such a chart has
    to say so, with `data-palette="published"`. Everything else stays on the
    data scale.
    """
    for gid, html in pages.items():
        for svg in re.findall(r"<svg.*?</svg>", html, re.DOTALL):
            if 'class="ph-art"' in svg:
                continue    # a placeholder for a figure still to come
            marks = re.findall(r'(?:fill|stroke)="var\((--[a-z0-9-]+)\)"', svg)
            brandish = [m for m in marks if m.startswith("--brand")]
            if brandish:
                assert 'data-palette="published"' in svg, (
                    f"{gid}: a chart uses brand token(s) {brandish} without declaring "
                    "that it reproduces the published palette"
                )
            assert any(m.startswith("--data-") for m in marks), (
                f"{gid}: no data-scale colour in a chart; found {sorted(set(marks))}"
            )


def test_place_comparisons_are_two_shades_of_one_hue(pages):
    """One measure in two places is not two different things.

    The published figures drew #003f5c over #a6cee3. Two categorical hues would
    signal that the bars measure different things, which is the wrong reading —
    but only where the two series are two PLACES. A chart whose series are
    genuinely different groups, like race over time, wants the categorical
    scale and is not covered here.
    """
    for gid, html in pages.items():
        name = re.search(r"<h1>([^<]+)</h1>", html).group(1)
        for svg in re.findall(r"<svg.*?</svg>", html, re.DOTALL):
            legend = " ".join(re.findall(r'class="legend">([^<]*)<', svg))
            if name not in legend or "United States" not in legend:
                continue    # not a place-vs-place comparison
            fills = set(re.findall(r'fill="var\((--data-[a-z0-9-]+)\)"', svg))
            assert "--data-2" not in fills, (
                f"{gid}: a place comparison uses the categorical orange; use the "
                f"sequential blue instead. Found {sorted(fills)}"
            )


def test_assets_are_inlined_not_linked(pages):
    """No second request, and a page works when opened from disk."""
    for gid, html in pages.items():
        assert "data:image/webp;base64," in html, f"{gid}: the logo is not inlined"
        assert 'src="assets/' not in html, f"{gid}: an asset is linked rather than inlined"


def test_no_raster_chart_images(pages):
    """Charts are inline SVG. The old build wrote 62,860 .webp files."""
    for gid, html in pages.items():
        assert ".webp\"" not in html.replace("data:image/webp;base64,", ""), gid
        assert "cloudfront" not in html.lower(), f"{gid}: still pointing at the old CDN"
        assert html.count("<svg") >= 4, f"{gid}: expected an SVG per figure"


# ---------------------------------------------------------------------------
# The income figures, as the published explorer drew them
# ---------------------------------------------------------------------------

def test_income_distribution_figure_matches_the_published_design(pages):
    """Single series, share printed in the bar, dashed line at an even spread.

    `02_generate_county_figures.R:483-490` — one `geom_col` in the data blue
    and `geom_text` with the percentage in white. The reference line that
    figure also drew is tested separately, since it is now classic-only.
    """
    for gid, html in pages.items():
        section = html[html.index('id="income_composition"'):]
        fig = section[:section.index("</figure>")]
        on_bar = re.findall(r'class="tick mid on-bar"', fig)
        assert len(on_bar) >= 8, (
            f"{gid}: only {len(on_bar)} shares printed inside their bars"
        )
        assert "National income decile" in fig and "Share of residents" in fig


def test_income_by_race_is_small_multiples_normalised_within_each_group(computed, pages):
    """One panel per group, each summing to 100% inside itself.

    The published figure normalises with
    `group_by(region, race) %>% mutate(share = total_count / sum(total_count))`,
    so the comparison is between the SHAPES of the distributions. Comparing
    levels would only restate that the groups are different sizes.
    """
    for gid, fig in computed.items():
        cx = fig.cross["income_composition"]
        assert len(cx.order_outer) >= 5, f"{gid}: only {len(cx.order_outer)} groups"
        for g in cx.order_outer:
            total = sum(cx.share(g, d) or 0 for d in cx.order_inner)
            assert total == pytest.approx(100.0, abs=0.01), (
                f"{gid}/{g}: panel sums to {total}%, so it is not normalised within "
                "its own group"
            )
        assert "0" not in cx.order_inner, (
            f"{gid}: decile 0 must be excluded here on the same convention as "
            "the one-dimensional case"
        )

    # And on the rendered figure, not just the data behind it. The bar
    # tooltips carry the share, so the normalisation is checkable from the SVG.
    import re
    for gid, html in pages.items():
        fig = _race_figure(html)
        assert fig, f"{gid}: the income-by-race figure is missing"
        titles = re.findall(r'class="panel-title"[^>]*>([^<]+)<', fig)
        assert len(titles) >= 5, f"{gid}: only {len(titles)} panels rendered"

        # The subject series is labelled with the place's own name, never a
        # placeholder, so match on the name the page actually uses.
        name = re.search(r'<h1>([^<]+)</h1>', html).group(1)
        shares = [float(x) for x in re.findall(
            rf"<title>{re.escape(name)} — decile \d+: ([\d.]+)%</title>", fig)]
        assert len(shares) == 10 * len(titles), (
            f"{gid}: expected 10 bars per panel, got {len(shares)} across "
            f"{len(titles)} panels"
        )
        for i, name in enumerate(titles):
            total = sum(shares[i * 10:(i + 1) * 10])
            assert total == pytest.approx(100.0, abs=0.6), (
                f"{gid}/{name}: rendered panel sums to {total:.1f}%, so it is not "
                "normalised within its own group"
            )


def _race_figure(html: str) -> str:
    """The Figure 5 block, or "" if it was not rendered."""
    marker = "Income Distribution by Race"
    if marker not in html:
        return ""
    start = html.rindex("<figure", 0, html.index(marker))
    return html[start:html.index("</figure>", start)]


def test_race_panels_keep_the_residual_group(pages):
    """The published figure dropped "Other or unknown"; this one shows it.

    That category is 10-24% of population depending on the place. Dropping a
    group that large from a figure about groups is the same omission that
    inverted the old race comparison. Asserted on the rendered panels, because
    that is where a group actually goes missing.
    """
    import re
    for gid, html in pages.items():
        fig = _race_figure(html)
        titles = re.findall(r'class="panel-title"[^>]*>([^<]+)<', fig)
        assert any("nknown" in t for t in titles), (
            f"{gid}: the residual group is missing from the rendered panels: {titles}"
        )


def test_the_income_section_renders_both_figures(pages):
    """The distribution, then the same distribution by race.

    Counted up to the map subsection, which now lives inside this section
    rather than in one of its own -- the heading it used to carry repeated the
    one directly above it.
    """
    for gid, html in pages.items():
        section = html[html.index('id="income_composition"'):]
        section = section[:section.index("</section>")]
        charts = section.split('id="maps-income"')[0]
        assert charts.count("<figure") == 2, (
            f"{gid}: the income section has {charts.count('<figure')} charts, not 2"
        )
        # Numbered by position, so the classic pages — which carry fewer
        # figures — do not skip numbers.
        labels = re.findall(r'<p class="fig-label">Figure (\d+)</p>', html)
        assert labels == [str(i + 1) for i in range(len(labels))], (
            f"{gid}: figure numbering is {labels}"
        )


def test_no_legend_says_this_place(pages):
    """Legends name the place. "This place" is a placeholder, not a label."""
    for gid, html in pages.items():
        assert "This place" not in html, f"{gid}: a legend still says 'This place'"
        name = re.search(r"<h1>([^<]+)</h1>", html).group(1)
        assert f">{name}</text>" in html, (
            f"{gid}: no chart legend names {name!r}"
        )


# ---------------------------------------------------------------------------
# Tabs, tooltips, and fitting the window
# ---------------------------------------------------------------------------

def test_sections_are_grouped_into_the_declared_tabs(pages):
    """The published explorer's three tabs, driven by the registry.

    Age lives inside Population, as it did there. The grouping is declared in
    `catalog/explorer.yaml`, so moving a section between tabs is a config edit.
    """
    declared = [t for t, _ in config.tabs()]
    for gid, html in pages.items():
        assert 'class="tabs"' in html, f"{gid}: no tab strip"
        rendered = re.findall(r'class="tab" role="tab" data-tab="([^"]+)"', html)
        assert rendered == declared, f"{gid}: tabs {rendered} != declared {declared}"
        for tab in declared:
            assert f'class="tabpanel" id="tab-{tab}"' in html, f"{gid}: no panel for {tab}"
        assert 'id="age_structure"' in html and 'id="population"' in html


def test_page_reads_top_to_bottom_without_javascript(pages):
    """Panels start visible; the script hides them.

    A tab strip that ships content hidden is content a search engine and a
    reader with JavaScript off never see. Nothing here is `hidden` in the
    markup — `[hidden]` is applied at runtime.
    """
    for gid, html in pages.items():
        panels = re.findall(r'<div class="tabpanel"[^>]*>', html)
        assert panels, f"{gid}: no panels"
        for p in panels:
            assert "hidden" not in p, f"{gid}: a panel ships hidden: {p}"


def test_every_mark_carries_its_own_numbers(pages):
    """Tooltips read from the mark, and work without the script too.

    Each mark has `data-v`/`data-k` for the styled tooltip and a native
    `<title>` as the no-JavaScript fallback, so a value is always reachable.
    """
    for gid, html in pages.items():
        marks = re.findall(r'<(?:rect|circle) class="mark"[^>]*>', html)
        assert len(marks) > 100, f"{gid}: only {len(marks)} interactive marks"
        for m in marks[:40]:
            assert "data-v=" in m and "data-k=" in m, f"{gid}: mark without data: {m}"
        titled = re.findall(r'class="mark"[^>]*>\s*<title>', html)
        assert len(titled) >= len(marks) - 2, (
            f"{gid}: {len(marks) - len(titled)} marks have no native <title> fallback"
        )


def test_nothing_is_pinned_to_a_minimum_width(pages):
    """Figures and tables scale down; they never force a sideways scroll.

    A horizontal scrollbar on a chart is a reader being asked to reassemble it
    from parts. The viewBox already keeps the composition identical at any
    width, so the figure just gets smaller.
    """
    css = (Path(__file__).parent.parent / "pipeline" / "explorer.css").read_text()
    assert "min-width" not in css.split("/* ---------- tabs")[0], (
        "a min-width in the figure or table styles reintroduces sideways scrolling"
    )
    for gid, html in pages.items():
        assert "min-width" not in html, f"{gid}: inline min-width in the markup"


def test_longitudinal_figures_are_on_the_main_pages(pages):
    """The trends answer questions the snapshots cannot.

    Whether a place is ageing, or how its composition shifted over 25 years,
    is not visible in a single-year bar chart. Those figures came from the
    published explorer and belong in the redesign too, not only beside it.
    """
    for gid, html in pages.items():
        assert "Population Shares by Age Group, 2000–2025" in html, (
            f"{gid}: the age trend is missing from the main page"
        )
        assert "Population Shares, 2000–2025" in html, (
            f"{gid}: the race composition trend is missing from the main page"
        )
        assert html.count("<figure") >= 7, (
            f"{gid}: only {html.count('<figure')} figures; expected the snapshots "
            "and the trends"
        )


# ---------------------------------------------------------------------------
# Prose, placeholders, and the hover trace
# ---------------------------------------------------------------------------

def test_the_trend_line_is_hoverable_along_its_whole_length(pages, computed):
    """Every year gets a hit area; the trace itself stays a clean line.

    A 3px point is a poor target and a row of dots clutters a 26-year series.
    Full-height bands, one per year, give the reader the whole column.
    """
    for gid, html in pages.items():
        svg = re.search(r"<svg[^>]*Population over time.*?</svg>", html, re.DOTALL).group(0)
        bands = re.findall(r'<g class="mark"[^>]*data-x="(\d{4})"', svg)
        years = [y for y in computed[gid].population.years]
        assert len(bands) == len(years), (
            f"{gid}: {len(bands)} hover bands for {len(years)} years"
        )
        assert svg.count("hover-dot") == len(years)
        assert 'fill="transparent"' in svg, f"{gid}: the hit areas are not invisible"


def test_prose_carries_subheads_and_definitions(pages):
    """The published pages explained their terms before using them.

    A share of a national income decile means nothing to a reader who has not
    been told what a decile is, and the old site said so on every page.
    """
    for gid, html in pages.items():
        heads = re.findall(r"<h3[^>]*>([^<]+)</h3>", html)
        for expected in ("Size and density", "Growth since 2015",
                         "What is measured", "Distribution across national deciles"):
            assert expected in heads, f"{gid}: missing sub-heading {expected!r}"
        # A sub-heading that merely repeats its section heading tells the reader
        # nothing; the age and race sections lost theirs for exactly that.
        sections = re.findall(r"<h2>([^<]+)</h2>", html)
        for h2 in sections:
            assert h2.lower() not in {h.lower() for h in heads}, (
                f"{gid}: sub-heading {h2!r} only repeats its section heading"
            )
        assert "Schedule 1 of IRS Form 1040" in html, f"{gid}: AGI is never defined"
        assert "Hispanic takes precedence" in html, f"{gid}: the race rule is unstated"
        assert "ten equally sized groups" in html, f"{gid}: deciles are never defined"


def test_race_shares_are_also_given_as_bullets(pages):
    """One line per group, each against the national figure."""
    for gid, html in pages.items():
        bullets = re.findall(r"<li><b>([^<]+)</b>(.*?)</li>", html, re.DOTALL)
        assert len(bullets) >= 5, f"{gid}: only {len(bullets)} group bullets"
        for name, rest in bullets:
            text = re.sub(r"<[^>]+>", "", rest)
            assert re.search(r"\d+(\.\d+)?% of residents", text), (
                f"{gid}/{name}: no share stated — got {text.strip()[:80]!r}"
            )
        joined = " ".join(re.sub(r"<[^>]+>", "", r) for _, r in bullets)
        assert "nationwide" in joined, f"{gid}: no bullet compares against the nation"


def test_a_missing_number_is_shown_as_a_gap_not_a_guess(pages):
    """Without land area, the page shows a hole rather than inventing a figure.

    `pages` is built without the grid, which is exactly the case where density
    cannot be computed. A plausible-looking placeholder would be worse than a
    visible one: the failure mode is a reader believing it.
    """
    for gid, html in pages.items():
        assert 'class="tbd">QQ<' in html, f"{gid}: no visible gap where density goes"
        assert re.search(r"\b\d[\d,.]*</strong> people per square", html) is None, (
            f"{gid}: a density figure appeared without the data behind it"
        )


def test_no_page_promises_a_map_it_does_not_show(pages):
    """The placeholder is gone, and nothing may reintroduce it.

    Before the maps were real, a page that could not draw one rendered a "Map
    in progress" panel from a `planned_maps` registry section. Once the maps
    became real that panel could only ever appear where the data cannot
    support one -- a state, whose grid is never built, or Kalawao County,
    which holds sixteen people in three cells: enough for a density map and
    not enough for the per-group panels. In every one of those cases the
    promise was permanent, so the scaffolding was removed.
    """
    for gid, html in pages.items():
        assert "Map in progress" not in html, f"{gid}: a map placeholder came back"
        assert 'class="ph-art"' not in html, f"{gid}: placeholder artwork came back"


def test_a_place_with_cells_gets_all_three_maps(pages):
    """Population, race and income each get a map where the data allows it.

    The income map is the one only this data can produce: income at grid
    resolution is not available from any other public source, so where the top
    and bottom of the national distribution live is the explorer's most
    distinctive figure rather than a restatement of something coarser.
    """
    for gid, html in pages.items():
        if "Where People Live in" not in html:
            continue        # this place draws no maps at all; covered elsewhere
        for tab, label in (("population", "Where people live"),
                           ("race", "Where each group lives"),
                           ("income", "Where the highest and lowest earners live")):
            assert f'<h3 id="maps-{tab}">{label}</h3>' in html, (
                f"{gid}: no map subsection for {label!r}"
            )


def test_a_page_can_still_stand_alone(computed, catalog):
    """Self-contained when asked, shared when told where the assets are."""
    fig = figures.with_comparisons(computed["51003"], catalog)
    standalone = render.page(fig)
    shared = render.page(fig, assets="assets/")
    assert "<style>" in standalone and "data:image/webp;base64," in standalone
    assert "<style>" not in shared and "data:image/webp;base64," not in shared
    # The name carries the stylesheet's content hash, so the URL changes
    # whenever the bytes do and no cached copy can outlive them.
    assert f'href="assets/{render.stylesheet_name()}"' in shared
    assert re.search(r"explorer\.[0-9a-f]{8}\.css", shared), (
        "the shared stylesheet is not content-addressed"
    )
    assert len(shared) < len(standalone) - 30_000, (
        f"sharing assets saved only {len(standalone) - len(shared):,} bytes"
    )


# ---------------------------------------------------------------------------
# Maps
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def map_pages(catalog) -> dict:
    """Pages built with cells, which is what the maps and density need."""
    out = {}
    for gid in ("51003", "51685"):
        try:
            fig = figures.with_comparisons(
                figures.figures_for("county", gid, catalog, with_grid=True), catalog)
        except Exception as e:  # noqa: BLE001 — the raw source may be unreachable
            pytest.skip(f"cell data unavailable: {e}")
        if fig.grid is None:
            pytest.skip("cell data unavailable")
        out[gid] = (fig, render.page(fig))
    return out


def test_every_map_block_is_a_real_sum_of_cells(map_pages):
    """Aggregation, not smoothing — so a block reading 400 people holds 400.

    The alternative was a Gaussian blur, where every drawn value is a number
    nobody counted. Quadtree aggregation attacks the noise where it lives, in
    small counts, and leaves dense areas at full resolution.
    """
    from pipeline import grid
    conv = config.registry()["conventions"]
    for gid, (fig, _) in map_pages.items():
        blocks = grid.aggregate(fig.grid, "pop",
                                min_count=conv["map_min_block_population"],
                                max_level=conv["map_max_aggregation_level"])
        assert blocks, f"{gid}: no blocks"
        assert sum(b.value for b in blocks) == pytest.approx(fig.grid.total, rel=1e-9), (
            f"{gid}: aggregation changed the total — it must be a partition"
        )
        assert sum(b.cells for b in blocks) == len(fig.grid), (
            f"{gid}: every populated cell belongs to exactly one block"
        )
        # Adaptive: a dense place keeps single-cell resolution somewhere.
        assert min(b.span for b in blocks) == pytest.approx(grid.CELL_DEG), (
            f"{gid}: no block stayed at full resolution"
        )
        # The map note promises every group holds the threshold. Only a
        # top-size square whose whole content falls short may hold less.
        top = (1 << conv["map_max_aggregation_level"]) * grid.CELL_DEG
        short = [b for b in blocks if b.value < conv["map_min_block_population"]]
        assert all(b.span == pytest.approx(top) for b in short), (
            f"{gid}: a block below the threshold was split smaller than the cap"
        )


def test_every_cell_gets_its_own_index(map_pages):
    """Cell centres sit on the half-step, where round() is ambiguous.

    It merged Albemarle's 1,440 cells onto 727 indices, so the quadtree ran on
    a lattice where neighbors were the same cell.
    """
    import math

    from pipeline import grid
    for gid, (fig, _) in map_pages.items():
        idx = {(math.floor(lo / grid.CELL_DEG), math.floor(la / grid.CELL_DEG))
               for lo, la in zip(fig.grid.lons, fig.grid.lats)}
        assert len(idx) == len(fig.grid), f"{gid}: two cells share an index"


def test_cells_are_painted_with_their_own_blocks_density(map_pages):
    from pipeline import grid, render
    conv = config.registry()["conventions"]
    for gid, (fig, _) in map_pages.items():
        blocks = grid.aggregate(fig.grid, "pop",
                                min_count=conv["map_min_block_population"],
                                max_level=conv["map_max_aggregation_level"])
        owner = {}
        for b in blocks:
            n = round(b.span / grid.CELL_DEG)
            for i in range(n):
                for j in range(n):
                    key = render._cell_key(b.lon + (i + .5) * grid.CELL_DEG,
                                           b.lat + (j + .5) * grid.CELL_DEG)
                    assert key not in owner, f"{gid}: blocks overlap"
                    owner[key] = b.density
        for lon, lat, dens, _v in render._cells_for(fig, "pop"):
            assert dens == pytest.approx(owner[render._cell_key(lon, lat)]), (
                f"{gid}: a cell carries another block's density"
            )


def test_prose_figure_references_match_the_figure_that_follows():
    from pipeline import render
    html = render._number_figures(
        "FIGURE_N <p>FIGREF_NEXT shows</p> FIGURE_N <p>FIGREF_NEXT plots</p> FIGURE_N")
    assert html == "Figure 1 <p>Figure 2 shows</p> Figure 2 <p>Figure 3 plots</p> Figure 3"


def test_map_panels_share_one_frame(map_pages):
    """Small multiples must show the same ground.

    Scaled to its own blocks, a panel for a group living in one town becomes a
    map of that town, and a reader comparing panels compares different places
    without being told.
    """
    for gid, (_, html) in map_pages.items():
        block = html[html.index("Where Racial and Ethnic Groups Live"):]
        block = block[:block.index("</figure>")]
        groups = re.findall(r'<g transform="translate\([^)]*\)">(.*?)</g>', block, re.DOTALL)
        assert len(groups) >= 4, f"{gid}: only {len(groups)} race map panels"
        # The outline is projected with the panel's own transform, so identical
        # outline geometry is exact proof that the frames match.
        outlines = {re.search(r'<path d="([^"]+)"', g).group(1) for g in groups}
        assert len(outlines) == 1, (
            f"{gid}: {len(outlines)} different outlines across panels — the frames differ"
        )


def test_maps_colour_by_density_not_count(map_pages):
    """Grouped cells differ in size, so only density is comparable between them."""
    import html as _html
    for gid, (_, page) in map_pages.items():
        assert "population density on a logarithmic scale" in page.lower(), (
            f"{gid}: the note does not say what colour means"
        )
        blob = re.search(r'data-hover="([^"]+)"', page)
        assert blob, f"{gid}: the map carries no hover table"
        table = json.loads(_html.unescape(blob.group(1)))
        assert table["cells"], f"{gid}: the hover table is empty"
        # Each entry is [density, count]; density is what the colour encodes.
        first = next(iter(table["cells"].values()))
        assert len(first) == 2 and first[0] > 0, f"{gid}: hover entry {first}"


def test_maps_are_paths_not_thousands_of_rects(map_pages):
    """One element per colour, not one per cell.

    At about 200 bytes a rect, a county map ran to 283 KB and put 1,446 nodes
    in the DOM. Cells sit on a regular lattice, so the pointer position
    converts straight to a column and row — the lookup table replaces the
    elements without losing the tooltip.
    """
    for gid, (_, page) in map_pages.items():
        block = page[page.index("Where People Live in"):]
        block = block[:block.index("</figure>")]
        cell_paths = len(re.findall(r'<path class="cell"', block))
        rects = len(re.findall(r"<rect", block))
        assert 1 <= cell_paths <= 8, f"{gid}: {cell_paths} colour paths"
        assert rects <= 12, (
            f"{gid}: {rects} rects in a map — the per-cell elements are back"
        )


def test_density_is_reported_both_ways(map_pages):
    """Conventional density for comparability, populated density for meaning."""
    for gid, (fig, html) in map_pages.items():
        assert "per square kilometer of land" in html, f"{gid}: no conventional density"
        assert "where people actually live" in html, f"{gid}: no populated-area density"
        assert 'class="tbd">QQ<' not in html, f"{gid}: a gap remains where density goes"
        conv = fig.population.at(2025) / fig.land_km2
        lived = fig.grid.total / fig.grid.land_km2
        assert lived > conv, (
            f"{gid}: density over populated land ({lived:.0f}) should exceed density "
            f"over all land ({conv:.0f}) — people do not live on every hectare"
        )


def test_no_map_placeholders_remain_where_cells_exist(map_pages):
    for gid, (_, html) in map_pages.items():
        assert "Map in progress" not in html, f"{gid}: a placeholder survived"
        assert html.count('class="mapfig"') == 3, (
            f"{gid}: expected population, race and income maps"
        )


def test_share_changes_are_stated_in_percentage_points(pages):
    """A share moving 13% to 21% has not "grown 8%".

    The published pages said exactly that — `round(abs(under_18_pct_change), 1)`
    rendered inside "the population was growing by X%" — and it is the same
    conflation of a level change with a rate that made their growth figures
    unreadable.
    """
    for gid, html in pages.items():
        # Assert the failure directly: a change in a share followed by a percent
        # sign rather than "percentage points".
        bad = re.findall(r"share has (?:risen|fallen) [\d.]+\s*%", html)
        bad += re.findall(r"share moved (?:up|down) [\d.]+\s*%", html)
        assert not bad, f"{gid}: share changes stated as percentages: {bad}"

        good = re.findall(r"share (?:has (?:risen|fallen)|moved (?:up|down)) "
                          r"[\d.]+ percentage points", html)
        assert good, f"{gid}: no share change carries its unit"


def test_no_sentence_reads_a_similar_share_as_a_difference(pages):
    """"about the same share than" is not English, and it was reachable."""
    for gid, html in pages.items():
        assert "about the same share than" not in html, f"{gid}: broken comparison phrasing"
        assert "a about" not in html, f"{gid}: article before a comparison word"


def test_age_residual_is_off_the_chart_but_in_the_table(map_pages):
    """Dropped from the bars, kept in the denominator, kept in the table.

    Age not reported is under two percent and nobody is comparing it to
    anything — a bar for it costs attention and returns nothing. Removing it
    from the denominator would be a different and much worse decision.
    """
    for gid, (fig, page) in map_pages.items():
        sh = fig.shares["age_structure"]
        section = page[page.index('id="age_structure"'):]
        section = section[:section.index("</section>")]
        label = sh.labels[sh.residual]

        svgs = re.findall(r"<svg.*?</svg>", section, re.DOTALL)
        bars = svgs[-1]
        drawn = re.findall(r'class="tick mid">([^<]+)</text>', bars)
        assert label not in drawn, f"{gid}: {label!r} is still a bar"
        assert len(drawn) == len(sh.order) - 1, f"{gid}: drew {drawn}"

        rows = re.findall(r"<tr[^>]*><td>([^<]+)</td>", section)
        assert label in rows, f"{gid}: {label!r} was dropped from the table too"
        assert "not reported" in section and "denominator" in section, (
            f"{gid}: the note does not explain the missing bar"
        )


def test_maps_carry_location_context(map_pages):
    """A map needs to say where it is, not only what is in it.

    Zero-filled cells so the whole area is visible, neighboring outlines so it
    sits somewhere, and a latitude/longitude graticule. Without these a reader
    sees colored squares and cannot place them.
    """
    for gid, (_, page) in map_pages.items():
        block = page[page.index("Where People Live in"):]
        block = block[:block.index("</figure>")]
        assert 'class="grat"' in block, f"{gid}: no graticule"
        assert re.search(r"\d+(\.\d+)?°[NW]", block), f"{gid}: coordinates are unlabeled"
        assert block.count('stroke="var(--line)"') >= 3, (
            f"{gid}: no neighboring outlines for context"
        )
        # Zero cells are drawn where the place has any -- Manassas Park is
        # 2.5 km2 and populated end to end, so it legitimately has none.
        from pipeline import shapes as _sh
        fig = map_pages[gid][0]
        _, empties = render.zero_fill(render._cells_for(fig, "pop"),
                                      _sh.rings("county", fig.geo_id))
        if empties:
            assert f'fill="{render.EMPTY_FILL}"' in block, (
                f"{gid}: {len(empties)} unpopulated cells omitted rather than drawn"
            )


def test_zero_cells_tile_with_the_populated_ones(map_pages):
    """One lattice, not two.

    Source coordinates are cell CENTRES (`centroid_offset: 0.005`, cell edges
    on exact multiples of 0.01). Zero-filling on multiples of 0.01 instead put
    the empty cells half a cell off from the populated ones, so they interleaved
    rather than tiled and left white gaps between every square.
    """
    from pipeline import grid, shapes as _sh
    step = grid.CELL_DEG
    for gid, (fig, _page) in map_pages.items():
        cells = render._cells_for(fig, "pop")
        _, empties = render.zero_fill(cells, _sh.rings("county", fig.geo_id))
        if not (cells and empties):
            continue
        # Every zero cell sits on the same lattice as the populated cells.
        ox, oy = cells[0][0], cells[0][1]
        for x, y in empties[:400]:
            for v, o in ((x, ox), (y, oy)):
                off = abs((v - o) / step - round((v - o) / step))
                assert off < 1e-6, (
                    f"{gid}: zero cell at {x},{y} is {off:.3f} of a cell off the "
                    "lattice the data uses"
                )


# --------------------------------------------------------------------------
# Every group is drawn, however small.
# --------------------------------------------------------------------------

# Small enough that a careless figure might drop it. Albemarle's 185 AIAN
# residents are the case these tests exist for.
SMALL = 1000


def _small(fig) -> dict[str, float]:
    """Race groups small enough to caveat, display name -> population."""
    sh = fig.shares.get("race_ethnicity")
    if sh is None:
        return {}
    floor = SMALL
    return {sh.labels.get(c, c).split(" (")[0]: sh.categories[c]
            for c in sh.order if sh.categories.get(c, 0) < floor}


def _mentions(name: str, svg: str) -> bool:
    """Whether a figure draws this group.

    Matched on a prefix, because an axis tick is truncated to fit -- the race
    bar chart draws AIAN as "American Ind...", which a whole-name search reads
    as a group that was dropped. Charts print the catalog's abbreviation where
    it gives one, so "AIAN" counts as drawing American Indian & Alaska Native.
    """
    short = {"American Indian & Alaska Native": "AIAN"}.get(name)
    return name.replace("&", "&amp;")[:10] in svg or bool(short and f">{short}<" in svg)


def _race_figures(html: str, names: list[str]):
    """Figure blocks whose marks are race groups."""
    for block in re.findall(r"<figure class=\"eil\">.*?</figure>", html, re.DOTALL):
        body, _, foot = block.partition("<figcaption")
        if sum(1 for n in names if _mentions(n, body)) >= 2:
            yield block, body, foot


def _race_names(fig):
    sh = fig.shares.get("race_ethnicity")
    return [] if sh is None else [sh.labels.get(c, c).split(" (")[0] for c in sh.order]


def test_every_group_is_drawn_however_small(pages, computed):
    """No group is dropped for being small.

    Albemarle has 185 AIAN residents against a 1,000-person caveat floor. The
    figures used to drop the group and say so beneath; a reader who finds a
    group in the table and not in the chart concludes the group is absent,
    which is the one thing the data does not say. Every group is drawn now.
    """
    checked = 0
    for gid, html in pages.items():
        names = _race_names(computed[gid])
        for _block, body, _foot in _race_figures(html, names):
            for name in names:
                checked += 1
                assert _mentions(name, body), (
                    f"{gid}: a race figure does not draw {name!r}"
                )
    assert checked, "no race figure exercised; the tripwire proves nothing"


def test_no_figure_carries_the_retired_small_group_caveat(pages):
    """Dropped 2026-10-01: drawn groups no longer carry a noise caveat."""
    for gid, html in pages.items():
        assert "read the shape, not the individual points" not in html, gid


@pytest.fixture(scope="module")
def mapped_page(catalog) -> str:
    """Albemarle rendered with its real cell grid, not the placeholder.

    The `pages` fixture builds without the grid, so every map there is the
    planned-map placeholder. The built map is a different code path with its
    own notes, and the group it suppresses is suppressed for a different
    reason -- so it needs its own page.
    """
    try:
        fig = figures.figures_for("county", "51003", catalog, with_grid=True)
        return render.page(figures.with_comparisons(fig, catalog))
    except Exception as ex:  # noqa: BLE001 — source grid unavailable means skip
        pytest.skip(f"cell grid unavailable: {ex}")


def test_the_built_race_map_draws_every_group(mapped_page, computed):
    """185 residents still get a panel.

    The map is a different code path from the charts, with its own filters and
    its own notes, so it needs its own assertion: the group is drawn, and the
    notes spell out the abbreviation its panel title uses.
    """
    small = _small(computed["51003"])
    assert small, "Albemarle should have a group below the caveat floor"
    block = next(b for b in re.findall(r"<figure class=\"eil\">.*?</figure>",
                                       mapped_page, re.DOTALL)
                 if "Where Racial and Ethnic Groups Live" in b)
    body, _, foot = block.partition("<figcaption")
    assert "ph-art" not in body, "this is still the placeholder, not a built map"
    for name in small:
        assert _mentions(name, body), f"the built race map has no {name!r} panel"
        assert _mentions(name, foot), f"the built race map never names {name!r}"


BRITISH = re.compile(
    r"\b(colour\w*|labell\w+|neighbour\w*|normalis\w+|analys(?:e|ed|es|ing)|"
    r"organis\w+|behaviour\w*|centre|grey)\b", re.I)


def test_reader_facing_text_is_american_english(pages, mapped_page):
    """One spelling convention, and it is the American one.

    Notes and prose are read; code comments are not. This checks what the page
    actually says, with the SVG marks stripped so a CSS class or token never
    counts as prose.
    """
    for gid, html in {**pages, "51003-map": mapped_page}.items():
        prose = re.sub(r"<svg.*?</svg>", " ", html, flags=re.DOTALL)
        prose = re.sub(r"<style.*?</style>", " ", prose, flags=re.DOTALL)
        prose = re.sub(r"<script.*?</script>", " ", prose, flags=re.DOTALL)
        prose = re.sub(r"<[^>]+>", " ", prose)
        found = sorted({m.group(0) for m in BRITISH.finditer(prose)})
        assert not found, f"{gid}: British spellings in reader-facing text: {found}"


def test_a_cell_is_drawn_centred_on_its_own_coordinate():
    """Source coordinates are cell centres, not corners.

    `centroid_offset: 0.005` in the upstream registry, with cell edges landing
    on exact multiples of 0.01. Drawing the square with its corner on the point
    shifted every map half a cell -- about half a kilometre north-east -- so
    settlement sat off its own roads, towns and boundary.

    Rendered with one cell and no outline, the square must bracket the point.
    """
    lon, lat = -78.225, 38.135
    ext = (lon - 0.05, lon + 0.05, lat - 0.05, lat + 0.05)
    svg = render.map_chart([(lon, lat, 100.0, 42.0)], label="Population",
                           extent=ext, legend=False, graticule=False,
                           interactive=False)
    m = re.search(r'<path class="cell" [^>]*d="M([-\d.]+),([-\d.]+)'
                  r'h([\d.]+)v([\d.]+)', svg)
    assert m, f"no cell path in {svg[:400]}"
    x, y, w_, h_ = (float(g) for g in m.groups())
    px, py, _scale, _k = render._project(ext, 900, 560, 10)
    cx, cy = px(lon), py(lat)
    assert x < cx < x + w_, (
        f"cell spans x {x:.1f}..{x + w_:.1f} but its own longitude projects to "
        f"{cx:.1f} — the square is not centred on its point"
    )
    assert y < cy < y + h_, (
        f"cell spans y {y:.1f}..{y + h_:.1f} but its own latitude projects to "
        f"{cy:.1f} — the square is not centred on its point"
    )


# ---------------------------------------------------------------------------
# Panel lattice
# ---------------------------------------------------------------------------

def test_coarsening_moves_no_people():
    """A coarsened panel holds exactly the residents the fine one did.

    The small multiples are drawn on a lattice matched to their render width,
    because a 0.01-degree cell across a metro at 290px is under a pixel. That
    is a redraw, not a recount: summing counts and recomputing density from
    the summed area keeps every resident. Averaging the densities instead
    would quietly under-weight the larger cells at high latitude, which is
    exactly the bug this guards.
    """
    fine = [(-78.5, 38.0, 100.0, 85.0), (-78.49, 38.0, 50.0, 42.0),
            (-78.5, 38.01, 10.0, 8.0), (-78.49, 38.01, 0.0, 0.0),
            (-78.3, 47.0, 900.0, 760.0)]
    for mult in (2, 4, 8):
        out = render._coarsen(fine, mult)
        assert sum(c[3] for c in out) == pytest.approx(sum(c[3] for c in fine)), (
            f"mult={mult} changed the population"
        )
        assert len(out) < len(fine), f"mult={mult} merged nothing"
        assert all(c[2] >= 0 for c in out), "negative density"

    # Density is the count over the summed AREA, not the mean of the per-cell
    # densities. The two agree only when every merged cell is the same size,
    # which they are not away from the equator: cell area falls with the cosine
    # of latitude, so a mean quietly under-weights the big southern cells.
    # These two land in one coarse cell (step 40.96 degrees) but differ in area
    # by a third, which is what separates the right answer from the wrong one.
    from pipeline import grid
    wide = [(-78.5, 5.0, 0.0, 100.0), (-78.5, 39.0, 0.0, 100.0)]
    merged = render._coarsen(wide, 4096)
    assert len(merged) == 1, "the fixture no longer merges into one cell"
    area = sum(grid.cell_km2(la) for _lo, la, _d, _v in wide)
    mean_of_densities = sum(v / grid.cell_km2(la) for _lo, la, _d, v in wide) / 2
    assert merged[0][2] == pytest.approx(200.0 / area), (
        "density is not the summed count over the summed area"
    )
    assert merged[0][2] != pytest.approx(mean_of_densities, rel=1e-3), (
        "averaging the per-cell densities would also pass; it must not"
    )


def test_the_wide_map_keeps_full_resolution():
    """Only the panels trade resolution for bytes.

    The wide map is the figure whose whole point is kilometre-scale texture.
    If `_lattice_mult` ever returns more than 1 at 900px for a frame this
    size, that texture is being thrown away where it was asked for.
    """
    albemarle = (-78.80, -78.20, 37.72, 38.28)
    atlanta = (-85.4, -83.1, 33.0, 34.6)
    assert render._lattice_mult(albemarle, 900) == 1, "a county map was coarsened"
    assert render._lattice_mult(atlanta, 900) == 1, "a metro map was coarsened"


def test_panels_coarsen_where_the_cells_are_subpixel():
    """And that the rule actually fires somewhere.

    A test that only asserts "never coarsens" passes trivially if the feature
    is dead. Atlanta spans far enough that its 290px panels must coarsen, and
    a county the size of Albemarle must not -- the rule is meant to follow the
    geometry, not to coarsen everything.
    """
    assert render._lattice_mult((-85.4, -83.1, 33.0, 34.6), 290) > 1, (
        "a metro panel was not coarsened"
    )
    assert render._lattice_mult((-78.80, -78.20, 37.72, 38.28), 290) == 1, (
        "a county panel was coarsened, losing texture it had room to show"
    )


def test_a_sparse_place_does_not_report_zero_density():
    """Density is a number, not a rounded-away zero.

    The Aleutians hold 356 people across 11,000 square kilometres. At whole
    people per square kilometre that printed "0 people per square kilometer of
    land", which reads as uninhabited -- the one thing a page about its
    residents must not say.
    """
    assert render.density_num(356 / 11_000) == "0.03"
    assert render.density_num(0.004) == "under 0.01"
    assert render.density_num(3.27) == "3.3"
    assert render.density_num(36.6) == "37"
    assert render.density_num(1234.5) == "1,234"
    assert render.density_num(None) == "—"


def test_the_graticule_does_not_smear_across_a_wide_frame():
    """Meridian spacing follows the span.

    Fixed at half a degree, Yukon-Koyukuk drew forty labelled meridians and
    the labels collapsed into an unreadable smear along the bottom edge.
    """
    for extent in ((-78.80, -78.20, 37.72, 38.28),      # a county
                   (-85.4, -83.1, 33.0, 34.6),          # a metro
                   (-161.0, -141.0, 62.0, 68.5),        # Yukon-Koyukuk, 20 deg
                   (-77.47, -77.44, 38.76, 38.79)):     # Manassas Park, 0.03 deg
        w0, e0, s0, n0 = extent
        # Each axis separately: a tall narrow county judged on its larger span
        # got a single meridian and no east-west reference at all.
        for axis, span in (("x", e0 - w0), ("y", n0 - s0)):
            step = render.grat_step(extent, axis=axis)
            assert step in render.GRAT_STEPS
            assert span / step <= 8, f"{extent} crowds its {axis} labels"
            assert span / step >= 1, f"{extent} draws no {axis} graticule"


def test_a_very_wide_place_is_drawn_on_a_coarser_lattice():
    """The sub-pixel rule reaches the wide map, not just the panels.

    Yukon-Koyukuk spans twenty degrees. Drawn at one cell per 0.01 degree it
    was a 23 MB page -- past the 10 MB ceiling above which CloudFront stops
    compressing, so it would have been served raw.
    """
    yukon = (-161.0, -141.0, 62.0, 68.5)
    assert render._lattice_mult(yukon, 900, 560) > 4, "a 20-degree frame was not coarsened"

    # A tall narrow county is fitted by HEIGHT, so judging it by the width of
    # the box it is given says its cells are comfortable when they are not.
    apache = (-109.95, -109.04, 33.65, 37.00)
    assert render._lattice_mult(apache, 900, 560) > 1, (
        "a tall county was judged by its longitude span alone"
    )


# ---------------------------------------------------------------------------
# Site chrome
# ---------------------------------------------------------------------------

def test_every_page_links_only_to_pages_that_exist():
    """The nav promised an About page that was never built.

    It 404'd from all 4,120 place pages plus the index and the full listing,
    which no test caught because every test looked at one page in isolation.
    """
    import pipeline.cli as cli
    chrome = {"index.html", "places.html", "about.html"}
    built = {render.index([], assets="assets/"),
             render.places_page([], assets="assets/"),
             render.about_page(assets="assets/")}
    for html in built:
        for href in set(re.findall(r'href="\./([^"#]+)"', html)):
            assert href in chrome or href == "", f"nav links to unbuilt {href!r}"
    # And the builder actually writes each one.
    src = (Path(cli.__file__)).read_text()
    for name in chrome:
        assert f'"{name}"' in src, f"{name} is linked but never written"


def test_about_states_the_conventions_it_claims_to(registry_conventions):
    """The About page explains the pages; it must not drift from them.

    The things that can change -- whether maps group cells, the year growth
    is measured from, the measure -- are read from the code and registry, so a
    convention change that did not reach this page would show up here.
    """
    html = render.about_page(assets="assets/")
    text = re.sub(r"<[^>]+>", " ", html)
    if registry_conventions["map_min_block_population"]:
        assert str(registry_conventions["map_min_block_population"]) in text
    else:
        assert "drawn from its own count" in " ".join(text.split())
    assert str(render.GROWTH_BASE) in text
    measure = config.upstream_catalog()["measures"][registry_conventions["measure"]]
    assert measure["label"].lower().replace(" counts", "") in text.lower()
    # The income guardrail is the whole reason this section exists.
    for phrase in ("national decile", "Adjusted Gross Income"):
        assert phrase in " ".join(text.split()), f"About drops the income guardrail: {phrase!r}"


@pytest.fixture(scope="module")
def registry_conventions() -> dict:
    return config.registry()["conventions"]


def test_a_page_never_promises_a_figure_it_will_not_show(catalog):
    """State pages advertised three "Map in progress" placeholders.

    The grid is only built for the levels below a state, so those maps were
    never coming. A placeholder is a promise; where there is nothing to
    promise the section should be absent, and the figures that remain should
    still number straight through.
    """
    fig = figures.with_comparisons(
        figures.figures_for("state", "51", catalog, with_grid=True), catalog)
    html = render.page(fig, assets="assets/")
    assert "Map in progress" not in html, "a state page still promises a map"
    nums = [int(n) for n in re.findall(r'class="fig-label">Figure (\d+)<', html)]
    assert nums == list(range(1, len(nums) + 1)), f"figures do not run 1..n: {nums}"
    assert nums, "the state page has no figures at all"
    # And the levels that DO have maps still get them.
    county = render.page(
        figures.with_comparisons(
            figures.figures_for("county", "51003", catalog, with_grid=True), catalog),
        assets="assets/")
    assert "Where People Live in" in county, "the county map disappeared"
    assert len(re.findall(r'class="fig-label">Figure (\d+)<', county)) > len(nums)


def test_front_page_mirrors_the_gridded_eif_landing():
    from pipeline import render
    html = render.index([("county/51003/", "County", "Albemarle County, VA")],
                        assets="assets/")
    assert '<label for="q">Search for a place</label>' in html
    # The hint names one place of each kind, so it shows what can be searched.
    hint = html.split('data-hint="', 1)[1].split('"', 1)[0]
    assert "County" in hint and "metro" in hint and "Texas" in hint
    assert 'rel="icon"' in html and render.FAVICON in html
    assert "Disclaimer:" in html and "Voorheis" in html


def test_growth_is_stated_from_2015_but_plotted_from_2000(pages):
    """Before 2015 the records' coverage was still expanding.

    Albemarle reads +59% from 2000 to 2024 in the EIF against +40% in the
    Census Bureau's estimates; from 2015 the two agree within a point.
    """
    from pipeline import render
    for gid, html in pages.items():
        if "<h3>Growth since" not in html:
            continue
        assert f"<h3>Growth since {render.GROWTH_BASE}</h3>" in html, gid
        assert "decade by decade" not in html, gid
        assert f"Annual Population Counts, {render.BASE_YEAR}" in html, gid


def test_metro_names_take_an_article_in_prose(catalog):
    fig = figures.with_comparisons(figures.figures_for("cbsa", "16820", catalog), catalog)
    html = render.page(fig)
    assert "the Charlottesville, VA Metro Area was home to" in html
    assert "<h1>Charlottesville, VA Metro Area</h1>" in html


def test_a_similar_comparison_reads_as_similar():
    """Every caller's own word for "similar" must count as similar."""
    from pipeline import narrative
    c = narrative.Claim.of(1.55, 1.66, tolerance=0.25, higher="faster",
                           lower="slower", similar="at about the same rate")
    assert c.is_similar and c.word == "at about the same rate"


def test_a_map_key_never_sits_on_the_map(mapped_page):
    """The key has a band of its own above the drawing.

    It used to sit inside the frame at the top right -- empty for most shapes,
    but Randall County, TX is a rectangle and Amarillo was under it.
    """
    from pipeline import render
    for svg in re.findall(r'<svg viewBox="0 0 \d+ \d+" role="img" class="mapfig".*?</svg>',
                          mapped_page, re.DOTALL):
        if 'class="tick">People per square kilometer' not in svg.split("<g transform", 1)[0]:
            continue
        before, _, drawing = svg.partition(f'<g transform="translate(0,{render.KEY_BAND})">')
        assert drawing, "the drawing is not shifted below the key"
        assert 'class="cell' not in before, "a cell is drawn in the key's band"
        ys = [float(y) for y in re.findall(r'<rect x="[\d.]+" y="([\d.]+)"', before)]
        assert ys and max(ys) < render.KEY_BAND, "the key spills past its band"


def test_the_main_map_is_clipped_and_labeled(mapped_page, catalog):
    """Cells stay inside the outline, and towns are named on every main map."""
    block = next(b for b in re.findall(r"<figure class=\"eil\">.*?</figure>",
                                       mapped_page, re.DOTALL)
                 if "Where People Live" in b)
    assert "<clipPath" in block and 'clip-path="url(#clip' in block
    assert 'class="town"' in block, "the population map has no place names"


def test_a_micro_area_is_not_called_a_metro(catalog):
    fig = figures.with_comparisons(figures.figures_for("cbsa", "46860", catalog), catalog)
    html = render.page(fig)
    assert '<p class="eyebrow">Micro area</p>' in html
    assert "of the metro area" not in html


def test_a_shrinking_place_has_no_negative_rate(catalog):
    """Rush County, KS shrank; "shrank ... -0.10% a year" was a double negative."""
    fig = figures.with_comparisons(figures.figures_for("county", "20165", catalog), catalog)
    html = render.page(fig)
    assert "shrank from" in html and "-0." not in html.split("shrank from", 1)[1][:120]


def test_a_metro_name_before_residents_takes_no_article(catalog):
    """"the Charlottesville, VA Metro Area was home to" but "18.0% of
    Charlottesville, VA Metro Area residents" -- the name is an adjective there."""
    fig = figures.with_comparisons(figures.figures_for("cbsa", "16820", catalog), catalog)
    html = render.page(fig)
    assert "of the Charlottesville, VA Metro Area residents" not in html
    assert "of Charlottesville, VA Metro Area residents" in html
