"""Fakes for the health checks and what they read (desktalk/core/health/)."""

from __future__ import annotations

from desktalk.domain import HealthMeasure


class FakeDetectionTimes:
    """Implements DetectionTimes. A test sets found_at (clock.monotonic())."""

    def __init__(self, found_at: float | None = None):
        self.found_at = found_at


class FakeDiskSpace:
    """Implements DiskSpace: the free space a test gives it (None = can't be read)."""

    def __init__(self, free_mb: float | None = 10_000.0):
        self.free = free_mb

    def free_mb(self) -> float | None:
        return self.free


class FakeHealthCheck:
    """Implements HealthCheck: the measures a test gives it (none by default)."""

    def __init__(self, measures: list[HealthMeasure] | None = None):
        self.given = list(measures or [])

    def measures(self) -> list[HealthMeasure]:
        return list(self.given)
