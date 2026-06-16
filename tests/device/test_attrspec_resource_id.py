from custom_components.aqara_lanlink.device.attrs import AttrSpec


def test_attrspec_carries_resource_id():
    a = AttrSpec(name="4.4.85", resource_id="4.4.85")
    assert a.resource_id == "4.4.85"


def test_attrspec_resource_id_defaults_none():
    assert AttrSpec(name="x").resource_id is None
