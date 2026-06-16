from custom_components.aqara_lanlink.device.settings import SettingSpec


def test_setting_spec_defaults():
    s = SettingSpec(rid="4.4.85", name="Child lock socket 1", platform="switch")
    assert s.rid == "4.4.85"
    assert s.name == "Child lock socket 1"
    assert s.platform == "switch"
    assert s.enum_values is None
    assert s.min is None
    assert s.max is None
    assert s.unit is None
    assert s.press_value is None
    assert s.entity_category == "config"
    assert s.default_enabled is True
    assert s.optimistic is True
    assert s.on_value == "1"
    assert s.off_value == "0"


def test_setting_spec_inverted_switch_values():
    s = SettingSpec(
        rid="8.0.2032",
        name="Indicator light",
        platform="switch",
        on_value="0",
        off_value="1",
    )
    assert s.on_value == "0"
    assert s.off_value == "1"


def test_setting_spec_full():
    s = SettingSpec(
        rid="4.4.85",
        name="Button mode",
        platform="select",
        enum_values={"1": "Single-Press", "2": "Multi-Function"},
        min=100,
        max=3250,
        unit="W",
        press_value="1",
        entity_category="config",
        default_enabled=False,
        optimistic=True,
    )
    assert s.enum_values == {"1": "Single-Press", "2": "Multi-Function"}
    assert s.min == 100
    assert s.max == 3250
    assert s.unit == "W"
    assert s.press_value == "1"
    assert s.entity_category == "config"
    assert s.default_enabled is False
    assert s.optimistic is True
