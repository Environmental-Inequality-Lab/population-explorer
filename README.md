# Population Data Explorer

Demographic and income profiles for every U.S. state, county and metro area —
4,120 places — built on the published
[Gridded EIF](https://www.census.gov/data/experimental-data-products/gridded-eif.html)
aggregates rather than a second copy of the pipeline that produces them.

An [Environmental Inequality Lab](https://environmental-inequality-lab.org) project.

**Live:** https://environmental-inequality-lab.github.io/population-explorer/
(forwards to the CloudFront distribution that serves the site — see
[Deploying](#deploying)). Version **2.0.0**, replacing the earlier explorer built
across `population-explorer-data`, `-processing` and `-quarto`.

## What a page holds

Three tabs, declared in [`catalog/explorer.yaml`](catalog/explorer.yaml) so moving
a section between tabs is a config edit:

| Tab | Text | Figures |
|---|---|---|
| **Population** | Size; density two ways (over all land, and over only the land where people live); growth since 2015 against the state and nation; one paragraph per age group | Population trend 2000–2025 · population density map · age shares over time · age vs. U.S. · age table |
| **Race & Ethnicity** | One bullet per group against the nation; the largest gap; the "Other or unknown" share | Shares over time · shares vs. U.S. · table · each group's share of every square kilometer (small-multiple maps) |
| **Income** | What AGI is; what a national decile is; the share of residents in the upper half of the national distribution, and in the top decile, against the nation | Decile distribution · table · distribution by race vs. U.S. · where the top and bottom deciles live (maps) |

Counties and metros carry ten figures. States carry seven: no maps, because 53,000
to 208,000 cells per state is the wrong tool at a zoom where a one-kilometre cell is
sub-pixel. Every figure and table is introduced by one sentence ("Figure 5 shows…",
"The table below gives…"), and figure numbers in that prose are resolved at
assembly, so they cannot drift as figures come and go.

Each tab ends on the same maroon rule the tab strip opens with, a *Back to top*
link and a *Next* button. **Save as PDF** prints the whole profile — all three
tabs, each from a new page, no navigation, no figure split across pages.

Every page's footer carries a citation (with the reader's own access date) and a
version line: explorer version, Gridded EIF data and pipeline versions, and the
date the data were updated.

## Running

The pipeline needs Python 3.11+ with `duckdb`, `pyarrow` and `pyyaml`:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest -q
```

**Build** writes outside the repo — to `~/Library/Caches/population-explorer/_site`
unless `EXPLORER_SITE_DIR` says otherwise. The build is 3 GB across ~4,600 files
and every page is regenerated on every build; it belongs neither in git nor in a
Dropbox folder, which once rolled a finished build back to an older synced copy.

```bash
.venv/bin/python -m pipeline.cli build --slice                         # Albemarle, Virginia, Charlottesville metro
.venv/bin/python -m pipeline.cli build --geography county --id 48381   # one place
.venv/bin/python -m pipeline.cli build --all                           # everything, in parallel
```

A partial build never rewrites the home page, the full listing or the search
index: those describe the whole site, so only `--all` may write them.

**Look at it** with the bundled server, which sends `no-store` (so a rebuild is
never hidden behind a cached page) and resolves `/county/51003/` the way
CloudFront does:

```bash
.venv/bin/python tools/serve.py 8791
```

**Full national build:** 4,120 pages, 3.0 GB on disk, about 0.45 GB compressed.
About 14 minutes on one worker; a few minutes across cores. The first build
mirrors the published Parquet into `.cache/` (a few hundred MB), keyed on the
upstream URL so a new data version fetches new files. Downloads retry, and a
truncated file is never used.

**The front-page graphic** — the 1 km grid around Charlottesville, its edge
drawn by where people live — is generated once and committed, not rebuilt:

```bash
.venv/bin/pip install -e ".[hero]" && .venv/bin/python tools/build_hero.py
```

## Deploying

The site is static HTML on **S3 behind CloudFront**. At 3 GB it is three times
GitHub Pages' 1 GB cap. Setup, costs and checks are in [`DEPLOY.md`](DEPLOY.md).

```bash
python -m pipeline.publish --site ~/Library/Caches/population-explorer/_site \
    --bucket eil-population-explorer --distribution-id E1XXXXXXXXXXXX --dry-run
```

`publish` prints the folder it is reading first, compares each file's MD5 with
its S3 ETag and uploads only what changed, then invalidates what it replaced. It
also deletes objects the build no longer contains — and so refuses to run
against anything but a complete `--all` build, since publishing after `--slice`
would otherwise take down every other page.

Pushes to `main` that touch `pipeline/`, `catalog/` or `assets/` run
[the workflow](.github/workflows/deploy.yml): tests, full build, publish, using
an OIDC role rather than stored keys.

**The cited address.** The site is cited at its github.io address, which this
repo's `gh-pages` branch serves as a redirect site: a forwarding page at every
path, generated by `tools/build_redirects.py`. Moving to a custom domain later is
that script with a new `--target`, pushed again — cited links keep working.

## The registry

[`catalog/explorer.yaml`](catalog/explorer.yaml) declares what the site shows. It
does **not** describe the data — gridded-eif's published `catalog.json` does that,
and is fetched at build time, never vendored. A committed copy would go stale the
moment upstream publishes a year.

**Three geographies get a page**: county, state and metro (CBSAs, so micropolitan
areas too, labelled as such). Upstream also computes PUMA, commuting zone and ZCTA,
which are deliberately not offered — they are not units a resident recognises.

**Income is a family, not a section.** `income_composition` ships; `income_soi`
(measured income from IRS SOI) is declared, disabled and unimplemented, and
`test_enabling_income_soi_needs_no_code_change` holds enabling it to a config edit.

## Conventions

One per dimension, applied identically to every year and place. The registry
holds the rules, `tests/fixtures/golden-figures.json` pins verified numbers, and
`test_registry_conventions_match_the_pinned_ones` fails if they drift.

- **Measure** — `n_noise_postprocessed` everywhere, never switched mid-series. A
  documented deviation from the source guide's 600,000-person threshold, because
  switching estimator as a place crosses it puts a step change into a 25-year
  trend.
- **Age and race shares** — residual-inclusive denominators, so shares sum to
  100%. "Other or unknown" is shown as a group in its own right: it is 10–24% of
  residents depending on the place, and the old explorer dropping it is what
  inverted one of its race comparisons.
- **Every group is drawn, however small.** A reader who finds a group in the
  table and not in the chart concludes it is absent, which is the one thing the
  data does not say.
- **Income deciles** — shares are of *residents with an income record*, and the
  page says so. Decile 0 is not an eleventh decile but a resident outside the
  income universe, so it is excluded from the shares rather than drawn as a bar.
- **Vintage** — `vintages.headline: latest`: 2025, the newest year, leads
  throughout. It comes from the source's preliminary "real-time" file, a recorded
  choice rather than an accident.
- **Growth and change are stated from 2015** (`render.GROWTH_BASE`); figures still
  plot 2000–2025. The administrative records behind the source cover more of the
  population over time, with increases in 2004 and 2015 (Voorheis et al. 2026,
  *Review of Environmental Economics and Policy* 20(2)). Measured from 2000,
  Albemarle grows 59% to 2024 here against 40% in the Census Bureau's estimates;
  from 2015 the two agree within a point.
- **Growth formula** — `(end/start)^(1/intervals) - 1`, one formula everywhere.
  1999 never appears.

### Income is a distribution, never dollars

`income_composition` is structurally forbidden from emitting a dollar figure
(`must_not_emit` in the registry, enforced by test). The old site published
*"the average household AGI in Arlington was $263,433"* — the county's decile mix
multiplied by **national** decile means, so two places with the same mix reported
the same dollars whatever anyone there earned. The registry carries
`example_claims` showing what *can* be said; a page says, for example:

> "In Albemarle County, VA, 62.6% of residents are in the upper half of the
> national income distribution, higher than the national 49.9%."

### Numbers never become strings until the last moment

`figures.py` returns numbers. `narrative.py` returns numbers plus the word that
describes them. `render.py` is the first place a value becomes text. The old
pipeline emitted `"higher"` and `"1,234"` into an `.RData` blob, so there was no
number left to check. Rules in `narrative.py`, each matching a bug that reached
production:

- **Compare unrounded values.** The old code compared 21.6% rounded to 22 against
  an unrounded 21.9% and called it higher.
- **Every comparison has a tolerance**, so "about the same" is reachable, and
  whether two values are similar is decided from the numbers, never from the word
  a caller chose for it.
- **Compare like with like.** `compare_share` raises `IncomparableError` when the
  two sides declare different universes.

## Maps

Built from the published grid joined to the published crosswalk.

- **Raw cells.** Each square is one published 0.01-degree cell (about a square
  kilometre) drawn from its own count — not grouped, smoothed or modelled. A
  quadtree grouping exists in `grid.aggregate` and is switched off
  (`map_min_block_population: 0`); setting a count brings it back.
- **Density on a log scale**, blue to red, starting at zero: a cell absent from
  the source is a true zero (user guide §7.2.2), so it is drawn at the low end
  and the map reads as a continuous surface.
- **Race maps show shares**, each group's share of a square's residents, on one
  0–100% scale for every panel, stretched at low shares where most squares fall.
  Zero has its own colour; squares with no residents are blank.
- **Small multiples share one frame and one scale**
  (`test_map_panels_share_one_frame`), so comparing panels compares the same
  ground.
- **Every main map is labelled** with towns from every state the frame touches,
  ranked by the residents around them so labels land where people are.
- **The colour key sits in its own band** above the map, never over it, and cells
  are clipped to the place's outline.
- **Paths, not elements.** One path per colour plus a lookup table for the hover,
  rather than an element per cell. Very large places (the Alaskan boroughs) are
  drawn on a coarser lattice, and the figure's note says so.

## House style

One brand, one token file. `assets/css/tokens.css` is **vendored verbatim from
gridded-eif**; `tests/test_style.py` fails if the two drift or a component uses a
literal colour. Brand colours and data colours are separate scales, so a rebrand
cannot change what a colour means in a chart. Light only, deliberately — a dark
explorer beside a light Gridded EIF site would be two products.

The chrome mirrors gridded-eif: the lab mark and two links in the header, the
same footer (credit, citation, version line, Census disclaimer), the same tab
icon. Every figure carries the EIL frame from the old explorer — FIGURE label,
title, subtitle, logo, Sources and Notes — as CSS around inline SVG rather than
62,860 composited image files. Each figure stands alone, notes included.

## Golden figures and tripwires

`tests/fixtures/golden-figures.json` pins verified figures for four geographies,
each chosen to break a different assumption: the nation; Los Angeles County (far
above the 600k measure threshold); Albemarle County (the ordinary case, familiar
enough that a wrong number shows by eye); and Manassas Park city, VA (small,
independent, with a ~25% race residual). Every figure travels with the convention
that produced it.

```bash
.venv/bin/python tools/build_golden_fixture.py --check
```

The fixture records the upstream pipeline version, so a MAJOR bump in gridded-eif
*should* fail `--check`. Re-baseline deliberately, after looking at what moved.

The tests are tripwires, not coverage: each fails on a specific bug.

| Bug | Caught by |
|---|---|
| Race residual dropped from display but kept in the denominator | `test_every_share_set_sums_to_100` |
| CAGR exponent off by one | `test_cagr_matches_its_own_series` |
| 1999 leaking into a series | `test_growth_series_never_starts_at_1999` |
| Headline year chosen by something other than the registry | `test_headline_year_follows_the_declared_vintage_policy` |
| Measure choice stripped of its reasoning | `test_measure_choice_is_recorded_as_a_deliberate_deviation` |
| Income relabelled as measured income, or stating dollars | `test_income_composition_is_never_labelled_as_measured_income`, `test_no_page_states_a_dollar_figure` |
| Income denominator not disclosed | `test_income_section_states_its_universe` |
| Decile 0 drawn as an eleventh bar | `test_income_shares_are_over_deciles_only` |
| Growth stated from 2000 again | `test_growth_is_stated_from_2015_but_plotted_from_2000` |
| A small group dropped from a figure | `test_every_group_is_drawn_however_small` |
| Map cells merged by rounding half-step coordinates | `test_every_cell_gets_its_own_index` |
| A map key drawn over the map | `test_a_map_key_never_sits_on_the_map` |
| "at about the same rate than the state" | `test_a_similar_comparison_reads_as_similar` |
| Publishing a partial build and pruning the rest | `test_a_partial_build_cannot_prune_the_published_site` |
| Upstream no longer publishing a needed file | `test_missing_upstream_data_fails_at_build_time` |
| Upstream renaming a dimension | `test_snapshot_still_matches_the_live_catalog_structurally` |

## One thing to pass upstream

Income decile 0 is absent from the 2024 Gridded EIF source and present in every
other year. Verified against the raw Census Parquet: same schema, no NULL
`income_decile`, ten distinct values in 2024 where 2023 and 2025 carry eleven.
2024 is a final vintage, so it is worth a look — either Census intends it, or the
file wants re-fetching. gridded-eif's validator treats an absent contract category
as a warning and CI pins `validate-source` to 2022, so nothing surfaces it there.
It does not affect these pages: decile 0 is outside the income shares by design.

## Contributors

Developed at the Environmental Inequality Lab. Arnav Dharmagadda, Josie Fischman
and Elizabeth Shiker contributed to the preliminary development of the county-level
analysis and the preparation of the Gridded EIF for county-level applications. Web
design and development of this version: [Grant M. Seiter](https://grantseiter.com).
