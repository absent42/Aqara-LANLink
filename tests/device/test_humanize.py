"""Tests for the PascalCase -> human-readable name transform.

The function is pure; cases here cover the trait names actually shipped
in our V3 catalogue plus the corner cases (acronyms, all-caps trailing
runs, idempotency) the implementation has to handle.
"""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device.humanize import humanize_name


@pytest.mark.parametrize("raw,expected", [
    # The bread-and-butter cases: simple PascalCase trait names from the V3
    # catalogue. First word capitalized; rest lowercased.
    ("OnOff", "On off"),
    ("CurrentLevel", "Current level"),
    ("ColorTemperature", "Color temperature"),
    ("BatPercentRemaining", "Bat percent remaining"),
    ("HubConnectionStatus", "Hub connection status"),
    ("StartUpOnOff", "Start up on off"),
    ("OnTransitionTime", "On transition time"),
    ("StateOfLowBat", "State of low bat"),
    ("AntiDeletionSetting", "Anti deletion setting"),
    ("ButtonEvent", "Button event"),
])
def test_basic_pascal_case(raw, expected):
    assert humanize_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # Acronym at the start (followed by a TitleCase word): the leading
    # caps run is kept as a single acronym token.
    ("URLPath", "URL path"),
    ("IRType", "IR type"),
    ("IRKey", "IR key"),
    ("RGBLighting", "RGB lighting"),
    # Acronym at the end (after a TitleCase word).
    ("WiFiRSSI", "Wi fi RSSI"),
    ("ColorXY", "Color XY"),
    ("CurrentXY", "Current XY"),
    # All-caps standalone is preserved.
    ("RSSI", "RSSI"),
    ("URL", "URL"),
    # Adjacent acronyms can't be split without a dictionary; emit as one
    # token. Documented limitation.
    ("CameraRTSPURL", "Camera RTSPURL"),
    # Single-letter color-channel labels (R/G/B/X/Y) are acronyms too --
    # `CurrentR` must stay `Current R`, not `Current r`. Catalogue trait
    # names CurrentR/G/B/X/Y appear on RGB controllers (ctrl_rgb_es1,
    # gateway_acn004, gateway_agl002).
    ("CurrentR", "Current R"),
    ("CurrentG", "Current G"),
    ("CurrentB", "Current B"),
    ("CurrentX", "Current X"),
    ("CurrentY", "Current Y"),
])
def test_acronyms_preserved(raw, expected):
    assert humanize_name(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "Reachable", "On off", "Color temperature", "URL",
])
def test_idempotent(raw):
    """Applying the transform twice yields the same string as applying
    it once -- so humanizing an already-humanized name is a no-op."""
    once = humanize_name(raw)
    assert humanize_name(once) == once


def test_empty_string_passes_through():
    assert humanize_name("") == ""


def test_single_lowercase_word_capitalized():
    # An unusual input shape (catalogue is all PascalCase), but exercise
    # the first-word-capitalized rule anyway.
    assert humanize_name("reachable") == "Reachable"
