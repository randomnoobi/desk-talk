"""What every TracePhotos must do (desktalk/ports.py): the pictures kept of each kind of object for its story (the app
page and the developer page's trace), each with when and why it was taken and the object's box. The timed pictures
('every') and the step pictures (appeared, joined, moved, removed) are kept apart, so a long stay's timed pictures
never push out how the object got there. The fake runs it here; the real files on
disk (TracePhotoFiles) join as one more test class in this file. Both are filled with add(photo, jpeg); a class
that is filled another way overrides the `add` fixture."""

from __future__ import annotations

import pytest

from desktalk.adapters.storage.trace_photo_files import TracePhotoFiles
from desktalk.domain import TracePhoto
from desktalk.ports import TracePhotos
from tests.fakes import FakeTracePhotos

JPEG = b'\xff\xd8\xff\xe0 a tiny made-up jpeg \xff\xd9'
TIMED_KEPT = 3
STEPS_KEPT = 3


def a_photo(photo_id: str, ts: float, label: str = 'cup', why: str = 'appeared') -> TracePhoto:
    return TracePhoto(id=photo_id, label=label, ts=ts, why=why, confidence=0.82, box=(10.0, 20.0, 50.0, 80.0),
                      size=(640, 480))


class TracePhotosContract:
    """Subclass and give a `trace_photos` fixture (no photos yet, keeping TIMED_KEPT timed pictures and STEPS_KEPT
    step pictures per kind of object)."""

    @pytest.fixture
    def trace_photos(self):
        raise NotImplementedError

    @pytest.fixture
    def add(self):
        return lambda photos, photo, jpeg: photos.add(photo, jpeg)

    def test_it_is_trace_photos(self, trace_photos):
        assert isinstance(trace_photos, TracePhotos)

    def test_nothing_kept_yet(self, trace_photos):
        assert trace_photos.photos('cup') == []
        assert trace_photos.jpeg('nope') is None

    def test_photos_come_back_oldest_first_with_their_picture(self, trace_photos, add):
        add(trace_photos, a_photo('p2', 200.0, why='joined'), JPEG + b'2')
        add(trace_photos, a_photo('p1', 100.0), JPEG + b'1')
        add(trace_photos, a_photo('p3', 300.0, why='every'), JPEG + b'3')
        assert [(p.id, p.why) for p in trace_photos.photos('cup')] == [('p1', 'appeared'), ('p2', 'joined'),
                                                                      ('p3', 'every')]
        assert trace_photos.photos('cup')[0] == a_photo('p1', 100.0)
        assert trace_photos.jpeg('p2') == JPEG + b'2'

    def test_each_kind_of_object_has_its_own(self, trace_photos, add):
        add(trace_photos, a_photo('p1', 100.0), JPEG)
        add(trace_photos, a_photo('g1', 110.0, label='glasses'), JPEG)
        assert [p.id for p in trace_photos.photos('glasses')] == ['g1']
        assert [p.id for p in trace_photos.photos('cup')] == ['p1']

    def test_timed_pictures_never_push_out_the_steps(self, trace_photos, add):
        add(trace_photos, a_photo('first', 100.0), JPEG)
        for n in range(TIMED_KEPT + 2):
            add(trace_photos, a_photo(f'e{n}', 200.0 + n, why='every'), JPEG)
        kept = [p.id for p in trace_photos.photos('cup')]
        assert kept == ['first'] + [f'e{n}' for n in range(2, TIMED_KEPT + 2)]
        assert trace_photos.jpeg('first') == JPEG and trace_photos.jpeg('e0') is None

    def test_only_the_newest_steps_are_kept(self, trace_photos, add):
        whys = ['appeared', 'joined', 'moved', 'removed']
        for n, why in enumerate(whys[:STEPS_KEPT + 1]):
            add(trace_photos, a_photo(f's{n}', 100.0 + n, why=why), JPEG)
        add(trace_photos, a_photo('e0', 50.0, why='every'), JPEG)     # an older timed one still has its own room
        assert [p.why for p in trace_photos.photos('cup')] == ['every', *whys[1:STEPS_KEPT + 1]]
        assert trace_photos.jpeg('s0') is None

    def test_what_comes_back_is_a_copy(self, trace_photos, add):
        add(trace_photos, a_photo('p1', 100.0), JPEG)
        trace_photos.photos('cup').clear()
        assert len(trace_photos.photos('cup')) == 1


class TestFakeTracePhotos(TracePhotosContract):
    @pytest.fixture
    def trace_photos(self):
        return FakeTracePhotos(timed_kept=TIMED_KEPT, steps_kept=STEPS_KEPT)


def test_the_fake_keeps_only_the_newest_it_is_told_to():
    fake = FakeTracePhotos(timed_kept=2)
    for n in range(3):
        fake.add(a_photo(f'p{n}', 100.0 + n, why='every'), JPEG)
    assert [p.id for p in fake.photos('cup')] == ['p1', 'p2'] and fake.jpeg('p0') is None


class TestTracePhotoFiles(TracePhotosContract):
    @pytest.fixture
    def trace_photos(self, tmp_path):
        return TracePhotoFiles(tmp_path / 'trace_photos', timed_kept=TIMED_KEPT, steps_kept=STEPS_KEPT)
