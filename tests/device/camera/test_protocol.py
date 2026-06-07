"""Tests for the Aqara doorbell protocol layer."""

import struct
import pytest

from custom_components.aqara_lanlink.device.camera.protocol import (
    crc16_kermit,
    build_packet,
    parse_packet,
    build_rtp_header,
    extract_adts_frames,
)
from custom_components.aqara_lanlink.device.camera.const import (
    MAGIC,
    TYPE_START_VOICE,
    TYPE_STOP_VOICE,
    TYPE_ACK,
    TYPE_HEARTBEAT,
)


# =============================================================================
# Task 2: CRC-16/KERMIT
# =============================================================================

class TestCrc16Kermit:
    def test_empty_input(self):
        """CRC of empty bytes is 0x0000."""
        assert crc16_kermit(b"") == 0x0000

    def test_standard_vector(self):
        """Standard CRC check value for b'123456789' is 0x906E."""
        assert crc16_kermit(b"123456789") == 0x906E

    def test_single_null_byte(self):
        """CRC of a single zero byte is 0xF078."""
        assert crc16_kermit(b"\x00") == 0xF078

    def test_returns_int(self):
        """Return type is int."""
        assert isinstance(crc16_kermit(b"abc"), int)

    def test_result_fits_in_16_bits(self):
        """Result is always a 16-bit unsigned value."""
        for data in [b"", b"\xff", b"hello world", b"\x00" * 100]:
            result = crc16_kermit(data)
            assert 0 <= result <= 0xFFFF

    def test_different_data_different_crc(self):
        """Different inputs produce different CRCs."""
        assert crc16_kermit(b"abc") != crc16_kermit(b"abd")


# =============================================================================
# Task 3: LmLocalPacket build and parse
# =============================================================================

SESSION_TS = 1_700_000_000_000  # arbitrary epoch-ms value for testing


class TestBuildPacket:
    def test_start_voice_length(self):
        """START_VOICE packet is 2+1+2+8+2 = 15 bytes."""
        pkt = build_packet(TYPE_START_VOICE, SESSION_TS)
        assert len(pkt) == 15

    def test_stop_voice_length(self):
        """STOP_VOICE packet has the same length as START_VOICE."""
        pkt = build_packet(TYPE_STOP_VOICE, SESSION_TS)
        assert len(pkt) == 15

    def test_heartbeat_length(self):
        """HEARTBEAT packet has the same 15-byte length."""
        pkt = build_packet(TYPE_HEARTBEAT, SESSION_TS)
        assert len(pkt) == 15

    def test_ack_length(self):
        """ACK packet uses a 1-byte payload: 2+1+2+1+2 = 8 bytes."""
        pkt = build_packet(TYPE_ACK, 0)
        assert len(pkt) == 8

    def test_magic_prefix(self):
        """All packets start with the magic bytes 0xFE 0xEF."""
        for pkt_type in (TYPE_START_VOICE, TYPE_STOP_VOICE, TYPE_ACK, TYPE_HEARTBEAT):
            pkt = build_packet(pkt_type, SESSION_TS)
            assert pkt[:2] == MAGIC

    def test_type_byte(self):
        """Third byte encodes the packet type."""
        for pkt_type in (TYPE_START_VOICE, TYPE_STOP_VOICE, TYPE_ACK, TYPE_HEARTBEAT):
            pkt = build_packet(pkt_type, SESSION_TS)
            assert pkt[2] == pkt_type

    def test_payload_len_field_non_ack(self):
        """PayloadLen field is 8 for non-ACK packets."""
        for pkt_type in (TYPE_START_VOICE, TYPE_STOP_VOICE, TYPE_HEARTBEAT):
            pkt = build_packet(pkt_type, SESSION_TS)
            payload_len = struct.unpack(">H", pkt[3:5])[0]
            assert payload_len == 8

    def test_payload_len_field_ack(self):
        """PayloadLen field is 1 for ACK packets."""
        pkt = build_packet(TYPE_ACK, 0)
        payload_len = struct.unpack(">H", pkt[3:5])[0]
        assert payload_len == 1

    def test_crc_is_valid(self):
        """CRC appended to packet verifies correctly."""
        pkt = build_packet(TYPE_START_VOICE, SESSION_TS)
        # CRC covers bytes from type through end of payload (pkt[2:-2])
        crc_in_packet = struct.unpack(">H", pkt[-2:])[0]
        assert crc16_kermit(pkt[2:-2]) == crc_in_packet

    def test_ack_value_truncated_to_byte(self):
        """ACK payload_value is masked to 8 bits."""
        pkt = build_packet(TYPE_ACK, 0x1FF)
        assert pkt[5] == 0xFF  # only low 8 bits


class TestParsePacket:
    def test_roundtrip_start_voice(self):
        """build then parse START_VOICE returns the original type and value."""
        pkt = build_packet(TYPE_START_VOICE, SESSION_TS)
        result = parse_packet(pkt)
        assert result is not None
        assert result["type"] == TYPE_START_VOICE
        assert result["type_name"] == "START_VOICE"
        assert result["value"] == SESSION_TS

    def test_roundtrip_stop_voice(self):
        """build then parse STOP_VOICE round-trips correctly."""
        pkt = build_packet(TYPE_STOP_VOICE, SESSION_TS)
        result = parse_packet(pkt)
        assert result is not None
        assert result["type"] == TYPE_STOP_VOICE
        assert result["type_name"] == "STOP_VOICE"
        assert result["value"] == SESSION_TS

    def test_roundtrip_heartbeat(self):
        """build then parse HEARTBEAT round-trips correctly."""
        pkt = build_packet(TYPE_HEARTBEAT, SESSION_TS)
        result = parse_packet(pkt)
        assert result is not None
        assert result["type"] == TYPE_HEARTBEAT
        assert result["type_name"] == "HEARTBEAT"
        assert result["value"] == SESSION_TS

    def test_roundtrip_ack(self):
        """build then parse ACK returns value 0."""
        pkt = build_packet(TYPE_ACK, 0)
        result = parse_packet(pkt)
        assert result is not None
        assert result["type"] == TYPE_ACK
        assert result["type_name"] == "ACK"
        assert result["value"] == 0

    def test_invalid_magic_returns_none(self):
        """Packet with wrong magic bytes is rejected."""
        pkt = bytearray(build_packet(TYPE_START_VOICE, SESSION_TS))
        pkt[0] = 0x00
        assert parse_packet(bytes(pkt)) is None

    def test_too_short_returns_none(self):
        """Data shorter than minimum packet size is rejected."""
        assert parse_packet(b"\xFE\xEF\x00\x00") is None
        assert parse_packet(b"") is None

    def test_bad_crc_returns_none(self):
        """Packet with corrupted CRC is rejected."""
        pkt = bytearray(build_packet(TYPE_START_VOICE, SESSION_TS))
        pkt[-1] ^= 0xFF  # flip bits in last CRC byte
        assert parse_packet(bytes(pkt)) is None

    def test_invalid_type_returns_none(self):
        """Packet with type > 3 is rejected."""
        pkt = bytearray(build_packet(TYPE_START_VOICE, SESSION_TS))
        pkt[2] = 0x42  # unsupported type
        assert parse_packet(bytes(pkt)) is None

    def test_truncated_payload_returns_none(self):
        """Packet truncated in the middle of the payload is rejected."""
        pkt = build_packet(TYPE_START_VOICE, SESSION_TS)
        assert parse_packet(pkt[:10]) is None


# =============================================================================
# Task 4: RTP header builder and ADTS extraction
# =============================================================================

class TestBuildRtpHeader:
    def test_header_length(self):
        """RTP header is always 12 bytes."""
        hdr = build_rtp_header(97, 0, 0, 0)
        assert len(hdr) == 12

    def test_version_byte(self):
        """First byte encodes RTP version 2 (0x80)."""
        hdr = build_rtp_header(97, 0, 0, 0)
        assert hdr[0] == 0x80

    def test_payload_type_byte(self):
        """Second byte is the payload type."""
        hdr = build_rtp_header(97, 1000, 42, 7)
        assert hdr[1] == 97

    def test_seq_num_field(self):
        """Sequence number occupies bytes 2-3 (big-endian)."""
        hdr = build_rtp_header(97, 0, 0, 0x1234)
        seq = struct.unpack(">H", hdr[2:4])[0]
        assert seq == 0x1234

    def test_timestamp_field(self):
        """Timestamp occupies bytes 4-7 (big-endian)."""
        hdr = build_rtp_header(97, 0xDEADBEEF, 0, 0)
        ts = struct.unpack(">I", hdr[4:8])[0]
        assert ts == 0xDEADBEEF

    def test_ssrc_field(self):
        """SSRC occupies bytes 8-11 (big-endian)."""
        hdr = build_rtp_header(97, 0, 0xCAFEBABE, 0)
        ssrc = struct.unpack(">I", hdr[8:12])[0]
        assert ssrc == 0xCAFEBABE

    def test_seq_num_wraps_at_16_bits(self):
        """Sequence number wraps modulo 65536."""
        hdr = build_rtp_header(97, 0, 0, 0x10000)
        seq = struct.unpack(">H", hdr[2:4])[0]
        assert seq == 0

    def test_timestamp_wraps_at_32_bits(self):
        """Timestamp wraps modulo 2^32."""
        hdr = build_rtp_header(97, 0x1_0000_0000, 0, 0)
        ts = struct.unpack(">I", hdr[4:8])[0]
        assert ts == 0

    def test_ssrc_wraps_at_32_bits(self):
        """SSRC wraps modulo 2^32."""
        hdr = build_rtp_header(97, 0, 0x1_0000_0000, 0)
        ssrc = struct.unpack(">I", hdr[8:12])[0]
        assert ssrc == 0


def _make_adts_frame(frame_len: int) -> bytes:
    """Build a minimal syntactically-valid ADTS header + dummy payload."""
    # ADTS fixed header (simplified): sync + 5 bytes encoding frame_len
    # Bytes: 0xFF, 0xF1 (sync + ID=0 + layer + no CRC), 0x50 (AAC-LC, 16kHz, mono approx),
    # then bytes 3-5 carry frame_len
    b3 = (frame_len >> 11) & 0x03
    b4 = (frame_len >> 3) & 0xFF
    b5 = ((frame_len & 0x07) << 5) | 0x1F
    b6 = 0xFC
    header = bytes([0xFF, 0xF1, 0x50, b3, b4, b5, b6])
    payload = b"\x00" * (frame_len - 7)
    return header + payload


class TestExtractAdtsFrames:
    def test_empty_data(self):
        """Empty input returns empty list."""
        assert extract_adts_frames(b"") == []

    def test_single_frame(self):
        """A single valid ADTS frame is extracted correctly."""
        frame = _make_adts_frame(100)
        result = extract_adts_frames(frame)
        assert len(result) == 1
        assert result[0] == frame

    def test_two_consecutive_frames(self):
        """Two back-to-back frames are both extracted."""
        f1 = _make_adts_frame(80)
        f2 = _make_adts_frame(120)
        result = extract_adts_frames(f1 + f2)
        assert len(result) == 2
        assert result[0] == f1
        assert result[1] == f2

    def test_garbage_prefix_is_skipped(self):
        """Bytes before the first sync word are silently skipped."""
        frame = _make_adts_frame(100)
        garbage = b"\x00\x01\x02\x03"
        result = extract_adts_frames(garbage + frame)
        assert len(result) == 1
        assert result[0] == frame

    def test_data_too_short_for_frame_returns_empty(self):
        """Data shorter than 8 bytes yields no frames."""
        assert extract_adts_frames(b"\xFF\xF1\x50\x00\x00\x1F\xFC") == []

    def test_incomplete_frame_not_returned(self):
        """A frame whose declared length exceeds the buffer is not returned."""
        frame = _make_adts_frame(100)
        result = extract_adts_frames(frame[:50])  # truncate
        assert result == []
