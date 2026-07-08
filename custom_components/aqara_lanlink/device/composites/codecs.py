"""Pure codec layer for composite entities.

A composite rid packs several logical fields into one wire value (a packed
integer or a JSON blob). Each codec turns one wire string into a
``{field: python-value}`` dict and back. This module has NO Home Assistant
imports and no device code - it is pure encode/decode math.

The ``datetime.time`` <-> minutes mapping and the bit math are load-bearing
and verified against live device captures. Do not "improve" the arithmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CompositeField:
    name: str
    platform: str  # "time" | "number" | "switch" | "text"
    label: str
    params: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Codec(Protocol):
    fields: tuple[CompositeField, ...]

    def decode(self, wire: str) -> dict[str, Any]: ...

    def encode(self, fields: dict[str, Any]) -> str: ...

    def defaults(self) -> dict[str, Any]: ...


# --- shared helpers ---------------------------------------------------------


def _mins_to_time(m: int) -> time:
    h, mm = divmod(int(m), 60)
    return time(h % 24, mm)


def _time_to_mins(t: time) -> int:
    return t.hour * 60 + t.minute


# --- codecs -----------------------------------------------------------------


class PackedPeriodCodec:
    """Time window packed into one integer.

    wire = (start_min << 12) | (end_min << 1) | disabled_bit0
    disabled_bit0: 0 = ON, 1 = OFF.
    """

    fields = (
        CompositeField("start", "time", "Start"),
        CompositeField("end", "time", "End"),
        CompositeField("enabled", "switch", "Enabled"),
    )

    def decode(self, wire: str) -> dict[str, Any]:
        w = int(wire)
        return {
            "start": _mins_to_time(w >> 12),
            "end": _mins_to_time((w >> 1) & 0x7FF),
            "enabled": (w & 1) == 0,
        }

    def encode(self, f: dict[str, Any]) -> str:
        w = (
            (_time_to_mins(f["start"]) << 12)
            | (_time_to_mins(f["end"]) << 1)
            | (0 if f["enabled"] else 1)
        )
        return str(w)

    def defaults(self) -> dict[str, Any]:
        return {"start": time(0, 0), "end": time(23, 59), "enabled": True}


class BrightnessCodec:
    """Colour / B&W brightness packed into one integer.

    wire = (bw << 16) | colour; wire == 0 => Auto.
    """

    fields = (
        CompositeField("auto", "switch", "Auto"),
        CompositeField("colour", "number", "Colour brightness", {"min": 0, "max": 100, "unit": "%"}),
        CompositeField("bw", "number", "B&W brightness", {"min": 0, "max": 100, "unit": "%"}),
    )

    def decode(self, wire: str) -> dict[str, Any]:
        w = int(wire)
        if w == 0:
            d = self.defaults()
            d["auto"] = True
            return d
        return {"auto": False, "colour": w & 0xFFFF, "bw": (w >> 16) & 0xFFFF}

    def encode(self, f: dict[str, Any]) -> str:
        if f.get("auto"):
            return "0"
        return str(((int(f["bw"]) & 0xFFFF) << 16) | (int(f["colour"]) & 0xFFFF))

    def defaults(self) -> dict[str, Any]:
        return {"auto": True, "colour": 100, "bw": 100}


_REPEAT_RE = re.compile(r"^[01]{7}$")


class ScheduleJsonCodec:
    """Weekly schedule carried as a JSON blob.

    wire = {"starttime": "HH:MM", "endtime": "HH:MM", "repeat": [7 ints]}
    """

    fields = (
        CompositeField("start", "time", "Start"),
        CompositeField("end", "time", "End"),
        CompositeField("repeat", "text", "Repeat days"),
    )

    def decode(self, wire: str) -> dict[str, Any]:
        d = json.loads(wire)
        repeat = "".join(str(int(b)) for b in d["repeat"])
        if not _REPEAT_RE.match(repeat):  # symmetric with encode; a malformed
            raise ValueError(f"repeat must be 7 chars of 0/1, got {repeat!r}")
        return {
            "start": time.fromisoformat(d["starttime"]),
            "end": time.fromisoformat(d["endtime"]),
            "repeat": repeat,
        }

    def encode(self, f: dict[str, Any]) -> str:
        r = f["repeat"]
        if not _REPEAT_RE.match(r):
            raise ValueError(f"repeat must be 7 chars of 0/1, got {r!r}")
        return json.dumps(
            {
                "starttime": f["start"].strftime("%H:%M"),
                "endtime": f["end"].strftime("%H:%M"),
                "repeat": [int(c) for c in r],
            }
        )

    def defaults(self) -> dict[str, Any]:
        return {"start": time(0, 0), "end": time(23, 59), "repeat": "1111111"}


class RegionJsonCodec:
    """Detection-region mask envelope: ``{"detect_region": <hex|int-array>}``.

    UNWRAP to a single ``text`` field carrying the bare inner value (a hex
    bitmap string, or the int array rendered as JSON). The mask itself is a 2D
    spatial bitmap not meaningfully hand-editable as scalars - this only strips
    the envelope for readability/paste; the real editor is a grid card.
    """

    fields = (CompositeField("region", "text", "Region"),)

    def decode(self, wire: str) -> dict[str, Any]:
        inner = json.loads(wire)["detect_region"]
        return {"region": inner if isinstance(inner, str) else json.dumps(inner)}

    def encode(self, f: dict[str, Any]) -> str:
        v = str(f["region"]).strip()
        inner = json.loads(v) if v[:1] in "[{" else v  # array vs bare hex string
        return json.dumps({"detect_region": inner})

    def defaults(self) -> dict[str, Any]:
        return {"region": ""}


class BboxRegionJsonCodec:
    """Bounding-box region: ``{"x_begin","x_end","y_begin","y_end"}`` (grid units).

    DECOMPOSE to four numbers. Unlike detect_region's opaque bitmap, a bbox is
    meaningful as scalars. Device emits keys in varying order (order-tolerant).
    """

    _KEYS = ("x_begin", "x_end", "y_begin", "y_end")
    fields = tuple(
        CompositeField(k, "number", k.replace("_", " ").title(), {"min": 0, "max": 15})
        for k in _KEYS
    )

    def decode(self, wire: str) -> dict[str, Any]:
        d = json.loads(wire)
        return {k: int(d[k]) for k in self._KEYS}

    def encode(self, f: dict[str, Any]) -> str:
        return json.dumps({k: int(f[k]) for k in self._KEYS})

    def defaults(self) -> dict[str, Any]:
        return {"x_begin": 0, "x_end": 8, "y_begin": 0, "y_end": 8}


class PtzPresetJsonCodec:
    """Pan/tilt preset position: ``{"mode","x","y"}`` (x/y 999 = unset)."""

    fields = (
        CompositeField("mode", "number", "Mode", {"min": 0, "max": 10}),
        CompositeField("x", "number", "X", {"min": 0, "max": 999}),
        CompositeField("y", "number", "Y", {"min": 0, "max": 999}),
    )

    def decode(self, wire: str) -> dict[str, Any]:
        d = json.loads(wire)
        return {k: int(d[k]) for k in ("mode", "x", "y")}

    def encode(self, f: dict[str, Any]) -> str:
        return json.dumps({k: int(f[k]) for k in ("mode", "x", "y")})

    def defaults(self) -> dict[str, Any]:
        return {"mode": 0, "x": 999, "y": 999}
