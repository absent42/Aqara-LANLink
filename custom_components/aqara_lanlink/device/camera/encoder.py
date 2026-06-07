"""Audio-to-AAC-ADTS encoder using ffmpeg subprocess.

Accepts raw audio input and produces AAC-LC ADTS frames matching the
Aqara doorbell's requirements: 16kHz, mono, 32kbps.

Supports two input formats:
- PCM s16le 16kHz mono (for direct use / go2rtc v1.9.14+)
- G.711 A-law 8kHz mono (for go2rtc v1.9.9 backchannel)
"""

from __future__ import annotations

import logging
import select
import subprocess

from .protocol import extract_adts_frames

_LOGGER = logging.getLogger(__name__)

FFMPEG_CMD_PCM = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    "-fflags", "nobuffer",
    "-flags", "low_delay",
    "-f", "s16le",
    "-ar", "16000",
    "-ac", "1",
    "-i", "pipe:0",
    "-c:a", "aac",
    "-profile:a", "aac_low",
    "-b:a", "32k",
    "-ar", "16000",
    "-ac", "1",
    "-f", "adts",
    "pipe:1",
]

FFMPEG_CMD_ALAW = [
    "ffmpeg",
    "-hide_banner",
    "-loglevel", "error",
    "-fflags", "nobuffer",
    "-flags", "low_delay",
    "-f", "alaw",
    "-ar", "8000",
    "-ac", "1",
    "-i", "pipe:0",
    "-c:a", "aac",
    "-profile:a", "aac_low",
    "-b:a", "32k",
    "-ar", "16000",
    "-ac", "1",
    "-f", "adts",
    "pipe:1",
]


class AACEncoder:
    """Encode audio to AAC-ADTS via ffmpeg subprocess."""

    def __init__(self, input_format: str = "pcm") -> None:
        """Initialize encoder.

        Args:
            input_format: "pcm" for s16le 16kHz mono, "alaw" for G.711 A-law 8kHz.
        """
        self._proc: subprocess.Popen | None = None
        self._buffer = bytearray()
        self._cmd = FFMPEG_CMD_ALAW if input_format == "alaw" else FFMPEG_CMD_PCM

    def start(self) -> None:
        """Start the ffmpeg encoder process."""
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
        )

    def write_pcm(self, pcm_data: bytes) -> list[bytes]:
        """Write audio data and return any available AAC-ADTS frames.

        Non-blocking read of ffmpeg stdout after writing data to stdin.
        Returns a list of complete ADTS frames (may be empty if ffmpeg
        hasn't produced output yet).
        """
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Encoder not started")

        try:
            self._proc.stdin.write(pcm_data)
            self._proc.stdin.flush()
        except BrokenPipeError:
            return []

        # Non-blocking read of available output
        frames: list[bytes] = []
        while select.select([self._proc.stdout], [], [], 0)[0]:
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                break
            self._buffer.extend(chunk)

        if self._buffer:
            frames = extract_adts_frames(bytes(self._buffer))
            consumed = sum(len(f) for f in frames)
            self._buffer = self._buffer[consumed:]

        return frames

    def flush(self) -> list[bytes]:
        """Close encoder input and return any remaining AAC-ADTS frames.

        Call this after the last write_pcm() to get frames still buffered
        inside the ffmpeg process.
        """
        if not self._proc or not self._proc.stdin:
            return []

        try:
            self._proc.stdin.close()
        except OSError:
            pass

        # Read all remaining output (blocking, with timeout)
        try:
            remaining, _ = self._proc.communicate(timeout=5)
            if remaining:
                self._buffer.extend(remaining)
        except subprocess.TimeoutExpired:
            _LOGGER.debug("ffmpeg flush timed out; trailing frames may be lost")

        frames: list[bytes] = []
        if self._buffer:
            frames = extract_adts_frames(bytes(self._buffer))
            consumed = sum(len(f) for f in frames)
            self._buffer = self._buffer[consumed:]

        return frames

    def stop(self) -> None:
        """Stop the ffmpeg encoder process."""
        if not self._proc:
            return
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        self._proc = None
        self._buffer.clear()
