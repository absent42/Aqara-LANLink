"""Tests for the GF(2^163) ECC implementation.

Test vectors were captured at runtime via Frida hooks on the native library,
so any divergence from the real library's math will fail these tests.
"""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.hub.ecc import (
    CURVE_A,
    CURVE_B,
    CURVE_ORDER,
    FIELD_BITS,
    FIELD_POLY,
    GEN_X,
    GEN_Y,
    GENERATOR,
    Point,
    bytes_to_field,
    ecdh_pubkey_from_privkey,
    ecdh_shared_secret,
    field_to_bytes,
    gf2_add,
    gf2_inv,
    gf2_mod,
    gf2_mul,
    gf2_mulmod,
    gf2_sqr,
    point_add,
    point_double,
    point_on_curve,
    pubkey_to_bytes,
    scalar_mult,
)


def _gf2_sqrt(a: int) -> int:
    """Square root in GF(2^m): a^(2^(m-1)), i.e. apply Frobenius m-1 times."""
    for _ in range(FIELD_BITS - 1):
        a = gf2_sqr(a)
    return a


class TestECDHPeerValidation:
    def test_two_torsion_point_is_on_curve_and_order_two(self):
        # (0, sqrt(b)) satisfies y^2 = b at x=0, and point_double(x=0) -> O.
        t = Point(0, _gf2_sqrt(CURVE_B))
        assert point_on_curve(t)
        assert point_double(t).is_infinity  # [2]T = O -> order 2

    def test_ecdh_rejects_low_order_peer_point(self):
        # A 2-torsion point is ON the curve but NOT in the prime-order subgroup.
        # Using it would let a peer confine the shared secret to a tiny set
        # (small-subgroup attack), leaking bits of our private key. It must be
        # rejected before the scalar multiply.
        t = Point(0, _gf2_sqrt(CURVE_B))
        with pytest.raises(ValueError):
            ecdh_shared_secret(field_to_bytes(0x1234567), pubkey_to_bytes(t.x, t.y))

    def test_ecdh_accepts_valid_subgroup_peer(self):
        # Regression: a legitimate pubkey (k*G, in <G>) is still accepted and
        # both sides derive the same 24-byte secret.
        priv_a = field_to_bytes(0xA11CE)
        priv_b = field_to_bytes(0xB0B)
        pub_a = ecdh_pubkey_from_privkey(priv_a)
        pub_b = ecdh_pubkey_from_privkey(priv_b)
        s_a = ecdh_shared_secret(priv_a, pub_b)
        s_b = ecdh_shared_secret(priv_b, pub_a)
        assert s_a == s_b
        assert len(s_a) == 24


# =============================================================================
# Field basics
# =============================================================================

class TestFieldArithmetic:
    def test_reduction_polynomial_bits(self):
        # x^163 + x^7 + x^6 + x^3 + 1 => bits 163, 7, 6, 3, 0 set.
        expected = (1 << 163) | (1 << 7) | (1 << 6) | (1 << 3) | 1
        assert FIELD_POLY == expected
        assert FIELD_POLY.bit_length() == 164

    def test_add_is_xor(self):
        assert gf2_add(0x1234, 0x5678) == 0x1234 ^ 0x5678

    def test_mul_trivial(self):
        assert gf2_mul(0, 0xdeadbeef) == 0
        assert gf2_mul(1, 0xdeadbeef) == 0xdeadbeef

    def test_mul_distributive(self):
        # Over GF(2): a * (b + c) = a*b + a*c
        a, b, c = 0xabc, 0x123, 0x456
        assert gf2_mul(a, gf2_add(b, c)) == gf2_add(gf2_mul(a, b), gf2_mul(a, c))

    def test_mod_reduces(self):
        # Any polynomial of degree >= 163 must be reduced below 2^163.
        x = 1 << 200
        r = gf2_mod(x)
        assert r.bit_length() <= FIELD_BITS

    def test_sqr_equivalent_to_mul(self):
        # a^2 == a * a in the field.
        for a in (1, 2, 0x1234, 0xabcdef, GEN_X):
            assert gf2_sqr(a) == gf2_mulmod(a, a)

    def test_inv_roundtrip(self):
        # a * a^-1 == 1 for several random-ish values.
        for a in (1, 2, 0x1234, 0xabcdef, GEN_X, GEN_Y, CURVE_B):
            ainv = gf2_inv(a)
            assert gf2_mulmod(a, ainv) == 1

    def test_inv_of_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            gf2_inv(0)


# =============================================================================
# Curve equation holds for captured generator and public keys
# =============================================================================

class TestCurveEquation:
    def test_curve_a_resolved(self):
        # a must be 0 or 1 for a sensible binary curve.
        assert CURVE_A in (0, 1)

    def test_generator_on_curve(self):
        assert point_on_curve(GENERATOR)

    @pytest.mark.parametrize("x,y", [
        # Captured ecdh_generate_keys outputs (little-endian LE from Frida).
        # Input keys were random; the outputs must lie on the curve.
        (
            int.from_bytes(bytes.fromhex(
                "b55f2162eaea9be7917f6d71586feb4a37e40dc100000000"
            ), "little"),
            int.from_bytes(bytes.fromhex(
                "8cd3e106511118567088d7db46fc45ff423343d500000000"
            ), "little"),
        ),
        (
            int.from_bytes(bytes.fromhex(
                "4944a25baae9ca8b6fb6717c6ed19046c708ff8a01000000"
            ), "little"),
            int.from_bytes(bytes.fromhex(
                "bf94b88b489d038cafe7b31b328964425ece344407000000"
            ), "little"),
        ),
        (
            int.from_bytes(bytes.fromhex(
                "3593749c8e363f6eaa66805a53108849ff6b7dc207000000"
            ), "little"),
            int.from_bytes(bytes.fromhex(
                "59e6d9d115af9b536eae2a62e9b3cfe975a77aab07000000"
            ), "little"),
        ),
    ])
    def test_captured_pubkeys_on_curve(self, x, y):
        assert point_on_curve(Point(x, y))


# =============================================================================
# Scalar multiplication
# =============================================================================

class TestScalarMult:
    def test_zero_times_g_is_infinity(self):
        assert scalar_mult(0, GENERATOR).is_infinity

    def test_one_times_g_is_g(self):
        p = scalar_mult(1, GENERATOR)
        assert (p.x, p.y) == (GEN_X, GEN_Y)

    def test_order_times_g_is_infinity(self):
        # n*G = O (defines the curve order).
        assert scalar_mult(CURVE_ORDER, GENERATOR).is_infinity

    def test_two_g_matches_double(self):
        assert scalar_mult(2, GENERATOR) == point_double(GENERATOR)

    def test_three_g_matches_add(self):
        double = point_double(GENERATOR)
        expected = point_add(double, GENERATOR)
        assert scalar_mult(3, GENERATOR) == expected


# =============================================================================
# ECDH against captured Frida test vectors
# =============================================================================

# Each entry: private key (24 bytes LE) -> public key (48 bytes LE x || y).
# Captured via Frida hook on `ecdh_generate_keys`.

ECDH_VECTORS = [
    (
        "3767e04166aa2cba477c2fa253ff696fe9e1177f03000000",
        "b55f2162eaea9be7917f6d71586feb4a37e40dc1000000008cd3e106511118567088d7db46fc45ff423343d500000000",
    ),
    (
        "e03aed74e81bdd080028bf6fa85015313d82141d02000000",
        "4944a25baae9ca8b6fb6717c6ed19046c708ff8a01000000bf94b88b489d038cafe7b31b328964425ece344407000000",
    ),
    (
        "ab87067596727644449cffffc725ad45a3a5918600000000",
        "3593749c8e363f6eaa66805a53108849ff6b7dc20700000059e6d9d115af9b536eae2a62e9b3cfe975a77aab07000000",
    ),
    (
        "ef9c133342e89f1e45b30d3311c017163023500e02000000",
        "33861bc05069a9e8fdc81887969a3d1e2d52c21b060000008bd64ae3c204a90119748cc167b9f77a7ec0b2e704000000",
    ),
    (
        "d8949c800ac2020cc65a704fef4d42d11fbd6eca02000000",
        "844049573cf8d0becf1d72d874c2eb3202ed67a70200000034efe0cdbf04d3045ff3716a5e6dcabc84968ed803000000",
    ),
]


@pytest.mark.parametrize("priv_hex,pub_hex", ECDH_VECTORS)
def test_ecdh_generate_keys_matches_capture(priv_hex, pub_hex):
    priv = bytes.fromhex(priv_hex)
    expected_pub = bytes.fromhex(pub_hex)
    got_pub = ecdh_pubkey_from_privkey(priv)
    assert got_pub == expected_pub, (
        f"\n  priv:     {priv.hex()}"
        f"\n  expected: {expected_pub.hex()}"
        f"\n  got:      {got_pub.hex()}"
    )


class TestEcdhPairwise:
    def test_pairwise_shared_secret_match(self):
        """Alice and Bob should derive the same shared secret independently."""
        alice_priv = bytes.fromhex(ECDH_VECTORS[0][0])
        bob_priv = bytes.fromhex(ECDH_VECTORS[1][0])
        alice_pub = ecdh_pubkey_from_privkey(alice_priv)
        bob_pub = ecdh_pubkey_from_privkey(bob_priv)
        s_alice = ecdh_shared_secret(alice_priv, bob_pub)
        s_bob = ecdh_shared_secret(bob_priv, alice_pub)
        assert s_alice == s_bob
