"""Tests for ObservedPathCache."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from custom_components.aqara_lanlink.device.observed_path_cache import (
    ObservedPathCache,
)


_MODEL = "lumi.vibration.agl002"
_DID_A = "lumi1.DID_A"
_DID_B = "lumi1.DID_B"


@pytest.fixture
def fake_hass():
    """Minimal hass stub backed by an in-memory storage dict."""
    storage: dict[str, object] = {}

    class FakeStore:
        def __init__(self, hass, version, key):
            self._key = key
            self.delayed_save_calls: list[float] = []

        async def async_load(self):
            return storage.get(self._key)

        async def async_save(self, data):
            storage[self._key] = data

        def async_delay_save(self, data_func, delay):
            self.delayed_save_calls.append(delay)
            storage[self._key] = data_func()

    hass = MagicMock()
    return hass, storage, FakeStore


def test_async_load_returns_empty_when_storage_missing(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    assert cache.get_paths("any.model") == frozenset()
    assert cache.models() == []


def test_async_load_restores_v2_persisted_state(fake_hass, monkeypatch):
    """v2 storage is keyed {model: {did: [paths]}}; restore preserves both."""
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = {
        _MODEL: {
            _DID_A: ["2.164.20536", "2.164.20537"],
            _DID_B: ["2.164.20538"],
        },
    }
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    # get_paths aggregates across all dids for the model.
    assert cache.get_paths(_MODEL) == frozenset({
        "2.164.20536", "2.164.20537", "2.164.20538",
    })
    assert cache.models() == [_MODEL]


def test_async_load_discards_legacy_v1_entries(fake_hass, monkeypatch):
    """v1 storage (no DID, model -> list) is discarded on load: the DID is
    unrecoverable from path alone, and the runtime trait_policy gate
    means most v1 entries were administrative chatter that no longer
    gets recorded anyway. Genuinely-novel paths re-appear on next push.
    """
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = {
        _MODEL: ["2.164.20536", "2.164.20537"],  # v1 shape
    }
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    assert cache.get_paths(_MODEL) == frozenset()
    assert cache.models() == []


def test_async_load_tolerates_garbage_storage(fake_hass, monkeypatch):
    """Storage with completely-wrong types starts empty without crashing."""
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = "not a dict"
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    assert cache.models() == []


def test_record_adds_path_and_schedules_save(fake_hass, monkeypatch):
    hass, storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20536")
    assert cache.get_paths(_MODEL) == frozenset({"2.164.20536"})
    assert storage["aqara_lanlink.observed_paths"] == {
        _MODEL: {_DID_A: ["2.164.20536"]},
    }


def test_record_partitions_by_did(fake_hass, monkeypatch):
    """The same model on two different devices keeps separate buckets so
    the notification can name the actual paired device, not just a model."""
    hass, storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20536")
    cache.record(_DID_B, _MODEL, "2.164.20537")
    snapshot = storage["aqara_lanlink.observed_paths"]
    assert snapshot[_MODEL][_DID_A] == ["2.164.20536"]
    assert snapshot[_MODEL][_DID_B] == ["2.164.20537"]


def test_record_is_idempotent(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20536")
    cache.record(_DID_A, _MODEL, "2.164.20536")
    assert cache.get_paths(_MODEL) == frozenset({"2.164.20536"})


def test_record_rejects_empty_args(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    cache.record("", _MODEL, "2.164.20536")  # empty did
    cache.record(_DID_A, "", "2.164.20536")  # empty model
    cache.record(_DID_A, _MODEL, "")          # empty path
    assert cache.models() == []


def test_new_paths_count_tracks_post_load_records(fake_hass, monkeypatch):
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = {
        _MODEL: {_DID_A: ["2.164.20536"]},
    }
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    assert cache.new_paths_count() == 0
    # Re-recording a pre-load path doesn't count.
    cache.record(_DID_A, _MODEL, "2.164.20536")
    assert cache.new_paths_count() == 0
    cache.record(_DID_A, _MODEL, "2.164.20537")
    cache.record(_DID_B, _MODEL, "2.164.20538")
    assert cache.new_paths_count() == 2


def test_new_paths_by_device_partitions_by_did_and_model(fake_hass, monkeypatch):
    """new_paths_by_device returns one entry per (did, model) recorded
    after async_load. Each entry carries the paths so the formatter can
    enumerate per-device."""
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20537")
    cache.record(_DID_A, _MODEL, "2.164.20538")
    cache.record(_DID_B, _MODEL, "2.164.20537")
    cache.record(_DID_A, "lumi.switch.acn099", "1.1.85")

    entries = cache.new_paths_by_device()
    # Sorted by (model, did) for deterministic rendering.
    assert [(e["model"], e["did"]) for e in entries] == [
        ("lumi.switch.acn099", _DID_A),
        (_MODEL, _DID_A),
        (_MODEL, _DID_B),
    ]
    assert entries[0]["paths"] == frozenset({"1.1.85"})
    assert entries[1]["paths"] == frozenset({"2.164.20537", "2.164.20538"})
    assert entries[2]["paths"] == frozenset({"2.164.20537"})


def test_new_paths_by_device_omits_empty_buckets(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    # An emptied per-(model, did) bucket must not appear in the output.
    cache._new_since_setup[_MODEL] = {_DID_A: set()}
    assert cache.new_paths_by_device() == []


def test_callback_fires_on_every_new_path_not_duplicates(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    calls: list[int] = []
    cache = ObservedPathCache(hass, on_new_paths_callback=lambda: calls.append(1))
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20536")  # 1st new
    cache.record(_DID_A, _MODEL, "2.164.20537")  # 2nd new
    cache.record(_DID_A, _MODEL, "2.164.20536")  # dup, no fire
    cache.record(_DID_A, _MODEL, "2.164.20538")  # 3rd new
    assert calls == [1, 1, 1]


def test_callback_swallows_exceptions(fake_hass, monkeypatch):
    hass, _storage, FakeStore = fake_hass
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )

    def boom():
        raise RuntimeError("nope")

    cache = ObservedPathCache(hass, on_new_paths_callback=boom)
    asyncio.run(cache.async_load())
    cache.record(_DID_A, _MODEL, "2.164.20536")
    assert cache.get_paths(_MODEL) == frozenset({"2.164.20536"})


def test_prune_dropped_paths_clears_now_filtered_traits(fake_hass, monkeypatch):
    """prune_dropped_paths walks cache entries and discards paths the
    trait_policy now drops -- run on every async_setup to clean out
    candidate-paths entries recorded before a new DROP was added to
    the policy (e.g. the ZigbeeNetworkDiagnostics wholesale-drop in
    commit `680fa33`).
    """
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = {
        _MODEL: {
            _DID_A: ["1.205.33107", "2.164.20536"],  # LQI dropped, vibration kept
            _DID_B: ["1.205.33107"],
        },
        "lumi.switch.acn099": {
            _DID_A: ["0.128.32904"],  # BasicInformation.Mac -- dropped
        },
    }
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())

    # Mock dropped-paths lookup: simulates the trait_policy classification.
    dropped_by_model = {
        _MODEL: frozenset({"1.205.33107"}),
        "lumi.switch.acn099": frozenset({"0.128.32904"}),
    }
    removed = cache.prune_dropped_paths(
        lambda m: dropped_by_model.get(m, frozenset()),
    )
    assert removed == 3  # 1.205.33107 twice + 0.128.32904 once
    # Only the genuinely-novel path survives.
    assert cache.get_paths(_MODEL) == frozenset({"2.164.20536"})
    # Model that had all its paths pruned is gone entirely.
    assert "lumi.switch.acn099" not in cache.models()


def test_prune_dropped_paths_returns_zero_when_nothing_to_remove(fake_hass, monkeypatch):
    hass, storage, FakeStore = fake_hass
    storage["aqara_lanlink.observed_paths"] = {
        _MODEL: {_DID_A: ["2.164.20536"]},
    }
    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.observed_path_cache.Store",
        FakeStore,
    )
    cache = ObservedPathCache(hass)
    asyncio.run(cache.async_load())
    removed = cache.prune_dropped_paths(lambda m: frozenset())
    assert removed == 0
    assert cache.get_paths(_MODEL) == frozenset({"2.164.20536"})
