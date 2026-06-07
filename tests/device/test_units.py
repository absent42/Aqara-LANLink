"""Tests for Aqara -> Home Assistant unit-string normalization."""
from __future__ import annotations

from custom_components.aqara_lanlink.device.units import normalize_unit


def test_normalizes_composed_degree_celsius():
    # Aqara spec data stores Celsius as U+2103 (a single composed glyph);
    # HA's temperature device class only accepts °C (U+00B0 + 'C').
    assert normalize_unit("℃") == "°C"


def test_normalizes_composed_degree_fahrenheit():
    # Defensive: U+2109 is the Fahrenheit counterpart of U+2103.
    assert normalize_unit("℉") == "°F"


def test_normalizes_lux_to_lx():
    # HA's illuminance device class expects 'lx', not 'lux'.
    assert normalize_unit("lux") == "lx"


def test_normalizes_ascii_micrograms_per_cubic_metre():
    # Aqara writes 'ug/m³' with ASCII 'u'; HA expects the micro sign µ.
    assert normalize_unit("ug/m³") == "µg/m³"


def test_normalizes_watt_hour():
    # Aqara writes 'W·h' (W + U+00B7 middle dot + h); HA energy expects 'Wh'.
    assert normalize_unit("W·h") == "Wh"


def test_passes_through_already_canonical_unit():
    assert normalize_unit("°C") == "°C"
    assert normalize_unit("%") == "%"
    assert normalize_unit("V") == "V"


def test_passes_through_unmapped_unit():
    assert normalize_unit("kPa") == "kPa"
    assert normalize_unit("ppm") == "ppm"


def test_passes_through_none():
    assert normalize_unit(None) is None


def test_passes_through_empty_string():
    assert normalize_unit("") == ""


def test_is_idempotent():
    # dataclasses.replace re-runs __post_init__, so a value already
    # normalized must survive a second pass unchanged.
    assert normalize_unit(normalize_unit("℃")) == "°C"
