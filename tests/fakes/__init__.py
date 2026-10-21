"""Fakes for tests: each one behaves like the real part (it passes the same contract tests in
tests/contracts/) but needs no network, camera, Pi, AI key or real time."""

from tests.fakes.clock import FakeClock

__all__ = ['FakeClock']
