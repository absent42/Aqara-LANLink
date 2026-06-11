"""Maintainer overrides for this model. Preserved across catalogue regen.
Add entries here for V3 spec corrections, custom device_class, etc.
"""
from custom_components.aqara_lanlink.device.traits import TraitSpec

OVERRIDES: dict[str, TraitSpec | None] = {
    # "wire_path": TraitSpec(...)  # replace generated entry
    # "wire_path": None            # drop generated entry
    #
    # OccupancySensing bleed: Aqara's V3 spec declares an OccupancySensor
    # endpoint (ep6) on the Switch H1, but this is a relay wall switch with
    # no presence hardware -- the Occupancy readback never fires. Drop it so
    # the model doesn't render a phantom always-clear occupancy sensor.
    # (The Elite Switch H2 keeps its Occupancy -- that model has real mmwave.)
    "6.160.33000": None,
}
