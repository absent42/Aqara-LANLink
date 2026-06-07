"""Binary-field elliptic-curve cryptography for LANLink.

Implements a custom curve over GF(2^163) used by liblanlink.so. Parameters
were extracted at runtime via Frida (see docs/LANLINK_TUNNEL_DECOMPILED.md).

Curve:
    Field           GF(2^163) with reduction polynomial x^163 + x^7 + x^6 + x^3 + 1
    Equation        y^2 + xy = x^3 + a*x^2 + b       (binary-field Weierstrass)
    a               1 (Koblitz-style; algorithms handle a in {0, 1})
    b               0x020a601907b8c953ca1481eb10512f78744a3205fd
    Generator G     (0x03f0eba16286a2d57ea0991168d4994637e8343e36,
                     0x00d51fbc6c71a0094fa2cdd545b11c5c0c797324f1)
    Order n         0x040000000000000000000292fe77e70c12a4234c33

Storage format on the wire (matches the native library layout):
    Private key = 24 bytes, little-endian byte order, upper 3 bytes zero-padded.
    Public key  = 48 bytes: little-endian x (24 bytes) || little-endian y (24 bytes).

We do all math on Python ints and only byte-swap at the (de)serialization boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

# =============================================================================
# Curve constants (big-endian integer form)
# =============================================================================

# Reduction polynomial: x^163 + x^7 + x^6 + x^3 + 1
FIELD_POLY = (1 << 163) | (1 << 7) | (1 << 6) | (1 << 3) | 1

CURVE_B = 0x020A601907B8C953CA1481EB10512F78744A3205FD

GEN_X = 0x03F0EBA16286A2D57EA0991168D4994637E8343E36
GEN_Y = 0x00D51FBC6C71A0094FA2CDD545B11C5C0C797324F1

CURVE_ORDER = 0x040000000000000000000292FE77E70C12A4234C33

# Field element bit size (163 bits) and byte lengths used by the library.
FIELD_BITS = 163
KEY_BYTES = 24  # padded size for private and coordinate elements
PUBKEY_BYTES = 48  # x || y


# =============================================================================
# Serialization helpers
# =============================================================================

def field_to_bytes(value: int) -> bytes:
    """Serialize a field element as a 24-byte little-endian array.

    The library stores 21-byte values with 3 bytes of zero padding at the end,
    so little-endian gives the right memory layout.
    """
    return value.to_bytes(KEY_BYTES, "little")


def bytes_to_field(data: bytes) -> int:
    """Deserialize a 24-byte little-endian buffer to a field element."""
    if len(data) != KEY_BYTES:
        raise ValueError(f"Expected {KEY_BYTES} bytes, got {len(data)}")
    return int.from_bytes(data, "little")


def pubkey_to_bytes(x: int, y: int) -> bytes:
    """Serialize an EC point as 48 bytes: little-endian x || little-endian y."""
    return field_to_bytes(x) + field_to_bytes(y)


def bytes_to_pubkey(data: bytes) -> tuple[int, int]:
    """Deserialize a 48-byte public key into (x, y)."""
    if len(data) != PUBKEY_BYTES:
        raise ValueError(f"Expected {PUBKEY_BYTES} bytes, got {len(data)}")
    return bytes_to_field(data[:KEY_BYTES]), bytes_to_field(data[KEY_BYTES:])


# =============================================================================
# GF(2^163) arithmetic
# =============================================================================

def _bit_length(x: int) -> int:
    return x.bit_length()


def gf2_add(a: int, b: int) -> int:
    """Addition in GF(2^m) is XOR."""
    return a ^ b


def gf2_mul(a: int, b: int) -> int:
    """Polynomial multiplication in GF(2), result may exceed the field size."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a <<= 1
        b >>= 1
    return result


def gf2_mod(a: int, poly: int = FIELD_POLY) -> int:
    """Reduce a polynomial modulo the field's irreducible polynomial."""
    poly_len = _bit_length(poly)
    while _bit_length(a) >= poly_len:
        shift = _bit_length(a) - poly_len
        a ^= poly << shift
    return a


def gf2_mulmod(a: int, b: int, poly: int = FIELD_POLY) -> int:
    """Multiplication in GF(2^163)."""
    return gf2_mod(gf2_mul(a, b), poly)


def gf2_sqr(a: int, poly: int = FIELD_POLY) -> int:
    """Squaring in GF(2^163). Squaring in GF(2) is bit-interleaving with zeros."""
    result = 0
    i = 0
    while a:
        if a & 1:
            result |= 1 << (2 * i)
        a >>= 1
        i += 1
    return gf2_mod(result, poly)


def gf2_inv(a: int, poly: int = FIELD_POLY) -> int:
    """Modular inverse in GF(2^m) via extended Euclidean algorithm.

    Finds a^-1 such that a * a^-1 == 1 (mod poly).
    """
    if a == 0:
        raise ZeroDivisionError("Cannot invert 0 in GF(2^m)")
    # Extended Euclidean in GF(2)[x].
    u, v = a, poly
    g1, g2 = 1, 0
    while u != 1:
        j = _bit_length(u) - _bit_length(v)
        if j < 0:
            u, v = v, u
            g1, g2 = g2, g1
            j = -j
        u ^= v << j
        g1 ^= g2 << j
    return gf2_mod(g1, poly)


# =============================================================================
# EC point arithmetic on y^2 + xy = x^3 + a*x^2 + b over GF(2^m)
# =============================================================================

# Curve coefficient `a`. Unknown yet (0 or 1). We'll validate by checking that
# the library's captured generator satisfies the equation.

CURVE_A = 1  # Tentative, will be validated by _validate_curve_a() at import.


@dataclass(frozen=True)
class Point:
    """Affine EC point. x == y == None represents the point at infinity."""

    x: int | None
    y: int | None

    @property
    def is_infinity(self) -> bool:
        return self.x is None and self.y is None


INFINITY = Point(None, None)


def point_on_curve(p: Point, a: int = CURVE_A, b: int = CURVE_B) -> bool:
    """Check y^2 + xy == x^3 + a*x^2 + b in GF(2^163)."""
    if p.is_infinity:
        return True
    x, y = p.x, p.y
    lhs = gf2_add(gf2_sqr(y), gf2_mulmod(x, y))
    x_sq = gf2_sqr(x)
    x_cb = gf2_mulmod(x_sq, x)
    rhs = gf2_add(gf2_add(x_cb, gf2_mulmod(a, x_sq)), b)
    return lhs == rhs


def _validate_curve_a() -> int:
    """Determine the curve `a` coefficient by checking the captured generator.

    Only a=0 or a=1 are sensible for binary curves; the generator must satisfy
    the curve equation with the correct value.
    """
    gen = Point(GEN_X, GEN_Y)
    for candidate_a in (1, 0):
        if point_on_curve(gen, candidate_a, CURVE_B):
            return candidate_a
    raise RuntimeError(
        "Captured generator does not lie on the curve for either a=0 or a=1"
    )


# Re-resolve the CURVE_A at import by probing both values against the generator.
CURVE_A = _validate_curve_a()


def point_neg(p: Point) -> Point:
    """In characteristic-2 curves, -P = (x, x + y)."""
    if p.is_infinity:
        return p
    return Point(p.x, gf2_add(p.x, p.y))


def point_double(p: Point, a: int = CURVE_A) -> Point:
    """Double a point on y^2 + xy = x^3 + a*x^2 + b (char 2)."""
    if p.is_infinity or p.x == 0:
        return INFINITY
    x = p.x
    y = p.y
    # lambda = x + y/x
    lam = gf2_add(x, gf2_mulmod(y, gf2_inv(x)))
    x3 = gf2_add(gf2_add(gf2_sqr(lam), lam), a)
    y3 = gf2_add(gf2_add(gf2_sqr(x), gf2_mulmod(lam, x3)), x3)
    return Point(x3, y3)


def point_add(p: Point, q: Point, a: int = CURVE_A) -> Point:
    """Add two distinct points (or handle doubling / infinity)."""
    if p.is_infinity:
        return q
    if q.is_infinity:
        return p
    if p.x == q.x:
        if p.y == q.y:
            return point_double(p, a)
        # p + (-p) == infinity
        return INFINITY
    # lambda = (y1 + y2) / (x1 + x2)
    dx = gf2_add(p.x, q.x)
    dy = gf2_add(p.y, q.y)
    lam = gf2_mulmod(dy, gf2_inv(dx))
    x3 = gf2_add(gf2_add(gf2_add(gf2_sqr(lam), lam), dx), a)
    y3 = gf2_add(gf2_add(gf2_mulmod(lam, gf2_add(p.x, x3)), x3), p.y)
    return Point(x3, y3)


def scalar_mult(k: int, p: Point, a: int = CURVE_A) -> Point:
    """Compute k*P using left-to-right double-and-add.

    Note: not constant-time. The real library IS constant-time via Montgomery
    ladder, but we don't need that for protocol correctness.
    """
    if k == 0 or p.is_infinity:
        return INFINITY
    k = k % CURVE_ORDER
    if k == 0:
        return INFINITY

    # Double-and-add starting from the MSB.
    result = INFINITY
    # Process bits from highest to lowest.
    for i in range(k.bit_length() - 1, -1, -1):
        result = point_double(result, a)
        if (k >> i) & 1:
            result = point_add(result, p, a)
    return result


# =============================================================================
# Public API matching the native library
# =============================================================================

GENERATOR = Point(GEN_X, GEN_Y)


def ecdh_pubkey_from_privkey(priv_bytes: bytes) -> bytes:
    """Compute the public key for a given 24-byte little-endian private key.

    Equivalent to the native `ecdh_generate_keys(pub_out, priv_in)` call, which
    takes a pre-filled random private key and computes the matching public key.
    """
    priv = bytes_to_field(priv_bytes)
    pub_point = scalar_mult(priv, GENERATOR)
    if pub_point.is_infinity:
        raise ValueError("Private key produced point at infinity")
    return pubkey_to_bytes(pub_point.x, pub_point.y)


def _mul_no_reduce(k: int, p: Point) -> Point:
    """Compute k*P WITHOUT reducing the scalar mod CURVE_ORDER.

    ``scalar_mult`` reduces k mod CURVE_ORDER, which would make CURVE_ORDER*P
    trivially the point at infinity for any P. The prime-subgroup membership
    test below needs the true value of n*P, so it must not reduce.
    """
    result = INFINITY
    for i in range(k.bit_length() - 1, -1, -1):
        result = point_double(result)
        if (k >> i) & 1:
            result = point_add(result, p)
    return result


def _is_in_prime_subgroup(p: Point) -> bool:
    """True if n*P == O, where n = CURVE_ORDER is the prime order of G.

    Points on the curve but outside <G> (the curve has cofactor 2, so half its
    points are in the order-2 coset) fail this check.
    """
    return _mul_no_reduce(CURVE_ORDER, p).is_infinity


def ecdh_shared_secret(priv_bytes: bytes, peer_pub_bytes: bytes) -> bytes:
    """Compute the ECDH shared secret.

    Returns a 24-byte little-endian encoding of the x-coordinate of
    `priv * peer_pub`. That is the customary ECDH output; the native library
    stores the first 16 bytes of this as the session key.
    """
    priv = bytes_to_field(priv_bytes)
    peer_x, peer_y = bytes_to_pubkey(peer_pub_bytes)
    peer = Point(peer_x, peer_y)
    # Validate the peer point BEFORE the scalar multiply, so a malicious peer
    # cannot mount an invalid-curve / small-subgroup confinement attack that
    # would leak bits of our private key across sessions. A local check only;
    # it never touches the wire, so it cannot break interop with a real hub
    # (whose key is always a valid point in <G>).
    if peer.is_infinity:
        raise ValueError("Peer public key is the point at infinity")
    if not point_on_curve(peer):
        raise ValueError("Peer public key is not on the curve")
    if not _is_in_prime_subgroup(peer):
        raise ValueError("Peer public key is not in the prime-order subgroup")
    shared = scalar_mult(priv, peer)
    if shared.is_infinity:
        raise ValueError("Shared secret is point at infinity")
    return field_to_bytes(shared.x)
