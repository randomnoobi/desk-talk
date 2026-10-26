"""TracePhotoTaker (desktalk/core/object_states/trace_photos.py): keeps a picture of an object, with its box, when it
appears, when it joins the chat, and every settings → dev.trace_photo_every_sec while it's there."""

from __future__ import annotations

import functools
import shutil
import tempfile
from pathlib import Path

from desktalk.config import AppConfig, DevSettings, load_config
from desktalk.core.object_states.trace_photos import TracePhotoTaker
from desktalk.domain import AgentJoined, BBox, Detection, DetectionEvent, ObjectStateChanged, Size
from desktalk.domain.events import Light
from tests.fakes import FakeClock, FakeLivePictures, FakeTracePhotos, SyncEventBus

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
    def __init__(self, plain: bytes | None = JPEG, settings: DevSettings | None = None):
        self.bus = SyncEventBus()
        self.clock = FakeClock()
        self.pictures = FakeLivePictures()
        if plain is not None:
            self.pictures.put(0, plain, None, Size(640, 480))
        self.photos = FakeTracePhotos(kept=50)
        self.settings = settings or app_config().dev
        self.taker = TracePhotoTaker(self.bus, self.pictures, self.photos, self.settings, self.clock)

    def see(self, *detections: Detection) -> None:
        self.bus.publish(DetectionEvent(ts=self.clock.now(), frame_id='pi-1', source='pi', size=(640, 480),
                                        detections=detections, person=False, light=Light(brightness=120)))

    def change(self, change: str, label: str = 'cup') -> None:
        new, old = ('PRESENT', 'ABSENT') if change == 'appeared' else ('ABSENT', 'PRESENT')
        self.bus.publish(ObjectStateChanged(ts=self.clock.now(), change=change, label=label, prev_state=old,
                                            new_state=new, device_id='pi'))

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


def test_every_photo_while_present():
    rig = Rig()
    every = rig.settings.trace_photo_every_sec
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
    rig.clock.advance(rig.settings.trace_photo_every_sec * 2)
    rig.see(CUP)
    assert rig.whys() == ['appeared']


def test_no_every_photo_while_this_picture_does_not_show_it():
    rig = Rig()
    rig.see(CUP)
    rig.change('appeared')
    rig.clock.advance(rig.settings.trace_photo_every_sec)
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
    rig.taker = TracePhotoTaker(rig.bus, rig.pictures, Broken(), rig.settings, rig.clock)
    rig.see(CUP)
    rig.change('appeared')
    assert all(sub['errors'] == 0 for sub in rig.bus.stats())
    assert 'disk full' in caplog.text
