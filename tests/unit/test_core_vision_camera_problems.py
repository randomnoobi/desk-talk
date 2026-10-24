"""Lens's camera problems on their own: when a problem is said, said again and thanked for (no pictures, no words)."""

from desktalk.core.vision.camera_measure import Reading
from desktalk.core.vision.camera_problems import CameraProblems, Said, Seen
from tests.unit.test_core_vision_kit import app_config

LENS = app_config().lens
FINE = Seen(reading=Reading(brightness=140, glare=0, sharpness=500), boxes=[('cup', (100, 100, 200, 200))],
            size=(480, 360), last_seen_anything=10_000)
DARK = Seen(reading=Reading(brightness=LENS.dark_below - 1, glare=0, sharpness=500), last_seen_anything=10_000)


def problems(thanked=('dark',)):
    return CameraProblems(LENS, thanked=thanked)


def run(p, seen, start, seconds, **check):
    said = []
    for t in range(start, start + seconds):
        said += p.check(t, seen, **{'connected': True, 'fps': 3.0, 'gravity': None, 'reference_gravity': None,
                                    'has_outline': True, 'stopped': False, 'quiet': False, **check})
    return said


def test_a_problem_is_said_once_it_lasted_and_thanked_for_once_it_is_gone():
    p = problems()
    assert run(p, DARK, 1000, 10) == [Said('dark', 'problem')]
    assert p.active() == {'dark': {}}
    assert run(p, FINE, 1010, 10) == [Said('dark', 'fixed')]
    assert p.active() == {}


def test_a_problem_without_a_thank_you_just_ends():
    p = problems(thanked=())
    run(p, DARK, 1000, 10)
    assert run(p, FINE, 1010, 10) == [] and p.active() == {}


def test_slow_video_carries_how_many_pictures_a_second():
    said = run(problems(), FINE, 1000, int(LENS.wait_before_saying_sec.slow) + 1, fps=1.0)
    assert said == [Said('slow', 'problem', {'fps': 1.0})]


def test_no_reading_means_no_picture_problems():
    assert run(problems(), Seen(reading=None, last_seen_anything=10_000), 1000, 30) == []
