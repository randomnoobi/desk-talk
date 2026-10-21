"""
Small interfaces only the background chat jobs need, on top of desktalk/ports.py.

Lens asks for these; bootstrap connects them to the real parts (Phase 3):

    PhoneCamera   the phone camera's tilt and its Stop button   (PhoneFrameSource)
    PiUploads     what came with the Pi's newest picture         (PiFrameSource keeps it for /api/health)
    DeskLayout    the desk corners marked in Settings            (the desk rules)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from desktalk.domain import UploadInfo

Gravity = tuple[float, float, float]        # which way is down for the phone, from its motion sensor


@runtime_checkable
class PhoneCamera(Protocol):
    def recent_gravity(self) -> Gravity | None:
        """The phone's latest tilt reading, or None if it hasn't sent one lately."""
        ...

    @property
    def stopped_by_user(self) -> bool:
        """You pressed Stop on the phone (so the video stopping isn't a problem)."""
        ...


@runtime_checkable
class PiUploads(Protocol):
    def latest_upload(self) -> tuple[UploadInfo, float] | None:
        """What the Pi sent with its newest picture (health, its own clock time ...) and when that picture
        arrived, on this computer's clock. None before the first picture."""
        ...


@runtime_checkable
class DeskLayout(Protocol):
    def has_outline(self) -> bool:
        """The desk corners are marked (Settings)."""
        ...

    def reference_gravity(self) -> Gravity | None:
        """How the phone was tilted when the corners were marked (None if they aren't, or it wasn't the phone)."""
        ...
