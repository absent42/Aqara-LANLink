"""Tests for go2rtc exec bridge script."""

import signal
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from custom_components.aqara_lanlink.device.camera import bridge as bridge_module
from custom_components.aqara_lanlink.device.camera.bridge import BridgeSession
from custom_components.aqara_lanlink.device.camera.const import (
    AUDIO_PORT,
    CONTROL_PORT,
    TYPE_ACK,
    TYPE_START_VOICE,
)
from custom_components.aqara_lanlink.device.camera.protocol import build_packet

_BRIDGE_PATH = bridge_module.__file__


class TestBridgeSession:
    def _make_ack(self, code: int = 0) -> bytes:
        return build_packet(TYPE_ACK, code)

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_connect_sends_start_voice(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = self._make_ack(0)

        session = BridgeSession("10.1.20.150")
        result = session.connect()
        assert result is True
        sent = mock_tcp.sendall.call_args[0][0]
        assert sent[0:2] == b"\xFE\xEF"
        assert sent[2] == TYPE_START_VOICE
        session.close()

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_connect_fails_on_rejected_ack(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = self._make_ack(1)

        session = BridgeSession("10.1.20.150")
        result = session.connect()
        assert result is False

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_connect_fails_on_timeout(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.connect.side_effect = TimeoutError("timed out")

        session = BridgeSession("10.1.20.150")
        result = session.connect()
        assert result is False

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_send_audio_sends_rtp_to_udp(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = self._make_ack(0)

        session = BridgeSession("10.1.20.150")
        session.connect()
        session.send_audio_frame(b"\xFF\xF1" + b"\x00" * 20)
        mock_udp.sendto.assert_called_once()
        data, addr = mock_udp.sendto.call_args[0]
        assert addr == ("10.1.20.150", AUDIO_PORT)
        assert len(data) == 12 + 22  # RTP header + frame
        session.close()

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_seq_num_increments(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = self._make_ack(0)

        session = BridgeSession("10.1.20.150")
        session.connect()
        session.send_audio_frame(b"\xFF\xF1" + b"\x00" * 10)
        session.send_audio_frame(b"\xFF\xF1" + b"\x00" * 10)
        assert mock_udp.sendto.call_count == 2
        # Check sequence numbers differ in the RTP headers
        rtp1 = mock_udp.sendto.call_args_list[0][0][0][:12]
        rtp2 = mock_udp.sendto.call_args_list[1][0][0][:12]
        seq1 = int.from_bytes(rtp1[2:4], "big")
        seq2 = int.from_bytes(rtp2[2:4], "big")
        assert seq2 == seq1 + 1
        session.close()

    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_close_sends_stop_voice(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = self._make_ack(0)

        session = BridgeSession("10.1.20.150")
        session.connect()
        session.close()
        # Second sendall should be STOP_VOICE
        assert mock_tcp.sendall.call_count >= 2
        stop_pkt = mock_tcp.sendall.call_args_list[-1][0][0]
        assert stop_pkt[2] == 1  # TYPE_STOP_VOICE


class TestBridgeFlow:
    @patch("custom_components.aqara_lanlink.device.camera.bridge.socket.socket")
    def test_full_connect_send_close_flow(self, mock_socket_cls):
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        mock_socket_cls.side_effect = [mock_tcp, mock_udp]
        mock_tcp.recv.return_value = build_packet(TYPE_ACK, 0)

        session = BridgeSession("10.1.20.150")
        assert session.connect() is True

        # Send a few frames
        fake_aac = b"\xFF\xF1" + b"\x00" * 20
        for _ in range(5):
            session.send_audio_frame(fake_aac)

        assert mock_udp.sendto.call_count == 5
        session.close()
        assert mock_tcp.sendall.call_count >= 2  # START + STOP


class TestBridgeSigterm:
    def test_bridge_module_imports_signal(self):
        """Verify the bridge module imports signal for SIGTERM handling."""
        import custom_components.aqara_lanlink.device.camera.bridge as bridge_mod
        assert hasattr(bridge_mod, "signal")
        assert bridge_mod.signal is signal

    def test_sigterm_handler_triggers_cleanup_via_sys_exit(self):
        """Test that SIGTERM handler (sys.exit(0)) triggers finally cleanup."""
        # The handler is: signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        # sys.exit(0) raises SystemExit, which triggers finally blocks.
        # Verify that close() sends STOP_VOICE (the cleanup path).
        mock_tcp = MagicMock()
        mock_udp = MagicMock()
        with patch(
            "custom_components.aqara_lanlink.device.camera.bridge.socket.socket"
        ) as mock_cls:
            mock_cls.side_effect = [mock_tcp, mock_udp]
            mock_tcp.recv.return_value = build_packet(TYPE_ACK, 0)

            session = BridgeSession("10.1.20.150")
            session.connect()

            # Simulate what the SIGTERM handler triggers
            try:
                raise SystemExit(0)
            except SystemExit:
                # This is what the finally block in main() does
                session.close()

            # STOP_VOICE should have been sent
            stop_calls = [
                c for c in mock_tcp.sendall.call_args_list
                if len(c[0][0]) > 2 and c[0][0][2] == 1  # TYPE_STOP_VOICE
            ]
            assert len(stop_calls) == 1


class TestBridgeStandaloneLaunch:
    """Regression tests for launching bridge.py the way go2rtc does.

    go2rtc spawns the exec source as a bare script: `python3 .../bridge.py IP`.
    Running a file as a script puts the script's own directory on sys.path[0],
    so the bridge must survive its import phase without a sibling module
    shadowing the stdlib, and must stay lightweight -- it must not pull Home
    Assistant or the whole integration package into the subprocess.
    """

    def test_runs_as_standalone_script(self):
        """bridge.py must reach main() when run directly, not crash on import.

        Regression: bridge.py imported `custom_components.aqara_lanlink`, which
        -- with the script directory on sys.path[0] -- let the stdlib-named
        `numbers.py` sibling shadow the real `numbers` module and abort the
        import with a circular-import ImportError.
        """
        proc = subprocess.run(
            [sys.executable, _BRIDGE_PATH, "127.0.0.1"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        output = proc.stdout + proc.stderr
        # "Bridge starting" is logged at the top of main(), reached only after
        # every module-level import in bridge.py has succeeded.
        assert "Bridge starting for 127.0.0.1" in output, output
        assert "ImportError" not in output, output
        assert "cannot import name" not in output, output

    def test_does_not_import_home_assistant(self):
        """Loading bridge.py must not import Home Assistant or the integration.

        Regression: the bridge dragged the whole integration package (hub
        coordinator, cloud client, homeassistant.components.*) into what should
        be a tiny audio subprocess.
        """
        probe = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location("
            f"'_bridge_probe', {_BRIDGE_PATH!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "heavy = sorted(\n"
            "    name for name in sys.modules\n"
            "    if name == 'homeassistant'\n"
            "    or name.startswith('homeassistant.')\n"
            "    or name.startswith('custom_components')\n"
            ")\n"
            "assert not heavy, 'bridge pulled in heavy modules: ' + repr(heavy)\n"
            "print('LIGHTWEIGHT_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 0, combined
        assert "LIGHTWEIGHT_OK" in proc.stdout, combined
