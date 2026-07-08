from __future__ import annotations

import json

import pytest
from datetime import time

from custom_components.aqara_lanlink.device.composites import CODECS
from custom_components.aqara_lanlink.device.composites.codecs import (
    BrightnessCodec,
    CompositeField,
    PackedPeriodCodec,
    ScheduleJsonCodec,
)


# --- Task 1.1: registry + field shape ---


def test_registry_has_three_codecs():
    assert set(CODECS) == {"packed_period", "brightness", "schedule_json"}


def test_field_shape():
    f = CompositeField(name="start", platform="time", label="Start")
    assert f.name == "start" and f.platform == "time" and f.params == {}


# --- Task 1.2: PackedPeriodCodec ---

C = PackedPeriodCodec()


def test_packed_period_decode_samples():
    assert C.decode("5162040") == {"start": time(21, 0), "end": time(9, 0), "enabled": True}
    assert C.decode("5653200") == {"start": time(23, 0), "end": time(6, 0), "enabled": True}
    assert C.decode("4547010") == {"start": time(18, 30), "end": time(3, 45), "enabled": True}
    assert C.decode("5162041")["enabled"] is False


def test_packed_period_roundtrip():
    for w in ("5162040", "5653200", "4547010", "5162041"):
        assert C.encode(C.decode(w)) == w


def test_packed_period_field_platforms():
    assert [(f.name, f.platform) for f in C.fields] == [
        ("start", "time"),
        ("end", "time"),
        ("enabled", "switch"),
    ]


# --- Task 1.3: BrightnessCodec ---

B = BrightnessCodec()


def test_brightness_samples():
    assert B.decode("3276850") == {"auto": False, "colour": 50, "bw": 50}
    assert B.decode("6553700") == {"auto": False, "colour": 100, "bw": 100}
    assert B.decode("65537") == {"auto": False, "colour": 1, "bw": 1}
    assert B.decode("0")["auto"] is True


def test_brightness_encode_auto_is_zero():
    assert B.encode({"auto": True, "colour": 50, "bw": 50}) == "0"
    assert B.encode({"auto": False, "colour": 50, "bw": 50}) == "3276850"


def test_brightness_number_params():
    colour = next(f for f in B.fields if f.name == "colour")
    assert colour.platform == "number" and colour.params == {"min": 0, "max": 100, "unit": "%"}


# --- Task 1.4: ScheduleJsonCodec ---

S = ScheduleJsonCodec()


def test_schedule_decode():
    w = '{"starttime": "01:00","endtime": "23:59","repeat": [1,1,1,1,1,1,1]}'
    assert S.decode(w) == {"start": time(1, 0), "end": time(23, 59), "repeat": "1111111"}


def test_schedule_roundtrip_semantic():
    w = '{"endtime":"00:42","repeat":[0,0,0,0,0,0,0],"starttime":"00:01"}'
    d = S.decode(w)
    assert d == {"start": time(0, 1), "end": time(0, 42), "repeat": "0000000"}
    assert json.loads(S.encode(d)) == json.loads(w)  # semantic equality


def test_schedule_repeat_validation():
    with pytest.raises(ValueError):
        S.encode({"start": time(0, 0), "end": time(1, 0), "repeat": "12"})
