"""Composer for the Knob deviceType.

Rotation remotes (e.g. lumi.remote.cagl02, lumi.remote.rkba01, lumi.switch.rkna01)
declare a Knob endpoint carrying any of:

  - Knob.RotationEvent (enum): "Rotate" / "HoldToRotate" pulse events.
    Modelled as an EventDescriptor so HA automations can trigger on each
    rotation pulse (same pattern as Button.ButtonEvent + Doorbell.ring).
  - Button.ButtonEvent (enum): present when the knob also presses (rkba01).
    Same EventDescriptor pattern, distinct entity.
  - Knob.RotationAngle (numeric, unit=deg): last rotation magnitude. Falls
    through to _fallback -> SensorDescriptor.
  - Knob.HoldRotationAngle (numeric, deg): rotation while held. Fallback.
  - Knob.RotationDirection (enum): cw/ccw indicator. Fallback.

Knob.RotationEvent and Button.ButtonEvent are absorbed here so HA gets
proper Event entities (vs the fallback's enum-as-sensor mapping). Other
Knob.* traits and any unrelated knobs delegate to _fallback.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import (
    AnyDescriptor, EventDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec

from . import _fallback
from ._base import ComposeContext, _ec


# Function/trait pairs that this composer fuses into EventDescriptors.
# Anything else on a Knob endpoint delegates to _fallback.
_EVENT_TRAITS: frozenset[tuple[str, str]] = frozenset({
    ("Knob", "RotationEvent"),
    ("Button", "ButtonEvent"),
})


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    out: list[AnyDescriptor] = []
    others: dict[str, TraitSpec] = {}
    for wp, spec in traits.items():
        if (spec.function_code, spec.trait_code) in _EVENT_TRAITS:
            event_types = (
                tuple(spec.enum_values.values()) if spec.enum_values else ("event",)
            )
            out.append(EventDescriptor(
                key=f"auto_{spec.id.replace('.', '_')}",
                name=spec.name,
                trigger_trait=spec,
                event_types=event_types,
                entity_category=_ec(spec),
                entity_registry_enabled_default=spec.default_enabled,
            ))
        else:
            others[wp] = spec
    out.extend(_fallback.compose(endpoint_id, others, context))
    return out
