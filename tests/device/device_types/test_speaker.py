"""Tests for the Speaker deviceType composer."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.aqara_lanlink.device.device_types import (
    _base, speaker, get_composer,
)
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor, SensorDescriptor, SwitchDescriptor,
)
from custom_components.aqara_lanlink.device.traits import TraitSpec


def _ctx() -> _base.ComposeContext:
    return _base.ComposeContext(model="aqara.speaker.hpcn03")


def _volume() -> TraitSpec:
    return TraitSpec(
        id="2.230.33100", wire_path="2.230.33100",
        function_code="Speaker", trait_code="Volume",
        name="Volume", data_type="int",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
        min_value=0.0, max_value=100.0, step=1.0,
    )


def _mute() -> TraitSpec:
    return TraitSpec(
        id="2.230.33101", wire_path="2.230.33101",
        function_code="Speaker", trait_code="Mute",
        name="Mute", data_type="bool",
        readable=True, writable=True, subscribable=True, endpoint_id=2,
    )


def _playback_state() -> TraitSpec:
    return TraitSpec(
        id="2.231.33110", wire_path="2.231.33110",
        function_code="MediaPlayback", trait_code="CurrentPlaybackState",
        name="CurrentPlaybackState", data_type="enum",
        enum_values={"0": "stopped", "1": "playing", "2": "paused"},
        readable=True, subscribable=True, endpoint_id=2,
    )


def test_volume_becomes_number():
    descs = speaker.compose(endpoint_id=2, traits={"2.230.33100": _volume()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], NumberDescriptor)
    assert descs[0].min_value == 0.0
    assert descs[0].max_value == 100.0


def test_mute_becomes_switch():
    descs = speaker.compose(endpoint_id=2, traits={"2.230.33101": _mute()}, context=_ctx())
    assert len(descs) == 1
    assert isinstance(descs[0], SwitchDescriptor)


def test_playback_state_becomes_enum_sensor():
    descs = speaker.compose(endpoint_id=2, traits={"2.231.33110": _playback_state()}, context=_ctx())
    assert len(descs) == 1
    d = descs[0]
    assert isinstance(d, SensorDescriptor)
    assert d.device_class == SensorDeviceClass.ENUM
    assert set(d.options or ()) == {"stopped", "playing", "paused"}


def test_full_speaker_set():
    traits = {t.id: t for t in (_volume(), _mute(), _playback_state())}
    descs = speaker.compose(endpoint_id=2, traits=traits, context=_ctx())
    assert len(descs) == 3


def test_empty_traits_returns_empty():
    assert speaker.compose(endpoint_id=2, traits={}, context=_ctx()) == []


def test_speaker_composer_registered():
    assert get_composer("Speaker") is speaker.compose
