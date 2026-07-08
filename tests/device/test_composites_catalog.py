"""Tests for the composites load + index + catalog plumbing (Task 4.1).

Mirrors the dropped_rids/settings plumbing: data.json -> _loader ->
registry index -> catalog accessor.
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
# include them or it KeyErrors before reaching the composites branch.
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

_COMPOSITES = {
    "8.0.2229": {"codec": "packed_period", "name": "Do not disturb"},
}


def _pkg(tmp_path, extra):
    (tmp_path / "data.json").write_text(json.dumps({**_BASE, **extra}))
    return tmp_path


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


# --- loader -----------------------------------------------------------------

def test_loader_exposes_composites(tmp_path):
    out = _loader.load_model_data(_pkg(tmp_path, {"composites": _COMPOSITES}))
    assert out["COMPOSITES"] == _COMPOSITES


def test_loader_composites_empty_when_absent(tmp_path):
    out = _loader.load_model_data(_pkg(tmp_path, {}))
    assert out["COMPOSITES"] == {}


# --- registry index ---------------------------------------------------------

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


def test_composites_indexed_per_model(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_composites", """
        MODELS = ("lumi.x",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Composites Pkg"
        COMPOSITES = {"8.0.2229": {"codec": "packed_period", "name": "Do not disturb"}}
    """)
    registry.get_device_class("anything")

    assert registry._COMPOSITES_INDEX["lumi.x"] == {
        "8.0.2229": {"codec": "packed_period", "name": "Do not disturb"},
    }
    registry.reset_for_tests()
    assert "lumi.x" not in registry._COMPOSITES_INDEX


# --- catalog accessor -------------------------------------------------------

def test_catalog_composites_for_model(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_composites_cat", """
        MODELS = ("lumi.x",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Composites Cat Pkg"
        COMPOSITES = {"8.0.2229": {"codec": "packed_period", "name": "Do not disturb"}}
    """)
    assert catalog.composites_for_model("lumi.x") == {
        "8.0.2229": {"codec": "packed_period", "name": "Do not disturb"},
    }
    assert catalog.composites_for_model("unknown") == {}


def test_composites_for_model_returns_copy(monkeypatch, tmp_path):
    _synth_package(monkeypatch, tmp_path, "synth_composites_copy", """
        MODELS = ("lumi.x",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Composites Copy Pkg"
        COMPOSITES = {"8.0.2229": {"codec": "packed_period", "name": "Do not disturb"}}
    """)
    result = catalog.composites_for_model("lumi.x")
    result["junk"] = None
    assert "junk" not in catalog.composites_for_model("lumi.x")
