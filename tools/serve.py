"""Static server for looking at the built site.

Plain `http.server` sends Last-Modified and no cache directives, so a browser
happily serves a stale page after a rebuild -- which looks exactly like a bug
that will not reproduce. This sends no-store on everything.

It also resolves `/county/51003/` to that directory's index.html, which is how
CloudFront will serve it via `deploy/index-rewrite.js`, so what you see here
is what the deployed site does.
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os

SITE = Path(os.environ.get("EXPLORER_SITE_DIR")
            or Path.home() / "Library" / "Caches" / "population-explorer" / "_site")


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request is noise at 4,000 pages; errors still surface.
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            super().log_message(fmt, *args)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    handler = partial(Handler, directory=str(SITE))
    print(f"serving {SITE} at http://localhost:{port}/  (no-store)")
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
