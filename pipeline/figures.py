"""Compute a geography's figures from the published aggregates.

Every convention declared in `catalog/explorer.yaml` is applied here, once, and
nowhere else. Two rules make that stick:

  * Nothing downstream ever sees a formatted string. This module returns
    numbers; formatting is the renderer's job. The old pipeline emitted
    `"higher"` and `"1,234"` into an .RData blob, which left no number to
    check and no test that could fail.

  * Shares are computed by one function with an explicit universe. The
    denominator is never implied by whatever happened to be filtered upstream
    of it — that is exactly how the published site came to divide race shares
    by a residual-inclusive denominator and then drop the residual from
    display.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache

import duckdb

from pipeline import config
from pipeline.config import ResolvedDataset, ResolvedGeography

MEASURE = "n_noise_postprocessed"
UNCERTAINTY = "n_noise"


CACHE = config.REPO_ROOT / ".cache"


@cache
def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


@cache
@cache
def land_km2(geography: str) -> dict[str, float]:
    """geo_id -> land area in square kilometres, from the published `_areas`.

    Upstream publishes TIGER's own `ALAND` beside `_names` and `_boundaries`,
    so this is a read rather than a derivation. It used to be a hand-rolled
    DBF parser over a sibling checkout's TIGER cache; the values are identical
    and the checkout is no longer needed.

    The URL comes from the catalog, never hardcoded: the `v1` prefix tracks
    the TIGER boundary vintage and moves when boundaries do.

    Returning {} rather than raising is deliberate — a missing land area costs
    the page one figure, not the whole build.
    """
    url = config.upstream_catalog().get("areas", {}).get(geography)
    if not url:
        return {}
    try:
        raw = config.fetch_bytes(url, as_json=True)
    except Exception:  # noqa: BLE001 — see the docstring
        return {}
    # geo_ids carry meaningful leading zeros (318 counties begin with "0"), so
    # they stay strings the whole way through. `aland_m2` excludes water, which
    # is what a density denominator wants.
    return {str(k): v["aland_m2"] / 1e6 for k, v in raw.items()}


def local(url: str) -> str:
    """Mirror a published Parquet file locally, once.

    Every query otherwise pays a round trip to CloudFront. Range requests keep
    that cheap — 65ms against 3ms local — but a national build runs tens of
    thousands of queries, and the difference is the difference between two
    minutes and an hour.

    Keyed on the published URL, which carries the derived version, so a new
    version fetches a new file rather than reusing a stale one. Delete
    `.cache/` to force a refetch.
    """
    if not url.startswith("http"):
        return url
    CACHE.mkdir(exist_ok=True)
    name = "__".join(url.rsplit("/", 4)[-4:]).replace("part-00.parquet", "") + ".parquet"
    dest = CACHE / name
    if not dest.exists():
        # Written under a name of this process's own and renamed into place:
        # parallel workers on a cold cache otherwise race to write the same
        # file, and DuckDB's write lock fails the second one (it did, on CI).
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
        _con().execute(
            f"COPY (SELECT * FROM read_parquet('{url}')) TO '{tmp}' (FORMAT parquet)"
        )
        os.replace(tmp, dest)
    return str(dest)


@cache
def names(geography: str, catalog_url: str | None = None) -> dict[str, str]:
    cat = config.upstream_catalog(catalog_url)
    return config.fetch_bytes(cat["names"][geography], as_json=True)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Series:
    """A measured quantity over time. Numbers only."""
    years: tuple[int, ...]
    values: tuple[float, ...]
    uncertainty: tuple[float, ...] = ()
    preliminary_years: tuple[int, ...] = ()

    def at(self, year: int) -> float | None:
        try:
            return self.values[self.years.index(year)]
        except ValueError:
            return None

    def cagr(self, start: int, end: int) -> float | None:
        """One growth formula. The exponent is the number of intervals."""
        a, b = self.at(start), self.at(end)
        if a is None or b is None or a <= 0 or end <= start:
            return None
        return (b / a) ** (1 / (end - start)) - 1


@dataclass(frozen=True)
class Shares:
    """Category shares with the denominator's universe stated, not implied."""
    universe: str
    denominator: float
    categories: dict[str, float]          # code -> population
    labels: dict[str, str]                # code -> display label
    order: tuple[str, ...]                # declared order, never alphabetical
    residual: str | None = None           # displayed inside the shares
    coverage: dict | None = None          # who the denominator leaves out

    def share(self, code: str) -> float | None:
        v = self.categories.get(code)
        return None if v is None or not self.denominator else 100.0 * v / self.denominator

    @property
    def total_pct(self) -> float:
        return sum(s for c in self.categories if (s := self.share(c)) is not None)


@dataclass(frozen=True)
class CrossShares:
    """Shares of one dimension computed WITHIN each level of another.

    The old explorer's income-by-race panels normalise inside each race group
    (`group_by(region, race) %>% mutate(share = total_count / sum(total_count))`),
    so every group's deciles sum to 100%. That is the point of the figure: it
    compares the SHAPE of each group's distribution, not its size.
    """
    universe: str
    outer: str                              # e.g. race_ethnicity
    inner: str                              # e.g. income_decile
    order_outer: tuple[str, ...]
    order_inner: tuple[str, ...]
    labels_outer: dict[str, str]
    labels_inner: dict[str, str]
    totals: dict[str, float]                # outer code -> denominator
    values: dict[tuple[str, str], float]    # (outer, inner) -> population

    def share(self, outer: str, inner: str) -> float | None:
        d = self.totals.get(outer)
        if not d:
            return None
        return 100.0 * self.values.get((outer, inner), 0.0) / d


@dataclass
class PlaceFigures:
    geo: ResolvedGeography
    geo_id: str
    name: str
    grid: object | None = None          # CellGrid, when this geography has one
    land_km2: float | None = None       # total land area, for conventional density
    population: Series | None = None
    shares: dict[str, Shares] = field(default_factory=dict)   # dataset id -> shares
    trends: dict[str, dict[str, Series]] = field(default_factory=dict)
    cross: dict[str, CrossShares] = field(default_factory=dict)
    comparisons: dict[str, PlaceFigures] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _where(geo_id: str | None) -> str:
    return "" if geo_id is None else f"AND geo_id = '{geo_id}'"


def population_series(ds: ResolvedDataset, geo_id: str | None) -> Series:
    rows = _con().execute(f"""
        SELECT year, sum({MEASURE}), sum({UNCERTAINTY})
        FROM read_parquet('{local(ds.url)}')
        WHERE year IS NOT NULL {_where(geo_id)}
        GROUP BY year ORDER BY year
    """).fetchall()
    rows = [r for r in rows if int(r[0]) in ds.years]
    return Series(
        years=tuple(int(r[0]) for r in rows),
        values=tuple(float(r[1]) for r in rows),
        uncertainty=tuple(float(r[2]) for r in rows),
        preliminary_years=ds.preliminary_years,
    )


def category_shares(ds: ResolvedDataset, geo_id: str | None, year: int, catalog: dict) -> Shares:
    """Apply the declared denominator convention for this dataset.

    Which categories count toward the denominator is read from the registry,
    never inferred from the query. `excluded_from_shares` leaves a category out
    and reports it as coverage; `residual_category` keeps it in and displays it.
    """
    spec = config.registry()["datasets"][ds.id]
    excluded = spec.get("excluded_from_shares")
    excluded = str(excluded) if excluded is not None else None
    universe = spec.get("denominator_universe", "all residents")

    dim = ds.dimension
    rows = _con().execute(f"""
        SELECT CAST({dim} AS VARCHAR), sum({MEASURE})
        FROM read_parquet('{local(ds.url)}')
        WHERE year = {year} {_where(geo_id)}
        GROUP BY 1
    """).fetchall()
    found = {str(k): float(v) for k, v in rows}

    # Declared order, from the upstream dimension. Never alphabetical: "19-65"
    # sorting before "Over 65" before "Under 18" is how an age chart ends up
    # reading middle, old, young.
    declared = [str(v["code"]) for v in catalog["dimensions"][dim]["values"]]
    labels = {str(v["code"]): v["label"] for v in catalog["dimensions"][dim]["values"]}

    kept_order = tuple(c for c in declared if c != excluded and c in found)
    kept = {c: found[c] for c in kept_order}
    denominator = sum(kept.values())

    coverage = None
    if excluded is not None:
        left_out = found.get(excluded)
        everyone = sum(found.values())
        coverage = {
            "excluded_category": excluded,
            "excluded_label": labels.get(excluded, excluded),
            "excluded_population": left_out,
            "all_residents": everyone,
            "excluded_pct": (100.0 * left_out / everyone) if left_out else None,
        }

    return Shares(
        universe=universe,
        denominator=denominator,
        categories=kept,
        labels=labels,
        order=kept_order,
        residual=spec.get("residual_category"),
        coverage=coverage,
    )


def cross_shares(ds: ResolvedDataset, geo_id: str | None, year: int, catalog: dict,
                 *, outer: str) -> CrossShares:
    """Cross `outer` with the dataset's own dimension, normalised within `outer`.

    The excluded category declared for this dataset is dropped from the INNER
    dimension exactly as it is in the one-dimensional case — the denominator
    convention does not change because a second dimension was added.
    """
    spec = config.registry()["datasets"][ds.id]
    excluded = spec.get("excluded_from_shares")
    excluded = str(excluded) if excluded is not None else None
    universe = spec.get("denominator_universe", "all residents")
    inner = ds.dimension

    rows = _con().execute(f"""
        SELECT CAST({outer} AS VARCHAR), CAST({inner} AS VARCHAR), sum({MEASURE})
        FROM read_parquet('{local(ds.url)}')
        WHERE year = {year} {_where(geo_id)}
        GROUP BY 1, 2
    """).fetchall()

    def declared(dim):
        vals = catalog["dimensions"][dim]["values"]
        return ([str(v["code"]) for v in vals],
                {str(v["code"]): v["label"] for v in vals})

    order_o, lab_o = declared(outer)
    order_i, lab_i = declared(inner)
    order_i = [c for c in order_i if c != excluded]

    values, totals = {}, {}
    present_o = set()
    for o, i, v in rows:
        o, i = str(o), str(i)
        if i == excluded:
            continue
        values[(o, i)] = float(v)
        totals[o] = totals.get(o, 0.0) + float(v)
        present_o.add(o)

    return CrossShares(
        universe=universe, outer=outer, inner=inner,
        order_outer=tuple(c for c in order_o if c in present_o),
        order_inner=tuple(c for c in order_i),
        labels_outer=lab_o, labels_inner=lab_i,
        totals=totals, values=values,
    )


def share_series(ds: ResolvedDataset, geo_id: str | None, catalog: dict) -> dict[str, Series]:
    """Each category's share of the denominator, year by year.

    The published age-trend figure plots shares over time with the residual in
    the denominator but not on the chart — `total` is `rowSums` over all four
    age groups including Missing Age, while only three lines are drawn. Same
    convention as everywhere else here, so the two agree.
    """
    spec = config.registry()["datasets"][ds.id]
    excluded = spec.get("excluded_from_shares")
    excluded = str(excluded) if excluded is not None else None
    dim = ds.dimension

    rows = _con().execute(f"""
        SELECT year, CAST({dim} AS VARCHAR), sum({MEASURE})
        FROM read_parquet('{local(ds.url)}')
        WHERE year IS NOT NULL {_where(geo_id)}
        GROUP BY 1, 2 ORDER BY 1
    """).fetchall()

    years = sorted({int(y) for y, _, _ in rows if int(y) in ds.years})
    by_year_cat: dict[int, dict[str, float]] = {y: {} for y in years}
    for y, k, v in rows:
        y = int(y)
        if y in by_year_cat and str(k) != excluded:
            by_year_cat[y][str(k)] = float(v)

    declared = [str(v["code"]) for v in catalog["dimensions"][dim]["values"]]
    present = [c for c in declared if any(c in by_year_cat[y] for y in years)]

    out: dict[str, Series] = {}
    for c in present:
        vals = []
        for y in years:
            tot = sum(by_year_cat[y].values())
            vals.append(100.0 * by_year_cat[y].get(c, 0.0) / tot if tot else 0.0)
        out[c] = Series(years=tuple(years), values=tuple(vals),
                        preliminary_years=ds.preliminary_years)
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def figures_for(
    geography: str,
    geo_id: str | None,
    catalog: dict,
    *,
    name: str | None = None,
    with_grid: bool = False,
) -> PlaceFigures:
    page = config.resolve_geography(geography, catalog)
    if name is None:
        name = "United States" if geo_id is None else names(page.upstream)[geo_id]

    out = PlaceFigures(geo=page, geo_id=geo_id or "US", name=name)
    if with_grid and geo_id:
        # Imported here: a comparison figure never needs cells, and building
        # the grid for one would download the raw source to draw nothing.
        from pipeline import grid
        if geography in grid.CELL_GEOGRAPHIES:
            year = next((d.headline_year for s in page.sections for d in s.datasets
                         if d.id == "population"), None)
            if year:
                # No try/except. A place with no cells comes back as None and
                # simply has no map; a grid that cannot be built at all is a
                # failed build. Swallowing that once let every page build
                # without maps -- on CI, where the raw source is not cached.
                out.grid = grid.cells(geography, geo_id, year)
        out.land_km2 = land_km2(page.upstream).get(geo_id)
    for sec in page.sections:
        for ds in sec.datasets:
            if ds.kind != "upstream":
                continue
            if ds.id == "population":
                out.population = population_series(ds, geo_id)
            elif ds.dimension:
                out.shares[ds.id] = category_shares(ds, geo_id, ds.headline_year, catalog)
                if config.registry()["datasets"][ds.id].get("trend"):
                    out.trends[ds.id] = share_series(ds, geo_id, catalog)
                cross_by = config.registry()["datasets"][ds.id].get("cross_by")
                if cross_by:
                    out.cross[ds.id] = cross_shares(ds, geo_id, ds.headline_year,
                                                    catalog, outer=cross_by)
    return out


def with_comparisons(fig: PlaceFigures, catalog: dict) -> PlaceFigures:
    """Attach whatever this geography compares itself to.

    `peer_size` is deliberately not implemented yet — a metro has no natural
    parent, and choosing peers is a design decision rather than a computation.
    Skipping it leaves the comparison absent rather than silently wrong.
    """
    for target in fig.geo.compare_to:
        if target == "nation":
            fig.comparisons["nation"] = figures_for("state", None, catalog, name="United States")
        elif target == "state" and fig.geo.nested_in == "state":
            fig.comparisons["state"] = figures_for("state", fig.geo_id[:2], catalog)
    return fig
