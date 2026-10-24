"""Helpers shared by the tests of desktalk/core/vision/ (this file holds no tests itself)."""

from __future__ import annotations

import functools
import shutil
import tempfile
from pathlib import Path

from desktalk.config import AppConfig, load_config
from desktalk.domain import SeenObject
from tests.fakes import FakeDeskScene, FakeLivePictures, FakePhotoAlbum, FakeSafetyBoard

ROOT = Path(__file__).resolve().parents[2]


@functools.cache
def app_config() -> AppConfig:
    """The real config files, without anyone's settings.local.yaml or .env."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'config').mkdir()
        for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
            shutil.copy(ROOT / 'config' / name, root / 'config' / name)
        return load_config(root=root, environ={}, dotenv=root / '.env')


def desk_object(label: str, box, conf: float = 0.9) -> dict:
    """One agent's object as DeskState.objects holds it."""
    x1, y1, x2, y2 = box
    return {'class_name': label, 'bbox': list(box), 'center': [(x1 + x2) / 2, (y1 + y2) / 2], 'conf': conf, 'cam': 0}


def seen_object(label: str, box, conf: float = 0.9) -> SeenObject:
    """One agent's object as the desk scene (DeskScene.members) hands it out."""
    x1, y1, x2, y2 = (float(v) for v in box)
    return SeenObject(label=label, box=(x1, y1, x2, y2), center=((x1 + x2) / 2, (y1 + y2) / 2), confidence=conf)


class FakeDesk(FakeLivePictures, FakeDeskScene, FakePhotoAlbum, FakeSafetyBoard):
    """The fakes of the desk interfaces the vision parts use, in one object (the way DeskState is one today)."""

    def __init__(self, keep_photos: int = 150):
        FakeLivePictures.__init__(self)
        FakeDeskScene.__init__(self)
        FakePhotoAlbum.__init__(self, keep_photos)
        FakeSafetyBoard.__init__(self)
