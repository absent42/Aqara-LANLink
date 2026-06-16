from custom_components.aqara_lanlink.device.attrs import AttrSpec
from custom_components.aqara_lanlink.device.descriptors import (
    NumberDescriptor,
    SelectDescriptor,
    SwitchDescriptor,
)


def test_switch_descriptor_optimistic():
    d = SwitchDescriptor(key="s", attr=AttrSpec(name="x"), optimistic=True)
    assert d.optimistic is True
    d2 = SwitchDescriptor(key="s", attr=AttrSpec(name="x"))
    assert d2.optimistic is False


def test_select_descriptor_optimistic():
    d = SelectDescriptor(
        key="s",
        attr=AttrSpec(name="x"),
        options_map=(("a", "1"),),
        optimistic=True,
    )
    assert d.optimistic is True
    d2 = SelectDescriptor(
        key="s",
        attr=AttrSpec(name="x"),
        options_map=(("a", "1"),),
    )
    assert d2.optimistic is False


def test_number_descriptor_optimistic():
    d = NumberDescriptor(
        key="s",
        attr=AttrSpec(name="x"),
        min_value=0,
        max_value=10,
        optimistic=True,
    )
    assert d.optimistic is True
    d2 = NumberDescriptor(
        key="s",
        attr=AttrSpec(name="x"),
        min_value=0,
        max_value=10,
    )
    assert d2.optimistic is False
