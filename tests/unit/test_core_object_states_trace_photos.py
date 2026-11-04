"""TracePhotoTaker (desktalk/core/object_states/trace_photos.py): keeps a picture of an object, with its box, when it
appears, joins the chat, moves or goes away, and every settings → object_story.photo_every_sec while it's there."""

from __future__ import annotations

import functools
import shutil
import tempfile
from pathlib import Path

from desktalk.config import AppConfig, ObjectStorySettings, load_config
from desktalk.core.object_states.trace_photos import TracePhotoTaker
from desktalk.domain import AgentJoined, BBox, Detection, DetectionEvent, ObjectSnapshot, ObjectStateChanged, Size
from desktalk.domain.events import Light
from tests.fakes import FakeClock, FakeLivePictures, FakeObjectStates, FakeTracePhotos, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]
JPEG = b'\xff\xd8 the newest picture \xff\xd9'


@functools.cache
def app_config() -> AppConfig:
    """The real config files, without anyone's settings.local.yaml or .env."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'config').mkdir()
        for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
            shutil.copy(ROOT / 'config' / name, root / 'config' / name)
        return load_config(root=root, environ={}, dotenv=root / '.env')


class Rig:
    def __init__(self, plain: bytes | None = JPEG, settings: ObjectStorySettings | None = None,
                 states: tuple[tuple[str, str], ...] = ()):
        """`states`: (label, state) pairs the object states start with, as restore() leaves them at startup."""
        self.bus = SyncEventBus()
        self.clock = FakeClock()
        self.pictures = FakeLivePictures()
        if plain is not None:
            self.pictures.put(0, plain, None, Size(640, 480))
        self.photos = FakeTracePhotos(timed_kept=50, steps_kept=50)
        self.settings = settings or app_config().object_story
        self.states = FakeObjectStates([ObjectSnapshot(label, state, 0.0) for label, state in states])
        self.taker = TracePhotoTaker(self.bus, self.pictures, self.photos, self.settings, self.clock,
                                     states=self.states)

    def see(self, *detections: Detection) -> None:
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='pi-1', source='pi', size=(640, 480),
                                        detections=detections, person=False, light=Light(brightness=120)))

    def change(self, change: str, label: str = 'cup', bbox: tuple[float, ...] | None = None) -> None:
        new, old = {'appeared': ('PRESENT', 'ABSENT'), 'removed': ('ABSENT', 'PRESENT'),
                    'moved': ('PRESENT', 'PRESENT')}[change]
        self.bus.publish(ObjectStateChanged(ts=self.clock.now(), change=change, label=label, prev_state=old,
                                            new_state=new, device_id='pi', bbox=bbox))

    def join(self, label: str = 'cup') -> None:
        self.bus.publish(AgentJoined(ts=self.clock.now(), agent=label, name=label.title(), label=label, conf=0.8))

    def whys(self, label: str = 'cup') -> list[str]:
        return [p.why for p in self.photos.photos(label)]


CUP = Detection('cup', 0.81, BBox(100, 120, 180, 220))
WEAK_CUP = Detection('cup', 0.5, BBox(400, 300, 420, 330))


def test_photo_on_appear_and_join():
    rig = Rig()
    rig.see(WEAK_CUP, CUP)
    rig.change('appeared')
    rig.clock.advance(3)
    rig.see(CUP)
    rig.join()
    photos = rig.photos.photos('cup')
    assert [p.why for p in photos] == ['appeared', 'joined']
    first = photos[0]
    assert first.label == 'cup' and first.ts == rig.clock.now() - 3
    assert first.confidence == 0.81 and first.box == (100.0, 120.0, 180.0, 220.0)   # the best box
    assert first.size == (640, 480)
    assert rig.photos.jpeg(first.id) == JPEG
    assert photos[0].id != photos[1].id


def test_an_object_brought_back_at_startup_gets_its_every_pictures():
    """After a restart the object states bring the cup back as PRESENT without an 'appeared' change."""
    rig = Rig(states=(('cup', 'PRESENT'), ('book', 'ABSENT')))
    book = Detection('book', 0.9, BBox(300, 300, 400, 400))
    rig.see(CUP, book)
    assert rig.whys() == []
    rig.clock.advance(rig.settings.photo_every_sec)
    rig.see(CUP, book)
    assert rig.whys() == ['every'] and rig.whys('book') == []


def test_a_box_from_before_it_was_removed_is_never_used_again():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.see()
    rig.change('removed')
    rig.join()                                  # a join with no new box seen since: no picture with the old box
    assert rig.whys() == ['appeared', 'removed']
    rig.see(WEAK_CUP)
    rig.join()
    assert rig.photos.photos('cup')[-1].box == (400.0, 300.0, 420.0, 330.0)


MOVED_CUP = Detection('cup', 0.77, BBox(300, 200, 380, 300))


def test_photo_when_it_moves_with_its_box_at_the_new_spot():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.clock.advance(5)
    rig.see(MOVED_CUP)
    rig.change('moved', bbox=(300.0, 200.0, 380.0, 300.0))
    moved = rig.photos.photos('cup')[-1]
    assert rig.whys() == ['appeared', 'moved']
    assert moved.box == (300.0, 200.0, 380.0, 300.0) and moved.confidence == 0.77 and moved.ts == rig.clock.now()


def test_a_moved_change_without_a_box_uses_the_newest_box_seen():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.see(MOVED_CUP)
    rig.change('moved')
    assert rig.photos.photos('cup')[-1].box == (300.0, 200.0, 380.0, 300.0)


def test_photo_when_it_went_away_shows_the_empty_spot_with_its_last_box():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.clock.advance(10)
    rig.pictures.put(0, b'\xff\xd8 the empty desk \xff\xd9', None, Size(640, 480))
    rig.see()                                   # the pictures since don't show it
    rig.change('removed', bbox=(101.0, 121.0, 181.0, 221.0))
    gone = rig.photos.photos('cup')[-1]
    assert rig.whys() == ['appeared', 'removed']
    assert gone.box == (101.0, 121.0, 181.0, 221.0) and gone.ts == rig.clock.now()
    assert rig.photos.jpeg(gone.id) == b'\xff\xd8 the empty desk \xff\xd9'      # the picture where it was gone


def test_every_photo_while_present():
    rig = Rig()
    every = rig.settings.photo_every_sec
    rig.see(CUP)
    rig.change('appeared')
    for _ in range(2):
        rig.clock.advance(every / 2)
        rig.see(CUP)                        # not long enough yet
        rig.clock.advance(every / 2)
        rig.see(CUP)
    assert rig.whys() == ['appeared', 'every', 'every']


def test_no_every_photo_once_it_is_gone():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.change('removed')
    rig.clock.advance(rig.settings.photo_every_sec * 2)
    rig.see(CUP)
    assert rig.whys() == ['appeared', 'removed']


def test_no_every_photo_while_this_picture_does_not_show_it():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.clock.advance(rig.settings.photo_every_sec)
    rig.see()                               # it's still PRESENT, but this picture has no box for it
    assert rig.whys() == ['appeared']
    rig.see(CUP)
    assert rig.whys() == ['appeared', 'every']


def test_no_picture_no_photo():
    rig = Rig(plain=None)
    rig.see(CUP)
    rig.change('appeared')
    rig.join()
    assert rig.photos.photos('cup') == []
    assert all(sub['errors'] == 0 for sub in rig.bus.stats())


def test_no_box_seen_yet_no_photo():
    rig = Rig()
    rig.change('appeared', label='glasses')
    assert rig.photos.photos('glasses') == []


def test_a_failing_keeper_is_only_logged(caplog):
    class Broken(FakeTracePhotos):
        def add(self, photo, jpeg):
            raise OSError('disk full')

    rig = Rig()
    rig.taker = TracePhotoTaker(rig.bus, rig.pictures, Broken(), rig.settings, rig.clock, states=rig.states)
    rig.see(CUP)
    rig.change('appeared')
    assert all(sub['errors'] == 0 for sub in rig.bus.stats())
    assert 'disk full' in caplog.text


def test_a_failing_camera_picture_is_only_logged(caplog):
    rig = Rig()

    def broken(cam=None):
        raise RuntimeError('no camera')
    rig.pictures.plain = broken
    rig.see(CUP)
    rig.change('appeared')
    assert rig.whys() == [] and all(sub['errors'] == 0 for sub in rig.bus.stats())
    assert 'Could not keep a trace picture' in caplog.text
