"""Generic time platform.

One :class:`CompositeTime` per composite-rid field whose ``field.platform`` is
``"time"``. Unlike the descriptor platforms, time entities are never built from
CSV descriptors -- they are always the time-typed fields of a composite rid,
driven by a shared :class:`CompositeController`.

The controller source (``entry.runtime_data.composite_controllers``) is
populated by a later wiring chunk; until then it defaults to ``{}`` and this
platform simply adds no entities.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device.composites.entities import CompositeTime
from .entity import setup_descriptor_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build a `CompositeTime` per time-typed composite field per device."""
    controllers = getattr(entry.runtime_data, "composite_controllers", {})

    def build(hub, device, subentry) -> list:
        entities: list = []
        for controller in controllers.get(device.did, []):
            for field in controller.codec.fields:
                if field.platform == "time":
                    entities.append(
                        CompositeTime(hub, device, subentry, controller, field)
                    )
        return entities

    await setup_descriptor_platform(entry, async_add_entities, build)


__all__ = ["CompositeTime", "async_setup_entry"]
