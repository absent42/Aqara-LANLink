"""Tests for hub.topology -- TopologyDevice + cloud/LANLink parsers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.aqara_lanlink.hub.topology import (
    TopologyDevice,
    parse_cloud_device,
    parse_lanlink_topology_push,
)


_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


HUB_DID = "lumi1.TESTHUB00001"
SUB_DID = "lumi3.TESTDEV000002"
ZIG_DID = "lumi.TESTZIG0000000A"


@pytest.fixture
def cloud_devices() -> list[dict]:
    """The ``result.devices`` array from the captured cloud response."""
    payload = json.loads((_FIXTURE_DIR / "cloud_device_list.json").read_text())
    return payload["result"]["devices"]


@pytest.fixture
def topology_capture() -> dict:
    """The full captured topology push frame."""
    return json.loads((_FIXTURE_DIR / "topology_capture.json").read_text())


class TestParseCloudDevice:
    def test_first_device_record_parses_to_topologydevice(self, cloud_devices):
        record = cloud_devices[0]
        parsed = parse_cloud_device(record)

        assert isinstance(parsed, TopologyDevice)
        assert parsed.did == "lumi3.TESTDEV000002"
        assert parsed.model == "lumi.camera.agl013"
        assert parsed.name == "Doorbell Camera G400"
        assert parsed.parent_did == ""
        assert parsed.state == 1
        assert parsed.firmware_version == "4.5.20_0026"
        # Raw cloud record is preserved verbatim for the auto-derive pipeline.
        assert parsed.cloud_metadata == record
        # And it's a copy -- mutating the original mustn't affect the dataclass.
        assert parsed.cloud_metadata is not record

    def test_hub_record_has_empty_parent_did(self, cloud_devices):
        # Find the M3 hub entry by DID.
        hub_record = next(r for r in cloud_devices if r["did"] == HUB_DID)
        parsed = parse_cloud_device(hub_record)

        assert parsed.did == HUB_DID
        assert parsed.parent_did == ""
        assert parsed.model == "lumi.gateway.agl004"
        assert parsed.name == "Hub M3"

    def test_sub_device_has_parent_did_set(self, cloud_devices):
        # The first zigbee remote's parentDeviceId is the M3 hub.
        sub_record = next(r for r in cloud_devices if r["did"] == ZIG_DID)
        parsed = parse_cloud_device(sub_record)

        assert parsed.did == ZIG_DID
        assert parsed.parent_did == HUB_DID
        assert parsed.model == "lumi.remote.b28ac1"
        # Captured deviceName -- not the originalName.
        assert parsed.name == "Wireless Remote Switch H1 (Double Rocker)"

    def test_falls_back_to_original_name_when_device_name_missing(self):
        record = {
            "did": "lumi.TESTZIG00000099",
            "model": "lumi.example",
            "originalName": "Original Name",
            "parentDeviceId": HUB_DID,
            "state": 1,
            "firmwareVersion": "0.0.0_0001",
        }
        parsed = parse_cloud_device(record)
        assert parsed.name == "Original Name"

    def test_defensive_defaults_for_missing_optional_fields(self):
        # Empty record -- everything defaults to "" or 0.
        parsed = parse_cloud_device({})
        assert parsed.did == ""
        assert parsed.model == ""
        assert parsed.name == ""
        assert parsed.parent_did == ""
        assert parsed.state == 0
        assert parsed.firmware_version == ""
        assert parsed.cloud_metadata == {}

    def test_state_unknown_string_coerces_to_zero(self):
        # ``state`` defaults via ``or 0`` so falsy values (None, "") map to 0.
        record = {"did": "x", "state": None}
        assert parse_cloud_device(record).state == 0
        record = {"did": "x", "state": ""}
        assert parse_cloud_device(record).state == 0


class TestParseLanlinkTopologyPush:
    def test_capture_parses_to_expected_did_set(self, topology_capture):
        data = topology_capture["data"]
        parsed = parse_lanlink_topology_push(data)

        assert isinstance(parsed, frozenset)
        # Hub DID + the one sub-device captured.
        assert parsed == frozenset({HUB_DID, SUB_DID})

    def test_multi_device_topology_returns_all_dids_including_hub(self):
        data = {
            HUB_DID: [HUB_DID, SUB_DID, ZIG_DID, "lumi.TESTZIG0000000B"],
        }
        parsed = parse_lanlink_topology_push(data)
        assert parsed == frozenset({
            HUB_DID, SUB_DID, ZIG_DID, "lumi.TESTZIG0000000B",
        })

    def test_none_returns_empty_frozenset(self):
        assert parse_lanlink_topology_push(None) == frozenset()

    def test_empty_dict_returns_empty_frozenset(self):
        assert parse_lanlink_topology_push({}) == frozenset()

    def test_wrong_shape_list_value_returns_empty(self):
        # Caller passes a non-dict.
        assert parse_lanlink_topology_push([HUB_DID, SUB_DID]) == frozenset()  # type: ignore[arg-type]

    def test_non_list_value_still_yields_hub_did_from_key(self):
        # If the value isn't a list we still take the (string) key as a DID.
        parsed = parse_lanlink_topology_push({HUB_DID: "garbage"})
        assert parsed == frozenset({HUB_DID})

    def test_non_string_dids_in_list_are_filtered(self):
        data = {HUB_DID: [HUB_DID, 42, None, SUB_DID, ""]}
        parsed = parse_lanlink_topology_push(data)
        assert parsed == frozenset({HUB_DID, SUB_DID})
