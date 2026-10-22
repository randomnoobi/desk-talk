from pathlib import Path

import pytest
import yaml

from deskbench.desk_set import answers_for, build_desk_set, choose_pictures, is_desk_picture, read_your_desk, scenes_for

KINDS = {'book': {'coco': ['book'], 'lvis': ['book', 'paperback_book']},
         'phone': {'coco': ['cell phone'], 'lvis': ['cellular_telephone']},
         'mouse': {'coco': ['mouse'], 'lvis': ['mouse_(computer_equipment)']},
         'AirPods': {'coco': [], 'lvis': ['earphone']}}
RULES = {'covered_share': 0.3, 'busy_objects': 6, 'small_share': 0.01, 'small_kinds': ['phone', 'mouse']}
REPO = Path(__file__).resolve().parents[2]


def test_coco_label_makes_it_there():
    assert answers_for(set(), set(), {'book'}, KINDS)['book'] == 'there'


def test_lvis_label_makes_it_there():
    assert answers_for({'paperback_book'}, set(), set(), KINDS)['book'] == 'there'


def test_no_label_anywhere_is_not_there():
    assert answers_for(set(), set(), set(), KINDS)['book'] == 'not there'


def test_not_exhaustive_makes_it_unknown():
    assert answers_for(set(), {'paperback_book'}, set(), KINDS)['book'] == 'unknown'


def test_covering_scene_when_30_percent_of_a_kind_box_is_covered():
    boxes = [('book', [0, 0, 10, 10]), (None, [7, 0, 10, 10])]          # 30% of the book is covered
    assert 'covering' in scenes_for({'book': 'there'}, boxes, 100, 100, 2, RULES)


def test_no_covering_when_less_is_covered():
    boxes = [('book', [0, 0, 10, 10]), (None, [8, 0, 10, 10])]          # 20%
    assert 'covering' not in scenes_for({'book': 'there'}, boxes, 100, 100, 2, RULES)


def test_tidy_when_no_other_scene():
    assert scenes_for({'book': 'there'}, [('book', [0, 0, 50, 50])], 100, 100, 1, RULES) == ['tidy']


def test_busy_with_six_objects():
    assert 'busy' in scenes_for({'book': 'there'}, [], 100, 100, 6, RULES)


def test_small_things_when_phone_box_is_under_1_percent():
    scenes = scenes_for({'phone': 'there'}, [('phone', [0, 0, 9, 9])], 100, 100, 1, RULES)   # 81 of 10,000
    assert scenes == ['small things']


def test_small_things_when_airpods_are_there():
    assert 'small things' in scenes_for({'AirPods': 'there'}, [], 100, 100, 1, RULES)


def test_desk_picture_needs_two_kinds_and_an_anchor():
    anchors = ['phone', 'mouse']
    assert is_desk_picture({'phone': 'there', 'book': 'there'}, anchors)
    assert not is_desk_picture({'phone': 'there', 'book': 'not there'}, anchors)
    assert not is_desk_picture({'book': 'there', 'AirPods': 'unknown', 'food': 'there'}, anchors)


def test_choose_keeps_rare_and_is_repeatable():
    cands = [{'id': i, 'answers': {'AirPods': 'there' if i in (3, 8) else 'not there'}} for i in range(10)]
    first = choose_pictures(cands, 4, ['AirPods'], seed=7)
    assert len(first) == 4 and {3, 8} <= {p['id'] for p in first}
    assert first == choose_pictures(list(reversed(cands)), 4, ['AirPods'], seed=7)
    assert [p['id'] for p in first] == sorted(p['id'] for p in first)


def test_choose_takes_all_when_there_are_few():
    cands = [{'id': i, 'answers': {}} for i in range(3)]
    assert len(choose_pictures(cands, 300, ['AirPods'], seed=7)) == 3


def fake_lvis():
    def image(i, split):
        return {'id': i, 'coco_url': f'http://images.cocodataset.org/{split}/{i:012d}.jpg', 'width': 100,
                'height': 100, 'neg_category_ids': [], 'not_exhaustive_category_ids': []}
    return {'categories': [{'id': 1, 'name': 'cellular_telephone'}, {'id': 2, 'name': 'book'}],
            'images': [image(5, 'val2017'), image(6, 'train2017')],
            'annotations': [{'image_id': i, 'category_id': c, 'bbox': [0, 0, 40, 40]}
                            for i in (5, 6) for c in (1, 2)]}


def test_build_desk_set_uses_only_val2017():
    cfg = {'kinds': KINDS, 'anchor_kinds': ['phone'], 'rare_kinds': [], 'max_pictures': 10, 'seed': 7,
           'scenes': RULES}
    pictures = build_desk_set(fake_lvis(), {5: [('mouse', [50, 50, 20, 20])]}, cfg)
    assert [p['id'] for p in pictures] == [5]
    p = pictures[0]
    assert p['url'].endswith('000000000005.jpg') and p['width'] == 100
    assert p['answers'] == {'book': 'there', 'phone': 'there', 'mouse': 'there', 'AirPods': 'not there'}


def write_labels(folder, labels):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'labels.yaml').write_text(yaml.safe_dump(labels))


def test_your_desk_labels_unlisted_kinds_are_not_there(tmp_path):
    write_labels(tmp_path, {'desk_01.jpg': ['phone']})
    [p] = read_your_desk(tmp_path, list(KINDS))
    assert p['id'] == 'desk_01.jpg' and p['path'] == tmp_path / 'desk_01.jpg'
    assert p['answers'] == {'book': 'not there', 'phone': 'there', 'mouse': 'not there', 'AirPods': 'not there'}
    assert p['scenes'] == ['your desk']


def test_your_desk_labels_missing_file_gives_nothing(tmp_path):
    assert read_your_desk(tmp_path, list(KINDS)) == []


def test_your_desk_labels_unknown_kind_is_an_error(tmp_path):
    write_labels(tmp_path, {'desk_02.jpg': ['phone', 'cup']})
    with pytest.raises(ValueError, match='desk_02.jpg.*cup'):
        read_your_desk(tmp_path, list(KINDS))


def test_words_match_the_apps_names():
    """A yoloe pick only works in the app as it is if its words are the app's own names."""
    desk = yaml.safe_load((REPO / 'config/benchmark.yaml').read_text())['desk']
    agents = yaml.safe_load((REPO / 'config/agents.yaml').read_text())['agents']
    for name, kind in desk['kinds'].items():
        if kind.get('app_agent'):
            names = kind['app_agent'] if isinstance(kind['app_agent'], list) else [kind['app_agent']]
            labels = {label for a in names for label in agents[a]['labels']}
            assert set(kind['words']) <= labels, f'{name}: {set(kind["words"]) - labels}'


def test_lying_on_a_desk_is_not_covering():
    lvis = fake_lvis()
    lvis['categories'].append({'id': 3, 'name': 'desk'})
    lvis['annotations'] = [{'image_id': 5, 'category_id': 1, 'bbox': [10, 10, 10, 10]},     # phone
                           {'image_id': 5, 'category_id': 2, 'bbox': [60, 60, 10, 10]},     # book, apart
                           {'image_id': 5, 'category_id': 3, 'bbox': [0, 0, 100, 100]}]     # the desk under both
    cfg = {'kinds': KINDS, 'anchor_kinds': ['phone'], 'rare_kinds': [], 'max_pictures': 10, 'seed': 7,
           'scenes': {**RULES, 'surfaces': ['desk']}}
    [p] = build_desk_set(lvis, {}, cfg)
    assert 'covering' not in p['scenes']


def test_photos_are_fetched_with_the_configured_tries(tmp_path):
    from deskbench.desk_data import download_photos
    (tmp_path / 'src.jpg').write_bytes(b'photo')
    pictures = [{'id': 1, 'url': (tmp_path / 'src.jpg').as_uri()}, {'id': 2, 'url': (tmp_path / 'gone.jpg').as_uri()}]
    failed = download_photos(pictures, tmp_path / 'out', {'timeout_sec': 5, 'tries': 1}, log=lambda m: None)
    assert failed == 1 and (tmp_path / 'out' / '1.jpg').read_bytes() == b'photo'
