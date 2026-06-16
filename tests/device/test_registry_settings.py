"""Tests for registry indexing of SETTINGS + POWER_CLASS (Task 2b)."""

from __future__ import annotations

import sys
import textwrap
import types

import pytest

from custom_components.aqara_lanlink.device import registry

_SYNTH_PREFIX = "custom_components.aqara_lanlink.device.models.synth_"


@pytest.fixture(autouse=True)
def _reset_registry():
    for key in list(sys.modules.keys()):
        if key.startswith(_SYNTH_PREFIX):
            del sys.modules[key]
    registry.reset_for_tests()
    yield
    for key in list(sys.modules.keys()):
        if key.startswith(_SYNTH_PREFIX):
            del sys.modules[key]
    registry.reset_for_tests()


def _synth_package(monkeypatch, tmp_path, subpkg_name: str, init_src: str) -> None:
    pkg_name = "custom_components.aqara_lanlink.device.models"
    path_str = str(tmp_path)
    existing = sys.modules.get(pkg_name)
    if existing is not None and getattr(existing, "_synth_test_pkg", False):
        if path_str not in existing.__path__:  # type: ignore[operator]
            existing.__path__.append(path_str)  # type: ignore[attr-defined]
    else:
        fake_pkg = types.ModuleType(pkg_name)
        fake_pkg.__path__ = [path_str]  # type: ignore[attr-defined]
        fake_pkg._synth_test_pkg = True  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, pkg_name, fake_pkg)
    subpkg_dir = tmp_path / subpkg_name
    subpkg_dir.mkdir(parents=True, exist_ok=True)
    (subpkg_dir / "__init__.py").write_text(textwrap.dedent(init_src).strip())


def test_settings_and_power_class_indexed_per_model(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_settings", """
        from custom_components.aqara_lanlink.device.settings import SettingSpec
        MODELS = ("lumi.test.s1", "lumi.test.s2")
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Settings Pkg"
        SETTINGS = {"4.4.85": SettingSpec(rid="4.4.85", name="Lock", platform="switch")}
        POWER_CLASS = "mains"
    """)
    registry.get_device_class("anything")

    for m in ("lumi.test.s1", "lumi.test.s2"):
        assert m in registry._SETTINGS_INDEX
        assert registry._SETTINGS_INDEX[m]["4.4.85"].name == "Lock"
        assert registry._POWER_CLASS_INDEX[m] == "mains"


def test_reset_clears_settings_indices(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_settings_reset", """
        from custom_components.aqara_lanlink.device.settings import SettingSpec
        MODELS = ("lumi.test.sr",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Settings Reset Pkg"
        SETTINGS = {"4.4.85": SettingSpec(rid="4.4.85", name="Lock", platform="switch")}
        POWER_CLASS = "battery"
    """)
    registry.get_device_class("anything")
    assert len(registry._SETTINGS_INDEX) > 0
    assert len(registry._POWER_CLASS_INDEX) > 0

    registry.reset_for_tests()

    assert len(registry._SETTINGS_INDEX) == 0
    assert len(registry._POWER_CLASS_INDEX) == 0
