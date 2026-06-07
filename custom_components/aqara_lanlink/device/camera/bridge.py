"""go2rtc exec bridge: stdin audio -> Aqara doorbell speaker.

This script is spawned by go2rtc as an exec source with backchannel enabled.
go2rtc pipes PCM audio (s16le, 16kHz, mono) from the browser's microphone
to this script's stdin. The script encodes it to AAC-LC ADTS, manages the
Aqara TCP control session, and sends frames to the camera via UDP RTP.

go2rtc invokes this as a script, not a module: exec.Command does not support
shell syntax, so PYTHONPATH cannot be set. Running a file as a script makes
Python prepend the file's own directory (the integration's device/camera/
package directory) to sys.path. Two consequences are handled at the top of
this file, before any other import:

  - A sibling module whose name matches a stdlib module (e.g. the stdlib
    `numbers` module) would shadow it on a bare `import`, crashing unrelated
    imports. The script directory is removed from sys.path defensively.
  - The bridge must NOT import the `custom_components.aqara_lanlink` package:
    that runs the integration's __init__.py chain (Home Assistant, the hub
    coordinator, the cloud client) which the bridge does not need and which
    is slow and fragile to import outside the HA runtime. Instead the three
    pure-Python modules it depends on (const, protocol, encoder) are loaded
    directly from disk into a private, self-contained package.

go2rtc stream config example (written automatically by the integration):
  streams:
    aqara_lanlink_10_1_20_150:
      - rtsp://user:pass@CAMERA_IP:8554/ch1
      - "ffmpeg:aqara_lanlink_10_1_20_150#video=copy#audio=opus#audio=copy"
      - "exec:/usr/local/bin/python3 /config/custom_components/aqara_lanlink/device/camera/bridge.py CAMERA_IP#backchannel=1"
"""

from __future__ import annotations

import os
import sys

# --- sys.path hygiene -------------------------------------------------------
# Running a file as a script puts its own directory on sys.path. For this file
# that is the integration's device/camera/ package directory, whose modules
# would shadow same-named stdlib modules on a bare `import`. Remove it before
# importing anything else. The dependency island below loads what the bridge
# needs from disk explicitly, so the directory is not needed on sys.path.
_SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path[:] = [
    p for p in sys.path if os.path.realpath(p or os.getcwd()) != _SCRIPT_DIR
]

import importlib.util  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402

_LOGGER = logging.getLogger(__name__)


# --- dependency island ------------------------------------------------------
# Load const/protocol/encoder straight from disk as a self-contained package,
# without importing `custom_components.aqara_lanlink` (which would pull in Home
# Assistant). The modules are pure Python and import only the stdlib and each
# other; loading them by file path keeps this subprocess tiny and robust.
_DEPS_PACKAGE = "_aqara_bridge_deps"


def _load_camera_modules() -> types.SimpleNamespace:
    """Load const, protocol and encoder as an isolated package, no HA imports.

    The modules use relative imports (`from .const import ...`), so they are
    registered under a private package name in dependency order; each is in
    sys.modules before the next one that imports it is executed.
    """
    package = types.ModuleType(_DEPS_PACKAGE)
    package.__path__ = [_SCRIPT_DIR]
    package.__package__ = _DEPS_PACKAGE
    sys.modules[_DEPS_PACKAGE] = package

    loaded: dict[str, types.ModuleType] = {}
    for name in ("const", "protocol", "encoder"):  # dependency order matters
        qualified = f"{_DEPS_PACKAGE}.{name}"
        spec = importlib.util.spec_from_file_location(
            qualified, os.path.join(_SCRIPT_DIR, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)
        loaded[name] = module
    return types.SimpleNamespace(**loaded)


_deps = _load_camera_modules()

AUDIO_PORT = _deps.const.AUDIO_PORT
CONTROL_PORT = _deps.const.CONTROL_PORT
HEARTBEAT_INTERVAL = _deps.const.HEARTBEAT_INTERVAL
RTP_PAYLOAD_TYPE = _deps.const.RTP_PAYLOAD_TYPE
TCP_CONNECT_TIMEOUT = _deps.const.TCP_CONNECT_TIMEOUT
TYPE_ACK = _deps.const.TYPE_ACK
TYPE_HEARTBEAT = _deps.const.TYPE_HEARTBEAT
TYPE_START_VOICE = _deps.const.TYPE_START_VOICE
TYPE_STOP_VOICE = _deps.const.TYPE_STOP_VOICE

build_packet = _deps.protocol.build_packet
build_rtp_header = _deps.protocol.build_rtp_header
parse_packet = _deps.protocol.parse_packet

AACEncoder = _deps.encoder.AACEncoder

# PCM read chunk: go2rtc v1.9.14 sends PCML (s16le) at 16kHz (2 bytes/sample).
# 2048 bytes = 1024 samples = one AAC frame worth (~64ms).
STDIN_CHUNK_SIZE = 2048

# AAC frame duration in seconds (1024 samples at 16kHz = 64ms)
FRAME_DURATION = 1024 / 16000  # 0.064s


class BridgeSession:
    """Synchronous Aqara talk session for the go2rtc exec bridge."""

    def __init__(self, camera_ip: str) -> None:
        self._camera_ip = camera_ip
        self._session_ts = int(time.time() * 1000)
        self._ssrc = random.randint(1, 2147483647)
        self._seq_num = 0
        self._tcp_sock: socket.socket | None = None
        self._udp_sock: socket.socket | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._running = False
        self._stop_event = threading.Event()

    def connect(self) -> bool:
        """Open TCP control session and UDP audio socket."""
        try:
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._tcp_sock.settimeout(TCP_CONNECT_TIMEOUT)
            self._tcp_sock.connect((self._camera_ip, CONTROL_PORT))

            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            pkt = build_packet(TYPE_START_VOICE, self._session_ts)
            self._tcp_sock.sendall(pkt)

            resp = self._tcp_sock.recv(1024)
            parsed = parse_packet(resp) if resp else None
            if not parsed or parsed["type"] != TYPE_ACK or parsed["value"] != 0:
                _LOGGER.error("Voice session rejected: %s", parsed)
                self.close()
                return False

            self._running = True
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True
            )
            self._heartbeat_thread.start()
            return True

        except (OSError, TimeoutError) as err:
            _LOGGER.error("Connect failed: %s", err)
            self.close()
            return False

    def send_audio_frame(self, aac_frame: bytes) -> None:
        """Send an AAC-ADTS frame via RTP UDP."""
        if not self._udp_sock:
            return
        ts = self._seq_num * 1024  # 1024 samples per AAC frame at 16kHz
        header = build_rtp_header(RTP_PAYLOAD_TYPE, ts, self._ssrc, self._seq_num)
        self._seq_num += 1
        self._udp_sock.sendto(header + aac_frame, (self._camera_ip, AUDIO_PORT))

    def close(self) -> None:
        """Send STOP_VOICE and close sockets."""
        self._running = False
        self._stop_event.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2)
            self._heartbeat_thread = None
        if self._tcp_sock:
            try:
                self._tcp_sock.sendall(build_packet(TYPE_STOP_VOICE, self._session_ts))
            except OSError:
                pass
            try:
                self._tcp_sock.close()
            except OSError:
                pass
            self._tcp_sock = None
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except OSError:
                pass
            self._udp_sock = None

    def _heartbeat_loop(self) -> None:
        """Send heartbeats every 5s."""
        failures = 0
        while self._running:
            if self._stop_event.wait(timeout=HEARTBEAT_INTERVAL):
                break  # stop_event was set
            if not self._running or not self._tcp_sock:
                break
            try:
                self._tcp_sock.sendall(build_packet(TYPE_HEARTBEAT, self._session_ts))
                resp = self._tcp_sock.recv(1024)
                parsed = parse_packet(resp) if resp else None
                if parsed and parsed["type"] == TYPE_ACK and parsed["value"] == 0:
                    failures = 0
                    continue
                failures += 1
            except (OSError, TimeoutError):
                failures += 1
            if failures > 5:
                _LOGGER.error("Heartbeat failed %d times, closing", failures)
                self._running = False
                break


def main() -> None:
    """Entry point for go2rtc exec bridge."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if len(sys.argv) < 2:
        print("Usage: python3 bridge.py CAMERA_IP", file=sys.stderr)
        sys.exit(1)

    camera_ip = sys.argv[1]
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    _LOGGER.info("Bridge starting for %s", camera_ip)

    session = BridgeSession(camera_ip)
    if not session.connect():
        sys.exit(1)

    # go2rtc sends G.711 A-law (PCMA) at 8kHz to exec backchannel stdin.
    # Despite version reporting as 1.9.14, HA's bundled go2rtc uses stdin.NewClient
    # which hardcodes PCMA/8000 and ignores the #audio= parameter.
    encoder = AACEncoder(input_format="alaw")
    encoder.start()
    _LOGGER.info("Encoding pipeline active, reading PCMA/8kHz from stdin")

    total_frames = 0
    start_time = 0.0  # set on first audio data, not on bridge start

    def _send_paced(frame: bytes) -> None:
        """Send a frame, sleeping if ahead of real-time schedule."""
        nonlocal total_frames, start_time
        if start_time == 0.0:
            start_time = time.monotonic()
        expected_time = start_time + total_frames * FRAME_DURATION
        now = time.monotonic()
        if now < expected_time:
            time.sleep(expected_time - now)
        session.send_audio_frame(frame)
        total_frames += 1

    first_stdin = True
    first_frame = True
    try:
        while True:
            pcm_data = sys.stdin.buffer.read(STDIN_CHUNK_SIZE)
            if not pcm_data:
                _LOGGER.info("stdin EOF, flushing encoder")
                break
            if first_stdin:
                _LOGGER.info("First stdin data received (%d bytes)", len(pcm_data))
                first_stdin = False
            frames = encoder.write_pcm(pcm_data)
            for frame in frames:
                if first_frame:
                    _LOGGER.info("First AAC frame produced (encoder latency: %.0fms)",
                                 (time.monotonic() - start_time) * 1000 if start_time else 0)
                    first_frame = False
                _send_paced(frame)

        # Flush remaining frames from encoder buffer
        remaining = encoder.flush()
        for frame in remaining:
            _send_paced(frame)
        _LOGGER.info("Sent %d audio frames total", total_frames)

    except KeyboardInterrupt:
        _LOGGER.info("Interrupted, stopping")
    finally:
        encoder.stop()
        session.close()
        _LOGGER.info("Bridge stopped")


if __name__ == "__main__":
    main()
