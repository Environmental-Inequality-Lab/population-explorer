"""Regression tests against the golden-figures fixture.

These are not tests of the fixture generator. They are the tripwires that the
old pipeline lacked: each one fails on a specific bug the audit found in
production, so that bug cannot return without a red test.

The pairing that matters is between a number and the convention that produced
it. A fixture figure with no stated convention is not evidence of anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "golden-figures.json"

BASE_YEAR, CURRENT_YEAR = 2000, 2025   # headline policy is `latest`
SHARE_TOLERANCE = 0.01  # percentage points, absorbing per-category rounding


@pytest.fixture(scope="module")
def golden() -> dict:
    assert FIXTURE.exists(), f"missing {FIXTURE} — run tools/build_golden_fixture.py"
    return json.loads(FIXTURE.read_text())


def geographies(golden: dict):
    """Every pinned geography as (label, block), national included."""
    yield "national", golden["national"]
    for geo_id, block in golden["counties"].items():
        yield f"{geo_id} {block['name']}", block


SHARE_DIMENSIONS = [f"{d}_{CURRENT_YEAR}" for d in
                    ("age_shares", "race_shares", "income_decile_shares")]


# --------------------------------------------------------------------------
# Denominators
# --------------------------------------------------------------------------

def test_every_share_set_sums_to_100(golden):
    """Displayed shares must sum to 100%.

    The published site divided race shares by a residual-inclusive denominator
    and then dropped the residual from display, so Arlington's five shares
    summed to 77.9% against a national 87.4%. Because the two residuals were
    different sizes, the county-vs-nation comparison inverted: the page said
    Arlington was less White than the nation when it is more.
    """
    for label, block in geographies(golden):
        for dim in SHARE_DIMENSIONS:
            total = block[dim]["shares_sum_to_pct"]
            assert abs(total - 100.0) <= SHARE_TOLERANCE, (
                f"{label} / {dim} sums to {total}%, not 100% "
                f"(±{SHARE_TOLERANCE}) — a category is being dropped from display "
                f"but left in the denominator, or vice versa"
            )


# Dimensions whose residual belongs INSIDE the denominator and on the page.
# Income is deliberately absent: see test_income_shares_are_over_deciles_only.
RESIDUALS = {
    f"age_shares_{CURRENT_YEAR}": "Missing Age",
    f"race_shares_{CURRENT_YEAR}": "Other/Unknown",
}

INCOME_DIMENSIONS = [f"income_decile_shares_{CURRENT_YEAR}"]


def test_residuals_exist_nationally_and_are_displayed(golden):
    """At national scale every residual is non-empty, so it must be shown.

    Other/Unknown is a median 11.8% of population in large counties and ranges
    from 4.6% to 22% across counties. Far too large to omit silently — omitting
    it is what inverted the published Arlington comparison.
    """
    for dim, residual in RESIDUALS.items():
        cats = golden["national"][dim]["categories"]
        assert residual in cats, (
            f"national / {dim} does not display the residual {residual!r} — "
            "the denominator includes it, so the page must too"
        )
        assert cats[residual]["share_pct"] > 0


def test_a_geography_may_have_an_empty_residual_but_never_a_hidden_one(golden):
    """Absence must mean zero people, not a dropped category.

    Manassas Park has no decile 0 residents at all in 2023 — a small geography
    genuinely can have an empty residual. What must never happen is a residual
    with population being left out of the display while staying in the
    denominator, and test_every_share_set_sums_to_100 is what catches that.
    """
    for label, block in geographies(golden):
        for dim, residual in RESIDUALS.items():
            cats = block[dim]["categories"]
            if residual in cats:
                assert cats[residual]["share_pct"] > 0, (
                    f"{label} / {dim}: residual {residual!r} is displayed with a "
                    "zero share — it should be absent rather than shown as 0"
                )


def test_income_shares_are_over_deciles_only(golden):
    """Decile 0 is never a bar on a decile chart.

    This is the one dimension where the residual stays OUT of the denominator,
    and the reason is categorical rather than practical. Missing Age and
    Other/Unknown are members of a partition of the population — the person is
    there and the value is unknown. Deciles partition the income distribution
    into ten equal-count groups; decile 0 is not an eleventh decile, it is
    outside that universe. An eleventh bar breaks the 10% even-distribution
    reference line that makes the chart readable.

    The published explorer had this right. What it lacked is the coverage
    disclosure below.
    """
    for label, block in geographies(golden):
        for dim in INCOME_DIMENSIONS:
            cats = block[dim]["categories"]
            assert set(cats) == {str(d) for d in range(1, 11)}, (
                f"{label} / {dim}: expected deciles 1-10, got {sorted(cats)}"
            )


def test_income_exclusion_is_always_disclosed(golden):
    """The exclusion is fine. Hiding it is not.

    The old site filtered `income_decile != 0` in every income figure and said
    nothing on the page, so a reader had no way to know the denominator was not
    everybody — between 0.02% and 0.40% of residents nationally by year, and up
    to 3.49% in a single county.
    """
    for label, block in geographies(golden):
        for dim in INCOME_DIMENSIONS:
            b = block[dim]
            assert b["denominator_universe"] == "residents with an income record", (
                f"{label} / {dim}: the denominator must name its universe"
            )
            cov = b.get("coverage")
            assert cov, f"{label} / {dim}: no coverage block"
            assert cov["excluded_category"] == "0"
            assert cov["all_residents"] >= b["denominator"]
            assert cov["note"], "coverage must be explicable in words on the page"

            if cov["excluded_population"] is not None:
                assert cov["excluded_pct_of_all_residents"] > 0
                assert cov["all_residents"] > b["denominator"], (
                    f"{label} / {dim}: residents are excluded but the denominator "
                    "equals the full population"
                )


def test_income_universe_is_a_definition_not_a_category(golden):
    """Why excluding decile 0 does not break comparability across years.

    Excluding a fixed CATEGORY would change the universe between years, since
    decile 0 is absent from the 2024 source and its residents sit inside
    deciles 1-10 that year. Excluding on a stated DEFINITION does not:
    "residents with an income record" means the same thing in every year, and
    2024 is simply a year in which the source records one for everybody.
    """
    dim = f"income_decile_shares_{CURRENT_YEAR}"
    for label, block in geographies(golden):
        cov = block[dim]["coverage"]
        assert cov["excluded_category"] == "0"
        if cov["excluded_population"] is None:
            assert block[dim]["denominator"] == cov["all_residents"], (
                f"{label}: with nobody excluded the denominator is everyone"
            )
        else:
            assert cov["all_residents"] > block[dim]["denominator"]
            assert cov["excluded_pct_of_all_residents"] < 5.0, (
                f"{label}: {cov['excluded_pct_of_all_residents']}% of residents fall "
                "outside the decile universe — too many to treat as coverage"
            )


def test_decile_zero_people_were_reclassified_not_dropped(golden):
    """The evidence behind the convention, pinned as a test.

    ageracesex and raceincome tabulate one population two ways. If 2024 had
    dropped the decile 0 population rather than reclassifying it, this gap
    would have opened by ~371,000. It stayed at its historical scale, which is
    what makes including decile 0 the universe-preserving choice.
    """
    ta = golden["national"]["tabulation_agreement"]

    # 0.05% of ~324M is ~162,000 people — comfortably above the historical
    # spread (max 0.0157%) and comfortably below the ~371,000 that vanishing
    # decile 0 would represent. A deletion cannot hide inside this band.
    LIMIT_PCT = 0.05
    for year, pct in ta["gap_pct_by_year"].items():
        assert abs(pct) < LIMIT_PCT, (
            f"{year}: the two tabulations diverge by {pct}% "
            f"({ta['gap_by_year'][year]:,.0f} people), over the {LIMIT_PCT}% bound. "
            "One of them is losing people — the income convention assumes neither "
            "does, so it needs rereading before this bound is widened."
        )
    assert ta["max_abs_gap_pct"] < LIMIT_PCT


# --------------------------------------------------------------------------
# The vanishing category
# --------------------------------------------------------------------------

def test_decile_zero_is_missing_from_exactly_one_year(golden):
    """2024 is a one-year gap, not a change of regime.

    This is the assertion the convention rests on. Decile 0 exists in every
    year from 2000 through 2025 except 2024 — bracketed on both sides by years
    that have it. An isolated gap in a single vintage does not justify dropping
    a category from 25 other years.

    If a later vintage widens the gap, or fills it, the income convention needs
    rereading before this test is edited to match.
    """
    dz = golden["national"]["decile_zero"]
    assert dz["absent_in_years"] == [2024], (
        f"decile 0 absence changed: expected [2024], got {dz['absent_in_years']}"
    )
    assert dz["isolated_gaps"] == [2024], (
        f"expected 2024 to be an isolated gap, got {dz['isolated_gaps']}. "
        "A gap that is no longer isolated is a regime change, and the decision "
        "to keep decile 0 in the denominator has to be re-argued."
    )
    covered_lo, covered_hi = dz["years_covered"]
    expected = set(range(covered_lo, covered_hi + 1)) - {2024}
    assert set(dz["present_in_years"]) == expected, (
        f"decile 0 should be present in every covered year except 2024; "
        f"missing {sorted(expected - set(dz['present_in_years']))}"
    )


def test_decile_zero_returned_in_the_year_after_the_gap(golden):
    """The evidence that 2024 is an anomaly rather than a deprecation.

    When this fixture was first written only 2000-2024 was built, so the
    category looked permanently dropped and the convention was very nearly
    written the other way. 2025 is what settled it.
    """
    pct = golden["national"]["decile_zero"]["national_pct_by_year"]
    assert pct.get("2023") is not None and pct["2023"] > 0
    assert pct.get("2024") is None
    assert pct.get("2025") is not None and pct["2025"] > 0, (
        "decile 0 no longer returns after the 2024 gap — reread the convention"
    )


def test_decile_zero_is_small_but_not_negligible(golden):
    """It never exceeds half a percent, and it moves by 25x across years.

    0.015% in 2008 to 0.403% in 2014. Small enough that keeping it in the
    denominator costs nothing; volatile and concentrated enough — up to 3.49%
    in a single county — that dropping it would be visible in exactly the
    small, low-income counties where a spurious jump misleads most.
    """
    pct = [v for v in golden["national"]["decile_zero"]["national_pct_by_year"].values()
           if v is not None]
    assert max(pct) < 0.5, f"decile 0 reached {max(pct)}% — too large to exclude quietly"
    assert min(pct) > 0


# --------------------------------------------------------------------------
# Growth arithmetic
# --------------------------------------------------------------------------

def test_cagr_matches_its_own_series(golden):
    """Recompute every pinned CAGR from the pinned series.

    The published pipeline used three different growth conventions in one
    script. One raised to 1/GROWTH_YEARS over a span of GROWTH_YEARS+1 years —
    exponent and span disagreeing by one. This catches that class directly:
    the exponent is the number of intervals, never the number of observations.
    """
    span = CURRENT_YEAR - BASE_YEAR
    for label, block in geographies(golden):
        series = block["population"]["total_by_year"]
        start, end = series[str(BASE_YEAR)], series[str(CURRENT_YEAR)]
        expected = (end / start) ** (1 / span) - 1
        actual = block["population"]["cagr_2000_2025"]
        assert actual == pytest.approx(expected, abs=1e-6), (
            f"{label}: pinned CAGR {actual} does not match "
            f"({end}/{start})^(1/{span})-1 = {expected}"
        )


def test_growth_series_never_starts_at_1999(golden):
    """1999 is excluded from the source registry and must not appear.

    Its administrative completeness is ~70% against 85-96% for every later
    year. The old pipeline's income CAGRs used first(year), which was 1999,
    across a discontinuity where one county's White count jumped 16.8%.
    """
    for label, block in geographies(golden):
        years = {int(y) for y in block["population"]["total_by_year"]}
        assert 1999 not in years, f"{label}: 1999 must not appear in any series"
        assert min(years) == BASE_YEAR


# --------------------------------------------------------------------------
# Vintages
# --------------------------------------------------------------------------

def test_headline_year_follows_the_declared_policy(golden):
    """The vintage choice is deliberate, recorded, and matches the registry.

    The policy is `latest`: the newest year available leads, preliminary or
    not. That is defensible for a page whose job is to describe a place now —
    but only if it is written down. The old site reached the same year by
    accident, with END_YEAR pointing at a preliminary vintage and nothing on
    the page saying so.
    """
    prov = golden["provenance"]
    assert prov["headline_policy"] == "latest"
    assert prov["headline_year"] == CURRENT_YEAR

    years = set()
    for _, block in geographies(golden):
        years |= {int(y) for y in block["population"]["total_by_year"]}
    assert max(years) == CURRENT_YEAR, (
        f"headline {CURRENT_YEAR} is not the newest pinned year ({max(years)}) — "
        "`latest` means latest"
    )
    assert prov.get("preliminary_years"), (
        "which vintages upstream calls preliminary must stay recorded even when "
        "the policy is to use them"
    )


def test_upstream_version_is_recorded(golden):
    """A number is only as pinned as the build that produced it.

    gridded-eif's MAJOR bump to 1.0.0 rebuilt every partition and moved these
    figures. That is supposed to fail this fixture — but only legibly, which
    needs the upstream version written down beside the numbers.
    """
    prov = golden["provenance"]
    for key in ("upstream_pipeline_version", "upstream_registry_version",
                "upstream_derived_version", "catalog_url"):
        assert prov.get(key), f"provenance is missing {key}"
    assert prov["derived_root"].startswith("http"), (
        "the fixture should be built from the published catalog, not a local "
        "build tree, so it reproduces on any machine"
    )


# --------------------------------------------------------------------------
# The measure decision
# --------------------------------------------------------------------------

def test_measure_choice_is_recorded_as_a_deliberate_deviation(golden):
    """The choice must travel with its reasoning.

    The old pipeline made this same choice and left no trace that a choice had
    been made, which is the actual failure — not the choice itself.
    """
    m = golden["conventions"]["measure"]
    assert m["deviation_from_source_guide"] is True
    assert "600,000" in m["why"] or "600k" in m["why"]
    assert len(m["why"]) > 200, "the reasoning must be written down, not gestured at"


def test_measures_agree_closely_in_large_geographies(golden):
    """Evidence for not switching estimator at the 600k threshold.

    Above the threshold the two measures are within a fraction of a percent, so
    switching buys almost nothing while introducing a step change into a
    25-year trend at exactly the geographies with the most readers.
    """
    la = golden["counties"]["06037"]["population"]["measure_comparison_2025"]
    assert abs(la["pct_difference"]) < 0.1, (
        f"Los Angeles measure gap is {la['pct_difference']}% — if this grows, the "
        "decision to publish post-processed everywhere needs revisiting"
    )
    national = golden["national"]["population"]["measure_comparison_2025"]
    assert abs(national["pct_difference"]) < 0.1


def test_both_measures_are_pinned_not_just_the_published_one(golden):
    """Raw ships alongside post-processed; it drives the uncertainty band."""
    for label, block in geographies(golden):
        mc = block["population"]["measure_comparison_2025"]
        assert mc["raw"] > 0 and mc["postprocessed"] > 0, label


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_every_convention_states_its_reasoning(golden):
    """A convention without a 'why' is how the old pipeline got where it is."""
    for name, conv in golden["conventions"].items():
        assert conv.get("rule"), f"convention {name!r} has no rule"
        assert len(conv.get("why", "")) > 80, (
            f"convention {name!r} has no substantive reasoning attached"
        )


def test_fixture_geographies_stress_distinct_cases(golden):
    """Each pinned county must say why it earns a slot."""
    assert set(golden["counties"]) == {"06037", "51003", "51685"}
    for geo_id, block in golden["counties"].items():
        assert len(block["why_this_one"]) > 80, f"{geo_id} has no stated purpose"


def test_small_county_carries_a_large_residual(golden):
    """Manassas Park's job in this fixture is to have an awkward residual.

    If a future vintage shrinks it, the fixture stops testing what it was
    chosen to test and a different geography should take its place.
    """
    mp = golden["counties"]["51685"]["race_shares_2025"]["categories"]
    assert mp["Other/Unknown"]["share_pct"] > 15, (
        "Manassas Park no longer has an unusually large race residual — pick a "
        "different small geography for the fixture"
    )
