"""What every DeskScene must do (desktalk.ports.DeskScene): what the camera sees on the desk right now."""

from __future__ import annotations

from desktalk.domain import SeenObject

CUP = SeenObject(label='cup', box=(10.0, 20.0, 50.0, 80.0), center=(30.0, 50.0), confidence=0.91)
KEYS = SeenObject(label='keys', box=(100.0, 100.0, 120.0, 110.0), center=(110.0, 105.0), confidence=0.4)


class DeskSceneContract:
    """Subclass and give a `scene` fixture (nothing seen yet)."""

    def test_nothing_seen_yet(self, scene):
        assert scene.members() == {} and scene.seen() == {}
        assert scene.first_seen('cup') is None and scene.last_seen('cup') is None
        assert scene.person_seen_at() is None and scene.last_motion() is None
        assert scene.session_started_at() is None
        assert scene.scan_labels() == set()

    def test_the_agents_on_the_desk_and_when_they_were_seen(self, scene):
        scene.set_members({'cup': CUP}, 100.0)
        scene.set_members({'cup': CUP}, 105.0)
        assert scene.members() == {'cup': CUP}
        assert (scene.first_seen('cup'), scene.last_seen('cup')) == (100.0, 105.0)
        scene.set_members({}, 110.0)
        assert scene.members() == {}
        assert (scene.first_seen('cup'), scene.last_seen('cup')) == (100.0, 105.0)

    def test_what_comes_back_is_a_copy(self, scene):
        scene.set_members({'cup': CUP}, 100.0)
        scene.members()['keys'] = KEYS
        scene.note_picture({'cup': CUP}, 100.0, person=False, moved=False, new_session=False)
        scene.seen()['keys'] = KEYS
        scene.set_scan_labels(['keys'])
        scene.scan_labels().add('phone')
        assert scene.members() == {'cup': CUP} and scene.seen() == {'cup': CUP}
        assert scene.scan_labels() == {'keys'}

    def test_each_analysed_picture(self, scene):
        scene.note_picture({'cup': CUP, 'keys': KEYS}, 100.0, person=False, moved=False, new_session=False)
        assert scene.session_started_at() == 100.0                # the first picture starts a session
        assert scene.seen() == {'cup': CUP, 'keys': KEYS}
        assert scene.person_seen_at() is None and scene.last_motion() is None
        scene.note_picture({}, 110.0, person=True, moved=True, new_session=False)
        assert scene.seen() == {}
        assert (scene.session_started_at(), scene.person_seen_at(), scene.last_motion()) == (100.0, 110.0, 110.0)
        scene.note_picture({}, 500.0, person=False, moved=False, new_session=True)
        assert (scene.session_started_at(), scene.person_seen_at(), scene.last_motion()) == (500.0, 110.0, 110.0)

    def test_what_the_last_vision_scan_saw(self, scene):
        scene.set_scan_labels(['keys', 'airpods'])
        assert scene.scan_labels() == {'keys', 'airpods'}
        scene.set_scan_labels(set())
        assert scene.scan_labels() == set()
