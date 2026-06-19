"""Typed metadata for a rid-keyed device setting; mirrors TraitSpec's role for the settings catalogue."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingSpec:
    """A single rid-keyed device setting's metadata.

    `rid` is the 3-part resource ID (e.g. `4.4.85`) written bare over
    LANLink, bypassing the wire-path trait pipeline. `platform` names the
    HA entity type that should represent the setting. Settings receive no
    push reports, so `optimistic` defaults True: the entity sets its own
    state after a write.

    State model: writes are fully local, state is cloud-seeded once at load
    and optimistic after each HA write; Aqara-app changes made mid-session
    are not reflected until the integration reloads. See
    `build_descriptors.build_setting_descriptors` for the full rationale.
    """

    rid: str
    name: str
    platform: str
    # Stable snake `Resource Name` (e.g. `door_modex`), parallel to a trait's
    # `trait_code`; `name` is the friendly `Attr name` HA label. Kept next to the
    # identity fields but defaulted (so it follows the required positionals rid/
    # name/platform). Pure metadata - no descriptor/entity consumes it.
    resource_code: str | None = None
    enum_values: dict[str, str] | None = None
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    press_value: str | None = None
    entity_category: str = "config"
    default_enabled: bool = True
    optimistic: bool = True
    on_value: str = "1"
    """switch wire values; some settings invert (e.g. indicator on == '0')."""
    off_value: str = "0"


__all__ = ["SettingSpec"]
