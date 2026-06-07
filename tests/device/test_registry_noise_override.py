"""Tests for registry's per-model ALLOW_NOISE_TRAITS index."""
from __future__ import annotations

import types

import pytest

from custom_components.aqara_lanlink.device import registry


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_for_tests()
    yield
    registry.reset_for_tests()


def _make_fake_package(
    pkg_name: str, models: tuple[str, ...],
    allow_noise: frozenset[int] | None = None,
) -> types.ModuleType:
    """Build a minimal in-memory module the indexer can consume."""
    mod = types.ModuleType(pkg_name)
    mod.__name__ = pkg_name
    mod.MODELS = models
    mod.DISPLAY_NAME = "Test"
    mod.MANUFACTURER = "Aqara"
    if allow_noise is not None:
        mod.ALLOW_NOISE_TRAITS = allow_noise
    return mod


def test_noise_override_for_model_returns_empty_when_absent():
    pkg = _make_fake_package("pkg_a", ("lumi.test.a",))
    registry._index_package(pkg, "pkg_a")
    assert registry.noise_override_for_model("lumi.test.a") == frozenset()


def test_noise_override_for_model_returns_declared_set():
    pkg = _make_fake_package(
        "pkg_b", ("lumi.test.b",), allow_noise=frozenset({20109, 33107}),
    )
    registry._index_package(pkg, "pkg_b")
    assert registry.noise_override_for_model("lumi.test.b") == frozenset({20109, 33107})


def test_noise_override_for_model_returns_empty_for_unknown_model():
    assert registry.noise_override_for_model("lumi.does_not_exist") == frozenset()


def test_noise_override_accepts_iterable_not_just_frozenset():
    """A contributor might write `ALLOW_NOISE_TRAITS = (20109,)` -- accept it."""
    pkg = _make_fake_package(
        "pkg_c", ("lumi.test.c",), allow_noise=(20109,),  # type: ignore[arg-type]
    )
    registry._index_package(pkg, "pkg_c")
    assert registry.noise_override_for_model("lumi.test.c") == frozenset({20109})
