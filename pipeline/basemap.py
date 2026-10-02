"""Roads and towns, so a map has landmarks.

Boundaries and coordinates tell a reader where a place is on the globe. Roads
and town names tell them where it is in their own head — a policymaker looking
at their county finds the interstate first, then the towns, then reads the
population pattern against them.

Both come from TIGER, the same source as the boundaries: primary roads
nationally, and place points per state. Downloaded once, cached, and drawn as
thin context — never as the subject of the figure.

This is the last thing the explorer takes straight from TIGER. Land area used
to be read here too and now arrives as published JSON, but place names and
their internal points are not published by anyone, so the readers stay.

The shapefile readers are deliberately small. TIGER's polyline format is a
header, a record header, and pairs of doubles; that does not justify a
geospatial stack.
"""

from __future__ import annotations

import io
import os
import shutil
import struct
import zipfile
from functools import cache
from pathlib import Path

from pipeline import config

CACHE = config.REPO_ROOT / ".cache"
TIGER = "https://www2.census.gov/geo/tiger/TIGER2024"

# Interstates and US highways only. TIGER's MTFCC codes: S1100 is a primary
# road, which is what a reader orients by. Everything below that is clutter at
# the scale of a county.
ROAD_FILE = "PRIMARYROADS/tl_2024_us_primaryroads.zip"
PLACE_FILE = "PLACE/tl_2024_{state}_place.zip"


def _read_dbf(path: Path, wanted: tuple[str, ...]) -> list[dict[str, str]]:
    """Minimal dBASE III reader — enough for a couple of character columns.

    TIGER ships place names and their internal points in the shapefile's
    attribute table and nowhere else, so this stays even though land area now
    comes from upstream as JSON. Two columns do not justify a geospatial
    stack, and the format is fixed-width and stable.
    """
    with path.open("rb") as fh:
        header = fh.read(32)
        n_records, header_len, record_len = struct.unpack("<I H H", header[4:12])

        fields = []
        while True:
            desc = fh.read(32)
            if not desc or desc[0] == 0x0D:
                break
            name = desc[:11].split(b"\0")[0].decode("ascii", "replace")
            length = desc[16]
            fields.append((name, length))

        fh.seek(header_len)
        rows = []
        for _ in range(n_records):
            rec = fh.read(record_len)
            if not rec or rec[:1] == b"*":       # deleted
                continue
            out, pos = {}, 1
            for name, length in fields:
                if name in wanted:
                    out[name] = rec[pos:pos + length].decode("latin-1").strip()
                pos += length
            rows.append(out)
    return rows


def _fetch(rel: str) -> Path:
    """Download and unpack one TIGER archive, once."""
    name = rel.split("/")[-1].removesuffix(".zip")
    folder = CACHE / f"tiger_{name}"
    if folder.exists():
        return folder
    CACHE.mkdir(exist_ok=True)
    # Through the shared fetch, which retries a dropped connection rather than
    # unzipping half an archive.
    data = config.fetch_bytes(f"{TIGER}/{rel}", timeout=300)
    # Unpacked into a folder of this process's own, then renamed into place.
    # A folder that merely exists is not a finished one: a parallel worker
    # used to find it half-unzipped and draw a map with no town labels.
    tmp = folder.with_name(f"{folder.name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    try:
        os.rename(tmp, folder)
    except OSError:          # another worker finished first; theirs is complete
        shutil.rmtree(tmp, ignore_errors=True)
    return folder


def read_polylines(shp: Path, extent) -> list[list[tuple[float, float]]]:
    """Every polyline part whose bounding box meets `extent`.

    Records carry their own bounding box, so most of a national file is
    rejected without reading a single coordinate.
    """
    w0, e0, s0, n0 = extent
    out: list[list[tuple[float, float]]] = []
    with shp.open("rb") as fh:
        fh.seek(24)
        file_words = struct.unpack(">I", fh.read(4))[0]
        fh.seek(100)
        end = file_words * 2
        while fh.tell() < end:
            head = fh.read(8)
            if len(head) < 8:
                break
            _num, length = struct.unpack(">II", head)
            body = fh.read(length * 2)
            shape_type = struct.unpack("<I", body[:4])[0]
            if shape_type != 3:            # polyline
                continue
            xmin, ymin, xmax, ymax = struct.unpack("<4d", body[4:36])
            if xmax < w0 or xmin > e0 or ymax < s0 or ymin > n0:
                continue
            n_parts, n_pts = struct.unpack("<II", body[36:44])
            parts = struct.unpack(f"<{n_parts}I", body[44:44 + 4 * n_parts])
            off = 44 + 4 * n_parts
            pts = struct.unpack(f"<{2 * n_pts}d", body[off:off + 16 * n_pts])
            bounds = list(parts) + [n_pts]
            for i in range(n_parts):
                seg = [(pts[2 * j], pts[2 * j + 1]) for j in range(bounds[i], bounds[i + 1])]
                if len(seg) > 1:
                    out.append(seg)
    return out


@cache
def roads(extent) -> tuple:
    try:
        folder = _fetch(ROAD_FILE)
        shp = next(folder.glob("*.shp"))
    except Exception:  # noqa: BLE001 — a map without roads is still a map
        return ()
    return tuple(tuple(p) for p in read_polylines(shp, extent))


@cache
def _places(state_fips: str, extent) -> tuple:
    """Named places inside the frame.

    Positions come from TIGER's own internal point, which is guaranteed to fall
    inside the place — a centroid is not, for anywhere crescent-shaped.
    """
    w0, e0, s0, n0 = extent
    try:
        folder = _fetch(PLACE_FILE.format(state=state_fips))
        dbf = next(folder.glob("*.dbf"))
    except Exception:  # noqa: BLE001 — a map without labels is still a map
        return ()
    out = []
    for r in _read_dbf(dbf, ("NAME", "INTPTLAT", "INTPTLON")):
        try:
            lat, lon = float(r["INTPTLAT"]), float(r["INTPTLON"])
        except (TypeError, ValueError):
            continue
        if w0 <= lon <= e0 and s0 <= lat <= n0:
            out.append((r["NAME"], lon, lat))
    return tuple(out)


def states_in(extent) -> tuple[str, ...]:
    """FIPS of every state whose outline's bounding box meets the frame.

    A metro crosses state lines and a county map shows its neighbors, so
    labels cannot come from one state's place file.
    """
    from pipeline import shapes
    x0, x1, y0, y1 = extent        # west, east, south, north
    out = []
    for feat in shapes._collection("state")["features"]:
        pts = [p for ring in _rings(feat["geometry"]) for p in ring]
        if not pts:
            continue
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        if min(lons) <= x1 and max(lons) >= x0 and min(lats) <= y1 and max(lats) >= y0:
            out.append(str(feat["properties"]["geo_id"]))
    return tuple(sorted(out))


def _rings(geom: dict):
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    if geom["type"] == "MultiPolygon":
        return [r for poly in geom["coordinates"] for r in poly]
    return []


def towns(state_fips, extent, cells=None, limit: int = 10,
          min_gap: float = 0.06) -> tuple:
    """The places worth labelling, ranked by the people actually near them.

    TIGER has no population field, and ranking by land area surfaces hamlets
    that happen to be spread out. Ranking by the population in the surrounding
    cells uses the data already on the page, and puts the labels where a reader
    would expect to find them.

    Labels are then thinned so none sits on top of another.
    """
    fips = (state_fips,) if isinstance(state_fips, str) else tuple(state_fips)
    places = tuple(p for s in fips if s for p in _places(s, extent))
    if not places:
        return ()

    if cells:
        near = []
        for name, lon, lat in places:
            pop = sum(v for clon, clat, _d, v in cells
                      if abs(clon - lon) < 0.045 and abs(clat - lat) < 0.035)
            near.append((pop, name, lon, lat))
        near.sort(reverse=True)
        ranked = [(n, lo, la) for pop, n, lo, la in near if pop > 0]
    else:
        ranked = list(places)

    kept: list[tuple] = []
    for name, lon, lat in ranked:
        if all(abs(lon - x) > min_gap or abs(lat - y) > min_gap * 0.7
               for _n, x, y in kept):
            kept.append((name, lon, lat))
        if len(kept) >= limit:
            break
    return tuple(kept)
