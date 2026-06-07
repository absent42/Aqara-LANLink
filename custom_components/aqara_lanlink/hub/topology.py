"""LANLink topology types and parsers.

Defines the bridge type that connects the cloud's per-device records to
the integration's device-construction layer, plus parsers for both
enumeration sources (cloud and LANLink push frame).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TopologyDevice:
    """One device known to a hub.

    Sourced primarily from the Aqara cloud's `position/device/query`
    response. Cross-validated against the LANLink topology push frame.
    """

    did: str
    model: str
    name: str
    parent_did: str          # hub DID for sub-devices, "" for hubs themselves
    state: int               # 1 online, 0 offline (or "unknown" -> 0)
    firmware_version: str
    cloud_metadata: dict     # the raw cloud record, kept for the auto-derive pipeline


def parse_cloud_device(record: dict) -> TopologyDevice:
    """Build a TopologyDevice from one entry of the cloud device list.

    Tolerates missing keys defensively (defaults to empty strings or zeros)
    so partial responses still produce something.
    """
    return TopologyDevice(
        did=record.get("did", ""),
        model=record.get("model", ""),
        name=record.get("deviceName") or record.get("originalName", ""),
        parent_did=record.get("parentDeviceId", ""),
        state=int(record.get("state", 0) or 0),
        firmware_version=record.get("firmwareVersion", ""),
        cloud_metadata=dict(record),
    )


def classify_tunnel_host(
    topology: frozenset[str] | None,
    host_did: str,
) -> str:
    """Classify a tunnel host as 'hub' or 'standalone' from its topology DID set.

    Parameters
    ----------
    topology:
        The frozenset[str] returned by ``parse_lanlink_topology_push``, or
        ``None`` / empty frozenset when no topology push has been received yet.
    host_did:
        The tunnel host's own DID (from config entry data CONF_HUB_DID).

    Returns
    -------
    'hub'
        The topology contains at least one DID that is not ``host_did``,
        meaning the tunnel host has child sub-devices.
    'standalone'
        The topology contains only ``host_did``, is empty, or is None.
        Conservatively the default when topology has not yet arrived.
    """
    if not topology:
        return "standalone"
    other_dids = topology - {host_did}
    return "hub" if other_dids else "standalone"


def parse_lanlink_topology_push(data: dict | None) -> frozenset[str]:
    """Parse the data field of a `cmd=topology, type=device` push frame.

    Wire shape captured from a live M3:
        {"<hub_did>": ["<hub_did>", "<sub_did_1>", "<sub_did_2>", ...]}

    Returns the flat set of DIDs (hub + sub-devices). Returns empty frozenset
    if the input is malformed or missing.
    """
    if not isinstance(data, dict) or not data:
        return frozenset()
    # The hub DID is the (single) key; its value is the list of all DIDs.
    # Take whatever single-key shape we observe; tolerate empty lists.
    dids: set[str] = set()
    for key, value in data.items():
        if isinstance(value, list):
            dids.update(d for d in value if isinstance(d, str) and d)
        if isinstance(key, str) and key:
            dids.add(key)
    return frozenset(dids)
