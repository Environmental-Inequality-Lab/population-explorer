"""Draw the front-page graphic: the 1 km grid around Charlottesville.

Every pixel block is one published 0.01-degree Gridded EIF cell, shaded by its
2025 population on the brand maroon ramp. It is a texture, not a map: no
outlines, no labels, nothing to read a place off.

The outline is the data too. Toward the edge of the frame only the denser
cells survive, so the shape is drawn by towns and frays into scattered pixels
where people are sparse. A cell absent from the source is a true zero and is
left transparent, which is why the ridges and the national forest show as
gaps.

Run once, commit the image. It changes only if the window, the vintage or the
styling does, so it is not part of `explorer build`.

    python tools/build_hero.py              # from the repo root
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import grid, render

YEAR = 2025
# Charlottesville and the Blue Ridge: the lab's home, and a window that holds
# a city, small towns, farmland and empty ridgeline in one frame.
LON0, LON1, LAT0, LAT1 = -79.25, -77.25, 37.60, 38.83
STEP = 0.01
PX = 6                       # pixels per cell; about 2x the size it displays at
LIGHT = np.array([246, 238, 238])   # --brand-deep-050
DEEP = np.array([96, 18, 21])       # --brand-deep
OUT = render.ASSETS / render.HERO_IMAGE


def population_grid() -> np.ndarray:
    path = grid.build("county", YEAR)
    q = duckdb.sql(f"""
        SELECT CAST(grid_lon AS DOUBLE) AS lon, CAST(grid_lat AS DOUBLE) AS lat, pop
        FROM read_parquet('{path}')
        WHERE CAST(grid_lon AS DOUBLE) BETWEEN {LON0} AND {LON1}
          AND CAST(grid_lat AS DOUBLE) BETWEEN {LAT0} AND {LAT1}""").fetchnumpy()
    cols = round((LON1 - LON0) / STEP) + 1
    rows = round((LAT1 - LAT0) / STEP) + 1
    g = np.zeros((rows, cols))
    # Coordinates are cell centres on the half-step (-79.245), so floor, not
    # round: rounding a .5 alternates direction and drops every other row.
    c = np.floor((q["lon"] - LON0) / STEP + 1e-6).astype(int)
    r = np.floor((LAT1 - q["lat"]) / STEP + 1e-6).astype(int)
    ok = (c >= 0) & (c < cols) & (r >= 0) & (r < rows)
    np.add.at(g, (r[ok], c[ok]), np.nan_to_num(q["pop"][ok]))
    return g


def dissolve_mask(g: np.ndarray) -> np.ndarray:
    """Keep a cell if it is dense enough for its distance from the centre.

    Distance is elliptical, 0 at the centre and 1 at the frame's edge. Density
    is the cell's percentile among populated cells, so the rule reads the same
    whatever the window: the centre keeps everything, the rim keeps only the
    densest.
    """
    rows, cols = g.shape
    y = (np.arange(rows)[:, None] - rows / 2) / (rows / 2)
    x = (np.arange(cols)[None, :] - cols / 2) / (cols / 2)
    dist = np.sqrt(x ** 2 + y ** 2)
    lit = g > 0
    pct = np.zeros_like(g)
    pct[lit] = np.argsort(np.argsort(g[lit])) / lit.sum()
    return lit & (dist < 0.62 + 0.55 * pct ** 1.5)


def main() -> int:
    g = population_grid()
    keep = dissolve_mask(g)
    shade = np.log10(g + 1)
    t = shade / shade.max()
    rgba = np.zeros((*g.shape, 4), np.uint8)
    rgba[..., :3] = (LIGHT + (DEEP - LIGHT) * t[..., None]).astype(np.uint8)
    rgba[..., 3] = np.where(keep, 255, 0)
    big = np.repeat(np.repeat(rgba, PX, 0), PX, 1)
    Image.fromarray(big, "RGBA").save(OUT, "WEBP", quality=88, method=6)
    print(f"{OUT.relative_to(render.ASSETS.parent)}  {big.shape[1]}x{big.shape[0]}  "
          f"{OUT.stat().st_size:,} bytes  ({int(keep.sum()):,} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
