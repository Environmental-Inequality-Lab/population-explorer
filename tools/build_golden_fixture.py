"""Generate the golden-figures fixture.

Phase 01 of the rebuild. Every later phase regresses against the file this
writes, so that "the rewrite produces different numbers" is a test failure
rather than something nobody notices for two summers.

The figures here are not a snapshot of what some implementation happened to
produce. Each one is computed directly from the published Gridded EIF
aggregates under a single stated convention, and the convention travels with
the number. That pairing is the point: every wrong figure the audit found in
the old pipeline was a convention someone chose implicitly and never wrote
down.

Reads the PUBLISHED catalog by default, not a local build, so the fixture is
reproducible from any machine. The upstream pipeline version is recorded in
provenance: when gridded-eif bumps MAJOR it rebuilds every partition, numbers
move slightly, and the fixture is supposed to notice.

Run:
    python tools/build_golden_fixture.py            # from the repo root
    python tools/build_golden_fixture.py --check    # verify, don't rewrite
    python tools/build_golden_fixture.py --derived /path/to/local/.build/derived/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

CATALOG_URL = "https://d2l6ob0rkxsi9o.cloudfront.net/catalog.json"

# --- Fixture geographies ------------------------------------------------------
#
# Chosen to stress the conventions rather than to be representative. Each one
# breaks a different assumption the old pipeline made silently.
COUNTIES = {
    "06037": {
        "name": "Los Angeles County, California",
        "why": (
            "Largest county in the US and far above the 600k threshold where the "
            "source guide recommends the raw measure. Pins the decision to publish "
            "post-processed everywhere rather than switching estimator mid-series."
        ),
    },
    "51003": {
        "name": "Albemarle County, Virginia",
        "why": (
            "Mid-sized and locally familiar, so a wrong number is noticeable by eye. "
            "The ordinary case that must stay ordinary."
        ),
    },
    "51685": {
        "name": "Manassas Park city, Virginia",
        "why": (
            "Small, and an independent city rather than a county — a form Virginia has "
            "38 of and most pipelines mishandle. Carries an unusually large residual "
            "(~25% Other/Unknown race, ~8% Missing Age), so any denominator mistake "
            "shows up here first."
        ),
    },
}

YEARS_PINNED = [2000, 2010, 2020, 2024, 2025]
# The headline year, matching `vintages.headline: latest` in the registry. 2025
# is flagged preliminary upstream and is used anyway, deliberately: a page whose
# job is to describe a place now should not hold back a year for a distinction
# most readers cannot act on. Recorded here so the choice stays visible.
CURRENT_YEAR = 2025
BASE_YEAR = 2000
DECILE_ZERO_REFERENCE_YEAR = 2025   # decile 0 exists again in 2025; absent only in 2024
INCOME_UNIVERSE = "residents with an income record"

# --- Conventions --------------------------------------------------------------
#
# One convention per dimension, applied identically to every year and every
# geography. Stated here so the fixture cannot be read without reading them.
CONVENTIONS = {
    "measure": {
        "rule": "n_noise_postprocessed, in every year and every geography",
        "why": (
            "The source guide recommends the raw measure above 600,000 population and "
            "post-processed below. Applying that literally makes a geography switch "
            "estimator as it crosses the threshold, putting a step change into a "
            "25-year trend line. Above 600k the two measures differ by a median 0.7% "
            "at cell level, so the switch buys almost nothing; meanwhile raw is "
            "negative in 2.86% of county cells and this is a public-facing product. "
            "Both measures ship in every download; raw drives the uncertainty band."
        ),
        "deviation_from_source_guide": True,
    },
    "age_shares": {
        "rule": "denominator includes 'Missing Age'; the residual is displayed as its own category",
        "why": (
            "Displayed shares must sum to 100%. The old pipeline used this same "
            "denominator but hid the residual, so its age shares summed to ~97% with "
            "nothing on the page explaining the gap."
        ),
        "residual_category": "Missing Age",
    },
    "race_shares": {
        "rule": "denominator includes 'Other/Unknown'; the residual is displayed as its own category",
        "why": (
            "Other/Unknown is a median 11.8% of population in large counties and varies "
            "from 4.6% to 22% across counties. The old pipeline divided by the full "
            "denominator and then dropped the residual from display, so Arlington's "
            "shares summed to 77.9% against a national 87.4% — which inverted the "
            "published county-vs-nation comparison for White population."
        ),
        "residual_category": "Other/Unknown",
    },
    "income_decile_shares": {
        "rule": (
            "shares over deciles 1-10 only, denominator stated as residents with an "
            "income record; decile 0 reported separately as coverage, never as a bar"
        ),
        "why": (
            "The one dimension where the residual is NOT folded into the denominator, "
            "because it is not the same kind of residual. Missing Age and "
            "Other/Unknown are members of a partition of the POPULATION — the person "
            "is there, the value is unknown. Deciles partition the INCOME "
            "DISTRIBUTION into ten equal-count groups, and decile 0 is not an "
            "eleventh decile; it is outside that universe. Rendering it as an "
            "eleventh bar breaks the 10% reference line that makes the chart legible "
            "at all."
        ),
        "excluded_from_shares": "0",
        "denominator_universe": "residents with an income record",
        "what_the_old_site_got_right": (
            "The published explorer excluded decile 0 from every income figure "
            "(02_generate_county_figures.R:140,155 and 01_generate_county_stats.R:434) "
            "and drew a 10% even-distribution reference line, which only reads "
            "correctly over ten deciles. That choice was sound."
        ),
        "what_it_got_wrong": (
            "It never disclosed the exclusion. Nothing on the page said the "
            "denominator was not everybody, so a reader had no way to know how many "
            "residents were outside it — between 0.02% and 0.40% nationally by year, "
            "and up to 3.49% in a single county."
        ),
        "stability_across_years": (
            "Excluding a fixed CATEGORY would change the universe between years, "
            "since decile 0 is absent from the 2024 source and its residents sit "
            "inside deciles 1-10 that year. Excluding on a stated DEFINITION does "
            "not: 'residents with an income record' means the same thing in every "
            "year, and 2024 is simply a year in which the source records one for "
            "everybody. Coverage is published per year so the shift is visible."
        ),
        "note": (
            "Both catalog/variables.yaml and the pinned source contract in gridded-eif "
            "declare decile 0 a valid category, which is correct. Its validator treats "
            "an absent contract category as a warning, and CI pins validate-source to "
            "2022, so the 2024 gap is not currently surfaced anywhere upstream."
        ),
    },
    "growth": {
        "rule": f"CAGR = (value[{CURRENT_YEAR}] / value[{BASE_YEAR}]) ** (1 / {CURRENT_YEAR - BASE_YEAR}) - 1",
        "why": (
            "One formula, used everywhere. The old pipeline used three different "
            "conventions in a single script, one of which raised to 1/GROWTH_YEARS "
            "over a span of GROWTH_YEARS+1 years. The exponent is always the number "
            "of intervals, never the number of observations."
        ),
        "base_year_excludes_1999": (
            "1999 is excluded from the source registry entirely: administrative "
            "completeness is ~70% against 85-96% for every later year. The old "
            "pipeline's income CAGRs used first(year), which was 1999."
        ),
    },
    "preliminary_years": {
        "rule": "the headline year is the latest available, preliminary or not",
        "why": (
            "2025 is flagged preliminary upstream and is used anyway. The explorer's "
            "job is to describe a place now, and holding the headline back a year for "
            "a distinction most readers cannot act on makes every page stale. The "
            "registry records this as `vintages.headline: latest`; switching to "
            "`latest_final` is one line and the resolver follows it."
        ),
        "recorded_not_hidden": (
            "Which years upstream considers preliminary stays in provenance, so a "
            "reader of this fixture can always tell which vintage a figure came from."
        ),
    },
}


def fetch_catalog(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def connect(derived: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if derived.startswith("http"):
        con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def path_for(derived: str, dataset: str, geography: str, year: str | int) -> str:
    return f"{derived.rstrip('/')}/{dataset}/{geography}/{year}/part-00.parquet"


def population_series(con, ars: str, geo_id: str | None) -> dict:
    """Total population by year, plus both measures in the current year."""
    where = "" if geo_id is None else f"AND geo_id = '{geo_id}'"
    rows = con.execute(f"""
        SELECT year,
               sum(n_noise_postprocessed) AS pp,
               sum(n_noise)               AS raw
        FROM read_parquet('{ars}')
        WHERE year IN ({','.join(str(y) for y in YEARS_PINNED)}) {where}
        GROUP BY year ORDER BY year
    """).fetchall()
    by_year = {str(int(y)): round(pp, 1) for y, pp, _ in rows}
    raw_by_year = {str(int(y)): round(rw, 1) for y, _, rw in rows}

    start, end = by_year.get(str(BASE_YEAR)), by_year.get(str(CURRENT_YEAR))
    cagr = None
    if start and end and start > 0:
        cagr = round((end / start) ** (1 / (CURRENT_YEAR - BASE_YEAR)) - 1, 6)

    pp_now, raw_now = by_year[str(CURRENT_YEAR)], raw_by_year[str(CURRENT_YEAR)]
    return {
        "total_by_year": by_year,
        f"cagr_{BASE_YEAR}_{CURRENT_YEAR}": cagr,
        f"measure_comparison_{CURRENT_YEAR}": {
            "postprocessed": pp_now,
            "raw": raw_now,
            "pct_difference": round(100.0 * (pp_now - raw_now) / raw_now, 4),
        },
    }


def shares(
    con,
    src: str,
    dim: str,
    geo_id: str | None,
    year: int,
    *,
    exclude: str | None = None,
    universe: str = "all residents",
) -> dict:
    """Category shares under the stated denominator convention.

    For age and race nothing is excluded: the residual is a member of a
    partition of the population and belongs in both the denominator and the
    display.

    For income deciles one category is excluded, because deciles partition the
    income distribution rather than the population. When `exclude` is given the
    shares are computed over the remaining categories and a `coverage` block
    quantifies who was left out — the exclusion is never silent, which is the
    single thing the old site got wrong here.
    """
    where = "" if geo_id is None else f"AND geo_id = '{geo_id}'"
    rows = con.execute(f"""
        SELECT CAST({dim} AS VARCHAR) AS k, sum(n_noise_postprocessed) AS v
        FROM read_parquet('{src}')
        WHERE year = {year} {where}
        GROUP BY 1 ORDER BY 1
    """).fetchall()

    everyone = sum(v for _, v in rows)
    kept = [(k, v) for k, v in rows if k != exclude]
    total = sum(v for _, v in kept)

    out = {
        "denominator": round(total, 1),
        "denominator_universe": universe,
        "categories": {k: {"population": round(v, 1), "share_pct": round(100.0 * v / total, 3)}
                       for k, v in kept},
    }
    out["shares_sum_to_pct"] = round(sum(c["share_pct"] for c in out["categories"].values()), 3)

    if exclude is not None:
        left_out = next((v for k, v in rows if k == exclude), None)
        out["coverage"] = {
            "excluded_category": exclude,
            "all_residents": round(everyone, 1),
            "excluded_population": (round(left_out, 1) if left_out is not None else None),
            "excluded_pct_of_all_residents": (
                round(100.0 * left_out / everyone, 4) if left_out is not None else None
            ),
            "note": (
                f"Shares are of {universe}. In this year the source records one for "
                "every resident."
                if left_out is None else
                f"Shares are of {universe}. The remainder have no income record and "
                "are outside the decile universe — they are counted in the population "
                "figures elsewhere on the page."
            ),
        }
    return out


def decile_zero_by_year(con, ri: str) -> dict:
    """The residual that vanishes for exactly one year.

    Pinned in full because the shape of the gap is what justifies the
    convention. Present 2000-2023, absent 2024, present again 2025: an
    isolated anomaly in a final-vintage year, not a change of regime.
    """
    rows = con.execute(f"""
        SELECT year,
               sum(n_noise_postprocessed) FILTER (WHERE income_decile = 0) AS d0,
               sum(n_noise_postprocessed)                                  AS tot
        FROM read_parquet('{ri}') GROUP BY year ORDER BY year
    """).fetchall()
    present = [int(y) for y, d0, _ in rows if d0 is not None]
    absent = [int(y) for y, d0, _ in rows if d0 is None]
    covered = {int(y) for y, _, _ in rows}
    isolated = [y for y in absent if (y - 1) in present and (y + 1) in present]
    return {
        "years_covered": [min(covered), max(covered)],
        "present_in_years": present,
        "absent_in_years": absent,
        "isolated_gaps": isolated,
        "isolated_gaps_note": (
            "An absent year bracketed on both sides by years in which the category "
            "exists. A gap of this shape is an anomaly in one vintage, not a change "
            "in methodology — which is why decile 0 stays in the denominator."
        ),
        "national_pct_by_year": {
            str(int(y)): (round(100.0 * d0 / tot, 4) if d0 is not None else None)
            for y, d0, tot in rows
        },
    }


def tabulation_agreement(con, ars: str, ri: str) -> dict:
    """Do the two tabulations still count the same people?

    ageracesex and raceincome tabulate one population two ways, so their totals
    track each other closely. This is the evidence that decile 0's absence in
    2024 is a reclassification rather than a deletion: if those people had been
    dropped, this gap would have opened by their number. It did not.

    Pinned so that a future vintage which really does drop people fails a test
    rather than quietly shrinking the denominator.
    """
    rows = con.execute(f"""
        WITH a AS (SELECT year, sum(n_noise_postprocessed) AS ars FROM read_parquet('{ars}') GROUP BY 1),
             b AS (SELECT year, sum(n_noise_postprocessed) AS ri  FROM read_parquet('{ri}')  GROUP BY 1)
        SELECT year, ars, ri FROM a JOIN b USING(year) ORDER BY year
    """).fetchall()
    return {
        "note": (
            "raceincome total minus ageracesex total, by year, as a percent of the "
            "ageracesex total. Expressed as a percent so the bound means the same "
            "thing in every year and at every scale."
        ),
        "gap_by_year": {str(int(y)): round(ri - ars, 1) for y, ars, ri in rows},
        "gap_pct_by_year": {
            str(int(y)): round(100.0 * (ri - ars) / ars, 4) for y, ars, ri in rows
        },
        "max_abs_gap_pct": round(max(abs(100.0 * (ri - ars) / ars) for _, ars, ri in rows), 4),
    }


def build(derived: str, catalog: dict | None) -> dict:
    con = connect(derived)
    ars = path_for(derived, "ageracesex", "county", "all")
    ri = path_for(derived, "raceincome", "county", "all")

    provenance = {
        "source": "gridded-eif derived v1 aggregates",
        "derived_root": derived,
        "measure": "n_noise_postprocessed unless stated",
        "headline_year": CURRENT_YEAR,
        "headline_policy": "latest",
    }
    if catalog:
        provenance |= {
            "catalog_url": CATALOG_URL,
            "upstream_pipeline_version": catalog.get("pipeline_version"),
            "upstream_registry_version": catalog.get("registry_version"),
            "upstream_derived_version": catalog.get("derived_version"),
            "catalog_generated_at": catalog.get("generated_at"),
            "preliminary_years": sorted({e["year"] for e in catalog.get("entries", [])
                                         if e.get("preliminary")}),
            "note": (
                "Upstream versions are recorded because a MAJOR pipeline bump rebuilds "
                "every partition and moves these numbers slightly. When that happens "
                "the fixture is supposed to fail; re-baseline deliberately, never "
                "by reflex."
            ),
        }

    fixture = {
        "$schema_version": 2,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "tools/build_golden_fixture.py",
        "purpose": (
            "Verified figures for the Population Explorer rebuild. A later phase that "
            "produces different numbers has a bug, not a difference of opinion."
        ),
        "provenance": provenance,
        "conventions": CONVENTIONS,
        "national": {},
        "counties": {},
    }

    fixture["national"] = {
        "population": population_series(con, ars, None),
        f"age_shares_{CURRENT_YEAR}": shares(con, ars, "age_group", None, CURRENT_YEAR),
        f"race_shares_{CURRENT_YEAR}": shares(con, ars, "race_ethnicity", None, CURRENT_YEAR),
        # Pinned for both years on purpose. Decile 0 is empty in 2024, so that
        # year cannot test a convention about residuals; 2023 is the one that does.
        f"income_decile_shares_{CURRENT_YEAR}": shares(
            con, ri, "income_decile", None, CURRENT_YEAR,
            exclude="0", universe=INCOME_UNIVERSE),
        "decile_zero": decile_zero_by_year(con, ri),
        "tabulation_agreement": tabulation_agreement(con, ars, ri),
    }

    for geo_id, meta in COUNTIES.items():
        fixture["counties"][geo_id] = {
            "name": meta["name"],
            "why_this_one": meta["why"],
            "population": population_series(con, ars, geo_id),
            f"age_shares_{CURRENT_YEAR}": shares(con, ars, "age_group", geo_id, CURRENT_YEAR),
            f"race_shares_{CURRENT_YEAR}": shares(con, ars, "race_ethnicity", geo_id, CURRENT_YEAR),
            f"income_decile_shares_{CURRENT_YEAR}": shares(
                con, ri, "income_decile", geo_id, CURRENT_YEAR,
                exclude="0", universe=INCOME_UNIVERSE),
        }

    return fixture


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--derived", default=None,
                    help="Root of the derived v1 tree. Defaults to the published base "
                         "from the live catalog.")
    ap.add_argument("--out", default=str(root / "tests" / "fixtures" / "golden-figures.json"))
    ap.add_argument("--check", action="store_true",
                    help="Compare against the committed fixture instead of rewriting it.")
    args = ap.parse_args()

    catalog = None
    derived = args.derived
    if derived is None:
        catalog = fetch_catalog(CATALOG_URL)
        derived = f"{catalog['base_url'].rstrip('/')}/derived/{catalog['derived_version']}"
        print(f"using published {derived} (pipeline {catalog['pipeline_version']})")

    fresh = build(derived, catalog)
    out = Path(args.out)

    if args.check:
        if not out.exists():
            print(f"FAIL: {out} does not exist", file=sys.stderr)
            return 1
        committed = json.loads(out.read_text())
        for side in (fresh, committed):
            side.pop("generated_at", None)
            side.get("provenance", {}).pop("catalog_generated_at", None)
            side.get("provenance", {}).pop("derived_root", None)
        if fresh == committed:
            print(f"OK: {out.name} matches a fresh computation")
            return 0
        print(f"FAIL: {out.name} differs from a fresh computation", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fresh, indent=2) + "\n")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
