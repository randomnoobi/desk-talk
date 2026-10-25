"""The five desk parts in desktalk/core/desk/ each do what their interface asks."""

from __future__ import annotations

import threading

import pytest

from desktalk.core.desk import Album, DangerBoard, LiveView, Memories, Scene, WholeDesk
from desktalk.domain import Danger, Size
from desktalk.ports import CharacterMemory, DeskScene, LivePictures, PhotoAlbum, SafetyBoard
from tests.contracts.character_memory_contract import CharacterMemoryContract
from tests.contracts.desk_scene_contract import CUP, DeskSceneContract
from tests.contracts.live_pictures_contract import LivePicturesContract
from tests.contracts.photo_album_contract import PhotoAlbumContract
from tests.contracts.safety_board_contract import SPILL, SafetyBoardContract


class TestLiveViewPassesLivePicturesContract(LivePicturesContract):
    @pytest.fixture
    def pictures(self):
        return LiveView()


class TestScenePassesDeskSceneContract(DeskSceneContract):
    @pytest.fixture
    def scene(self):
        return Scene()


class TestDangerBoardPassesSafetyBoardContract(SafetyBoardContract):
    @pytest.fixture
    def board(self):
        return DangerBoard()


class TestMemoriesPassesCharacterMemoryContract(CharacterMemoryContract):
    @pytest.fixture
    def memory(self):
        return Memories()


class TestAlbumPassesPhotoAlbumContract(PhotoAlbumContract):
    @pytest.fixture
    def album(self):
        return Album(keep=3)


def whole_desk() -> WholeDesk:
    return WholeDesk(LiveView(), Scene(), DangerBoard(), Memories(), Album(keep=3))


class TestWholeDeskPassesLivePicturesContract(LivePicturesContract):
    @pytest.fixture
    def pictures(self):
        return whole_desk()


class TestWholeDeskPassesDeskSceneContract(DeskSceneContract):
    @pytest.fixture
    def scene(self):
        return whole_desk()


class TestWholeDeskPassesSafetyBoardContract(SafetyBoardContract):
    @pytest.fixture
    def board(self):
        return whole_desk()


class TestWholeDeskPassesCharacterMemoryContract(CharacterMemoryContract):
    @pytest.fixture
    def memory(self):
        return whole_desk()


class TestWholeDeskPassesPhotoAlbumContract(PhotoAlbumContract):
    @pytest.fixture
    def album(self):
        return whole_desk()


@pytest.mark.parametrize(('part', 'port'), [
    (LiveView(), LivePictures), (Scene(), DeskScene), (DangerBoard(), SafetyBoard),
    (Memories(), CharacterMemory), (Album(keep=3), PhotoAlbum)])
def test_each_part_has_all_of_its_interface(part, port):
    assert isinstance(part, port)


@pytest.mark.parametrize('port', [LivePictures, DeskScene, SafetyBoard, CharacterMemory, PhotoAlbum])
def test_the_whole_desk_has_all_of_every_desk_interface(port):
    assert isinstance(whole_desk(), port)


def test_the_whole_desk_is_the_same_desk_as_its_parts():
    pictures, scene, board, memory, album = LiveView(), Scene(), DangerBoard(), Memories(), Album(keep=3)
    desk = WholeDesk(pictures, scene, board, memory, album)
    desk.put(0, b'plain', b'boxes', Size(320, 240))
    desk.set_members({'cup': CUP}, 100.0)
    desk.report('DANGEROUS', [SPILL], {'risk_level': 3}, 100.0)
    desk.note_user_activity(50.0)
    photo_id = desk.add(b'jpeg', 'Spill', 100.0)
    assert pictures.plain(0) == b'plain' and scene.members() == {'cup': CUP}
    assert board.now().state == 'DANGEROUS' and memory.user_active_at() == 50.0
    assert album.get(photo_id) is not None


def test_an_empty_picture_counts_as_none():
    pictures = LiveView()
    pictures.put(0, b'', b'', Size(640, 480))
    assert pictures.plain(0) is None and pictures.boxed(0) is None


def test_the_size_before_the_first_picture():
    assert LiveView().size() == Size(640, 480)


def test_photo_ids_are_twelve_letters_as_they_have_always_been():
    assert len(Album(keep=3).add(b'jpeg', 'x', 1.0)) == 12


def test_the_newest_report_keeps_the_dangers_as_sent():
    board = DangerBoard()
    zone = Danger(pair=('cup', 'laptop'), label='Spill', kind='zone_intrusion', depth_px=12)
    board.report('DANGEROUS', [zone], None, 100.0)
    assert board.now().dangers[0].to_dict() == {'pair': ['cup', 'laptop'], 'label': 'Spill', 'depth_px': 12,
                                                'type': 'zone_intrusion'}


def test_parts_can_be_used_from_many_threads_at_once():
    album, memory = Album(keep=50), Memories()

    def work(n: int) -> None:
        for i in range(200):
            album.add(b'jpeg', f'{n}-{i}', float(i))
            memory.remember('cup', f'{n}-{i}', keep=10)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(album.newest(100)) == 50 and len(memory.recent('cup')) == 10
