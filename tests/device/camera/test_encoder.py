"""Tests for PCM-to-AAC encoder."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.aqara_lanlink.device.camera.encoder import AACEncoder


class TestAACEncoder:
    def test_start_spawns_ffmpeg(self):
        with patch(
            "custom_components.aqara_lanlink.device.camera.encoder.subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc
            enc = AACEncoder()
            enc.start()
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert "ffmpeg" in cmd[0]
            assert "s16le" in cmd
            assert "16000" in cmd
            assert "aac" in cmd
            assert "adts" in cmd
            enc.stop()

    def test_encode_writes_to_stdin(self):
        with patch(
            "custom_components.aqara_lanlink.device.camera.encoder.subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdout = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            # Make select.select return empty so we skip the read loop
            with patch(
                "custom_components.aqara_lanlink.device.camera.encoder.select.select",
                return_value=([], [], []),
            ):
                enc = AACEncoder()
                enc.start()
                pcm_data = b"\x00" * 2048
                enc.write_pcm(pcm_data)
                mock_proc.stdin.write.assert_called_once_with(pcm_data)
                enc.stop()

    def test_stop_closes_process(self):
        with patch(
            "custom_components.aqara_lanlink.device.camera.encoder.subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            enc = AACEncoder()
            enc.start()
            enc.stop()
            mock_proc.stdin.close.assert_called_once()
            mock_proc.wait.assert_called_once()

    def test_not_started_raises(self):
        enc = AACEncoder()
        with pytest.raises(RuntimeError):
            enc.write_pcm(b"\x00" * 100)

    def test_stop_when_not_started_is_noop(self):
        enc = AACEncoder()
        enc.stop()  # should not raise
