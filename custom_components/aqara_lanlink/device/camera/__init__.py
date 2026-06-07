"""Shared camera infrastructure for Aqara LANLink camera and doorbell models.

Holds the go2rtc, two-way-audio (talk), RTSP, bridge, encoder, and protocol
helpers plus the CameraDevice base class. Camera model packages under
device/models/ subclass CameraDevice and declare only model-specifics.
"""

from .base import CameraDevice

__all__ = ["CameraDevice"]
