"""Registry consistency, and the contract that keeps it a registry.

The claim `catalog/explorer.yaml` makes about itself is that adding a
geography, a section, or a dataset is a config edit. The load-bearing test in
this file is `test_enabling_income_soi_needs_no_code_change`: it enables a
dataset that has no implementation, no branch, and no component, and asserts a
complete page spec comes back. If that test ever needs a code change to pass,
the registry has stopped being a registry.

Most tests run against a committed snapshot of the upstream catalog so they
work offline and deterministically. One test hits the live catalog to check
the snapshot has not drifted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline import config
from pipeline.config import RegistryError

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "upstream-catalog.json"
GOLDEN = FIXTURES / "golden-figures.json"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return config.load_catalog_from_file(SNAPSHOT)


@pytest.fixture(scope="module")
def reg() -> dict:
    return config.registry()


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def test_enabling_income_soi_needs_no_code_change(catalog):
    """The whole point of phase 03, as an assertion.

    `income_soi` is declared, disabled, and entirely unimplemented — there is
    no module for it, no branch that names it, and no component. Enabling it
    must produce a complete, renderable dataset spec for every geography it
    claims, purely from its declaration.

    If this test starts failing because something needs writing, the income
    family was modelled wrong and phase 07 just became expensive.
    """
    for geo in ("county", "state", "cbsa"):
        page = config.resolve_geography(geo, catalog, force_enabled=True)
        income = next(s for s in page.sections if s.id == "income")
        ids = [d.id for d in income.datasets]

        assert "income_soi" in ids, f"{geo}: enabling income_soi produced nothing"
        soi = next(d for d in income.datasets if d.id == "income_soi")
        assert soi.label and soi.caveat and soi.headline_noun, (
            f"{geo}: income_soi resolved without the fields a page needs"
        )
        assert soi.family == "income"

    # And with both members live, the family note appears to explain why two
    # income numbers can differ.
    page = config.resolve_geography("county", catalog, force_enabled=True)
    income = next(s for s in page.sections if s.id == "income")
    assert len(income.datasets) == 2
    assert income.family_note and "different questions" in income.family_note


def test_family_note_stays_quiet_while_only_one_member_is_live(catalog):
    """Explaining a discrepancy nobody can see is noise."""
    page = config.resolve_geography("county", catalog)
    income = next(s for s in page.sections if s.id == "income")
    assert [d.id for d in income.datasets] == ["income_composition"]
    assert income.family_note is None


def test_metro_soi_carries_its_boundary_note(catalog):
    """CBSA is the one geography where two derivations meet.

    Upstream metro figures come from point-in-polygon on grid cells; an
    external county source summed to metro is exact by OMB definition. They
    will differ slightly at the boundary, and the page has to say so.
    """
    page = config.resolve_geography("cbsa", catalog, force_enabled=True)
    soi = next(d for s in page.sections for d in s.datasets if d.id == "income_soi")
    assert any("boundary" in n.lower() for n in soi.notes), soi.notes

    county = config.resolve_geography("county", catalog, force_enabled=True)
    soi_c = next(d for s in county.sections for d in s.datasets if d.id == "income_soi")
    assert not any("boundary" in n.lower() for n in soi_c.notes), (
        "county is SOI's native geography — no boundary reconciliation applies"
    )


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------

def test_every_section_references_a_declared_dataset(reg):
    declared = set(reg["datasets"])
    for sec in reg["sections"]:
        for ds_id in sec["datasets"]:
            assert ds_id in declared, f"section {sec['id']!r} references undeclared {ds_id!r}"


def test_every_declared_dataset_is_reachable_from_a_section(reg):
    """An unreferenced dataset is dead config, which rots exactly like dead code."""
    referenced = {d for sec in reg["sections"] for d in sec["datasets"]}
    orphans = set(reg["datasets"]) - referenced
    assert not orphans, f"declared but never shown: {sorted(orphans)}"


def test_every_family_member_declares_that_family(reg):
    for fam in reg.get("families", {}):
        members = [k for k, v in reg["datasets"].items() if v.get("family") == fam]
        assert len(members) >= 2, (
            f"family {fam!r} has {len(members)} member(s) — a family of one is just "
            "a dataset, and its disambiguation note will never render"
        )


def test_undeclared_dataset_fails_loudly(catalog):
    reg = config.registry()
    reg["sections"].append({"id": "x", "label": "X", "datasets": ["nope"]})
    try:
        with pytest.raises(RegistryError, match="undeclared"):
            config.resolve_sections("county", catalog)
    finally:
        reg["sections"].pop()


# ---------------------------------------------------------------------------
# Agreement with upstream
# ---------------------------------------------------------------------------

def test_every_page_geography_exists_upstream(catalog, reg):
    for geo, spec in reg["geographies"].items():
        assert spec["upstream"] in catalog["geographies"], (
            f"{geo!r} maps to upstream {spec['upstream']!r}, which is not published"
        )


def test_every_upstream_dataset_and_dimension_exists(catalog, reg):
    for ds_id, spec in reg["datasets"].items():
        if spec["kind"] != "upstream":
            continue
        assert spec["upstream_dataset"] in catalog["datasets"], ds_id
        if spec.get("dimension"):
            assert spec["dimension"] in catalog["dimensions"], ds_id


def test_residual_categories_match_the_published_dimension(catalog, reg):
    """A residual the data does not contain would silently never render."""
    for ds_id, spec in reg["datasets"].items():
        dim = spec.get("dimension")
        if dim is None or spec["kind"] != "upstream":
            continue
        codes = {str(v["code"]) for v in catalog["dimensions"][dim]["values"]}
        for key in ("residual_category", "excluded_from_shares"):
            named = spec.get(key)
            if named is None:
                continue
            assert str(named) in codes, (
                f"{ds_id!r} names {key} {named!r}, absent from published "
                f"{dim!r} values {sorted(codes)}"
            )


def test_income_excludes_its_residual_and_must_disclose_it(reg):
    """The one dimension that excludes rather than displays, and why.

    Deciles partition the income distribution, not the population, so decile 0
    is outside the universe rather than a member of it. The old site excluded
    it too — correctly — but never said so on the page. Disclosure is what
    makes the exclusion honest, so it is required rather than optional.
    """
    inc = reg["datasets"]["income_composition"]
    assert inc.get("excluded_from_shares") == "0"
    assert "residual_category" not in inc, (
        "income must not declare a displayed residual — decile 0 is excluded "
        "from the shares, not shown among them"
    )
    assert inc["denominator_universe"] == "residents with an income record"
    assert inc["coverage_disclosure_required"] is True

    # And the population partitions must NOT adopt the exclusion.
    for ds_id in ("age_structure", "race_ethnicity"):
        spec = reg["datasets"][ds_id]
        assert spec.get("residual_category"), f"{ds_id} must display its residual"
        assert "excluded_from_shares" not in spec, (
            f"{ds_id}: a population residual belongs in the denominator — "
            "the person is there, the value is unknown"
        )


def test_coverage_note_is_taken_from_upstream_never_restated(catalog):
    """The caveat must not drift between the two products.

    Metro and micropolitan areas do not tile the country; 5.2% of the
    population lives outside any of them. Snapping those cells to the nearest
    metro once inflated the national CBSA total from 303.7M to 317.3M.
    """
    # Switched off for metros in the registry; when a geography asks for one,
    # it must be upstream's text verbatim.
    for geo, spec in config.registry()["geographies"].items():
        note = config.resolve_geography(geo, catalog).coverage_note
        if spec.get("requires_coverage_note"):
            assert note == catalog["geographies"][spec["upstream"]]["caveat"]
        else:
            assert note is None


def test_missing_upstream_data_fails_at_build_time(catalog):
    """A registry promising data nobody publishes should not render an empty section."""
    stripped = json.loads(json.dumps(catalog))
    stripped["combined"] = [c for c in stripped["combined"] if c["dataset"] != "raceincome"]
    with pytest.raises(RegistryError, match="does not publish"):
        config.resolve_sections("county", stripped)


# ---------------------------------------------------------------------------
# Vintages
# ---------------------------------------------------------------------------

def test_headline_year_follows_the_declared_vintage_policy(catalog, reg):
    """Whichever policy is declared, every dataset must actually follow it.

    `latest` takes the newest year available; `latest_final` holds back to the
    newest non-preliminary vintage. The failure this guards against is not
    either choice — it is a resolver that ignores the registry and picks a year
    of its own, which is how the old site ended up on a preliminary vintage
    without anyone deciding to.
    """
    policy = reg["vintages"]["headline"]
    assert policy in ("latest", "latest_final")
    for geo in config.page_geographies():
        page = config.resolve_geography(geo, catalog)
        for sec in page.sections:
            for ds in sec.datasets:
                if ds.kind != "upstream":
                    continue
                assert ds.headline_year is not None, f"{geo}/{ds.id}: no headline year"
                if policy == "latest":
                    assert ds.headline_year == max(ds.years), (
                        f"{geo}/{ds.id}: policy is `latest` but the headline is "
                        f"{ds.headline_year}, not {max(ds.years)}"
                    )
                else:
                    assert ds.headline_year not in ds.preliminary_years, (
                        f"{geo}/{ds.id}: headline {ds.headline_year} is preliminary"
                    )
                # Recorded either way, so a page can always say which vintage it used.
                assert ds.preliminary_years, (
                    f"{geo}/{ds.id}: expected 2025 to be flagged preliminary upstream"
                )


def test_1999_is_excluded_from_every_resolved_series(catalog):
    """~70% administrative completeness against 85-96% for every later year."""
    for geo in config.page_geographies():
        page = config.resolve_geography(geo, catalog)
        for sec in page.sections:
            for ds in sec.datasets:
                assert 1999 not in ds.years, f"{geo}/{ds.id} includes 1999"


def test_the_year_exclusion_actually_filters(catalog):
    """Exercise the filter against a catalog that really does offer 1999.

    Upstream already drops 1999, so the test above passes whether or not this
    repo filters anything — it was vacuous until this one existed. Two
    independent exclusions are worth having: upstream's is a judgement about
    the source, and ours is a guarantee about what we publish. Neither should
    rely on the other.
    """
    permissive = json.loads(json.dumps(catalog))
    for c in permissive["combined"]:
        c["years"] = sorted({1999, *c["years"]})

    for geo in config.page_geographies():
        page = config.resolve_geography(geo, permissive)
        upstream_datasets = [d for s in page.sections for d in s.datasets
                             if d.kind == "upstream"]
        assert upstream_datasets, geo
        for ds in upstream_datasets:
            assert 1999 not in ds.years, (
                f"{geo}/{ds.id}: 1999 offered upstream and not filtered here"
            )
            assert ds.years, f"{geo}/{ds.id}: filtering removed everything"


def test_known_gaps_are_declared_where_the_page_must_explain_one(reg):
    """The 2024 income residual gap has to be explicable on the page itself."""
    gaps = reg["datasets"]["income_composition"].get("known_gaps", [])
    gap = next((g for g in gaps if g["year"] == 2024), None)
    assert gap is not None, "the 2024 decile 0 gap is not declared"
    assert str(gap["category"]) == "0"

    note = gap["note"].lower()
    # A reader seeing a hole in a chart will conclude people went missing. The
    # note has to do two things: say they are counted, and say where they are.
    assert "counted" in note, "the note must say those residents are still counted"
    assert "deciles 1 to 10" in note or "deciles 1-10" in note, (
        "the note must say WHERE those residents are, not only that the category "
        f"is absent. Got: {gap['note']!r}"
    )


# ---------------------------------------------------------------------------
# Agreement with the golden fixture
# ---------------------------------------------------------------------------

def test_registry_conventions_match_the_pinned_ones(reg, golden):
    """The fixture holds the numbers; the registry holds the rules. Both, or neither."""
    conv, pinned = reg["conventions"], golden["conventions"]
    assert conv["measure"] == "n_noise_postprocessed"
    assert conv["measure"] in pinned["measure"]["rule"]
    assert conv["measure_is_deviation_from_source_guide"] is True
    assert pinned["measure"]["deviation_from_source_guide"] is True
    assert conv["uncertainty_band"] == "n_noise"
    assert conv["residuals"] == "include_in_denominator_and_display"
    for dim in ("age_shares", "race_shares"):
        assert "includes" in pinned[dim]["rule"], (
            f"registry says population residuals are included, but the pinned "
            f"{dim} rule says: {pinned[dim]['rule']}"
        )
    assert conv["income_residual"] == "exclude_from_shares_and_report_as_coverage"
    assert "deciles 1-10 only" in pinned["income_decile_shares"]["rule"]
    assert "coverage" in pinned["income_decile_shares"]["rule"]


def test_registry_residuals_match_the_pinned_ones(reg, golden):
    for ds_id, conv_key in [("age_structure", "age_shares"),
                            ("race_ethnicity", "race_shares")]:
        declared = str(reg["datasets"][ds_id]["residual_category"])
        pinned = str(golden["conventions"][conv_key]["residual_category"])
        assert declared == pinned, f"{ds_id}: registry {declared!r} vs fixture {pinned!r}"

    inc_reg, inc_pin = reg["datasets"]["income_composition"], golden["conventions"]["income_decile_shares"]
    assert str(inc_reg["excluded_from_shares"]) == str(inc_pin["excluded_from_shares"])
    assert inc_reg["denominator_universe"] == inc_pin["denominator_universe"]


def test_income_composition_never_emits_dollars(reg):
    """The composition index may not state a dollar figure. Structurally.

    The old site published "the average household AGI in Arlington was
    $263,433". That number is the county's decile mix multiplied by NATIONAL
    decile means — two places with the same mix report the same dollars no
    matter what anyone earns. Its growth rate was worse still: national
    decile-mean growth reweighted by drift in local composition, presented as
    local income growth.

    A caveat does not prevent that; a caveat is what the reader skips. This
    does. Dollars are `income_soi`'s to state, because SOI measures them.
    """
    comp = reg["datasets"]["income_composition"]
    forbidden = set(comp["must_not_emit"])
    assert {"dollar_amount", "dollar_growth_rate"} <= forbidden
    assert not (set(comp["emits"]) & forbidden), (
        "income_composition both emits and forbids the same output"
    )
    assert "decile_shares" in comp["emits"], (
        "shares are the measured thing — they must survive the restriction"
    )
    assert comp["example_claims"], (
        "forbidding the dollar figure is only half the job; the registry has to "
        "show what CAN be said, or a page author will reach for the dollars"
    )

    soi = reg["datasets"]["income_soi"]
    assert "dollar_amount" in soi["emits"], (
        "SOI measures dollars — it is the dataset that may state them"
    )


def test_no_example_claim_says_something_the_dataset_may_not(reg):
    """The examples have to obey the same rule they illustrate."""
    comp = reg["datasets"]["income_composition"]
    banned = {p.lower() for p in comp["never_call_it"]}
    for claim in comp["example_claims"]:
        low = claim.lower()
        assert "$" not in claim, f"example claim states dollars: {claim!r}"
        for phrase in banned:
            assert phrase not in low, f"example claim uses {phrase!r}: {claim!r}"


def test_income_composition_is_never_labelled_as_measured_income(reg):
    """Honest labelling, as an assertion rather than an intention.

    The old site published this as "the average household AGI in X was $Y".
    It is a distributional profile: two places with the same decile mix report
    the same figure regardless of what anyone actually earns.
    """
    spec = reg["datasets"]["income_composition"]
    banned = {p.lower() for p in spec["never_call_it"]}
    assert {"average income", "median income", "household income",
            "average household agi", "income growth"} <= banned
    surfaces = " ".join([spec["label"], spec["headline_noun"], spec["caveat"]]).lower()
    for phrase in banned:
        assert phrase not in surfaces, (
            f"income_composition uses the phrase it forbids: {phrase!r}"
        )
    assert "not a measure of what people here earn" in spec["caveat"]


# ---------------------------------------------------------------------------
# Snapshot freshness
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("OFFLINE"), reason="OFFLINE set")
def test_snapshot_still_matches_the_live_catalog_structurally(catalog):
    """The snapshot is for speed, not for pretending upstream is frozen.

    Only structure is compared. New years and new entries are expected and
    fine; a renamed dimension or a dropped geography is not.
    """
    try:
        live = config.upstream_catalog()
    except Exception as e:  # noqa: BLE001 — offline, DNS, CDN hiccup
        pytest.skip(f"live catalog unreachable: {e}")

    assert live["derived_version"] == catalog["derived_version"]
    assert set(live["dimensions"]) == set(catalog["dimensions"])
    assert set(live["measures"]) == set(catalog["measures"])
    needed = {config.registry()["geographies"][g]["upstream"]
              for g in config.page_geographies()}
    assert set(live["geographies"]) >= needed
    for dim, spec in catalog["dimensions"].items():
        assert {v["code"] for v in live["dimensions"][dim]["values"]} == \
               {v["code"] for v in spec["values"]}, f"{dim} categories changed upstream"


@pytest.mark.skipif(os.environ.get("OFFLINE"), reason="OFFLINE set")
def test_registry_records_the_upstream_version_it_was_validated_against(reg):
    """A MAJOR bump upstream rebuilds every partition and moves the golden figures.

    This does not fail on a bump — the fixture does that. It fails when the
    recorded version was never updated after someone re-baselined, leaving no
    record of which build the numbers came from.
    """
    try:
        live = config.upstream_catalog()
    except Exception as e:  # noqa: BLE001 — any failure here means skip, not fail
        pytest.skip(f"live catalog unreachable: {e}")
    recorded = reg["upstream"]["validated_against_pipeline_version"]
    assert recorded == live["pipeline_version"], (
        f"registry records upstream {recorded}, live catalog is "
        f"{live['pipeline_version']} — re-run the golden fixture, confirm what "
        "moved, then update this field deliberately"
    )
