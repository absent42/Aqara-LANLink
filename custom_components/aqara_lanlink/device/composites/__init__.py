"""Composite entity codec registry."""

from __future__ import annotations

from .codecs import BrightnessCodec, PackedPeriodCodec, ScheduleJsonCodec

CODECS = {
    "packed_period": PackedPeriodCodec(),
    "brightness": BrightnessCodec(),
    "schedule_json": ScheduleJsonCodec(),
}

__all__ = ["CODECS"]
