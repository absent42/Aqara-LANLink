"""Tests for the integration's diagnostics export."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from custom_components.aqara_lanlink.device.overlay import Overlay
from custom_components.aqara_lanlink.device.traits import TraitSpec
from custom_components.aqara_lanlink.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)


def _make_entry(overlay: Overlay, candidate_paths: dict[str, list[str]]):
    coord = SimpleNamespace(
        observed_path_cache=SimpleNamespace(
            new_paths_by_model=lambda: candidate_paths,
            get_paths=lambda model: frozenset(candidate_paths.get(model, ())),
            models=lambda: list(candidate_paths.keys()),
        ),
    )
    return SimpleNamespace(
        entry_id="e1",
        runtime_data=SimpleNamespace(
            hub=coord, overlay=overlay,
        ),
    )


async def test_config_entry_diagnostics_dumps_overlay_and_counts(hass):
    overlay = Overlay()
    overlay.set_trait(
        model="lumi.test", pid="0.1.85",
        spec=TraitSpec(id="0.1.85", name="Temp", data_type="number", platform="sensor"),
        discovered_from="cloud_scan",
        discovered_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
    )
    entry = _make_entry(overlay, candidate_paths={"lumi.test": ["9.9.99999"]})
    out = await async_get_config_entry_diagnostics(hass, entry)
    assert "overlay" in out
    assert "lumi.test" in out["overlay"]
    assert out["overlay"]["lumi.test"]["0.1.85"]["name"] == "Temp"
    assert "model_summaries" in out
    assert "lumi.test" in out["model_summaries"]
    assert out["model_summaries"]["lumi.test"]["overlay_traits"] == 1
    assert "candidate_paths" in out
    assert "9.9.99999" in out["candidate_paths"]["lumi.test"]


async def test_diagnostics_includes_tunnel_health(hass):
    coord = SimpleNamespace(
        connected=True,
        did="lumi1.HUB",
        lanlink_topology_dids=frozenset({"lumi1.HUB", "lumi3.cam"}),
        seconds_since_last_report=lambda: 12.5,
        observed_path_cache=SimpleNamespace(
            new_paths_by_model=lambda: {},
            get_paths=lambda m: frozenset(),
            models=lambda: [],
        ),
    )
    runtime = SimpleNamespace(
        hub=coord, overlay=Overlay(),
        host_kind="hub", watchdog_last_rearm=0.0, rearm_in_flight=False,
        subscription_targets=[("d", "t", "did", "model")], cloud_region="EU",
    )
    entry = SimpleNamespace(entry_id="e1", runtime_data=runtime)
    out = await async_get_config_entry_diagnostics(hass, entry)
    assert "tunnel" in out
    t = out["tunnel"]
    assert t["connected"] is True
    assert t["host_kind"] == "hub"
    assert t["topology_did_count"] == 2
    assert t["seconds_since_last_report"] == 12.5
    assert t["subscription_target_count"] == 1
    assert t["region"] == "EU"


async def test_config_entry_diagnostics_redacts_topology_dids(hass):
    """The diagnostics output is run through async_redact_data, so the
    device-identifier list is redacted while its count is preserved."""
    from homeassistant.components.diagnostics import REDACTED

    coord = SimpleNamespace(
        connected=True,
        did="lumi1.HUB",
        lanlink_topology_dids=frozenset({"lumi1.HUB", "lumi3.cam"}),
        seconds_since_last_report=lambda: 1.0,
        observed_path_cache=SimpleNamespace(
            new_paths_by_model=lambda: {},
            get_paths=lambda m: frozenset(),
            models=lambda: [],
        ),
    )
    runtime = SimpleNamespace(
        hub=coord, overlay=Overlay(),
        host_kind="hub", watchdog_last_rearm=0.0, rearm_in_flight=False,
        subscription_targets=[], cloud_region="EU",
    )
    entry = SimpleNamespace(entry_id="e1", runtime_data=runtime)
    out = await async_get_config_entry_diagnostics(hass, entry)
    assert out["tunnel"]["topology_dids"] == REDACTED
    assert out["tunnel"]["topology_did_count"] == 2


def test_to_redact_covers_credential_keys():
    from custom_components.aqara_lanlink.const import (
        CONF_AQARA_ACCOUNT,
        CONF_AQARA_PASSWORD,
        CONF_AQARA_TOKEN,
        CONF_AQARA_USER_ID,
        CONF_PHONE_ID,
    )
    from custom_components.aqara_lanlink.diagnostics import TO_REDACT

    required = {
        CONF_AQARA_TOKEN,
        CONF_AQARA_USER_ID,
        CONF_AQARA_ACCOUNT,
        CONF_AQARA_PASSWORD,
        CONF_PHONE_ID,
        "rtsp_username",
        "rtsp_password",
    }
    assert required <= TO_REDACT


async def test_diagnostics_does_not_import_learned_path_cache():
    """The diagnostics module must not import path_cache or
    LearnedPathCache (deleted in Task 10)."""
    import custom_components.aqara_lanlink.diagnostics as diag_mod
    assert "LearnedPathCache" not in dir(diag_mod)
    assert "path_cache" not in dir(diag_mod)
