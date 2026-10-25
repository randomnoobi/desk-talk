"""What every PhotoAlbum must do (desktalk.ports.PhotoAlbum): the proof photos kept for the chat and Captures."""

from __future__ import annotations

from desktalk.domain import Photo


class PhotoAlbumContract:
    """Subclass and give an `album` fixture that is empty and keeps at most 3 photos."""

    def test_empty_at_first(self, album):
        assert album.newest(10) == []
        assert album.get('no such photo') is None

    def test_a_photo_comes_back_with_its_picture_and_tags(self, album):
        photo_id = album.add(b'jpeg bytes', 'Spill risk', 100.0, char_ids=['cup'], labels=['cup', 'laptop'],
                             vlm_labels=['keys'])
        photo = album.get(photo_id)
        assert photo == Photo(id=photo_id, ts=100.0, label='Spill risk', jpeg=b'jpeg bytes', char_ids=('cup',),
                              labels=('cup', 'laptop'), vlm_labels=('keys',))

    def test_newest_first(self, album):
        ids = [album.add(b'jpeg', f'photo {n}', 100.0 + n) for n in range(3)]
        assert len(set(ids)) == 3
        assert [p.id for p in album.newest(10)] == ids[::-1]
        assert [p.id for p in album.newest(2)] == ids[:0:-1]

    def test_the_oldest_photo_goes_when_the_album_is_full(self, album):
        ids = [album.add(b'jpeg', f'photo {n}', 100.0 + n) for n in range(4)]
        assert album.get(ids[0]) is None
        assert [p.id for p in album.newest(10)] == ids[:0:-1]
