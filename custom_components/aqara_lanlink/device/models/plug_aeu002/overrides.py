"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
# NOTE: do NOT run generate_catalogue.py on this model before v2 - it is not yet settings-aware and would wipe the data.json `settings` block.
from custom_components.aqara_lanlink.device.settings import SettingSpec
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
}

# Tuning layer for the rid-keyed settings authored in data.json. Entries here
# are merged onto the data.json `settings` block at load time. Empty by default;
# data.json is the end-goal home for confirmed settings.
SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
    # "8.0.2042": SettingSpec(rid="8.0.2042", name="Max power", platform="number", min=100, max=4000, unit="W")  # replace data.json entry
    # "8.0.2096": None  # drop a data.json entry
    # "9.9.99": SettingSpec(rid="9.9.99", name="New setting", platform="switch")  # add a new rid not in data.json
}
