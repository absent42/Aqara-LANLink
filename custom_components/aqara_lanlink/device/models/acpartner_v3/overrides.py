"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
}
