"""Build the GitHub Pages redirect site at the explorer's cited address.

The site is cited as https://environmental-inequality-lab.github.io/population-explorer/
but served from CloudFront: at about 3 GB it is three times GitHub Pages' 1 GB
cap. So the github.io address publishes only this: a forwarding page for
every page of the real site, at the same path, plus a 404 page that forwards
any other path unchanged. The whole thing is a few megabytes. (The old
explorer that lived here is retired outright; its addresses are not mapped.)

Every forwarding page redirects three ways -- meta refresh, a script, and a
visible link -- and names the destination as canonical, so search engines
index the CloudFront copy rather than the shell.

To move the site later (a custom domain, say), rebuild this with the new
--target and push it again. Nothing on the main site changes, and every
address anyone has cited keeps landing in the right place.

    python tools/build_redirects.py --target https://dxxxxxxxxxxxxx.cloudfront.net
    python tools/build_redirects.py --target ... --out /path/to/gh-pages-checkout
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import sys
from pathlib import Path

SITE = Path(os.environ.get("EXPLORER_SITE_DIR")
            or Path.home() / "Library" / "Caches" / "population-explorer" / "_site")
DEFAULT_OUT = SITE.parent / "redirects"


def page(dest: str) -> str:
    d = html.escape(dest, quote=True)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>Population Data Explorer</title>'
            f'<link rel="canonical" href="{d}">'
            f'<meta name="robots" content="noindex">'
            f'<meta http-equiv="refresh" content="0; url={d}">'
            f'<script>location.replace({json.dumps(dest)}'
            f' + location.search + location.hash)</script></head>'
            f'<body><p>This page has moved to <a href="{d}">{d}</a>.</p></body></html>')


NOT_FOUND = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Population Data Explorer</title><meta name="robots" content="noindex">
<script>
(function () {{
  var target = {target}, base = {base};
  var path = location.pathname;
  if (path.indexOf(base) === 0) path = path.slice(base.length);
  path = path.replace(/^\\/+/, '');
  location.replace(target + path + location.search + location.hash);
}})();
</script></head>
<body><p>This site has moved to <a href={target}>{target_text}</a>.</p></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", required=True,
                    help="the real site's base URL, e.g. https://dxxxx.cloudfront.net")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--base", default="/population-explorer/",
                    help="the path GitHub Pages serves this repo under")
    a = ap.parse_args()

    target = a.target.rstrip("/") + "/"
    index = SITE / "places.json"
    if not index.exists():
        sys.exit(f"{index} missing: run `explorer build --all` first")
    places = json.loads(index.read_text())

    if a.out.exists():
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)

    pages = ["", "about.html", "places.html"] + [u for u, _k, _n in places]
    for path in pages:
        dest = a.out / (path + "index.html" if path == "" or path.endswith("/") else path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page(target + path))

    # Anything without a forwarding page -- a place added since this was
    # built -- goes to the same path on the real site.
    (a.out / "404.html").write_text(NOT_FOUND.format(
        target=json.dumps(target), target_text=html.escape(target), base=json.dumps(a.base)))
    # GitHub Pages otherwise runs Jekyll, which skips nothing here but costs
    # a build step.
    (a.out / ".nojekyll").write_text("")

    size = sum(p.stat().st_size for p in a.out.rglob("*") if p.is_file())
    print(f"{len(pages):,} forwarding pages -> {target}")
    print(f"{a.out}  ({size / 2**20:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
