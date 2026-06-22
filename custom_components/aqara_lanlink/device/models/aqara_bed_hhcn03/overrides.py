"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
from custom_components.aqara_lanlink.device.settings import SettingSpec
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
}

# A single-option enum ({"1": "Open"}) is a command mis-rendered as a 1-option
# select (meaningless). Force it to a momentary button whose press_value is the
# enum key.
SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
    "4.7.85": SettingSpec(
        rid="4.7.85", name="Stop", platform="button", press_value="1",
        resource_code="switch_nostatus", entity_category="config",
    ),
}
