"""Tests for host (IP / hostname) validation helpers."""
from __future__ import annotations

import pytest

from custom_components.aqara_lanlink.host_validation import (
    is_safe_host,
    validate_host,
)


@pytest.mark.parametrize(
    "value",
    [
        "10.1.20.150",
        "192.168.0.1",
        "fe80::1",
        "::1",
        "camera-1.local",
        "hub.example.com",
        "a",
    ],
)
def test_accepts_valid_ip_and_hostname(value):
    assert is_safe_host(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "1.2.3.4 --foo=bar",  # the H1 argument-injection payload
        "evil;rm -rf",
        "`id`",
        "host name",
        "10.0.0.1\n10.0.0.2",  # newline injection
        "a b",
        "-leadinghyphen",
        "",
        "x" * 254,  # over the 253-char host limit
        "host/../etc",
        "http://10.0.0.1",  # scheme/colon/slash
    ],
)
def test_rejects_unsafe_values(value):
    assert is_safe_host(value) is False


def test_validate_host_returns_value_when_safe():
    assert validate_host("10.1.20.150") == "10.1.20.150"


def test_validate_host_raises_on_unsafe():
    with pytest.raises(ValueError):
        validate_host("1.2.3.4 --foo=bar")
