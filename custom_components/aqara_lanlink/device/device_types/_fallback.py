"""Fallback composer for deviceTypes without a specific handler.

Emits one descriptor per trait, dispatching on TraitSpec.data_type.
Drop-classified traits never reach here (filtered by classify_v3 before
calling compose).

This is the safety net: when V3 introduces a new deviceType Aqara hasn't
documented yet, users still get _some_ entities until a per-deviceType
composer module lands.
"""
from __future__ import annotations

from custom_components.aqara_lanlink.device.descriptors import AnyDescriptor
from custom_components.aqara_lanlink.device.trait_policy import BUTTON_TRAITS
from custom_components.aqara_lanlink.device.traits import TraitSpec

from ._base import ComposeContext
from ._build import build_descriptor


def compose(
    endpoint_id: int,
    traits: dict[str, TraitSpec],
    context: ComposeContext,
) -> list[AnyDescriptor]:
    """Emit one descriptor per visible trait, dispatching by data_type."""
    out: list[AnyDescriptor] = []
    for wp, spec in traits.items():
        desc = _descriptor_for_trait(wp, spec)
        if desc is not None:
            out.append(desc)
    return out


def _descriptor_for_trait(wp: str, spec: TraitSpec) -> AnyDescriptor | None:
    # Press-to-trigger traits (e.g. Identify.IdentifyTime) render as a
    # stateless Button rather than the data_type-dispatched defaults
    # (which would otherwise turn this writable trait into a Switch or
    # Number). The mapping lives in trait_policy so the offline generator
    # and runtime classifier stay in lockstep; the dict's value is the
    # press_value the button writes to the trait.
    if BUTTON_TRAITS.get((spec.function_code, spec.trait_code)) is not None:
        platform = "button"
    elif spec.data_type == "bool":
        platform = "switch" if spec.writable else "binary_sensor"
    elif spec.data_type in ("int", "float"):
        if spec.writable and spec.min_value is not None and spec.max_value is not None:
            platform = "number"
        else:
            platform = "sensor"
    elif spec.data_type == "enum":
        platform = "select" if (spec.writable and spec.enum_values) else "sensor"
    else:
        platform = "sensor"
    desc = build_descriptor(spec, platform)
    # Button press_value is the one field build_descriptor can't infer; patch it.
    if platform == "button" and desc is not None:
        from dataclasses import replace
        desc = replace(desc, press_value=BUTTON_TRAITS[(spec.function_code, spec.trait_code)])
    return desc
