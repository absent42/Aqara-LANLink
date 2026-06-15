"""Tests for async_step_pick_device cluster-member hub inclusion.

Task 10: cluster-member hubs (devicetype==1, parentDeviceId=="") that are NOT
the connected tunnel-host must appear in the device picker so the user can
add them as subentries.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aqara_lanlink import config_flow as config_flow_module
from custom_components.aqara_lanlink.config_flow import AqaraDeviceSubentryFlow
from custom_components.aqara_lanlink.const import (
    CONF_ACTIVATION_HOST,
    CONF_ACTIVATION_PORT,
    CONF_AQARA_ACCOUNT,
    CONF_AQARA_REGION,
    CONF_AQARA_TOKEN,
    CONF_AQARA_USER_ID,
    CONF_HUB_DID,
    CONF_HUB_IP,
    CONF_HUB_MODEL,
    CONF_HUB_PORT,
    DOMAIN,
)

# ---------------------------------------------------------------------------
# Helpers shared with the main config-flow test module, replicated here so
# this file stands alone.
# ---------------------------------------------------------------------------


def _hub_entry(hass, *, hub_did: str = "lumi1.M3HUB") -> MockConfigEntry:
    """Build + register a minimal hub config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=hub_did,
        title="Hub M3",
        data={
            CONF_HUB_IP: "192.0.2.10",
            CONF_HUB_PORT: 59703,
            CONF_HUB_DID: hub_did,
            CONF_HUB_MODEL: "lumi.gateway.agl004",
            CONF_AQARA_ACCOUNT: "user@example.com",
            CONF_AQARA_REGION: "EU",
            CONF_AQARA_USER_ID: "USR",
            CONF_AQARA_TOKEN: "TOK",
        },
    )
    entry.add_to_hass(hass)
    return entry


def _build_subentry_flow(hass, entry: MockConfigEntry) -> AqaraDeviceSubentryFlow:
    """Wire a subentry flow instance the same way HA's flow manager would."""
    flow = AqaraDeviceSubentryFlow()
    flow.hass = hass
    flow.handler = (entry.entry_id, "device")
    flow.flow_id = "test_flow_id"
    flow.context = {"source": "user"}
    return flow


def _make_device_list_stub(device_list):
    """Return an async stub for _fetch_device_list that always returns device_list."""
    async def _async_devices(self, region, token):
        return device_list
    return _async_devices


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPickDeviceClusterMemberHub:
    """Cluster-member hubs must appear in the device picker."""

    async def test_cluster_member_hub_offered_connected_hub_excluded(
        self, hass,
    ) -> None:
        """The M100 (devicetype==1, parentDeviceId=="") must appear in the
        picker. The connected M3 (also devicetype==1) must NOT appear.
        A normal child of the M3 must still appear unchanged.
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected tunnel-host M3 -- must be excluded always.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Cluster-member M100 -- hub, not the connected host.
            # Must be INCLUDED as a candidate.
            {
                "did": "lumi3.M100HUB",
                "model": "lumi.gateway.agl008",
                "deviceName": "Hub M100",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Normal Zigbee child of M3 -- must still be offered.
            {
                "did": "lumi.CHILDSENSOR",
                "model": "lumi.light.agl003",
                "deviceName": "LED Bulb",
                "parentDeviceId": hub_did,
                "devicetype": 2,
            },
        ]

        async def _async_devices(self, region, token):
            return device_list

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = {opt["value"] for opt in options}

        # M100 cluster-member hub must be offered.
        assert "lumi3.M100HUB" in offered_dids, (
            "cluster-member hub M100 was not offered in the device picker"
        )
        # Connected M3 must never appear.
        assert hub_did not in offered_dids, (
            "connected tunnel-host hub must not appear in the device picker"
        )
        # The normal Zigbee child must still be offered.
        assert "lumi.CHILDSENSOR" in offered_dids

    async def test_devicetype_missing_not_offered_as_cluster_hub(
        self, hass,
    ) -> None:
        """A record with no devicetype key must not be added via the new
        cluster-member-hub path. It must only appear if its parentDeviceId
        is in the hub set (the existing logic).
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected hub.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Orphan record: no devicetype key, no parent. Must NOT appear.
            {
                "did": "lumi.ORPHAN",
                "model": "lumi.mystery.x",
                "deviceName": "Mystery Device",
                "parentDeviceId": "",
                # deliberately no "devicetype" key
            },
            # Normal child of M3.
            {
                "did": "lumi.CHILDSENSOR",
                "model": "lumi.light.agl003",
                "deviceName": "LED Bulb",
                "parentDeviceId": hub_did,
                "devicetype": 2,
            },
        ]

        async def _async_devices(self, region, token):
            return device_list

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = {opt["value"] for opt in options}

        # Orphan (no devicetype) must not be offered via the hub path.
        assert "lumi.ORPHAN" not in offered_dids
        # The connected hub must still be excluded.
        assert hub_did not in offered_dids
        # Normal child must still be offered.
        assert "lumi.CHILDSENSOR" in offered_dids

    async def test_no_double_listing_if_record_matches_both_paths(
        self, hass,
    ) -> None:
        """A record that would match both the existing parent-in-hub-set
        path and the new devicetype==1 path must appear exactly once.

        This exercises the guard against double-listing in an edge-case
        scenario where a hub record appears with a parentDeviceId that
        is also a hub (unusual but theoretically possible).
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected hub.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Another hub whose parentDeviceId is the M3 hub DID.
            # It would satisfy the old "parent in hub_dids" rule AND
            # the new "devicetype==1" rule simultaneously.
            {
                "did": "lumi1.SUB_HUB",
                "model": "lumi.gateway.agl008",
                "deviceName": "Sub Hub",
                "parentDeviceId": hub_did,
                "devicetype": 1,
            },
        ]

        async def _async_devices(self, region, token):
            return device_list

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = [opt["value"] for opt in options]

        # Must appear exactly once, not twice.
        assert offered_dids.count("lumi1.SUB_HUB") == 1
        # Connected hub must still be excluded.
        assert hub_did not in offered_dids

    async def test_cluster_member_hub_string_devicetype_offered(
        self, hass,
    ) -> None:
        """A cluster-member hub with devicetype as the string "1" (rather than
        int 1) must still be offered in the picker.

        Aqara's cloud has been observed returning numeric JSON fields as
        strings in some API versions. The _cloud_devicetype helper must
        normalise the raw value so string "1" is treated the same as int 1.
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected tunnel-host M3.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Cluster-member M100 with devicetype as a STRING -- must still
            # be recognised as a hub and offered in the picker.
            {
                "did": "lumi3.M100HUB",
                "model": "lumi.gateway.agl008",
                "deviceName": "Hub M100",
                "parentDeviceId": "",
                "devicetype": "1",  # string, not int
            },
        ]

        async def _async_devices(self, region, token):
            return device_list

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                return_value=None,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _async_devices,
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = {opt["value"] for opt in options}

        # M100 with string "1" devicetype must be offered.
        assert "lumi3.M100HUB" in offered_dids, (
            "cluster-member hub with string devicetype '1' was not offered"
        )
        # Connected hub must still be excluded.
        assert hub_did not in offered_dids


# ---------------------------------------------------------------------------
# Standalone Wi-Fi device activation (FP2, lumi.motion.agl001).
#
# A standalone Wi-Fi device is cloud devicetype 8 with an empty
# parentDeviceId and is NOT in the hub topology, so it falls through all
# three passes of _build_candidate_records. The activation path is a 4th,
# additive candidate source: it is offered only when discovered on the LAN,
# validated as an Aqara TLS endpoint, and absent from the topology. On add,
# the hub is poked into relaying it.
# ---------------------------------------------------------------------------


_FP2_DID = "lumi1.fp2"
_FP2_MODEL = "lumi.motion.agl001"
_FP2_HOST = "10.1.20.160"


def _fp2_device_list(hub_did: str) -> list[dict]:
    """Cloud list: the connected hub + a standalone FP2 (devicetype 8)."""
    return [
        {
            "did": hub_did,
            "model": "lumi.gateway.agl004",
            "deviceName": "Hub M3",
            "parentDeviceId": "",
            "devicetype": 1,
        },
        {
            "did": _FP2_DID,
            "model": _FP2_MODEL,
            "deviceName": "Aqara FP2",
            "parentDeviceId": "",
            "devicetype": 8,
        },
    ]


def _fp2_lan_record():
    """An mDNS discovery record matching the FP2 on the LAN."""
    return SimpleNamespace(
        host=_FP2_HOST, port=443, did=_FP2_DID, name="Aqara-FP2",
    )


class TestStandaloneActivationCandidates:
    """Standalone Wi-Fi devices on the LAN must be offered for activation."""

    async def test_standalone_device_offered_when_on_lan_and_not_in_topology(
        self, hass,
    ) -> None:
        """An FP2 discovered on the LAN with a valid Aqara endpoint, not in
        the topology, must be offered and its activation endpoint recorded.
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        # Topology contains only the hub: the FP2 is standalone.
        stub_hub = SimpleNamespace(lanlink_topology_dids=frozenset({hub_did}))
        entry.runtime_data = SimpleNamespace(hub=stub_hub)
        flow = _build_subentry_flow(hass, entry)

        with (
            patch.object(
                AqaraDeviceSubentryFlow,
                "_fetch_device_list",
                _make_device_list_stub(_fp2_device_list(hub_did)),
            ),
            patch.object(
                config_flow_module,
                "discover_lan_devices",
                AsyncMock(return_value=[_fp2_lan_record()]),
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered = {opt["value"]: opt["label"] for opt in options}

        assert _FP2_DID in offered, "FP2 was not offered in the picker"
        assert "needs activation" in offered[_FP2_DID]
        assert flow._activation_endpoints[_FP2_DID] == (_FP2_HOST, 443)

    async def test_standalone_device_in_topology_not_double_offered(
        self, hass,
    ) -> None:
        """A devicetype-8 device that is already in the topology is handled by
        the normal Pass-3 path and must NOT be added via the activation path
        (no activation endpoint recorded for it).
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        # FP2 is in the topology push this time.
        stub_hub = SimpleNamespace(
            lanlink_topology_dids=frozenset({hub_did, _FP2_DID}),
        )
        entry.runtime_data = SimpleNamespace(hub=stub_hub)
        flow = _build_subentry_flow(hass, entry)

        with (
            patch.object(
                AqaraDeviceSubentryFlow,
                "_fetch_device_list",
                _make_device_list_stub(_fp2_device_list(hub_did)),
            ),
            patch.object(
                config_flow_module,
                "discover_lan_devices",
                AsyncMock(return_value=[_fp2_lan_record()]),
            ),
        ):
            result = await flow.async_step_user()

        # The activation path must have skipped it (it is in topology).
        assert _FP2_DID not in flow._activation_endpoints
        # It may still be offered once via Pass 3 (topology path).
        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = [opt["value"] for opt in options]
        assert offered_dids.count(_FP2_DID) == 1

    async def test_activate_relay_called_on_add_and_host_persisted(
        self, hass,
    ) -> None:
        """Adding the FP2 must call activate_relay and persist the activation
        host/port on the created subentry's data.
        """
        hub_did = "lumi1.M3HUB"
        entry = _hub_entry(hass, hub_did=hub_did)
        # During render the FP2 must be standalone (only the hub in topology)
        # so the activation path offers it. The post-activation wait is made
        # fast by patching asyncio.sleep; the loop then exits when the stub
        # topology (which lacks the FP2) is exhausted.
        stub_hub = SimpleNamespace(
            lanlink_topology_dids=frozenset({hub_did}),
        )
        entry.runtime_data = SimpleNamespace(hub=stub_hub)
        flow = _build_subentry_flow(hass, entry)

        activate_mock = AsyncMock()

        with (
            patch.object(
                AqaraDeviceSubentryFlow,
                "_fetch_device_list",
                _make_device_list_stub(_fp2_device_list(hub_did)),
            ),
            patch.object(
                config_flow_module,
                "discover_lan_devices",
                AsyncMock(return_value=[_fp2_lan_record()]),
            ),
            patch.object(
                config_flow_module, "activate_relay", activate_mock,
            ),
            patch.object(
                config_flow_module.asyncio, "sleep", AsyncMock(),
            ),
        ):
            # First render populates _activation_endpoints + _devices_by_did.
            await flow.async_step_user()
            # Submit the picker with the FP2 selected.
            result = await flow.async_step_pick_device({"device": _FP2_DID})

        assert result["type"] == FlowResultType.CREATE_ENTRY
        activate_mock.assert_awaited_once_with(_FP2_HOST, _FP2_DID, 443)
        data = result["data"]
        assert data[CONF_ACTIVATION_HOST] == _FP2_HOST
        assert data[CONF_ACTIVATION_PORT] == 443


class TestPickDeviceTopologyPushCameras:
    """Cameras (devicetype==8, parentDeviceId=="") must be offered when their
    DID appears in the connected hub's LANLink topology push.

    Task 13: the candidate set is extended so a cloud device whose DID is in
    hub.lanlink_topology_dids is also a candidate, subject to the guards that:
      - it must not be the hub's own DID
      - it must not already be in candidate_dids (dedup)
    """

    async def test_camera_in_topology_push_is_offered(self, hass) -> None:
        """A camera (devicetype==8, parentDeviceId=="") that the connected hub
        reported via the LANLink topology push must appear in the device picker.
        """
        hub_did = "lumi1.M3HUB"
        camera_did = "lumi.camera.TESTCAM001"
        entry = _hub_entry(hass, hub_did=hub_did)

        # Wire a stub runtime_data onto the entry so the flow can read
        # lanlink_topology_dids.  The SimpleNamespace mirrors the shape of
        # AqaraLanLinkRuntimeData closely enough for the defensive getattr
        # chain in _render_pick_device_form.
        stub_hub = SimpleNamespace(
            lanlink_topology_dids=frozenset({hub_did, camera_did}),
        )
        entry.runtime_data = SimpleNamespace(hub=stub_hub)

        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected tunnel-host M3.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # G400 camera -- devicetype==8, no parent -- in the topology push.
            {
                "did": camera_did,
                "model": "lumi.camera.agl013",
                "deviceName": "Aqara G400",
                "parentDeviceId": "",
                "devicetype": 8,
            },
        ]

        # Patch get_device_class: camera model is a registered override;
        # hub model is not (the hub DID is excluded by position in the list).
        # Using patch.object guards against registry state from other tests.
        def _gcls(model):
            if model == "lumi.camera.agl013":
                return object  # any non-None sentinel counts as _SUPPORTED_OVERRIDE
            return None

        with (
            patch.object(
                config_flow_module.registry,
                "get_device_class",
                side_effect=_gcls,
            ),
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _make_device_list_stub(device_list),
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = {opt["value"] for opt in options}

        # Camera in the topology push must be offered.
        assert camera_did in offered_dids, (
            "camera DID in LANLink topology push was not offered in the device picker"
        )
        # Connected hub must never appear.
        assert hub_did not in offered_dids

    async def test_camera_not_in_topology_push_not_offered(self, hass) -> None:
        """A camera (devicetype==8, parentDeviceId=="") whose DID is NOT in the
        hub's LANLink topology push must NOT be offered in the picker.

        This confirms the topology-push gate is enforced: the mere presence of
        devicetype==8 in the cloud list is not enough; the device must also be
        announced via the hub's topology push.
        """
        hub_did = "lumi1.M3HUB"
        camera_did = "lumi.camera.OTHERCAM"
        entry = _hub_entry(hass, hub_did=hub_did)

        # The topology push contains only the hub itself -- the camera is absent.
        stub_hub = SimpleNamespace(
            lanlink_topology_dids=frozenset({hub_did}),
        )
        entry.runtime_data = SimpleNamespace(hub=stub_hub)

        flow = _build_subentry_flow(hass, entry)

        device_list = [
            # Connected tunnel-host M3.
            {
                "did": hub_did,
                "model": "lumi.gateway.agl004",
                "deviceName": "Hub M3",
                "parentDeviceId": "",
                "devicetype": 1,
            },
            # Camera NOT in the topology push.
            {
                "did": camera_did,
                "model": "lumi.camera.agl013",
                "deviceName": "Aqara G400 (other)",
                "parentDeviceId": "",
                "devicetype": 8,
            },
        ]

        with (
            patch.object(
                AqaraDeviceSubentryFlow, "_fetch_device_list", _make_device_list_stub(device_list),
            ),
        ):
            result = await flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "pick_device"

        # When only the hub itself is in the topology set (no addable devices
        # in either the hub-child or topology path), the picker still renders
        # a "device" selector, but its only option is the manual-IP sentinel:
        # the camera NOT in the topology push must not appear as a real choice.
        schema = result["data_schema"].schema
        key = next(k for k in schema if str(k) == "device")
        options = schema[key].config["options"]
        offered_dids = {opt["value"] for opt in options}
        assert offered_dids == {config_flow_module._MANUAL_ACTIVATE_SENTINEL}, (
            "camera NOT in the topology push must not appear in the picker"
        )
