import json
import pathlib

import pytest

from custom_components.aqara_lanlink.ptz import ioctl


def test_build_left_matches_layout():
    buf = ioctl.build(4138, {"action": "left"}, id=6)
    assert buf[:4] == b"lumi"
    assert buf[4:8] == (4138).to_bytes(4, "little")
    assert buf[8:12] == (6).to_bytes(4, "little")
    body = b'{"action":"left"}'
    assert buf[12:16] == len(body).to_bytes(4, "little")
    assert buf[16:] == body


def test_round_trip():
    buf = ioctl.build(4138, {"action": "right"}, id=9)
    iotype, _id, body = ioctl.parse(buf)
    assert (iotype, _id, body) == (4138, 9, {"action": "right"})


@pytest.mark.parametrize(
    "buf",
    [
        b"",  # empty
        b"xxxx",  # wrong tag, too short
        b"nope000000000000body",  # 4-byte non-tag prefix
        b"lumi\x00\x00\x00\x00\x00\x00\x00\x00",  # tag but < 16 bytes (no len field)
    ],
)
def test_parse_rejects_malformed_buffer_with_valueerror(buf):
    # Must raise ValueError (not AssertionError, which -O strips; not
    # IndexError) so the PTZ receive loop's handler catches it.
    with pytest.raises(ValueError):
        ioctl.parse(buf)


def test_parse_rejects_truncated_body():
    # Header declares 99 body bytes but only a few follow.
    buf = b"lumi" + (4138).to_bytes(4, "little") + (1).to_bytes(4, "little") \
        + (99).to_bytes(4, "little") + b"{}"
    with pytest.raises(ValueError):
        ioctl.parse(buf)


def test_parse_rejects_non_utf8_body():
    body = b"\xff\xfe\xfa"
    buf = b"lumi" + (4138).to_bytes(4, "little") + (1).to_bytes(4, "little") \
        + len(body).to_bytes(4, "little") + body
    with pytest.raises(ValueError):
        ioctl.parse(buf)


def test_captured_sample_decodes_after_header_strip():
    samples = json.loads(
        (pathlib.Path(__file__).parent / "fixtures/ioctl_samples.json").read_text()
    )
    s = next(x for x in samples if x["action"] == "left")
    raw = bytes.fromhex(s["plaintext_hex"])
    # 8-byte session header: f1 <type> 00 .. d1 ..
    assert raw[0] == 0xF1 and raw[4] == 0xD1
    iotype, _id, body = ioctl.parse(raw[8:])
    assert iotype == 4138 and body == {"action": "left"}
