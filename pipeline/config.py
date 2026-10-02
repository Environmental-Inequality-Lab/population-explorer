"""Registry loader and upstream catalog reader.

Two sources of truth, deliberately separate:

  catalog/explorer.yaml   what the explorer SHOWS      (this repo)
  gridded-eif catalog     what the data IS             (fetched at runtime)

Nothing here hardcodes a category, a label, a year range, or a geography. If a
value can come from the upstream catalog it does, so that publishing new data
upstream never requires a change here.

The one thing this module does hardcode is the SHAPE of the contract: what a
resolved dataset looks like to a page builder. Everything above that line is
data.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "catalog" / "explorer.yaml"


class RegistryError(RuntimeError):
    """The registry asks for something the upstream catalog cannot provide."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

@cache
def registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text())


@cache
def upstream_catalog(url: str | None = None) -> dict:
    """Fetch gridded-eif's published catalog.

    Fetched, never vendored. A copy committed here would go stale the moment
    upstream publishes a year, which is the failure mode the whole runtime
    catalog design exists to avoid.
    """
    return fetch_bytes(url or registry()["upstream"]["catalog_url"], as_json=True)


def fetch_bytes(url: str, *, timeout: int = 120, attempts: int = 6, as_json: bool = False):
    """GET a published artifact, retrying transient failures.

    A connection that drops mid-body raises IncompleteRead, not URLError, and
    it happens often enough behind proxies that a 4,120-page build without a
    retry fails on its first download more often than not. A truncated body
    is never returned: it either arrives whole or this raises.
    """
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read()
            return json.loads(body) if as_json else body
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError,
                ConnectionError, json.JSONDecodeError):
            if i == attempts - 1:
                raise
            time.sleep(2 ** i)


def load_catalog_from_file(path: str | Path) -> dict:
    """Escape hatch for tests and offline work."""
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------------------
# The resolved shape a page builder consumes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDataset:
    id: str
    label: str
    kind: str                      # "upstream" | "external"
    geography: str
    family: str | None = None
    dimension: str | None = None
    residual_category: str | None = None
    caveat: str | None = None
    headline_noun: str | None = None
    never_call_it: tuple[str, ...] = ()
    # Populated for kind="upstream" only: where the data actually lives.
    url: str | None = None
    years: tuple[int, ...] = ()
    preliminary_years: tuple[int, ...] = ()
    known_gaps: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()
    headline_policy: str = "latest_final"

    @property
    def headline_year(self) -> int | None:
        """The year a page leads with, per the registry's vintage policy.

        `latest` takes the most recent year available; `latest_final` holds back
        to the newest non-preliminary vintage. Nothing here hardcodes a year —
        changing the policy is a one-line registry edit.
        """
        if not self.years:
            return None
        if self.headline_policy == "latest":
            return max(self.years)
        final = [y for y in self.years if y not in self.preliminary_years]
        return max(final) if final else None


@dataclass(frozen=True)
class ResolvedSection:
    id: str
    label: str
    datasets: tuple[ResolvedDataset, ...]
    family_note: str | None = None
    tab: str | None = None


@dataclass(frozen=True)
class ResolvedGeography:
    id: str
    label: str
    upstream: str
    compare_to: tuple[str, ...]
    url_prefix: str = ""
    nested_in: str | None = None
    coverage_note: str | None = None
    peer_set: dict | None = None
    sections: tuple[ResolvedSection, ...] = field(default=())


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _combined_entry(catalog: dict, dataset: str, geography: str) -> dict | None:
    for c in catalog.get("combined", []):
        if c["dataset"] == dataset and c["geography"] == geography:
            return c
    return None


def _preliminary_years(catalog: dict) -> set[int]:
    return {e["year"] for e in catalog.get("entries", []) if e.get("preliminary")}


def resolve_dataset(
    ds_id: str,
    geography: str,
    catalog: dict,
    *,
    force_enabled: bool = False,
) -> ResolvedDataset | None:
    """Resolve one declared dataset for one geography.

    Returns None when the dataset is disabled or not offered for this
    geography. Raises RegistryError when it is declared but the upstream
    catalog cannot serve it — a registry that promises data nobody publishes
    should fail loudly at build time, not render an empty section.
    """
    reg = registry()
    spec = reg["datasets"].get(ds_id)
    if spec is None:
        raise RegistryError(f"section references undeclared dataset {ds_id!r}")

    if not (spec.get("enabled") or force_enabled):
        return None
    if geography not in spec.get("geographies", []):
        return None

    common = {
        "id": ds_id,
        "label": spec["label"],
        "kind": spec["kind"],
        "geography": geography,
        "family": spec.get("family"),
        "dimension": spec.get("dimension"),
        "residual_category": (
            str(spec["residual_category"])
            if spec.get("residual_category") is not None else None
        ),
        "caveat": spec.get("caveat"),
        "headline_noun": spec.get("headline_noun"),
        "never_call_it": tuple(spec.get("never_call_it", ())),
        "known_gaps": tuple(spec.get("known_gaps", ())),
    }

    if spec["kind"] == "external":
        notes = []
        if geography in spec.get("boundary_note_required_for", []):
            notes.append(
                "Figures for this area are summed from whole counties, while "
                "population figures are derived from the underlying grid. The two "
                "differ slightly at the boundary."
            )
        if geography in spec.get("aggregates_to", []):
            notes.append(
                f"Aggregated from {spec['native_geography']} data."
            )
        return ResolvedDataset(**common, notes=tuple(notes))

    # kind == "upstream": bind it to a real published file.
    up = spec["upstream_dataset"]
    if up not in catalog.get("datasets", {}):
        raise RegistryError(f"{ds_id!r} wants upstream dataset {up!r}, not in the catalog")

    up_geo = reg["geographies"][geography]["upstream"]
    entry = _combined_entry(catalog, up, up_geo)
    if entry is None:
        raise RegistryError(
            f"{ds_id!r} wants {up}/{up_geo}, which the catalog does not publish"
        )

    excluded = set(reg["vintages"].get("exclude_years", []))
    years = tuple(sorted(y for y in entry["years"] if y not in excluded))
    prelim = tuple(sorted(_preliminary_years(catalog) & set(years)))

    dim = spec.get("dimension")
    if dim and dim not in catalog.get("dimensions", {}):
        raise RegistryError(f"{ds_id!r} uses dimension {dim!r}, not in the catalog")

    return ResolvedDataset(
        **common, url=entry["url"], years=years, preliminary_years=prelim,
        headline_policy=reg["vintages"].get("headline", "latest_final"),
    )


def resolve_sections(geography: str, catalog: dict, **kw) -> tuple[ResolvedSection, ...]:
    reg = registry()
    out = []
    for sec in reg["sections"]:
        resolved = tuple(
            d for d in (
                resolve_dataset(ds_id, geography, catalog, **kw)
                for ds_id in sec["datasets"]
            ) if d is not None
        )
        if not resolved:
            continue
        # A family note only earns its place when more than one member is live —
        # explaining why two numbers differ is noise when only one is shown.
        note = None
        families = {d.family for d in resolved if d.family}
        for fam in families:
            if sum(1 for d in resolved if d.family == fam) > 1:
                note = reg.get("families", {}).get(fam, {}).get("disambiguate")
        out.append(ResolvedSection(sec["id"], sec["label"], resolved, note, sec.get("tab")))
    return tuple(out)


def resolve_geography(geography: str, catalog: dict, **kw) -> ResolvedGeography:
    reg = registry()
    spec = reg["geographies"].get(geography)
    if spec is None:
        raise RegistryError(f"unknown geography {geography!r}")

    up_geo = spec["upstream"]
    up_spec = catalog.get("geographies", {}).get(up_geo)
    if up_spec is None:
        raise RegistryError(f"{geography!r} maps to {up_geo!r}, not in the catalog")

    # The coverage note is upstream's own text, never restated here — it is the
    # kind of caveat that must not drift between two products.
    note = None
    if spec.get("requires_coverage_note"):
        note = up_spec.get("caveat")
        if not note:
            raise RegistryError(
                f"{geography!r} requires a coverage note but the catalog supplies none"
            )

    return ResolvedGeography(
        id=geography,
        label=spec["label"],
        upstream=up_geo,
        compare_to=tuple(spec.get("compare_to", ())),
        url_prefix=spec.get("url_prefix", ""),
        nested_in=spec.get("nested_in"),
        coverage_note=note,
        peer_set=spec.get("peer_set"),
        sections=resolve_sections(geography, catalog, **kw),
    )


def tabs() -> tuple[tuple[str, str], ...]:
    """(id, label) for the page's tab strip, in declared order."""
    return tuple((t["id"], t["label"]) for t in registry().get("tabs", []))


def page_geographies() -> tuple[str, ...]:
    return tuple(registry()["geographies"])
