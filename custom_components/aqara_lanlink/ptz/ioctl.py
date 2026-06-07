"""lumi IOCTL codec. Innermost layer; excludes the 8-byte pppp session header."""
import json

TAG = b"lumi"

def build(iotype: int, body: dict, id: int) -> bytes:
    body_json = json.dumps(body, separators=(",", ":")).encode()
    return (TAG + iotype.to_bytes(4, "little") + id.to_bytes(4, "little")
            + len(body_json).to_bytes(4, "little") + body_json)

def parse(buf: bytes):
    # Explicit checks (not assert, which -O strips) so every malformed/attacker
    # buffer raises ValueError -- the exception the PTZ receive loop catches.
    if len(buf) < 16 or buf[:4] != TAG:
        raise ValueError("not a lumi IOCTL")
    iotype = int.from_bytes(buf[4:8], "little")
    _id = int.from_bytes(buf[8:12], "little")
    blen = int.from_bytes(buf[12:16], "little")
    if len(buf) < 16 + blen:
        raise ValueError(
            f"truncated IOCTL body: declared {blen}, got {len(buf) - 16}"
        )
    raw = buf[16:16 + blen]
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid IOCTL body: {exc}") from exc
    return iotype, _id, body
