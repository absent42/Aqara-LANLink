"""Tests for the V3 gap-report builder.

The builder is pure: given a `{wire_path: value}` pushed map, a
`{wire_path: TraitSpec}` known map, and a model string, it returns a
GapReport with one GapEntry per uncovered wire path. Each entry carries
a V3-shaped proposed TraitSpec (id == wire_path, function_code / trait_code
populated when derivable from the live catalogue).
"""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink import gap_report
from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.gap_report import (
    GapEntry,
    build_gap_report,
    build_gap_report_from_cloud,
)


@pytest.fixture(autouse=True)
def _reset_gap_report_cache():
    """Force the function_id -> function_code cache to rebuild each test.

    The cache is module-level so a test that monkeypatches the registry
    after the cache is populated would otherwise see stale data.
    """
    gap_report._rebuild_fn_id_to_code()
    yield
    gap_report._rebuild_fn_id_to_code()


def _seed_fn_id(monkeypatch, *, fn_id: int, function_code: str,
                model: str = "lumi.test.seed") -> None:
    """Inject a synthetic TraitSpec into the registry so the V3 lookup
    map exposes `fn_id -> function_code`.

    The model packages do not yet carry V3 catalogue data, so tests that
    want a recognised function_code need to seed it. Combined with the
    autouse cache-reset fixture, each test starts from a clean state.
    """
    wire_path = f"2.{fn_id}.99999"
    spec = TraitSpec(
        id=wire_path,
        name="seed",
        wire_path=wire_path,
        function_code=function_code,
        endpoint_id=2,
    )
    monkeypatch.setattr(registry, "_discovered", True)
    registry._TRAIT_INDEX[(model, wire_path)] = spec
    gap_report._rebuild_fn_id_to_code()


# ---------------------------------------------------------------------------
# _build_fn_id_to_code: cloud-spec collisions resolve by majority
# ---------------------------------------------------------------------------


def test_fn_id_collision_resolves_by_majority(monkeypatch, caplog):
    """When the same function_id appears under multiple function_codes
    across the catalogue (a real Aqara V3 cloud-spec quirk -- e.g.
    fn_id=133 is LevelControl on every dimmable light but Output on
    lumi.plug.acn005 endpoint 3), the map should pick the most-common
    function_code and log the collision at DEBUG -- not WARNING. A
    catalogue regen reintroduces the collision, so this must not be a
    user-visible defect."""
    # Five LevelControl seeds, one Output seed for fn_id=133.
    for i in range(5):
        wire_path = f"2.133.{99000 + i}"
        spec = TraitSpec(
            id=wire_path, name=f"seed{i}", wire_path=wire_path,
            function_code="LevelControl", endpoint_id=2,
        )
        registry._TRAIT_INDEX[(f"lumi.test.dim{i}", wire_path)] = spec
    odd = TraitSpec(
        id="3.133.32920", name="seed_odd", wire_path="3.133.32920",
        function_code="Output", endpoint_id=3,
    )
    registry._TRAIT_INDEX[("lumi.test.oddplug", "3.133.32920")] = odd
    monkeypatch.setattr(registry, "_discovered", True)

    with caplog.at_level("DEBUG", logger="custom_components.aqara_lanlink.gap_report"):
        gap_report._rebuild_fn_id_to_code()
        result = gap_report._fn_id_to_code()

    assert result[133] == "LevelControl"
    # The collision is informational, not a WARNING.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not [w for w in warnings if "function_id" in w.getMessage()], (
        "fn_id collisions must not log at WARNING"
    )


# ---------------------------------------------------------------------------
# V3 build_gap_report (pushed/known signature)
# ---------------------------------------------------------------------------


def test_empty_pushed_yields_empty_report():
    report = build_gap_report({}, {}, model="lumi.test")
    assert report.entries == []


def test_known_wire_paths_are_filtered_out():
    """A wire path already in `known` does not appear in the gap report."""
    spec = TraitSpec(
        id="2.160.33000", name="Existing", wire_path="2.160.33000",
        data_type="bool", endpoint_id=2,
    )
    known = {"2.160.33000": spec}
    pushed = {"2.160.33000": "1"}
    report = build_gap_report(pushed, known, model="lumi.test")
    assert report.entries == []


def test_gap_report_proposes_v3_trait_spec_with_wire_path_key(monkeypatch):
    """Trait the device pushed at wire path 2.160.33000 (Occupancy) appears
    in the gap report as a V3-shaped TraitSpec proposal."""
    _seed_fn_id(monkeypatch, fn_id=160, function_code="OccupancySensing")

    pushed = {"2.160.33000": "1"}
    report = build_gap_report(pushed, {}, model="lumi.test.unknown")
    proposals = {p.wire_path: p for p in (e.proposed_spec for e in report.entries)}
    assert "2.160.33000" in proposals
    p = proposals["2.160.33000"]
    assert p.endpoint_id == 2
    assert p.function_code == "OccupancySensing"
    assert p.id == p.wire_path  # V3 contract: id is the wire path


def test_gap_report_proposes_v3_drops_propertyid_keying(monkeypatch):
    """A pushed wire path is NEVER mapped to a propertyId-shape id."""
    _seed_fn_id(monkeypatch, fn_id=160, function_code="OccupancySensing")
    pushed = {"2.160.33000": "1"}
    report = build_gap_report(pushed, {}, model="lumi.test.x")
    assert report.entries
    for entry in report.entries:
        p = entry.proposed_spec
        assert "." in p.id  # V3 wire path, not a 1.x.y propertyId form
        # V3 contract: id is exactly the wire_path.
        assert p.id == p.wire_path


def test_propose_trait_spec_unknown_function_keeps_codes_none():
    """An unrecognised function_id leaves function_code / trait_code None."""
    from custom_components.aqara_lanlink.gap_report import _propose_trait_spec
    spec = _propose_trait_spec(
        wire_path="9.99.99999", value="1", model="lumi.test.x",
    )
    assert spec is not None
    assert spec.wire_path == "9.99.99999"
    assert spec.endpoint_id == 9
    assert spec.function_code is None
    assert spec.trait_code is None
    assert "unrecognised_wire_path" in spec.sources


def test_propose_trait_spec_known_function_omits_unrecognised_marker(monkeypatch):
    """A recognised function_id drops the 'unrecognised_wire_path' marker."""
    _seed_fn_id(monkeypatch, fn_id=160, function_code="OccupancySensing")
    from custom_components.aqara_lanlink.gap_report import _propose_trait_spec
    spec = _propose_trait_spec(
        wire_path="2.160.33000", value="1", model="lumi.test.x",
    )
    assert spec is not None
    assert spec.function_code == "OccupancySensing"
    assert "unrecognised_wire_path" not in spec.sources
    assert "observed" in spec.sources


def test_empty_wire_path_is_skipped():
    """Empty-string wire paths are not surfaced (defensive)."""
    pushed = {"": "1", "2.160.33000": "1"}
    report = build_gap_report(pushed, {}, model="lumi.test.x")
    assert all(e.wire_path for e in report.entries)


def test_entries_sorted_by_wire_path():
    pushed = {
        "5.10.40000": "1",
        "2.160.33000": "1",
        "3.20.32000": "2",
    }
    report = build_gap_report(pushed, {}, model="lumi.test.x")
    paths = [e.wire_path for e in report.entries]
    assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# Data-type inference helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("0", "bool"),
    ("1", "bool"),
    ("true", "bool"),
    ("False", "bool"),
    ("42", "number"),
    ("23.5", "number"),
    ("-7", "number"),
    ("hello", "string"),
    ("", "string"),
])
def test_infer_data_type(value, expected):
    from custom_components.aqara_lanlink.gap_report import _infer_data_type
    assert _infer_data_type(value) == expected


# ---------------------------------------------------------------------------
# Cloud-shape adapter (build_gap_report_from_cloud)
# ---------------------------------------------------------------------------


def test_adapter_filters_catalogued_wire_paths():
    """A wire path in the catalogue is filtered out via the cloud adapter."""
    catalogue = {
        "0.1.85": TraitSpec(
            id="0.1.85", name="Temp", data_type="number",
            wire_path="4.143.32952",
        ),
    }
    traits_response = [
        {"path": "4.143.32952", "propertyId": ["0.1.85"], "value": "23.5"},
    ]
    report = build_gap_report_from_cloud(
        panels={}, traits_response=traits_response,
        catalogue=catalogue, overlay=Overlay(), model="lumi.test",
    )
    assert report.entries == []


def test_adapter_truly_novel_trait_appears_as_v3_proposal():
    """A trait whose wire_path is in neither catalogue nor overlay appears
    in the report as a V3-shaped proposal."""
    traits_response = [
        {
            "path": "4.143.32952",
            "propertyId": ["0.1.85"],
            "value": "23.5",
        },
    ]
    report = build_gap_report_from_cloud(
        panels={}, traits_response=traits_response,
        catalogue={}, overlay=Overlay(), model="lumi.test",
    )
    assert len(report.entries) == 1
    entry = report.entries[0]
    assert isinstance(entry, GapEntry)
    assert entry.wire_path == "4.143.32952"
    assert entry.pid == "4.143.32952"  # V3: pid == wire_path
    assert entry.proposed_spec.id == "4.143.32952"
    assert entry.proposed_spec.wire_path == "4.143.32952"
    assert entry.cloud_value == "23.5"


def test_adapter_overlay_traits_filtered_by_wire_path():
    """An overlay entry whose TraitSpec carries `wire_path` filters out the
    matching cloud trait."""
    from datetime import datetime, timezone
    overlay = Overlay()
    overlay.set_trait(
        model="lumi.test",
        pid="4.143.32952",
        spec=TraitSpec(
            id="4.143.32952", name="Already accepted",
            data_type="number", wire_path="4.143.32952",
        ),
        discovered_from="cloud_scan",
        discovered_at=datetime.now(timezone.utc),
    )
    traits_response = [
        {"path": "4.143.32952", "propertyId": ["0.1.85"], "value": "23.5"},
    ]
    report = build_gap_report_from_cloud(
        panels={}, traits_response=traits_response,
        catalogue={}, overlay=overlay, model="lumi.test",
    )
    assert report.entries == []


def test_adapter_trait_without_path_is_skipped():
    """Cloud traits without a path cannot be V3-keyed; not surfaced."""
    traits_response = [{"propertyId": ["0.1.85"], "value": "23.5"}]
    report = build_gap_report_from_cloud(
        panels={}, traits_response=traits_response,
        catalogue={}, overlay=Overlay(), model="lumi.test",
    )
    assert report.entries == []


# ---------------------------------------------------------------------------
# _propose_name: function_code humanised, else auto_<wire_path> fallback
# ---------------------------------------------------------------------------


def test_propose_name_humanises_function_code():
    """Tier 1: a resolvable function_code is humanised into the name."""
    from custom_components.aqara_lanlink import gap_report
    name = gap_report._propose_name(
        wire_path="1.140.424242", function_code="BatteryPercentage",
    )
    assert name == "Battery percentage"


def test_propose_name_unrecognised_path_falls_back_to_auto():
    """Tier 2: no function_code -> stable auto_<wire_path> fallback."""
    from custom_components.aqara_lanlink import gap_report
    name = gap_report._propose_name(
        wire_path="2.132.20101", function_code=None,
    )
    assert name == "auto_2.132.20101"
