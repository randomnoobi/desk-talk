"""Desk geometry (relationships.py) and Lens (camera_health.py), kept from the original tests."""

import numpy as np

from desktalk.vision import camera_health as ch
from desktalk.vision import relationships as rel


def test_desk_edges_ignore_picture_border():
    an = rel.RelationshipAnalyzer()
    an.frame_w, an.frame_h = 960, 720
    old = rel.DESK_OUTLINE

    def alerts(outline, bbox):
        rel.DESK_OUTLINE = outline
        res = {'risk_events': [], 'explanations': [], 'relationships': []}
        an._edge_proximity({'cup': {'class_name': 'cup', 'bbox': bbox}}, res)
        return [e['edge'] for e in res['risk_events']]

    try:
        desk = [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]       # the desk runs off the left, top and right
        assert alerts([], [0, 300, 60, 400]) == []        # nothing marked: no edge alerts
        assert alerts(desk, [0, 300, 60, 400]) == []      # at the side of the picture: fine
        assert alerts(desk, [400, 480, 470, 570]) == ['front']   # near the real front edge
        assert alerts(desk, [400, 200, 470, 300]) == []   # middle of the desk
    finally:
        rel.DESK_OUTLINE = old


def det(label, box):
    x1, y1, x2, y2 = box
    return {'class_name': label, 'bbox': list(box), 'center': [(x1 + x2) / 2, (y1 + y2) / 2], 'conf': 0.9}


def test_cup_in_the_laptop_zone_is_an_alert():
    an = rel.RelationshipAnalyzer()
    res = an.analyze({'computer': det('laptop', (200, 200, 400, 350)), 'cup': det('cup', (410, 250, 450, 320))})
    zi = [e for e in res['risk_events'] if e['type'] == 'zone_intrusion']
    assert zi and zi[0]['intruder'] == 'cup' and zi[0]['target'] == 'computer'


def test_a_book_near_the_laptop_is_fine():
    an = rel.RelationshipAnalyzer()
    res = an.analyze({'computer': det('laptop', (200, 200, 400, 350)), 'paper': det('book', (410, 250, 450, 320))})
    assert not [e for e in res['risk_events'] if e['type'] == 'zone_intrusion']


def test_new_drink_words_count_as_liquids():
    rel.set_categories(['cup', 'mug', 'water bottle'], ['apple'], ['scissors'])
    try:
        assert rel.is_meaningful_zone_intrusion('laptop', 'mug')
        assert rel.is_meaningful_zone_intrusion('laptop', 'apple')
        assert rel.is_meaningful_zone_intrusion('cell phone', 'scissors')
        assert not rel.is_meaningful_zone_intrusion('laptop', 'pen')
    finally:
        rel.set_categories(['cup', 'bottle', 'wine glass'], ['banana', 'apple'], ['scissors', 'knife'])


def bright_frame():
    f = np.full((480, 640, 3), 140, np.uint8)
    f[::8] = 30                                      # some texture so it isn't "blurry"
    return f


def test_lens_notices_dark_and_says_thanks():
    bright = bright_frame()
    dark = (bright * 0.15).astype(np.uint8)
    h = ch.CameraHealth({'cup'})
    said = []
    for t in range(0, 60):
        frame = dark if 20 <= t < 40 else bright
        h.sample(frame, [('cup', [100, 100, 200, 200])], now=1000 + t)
        said += [m['key'] + ':' + m['kind'] for m in h.check(1000 + t, connected=True, fps=3)]
    assert said == ['dark:problem', 'dark:fixed'], said


def test_lens_speaks_for_the_pi_when_pictures_stop():
    h = ch.CameraHealth(ignore_classes={'person'}, source_kind='pi')
    h.sample(bright_frame(), [('cup', [100, 100, 200, 200])], now=1000)
    h.check(1000, connected=True, fps=3)
    said = []
    for t in range(1, 40):
        said += h.check(1000 + t, connected=False)
    assert said and said[0]['key'] == 'lost' and 'Raspberry Pi' in said[0]['text']
    assert h.issues()[0]['fix'].startswith('Check the Pi')


def test_three_pictures_a_second_is_not_slow():
    h = ch.CameraHealth(source_kind='pi')
    said = []
    for t in range(0, 60):
        h.sample(bright_frame(), [('cup', [100, 100, 200, 200])], now=1000 + t)
        said += [m['key'] for m in h.check(1000 + t, connected=True, fps=2.9)]
    assert 'slow' not in said


def test_cut_off_uses_the_ignore_list():
    h = ch.CameraHealth(ignore_classes={'person', 'chair'})
    assert h._watched('cup') and not h._watched('chair') and not h._watched('person')
