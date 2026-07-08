"""Composite entity codec registry."""

from __future__ import annotations

from .codecs import (
    BboxRegionJsonCodec,
    BrightnessCodec,
    PackedPeriodCodec,
    PtzPresetJsonCodec,
    RegionJsonCodec,
    ScheduleJsonCodec,
)

CODECS = {
    "packed_period": PackedPeriodCodec(),
    "brightness": BrightnessCodec(),
    "schedule_json": ScheduleJsonCodec(),
    "region_json": RegionJsonCodec(),
    "bbox_region_json": BboxRegionJsonCodec(),
    "ptz_preset_json": PtzPresetJsonCodec(),
}

__all__ = ["CODECS"]
