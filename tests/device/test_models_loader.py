"""Unit tests for the JSON catalogue loader."""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from custom_components.aqara_lanlink.device.models import _loader
from custom_components.aqara_lanlink.device.traits import TraitSpec


@pytest.fixture
def fake_package(tmp_path: Path) -> Path:
    """Build a minimal model-package directory with data.json + overrides.py."""
    pkg = tmp_path / "fake_model"
    pkg.mkdir()
    (pkg / "data.json").write_text(json.dumps({
        "model": "lumi.test.fake",
        "models": ["lumi.test.fake"],
        "manufacturer": "Aqara",
        "display_name": "Fake Test Model",
        "regions": ["EU"],
        "bundle_ids": [],
        "source_region": "ger",
        "endpoints": {
            "0": {"deviceType": "Root"},
            "2": {"deviceType": "OccupancySensor"},
        },
        "device_types": ["OccupancySensor"],
        "traits": {
            "0.128.32904": {
                "function_code": "BasicInformation", "trait_code": "Mac",
                "name": "Mac", "data_type": "string",
                "readable": True, "subscribable": True, "endpoint_id": 0,
                "entity_category": "diagnostic", "default_enabled": False,
            },
            "2.160.33000": {
                "function_code": "OccupancySensing", "trait_code": "Occupancy",
                "name": "Occupancy", "data_type": "bool",
                "readable": True, "subscribable": True, "endpoint_id": 2,
            },
        },
    }))
    (pkg / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {}
    """).strip())
    return pkg


def test_load_returns_standard_contract(fake_package: Path):
    data = _loader.load_model_data(fake_package)
    assert data["MODELS"] == ("lumi.test.fake",)
    assert data["MANUFACTURER"] == "Aqara"
    assert data["DISPLAY_NAME"] == "Fake Test Model"
    assert data["REGIONS"] == ("EU",)
    assert data["BUNDLE_IDS"] == ()
    assert data["DEVICE_TYPES"] == ("OccupancySensor",)


def test_load_materialises_endpoints_with_int_keys(fake_package: Path):
    data = _loader.load_model_data(fake_package)
    assert data["ENDPOINTS"] == {
        0: {"deviceType": "Root"},
        2: {"deviceType": "OccupancySensor"},
    }


def test_load_materialises_traits_as_traitspec_instances(fake_package: Path):
    data = _loader.load_model_data(fake_package)
    traits = data["TRAITS"]
    assert isinstance(traits["2.160.33000"], TraitSpec)
    occ = traits["2.160.33000"]
    assert occ.id == "2.160.33000"
    assert occ.wire_path == "2.160.33000"
    assert occ.function_code == "OccupancySensing"
    assert occ.trait_code == "Occupancy"
    assert occ.endpoint_id == 2
    assert occ.subscribable is True
    assert occ.readable is True


def test_load_propagates_diagnostic_fields(fake_package: Path):
    data = _loader.load_model_data(fake_package)
    mac = data["TRAITS"]["0.128.32904"]
    assert mac.entity_category == "diagnostic"
    assert mac.default_enabled is False


def test_overrides_replace_an_entry(fake_package: Path):
    (fake_package / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {
            "2.160.33000": TraitSpec(
                id="2.160.33000", wire_path="2.160.33000",
                function_code="OccupancySensing", trait_code="Occupancy",
                name="Custom occupancy name", data_type="bool",
                readable=True, subscribable=True, endpoint_id=2,
            ),
        }
    """).strip())
    data = _loader.load_model_data(fake_package)
    occ = data["TRAITS"]["2.160.33000"]
    assert occ.name == "Custom occupancy name"


def test_overrides_fill_wire_path_from_key_when_omitted(fake_package: Path):
    # A maintainer-added override that omits wire_path must still get one (from
    # the dict key) so subscribe+seed treats it identically to a data.json trait.
    (fake_package / "overrides.py").write_text(dedent('''
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {
            "2.143.32952": TraitSpec(
                id="2.143.32952", name="Smile", data_type="bool",
                readable=True, subscribable=True,
            ),
        }
    ''').strip())
    data = _loader.load_model_data(fake_package)
    smile = data["TRAITS"]["2.143.32952"]
    assert smile.wire_path == "2.143.32952"


def test_overrides_preserve_explicit_wire_path(fake_package: Path):
    (fake_package / "overrides.py").write_text(dedent('''
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {
            "2.143.32952": TraitSpec(
                id="2.143.32952", wire_path="9.9.99999", name="X", data_type="bool",
            ),
        }
    ''').strip())
    data = _loader.load_model_data(fake_package)
    assert data["TRAITS"]["2.143.32952"].wire_path == "9.9.99999"


def test_overrides_none_drops_an_entry(fake_package: Path):
    (fake_package / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {"2.160.33000": None}
    """).strip())
    data = _loader.load_model_data(fake_package)
    assert "2.160.33000" not in data["TRAITS"]
    assert "0.128.32904" in data["TRAITS"]


def test_missing_overrides_file_is_ok(fake_package: Path):
    (fake_package / "overrides.py").unlink()
    data = _loader.load_model_data(fake_package)
    assert "2.160.33000" in data["TRAITS"]


def test_corrupt_data_json_raises_loudly(fake_package: Path):
    (fake_package / "data.json").write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        _loader.load_model_data(fake_package)


def test_trait_with_unknown_field_raises_at_load_time(fake_package: Path):
    raw = json.loads((fake_package / "data.json").read_text())
    raw["traits"]["2.160.33000"]["bogus_field"] = 42
    (fake_package / "data.json").write_text(json.dumps(raw))
    with pytest.raises(TypeError):
        _loader.load_model_data(fake_package)


def test_classification_annotation_in_data_json_raises(fake_package: Path):
    raw = json.loads((fake_package / "data.json").read_text())
    raw["traits"]["2.160.33000"]["_classification"] = "visible"
    (fake_package / "data.json").write_text(json.dumps(raw))
    with pytest.raises(TypeError):
        _loader.load_model_data(fake_package)
