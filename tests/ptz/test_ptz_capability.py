"""Capability declaration: CAPABILITIES in overrides.py -> catalog helper."""
from pathlib import Path

from custom_components.aqara_lanlink.device.models._loader import load_model_data

MODELS = Path(__file__).parents[2] / "custom_components/aqara_lanlink/device/models"  # ptz/ -> tests/ -> repo root


def test_loader_emits_capabilities_for_g350():
    data = load_model_data(MODELS / "camera_agl010")
    assert data["CAPABILITIES"] == {"ptz": frozenset({"pan_tilt", "zoom", "presets"})}


def test_loader_defaults_capabilities_empty_when_absent():
    # acpartner_aq1 has no CAPABILITIES in overrides.py
    data = load_model_data(MODELS / "acpartner_aq1")
    assert data["CAPABILITIES"] == {}


def test_registry_indexes_capabilities():
    from custom_components.aqara_lanlink.device import registry
    registry._ensure_discovered()
    assert registry._CAPABILITIES_INDEX["lumi.camera.agl010"] == {
        "ptz": frozenset({"pan_tilt", "zoom", "presets"})
    }


def test_ptz_features_for_model():
    from custom_components.aqara_lanlink.device.catalog import ptz_features_for_model
    assert ptz_features_for_model("lumi.camera.agl010") == frozenset(
        {"pan_tilt", "zoom", "presets"}
    )
    # non-PTZ camera and unknown model -> empty
    assert ptz_features_for_model("lumi.camera.acn007") == frozenset({"pan_tilt", "presets"})
    assert ptz_features_for_model("does.not.exist") == frozenset()
