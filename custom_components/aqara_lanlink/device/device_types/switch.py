"""Composer for the Switch deviceType: Output.OnOff -> SwitchDescriptor.

Other traits on the Switch endpoint (StartUpOnOff config, etc.) delegate
to _fallback so they surface as Select/Number/etc. as appropriate.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import SwitchDescriptor

from ._base import _ec, make_single_trait_composer

compose = make_single_trait_composer(
    function_code="Output",
    trait_code="OnOff",
    descriptor_factory=lambda spec: SwitchDescriptor(
        key=f"auto_{spec.id.replace('.', '_')}",
        name=spec.name,
        attr=AttrSpec(name=spec.id),
        entity_category=_ec(spec),
        entity_registry_enabled_default=spec.default_enabled,
    ),
)
