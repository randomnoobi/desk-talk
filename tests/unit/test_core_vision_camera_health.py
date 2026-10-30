"""Lens's camera checks: brightness, glare, blur, things cut off, the camera moving, slow or lost video."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from desktalk.adapters.picture import OpenCvPictureTools
from desktalk.core.vision.camera_health import CameraHealth
from desktalk.domain import BBox, Detection
from desktalk.ports import CameraHealthView, PictureCheck
from tests.unit.test_core_vision_kit import app_config

LENS = app_config().lens
WORDS = app_config().prompts.camera
TOOLS = OpenCvPictureTools()
CUP = Detection('cup', 0.9, BBox(100, 100, 200, 200))
PERSON = Detection('person', 0.9, BBox(300, 0, 480, 300))


def bright():
    f = np.full((360, 480, 3), 140, np.uint8)       # twice the measuring width: quick to shrink
    f[::8] = 30                                      # some texture so it isn't "blurry"
    return f


DARK = (bright() * 0.15).astype(np.uint8)
BLACK = np.zeros((360, 480, 3), np.uint8)
WHITE = np.full((360, 480, 3), 255, np.uint8)
FLAT = np.full((360, 480, 3), 140, np.uint8)        # nothing at all in it: a plain cover (paper, a hand)
GLARE = bright()
GLARE[:, 240:] = 255                                # half the picture pure white, the other half the desk
# soft stripes: there are shapes, but nothing has a sharp edge (sharpness about 16): blurry, not covered
SOFT = np.repeat((140 + 60 * np.sin(np.arange(480) * 2 * np.pi / 48)).round().astype(np.uint8)[None, :, None],
                 360, 0).repeat(3, 2)
BOOK_IN_FRONT = Detection('book', 0.9, BBox(10, 5, 470, 355))      # one thing filling almost the whole picture
REAL_DESK = Path(__file__).parent.parent / 'fixtures' / 'real_drinks_2026_10_25.json'


def health(source='phone', lens=LENS, words=WORDS, **kw):
    return CameraHealth(lens, source_kind=source, moved_within_sec=1.5, pictures=TOOLS, words=words, **kw)


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
    assert h.issues() == [{'key': 'covered', 'title': 'Camera is blocked',
                           'fix': 'Move whatever is in front of the lens, and point the camera at the desk.'}]


def test_glare():
    h = health()
    said = run(h, 10, GLARE)
    assert said == ['glare:problem']


def test_blurry():
    assert run(health(), 13, SOFT) == ['blurry:problem']


@pytest.mark.parametrize('picture', [WHITE, FLAT], ids=['white', 'gray'])
def test_a_plain_cover_at_any_brightness_is_covered_not_glare_or_blurry(picture):
    """White paper or a well-lit hand on the lens: bright, but with no detail at all."""
    assert run(health(), 30, picture) == ['covered:problem']


def test_a_dim_plain_picture_is_dark_not_covered():
    """A dim evening desk with little contrast has little detail too, but it is too dark, not a blocked lens."""
    dim = np.full((360, 480, 3), 30, np.uint8)       # between covered_below (12) and dark_below (45)
    assert run(health(), 30, dim) == ['dark:problem']


def test_how_little_detail_counts_as_covered_comes_from_the_settings():
    assert run(health(lens=LENS.model_copy(update={'covered_detail_below': 0})), 13, FLAT) == ['blurry:problem']


def test_one_thing_held_in_front_of_the_lens_is_covered():
    h = health()
    assert run(h, 4, bright(), things=(BOOK_IN_FRONT,)) == []
    assert run(h, 2, bright(), things=(BOOK_IN_FRONT,), start=1004) == ['covered:problem']


def test_how_much_of_the_picture_one_thing_must_fill_comes_from_the_settings():
    h = health(lens=LENS.model_copy(update={'covered_by_box_share': 0.05}))     # the cup is about 6%
    assert run(h, 10, bright()) == ['covered:problem']


def test_a_person_or_the_desk_filling_the_picture_is_not_covered():
    near = Detection('person', 0.9, BBox(0, 0, 480, 360))
    desk = Detection('dining table', 0.9, BBox(0, 0, 480, 360))
    said = run(health(ignore_labels={'dining table'}), 30, bright(), things=(CUP, near, desk))
    assert 'covered:problem' not in said


def test_the_things_on_a_real_desk_are_not_covered():
    """The user's desk on 25 October: the laptop is the biggest box, about a quarter of the picture."""
    pictures = json.loads(REAL_DESK.read_text())
    width, height = pictures[0]['size']
    desk = np.full((height, width, 3), 140, np.uint8)
    desk[::8] = 30
    h, said = health(), []
    for t, p in enumerate(pictures, start=1000):
        things = [Detection(d['label'], d['conf'], BBox(*d['bbox'])) for d in p['detections']]
        h.sample(desk, things, t)
        said += [m['key'] for m in h.check(t, connected=True, fps=3.0)]
    assert 'covered' not in said


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
    tilted: dict[str, Any] = {'gravity': (0, 9.8, 0), 'reference_gravity': (0, 9.8, 4)}
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
    assert h.phone_tip() == 'Camera is blocked'


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
    h._problems._active = SwitchingDict()
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


# ── what Lens says for each problem, word for word (as the old code said it) ──
TILTED = {'gravity': (0, 9.8, 0), 'reference_gravity': (0, 9.8, 4)}
PROBLEMS = {     # key: (picture, things in it, check arguments)
    'dark': (DARK, (CUP,), {}),
    'glare': (GLARE, (CUP,), {}),
    'blurry': (SOFT, (CUP,), {}),
    'covered': (BLACK, (CUP,), {}),
    'empty': (bright(), (), {}),
    'slow': (bright(), (CUP,), {'fps': 1.0}),
    'bumped': (bright(), (CUP,), TILTED),
}


def first_words(h, key, start=1000, most=400):
    """(seconds until Lens said something, what it said) for one picture and one check a second."""
    picture, things, check = PROBLEMS[key]
    for t in range(start, start + most):
        h.sample(picture, list(things), t)
        said = h.check(t, **{'connected': True, 'fps': 3.0, **check})
        if said:
            return t - start, said
    return None, []


@pytest.mark.parametrize('key, waited, text, detail, title, fix', [
    ('dark', 8, "It's too dark for me to see the desk well. Could you turn on a lamp?", {},
     'Too dark', 'Turn on a lamp near the desk.'),
    ('glare', 8, 'Something really bright is shining into my camera. Can you angle me away from the window or lamp?',
     {}, 'Glare', 'Angle the camera away from windows or bright lights.'),
    ('blurry', 12, 'Everything looks blurry to me. Could you wipe my lens, or move me back a little so I can focus?',
     {}, 'Blurry picture', 'Wipe the lens, or move the camera back a little so it can focus.'),
    ('covered', 4, "I can't see the desk anymore. Is something in front of my lens, or did the camera get turned "
     'away?', {}, 'Camera is blocked', 'Move whatever is in front of the lens, and point the camera at the desk.'),
    ('empty', 181, "I can't spot anything I recognize on the desk. Am I pointed at it? Tilt me so the whole desk "
     'top is in the picture.', {}, 'Nothing recognized', 'Tilt the camera so the whole desk top is in the picture.'),
    ('slow', 30, 'The pictures are only coming through at about 1.0 a second. Keeping the camera near the Wi-Fi '
     'router (and plugged in) helps.', {'fps': 1.0}, 'Video is slow',
     'Move the camera (or the Wi-Fi router) closer, and keep it plugged in.'),
    ('bumped', 5, "Whoa, I think I got bumped. I'm tilted about 22° from where I was, so the desk edges you marked "
     'are off now. Put me back, or mark the edges again in Settings.', {'degrees': 22}, 'Phone was moved',
     'Put the phone back, or mark the desk edges again in Settings.'),
])
def test_each_problem_is_said_in_the_same_words_after_the_same_wait(key, waited, text, detail, title, fix):
    h = health()
    assert first_words(h, key) == (waited, [{'key': key, 'kind': 'problem', 'text': text, 'detail': detail}])
    assert h.issues() == [{'key': key, 'title': title, 'fix': fix}]
    assert h.phone_tip() == (title if key not in ('empty', 'slow') else None)


@pytest.mark.parametrize('key, thanks', [
    ('dark', 'Much better, thanks for the light!'),
    ('glare', 'Glare is gone. Thanks!'),
    ('blurry', 'Sharp again. I can see clearly now.'),
    ('covered', 'There we go, I can see again.'),
    ('empty', 'I can see the desk now. Thanks!'),
    ('bumped', "Thanks, I'm back where I was."),
])
def test_each_problem_that_clears_up_gets_its_own_thanks(key, thanks):
    h = health()
    waited, _ = first_words(h, key)
    assert waited is not None
    start = 1000 + waited + 1
    said = []
    for t in range(start, start + 30):
        h.sample(bright(), [CUP], t)
        said += h.check(t, connected=True, fps=3.0, gravity=(0, 9.8, 0), reference_gravity=(0, 9.8, 0))
    assert said == [{'key': key, 'kind': 'fixed', 'text': thanks}]


def test_the_tip_to_mark_the_desk_word_for_word():
    h = health()
    assert run(h, 120, bright(), has_outline=False) == []
    h.sample(bright(), [CUP], 1120)
    assert h.check(1120, connected=True, fps=3.0, has_outline=False) == [{'key': 'edge_tip', 'kind': 'tip', 'text': (
        'Tip from me, Lens: open Settings and tap the corners of your desk on the picture. Then I know where the '
        'desk really ends, and everyone can warn you before something slides off the edge.')}]


# ── the wording comes from config/prompts.yaml → camera ──
def words_with(**changes):
    return WORDS.model_copy(update=changes)


def test_what_lens_says_comes_from_the_prompts_file():
    problems = WORDS.problems.model_copy(update={'dark': 'Lights, please!'})
    fixed = WORDS.fixed.model_copy(update={'dark': 'Ah, light.'})
    h = health(words=words_with(problems=problems, fixed=fixed))
    texts = []
    for t in range(1000, 1020):
        h.sample(DARK if t < 1010 else bright(), [CUP], t)
        texts += [m['text'] for m in h.check(t, connected=True, fps=3.0)]
    assert texts == ['Lights, please!', 'Ah, light.']


def test_the_app_panel_titles_come_from_the_prompts_file():
    titles = WORDS.titles.model_copy(update={'covered': WORDS.titles.covered.model_copy(update={'title': 'Lens shut'})})
    h = health(words=words_with(titles=titles))
    run(h, 30, BLACK)
    assert h.issues()[0]['title'] == 'Lens shut' and h.phone_tip() == 'Lens shut'


@pytest.mark.parametrize('source', ['pi', 'phone', 'webcam'])
def test_lost_video_words_come_from_the_prompts_file_for_each_camera(source):
    h = health(source)
    run(h, 2, bright())
    said = [m for t in range(1002, 1030) for m in h.check(t, connected=False)]
    assert said[0]['text'] == getattr(WORDS.lost, source)
    assert h.issues() == [{'key': 'lost', 'title': WORDS.lost_title, 'fix': getattr(WORDS.lost_fix, source)}]


def test_a_cut_off_object_on_an_unknown_side_gets_the_other_hint():
    h = health()
    assert h._problem_text('cutoff', {'object': 'cup', 'side': 'middle'}) == WORDS.problems.cutoff.format(
        object='cup', side='middle', hint=WORDS.other_side_hint)
    assert h._problem_text('cutoff', {}) == WORDS.problems.cutoff.format(
        object=WORDS.unknown_object, side='left', hint=WORDS.side_hints.left)


def test_bumped_without_degrees_says_the_setting():
    assert health()._problem_text('bumped', {}) == WORDS.problems.bumped.format(degrees=LENS.bump_degrees)


def test_slow_says_the_pictures_a_second():
    assert health()._problem_text('slow', {'fps': 1.234}) == WORDS.problems.slow.format(fps=1.234)
