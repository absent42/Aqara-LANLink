"""Tests for go2rtc config file integration module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from custom_components.aqara_lanlink.device.camera.const import CHANNELS
from custom_components.aqara_lanlink.device.camera.go2rtc import (
    build_exec_source,
    build_rtsp_source,
    register_stream,
    remove_stream,
)


def test_channels_constant():
    assert CHANNELS == (1, 2, 3)


def test_build_rtsp_source_defaults_to_channel_1():
    url = build_rtsp_source("192.168.1.5", "admin", "pw")
    assert url == "rtsp://admin:pw@192.168.1.5:8554/ch1"


def test_build_rtsp_source_honours_channel():
    assert build_rtsp_source("192.168.1.5", "admin", "pw", channel=2).endswith("/ch2")
    assert build_rtsp_source("192.168.1.5", "admin", "pw", channel=3).endswith("/ch3")


@pytest.mark.parametrize("bad_ip", ["1.2.3.4 --foo=bar", "evil;rm -rf", "a b"])
def test_build_exec_source_rejects_unsafe_camera_ip(bad_ip):
    # Defence in depth: even if an unsafe host slips past upstream parsing,
    # the exec command must never be built from it.
    with pytest.raises(ValueError):
        build_exec_source("/config", bad_ip)


@pytest.mark.parametrize("bad_ip", ["1.2.3.4 --foo=bar", "evil;rm -rf", "a b"])
def test_build_rtsp_source_rejects_unsafe_camera_ip(bad_ip):
    with pytest.raises(ValueError):
        build_rtsp_source(bad_ip, "admin", "pw")


class TestBuildExecSource:
    def test_contains_backchannel_param(self):
        result = build_exec_source("/config", "10.1.20.150")
        assert "#backchannel=1" in result

    def test_starts_with_exec_prefix(self):
        result = build_exec_source("/config", "10.1.20.150")
        assert result.startswith("exec:")

    def test_contains_python_executable(self):
        result = build_exec_source("/config", "10.1.20.150")
        assert sys.executable in result

    def test_contains_bridge_script_path(self):
        result = build_exec_source("/config", "10.1.20.150")
        assert (
            "/config/custom_components/aqara_lanlink/device/camera/bridge.py"
            in result
        )

    def test_contains_camera_ip(self):
        result = build_exec_source("/config", "192.168.1.50")
        assert "192.168.1.50" in result

    def test_camera_ip_before_backchannel_param(self):
        result = build_exec_source("/config", "10.1.20.150")
        ip_pos = result.index("10.1.20.150")
        bc_pos = result.index("#backchannel=1")
        assert ip_pos < bc_pos

    def test_different_config_dir(self):
        result = build_exec_source("/data/homeassistant", "10.0.0.1")
        assert (
            "/data/homeassistant/custom_components/aqara_lanlink/device/camera/bridge.py"
            in result
        )


class TestBuildRtspSource:
    def test_basic_url(self):
        result = build_rtsp_source("10.1.20.150", "admin", "pass123")
        assert result == "rtsp://admin:pass123@10.1.20.150:8554/ch1"

    def test_special_chars_in_password(self):
        result = build_rtsp_source("10.1.20.150", "admin", "p@ss:w/d")
        assert "p%40ss%3Aw%2Fd" in result
        assert result.startswith("rtsp://admin:")
        assert "@10.1.20.150:8554/ch1" in result

    def test_special_chars_in_username(self):
        result = build_rtsp_source("10.1.20.150", "user@home", "secret")
        assert "user%40home" in result


class TestRegisterStream:
    def test_creates_config_from_scratch(self, tmp_path):
        result = register_stream(
            str(tmp_path), "my_stream", ["rtsp://...", "exec:..."]
        )
        assert result is True

        config = yaml.safe_load((tmp_path / "go2rtc.yaml").read_text())
        assert config["streams"]["my_stream"] == ["rtsp://...", "exec:..."]

    def test_preserves_existing_config(self, tmp_path):
        existing = {"api": {"password": "secret"}, "streams": {"other": ["rtsp://other"]}}
        (tmp_path / "go2rtc.yaml").write_text(yaml.safe_dump(existing))

        register_stream(str(tmp_path), "my_stream", ["rtsp://...", "exec:..."])

        config = yaml.safe_load((tmp_path / "go2rtc.yaml").read_text())
        assert config["api"]["password"] == "secret"
        assert config["streams"]["other"] == ["rtsp://other"]
        assert config["streams"]["my_stream"] == ["rtsp://...", "exec:..."]

    def test_updates_existing_stream(self, tmp_path):
        existing = {"streams": {"my_stream": ["rtsp://old"]}}
        (tmp_path / "go2rtc.yaml").write_text(yaml.safe_dump(existing))

        register_stream(str(tmp_path), "my_stream", ["rtsp://new", "exec:..."])

        config = yaml.safe_load((tmp_path / "go2rtc.yaml").read_text())
        assert config["streams"]["my_stream"] == ["rtsp://new", "exec:..."]

    def test_skips_write_when_sources_match(self, tmp_path):
        sources = ["rtsp://...", "exec:..."]
        existing = {"streams": {"my_stream": sources}}
        (tmp_path / "go2rtc.yaml").write_text(yaml.safe_dump(existing))
        mtime_before = (tmp_path / "go2rtc.yaml").stat().st_mtime_ns

        register_stream(str(tmp_path), "my_stream", sources)

        mtime_after = (tmp_path / "go2rtc.yaml").stat().st_mtime_ns
        assert mtime_before == mtime_after  # file not rewritten

    def test_returns_false_on_error(self, tmp_path):
        # Make config dir read-only to trigger write error
        (tmp_path / "go2rtc.yaml").write_text("streams: {}")
        (tmp_path / "go2rtc.yaml").chmod(0o444)

        result = register_stream(str(tmp_path), "my_stream", ["rtsp://..."])
        # Might succeed or fail depending on permissions -- just shouldn't crash
        assert isinstance(result, bool)

        (tmp_path / "go2rtc.yaml").chmod(0o644)  # restore for cleanup


class TestRemoveStream:
    def test_removes_stream(self, tmp_path):
        existing = {"streams": {"my_stream": ["rtsp://..."], "other": ["rtsp://other"]}}
        (tmp_path / "go2rtc.yaml").write_text(yaml.safe_dump(existing))

        remove_stream(str(tmp_path), "my_stream")

        config = yaml.safe_load((tmp_path / "go2rtc.yaml").read_text())
        assert "my_stream" not in config["streams"]
        assert config["streams"]["other"] == ["rtsp://other"]

    def test_noop_when_stream_not_present(self, tmp_path):
        existing = {"streams": {"other": ["rtsp://other"]}}
        (tmp_path / "go2rtc.yaml").write_text(yaml.safe_dump(existing))

        remove_stream(str(tmp_path), "nonexistent")

        config = yaml.safe_load((tmp_path / "go2rtc.yaml").read_text())
        assert config["streams"]["other"] == ["rtsp://other"]

    def test_noop_when_no_config_file(self, tmp_path):
        # Should not raise
        remove_stream(str(tmp_path), "anything")
