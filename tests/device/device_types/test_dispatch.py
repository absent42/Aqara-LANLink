"""Tests for the per-deviceType dispatcher (returns _fallback until composers land)."""
from __future__ import annotations

import dataclasses
import inspect

import pytest

from custom_components.aqara_lanlink.device.device_types import (
    _base,
    _fallback,
    get_composer,
)


def test_get_composer_returns_callable():
    composer = get_composer("Light")
    assert callable(composer)


def test_get_composer_returns_fallback_for_unknown_device_type():
    composer = get_composer("CompletelyMadeUpDeviceType")
    assert composer is _fallback.compose


def test_get_composer_returns_fallback_for_unmapped_cluster_names():
    """Cluster names that never appear as a top-level deviceType (only as
    trait function_codes) route to _fallback if accidentally queried.
    HeaterCooler is the canonical example: it's a HeaterCooler.* trait
    family, never a deviceType in any captured V3 spec.
    """
    for device_type in ("HeaterCooler",):
        composer = get_composer(device_type)
        assert composer is _fallback.compose, (
            f"deviceType={device_type!r} should fall through to _fallback"
        )


def test_composer_signature_matches_contract():
    sig = inspect.signature(_fallback.compose)
    params = list(sig.parameters.keys())
    assert params == ["endpoint_id", "traits", "context"], (
        f"compose signature should be (endpoint_id, traits, context); got {params}"
    )


def test_compose_context_is_frozen_dataclass():
    ctx = _base.ComposeContext(model="lumi.test")
    assert dataclasses.is_dataclass(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.model = "lumi.other"  # type: ignore[misc]
