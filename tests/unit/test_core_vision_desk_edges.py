"""The desk outline you mark in Settings, its real edges, and things close to falling off."""

import dataclasses

import pytest

from desktalk.core.vision import desk_edges as de
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import make_rules
from tests.unit.test_core_vision_kit import app_config, desk_object

SIZE = (960, 720)
# The desk runs off the left, top and right of the picture; its front edge is at 80% of the height.
DESK = ((0, 0), (1, 0), (1, 0.8), (0, 0.8))
RULES = dataclasses.replace(make_rules(app_config().safety), outline=DESK)


def cup(box, label='cup'):
    return {'cup': Thing.from_desk('cup', desk_object(label, box))}


def test_sides_along_the_picture_border_are_not_desk_edges():
    assert de.desk_edges(DESK, SIZE, border_share=0.03) == [((960, 576), (0, 576))]


def test_no_outline_no_edges():
    assert de.desk_edges((), SIZE, border_share=0.03) == []


@pytest.mark.parametrize('seg, name', [
    (((0, 700), (960, 700)), 'front'),
    (((0, 20), (960, 20)), 'back'),
    (((20, 0), (20, 720)), 'left'),
    (((940, 0), (940, 720)), 'right'),
])
def test_edge_names_come_from_where_the_edge_is(seg, name):
    assert de.edge_name(seg, SIZE) == name


def test_point_inside_the_outline():
    assert de.inside_outline((480, 300), DESK, SIZE)
    assert not de.inside_outline((480, 650), DESK, SIZE)


def test_on_the_desk_is_inside_the_outline_or_anywhere_when_none_is_marked():
    assert de.on_desk((480, 300), DESK, SIZE) and not de.on_desk((480, 650), DESK, SIZE)
    assert de.on_desk((480, 650), (), SIZE)


# Ported from test_desk_edges_ignore_picture_border
@pytest.mark.parametrize('outline, box, edges', [
    ((), (0, 300, 60, 400), []),                  # nothing marked: no edge alerts
    (DESK, (0, 300, 60, 400), []),                # at the side of the picture: fine
    (DESK, (400, 480, 470, 570), ['front']),      # near the real front edge
    (DESK, (400, 200, 470, 300), []),             # middle of the desk
])
def test_only_real_desk_edges_count(outline, box, edges):
    found = de.edge_risks(cup(box), dataclasses.replace(RULES, outline=outline), SIZE)
    assert [e['edge'] for e in found.risk_events] == edges


def test_right_at_the_edge_is_a_danger():
    found = de.edge_risks(cup((400, 480, 470, 570)), RULES, SIZE)
    assert found.risk_events == [{'type': 'edge_danger', 'object': 'cup', 'class': 'cup', 'edge': 'front',
                                  'distance_px': 6.0}]
    assert found.explanations == ['cup is right at the front edge of the desk and could fall off.']
    assert found.relationships == [{'type': 'edge_proximity', 'severity': 'danger', 'object': 'cup',
                                    'edge': 'front', 'distance_px': 6.0, 'segment': [[960, 576], [0, 576]]}]


def test_near_the_edge_is_a_warning_without_an_explanation():
    found = de.edge_risks(cup((400, 430, 470, 520)), RULES, SIZE)
    assert found.risk_events == [{'type': 'edge_warning', 'object': 'cup', 'class': 'cup', 'edge': 'front',
                                  'distance_px': 56.0}]
    assert found.explanations == []
    assert found.relationships[0]['severity'] == 'warning'


def test_off_the_desk_and_not_close_is_ignored():
    assert de.edge_risks(cup((400, 610, 470, 700)), RULES, SIZE).risk_events == []


def test_off_the_desk_but_touching_the_edge_is_still_a_danger():
    found = de.edge_risks(cup((400, 560, 470, 590)), RULES, SIZE)
    assert [e['type'] for e in found.risk_events] == ['edge_danger']


def test_only_the_edge_objects_are_checked():
    assert de.edge_risks(cup((400, 480, 470, 570), label='book'), RULES, SIZE).risk_events == []


def test_saved_edge_distances_are_used():
    closer = dataclasses.replace(RULES, edge_danger_px=5, edge_warn_px=10)
    found = de.edge_risks(cup((400, 480, 470, 570)), closer, SIZE)
    assert [e['type'] for e in found.risk_events] == ['edge_warning']
