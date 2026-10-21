"""Extra rules for cameras that send us pictures (Pi, phone): the newest picture wins."""

from __future__ import annotations

from tests.contracts.frame_source_contract import picture


class PushSourceContract:
    """Subclass and give `source` (a FrameSource that is also a PictureInbox) and `jpeg_of(picture) -> bytes`."""

    def test_not_connected_before_any_picture(self, source):
        assert not source.connected

    def test_newest_picture_wins(self, source, jpeg_of):
        assert source.push(jpeg_of(picture(10)))
        assert source.push(jpeg_of(picture(200)))
        frame = source.read(timeout=0.5)
        assert int(frame.picture[0, 0, 0]) > 150
        assert source.read(timeout=0.01) is None

    def test_garbage_is_refused(self, source):
        assert source.push(b'not a jpeg') is False
        assert source.read(timeout=0.01) is None

    def test_keeps_the_original_jpeg(self, source, jpeg_of):
        data = jpeg_of(picture(77))
        source.push(data)
        assert source.read(timeout=0.5).jpeg == data
