"""Cell-level data for the maps.

The published aggregates stop at whole geographies, which is deliberate: a
single 0.01-degree cell is noise-dominated, and gridded-eif declines to serve
one. The maps are this repo's job, so this module builds them — the raw Census
source joined to the published crosswalk, aggregated to one row per cell per
place, cached locally and never served.

The output is small enough to inline in a page. A county holds 884 cells at the
median and 5,697 at the most, which is less data than one of the charts already
on the page and roughly fifty times smaller than the raster the published
explorer shipped for the same map.

States are excluded on purpose. A state holds 53,000 cells at the median and
208,000 at the most, and at that zoom a one-kilometre cell is smaller than a
pixel — the wrong tool for the question. State maps are county choropleths
drawn from the boundaries gridded-eif already publishes.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pipeline import config, figures

CACHE = config.REPO_ROOT / ".cache"

# The source files. Census serves them at this base, but the endpoint is not
# always up — it returned 520 while this was written — so a local mirror can be
# named instead. Whichever is used, the crosswalk that assigns cells to places
# is always the published one, so both products agree on which cell is where.
CENSUS_BASE = "https://www2.census.gov/ces/gridded_eif"
LOCAL_SOURCE_ENV = "GEIF_SOURCE_DIR"
FILE = {"ageracesex": "gridded_eif_pop_ageracesex_{year}.parquet",
        "raceincome": "gridded_eif_pop_raceincome_{year}.parquet"}
# A preliminary year comes from Census's "real-time" release, published under
# its own name -- the same pattern gridded-eif's registry declares as
# `preliminary_file_pattern`. Asking for 2025 by the final name is a 404, which
# a machine with a warm cache never sees and a fresh CI runner always does.
PRELIMINARY_FILE = {"ageracesex": "gridded_eif_pop_ageracesex_{year}_realtime.parquet",
                    "raceincome": "gridded_eif_pop_raceincome_{year}_realtime.parquet"}

# Latitude and longitude degrees are not the same distance, and the longitude
# one shrinks toward the poles — so cell area is a function of latitude, not a
# constant. One degree of latitude is 111.049 km; one of longitude is 111.320
# km times the cosine of the latitude.
KM_PER_DEG_LAT = 111.049
KM_PER_DEG_LON = 111.320
CELL_DEG = 0.01

# Geographies whose maps are drawn from cells. See the module note on states.
CELL_GEOGRAPHIES = ("county", "cbsa")


def source(dataset: str, year: int) -> str:
    prelim = year in (config.upstream_catalog().get("datasets", {})
                      .get(dataset, {}).get("preliminary_years", ()))
    name = (PRELIMINARY_FILE if prelim else FILE)[dataset].format(year=year)
    local = os.environ.get(LOCAL_SOURCE_ENV)
    if local:
        for candidate in (Path(local) / name, Path(local) / dataset / name):
            if candidate.exists():
                return str(candidate)
    return f"{CENSUS_BASE}/{name}"


def cell_km2(lat: float) -> float:
    """Area of one 0.01-degree cell at this latitude, in square kilometres."""
    return (CELL_DEG * KM_PER_DEG_LAT) * (CELL_DEG * KM_PER_DEG_LON * math.cos(math.radians(lat)))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

@cache
def _crosswalk(geography: str) -> str:
    cat = config.upstream_catalog()
    url = cat["crosswalks"][geography]
    return figures.local(url)


def build(geography: str, year: int, *, force: bool = False) -> Path:
    """One row per (place, cell), wide across the dimensions the maps need.

    Wide rather than long because every map wants the same cells and differs
    only in which column it colours — and because a column of zeros costs
    almost nothing in Parquet, while a row does.
    """
    if geography not in CELL_GEOGRAPHIES:
        raise ValueError(f"{geography!r} maps are not drawn from cells — see the module note")
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"grid_{geography}_{year}.parquet"
    if out.exists() and not force:
        return out

    con = figures._con()
    xw = _crosswalk(geography)
    # Built under a per-process name and renamed into place, so a parallel
    # worker never reads a half-written grid or collides on DuckDB's lock.
    final, out = out, out.with_name(f"{out.name}.{os.getpid()}.tmp")
    ars, ri = source("ageracesex", year), source("raceincome", year)
    m = figures.MEASURE

    races = [v["code"] for v in config.upstream_catalog()["dimensions"]
             ["race_ethnicity"]["values"]]
    race_cols = ",\n           ".join(
        f"""sum(CASE WHEN a.race_ethnicity = '{r}' THEN a.{m} END) AS "race_{r}" """
        for r in races
    )

    con.execute(f"""
        COPY (
          WITH pop AS (
            SELECT x.geo_id, a.grid_lon, a.grid_lat,
                   sum(a.{m}) AS pop,
                   {race_cols}
            FROM read_parquet('{ars}') a
            JOIN read_parquet('{xw}') x
              ON a.grid_lon = x.grid_lon AND a.grid_lat = x.grid_lat
            GROUP BY 1, 2, 3
          ),
          inc AS (
            SELECT x.geo_id, r.grid_lon, r.grid_lat,
                   sum(CASE WHEN r.income_decile = 1  THEN r.{m} END) AS decile_low,
                   sum(CASE WHEN r.income_decile = 10 THEN r.{m} END) AS decile_high,
                   sum(CASE WHEN r.income_decile BETWEEN 1 AND 10 THEN r.{m} END) AS with_income
            FROM read_parquet('{ri}') r
            JOIN read_parquet('{xw}') x
              ON r.grid_lon = x.grid_lon AND r.grid_lat = x.grid_lat
            GROUP BY 1, 2, 3
          )
          SELECT p.*, i.decile_low, i.decile_high, i.with_income
          FROM pop p LEFT JOIN inc i
            ON p.geo_id = i.geo_id AND p.grid_lon = i.grid_lon AND p.grid_lat = i.grid_lat
          -- A cell the noise pushed to zero or below is not a place anyone lives.
          WHERE p.pop > 0.5
          ORDER BY p.geo_id
        ) TO '{out}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 20000)
    """)
    os.replace(out, final)
    return final


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellGrid:
    """A place's populated cells, and the area they cover."""
    geo_id: str
    lons: tuple[float, ...]
    lats: tuple[float, ...]
    values: dict[str, tuple[float, ...]]   # layer name -> per-cell value
    land_km2: float                        # summed cell area, the populated footprint

    def __len__(self) -> int:
        return len(self.lons)

    @property
    def total(self) -> float:
        return sum(self.values.get("pop", ()))

    def layer(self, name: str) -> tuple[float, ...]:
        return self.values.get(name, ())


def cells(geography: str, geo_id: str, year: int) -> CellGrid | None:
    path = build(geography, year)
    con = figures._con()
    cols = [d[0] for d in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    layers = [c for c in cols if c not in ("geo_id", "grid_lon", "grid_lat")]
    rows = con.execute(f"""
        SELECT CAST(grid_lon AS DOUBLE), CAST(grid_lat AS DOUBLE), {", ".join(f'"{c}"' for c in layers)}
        FROM read_parquet('{path}') WHERE geo_id = '{geo_id}'
    """).fetchall()
    if not rows:
        return None
    lons = tuple(r[0] for r in rows)
    lats = tuple(r[1] for r in rows)
    values = {name: tuple((r[i + 2] or 0.0) for r in rows) for i, name in enumerate(layers)}
    land = sum(cell_km2(lat) for lat in lats)
    return CellGrid(geo_id=geo_id, lons=lons, lats=lats, values=values, land_km2=land)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    """A square of cells shown as one mark, and the real sum inside it."""
    lon: float          # west edge
    lat: float          # south edge
    span: float         # degrees on a side
    value: float        # summed count of the cells inside — measured, not modelled
    cells: int          # how many populated cells were combined
    km2: float

    @property
    def density(self) -> float:
        return self.value / self.km2 if self.km2 else 0.0


def aggregate(g: CellGrid, layer: str, *, min_count: float, max_level: int = 5) -> list[Block]:
    """Quadtree aggregation: combine cells until each block carries enough people.

    Preferred over blurring because every block is a real sum of real cells —
    a block reading 400 people contains 400 people — while a blurred cell shows
    a number nobody counted. Injected noise falls as the square root of the
    cells combined, so this attacks the noise where it actually lives, in small
    counts, and leaves dense areas at full resolution.

    Top-down: start from squares `2**max_level` cells on a side and split one
    into its four quarters only if every populated quarter still holds
    `min_count`. The blocks then partition the cells, and every block clears
    the threshold except a top-size square whose whole content falls short.
    The earlier bottom-up version let a cell claim the smallest square that
    cleared, which left the sparse remainder of a larger square as a "block"
    of well under the threshold -- a fifth of Albemarle's blocks.

    Blocks vary in size, so a count is not comparable between them. The map
    therefore colours by DENSITY, which is.
    """
    vals = g.layer(layer)
    if not vals:
        return []

    # Cell INDEX, not round(coordinate * 100). Centres sit on the half-step
    # (-78.455), where rounding is ambiguous: it put Albemarle's 1,440 cells
    # on 727 indices, so neighbors were merged and blocks were the wrong
    # shape. Floor is exact, and matches render._cell_key.
    xs = [math.floor(lo / CELL_DEG) for lo in g.lons]
    ys = [math.floor(la / CELL_DEG) for la in g.lats]

    top = 1 << max_level
    roots: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(zip(xs, ys)):
        roots.setdefault((x // top * top, y // top * top), []).append(i)

    out: list[Block] = []

    def emit(bx: int, by: int, side: int, members: list[int]) -> None:
        lat_mid = (by + side / 2) * CELL_DEG
        out.append(Block(
            lon=bx * CELL_DEG, lat=by * CELL_DEG, span=side * CELL_DEG,
            value=sum(vals[i] for i in members),
            cells=len(members),
            km2=len(members) * cell_km2(lat_mid),
        ))

    def split(bx: int, by: int, side: int, members: list[int]) -> None:
        if side > 1:
            half = side // 2
            quarters: dict[tuple[int, int], list[int]] = {}
            for i in members:
                quarters.setdefault(((xs[i] - bx) // half, (ys[i] - by) // half), []).append(i)
            if all(sum(vals[i] for i in q) >= min_count for q in quarters.values()):
                for (qx, qy), q in quarters.items():
                    split(bx + qx * half, by + qy * half, half, q)
                return
        emit(bx, by, side, members)

    for (bx, by), members in roots.items():
        split(bx, by, top, members)
    return out
