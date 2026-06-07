"""Normalize Aqara unit strings to Home Assistant canonical units.

Aqara's device-spec data (the master spec, TRAIT_NAMES, and the cloud
trait responses) reports units in forms that HA's device-class unit
validation rejects:

  - ``℃`` (U+2103, a single composed glyph) instead of ``°C``
  - ``℉`` (U+2109) instead of ``°F``
  - ``lux`` instead of ``lx``
  - ``ug/m³`` (ASCII ``u``) instead of ``µg/m³`` (micro sign, U+00B5)
  - ``W·h`` (with U+00B7 middle dot) instead of ``Wh``

An entity whose ``native_unit_of_measurement`` is one of these AND whose
``device_class`` is set logs a warning at every startup ("not a valid unit
for the device class"). ``normalize_unit`` maps the Aqara form to the
canonical HA string; it is the single place that knows this mapping.
"""
from __future__ import annotations

from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    LIGHT_LUX,
    UnitOfEnergy,
    UnitOfTemperature,
)

# Aqara unit string -> HA-canonical unit string. Keys are the exact
# literals seen in Aqara spec data; values come from HA's own constants
# so the mapping can never drift from what HA validates against.
_UNIT_ALIASES: dict[str, str] = {
    "℃": str(UnitOfTemperature.CELSIUS),     # ℃ -> °C
    "℉": str(UnitOfTemperature.FAHRENHEIT),  # ℉ -> °F
    "lux": str(LIGHT_LUX),                        # lux -> lx
    "ug/m³": str(CONCENTRATION_MICROGRAMS_PER_CUBIC_METER),  # ug/m³ -> µg/m³
    "W·h": str(UnitOfEnergy.WATT_HOUR),      # W·h -> Wh
}


def normalize_unit(unit: str | None) -> str | None:
    """Return the HA-canonical form of an Aqara unit string.

    Units already canonical, or not validated by any HA device class, are
    returned unchanged. ``None`` and the empty string pass through as-is.
    The mapping is idempotent: normalizing a canonical value is a no-op.
    """
    if not unit:
        return unit
    return _UNIT_ALIASES.get(unit, unit)


__all__ = ["normalize_unit"]
