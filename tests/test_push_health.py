"""Report silence is not a failure contract for event-driven devices."""
import pytest

from custom_components.aqara_lanlink.push_health import forwarding_health


@pytest.mark.parametrize("age", [300.0, 2280.0, 86400.0])
def test_quiet_connected_topology_has_unknown_forwarding(age):
    assert forwarding_health(
        connected=True, topology_size=2, report_age=age, freshness_window=300,
    ) is None


def test_no_report_received_is_not_positive_evidence():
    assert forwarding_health(
        connected=True, topology_size=2, report_age=None, freshness_window=300,
    ) is None


def test_real_report_after_long_silence_provides_fresh_evidence():
    assert forwarding_health(
        connected=True, topology_size=2, report_age=0, freshness_window=300,
    ) is True


def test_tunnel_disconnection_is_still_a_failure():
    assert forwarding_health(
        connected=False, topology_size=2, report_age=0, freshness_window=300,
    ) is False


def test_empty_topology_does_not_prove_forwarding():
    assert forwarding_health(
        connected=True, topology_size=0, report_age=0, freshness_window=300,
    ) is None
