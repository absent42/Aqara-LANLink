"""Tests for the dropped_rids load + index plumbing (Chunk 3, Tasks 8-10).

dropped_rids mirrors dropped_paths but is a {rid: reason} dict (not a
frozenset) carried for the future scan-device consumer.
"""
from __future__ import annotations

import json
import sys
import textwrap
import types

import pytest

from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.models import _loader

_SYNTH_PREFIX = "custom_components.aqara_lanlink.device.models.synth_"

# load_model_data direct-subscripts several required keys; the fixture must
# include them or it KeyErrors before reaching the dropped_rids branch.
_BASE = {
    "models": ["lumi.x"],
    "manufacturer": "Aqara",
    "display_name": "X",
    "regions": ["EU"],
    "bundle_ids": [],
    "endpoints": {},
    "device_types": [],
    "traits": {},
}


def _pkg(tmp_path, extra):
    (tmp_path / "data.json").write_text(json.dumps({**_BASE, **extra}))
    return tmp_path


# --- Task 8: loader ---------------------------------------------------------

def test_loader_exposes_dropped_rids(tmp_path):
    out = _loader.load_model_data(
        _pkg(tmp_path, {"dropped_rids": {"8.0.2007": "diagnostic:telemetry"}})
    )
    assert out["DROPPED_RIDS"] == {"8.0.2007": "diagnostic:telemetry"}


def test_loader_no_dropped_rids_key_when_absent(tmp_path):
    out = _loader.load_model_data(_pkg(tmp_path, {}))
    assert "DROPPED_RIDS" not in out or out["DROPPED_RIDS"] == {}


# --- Task 9: registry index -------------------------------------------------

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


def test_dropped_rids_indexed_per_model(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_dropped_rids", """
        MODELS = ("lumi.x",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Dropped Rids Pkg"
        DROPPED_RIDS = {"8.0.2007": "diagnostic:telemetry"}
    """)
    registry.get_device_class("anything")

    assert registry._DROPPED_RIDS_INDEX["lumi.x"] == {"8.0.2007": "diagnostic:telemetry"}
    registry.reset_for_tests()
    assert "lumi.x" not in registry._DROPPED_RIDS_INDEX


# --- Task 10: catalog accessor ---------------------------------------------

def test_catalog_dropped_rids_for_model(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_dropped_rids_cat", """
        MODELS = ("lumi.x",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Dropped Rids Cat Pkg"
        DROPPED_RIDS = {"8.0.2007": "diagnostic:telemetry"}
    """)
    assert catalog.dropped_rids_for_model("lumi.x") == {"8.0.2007": "diagnostic:telemetry"}
    assert catalog.dropped_rids_for_model("unknown") == {}
