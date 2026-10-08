"""Tests for the Switch deviceType composer."""
from __future__ import annotations

from pathlib import Path

from custom_components.aqara_lanlink.device.classify_v3 import classify_v3
from custom_components.aqara_lanlink.device.device_types import (
    _base, switch, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    EventDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.models._loader import load_model_data
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="lumi.switch.l3acn1")


def _onoff_trait(wp: str = "2.130.32945") -> TraitSpec:
    return TraitSpec(
        id=wp, wire_path=wp,
        function_code="Output", trait_code="OnOff",
        name="OnOff", data_type="bool",
        readable=True, writable=True, subscribable=True,
        endpoint_id=int(wp.split(".")[0]),
    )


def test_onoff_becomes_switch_descriptor():
    descs = switch.compose(endpoint_id=2, traits={"2.130.32945": _onoff_trait()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SwitchDescriptor)


def test_switch_attr_name_is_wire_path():
    """SwitchDescriptor's attr.name must be the wire path for routing."""
    descs = switch.compose(endpoint_id=2, traits={"2.130.32945": _onoff_trait()}, context=_ctx())
    assert descs[0].attr.name == "2.130.32945"


def test_switch_composer_registered():
    assert get_composer("Switch") is switch.compose


def test_outlet_alias_routes_to_switch_composer():
    """Multi-outlet plug strips (e.g. lumi.plug.aeu002) declare each socket
    as deviceType "Outlet". Trait surface per Outlet endpoint is just
    Output.OnOff -- identical to a Switch endpoint -- so the alias must
    route to the same composer rather than falling through to per-trait
    classification (which logs a WARNING per endpoint per setup)."""
    assert get_composer("Outlet") is switch.compose


def test_switch_without_onoff_emits_nothing():
    assert switch.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_catalogued_switch_button_events_keep_switch_and_config_traits():
    """Button semantics must not steal Output.OnOff or writable config traits.

    Cover every shipped Switch endpoint that combines a Button.ButtonEvent
    with switch functionality. This guards both classification and the
    expected one-trait-to-descriptor coverage across the real catalogue.
    """
    models_root = (
        Path(__file__).resolve().parents[3]
        / "custom_components" / "aqara_lanlink" / "device" / "models"
    )
    affected_endpoints = 0
    affected_models: set[str] = set()

    for package in sorted(
        p for p in models_root.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    ):
        data = load_model_data(package)
        for endpoint_id, endpoint in data["ENDPOINTS"].items():
            if endpoint.get("deviceType") != "Switch":
                continue
            endpoint_traits = {
                path: trait for path, trait in data["TRAITS"].items()
                if trait.endpoint_id == endpoint_id
            }
            button_paths = {
                path for path, trait in endpoint_traits.items()
                if trait.function_code == "Button"
                and trait.trait_code == "ButtonEvent"
            }
            if not button_paths:
                continue

            model = (data["MODELS"] or (package.name,))[0]
            affected_models.add(model)
            affected_endpoints += 1
            descriptors = classify_v3(
                model=model,
                endpoints={endpoint_id: endpoint},
                traits=endpoint_traits,
            )

            by_path = {
                (d.trigger_trait.wire_path or d.trigger_trait.id): d
                for d in descriptors if isinstance(d, EventDescriptor)
            }
            for path in button_paths:
                assert isinstance(by_path.get(path), EventDescriptor), (
                    model, endpoint_id, path,
                )

            onoff_paths = {
                path for path, trait in endpoint_traits.items()
                if trait.function_code == "Output" and trait.trait_code == "OnOff"
            }
            assert onoff_paths, (model, endpoint_id, "missing Output.OnOff")
            descriptor_paths = {
                d.attr.name: d for d in descriptors
                if isinstance(d, SwitchDescriptor)
            }
            for path in onoff_paths:
                assert isinstance(descriptor_paths.get(path), SwitchDescriptor), (
                    model, endpoint_id, path,
                )

            # Other writable traits are configuration/control surfaces. The
            # ButtonEvent override must not absorb or drop those descriptors.
            for path, trait in endpoint_traits.items():
                if not trait.writable or path in onoff_paths:
                    continue
                assert any(
                    getattr(d, "attr", None) is not None
                    and d.attr.name == path
                    or getattr(d, "trait", None) is not None
                    and d.trait.id == path
                    for d in descriptors
                ), (model, endpoint_id, "writable trait missing", path)

    assert affected_endpoints > 0
    assert affected_models
