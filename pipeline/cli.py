"""Build place pages.

    explorer build --geography county --id 51003
    explorer build --slice                      # one of each geography
    explorer build --all                        # every place, in parallel
    explorer build --all --geography county     # every county
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pipeline import config, figures, render

# Build output does NOT belong in the repo by default, because this repo lives
# in a Dropbox folder and the built site is 3 GB across 4,600 files. Dropbox
# syncs it, and worse, reverts it: a correct 4,120-place index written at 15:39
# was rolled back to a stale 3-place copy from 15:30, which looked exactly like
# a build bug and was not one. Regenerable output has no business in a synced
# folder anyway -- it would upload 3 GB on every rebuild.
#
# Override with EXPLORER_SITE_DIR to build somewhere else.
DEFAULT_OUT = Path.home() / "Library" / "Caches" / "population-explorer" / "_site"
OUT = Path(os.environ.get("EXPLORER_SITE_DIR") or DEFAULT_OUT)

# One of each geography. The metro is the case that reveals whether the
# template is secretly county-shaped.
SLICE = [("county", "51003"), ("state", "51"), ("cbsa", "16820")]


ASSET_DIR = "assets"


def write_assets() -> None:
    """The stylesheet, the marks, the tab icon and the front-page graphic,
    written once for the whole site."""
    out = OUT / ASSET_DIR
    out.mkdir(parents=True, exist_ok=True)
    # Name carries the content hash, so old copies cannot be served against a
    # new page. Stale ones are removed here and pruned from S3 on publish.
    for old in out.glob("explorer.*.css"):
        old.unlink()
    (out / render.stylesheet_name()).write_text(render.stylesheet())
    for name in (*render.LOGO_FILES.values(), render.FAVICON, render.HERO_IMAGE):
        if not (render.ASSETS / name).exists():
            continue
        (out / name).write_bytes((render.ASSETS / name).read_bytes())
    # Browsers and crawlers also ask for /favicon.ico whatever the page links,
    # and a 404 there is noise in every log.
    (OUT / render.FAVICON).write_bytes((render.ASSETS / render.FAVICON).read_bytes())


def url_for(geography: str, geo_id: str) -> str:
    """The place's URL relative to the site root, with a trailing slash."""
    prefix = config.registry()["geographies"][geography].get("url_prefix") or geography
    return f"{prefix}/{geo_id}/"


def write_search_index(built: list[tuple[str, str, str]]) -> None:
    """One small JSON the front page searches, rather than 4,120 links.

    Listing every place on the index would be a megabyte of HTML that nobody
    reads; a searchable index is both smaller and the thing people actually
    want. Arrays rather than objects because the keys would otherwise be
    repeated four thousand times.
    """
    out = OUT / "places.json"
    out.write_text(json.dumps([[u, k, n] for u, k, n in built],
                              separators=(",", ":")))
    print(f"  places.json ({out.stat().st_size:,} bytes, {len(built):,} places)")


def page_path(geography: str, geo_id: str) -> Path:
    """Where a place's page lives: /{url_prefix}/{geo_id}/index.html.

    A directory with an index, not a bare .html — see the URL SHAPE note in
    the registry. Everything is two levels deep, so the relative path back to
    the site root is the same for every page.
    """
    prefix = config.registry()["geographies"][geography].get("url_prefix") or geography
    return OUT / prefix / geo_id / "index.html"


def build_one(geography: str, geo_id: str, catalog: dict) -> Path:
    fig = figures.with_comparisons(
        figures.figures_for(geography, geo_id, catalog, with_grid=True), catalog)
    out = page_path(geography, geo_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    root = "../" * len(out.relative_to(OUT).parent.parts)
    out.write_text(render.page(fig, assets=f"{root}{ASSET_DIR}/", root=root))
    return out


def place_names(geography: str, catalog: dict) -> dict[str, str]:
    """geo_id -> display name, from the upstream `_names` artifact.

    The same file the gridded-eif site searches, so the two products call a
    place by the same name.
    """
    return config.fetch_bytes(catalog["names"][geography], as_json=True)


def every_target(geographies: list[str], catalog: dict) -> list[tuple[str, str]]:
    return [(g, gid) for g in geographies for gid in sorted(place_names(g, catalog))]


_CATALOG: dict | None = None


def _worker_init() -> None:
    """Each process fetches the catalog once, not once per page."""
    global _CATALOG
    _CATALOG = config.upstream_catalog()


def _worker(task: tuple[str, str]) -> tuple[str, str, int]:
    geo, gid = task
    p = build_one(geo, gid, _CATALOG)
    return geo, gid, p.stat().st_size


def build_many(targets: list[tuple[str, str]], jobs: int) -> list[tuple[str, str, int]]:
    """Pages are independent, so this is embarrassingly parallel.

    Serial, the full site is a little over half an hour; across cores it is a
    few minutes. Each worker holds its own DuckDB connection and its own
    Parquet mirror handles, which is why the catalog is fetched per process
    rather than pickled across.
    """
    if jobs == 1:
        _worker_init()
        return [_worker(t) for t in targets]
    with ProcessPoolExecutor(max_workers=jobs, initializer=_worker_init) as pool:
        return list(pool.map(_worker, targets, chunksize=8))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["build"])
    ap.add_argument("--geography")
    ap.add_argument("--id")
    ap.add_argument("--slice", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every place (optionally limited by --geography)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallel workers; 0 picks one per core")
    ap.add_argument("--limit", type=int, help="stop after N places (for smoke runs)")
    a = ap.parse_args()

    catalog = config.upstream_catalog()
    write_assets()

    if a.all:
        geos = [a.geography] if a.geography else list(config.registry()["geographies"])
        targets = every_target(geos, catalog)
    elif a.slice:
        targets = SLICE
    else:
        targets = [(a.geography, a.id)]
    if a.limit:
        targets = targets[:a.limit]

    jobs = a.jobs or (os.cpu_count() or 1)
    t0 = time.time()
    results = build_many(targets, jobs if len(targets) > 1 else 1)
    elapsed = time.time() - t0

    names = {g: place_names(g, catalog) for g in {t[0] for t in targets}}
    labels = {g: config.registry()["geographies"][g]["label"]
              for g in {t[0] for t in targets}}
    built = [(url_for(geo, gid), labels[geo], names[geo].get(gid, gid))
             for geo, gid, _n in results]

    if len(results) <= 12:
        for geo, gid, size in results:
            print(f"  {url_for(geo, gid):<28} ({size:,} bytes)")
    total = sum(n for _g, _i, n in results)
    print(f"  {len(results):,} pages, {total / 2**30:.2f} GB, "
          f"{elapsed:.0f}s on {jobs} workers")

    # The index, the full listing and the search file describe the WHOLE site,
    # so only a whole-site build may write them. Rebuilding one page used to
    # rewrite them from that one page -- `places.json` went to a single entry
    # and the front page reported "Loading 1 places", which looked like a
    # front-end bug and was a build one.
    if a.all and not a.limit and not a.geography:
        write_search_index(built)
        for name, html in (("index.html", render.index(built, assets=f"{ASSET_DIR}/")),
                           ("places.html", render.places_page(built, assets=f"{ASSET_DIR}/")),
                           # Linked from the nav of every page, never optional.
                           ("about.html", render.about_page(assets=f"{ASSET_DIR}/",
                                                            n_places=len(built)))):
            (OUT / name).write_text(html)
            print(f"  {name}  ({(OUT / name).stat().st_size:,} bytes)")
    else:
        # `about.html` describes conventions rather than the set of places, so
        # it is safe to refresh from any build.
        (OUT / "about.html").write_text(
            render.about_page(assets=f"{ASSET_DIR}/",
                              n_places=len(json.loads((OUT / "places.json").read_text()))
                              if (OUT / "places.json").exists() else len(built)))
        print("  (partial build: index, listing and search index left alone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
