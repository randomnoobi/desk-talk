"""pi/camera_node.py: taking, storing, cleaning up and handing over pictures."""

import importlib.util
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('camera_node', ROOT / 'pi' / 'camera_node.py')
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


@pytest.fixture
def jpg_folder(tmp_path):
    d = tmp_path / 'frames'
    d.mkdir()
    for i in range(3):
        img = np.full((120, 160, 3), 60 * i, np.uint8)
        cv2.imwrite(str(d / f'{i}.jpg'), img)
    return d


def test_photo_store_saves_into_day_folders(tmp_path):
    store = node.PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    p = store.save(b'\xff\xd8 fake jpeg', time.time(), 'pi-000001')
    assert p.is_file() and p.parent.parent == tmp_path / 'photos' and p.name.endswith('_pi-000001.jpg')
    assert store.count() == 1


def test_cleanup_deletes_every_picture(tmp_path):
    store = node.PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    for i in range(5):
        store.save(b'x', time.time(), f'pi-{i}')
    assert store.cleanup() == 5
    assert store.count() == 0
    assert list((tmp_path / 'photos').iterdir()) == []     # empty day folders are removed too


def test_cleanup_can_keep_the_last_minutes(tmp_path):
    store = node.PhotoStore(tmp_path / 'photos', keep_last_sec=120, min_free_mb=0)
    old = store.save(b'x', time.time(), 'old')
    os.utime(old, (time.time() - 600, time.time() - 600))
    store.save(b'x', time.time(), 'new')
    assert store.cleanup() == 1
    assert [p.name.split('_')[1] for p in store.photos()] == ['new.jpg']


def test_disk_nearly_full_deletes_the_oldest(tmp_path, monkeypatch):
    store = node.PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=100)
    for i in range(4):
        store.save(b'x', time.time() + i, f'pi-{i}')
    free = iter([50, 50, 50, 200, 200])                     # room again after two deletions
    monkeypatch.setattr(store, 'free_mb', lambda: next(free))
    assert store.free_up_space() == 2
    assert store.count() == 2


def test_node_step_and_scheduled_cleanup(tmp_path, jpg_folder):
    cfg = dict(node.DEFAULTS, photo_dir=str(tmp_path / 'photos'), delete_every_min=10, min_free_mb=0)
    n = node.CameraNode(cfg, node.FolderCamera(jpg_folder), server=None, key=None)
    metas = [n.step(time.time()) for _ in range(6)]
    assert [m['frame_id'] for m in metas][:2] == ['pi-000001', 'pi-000002']
    assert n.store.count() == 6
    assert n.maybe_cleanup(n.started + 60) is None          # not yet
    assert n.maybe_cleanup(n.started + 601) == 6            # 10 minutes later: all gone
    assert n.store.count() == 0
    assert n.maybe_cleanup(n.started + 700) is None         # the next one is 10 minutes after that


def test_uploader_keeps_only_the_newest(tmp_path):
    store = node.PhotoStore(tmp_path / 'p', 0, 0)
    up = node.Uploader('http://127.0.0.1:9', 'k', 1, store, 'folder')
    up.offer(b'1', {'frame_id': 'a', 'taken_at': 1})
    up.offer(b'2', {'frame_id': 'b', 'taken_at': 2})
    assert up.latest[1]['frame_id'] == 'b' and up.skipped == 1


def test_config_order(tmp_path, monkeypatch):
    monkeypatch.setattr(node, 'settings_from_server', lambda *a, **k: {'fps': 2, 'delete_every_min': 5})
    args = node.parse_args(['--server', 'http://x:5000', '--key', 'k', '--fps', '4'])
    cfg = node.build_config(args)
    assert cfg['fps'] == 4                   # flag wins
    assert cfg['delete_every_min'] == 5      # from the computer's settings
    assert cfg['jpeg_quality'] == 80         # from config/settings.yaml (pi:)


def test_missing_server_prints_help(capsys):
    assert node.main([]) == 2
    assert 'server.py' in capsys.readouterr().out
