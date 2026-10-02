"""Place outlines, so a map is of somewhere.

Cells alone are not a map. Without the boundary a reader sees coloured squares
floating in space and cannot answer the only question the figure is asked —
where in *this place* do people live. The outline is what turns a scatter of
values into a county.

Geometry comes from the boundaries gridded-eif already publishes, cached once
and shared by every page.
"""

from __future__ import annotations

import json
from functools import cache

from pipeline import config

CACHE = config.REPO_ROOT / ".cache"


@cache
def _collection(geography: str) -> dict:
    CACHE.mkdir(exist_ok=True)
    local = CACHE / f"boundaries_{geography}.geojson"
    if not local.exists():
        url = config.upstream_catalog()["boundaries"][geography]
        local.write_bytes(config.fetch_bytes(url))
    return json.loads(local.read_text())


@cache
def rings(geography: str, geo_id: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Every ring of a place's outline, as (lon, lat) pairs.

    Polygons and multipolygons flatten to the same thing here: the outline is
    drawn, never filled by winding rule, so holes and islands need no special
    handling.
    """
    for f in _collection(geography)["features"]:
        if f["properties"].get("geo_id") != geo_id:
            continue
        geom = f["geometry"]
        coords = geom["coordinates"]
        parts = coords if geom["type"] == "Polygon" else [r for poly in coords for r in poly]
        return tuple(tuple((float(x), float(y)) for x, y in ring) for ring in parts)
    return ()


def bbox(rings) -> tuple[float, float, float, float]:
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    return (min(xs), max(xs), min(ys), max(ys))


def contains(rings, x: float, y: float) -> bool:
    """Ray casting, counting every ring.

    An odd number of crossings means inside — which handles holes and islands
    without treating them as special cases, since a point inside a hole crosses
    both the outer ring and the inner one.
    """
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xi = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
                if x < xi:
                    inside = not inside
    return inside


@cache
def neighbours(geography: str, geo_id: str, pad: float = 0.02) -> tuple:
    """Outlines of the places around this one, for context.

    A map of a county with nothing beyond its border tells a reader where
    people live but not where the county is. Neighbouring outlines put it on
    the map without needing tiles, an external request, or an attribution.
    """
    own = rings(geography, geo_id)
    if not own:
        return ()
    w0, e0, s0, n0 = bbox(own)
    w0, e0, s0, n0 = w0 - pad, e0 + pad, s0 - pad, n0 + pad
    out = []
    for f in _collection(geography)["features"]:
        gid = f["properties"].get("geo_id")
        if gid == geo_id:
            continue
        geom = f["geometry"]
        coords = geom["coordinates"]
        parts = coords if geom["type"] == "Polygon" else [r for poly in coords for r in poly]
        rs = tuple(tuple((float(x), float(y)) for x, y in ring) for ring in parts)
        bw, be, bs, bn = bbox(rs)
        if be < w0 or bw > e0 or bn < s0 or bs > n0:
            continue
        out.append(rs)
    return tuple(out)
