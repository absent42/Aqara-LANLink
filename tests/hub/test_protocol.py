"""Tests for the LANLink JSON message protocol layer."""

import json
import time

import pytest

from custom_components.aqara_lanlink.hub.protocol import (
    G400_MODEL,
    LANEntity,
    LANReport,
    LANLINK_CMD_CHECKIN,
    LANLINK_CMD_CHECKIN_DONE,
    LANLINK_CMD_KEEPALIVE,
    LANLINK_CMD_READ,
    LANLINK_CMD_REPORT,
    LANLINK_CMD_WRITE,
    LANLINK_TYPE_DEVICE,
    LANLINK_TYPE_SESSION,
    build_checkin,
    build_keepalive,
    build_read,
    build_write,
    parse_message,
)

# Shared test data
USER_ID = "u123456"
DEVICE_ID = "lumi.12345678901234"
TOKEN = "c508ff5d0658f049c10a45b0636d174fdad4"
SEQ = 42


# =============================================================================
# G400_MODEL constant
# =============================================================================

class TestG400Model:
    def test_value(self):
        """G400_MODEL must match the documented firmware model string."""
        assert G400_MODEL == "lumi.camera.agl013"

    def test_type(self):
        """G400_MODEL is a string."""
        assert isinstance(G400_MODEL, str)


# =============================================================================
# LANEntity dataclass
# =============================================================================

class TestLANEntity:
    def test_fields_stored(self):
        """LANEntity stores seq, type, cmd, and data fields."""
        entity = LANEntity(seq=1, type=LANLINK_TYPE_SESSION, cmd=LANLINK_CMD_CHECKIN, data={"key": "val"})
        assert entity.seq == 1
        assert entity.type == LANLINK_TYPE_SESSION
        assert entity.cmd == LANLINK_CMD_CHECKIN
        assert entity.data == {"key": "val"}

    def test_data_defaults_to_none(self):
        """data field defaults to None when not provided."""
        entity = LANEntity(seq=0, type=LANLINK_TYPE_SESSION, cmd=LANLINK_CMD_KEEPALIVE)
        assert entity.data is None


# =============================================================================
# build_checkin
# =============================================================================

class TestBuildCheckin:
    def setup_method(self):
        self.msg = build_checkin(SEQ, USER_ID, DEVICE_ID, TOKEN)

    def test_returns_dict(self):
        assert isinstance(self.msg, dict)

    def test_seq(self):
        assert self.msg["seq"] == SEQ

    def test_type(self):
        assert self.msg["type"] == LANLINK_TYPE_SESSION

    def test_cmd(self):
        assert self.msg["cmd"] == LANLINK_CMD_CHECKIN

    def test_data_present(self):
        assert "data" in self.msg
        assert isinstance(self.msg["data"], dict)

    def test_data_has_user_id(self):
        assert self.msg["data"]["user"] == USER_ID

    def test_data_has_device_id(self):
        assert self.msg["data"]["did"] == DEVICE_ID

    def test_data_has_aid(self):
        """aid field carries the cloud token."""
        assert self.msg["data"]["aid"] == TOKEN

    def test_is_json_serializable(self):
        """Message must serialize cleanly to JSON."""
        assert json.dumps(self.msg)


# =============================================================================
# build_keepalive
# =============================================================================

class TestBuildKeepalive:
    def setup_method(self):
        self.before = int(time.time() * 1000)
        self.msg = build_keepalive(SEQ)
        self.after = int(time.time() * 1000)

    def test_returns_dict(self):
        assert isinstance(self.msg, dict)

    def test_seq(self):
        assert self.msg["seq"] == SEQ

    def test_type(self):
        assert self.msg["type"] == LANLINK_TYPE_SESSION

    def test_cmd(self):
        assert self.msg["cmd"] == LANLINK_CMD_KEEPALIVE

    def test_data_present(self):
        assert "data" in self.msg
        assert isinstance(self.msg["data"], dict)

    def test_has_timestamp(self):
        """data must contain a 'timestamp' field with a current epoch-ms value."""
        ts = self.msg["data"]["timestamp"]
        assert isinstance(ts, int)
        assert self.before <= ts <= self.after

    def test_is_json_serializable(self):
        assert json.dumps(self.msg)


# =============================================================================
# build_read
# =============================================================================

class TestBuildRead:
    def setup_method(self):
        self.attrs = ["human_detect_enable", "mdtrigger_enable"]
        self.msg = build_read(SEQ, DEVICE_ID, G400_MODEL, self.attrs)

    def test_returns_dict(self):
        assert isinstance(self.msg, dict)

    def test_seq(self):
        assert self.msg["seq"] == SEQ

    def test_type(self):
        assert self.msg["type"] == LANLINK_TYPE_DEVICE

    def test_cmd(self):
        assert self.msg["cmd"] == LANLINK_CMD_READ

    def test_data_present(self):
        assert "data" in self.msg
        assert isinstance(self.msg["data"], dict)

    def test_data_has_did(self):
        assert self.msg["data"]["did"] == DEVICE_ID

    def test_data_has_sdid(self):
        """sdid equals did for WiFi devices."""
        assert self.msg["data"]["sdid"] == DEVICE_ID

    def test_data_has_model(self):
        assert self.msg["data"]["model"] == G400_MODEL

    def test_data_has_value(self):
        """value is a flat list of attribute name strings."""
        value = self.msg["data"]["value"]
        assert isinstance(value, list)
        assert value == self.attrs

    def test_is_json_serializable(self):
        assert json.dumps(self.msg)


# =============================================================================
# build_write
# =============================================================================

class TestBuildWrite:
    def setup_method(self):
        self.attrs = {"human_detect_enable": 1, "body_push_enable": 0}
        self.msg = build_write(SEQ, DEVICE_ID, G400_MODEL, self.attrs)

    def test_returns_dict(self):
        assert isinstance(self.msg, dict)

    def test_seq(self):
        assert self.msg["seq"] == SEQ

    def test_type(self):
        assert self.msg["type"] == LANLINK_TYPE_DEVICE

    def test_cmd(self):
        assert self.msg["cmd"] == LANLINK_CMD_WRITE

    def test_data_present(self):
        assert "data" in self.msg
        assert isinstance(self.msg["data"], dict)

    def test_data_has_did(self):
        assert self.msg["data"]["did"] == DEVICE_ID

    def test_data_has_sdid(self):
        assert self.msg["data"]["sdid"] == DEVICE_ID

    def test_data_has_model(self):
        assert self.msg["data"]["model"] == G400_MODEL

    def test_data_has_src(self):
        """src field must be present with provenance format '3,,{timestamp},,'."""
        src = self.msg["data"]["src"]
        assert isinstance(src, str)
        assert src.startswith("3,,")
        assert src.endswith(",,")

    def test_data_has_value_dict(self):
        """value is a flat dict of {attr: value} pairs."""
        value = self.msg["data"]["value"]
        assert isinstance(value, dict)
        assert value == self.attrs

    def test_is_json_serializable(self):
        assert json.dumps(self.msg)


# =============================================================================
# build_read / build_write with explicit sdid (gateway-relay framing)
# =============================================================================

HUB_DID = "lumi1.TESTHUB00001"
HUB_MODEL = "lumi.gateway.agl004"
SUB_DID = "lumi.TESTZIG0000000A"


class TestBuildReadWithExplicitSdid:
    """When ``sdid`` is provided, ``data.did`` and ``data.sdid`` differ.

    This is the gateway-relay framing required for sub-device reads:
    ``did`` carries the hub DID, ``sdid`` carries the sub-device DID,
    ``model`` carries the hub model. Without this framing the hub
    silently times out on sub-device reads.
    """

    def setup_method(self):
        self.attrs = ["8.0.2002"]
        self.msg = build_read(
            SEQ, HUB_DID, HUB_MODEL, self.attrs, sdid=SUB_DID,
        )

    def test_data_has_hub_did(self):
        assert self.msg["data"]["did"] == HUB_DID

    def test_data_has_sub_sdid(self):
        assert self.msg["data"]["sdid"] == SUB_DID

    def test_did_and_sdid_differ(self):
        assert self.msg["data"]["did"] != self.msg["data"]["sdid"]

    def test_data_has_hub_model(self):
        assert self.msg["data"]["model"] == HUB_MODEL

    def test_data_has_value(self):
        assert self.msg["data"]["value"] == self.attrs

    def test_is_json_serializable(self):
        assert json.dumps(self.msg)

    def test_sdid_must_be_keyword_only(self):
        """``sdid`` is keyword-only -- positional supply is rejected."""
        with pytest.raises(TypeError):
            build_read(SEQ, HUB_DID, HUB_MODEL, ["x"], SUB_DID)  # type: ignore[misc]

    def test_omitted_sdid_defaults_to_did(self):
        """Backward-compat: omitting ``sdid`` keeps ``data.sdid == data.did``."""
        msg = build_read(SEQ, DEVICE_ID, G400_MODEL, ["a"])
        assert msg["data"]["did"] == DEVICE_ID
        assert msg["data"]["sdid"] == DEVICE_ID


class TestBuildWriteWithExplicitSdid:
    """build_write mirrors build_read for gateway-relay framing."""

    def setup_method(self):
        self.attrs = {"8.0.2002": 1}
        self.msg = build_write(
            SEQ, HUB_DID, HUB_MODEL, self.attrs, sdid=SUB_DID,
        )

    def test_data_has_hub_did(self):
        assert self.msg["data"]["did"] == HUB_DID

    def test_data_has_sub_sdid(self):
        assert self.msg["data"]["sdid"] == SUB_DID

    def test_did_and_sdid_differ(self):
        assert self.msg["data"]["did"] != self.msg["data"]["sdid"]

    def test_data_has_hub_model(self):
        assert self.msg["data"]["model"] == HUB_MODEL

    def test_data_has_value(self):
        assert self.msg["data"]["value"] == self.attrs

    def test_is_json_serializable(self):
        assert json.dumps(self.msg)

    def test_sdid_must_be_keyword_only(self):
        with pytest.raises(TypeError):
            build_write(SEQ, HUB_DID, HUB_MODEL, {"x": 1}, SUB_DID)  # type: ignore[misc]

    def test_omitted_sdid_defaults_to_did(self):
        msg = build_write(SEQ, DEVICE_ID, G400_MODEL, {"a": 1})
        assert msg["data"]["did"] == DEVICE_ID
        assert msg["data"]["sdid"] == DEVICE_ID


# =============================================================================
# parse_message
# =============================================================================

VALID_REPORT_JSON = json.dumps({
    "seq": 10,
    "type": LANLINK_TYPE_DEVICE,
    "cmd": LANLINK_CMD_REPORT,
    "data": {
        "did": DEVICE_ID,
        "sdid": DEVICE_ID,
        "src": "cloud",
        "path": "/subdev/attribute",
        "time": 1700000000000,
        "value": [{"attr": "human_detect_enable", "value": 1}],
    },
})

VALID_CHECKIN_DONE_JSON = json.dumps({
    "seq": 1,
    "type": LANLINK_TYPE_SESSION,
    "cmd": LANLINK_CMD_CHECKIN_DONE,
    "data": {"result": 0},
})


class TestParseMessage:
    def test_valid_report_returns_entity(self):
        entity = parse_message(VALID_REPORT_JSON)
        assert entity is not None
        assert isinstance(entity, LANEntity)

    def test_report_seq(self):
        entity = parse_message(VALID_REPORT_JSON)
        assert entity.seq == 10

    def test_report_type(self):
        entity = parse_message(VALID_REPORT_JSON)
        assert entity.type == LANLINK_TYPE_DEVICE

    def test_report_cmd(self):
        entity = parse_message(VALID_REPORT_JSON)
        assert entity.cmd == LANLINK_CMD_REPORT

    def test_report_data_dict(self):
        entity = parse_message(VALID_REPORT_JSON)
        assert isinstance(entity.data, dict)

    def test_valid_checkin_done_returns_entity(self):
        entity = parse_message(VALID_CHECKIN_DONE_JSON)
        assert entity is not None
        assert entity.cmd == LANLINK_CMD_CHECKIN_DONE

    def test_invalid_json_returns_none(self):
        assert parse_message("{not valid json}") is None

    def test_empty_string_returns_none(self):
        assert parse_message("") is None

    def test_missing_cmd_returns_none(self):
        data = {"seq": 1, "type": LANLINK_TYPE_SESSION}
        assert parse_message(json.dumps(data)) is None

    def test_missing_type_returns_none(self):
        data = {"seq": 1, "cmd": LANLINK_CMD_CHECKIN_DONE}
        assert parse_message(json.dumps(data)) is None

    def test_missing_seq_returns_none(self):
        data = {"type": LANLINK_TYPE_SESSION, "cmd": LANLINK_CMD_CHECKIN_DONE}
        assert parse_message(json.dumps(data)) is None

    def test_non_dict_json_returns_none(self):
        """A valid JSON array is not a valid message."""
        assert parse_message(json.dumps([1, 2, 3])) is None


# =============================================================================
# LANReport dataclass and from_entity classmethod
# =============================================================================

REPORT_ENTITY = LANEntity(
    seq=10,
    type=LANLINK_TYPE_DEVICE,
    cmd=LANLINK_CMD_REPORT,
    data={
        "did": DEVICE_ID,
        "sdid": DEVICE_ID,
        "src": "10,,1776428273691,0.trg=1,,",
        "time": 1776428273691,
        "value": {"5.160.33000.1": "1"},
    },
)


class TestLANReport:
    def test_from_entity_returns_lan_report(self):
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report is not None
        assert isinstance(report, LANReport)

    def test_from_entity_did(self):
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report.did == DEVICE_ID

    def test_from_entity_sdid(self):
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report.sdid == DEVICE_ID

    def test_from_entity_src(self):
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report.src == "10,,1776428273691,0.trg=1,,"

    def test_from_entity_time(self):
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report.time == 1776428273691

    def test_from_entity_values(self):
        """values is the trait-ID-keyed dict from the report."""
        report = LANReport.from_entity(REPORT_ENTITY)
        assert report.values == {"5.160.33000.1": "1"}

    def test_from_entity_non_report_returns_none(self):
        """from_entity returns None for non-report command types."""
        checkin_done = LANEntity(
            seq=1,
            type=LANLINK_TYPE_SESSION,
            cmd=LANLINK_CMD_CHECKIN_DONE,
            data={"code": 0},
        )
        assert LANReport.from_entity(checkin_done) is None

    def test_from_entity_keepalive_returns_none(self):
        """from_entity returns None for keepalive entities."""
        ka = LANEntity(seq=5, type=LANLINK_TYPE_SESSION, cmd=LANLINK_CMD_KEEPALIVE, data={})
        assert LANReport.from_entity(ka) is None

    def test_from_entity_none_data_returns_none(self):
        """from_entity returns None if entity data is None."""
        entity = LANEntity(seq=1, type=LANLINK_TYPE_DEVICE, cmd=LANLINK_CMD_REPORT, data=None)
        assert LANReport.from_entity(entity) is None

    def test_from_entity_non_dict_value_returns_none(self):
        """Legacy path/value reports (non-dict value) are rejected."""
        entity = LANEntity(
            seq=1, type=LANLINK_TYPE_DEVICE, cmd=LANLINK_CMD_REPORT,
            data={"did": DEVICE_ID, "value": "legacy_string"},
        )
        assert LANReport.from_entity(entity) is None

    def test_lanreport_accepts_attr_name_keyed_values(self):
        """Synthetic initial-read reports use attr-name keys (not trait IDs).

        The read-on-connect path feeds read_done ``value`` dicts straight
        into a LANReport -- the validator must not reject them just because
        the keys aren't dotted trait IDs.
        """
        entity = LANEntity(
            seq=0, type="device", cmd="report",
            data={"did": "x", "sdid": "x", "value": {"body_sensivity": "1"}},
        )
        report = LANReport.from_entity(entity)
        assert report is not None
        assert report.values == {"body_sensivity": "1"}
