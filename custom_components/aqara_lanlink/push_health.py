"""Evidence-based health for an event-driven LANLink report stream."""
from __future__ import annotations


def forwarding_health(
    *, connected: bool, topology_size: int,
    report_age: float | None, freshness_window: float,
) -> bool | None:
    """Return recent push evidence, a disconnected tunnel, or unknown.

    No supported protocol contract guarantees a periodic report. A quiet
    doorbell can therefore provide no current evidence without being broken.
    Keepalive replies prove the tunnel, not the device-report forwarding path.
    """
    if not connected:
        return False
    if topology_size <= 0 or report_age is None:
        return None
    if report_age < freshness_window:
        return True
    return None
