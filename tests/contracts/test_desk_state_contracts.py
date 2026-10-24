"""DeskState does what all five desk interfaces ask, until Task 10 of the cleanup plan splits it into one
class per interface. Its old attributes stay too, so the code that still uses them and the code that uses the
new methods see the same desk."""

from __future__ import annotations

import pytest

from desktalk.desk_state import DeskState
from desktalk.domain import BBox, Danger, Detection, Light, SeenObject, Size
from desktalk.ports import CharacterMemory, DeskScene, LivePictures, PhotoAlbum, SafetyBoard
from tests.contracts.character_memory_contract import CharacterMemoryContract
from tests.contracts.desk_scene_contract import DeskSceneContract
from tests.contracts.live_pictures_contract import LivePicturesContract
from tests.contracts.photo_album_contract import PhotoAlbumContract
from tests.contracts.safety_board_contract import SafetyBoardContract


class TestDeskStatePassesLivePicturesContract(LivePicturesContract):
    @pytest.fixture
    def pictures(self):
        return DeskState()


class TestDeskStatePassesDeskSceneContract(DeskSceneContract):
    @pytest.fixture
    def scene(self):
        return DeskState()


class TestDeskStatePassesSafetyBoardContract(SafetyBoardContract):
    @pytest.fixture
    def board(self):
        return DeskState()


class TestDeskStatePassesCharacterMemoryContract(CharacterMemoryContract):
    @pytest.fixture
    def memory(self):
        return DeskState()


class TestDeskStatePassesPhotoAlbumContract(PhotoAlbumContract):
    @pytest.fixture
    def album(self):
        return DeskState(max_snapshots=3)


@pytest.mark.parametrize('port', [LivePictures, DeskScene, SafetyBoard, CharacterMemory, PhotoAlbum])
def test_desk_state_has_all_of_every_desk_interface(port):
    assert isinstance(DeskState(), port)


# ── the old attributes and the new methods are the same desk ──
def test_pictures_written_the_old_way_are_read_the_new_way_and_back():
    desk = DeskState()
    desk.raw_frames[0], desk.frames[0], desk.cameras[0] = b'plain', b'boxes', True
    desk.light = {'brightness': 80.0, 'lux': None}
    assert (desk.plain(), desk.boxed(0), desk.camera_numbers(), desk.newest_light()) == (
        b'plain', b'boxes', [0], Light(brightness=80.0))
    desk.put(1, b'plain 1', b'boxes 1', Size(320, 240))
    assert (desk.raw_frames[1], desk.frames[1], desk.cameras[1]) == (b'plain 1', b'boxes 1', True)
    assert (desk.frame_w, desk.frame_h) == (320, 240) and desk.first_raw_frame() == b'plain'
    desk.set_light(30.0, 12.0)
    assert desk.light == {'brightness': 30.0, 'lux': 12.0}


def test_the_scene_keeps_the_shapes_the_old_readers_expect():
    desk = DeskState()
    cup = SeenObject.from_detection(Detection('cup', 0.9123, BBox(10, 20, 50, 80)))
    desk.set_members({'cup': cup}, 100.0)
    assert desk.objects == {'cup': {'class_name': 'cup', 'bbox': [10.0, 20.0, 50.0, 80.0], 'center': [30.0, 50.0],
                                    'conf': 0.912, 'cam': 0}}
    assert (desk.object_first_seen, desk.object_last_seen) == ({'cup': 100.0}, {'cup': 100.0})
    desk.note_picture({'cup': cup}, 100.0, person=True, moved=True, new_session=False)
    assert desk.seen_labels == {'cup': {'label': 'cup', 'conf': 0.912, 'bbox': [10.0, 20.0, 50.0, 80.0],
                                        'center': [30.0, 50.0]}}
    assert (desk.session_start, desk.person_last_seen, desk.last_motion_ts) == (100.0, 100.0, 100.0)
    desk.set_scan_labels(['keys'])
    assert desk.lost_last_vlm_visible == {'keys'}


def test_the_scene_reads_what_the_old_writers_wrote():
    desk = DeskState()
    desk.objects = {'cup': {'class_name': 'cup', 'bbox': [10, 20, 50, 80], 'center': [30, 50], 'conf': 0.9}}
    desk.seen_labels = {'keys': Detection('keys', 0.4, BBox(1, 2, 3, 4)).to_dict()}
    assert desk.members() == {'cup': SeenObject(label='cup', box=(10.0, 20.0, 50.0, 80.0), center=(30.0, 50.0),
                                                confidence=0.9, cam=0)}
    assert desk.seen()['keys'].box == (1.0, 2.0, 3.0, 4.0)


def test_the_safety_board_keeps_the_shapes_the_old_readers_expect():
    desk = DeskState()
    spill = Danger(pair=('cup', 'laptop'), label='Spill', kind='zone_intrusion', depth_px=12.0)
    edge = Danger(pair=('cup', 'desk_edge'), label='cup near left edge', kind='edge_danger')
    desk.report('DANGEROUS', [spill, edge], {'risk_level': 3}, 100.0)
    assert desk.safety_state == 'DANGEROUS' and desk.rel_result == {'risk_level': 3}
    assert desk.safety_dangers == [
        {'pair': ['cup', 'laptop'], 'label': 'Spill', 'depth_px': 12.0, 'type': 'zone_intrusion'},
        {'pair': ['cup', 'desk_edge'], 'label': 'cup near left edge', 'type': 'edge_danger'}]
    assert desk.danger_active_since == {frozenset({'cup', 'laptop'}): 100.0, frozenset({'cup', 'desk_edge'}): 100.0}
    desk.safety_dangers = [{'pair': ['cup', 'laptop'], 'label': 'Spill', 'depth_px': 12.0, 'type': 'zone_intrusion'}]
    assert desk.now().dangers == (spill,)
    desk.note_alert({'n': 1}, keep=2)
    assert desk.recent_events == [{'n': 1}]


def test_memory_and_photos_keep_the_shapes_the_old_readers_expect():
    desk = DeskState(max_snapshots=2)
    desk.remember('cup', 'hello', keep=5)
    desk.note_widget_view('cup', 'view', 100.0, keep=5)
    desk.note_user_activity(50.0)
    desk.set_vision_brief('posture=ok')
    assert (desk.char_memory, desk.widget_views) == ({'cup': ['hello']}, {'cup': [{'ts': 100.0, 'action': 'view'}]})
    assert (desk.last_user_activity, desk.vision_habit_brief) == (50.0, 'posture=ok')
    photo_id = desk.add(b'jpeg', 'Spill', 100.0, char_ids=['cup'], labels=['cup'], vlm_labels=[])
    assert desk.snapshots[photo_id] == b'jpeg'
    assert desk.snapshot_order == [{'id': photo_id, 'ts': 100.0, 'label': 'Spill', 'char_ids': ['cup'],
                                    'labels': ['cup'], 'vlm_labels': []}]
