"""light_acn132 rid-settings gate.

Locks the shipped `lumi.light.acn132` packed-vs-number setting classification
against the regenerated catalogue + the per-model `SETTINGS_OVERRIDES`:

- `14.165.85` (mode_light): the CSV Description is bit-field prose
  ("bit8~bit31: reserved bit0~bit7: 0x00: ...") so the generator routes the
  packed numeric to a `text` entity (BUG 2).
- `14.7.85` (scene_set): the CSV Description is plain prose but the real value
  is a packed hex blob the generator cannot detect, so a per-model override
  forces it to `text` (BUG 3).
- `14.164.85` (Dynamic_change_speed): a clean numeric with a plain Description
  stays a `number`, and its built AqaraNumber has a functional slider
  (min/max fall back to HA's 0/100, never None -- BUG 1).

If this gate fails after a catalogue regen, fix the generator / override, NOT
this test.
"""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device import catalog, registry
from custom_components.aqara_lanlink.device.build_descriptors import build_descriptors
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor,
    TextDescriptor,
)
from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.number import AqaraNumber
from custom_components.aqara_lanlink.text import AqaraText
from custom_components.aqara_lanlink.entity import build_descriptor_entities

from ..platforms.conftest import make_device, make_hub, make_subentry

_MODEL = "lumi.light.acn132"


@pytest.fixture(autouse=True)
def _reset_catalog():
    catalog.reset_for_tests()
    registry.reset_for_tests()
    yield
    catalog.reset_for_tests()
    registry.reset_for_tests()


def test_packed_rids_are_text_and_clean_numeric_is_number():
    settings = catalog.settings_for_model(_MODEL)
    # BUG 2: bit-field Description -> text (regenerated from the CSV).
    assert settings["14.165.85"].platform == "text"
    # BUG 3: plain-Description packed hex -> text (per-model override).
    assert settings["14.7.85"].platform == "text"
    # Clean numeric stays a number.
    assert settings["14.164.85"].platform == "number"


def _build_acn132_device():
    descriptors = build_descriptors(_MODEL, Overlay())
    hub = make_hub()
    sub = make_subentry(subentry_id="sub-acn132", did="did-acn132", model=_MODEL)
    device = make_device(descriptors, subentry=sub, model=_MODEL)
    return hub, sub, device


def test_dynamic_change_speed_number_slider_is_functional():
    hub, sub, device = _build_acn132_device()
    numbers = build_descriptor_entities(
        hub, device, sub, NumberDescriptor, AqaraNumber
    )
    num = next(e for e in numbers if e.descriptor.key == "14.164.85")
    # BUG 1: a no-range number falls back to HA's 0/100/1, never None.
    assert num.min_value == 0.0
    assert num.max_value == 100.0
    assert num.min_value is not None and num.max_value is not None
    assert num.step == 1.0


def test_packed_rids_become_text_entities_end_to_end():
    hub, sub, device = _build_acn132_device()
    texts = build_descriptor_entities(hub, device, sub, TextDescriptor, AqaraText)
    text_keys = {e.descriptor.key for e in texts}
    assert {"14.165.85", "14.7.85"} <= text_keys
    for e in texts:
        if e.descriptor.key in {"14.165.85", "14.7.85"}:
            assert isinstance(e, AqaraText)


def test_packed_rids_are_not_numbers_end_to_end():
    hub, sub, device = _build_acn132_device()
    numbers = build_descriptor_entities(
        hub, device, sub, NumberDescriptor, AqaraNumber
    )
    number_keys = {e.descriptor.key for e in numbers}
    # The two packed rids must NOT surface as number entities.
    assert "14.165.85" not in number_keys
    assert "14.7.85" not in number_keys
