"""What the publish step promises, without touching AWS.

Everything here is a pure function over paths, so it runs in CI with no
credentials. The parts that need S3 are the two calls that wrap these.
"""

import json
from pathlib import Path

import pytest

from pipeline import publish


def test_html_is_served_as_html_not_downloaded():
    """S3 guesses badly, so the type is set explicitly.

    A page served as application/octet-stream downloads instead of rendering,
    and a stylesheet served as text/plain is ignored by the browser entirely.
    """
    assert publish.content_type(Path("county/51003/index.html")) == "text/html; charset=utf-8"
    assert publish.content_type(Path("assets/explorer.2a2b2faa.css")) == "text/css; charset=utf-8"
    assert publish.content_type(Path("places.json")) == "application/json"
    assert publish.content_type(Path("assets/eil-logo.webp")) == "image/webp"


def test_pages_are_not_cached_for_long():
    """`/county/51003/` is a stable URL whose CONTENTS change on a refresh.

    Nothing here is content-hashed, so a long TTL would serve last year's
    population until it expired. Assets change rarely and may sit longer.
    """
    page = publish.cache_control("county/51003/index.html")
    asset = publish.cache_control("assets/explorer.2a2b2faa.css")
    assert "max-age=300" in page and "must-revalidate" in page
    assert "immutable" not in page, "a rewritable page was marked immutable"
    # The stylesheet's name carries its content hash, so that URL's bytes
    # never change and it may be cached hard.
    assert "immutable" in asset


def test_only_changed_files_upload(tmp_path):
    """A rebuild rewrites every page; most are byte-identical to what is live."""
    site = tmp_path / "_site"
    (site / "county" / "51003").mkdir(parents=True)
    same = site / "county" / "51003" / "index.html"
    same.write_text("<html>unchanged</html>")
    changed = site / "index.html"
    changed.write_text("<html>new</html>")

    existing = {
        "county/51003/index.html": publish._md5(same),
        "index.html": "0" * 32,
        "county/99999/index.html": "0" * 32,      # a place that no longer exists
    }
    upload, stale = publish.plan(site, existing)
    assert [p.name for p in upload] == ["index.html"], "an unchanged page re-uploaded"
    assert stale == ["county/99999/index.html"], "a removed place was left published"


def test_a_removed_place_stops_resolving(tmp_path):
    """A URL that should no longer exist must 404, not serve stale data."""
    site = tmp_path / "_site"
    site.mkdir()
    (site / "index.html").write_text("x")
    _upload, stale = publish.plan(site, {"county/02016/index.html": "abc"})
    assert "county/02016/index.html" in stale


def test_invalidation_covers_the_directory_url(tmp_path):
    """Readers hold `/county/51003/`, not `/county/51003/index.html`.

    Invalidating only the object key would leave the URL people actually
    visit serving the old page.
    """
    site = tmp_path / "_site"
    (site / "county" / "51003").mkdir(parents=True)
    p = site / "county" / "51003" / "index.html"
    p.write_text("x")
    paths = publish.invalidation_paths([p], [], site)
    assert "/county/51003/" in paths, "the directory url was not invalidated"
    assert "/county/51003/index.html" in paths


def test_a_big_publish_invalidates_everything_once(tmp_path):
    """Past a thousand paths CloudFront charges per path, so name none of them."""
    site = tmp_path / "_site"
    site.mkdir()
    many = []
    for i in range(1200):
        d = site / "county" / f"{i:05d}"
        d.mkdir(parents=True)
        f = d / "index.html"
        f.write_text("x")
        many.append(f)
    assert publish.invalidation_paths(many, [], site) == ["/*"]


def test_a_partial_build_does_not_rewrite_the_site_index():
    """Rebuilding one page must not truncate the whole-site files.

    `index.html`, `places.html` and `places.json` describe every place on the
    site. Writing them from a single-page build reduced `places.json` to one
    entry and the front page to "Loading 1 places" -- a build bug that
    presented as a front-end one.
    """
    from pathlib import Path

    import pipeline.cli as cli

    src = Path(cli.__file__).read_text()
    guard = "if a.all and not a.limit and not a.geography:"
    assert guard in src, "the whole-site files are written unconditionally"
    # The guard must come before the writes it protects.
    for name in ("index.html", "places.html"):
        assert src.index(guard) < src.index(f'"{name}"', src.index(guard)), (
            f"{name} is written outside the whole-site guard"
        )
    assert src.index(guard) < src.index("write_search_index(built)", src.index(guard))


def test_a_partial_build_cannot_prune_the_published_site(tmp_path):
    # Pruning deletes whatever the local build lacks, so a --slice build must
    # never be allowed to prune: it would take down every other place.
    site = tmp_path / "_site"
    (site / "county/51003").mkdir(parents=True)
    (site / "county/51003/index.html").write_text("x")
    with pytest.raises(SystemExit, match="not a whole-site build"):
        publish.check_complete(site)

    (site / "places.json").write_text(
        json.dumps([["county/51003/", "County", "A"], ["county/51005/", "County", "B"]]))
    with pytest.raises(SystemExit, match="1 pages"):
        publish.check_complete(site)

    (site / "county/51005").mkdir(parents=True)
    (site / "county/51005/index.html").write_text("x")
    publish.check_complete(site)

