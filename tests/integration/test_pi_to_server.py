"""
Raspberry Pi → computer over real HTTP:
camera_node.py takes pictures from a folder, saves them, uploads them to a
running server; the server turns them into detection events in the event log;
the Pi deletes its pictures on schedule.
"""

import importlib.util
import socket
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from werkzeug.serving import make_server

from conftest import obj
from desktalk.web.routes import create_web_app

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('camera_node', ROOT / 'pi' / 'camera_node.py')
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(make_app):
    app = make_app(synchronous=False)
    app.detector.objects = [obj('laptop', 20, 20, 120, 90), obj('cup', 130, 40, 150, 80)]
    app.producer.start()
    port = free_port()
    srv = make_server('127.0.0.1', port, create_web_app(app), threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield app, f'http://127.0.0.1:{port}'
    srv.shutdown()
    app.producer.stop()
    app.stream.close()


@pytest.fixture
def frames(tmp_path):
    d = tmp_path / 'frames'
    d.mkdir()
    for i in range(4):
        img = np.full((180, 240, 3), 90 + 20 * i, np.uint8)
        img[::6] = 30
        cv2.imwrite(str(d / f'{i}.jpg'), img)
    return d


def test_pi_streams_to_the_computer(server, frames, tmp_path):
    app, url = server
    args = node.parse_args(['--server', url, '--key', 'testkey', '--camera', f'folder:{frames}',
                            '--fps', '6', '--photo-dir', str(tmp_path / 'pi-photos'), '--delete-every-min', '0.05'])
    cfg = node.build_config(args)
    assert cfg['fps'] == 6
    n = node.CameraNode(cfg, node.open_camera(args.camera, 240, 180, 80), url, 'testkey')
    t = threading.Thread(target=n.run, kwargs={'max_pictures': 30}, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive()
    assert n.taken == 30
    deadline = time.time() + 10
    while time.time() < deadline and app.producer.frames_done < 5:
        time.sleep(0.1)
    app.stream.wait_idle(5)

    # the computer got pictures and made detection events from them
    assert n.uploader.sent >= 5 and n.uploader.failed == 0
    assert app.producer.frames_done >= 5
    assert app.source.last_frame_id.startswith('pi-')
    day = app.event_log.days()[0]
    raw = app.event_log.recent_detections(day, 100)
    assert raw and raw[0]['source'] == 'pi' and raw[0]['frame_id'].startswith('pi-')
    assert {o['label'] for o in raw[0]['detected_obj']} == {'laptop', 'cup'}
    # the agents joined (the node ran ~5 s)
    assert set(app.roster.member_ids()) == {'computer', 'cup'}
    # the Pi deleted its pictures every 3 seconds (0.05 min), so few are left
    assert n.store.deleted_total > 0
    assert n.store.count() < 30


def test_wrong_key_is_reported(server, frames, tmp_path, capsys):
    app, url = server
    cfg = dict(node.DEFAULTS, photo_dir=str(tmp_path / 'p'), fps=10, min_free_mb=0)
    n = node.CameraNode(cfg, node.FolderCamera(frames), url, 'WRONG')
    t = threading.Thread(target=n.run, kwargs={'max_pictures': 5}, daemon=True)
    t.start()
    t.join(15)
    time.sleep(0.5)
    assert n.uploader.sent == 0 and n.uploader.failed >= 1 and '403' in n.uploader.last_error
    assert 'Pairing key does not match' in capsys.readouterr().out


def test_unreachable_computer_keeps_pictures_coming(frames, tmp_path):
    cfg = dict(node.DEFAULTS, photo_dir=str(tmp_path / 'p'), fps=20, min_free_mb=0, upload_timeout_sec=0.5)
    n = node.CameraNode(cfg, node.FolderCamera(frames), 'http://127.0.0.1:9', 'k')
    n.run(max_pictures=10)
    assert n.taken == 10 and n.store.count() == 10       # pictures are still taken and saved
