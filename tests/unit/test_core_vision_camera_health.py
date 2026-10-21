"""Lens's camera checks: brightness, glare, blur, things cut off, the camera moving, slow or lost video."""

import threading
import time

import numpy as np
import pytest

from desktalk.core.vision.camera_health import CameraHealth
from desktalk.domain import BBox, Detection
from desktalk.ports import CameraHealthView, PictureCheck
from tests.unit.test_core_vision_kit import app_config

LENS = app_config().lens
CUP = Detection('cup', 0.9, BBox(100, 100, 200, 200))
PERSON = Detection('person', 0.9, BBox(300, 0, 480, 300))


def bright():
    f = np.full((360, 480, 3), 140, np.uint8)       # twice the measuring width: quick to shrink
    f[::8] = 30                                      # some texture so it isn't "blurry"
    return f


DARK = (bright() * 0.15).astype(np.uint8)
BLACK = np.zeros((360, 480, 3), np.uint8)
WHITE = np.full((360, 480, 3), 255, np.uint8)
FLAT = np.full((360, 480, 3), 140, np.uint8)        # nothing sharp in it: blurry


def health(source='phone', lens=LENS, **kw):
    return CameraHealth(lens, source_kind=source, moved_within_sec=1.5, **kw)


def lens_with(**waits):
    return LENS.model_copy(update={'wait_before_saying_sec': LENS.wait_before_saying_sec.model_copy(update=waits)})


def run(h, seconds, picture=None, things=(CUP,), start=1000, **check):
    """One picture and one check a second. Returns 'key:kind' for everything Lens said."""
    said = []
    for t in range(start, start + seconds):
        if picture is not None:
            h.sample(picture, list(things), t)
        said += [f"{m['key']}:{m['kind']}" for m in h.check(t, **{'connected': True, 'fps': 3.0, **check})]
    return said


def test_it_is_a_picture_check_and_a_camera_health_view():
    h = health()
    assert isinstance(h, PictureCheck) and isinstance(h, CameraHealthView)


# Ported from test_lens_notices_dark_and_says_thanks
def test_lens_notices_dark_and_says_thanks():
    h = health(watch_labels={'cup'})
    said = run(h, 20, bright()) + run(h, 20, DARK, start=1020) + run(h, 20, bright(), start=1040)
    assert said == ['dark:problem', 'dark:fixed']


def test_dark_must_last_a_while_before_lens_says_so():
    h = health()
    assert run(h, 7, DARK) == []
    assert run(h, 2, DARK, start=1007) == ['dark:problem']


def test_how_long_to_wait_comes_from_the_settings():
    assert run(health(lens=lens_with(dark=2)), 3, DARK) == ['dark:problem']


def test_thanks_only_after_the_problem_stays_gone():
    h = health()
    run(h, 10, DARK)
    assert run(h, 4, bright(), start=1010) == []
    assert run(h, 2, bright(), start=1014) == ['dark:fixed']


def test_covered_lens_hides_the_dark_and_blurry_problems():
    h = health()
    assert run(h, 30, BLACK) == ['covered:problem']
    assert h.issues() == [{'key': 'covered', 'title': 'Camera is covered',
                           'fix': 'Check nothing is on the lens and the camera is facing the desk.'}]


def test_glare():
    h = health()
    said = run(h, 10, WHITE)
    assert said == ['glare:problem']


def test_blurry():
    assert run(health(), 13, FLAT) == ['blurry:problem']


def test_nothing_recognized_for_minutes():
    h = health()
    assert run(h, 180, bright(), things=(PERSON,)) == []
    said = run(h, 3, bright(), things=(PERSON,), start=1180)
    assert said == ['empty:problem']


def test_object_cut_off_at_the_side_of_the_picture():
    h = health()
    cut = Detection('cup', 0.9, BBox(0, 200, 80, 300))
    assert run(h, 45, bright(), things=(cut,)) == []
    msgs = []
    for t in range(1045, 1048):
        h.sample(bright(), [cut], t)
        msgs += h.check(t, connected=True, fps=3.0)
    assert [m['key'] for m in msgs] == ['cutoff']
    assert msgs[0]['text'] == ("Your cup is cut off at the left side of the picture, so the others can't see all "
                               "of it. Could you point me a little more to the left?")
    assert msgs[0]['detail'] == {'object': 'cup', 'side': 'left'}
    assert h.issues()[0]['title'] == 'Cup is cut off (left)'
    assert h.phone_tip() == 'Something is cut off'


# Ported from test_cut_off_uses_the_ignore_list
def test_cut_off_skips_ignored_things_and_people():
    h = health(ignore_labels={'chair'})
    chair = Detection('chair', 0.9, BBox(0, 200, 80, 300))
    person = Detection('person', 0.9, BBox(0, 200, 80, 300))
    assert 'cutoff:problem' not in run(h, 60, bright(), things=(CUP, chair, person))


def test_a_box_filling_the_picture_is_not_cut_off():
    big = Detection('cup', 0.9, BBox(0, 10, 450, 300))
    assert 'cutoff:problem' not in run(health(), 60, bright(), things=(big,))


def test_slow_video():
    h = health()
    said = run(h, 31, bright(), fps=1.0)
    assert said == ['slow:problem']


# Ported from test_three_pictures_a_second_is_not_slow
def test_three_pictures_a_second_is_not_slow():
    assert 'slow:problem' not in run(health('pi'), 60, bright(), fps=2.9)


def test_phone_bumped_away_from_where_the_desk_was_marked():
    h = health()
    msgs = []
    for t in range(1000, 1010):
        h.sample(bright(), [CUP], t)
        msgs += h.check(t, connected=True, fps=3.0, gravity=(0, 9.8, 0), reference_gravity=(0, 9.8, 4))
    assert [m['key'] for m in msgs] == ['bumped']
    assert 'tilted about 22°' in msgs[0]['text']


def test_forgetting_a_problem_means_no_thanks():
    h = health()
    for t in range(1000, 1010):
        h.sample(bright(), [CUP], t)
        h.check(t, connected=True, fps=3.0, gravity=(0, 9.8, 0), reference_gravity=(0, 9.8, 4))
    h.forget('bumped')
    assert run(h, 20, bright(), start=1010) == [] and h.issues() == []


# Ported from test_lens_speaks_for_the_pi_when_pictures_stop
def test_lens_speaks_for_the_pi_when_pictures_stop():
    h = health('pi', ignore_labels={'person'})
    h.sample(bright(), [CUP], 1000)
    h.check(1000, connected=True, fps=3)
    said = []
    for t in range(1, 40):
        said += h.check(1000 + t, connected=False)
    assert said and said[0]['key'] == 'lost' and 'Raspberry Pi' in said[0]['text']
    assert h.issues()[0]['fix'].startswith('Check the Pi')


def test_lost_pi_pictures_name_the_camera_program_that_runs_on_the_pi_now():
    h = health('pi')
    run(h, 2, bright())
    said = []
    for t in range(1002, 1030):
        said += h.check(t, connected=False)
    fix = h.issues()[0]['fix']
    for words in (said[0]['text'], fix):
        assert 'python3 -m raspberry_pi.camera' in words and 'camera_node' not in words


@pytest.mark.parametrize('source, words, fix', [
    ('phone', 'Is its screen still on?', 'Keep the phone unlocked'),
    ('webcam', 'Is it still plugged in', 'Plug the webcam back in'),
])
def test_lost_video_words_fit_the_camera(source, words, fix):
    h = health(source)
    run(h, 2, bright())
    said = []
    for t in range(1002, 1030):
        said += h.check(t, connected=False)
    assert words in said[0]['text']
    issue = h.issues()[0]
    assert (issue['key'], issue['title']) == ('lost', 'No pictures from the camera')
    assert issue['fix'].startswith(fix)


def test_no_complaint_when_the_video_was_stopped_on_purpose():
    h = health()
    run(h, 2, bright())
    said = []
    for t in range(1002, 1030):
        said += h.check(t, connected=False, stopped=True)
    assert said == [] and h.issues() == []


def test_no_complaint_before_the_video_ever_started():
    h = health()
    assert [m for t in range(1000, 1030) for m in h.check(t, connected=False)] == []


def test_one_problem_at_a_time_with_a_gap_between():
    h = health(lens=lens_with(slow=9))        # slow video is ready to be said a second after the dark
    said_at = {}
    for t in range(1000, 1040):
        h.sample(DARK, [CUP], t)
        for m in h.check(t, connected=True, fps=1.0):
            said_at[m['key']] = t
    assert said_at == {'dark': 1008, 'slow': 1008 + LENS.speak_gap_sec}


def test_the_same_problem_is_mentioned_again_only_after_a_long_while():
    h = health()
    said = run(h, 60 * 20, DARK)
    assert said == ['dark:problem']
    assert run(h, 60 * 2, DARK, start=1000 + 60 * 20) == ['dark:problem']


def test_one_thanks_when_several_problems_clear_at_once():
    h = health()
    tilted = {'gravity': (0, 9.8, 0), 'reference_gravity': (0, 9.8, 4)}
    assert run(h, 60, DARK, **tilted) == ['bumped:problem', 'dark:problem']
    assert run(h, 10, bright(), start=1060) == ['dark:fixed']       # dark comes first in PRIORITY


def test_one_time_tip_to_mark_the_desk():
    h = health()
    said = run(h, 200, bright(), has_outline=False)
    assert said == ['edge_tip:tip']
    assert run(health(), 200, bright(), has_outline=True) == []


def test_old_readings_are_not_trusted():
    h = health(lens=lens_with(dark=12))
    h.sample(DARK, [CUP], 1000)
    said = [m['key'] for t in range(1000, 1030) for m in h.check(t, connected=True, fps=3.0)]
    assert 'dark' not in said           # the one dark picture is too old to count after 10 s


def test_phone_tip_shows_the_most_important_problem():
    h = health()
    assert h.phone_tip() is None
    run(h, 30, BLACK)
    assert h.phone_tip() == 'Camera is covered'


# ── the camera moving (so "things moved" isn't the desk changing) ──
def test_moved_recently_after_the_picture_changed_a_lot():
    h = health()
    h.sample(bright(), [CUP], 1000)
    assert not h.moved_recently(1000)
    h.sample(WHITE, [CUP], 1001)
    assert h.moved_recently(1001) and h.moved_recently(1002.4)
    assert not h.moved_recently(1002.5)


def test_pictures_are_measured_at_most_once_a_second():
    h = health()
    h.sample(bright(), [CUP], 1000)
    h.sample(WHITE, [CUP], 1000.5)            # skipped: too soon
    assert not h.moved_recently(1000.5)
    assert run(h, 10, None, start=1001) == []  # and the white picture never counted as glare


class SwitchingDict(dict):
    """A dict that lets other threads run whenever it's asked what it holds: the thread switch that can come at
    any moment on a busy computer, made to happen every time."""

    def __contains__(self, key):
        found = super().__contains__(key)
        time.sleep(0.0005)
        return found


def test_the_web_thread_can_read_the_problems_while_lens_changes_them():
    """issues() and phone_tip() run on the web server's threads while Lens's job checks and forgets problems."""
    h = health(lens=lens_with(dark=0))
    h._active = SwitchingDict()
    h.sample(DARK, [CUP], 1000)
    errors, stop = [], threading.Event()

    def lens_job():
        try:
            while not stop.is_set():
                h.check(1000, connected=True, fps=3.0)       # 'dark' comes back at once (no wait)...
                h.forget('dark')                              # ...and goes away again
        except Exception as exc:
            errors.append(exc)

    job = threading.Thread(target=lens_job)
    job.start()
    try:
        for _ in range(300):
            h.issues()
            h.phone_tip()
    except Exception as exc:
        errors.append(exc)
    finally:
        stop.set()
        job.join(5)
    assert errors == []


def test_problems_found_in_quiet_hours_are_never_thanked_for():
    """Lens says nothing in quiet hours, so a problem then can't be 'fixed, thanks' later either."""
    h = health()
    said = run(h, 20, bright()) + run(h, 20, DARK, start=1020, quiet=True)
    said += run(h, 20, bright(), start=1040)
    assert said == []


def test_a_problem_still_there_after_quiet_hours_is_said_then():
    h = health()
    said = run(h, 20, DARK, quiet=True) + run(h, 2, DARK, start=1020)
    assert said == ['dark:problem']
