"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
from custom_components.aqara_lanlink.device.settings import SettingSpec
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
}

# Drop generated rid-settings that are name-only duplicates of an existing
# wire-path trait - the matcher cannot dedup these safely (no enum on the
# trait + divergent names; a name-synonym wide enough to catch them would
# also hide the real music_action_sensitivity setting). Owner-confirmed duals.
SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
    "4.160.85": None,   # music_action_switch == trait 2.134.20205 Lighting music sync enable
    "14.160.85": None,  # device_paragraph    == trait 2.134.20459 Light strip segment count
    # 14.7.85 "Dynamic-sequence operation" is Value Type=uint32_t with a plain
    # Description, but its real value is a packed hex blob (e.g. 87038001010101)
    # the generator cannot detect from the Description alone. Force it to text so
    # the entity edits the raw packed string instead of presenting a bogus slider.
    "14.7.85": SettingSpec(
        rid="14.7.85", name="Dynamic-sequence operation", platform="text",
        resource_code="scene_set", entity_category="config",
    ),
}
