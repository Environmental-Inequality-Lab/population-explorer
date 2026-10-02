"""Publishes the built site to S3, behind CloudFront.

Why not GitHub Pages: the built site is about 3.2 GB across 4,120 pages, and
GitHub caps a published site at 1 GB. It is not a near miss. Committing the
output would also be a mistake independent of the limit — essentially every
page changes on every rebuild, and git keeps every version.

Two things here are easy to get wrong:

**Only changed files are uploaded.** A rebuild rewrites all 4,120 pages, but
most are byte-identical to what is already published. S3 returns each object's
MD5 as its ETag, so comparing locally costs one LIST per thousand objects and
saves re-uploading gigabytes that did not change. A template tweak still
uploads everything, which is correct.

**HTML is mutable, so it gets a short TTL.** Nothing here is content-hashed:
`/county/51003/` is a stable, citable URL whose contents change when the data
refreshes. Long-lived caching would serve stale pages until the TTL expired.
The stylesheet and logos are mutable too, but they change rarely and a stale
one for a day is harmless.

CloudFront compresses text responses itself (gzip and brotli, for objects
between 1 KB and 10 MB) as long as we do not set Content-Encoding, so nothing
is pre-compressed here. The largest page is about 4.4 MB, inside that window.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Matches the builder: output lives outside the synced repo folder unless
# EXPLORER_SITE_DIR says otherwise. See the note in cli.py.
SITE = Path(os.environ.get("EXPLORER_SITE_DIR")
            or Path.home() / "Library" / "Caches" / "population-explorer" / "_site")

# Everything the site serves is rewritable in place, so nothing is cached for
# long. Five minutes keeps the CDN useful without making a data refresh
# invisible; the publish invalidates what it replaced anyway.
HTML_CACHE = "public, max-age=300, must-revalidate"
# The stylesheet's name carries its content hash, so its URL changes whenever
# its bytes do and it can be cached hard. The logos are referenced from inside
# that stylesheet, so they turn over with it in practice.
ASSET_CACHE = "public, max-age=31536000, immutable"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
}


def content_type(path: Path) -> str:
    return (CONTENT_TYPES.get(path.suffix.lower())
            or mimetypes.guess_type(path.name)[0]
            or "application/octet-stream")


def cache_control(key: str) -> str:
    return ASSET_CACHE if key.startswith("assets/") else HTML_CACHE


def _md5(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - matching S3's ETag, not hashing a secret
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def published_etags(s3, bucket: str) -> dict[str, str]:
    """Every key already in the bucket, with its ETag.

    A multipart upload's ETag carries a `-N` suffix and is not the object's
    MD5. Those are returned as-is and simply never match, so the file uploads
    again - wrong in the safe direction.
    """
    out: dict[str, str] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = obj["ETag"].strip('"')
    return out


def plan(site: Path, existing: dict[str, str]) -> tuple[list[Path], list[str]]:
    """What to upload and what to delete, comparing local MD5 to remote ETag."""
    upload, seen = [], set()
    for path in sorted(site.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        key = path.relative_to(site).as_posix()
        seen.add(key)
        if existing.get(key) != _md5(path):
            upload.append(path)
    # A place that disappears upstream leaves a page behind; a URL that should
    # no longer exist must stop resolving rather than serve last year's data.
    stale = sorted(set(existing) - seen)
    return upload, stale


def check_complete(site: Path) -> None:
    """Refuse to prune from anything but a whole-site build.

    Pruning deletes every published object the local build lacks, so
    publishing after `build --slice` or `--limit` would take down every other
    place. Only a whole-site build writes `places.json`, and it lists every
    page that build produced, so each listed page must exist locally.
    """
    index = site / "places.json"
    if not index.exists():
        raise SystemExit(f"{index} is missing, so {site} is not a whole-site build; "
                         "run `explorer build --all` or pass --no-prune")
    missing = [u for u, _k, _n in json.loads(index.read_text())
               if not (site / u / "index.html").exists()]
    if missing:
        raise SystemExit(f"{len(missing):,} pages in places.json were not built "
                         f"(first: {missing[0]}); refusing to prune")


def publish(bucket: str, *, site: Path | None = None, distribution_id: str | None = None,
            dry_run: bool = False, jobs: int = 16, prune: bool = True) -> dict:
    import boto3

    site = site or SITE
    if not site.exists():
        raise SystemExit(f"nothing built at {site} - run `explorer build --all` first")

    # Said first and in full: with EXPLORER_SITE_DIR unset in the shell that
    # runs this, it falls back to the default folder, which can hold an older
    # build -- and a dry run listing 4,127 files looks right either way.
    print(f"  publishing {site}")
    if prune:
        check_complete(site)

    s3 = boto3.client("s3")
    existing = published_etags(s3, bucket)
    upload, stale = plan(site, existing)
    if not prune:
        stale = []

    total = sum(p.stat().st_size for p in upload)
    print(f"  {len(existing):,} objects published, {len(upload):,} to upload "
          f"({total / 2**30:.2f} GB), {len(stale):,} to remove")
    if dry_run:
        for p in upload[:10]:
            print(f"    would upload {p.relative_to(site).as_posix()}")
        return {"uploaded": len(upload), "deleted": len(stale), "dry_run": True}

    def put(path: Path) -> None:
        key = path.relative_to(site).as_posix()
        s3.upload_file(str(path), bucket, key, ExtraArgs={
            "ContentType": content_type(path),
            "CacheControl": cache_control(key),
        })

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(put, upload))

    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={
            "Objects": [{"Key": k} for k in stale[i:i + 1000]]})

    print(f"  uploaded in {time.time() - t0:.0f}s")
    if distribution_id:
        _invalidate(distribution_id, upload, stale, site)
    return {"uploaded": len(upload), "deleted": len(stale)}


def invalidation_paths(upload: list[Path], stale: list[str], site: Path) -> list[str]:
    """What to invalidate, or `/*` when naming them all would cost more.

    CloudFront charges per path after the first thousand a month, so a large
    publish invalidates everything once instead of naming ten thousand paths.
    A small one names them, which keeps the rest of the cache warm.

    A page is served at its DIRECTORY url, so that is the path a reader's
    cache holds. Invalidating only `/county/51003/index.html` would leave the
    stale copy in place.
    """
    paths = ["/" + p.relative_to(site).as_posix() for p in upload]
    paths += ["/" + k for k in stale]
    paths += [p[:-len("index.html")] for p in paths if p.endswith("/index.html")]
    paths = sorted(set(paths))
    return ["/*"] if len(paths) > 1000 else paths


def _invalidate(distribution_id: str, upload: list[Path], stale: list[str],
                site: Path) -> None:
    import boto3

    paths = invalidation_paths(upload, stale, site)
    if not paths:
        print("  nothing to invalidate")
        return
    boto3.client("cloudfront").create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": f"explorer-{int(time.time())}",
        },
    )
    print(f"  invalidated {len(paths):,} path(s)")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Publish the built site to S3.")
    ap.add_argument("--bucket", default=os.environ.get("EXPLORER_BUCKET"))
    ap.add_argument("--distribution-id", default=os.environ.get("EXPLORER_DISTRIBUTION_ID"))
    ap.add_argument("--site", type=Path, default=None,
                    help="the built site to publish (default: EXPLORER_SITE_DIR, "
                         "else ~/Library/Caches/population-explorer/_site)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--no-prune", action="store_true",
                    help="leave objects that are no longer built")
    a = ap.parse_args(argv)
    if not a.bucket:
        raise SystemExit("--bucket or EXPLORER_BUCKET is required")
    publish(a.bucket, site=a.site.expanduser() if a.site else None,
            distribution_id=a.distribution_id, dry_run=a.dry_run,
            jobs=a.jobs, prune=not a.no_prune)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
