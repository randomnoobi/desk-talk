"""What every PhotoTaker must do (desktalk.ports.PhotoTaker)."""

from __future__ import annotations


class PhotoTakerContract:
    """Subclass and give two fixtures: `taker` (the live view has a picture) and `taker_without_picture`."""

    def test_each_photo_gets_its_own_id(self, taker):
        first = taker.save('Spill risk')
        second = taker.save('Spill risk', [(4.0, 4.0, 40.0, 30.0)])
        assert isinstance(first, str) and isinstance(second, str)
        assert first != second

    def test_no_photo_before_there_is_a_picture(self, taker_without_picture):
        assert taker_without_picture.save('Spill risk') is None
