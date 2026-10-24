"""
Small interfaces only the background chat jobs need, on top of desktalk/ports.py.

Lens asks for these; bootstrap connects them to the real parts (Phase 3):

    PhoneCamera   the phone camera's tilt and its Stop button   (PhoneFrameSource)
    PiUploads     what came with the Pi's newest picture, and how far its clock is off   (PiFrameSource)
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

    def clock_off_sec(self) -> float | None:
        """The Pi's clock minus this computer's (negative = behind), judged by its quickest recent picture
        (detection time taken off). None before a picture that said when it was taken."""
        ...
