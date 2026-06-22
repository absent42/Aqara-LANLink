"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
from custom_components.aqara_lanlink.device.settings import SettingSpec
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
}

# A single-option enum ({"1": "Real-time query"}) is a command mis-rendered as a
# 1-option select (meaningless). Force it to a momentary button whose press_value
# is the enum key. Name/resource_code carried from the rid's data.json entry.
SETTINGS_OVERRIDES: dict[str, SettingSpec | None] = {
    "4.21.85": SettingSpec(
        rid="4.21.85", name="Start time of longest sleep interval",
        platform="button", press_value="1", resource_code="ai_query_type",
        entity_category="config",
    ),
    # AI sleep-metric readouts mis-marked writable in the V3 CSV - they are
    # measured statistics, not settings (Z2M confirms this is a metrics device).
    # Drop as sensors. See project_local_zcl_and_z2m_source.
    "14.52.85": None,   # "Turnovers per day"
    "14.9.85": None,    # "Turnover times per day"
}
