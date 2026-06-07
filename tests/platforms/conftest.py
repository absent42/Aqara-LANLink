"""Shared fixtures and helpers for platform-layer tests.

These tests do not run inside HA's test harness; they construct entities
directly with mock hubs / synthetic devices and exercise the
descriptor-driven `apply_value` path plus platform-specific action
methods. The fixtures here are deliberately minimal -- one synthetic
descriptor + one bare Device + a SimpleNamespace subentry is enough.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.aqara_lanlink.device.base import Device


def make_subentry(
    subentry_id: str = "subentry_test_001",
    did: str = "test_did_001",
    model: str = "test.model.x",
    **extras: Any,
) -> SimpleNamespace:
    """Build a synthetic subentry with the fields the entity layer reads."""
    return SimpleNamespace(
        subentry_id=subentry_id,
        data={"did": did, "model": model, **extras},
    )


def make_hub(
    *, did: str = "test_hub_did", connected: bool = True,
) -> MagicMock:
    """Build a mock hub with the attributes `AqaraEntity` consults."""
    hub = MagicMock()
    hub.did = did
    hub.connected = connected
    hub.is_lan_direct_offline = lambda _did: False
    return hub


def make_device(
    descriptors: list[Any],
    *,
    coordinator: Any | None = None,
    subentry: SimpleNamespace | None = None,
    model: str = "test.model.x",
    display_name: str = "Test Device",
) -> Device:
    """Build a synthetic Device with the given descriptors as `derived`."""
    if coordinator is None:
        coordinator = MagicMock()
        coordinator.async_write = AsyncMock()
    if subentry is None:
        subentry = make_subentry()

    class _Sub(Device):
        MODEL = model
        DISPLAY_NAME = display_name
        MANUFACTURER = "Aqara"

    return _Sub(coordinator=coordinator, subentry=subentry, derived=descriptors)


@pytest.fixture
def hub() -> MagicMock:
    return make_hub()


@pytest.fixture
def subentry() -> SimpleNamespace:
    return make_subentry()
