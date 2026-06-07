"""Tests for ObservedPathCache wiring inside Device.handle_report."""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device.base import _is_numeric_path


@pytest.mark.parametrize("key, expected", [
    ("2.164.20536", True),
    ("0.128.32901", True),
    ("2.164.20536.1", True),  # 4-part also numeric
    ("device_i_am", False),
    ("", False),
    ("1.2", False),                 # 2-part not a path
    ("a.b.c", False),
    ("1.2.three", False),
    ("1..2", False),                # empty segment
])
def test_is_numeric_path(key, expected):
    assert _is_numeric_path(key) is expected


from unittest.mock import MagicMock

from custom_components.aqara_lanlink.device.base import Device


class _StubReport:
    """Minimal report shape matching what handle_report reads."""
    def __init__(self, values: dict) -> None:
        self.values = values


def _make_device(model: str, observed_cache):
    """Build a minimal Device with no derived descriptors, model set."""
    coordinator = MagicMock()
    coordinator.observed_path_cache = observed_cache
    subentry = MagicMock()
    subentry.data = {"did": "lumi.test.001"}
    device = Device(coordinator=coordinator, subentry=subentry, derived=())
    device.MODEL = model
    return device


def test_handle_report_records_numeric_paths(monkeypatch):
    from custom_components.aqara_lanlink.device.observed_path_cache import (
        ObservedPathCache,
    )

    storage: dict[str, object] = {}

    class FakeStore:
        def __init__(self, hass, version, key):
            self._key = key
        async def async_load(self):
            return storage.get(self._key)
        async def async_save(self, data):
            storage[self._key] = data
        def async_delay_save(self, data_func, delay):
            storage[self._key] = data_func()

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    import asyncio
    cache = ObservedPathCache(MagicMock())
    asyncio.run(cache.async_load())
    device = _make_device("lumi.vibration.agl002", cache)

    device.handle_report(_StubReport({
        "2.164.20536": "value-a",
        "2.164.20537.1": "value-b",  # 4-part, canonicalized to 3-part
        "device_i_am": "ignored",     # attr name, not a path
    }))

    paths = cache.get_paths("lumi.vibration.agl002")
    assert "2.164.20536" in paths
    assert "2.164.20537" in paths  # the .1 instance suffix is stripped
    assert "device_i_am" not in paths


def test_handle_report_with_no_observed_cache_is_no_op():
    """Constructor allows observed_path_cache to be None; no crash."""
    coordinator = MagicMock()
    coordinator.observed_path_cache = None
    subentry = MagicMock()
    subentry.data = {"did": "lumi.test.001"}
    device = Device(coordinator=coordinator, subentry=subentry, derived=())
    device.MODEL = "any.model"
    device.handle_report(_StubReport({"2.164.20536": "v"}))
    # No assertion needed: must not raise.


def test_handle_report_skips_record_when_model_empty(monkeypatch):
    from custom_components.aqara_lanlink.device.observed_path_cache import (
        ObservedPathCache,
    )

    class FakeStore:
        def __init__(self, hass, version, key):
            self._key = key
        async def async_load(self):
            return None
        def async_delay_save(self, data_func, delay):
            pass

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    import asyncio
    cache = ObservedPathCache(MagicMock())
    asyncio.run(cache.async_load())
    device = _make_device("", cache)  # empty model
    device.handle_report(_StubReport({"2.164.20536": "v"}))
    assert cache.models() == []


async def test_handle_report_drops_paths_in_catalogue():
    """A pushed numeric path that the catalogue already covers does not
    enter ObservedPathCache."""
    from unittest.mock import MagicMock
    from custom_components.aqara_lanlink.device.base import Device
    from types import SimpleNamespace

    cache = MagicMock()
    coordinator = SimpleNamespace(observed_path_cache=cache)
    device = Device(
        coordinator=coordinator,
        subentry=SimpleNamespace(
            subentry_id="s1", data={"did": "d1", "model": "lumi.test"},
        ),
        derived=[],
    )
    device.MODEL = "lumi.test"
    device._known_wire_paths = {"5.160.33000"}
    device.handle_report(SimpleNamespace(values={"5.160.33000.1": "1"}))
    cache.record.assert_not_called()


async def test_handle_report_records_unknown_paths():
    """A pushed numeric path that is NOT in catalogue/overlay enters the
    candidate cache. The record call carries (did, model, path) so the
    Repair notification can identify the specific paired device that
    emitted the path."""
    from unittest.mock import MagicMock
    from custom_components.aqara_lanlink.device.base import Device
    from types import SimpleNamespace

    cache = MagicMock()
    coordinator = SimpleNamespace(observed_path_cache=cache)
    device = Device(
        coordinator=coordinator,
        subentry=SimpleNamespace(
            subentry_id="s1", data={"did": "d1", "model": "lumi.test"},
        ),
        derived=[],
    )
    device.MODEL = "lumi.test"
    device._known_wire_paths = set()
    device.handle_report(SimpleNamespace(values={"5.160.33000.1": "1"}))
    cache.record.assert_called_once_with("d1", "lumi.test", "5.160.33000")
