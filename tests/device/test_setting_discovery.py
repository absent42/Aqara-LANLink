"""Tests for the pure wire-path -> rid bridge from a cloud device scan."""

from custom_components.aqara_lanlink.device.setting_discovery import (
    extract_resource_id_map,
)


# ----------------------------------------------------------------------
# extract_resource_id_map
# ----------------------------------------------------------------------


def test_extract_maps_only_propertyid_bearing_traits():
    """Only traits with a non-empty propertyId yield a wire-path -> rid pair."""
    response = {
        "result": [
            {
                "deviceId": "dev1",
                "traits": [
                    {"path": "2.163.20237", "propertyId": ["14.35.85"]},
                    {"path": "4.21.85"},  # bare, no propertyId
                    {"path": "8.0.2032", "propertyId": ["1.2.3"]},
                ],
            }
        ]
    }
    assert extract_resource_id_map(response) == {
        "2.163.20237": "14.35.85",
        "8.0.2032": "1.2.3",
    }


def test_extract_canonicalises_four_part_path():
    """A trailing .<idx> on a 4-part path is stripped to the 3-part form."""
    response = {
        "result": [
            {
                "traits": [
                    {"path": "2.163.20237.1", "propertyId": ["14.35.85"]},
                ],
            }
        ]
    }
    assert extract_resource_id_map(response) == {"2.163.20237": "14.35.85"}


def test_extract_merges_multiple_devices_in_result():
    response = {
        "result": [
            {"traits": [{"path": "1.1.1", "propertyId": ["9.9.9"]}]},
            {"traits": [{"path": "2.2.2", "propertyId": ["8.8.8"]}]},
        ]
    }
    assert extract_resource_id_map(response) == {
        "1.1.1": "9.9.9",
        "2.2.2": "8.8.8",
    }


def test_extract_uses_first_propertyid_when_list_has_many():
    response = {
        "result": [
            {"traits": [{"path": "1.1.1", "propertyId": ["a.a.a", "b.b.b"]}]}
        ]
    }
    assert extract_resource_id_map(response) == {"1.1.1": "a.a.a"}


def test_extract_is_defensive_about_missing_and_malformed_keys():
    response = {
        "result": [
            {"traits": [
                {"propertyId": ["x.x.x"]},  # no path
                {"path": "1.1.1", "propertyId": []},  # empty list
                {"path": "2.2.2", "propertyId": "not-a-list"},  # wrong type
                {"path": "3.3.3", "propertyId": None},  # None
                {"path": "4.4.4", "propertyId": ["ok.ok.ok"]},  # good
            ]},
            {},  # device with no traits key
        ]
    }
    assert extract_resource_id_map(response) == {"4.4.4": "ok.ok.ok"}


def test_extract_handles_empty_or_missing_result():
    assert extract_resource_id_map({}) == {}
    assert extract_resource_id_map({"result": []}) == {}
    assert extract_resource_id_map({"result": None}) == {}
