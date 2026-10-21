"""raspberry_pi/camera/photo_store.py: saving pictures on the Pi and deleting them again."""

import time

from raspberry_pi.camera.photo_store import PhotoStore

# 2026-10-21 09:30:05.250, the Pi's local time
TAKEN = time.mktime((2026, 10, 21, 9, 30, 5, 0, 0, -1)) + 0.25


def test_saves_into_a_folder_per_day_named_by_the_time_taken(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    path = store.save(b'\xff\xd8 picture', TAKEN, 'pi-000001')
    assert path == tmp_path / 'photos' / '2026-10-21' / '09-30-05.250_pi-000001.jpg'
    assert path.read_bytes() == b'\xff\xd8 picture'
    assert list(path.parent.iterdir()) == [path]          # nothing half-written left behind
    assert store.count() == 1


def test_a_saved_picture_carries_the_time_it_was_taken(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    path = store.save(b'x', TAKEN, 'pi-000001')
    assert path.stat().st_mtime == TAKEN


def test_cleanup_deletes_every_picture_and_the_empty_day_folders(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    for i in range(5):
        store.save(b'x', TAKEN + i, f'pi-{i}')
    store.save(b'x', TAKEN + 86400, 'next-day')
    assert store.cleanup(TAKEN + 86400) == 6
    assert store.count() == 0
    assert list((tmp_path / 'photos').iterdir()) == []
    assert store.last_cleanup == TAKEN + 86400
    assert store.deleted_total == 6


def test_cleanup_can_keep_the_last_minutes(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=120, min_free_mb=0)
    store.save(b'x', TAKEN - 600, 'old')
    store.save(b'x', TAKEN - 60, 'new')
    assert store.cleanup(TAKEN) == 1
    assert [p.name.split('_')[1] for p in store.photos()] == ['new.jpg']


def test_disk_nearly_full_deletes_the_oldest_until_there_is_room(tmp_path):
    def free_mb():                                           # room again once only two pictures are left
        return 200 if store.count() <= 2 else 50

    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=100, free_mb=free_mb)
    for i in range(4):
        store.save(b'x', TAKEN + i, f'pi-{i}')
    assert store.free_up_space() == 2
    assert [p.name.split('_')[1] for p in store.photos()] == ['pi-2.jpg', 'pi-3.jpg']
    assert store.deleted_total == 2


def test_disk_with_room_deletes_nothing(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=100, free_mb=lambda: 500)
    store.save(b'x', TAKEN, 'pi-1')
    assert store.free_up_space() == 0
    assert store.count() == 1


def test_free_space_comes_from_the_disk_by_default(tmp_path):
    store = PhotoStore(tmp_path / 'photos', keep_last_sec=0, min_free_mb=0)
    assert store.free_mb() > 0
