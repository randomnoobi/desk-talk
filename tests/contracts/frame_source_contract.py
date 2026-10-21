"""What every FrameSource must do (desktalk.ports.FrameSource)."""

from __future__ import annotations

import numpy as np

from desktalk.domain import Frame


def picture(value: int):
    return np.full((48, 64, 3), value, np.uint8)


class FrameSourceContract:
    """Subclass and give fixtures: `source`, and `send`: a function send(source, picture) that makes
    a new picture arrive (push it, or have the fake camera produce it)."""

    def test_kind_is_named(self, source):
        assert source.kind in ('pi', 'phone', 'webcam', 'fake')

    def test_nothing_new_gives_none(self, source):
        assert source.read(timeout=0.01) is None

    def test_a_new_picture_is_handed_out_once(self, source, send):
        send(source, picture(10))
        frame = source.read(timeout=0.5)
        assert isinstance(frame, Frame)
        assert int(frame.picture[0, 0, 0]) == 10
        assert frame.source == source.kind
        assert source.read(timeout=0.01) is None

    def test_numbers_count_up(self, source, send):
        send(source, picture(1))
        first = source.read(timeout=0.5)
        send(source, picture(2))
        second = source.read(timeout=0.5)
        assert second.number > first.number

    def test_connected_after_a_picture(self, source, send):
        send(source, picture(3))
        source.read(timeout=0.5)
        assert source.connected
