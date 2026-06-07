"""Tests for the CameraRTSPURL (trait 20368) parser."""
from __future__ import annotations

import json
import pathlib

from custom_components.aqara_lanlink.device.camera.rtsp import (
    parse_camera_rtsp_url,
)


def test_parse_bare_rtsp_url():
    raw = "rtsp://admin:secret@10.1.20.150:8554/ch1"
    details = parse_camera_rtsp_url(raw)
    assert details is not None
    assert details.camera_ip == "10.1.20.150"
    assert details.username == "admin"
    assert details.password == "secret"
    assert details.stream_url == raw


def test_parse_json_object_picks_highest_resolution():
    # Ascending-order document, matching the real G400 capture shape.
    raw = json.dumps({
        "480p": "rtsp://u:p@10.1.20.150:8554/ch3",
        "960p": "rtsp://u:p@10.1.20.150:8554/ch2",
        "1536p": "rtsp://u:p@10.1.20.150:8554/ch1",
    })
    details = parse_camera_rtsp_url(raw)
    assert details is not None
    assert details.camera_ip == "10.1.20.150"
    assert details.stream_url == "rtsp://u:p@10.1.20.150:8554/ch1"


def test_parse_nested_json_object_with_urls():
    raw = json.dumps({
        "ip": "10.1.20.150",
        "urls": {
            "720p": "rtsp://admin:secret@10.1.20.150:8554/ch2",
            "1536p": "rtsp://admin:secret@10.1.20.150:8554/ch1",
        },
    })
    details = parse_camera_rtsp_url(raw)
    assert details is not None
    assert details.camera_ip == "10.1.20.150"
    assert details.username == "admin"
    assert details.password == "secret"
    assert details.stream_url == "rtsp://admin:secret@10.1.20.150:8554/ch1"


def test_parse_percent_encoded_credentials():
    raw = "rtsp://user%40x:p%2Fw@10.0.0.5:8554/s"
    details = parse_camera_rtsp_url(raw)
    assert details.username == "user@x"
    assert details.password == "p/w"


def test_parse_empty_returns_none():
    assert parse_camera_rtsp_url(None) is None
    assert parse_camera_rtsp_url("") is None
    assert parse_camera_rtsp_url("not a url and not json") is None


def test_parse_rejects_hostname_with_injection_chars():
    # A malicious/compromised camera returns an RTSP URL whose host carries
    # whitespace, which urlsplit preserves verbatim in .hostname. That value
    # would later be interpolated into a go2rtc exec command line, so the
    # parser must refuse it rather than hand back a poisoned camera_ip.
    raw = "rtsp://1.2.3.4 --foo=bar/ch1"
    assert parse_camera_rtsp_url(raw) is None


def test_parse_rejects_bare_url_with_unsafe_host():
    raw = "rtsp://evil;rm -rf:8554/ch1"
    assert parse_camera_rtsp_url(raw) is None


def test_parse_accepts_normal_host_after_validation():
    # Regression guard: a legitimate IP host still parses post-validation.
    details = parse_camera_rtsp_url("rtsp://u:p@10.1.20.150:8554/ch1")
    assert details is not None
    assert details.camera_ip == "10.1.20.150"


def test_parse_captured_fixture():
    """The real Task-12 captured fixture parses to the 1536p stream."""
    # parents[3] is the repo root (test file is 3 dirs deep under it).
    fixture = (
        pathlib.Path(__file__).parents[3]
        / "tests" / "fixtures" / "g400_camera_rtspurl.json"
    )
    raw = fixture.read_text()
    details = parse_camera_rtsp_url(raw)
    assert details is not None
    assert details.camera_ip == "10.1.20.150"
    assert details.username == "user"
    assert details.password == "pass"
    assert details.stream_url == "rtsp://user:pass@10.1.20.150:8554/ch1"
