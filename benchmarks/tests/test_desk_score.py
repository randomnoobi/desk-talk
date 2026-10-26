import pytest
import yaml

from deskbench.desk_score import (
    app_one_name,
    app_thresholds,
    found_kinds,
    missing_names,
    name_map,
    one_name_each,
    scene_scores,
    score,
)

KINDS = {'phone': {'coco': ['cell phone'], 'objects365': ['Cell Phone'], 'words': ['cell phone']},
         'AirPods': {'coco': [], 'objects365': ['earphone'], 'words': ['earphone']}}


def pics(*answers, scenes=('tidy',)):
    return [{'id': i, 'answers': a, 'scenes': list(scenes)} for i, a in enumerate(answers)]


def test_kind_score_is_found_rate_times_one_minus_false_alarms():
    p = pics({'phone': 'there'}, {'phone': 'there'}, {'phone': 'not there'}, {'phone': 'not there'})
    s = score(p, {0: {'phone'}, 1: set(), 2: {'phone'}, 3: set()}, ['phone'], {'phone'})
    k = s['kinds']['phone']
    assert (k['there'], k['not_there'], k['found_rate'], k['false_alarms']) == (2, 2, 0.5, 0.5)
    assert k['score'] == pytest.approx(0.25)
    assert s['desk_score'] == pytest.approx(0.25)


def test_unknown_is_never_scored():
    p = pics({'phone': 'there'}, {'phone': 'unknown'}, {'phone': 'not there'})
    s = score(p, {0: {'phone'}, 1: {'phone'}, 2: set()}, ['phone'], {'phone'})
    assert s['kinds']['phone']['score'] == 1.0


def test_kind_the_model_cannot_name_scores_zero():
    p = pics({'phone': 'there', 'AirPods': 'there'}, {'phone': 'not there', 'AirPods': 'not there'})
    s = score(p, {0: {'phone'}, 1: set()}, ['phone', 'AirPods'], {'phone'})
    assert s['kinds']['AirPods']['score'] == 0
    assert s['desk_score'] == pytest.approx(0.5)


def test_kind_with_no_there_pictures_is_left_out_of_the_average():
    p = pics({'phone': 'there', 'AirPods': 'not there'}, {'phone': 'not there', 'AirPods': 'not there'})
    s = score(p, {0: {'phone'}, 1: set()}, ['phone', 'AirPods'], {'phone'})
    assert s['kinds']['AirPods']['score'] is None
    assert s['desk_score'] == 1.0


def test_no_not_there_pictures_scores_found_rate_alone():
    p = pics({'phone': 'there'}, {'phone': 'there'})
    s = score(p, {0: {'phone'}, 1: set()}, ['phone'], {'phone'})
    assert s['kinds']['phone']['false_alarms'] is None
    assert s['kinds']['phone']['score'] == 0.5


def test_no_pictures_at_all_gives_no_desk_score():
    assert score([], {}, ['phone'], {'phone'})['desk_score'] is None


def test_names_match_ignoring_case():
    assert found_kinds([('Cell Phone', 0.9)], name_map(KINDS, 'objects365'), 0.45) == {'phone'}
    assert found_kinds([('CELL PHONE', 0.9)], name_map(KINDS, 'coco'), 0.45) == {'phone'}


def test_threshold_per_kind():
    names = {'bottle': 'water bottle'}
    assert found_kinds([('bottle', 0.5)], names, {'water bottle': 0.55}) == set()
    assert found_kinds([('bottle', 0.6)], names, {'water bottle': 0.55}) == {'water bottle'}


def test_names_of_other_things_are_ignored():
    assert found_kinds([('person', 0.99)], name_map(KINDS, 'coco'), 0.45) == set()


def test_name_map_leaves_out_kinds_without_names():
    assert name_map(KINDS, 'coco') == {'cell phone': 'phone'}


STEPS = {'agents': {'sensitivity_steps': [{'name': 'Picky', 'min_confidence': 0.65},
                                          {'name': 'Normal', 'min_confidence': 0.45}], 'sensitivity_default': 1}}


def test_app_thresholds_use_agent_override(tmp_path):
    """The app's own rule: the agent's min_confidence, else the sensitivity step used until the slider is moved."""
    s, a = tmp_path / 'settings.yaml', tmp_path / 'agents.yaml'
    s.write_text(yaml.safe_dump(STEPS))
    a.write_text(yaml.safe_dump({'agents': {'cup': {'labels': ['bottle'], 'min_confidence': 0.55},
                                            'phone': {'labels': ['cell phone']}}}))
    kinds = {'water bottle': {'app_agent': 'cup'}, 'phone': {'app_agent': 'phone'}, 'trash': {}}
    assert app_thresholds(s, a, kinds) == {'water bottle': 0.55, 'phone': 0.45, 'trash': 0.45}


def test_scene_scores_need_enough_pictures():
    busy = pics(*[{'phone': 'there'}] * 5, scenes=('busy',))
    tidy = [{**p, 'id': 10 + i} for i, p in enumerate(pics(*[{'phone': 'there'}] * 4, scenes=('tidy',)))]
    found = {p['id']: {'phone'} for p in busy + tidy}
    s = scene_scores(busy + tidy, found, ['phone'], {'phone'}, min_photos=5)
    assert s['busy'] == 1.0
    assert s['tidy'] is None


def test_app_thresholds_with_two_agents_use_the_first_override(tmp_path):
    s, a = tmp_path / 'settings.yaml', tmp_path / 'agents.yaml'
    s.write_text(yaml.safe_dump(STEPS))
    a.write_text(yaml.safe_dump({'agents': {'earbuds': {'labels': ['earbuds']},
                                            'headphones': {'labels': ['headphones'], 'min_confidence': 0.6}}}))
    assert app_thresholds(s, a, {'AirPods': {'app_agent': ['earbuds', 'headphones']}}) == {'AirPods': 0.6}


def test_names_the_model_does_not_have_are_listed():
    kinds = {'food': {'objects365': ['Orange/Tangerine', 'Bread']}, 'phone': {'objects365': ['Cell Phone']}}
    assert missing_names(kinds, 'objects365', ['bread', 'cell phone', 'orange']) == ['orange/tangerine']


def test_app_thresholds_keep_the_step_when_the_agents_own_is_laxer(tmp_path):
    """The app counts an agent's own min_confidence only when it is stricter than the step."""
    s, a = tmp_path / 'settings.yaml', tmp_path / 'agents.yaml'
    s.write_text(yaml.safe_dump(STEPS))
    a.write_text(yaml.safe_dump({'agents': {'cup': {'labels': ['cup'], 'min_confidence': 0.3}}}))
    assert app_thresholds(s, a, {'water bottle': {'app_agent': 'cup'}}) == {'water bottle': 0.45}


def test_boxes_are_read_with_the_names():
    assert found_kinds([['cell phone', 0.9, 0, 0, 10, 10]], name_map(KINDS, 'coco'), 0.45) == {'phone'}


LAPTOP = ['laptop', 0.95, 574, 183, 1094, 713]
BOOK_ON_LAPTOP = ['book', 0.52, 574, 180, 1126, 716]


def test_a_second_name_for_the_same_box_is_left_out_like_in_the_app():
    assert one_name_each([BOOK_ON_LAPTOP, LAPTOP], 0.7, frozenset()) == [LAPTOP]
    assert one_name_each([BOOK_ON_LAPTOP, LAPTOP], 0.99, frozenset()) == [BOOK_ON_LAPTOP, LAPTOP]


def test_ignored_words_take_no_part_and_rows_without_a_box_are_kept():
    frame = ['picture/frame', 0.99, 574, 183, 1094, 713]
    old = ['cup', 0.6]                                   # saved by an older run, before boxes were kept
    assert one_name_each([frame, LAPTOP, old], 0.7, frozenset({'picture/frame'})) == [frame, LAPTOP, old]


def test_app_one_name_reads_the_overlap_and_the_ignore_list(tmp_path):
    s, a = tmp_path / 'settings.yaml', tmp_path / 'agents.yaml'
    s.write_text(yaml.safe_dump({'agents': {**STEPS['agents'], 'same_thing_overlap': 0.7}}))
    a.write_text(yaml.safe_dump({'agents': {}, 'ignore': ['person', 'Picture/Frame']}))
    assert app_one_name(s, a) == (0.7, frozenset({'person', 'picture/frame'}))
