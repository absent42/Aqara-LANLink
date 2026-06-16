"""Unit tests for the loader's settings + power_class parsing (Task 2a)."""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from custom_components.aqara_lanlink.device.models import _loader
from custom_components.aqara_lanlink.device.settings import SettingSpec


@pytest.fixture
def settings_package(tmp_path: Path) -> Path:
    """Build a minimal model package with a settings block + power_class."""
    pkg = tmp_path / "settings_model"
    pkg.mkdir()
    (pkg / "data.json").write_text(json.dumps({
        "model": "lumi.test.settings",
        "models": ["lumi.test.settings"],
        "manufacturer": "Aqara",
        "display_name": "Settings Test Model",
        "regions": ["EU"],
        "bundle_ids": [],
        "source_region": "ger",
        "endpoints": {"0": {"deviceType": "Root"}},
        "device_types": ["Switch"],
        "power_class": "mains",
        "traits": {},
        "settings": {
            "4.4.85": {
                "name": "Child lock socket 1",
                "platform": "switch",
            },
            "4.5.85": {
                "name": "Button mode",
                "platform": "select",
                "enum_values": {"1": "Single", "2": "Multi"},
            },
        },
    }))
    (pkg / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        OVERRIDES: dict[str, TraitSpec | None] = {}
    """).strip())
    return pkg


def test_settings_materialise_as_settingspec(settings_package: Path):
    data = _loader.load_model_data(settings_package)
    settings = data["SETTINGS"]
    assert isinstance(settings["4.4.85"], SettingSpec)
    s = settings["4.4.85"]
    assert s.rid == "4.4.85"
    assert s.name == "Child lock socket 1"
    assert s.platform == "switch"
    # Settings have no wire_path field at all.
    assert not hasattr(s, "wire_path")


def test_settings_are_not_in_traits(settings_package: Path):
    data = _loader.load_model_data(settings_package)
    assert data["TRAITS"] == {}
    assert "4.4.85" not in data["TRAITS"]


def test_settings_propagate_enum_values(settings_package: Path):
    data = _loader.load_model_data(settings_package)
    assert data["SETTINGS"]["4.5.85"].enum_values == {"1": "Single", "2": "Multi"}


def test_power_class_exposed(settings_package: Path):
    data = _loader.load_model_data(settings_package)
    assert data["POWER_CLASS"] == "mains"


def test_power_class_none_when_absent(settings_package: Path):
    raw = json.loads((settings_package / "data.json").read_text())
    del raw["power_class"]
    (settings_package / "data.json").write_text(json.dumps(raw))
    data = _loader.load_model_data(settings_package)
    assert data["POWER_CLASS"] is None


def test_settings_override_none_drops(settings_package: Path):
    (settings_package / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.device.settings import SettingSpec
        OVERRIDES: dict[str, TraitSpec | None] = {}
        SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {"4.4.85": None}
    """).strip())
    data = _loader.load_model_data(settings_package)
    assert "4.4.85" not in data["SETTINGS"]
    assert "4.5.85" in data["SETTINGS"]


def test_settings_override_replaces(settings_package: Path):
    (settings_package / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.device.settings import SettingSpec
        OVERRIDES: dict[str, TraitSpec | None] = {}
        SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
            "4.4.85": SettingSpec(
                rid="4.4.85", name="Renamed lock", platform="switch",
            ),
        }
    """).strip())
    data = _loader.load_model_data(settings_package)
    assert data["SETTINGS"]["4.4.85"].name == "Renamed lock"


def test_settings_override_adds_new_rid(settings_package: Path):
    (settings_package / "overrides.py").write_text(dedent("""
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        from custom_components.aqara_lanlink.device.settings import SettingSpec
        OVERRIDES: dict[str, TraitSpec | None] = {}
        SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
            "4.9.85": SettingSpec(
                rid="4.9.85", name="Extra setting", platform="number",
            ),
        }
    """).strip())
    data = _loader.load_model_data(settings_package)
    assert data["SETTINGS"]["4.9.85"].name == "Extra setting"
    assert "4.4.85" in data["SETTINGS"]


def test_missing_settings_block_yields_empty(settings_package: Path):
    raw = json.loads((settings_package / "data.json").read_text())
    del raw["settings"]
    (settings_package / "data.json").write_text(json.dumps(raw))
    data = _loader.load_model_data(settings_package)
    assert data["SETTINGS"] == {}
