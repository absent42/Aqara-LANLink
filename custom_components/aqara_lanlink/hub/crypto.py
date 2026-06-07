"""LANLink cryptographic primitives.

Ports the device-key derivation algorithm and wire-frame cipher operations
from the decompiled liblanlink.so. All operations are deterministic given the
same inputs; captured traffic is used as test vectors.

Wire frame format (validated against Frida socket captures, 2026-04-17):
    [0xFF] [0xFE] [2B type BE] [4B ct_len BE] [N bytes AES-CBC ciphertext] [2B CRC BE]

The trailing CRC is CRC-16/CCITT (poly 0x1021, init 0xFFFF, final XOR 0xFFFF)
computed over the ciphertext bytes only -- NOT the header.

Message types:
    0x2020 - ECDH key exchange (encrypted with device key)
    0x201b - session key verification (encrypted with device key)
    0x2021 - session data (JSON, encrypted with session key)

AES parameters (confirmed from native hooks):
    - AES-128-CBC with PKCS7 padding
    - IV is always equal to the key (IV == key)
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

MSG_TYPE_ECDH = 0x2020
MSG_TYPE_VERIFY = 0x201B
MSG_TYPE_SESSION = 0x2021

FRAME_MAGIC = b"\xff\xfe"
FRAME_HEADER_LEN = 8  # magic(2) + type(2) + length(4)
FRAME_TRAILER_LEN = 2  # CRC-16 BE over ciphertext
# Upper bound on a frame's ciphertext length. The wire length field is 4 bytes
# (up to 4 GiB); a real hub never sends anything near this. Legitimate frames
# (handshake, topology/report JSON) are at most a few tens of KiB, so 256 KiB
# is generous headroom. Rejecting larger declared lengths stops a malicious or
# buggy peer from making the read loop buffer unboundedly (memory-exhaustion
# DoS).
MAX_FRAME_CT_LEN = 256 * 1024


# =============================================================================
# CRC-16/CCITT used inside the device-key derivation
# =============================================================================

def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT (polynomial 0x1021, init 0xFFFF, final XOR 0xFFFF).

    This is the same CRC the library uses internally for frame checksums and
    for the device-key derivation fold.
    """
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


# =============================================================================
# Device key derivation (FUN_000136ec in Ghidra)
# =============================================================================

def derive_device_key(device_id: str) -> bytes:
    """Derive the 16-byte AES device key from a device ID string.

    Deterministic multi-pass derivation. Both client and server compute this
    independently from the same device ID, so no exchange is needed.

    See tests/hub/test_crypto.py for canonical input/output
    vectors validated against captured wire traffic.
    """
    original = device_id.encode("ascii")
    length = len(original)
    buf = bytearray(original)

    # Pass 1: XOR adjacent chars with position-reversed tail.
    # Note: reads from `original` (pre-pass snapshot), writes into `buf`.
    for i in range(length - 1):
        buf[i] = (original[i] ^ original[i + 1]) + buf[length - 1 - i] & 0xFF

    # Pass 2: fold first half XOR'd with mid-shifted values into the tail.
    half = length >> 1
    for i in range(half):
        buf[length - 1 - i] = buf[i] ^ buf[i + half - 1]

    # Pass 3: fold the buffer (read back-to-front) into 6 bytes via XOR.
    key6 = bytearray(6)
    for i in range(length):
        key6[i % 6] ^= buf[length - 1 - i]

    # Pass 4: additional mixing of the 6 bytes.
    for i in range(6):
        idx = i % 6
        key6[idx] = (key6[idx] + (key6[5 - idx] ^ key6[(i + 3) % 6])) & 0xFF

    # Append a CRC-16 of the 6 bytes to make an 8-byte seed.
    crc = crc16_ccitt(bytes(key6))
    seed = bytearray(16)
    seed[0:6] = key6
    seed[6] = crc & 0xFF
    seed[7] = (crc >> 8) & 0xFF

    # Pass 5: write bytes[8..15] = bytes[0..7] XOR bytes[7], indexed in reverse.
    # (0x1c23b - i = byte at position 15 - i)
    anchor = seed[7]
    for i in range(8):
        seed[15 - i] = seed[i] ^ anchor

    # Pass 6: chain-XOR all 16 bytes IN PLACE. The wrap-around at i=15 reads
    # seed[0] AFTER it was modified at i=0 -- that's why the last byte differs
    # from a naive read-then-write implementation.
    for i in range(16):
        nxt = 0 if i == 15 else i + 1
        seed[i] = (seed[i] ^ seed[nxt]) & 0xFF

    return bytes(seed)


# =============================================================================
# AES-128-CBC with IV == key (as the native library calls it)
# =============================================================================

def aes_cbc_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-128-CBC encrypt with PKCS7 padding. IV is the key itself."""
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_cbc_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CBC decrypt with PKCS7 unpadding. IV is the key itself."""
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


# =============================================================================
# Wire frame codec
# =============================================================================

@dataclass
class Frame:
    """Decoded wire frame."""

    type: int
    ciphertext: bytes

    def encode(self) -> bytes:
        """Serialize to the on-wire `FF FE ... CRC` format."""
        ct = self.ciphertext
        crc = crc16_ccitt(ct).to_bytes(2, "big")
        return (
            FRAME_MAGIC
            + self.type.to_bytes(2, "big")
            + len(ct).to_bytes(4, "big")
            + ct
            + crc
        )


def parse_frame(data: bytes) -> tuple[Frame, bytes] | None:
    """Parse one frame from the head of `data`.

    Returns (frame, remaining_bytes) on success, or None if not enough bytes
    are available yet. Raises ValueError on malformed frames or bad CRC.
    """
    if len(data) < FRAME_HEADER_LEN:
        return None
    if data[0:2] != FRAME_MAGIC:
        raise ValueError(f"Bad magic: expected FF FE, got {data[0:2].hex()}")
    msg_type = int.from_bytes(data[2:4], "big")
    ct_len = int.from_bytes(data[4:8], "big")
    if ct_len > MAX_FRAME_CT_LEN:
        # Reject before deciding to wait for more bytes, so the caller's read
        # loop cannot be driven to buffer a bogus multi-gigabyte length.
        raise ValueError(
            f"Frame ciphertext length {ct_len} too large "
            f"(max {MAX_FRAME_CT_LEN})"
        )
    total = FRAME_HEADER_LEN + ct_len + FRAME_TRAILER_LEN
    if len(data) < total:
        return None  # need more bytes
    ct = bytes(data[FRAME_HEADER_LEN : FRAME_HEADER_LEN + ct_len])
    trailer = bytes(data[FRAME_HEADER_LEN + ct_len : total])
    expected = crc16_ccitt(ct).to_bytes(2, "big")
    if trailer != expected:
        raise ValueError(
            f"Bad CRC: expected {expected.hex()}, got {trailer.hex()}"
        )
    return Frame(type=msg_type, ciphertext=ct), bytes(data[total:])
