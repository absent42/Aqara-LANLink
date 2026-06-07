"""Tests for the firmware attribute-name catalog."""

from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.device import attrs as attrs_catalog
from custom_components.aqara_lanlink.device.attrs import (
    AttrSpec,
    get,
    register_attr,
)


@pytest.fixture(autouse=True)
def _reset_attrs():
    attrs_catalog.reset_for_tests()
    yield
    attrs_catalog.reset_for_tests()


def test_attr_spec_is_frozen_and_hashable():
    spec = AttrSpec(name="test_brightness", id="1.7.85")
    with pytest.raises(Exception):
        spec.name = "other"  # type: ignore[misc]
    assert hash(spec) == hash(AttrSpec(name="test_brightness", id="1.7.85"))


def test_register_and_get():
    spec = AttrSpec(name="test_brightness", id="1.7.85")
    returned = register_attr(spec)
    assert returned is spec
    assert get("test_brightness") is spec
    assert get("missing") is None


def test_register_idempotent_identical_fields():
    spec = AttrSpec(name="test_brightness", id="1.7.85", unit="%")
    register_attr(spec)
    returned = register_attr(AttrSpec(name="test_brightness", id="1.7.85", unit="%"))
    assert returned == spec


def test_register_overwrites_when_fields_differ():
    first = AttrSpec(name="auto_1_7_85", id="1.7.85")
    register_attr(first)
    second = AttrSpec(name="auto_1_7_85", id="1.7.85", unit="%")
    returned = register_attr(second)
    assert returned is second
    assert get("auto_1_7_85") is second


def test_change_listener_fires_on_mutation_only():
    fired: list[int] = []
    attrs_catalog.set_change_listener(lambda: fired.append(1))
    spec = AttrSpec(name="test_brightness")
    register_attr(spec)
    assert len(fired) == 1
    register_attr(AttrSpec(name="test_brightness"))
    assert len(fired) == 1
    register_attr(AttrSpec(name="test_brightness", unit="%"))
    assert len(fired) == 2


def test_listener_failure_does_not_break_registration():
    attrs_catalog.set_change_listener(
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    spec = AttrSpec(name="test_brightness")
    returned = register_attr(spec)
    assert returned is spec


def test_all_attrs_returns_snapshot():
    register_attr(AttrSpec(name="test_brightness"))
    register_attr(AttrSpec(name="test_motion"))
    snap = attrs_catalog.all_attrs()
    assert len(snap) == 2
    snap.clear()
    assert len(attrs_catalog.all_attrs()) == 2
