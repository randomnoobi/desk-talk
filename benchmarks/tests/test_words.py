import json

import pytest

from deskbench.words import clear_if_changed, desk_words, stamp_path, words_model_path

M = 'yoloe-26n-seg'


def test_desk_words_keep_order_and_drop_repeats():
    cfg = {'kinds': {'phone': {'words': ['cell phone']}, 'trash': {'words': ['empty can', 'Cell Phone']}},
           'yoloe_extra_words': ['person', 'empty can']}
    assert desk_words(cfg) == ['cell phone', 'empty can', 'person']


def test_words_model_is_named_after_the_base_model(tmp_path):
    assert words_model_path(tmp_path, M) == tmp_path / 'yoloe-26n-seg-desk.pt'


def test_changed_words_remove_old_yoloe_files(tmp_path):
    (tmp_path / f'{M}-desk.pt').write_text('x')
    (tmp_path / f'{M}_320_fp16_ncnn_model').mkdir()
    (tmp_path / f'{M}_480_fp32.onnx').write_text('x')
    (tmp_path / f'{M}.pt').write_text('x')                         # the downloaded base model: kept
    (tmp_path / 'yolo11n_320_fp16_ncnn_model').mkdir()              # another model: left alone
    stamp_path(tmp_path, M).write_text(json.dumps(['old']))
    assert clear_if_changed(tmp_path, M, ['new']) is True
    assert not (tmp_path / f'{M}-desk.pt').exists()
    assert not (tmp_path / f'{M}_320_fp16_ncnn_model').exists()
    assert not (tmp_path / f'{M}_480_fp32.onnx').exists()
    assert not stamp_path(tmp_path, M).exists()
    assert (tmp_path / f'{M}.pt').exists()
    assert (tmp_path / 'yolo11n_320_fp16_ncnn_model').exists()


def test_same_words_keep_files(tmp_path):
    (tmp_path / f'{M}-desk.pt').write_text('x')
    stamp_path(tmp_path, M).write_text(json.dumps(['a', 'b']))
    assert clear_if_changed(tmp_path, M, ['a', 'b']) is False
    assert (tmp_path / f'{M}-desk.pt').exists()


def test_files_without_a_stamp_are_made_again(tmp_path):
    (tmp_path / f'{M}-desk.pt').write_text('x')                     # made before stamps, or a crash
    assert clear_if_changed(tmp_path, M, ['a']) is True
    assert not (tmp_path / f'{M}-desk.pt').exists()


def test_without_clip_changed_words_stop_instead_of_deleting(tmp_path):
    (tmp_path / f'{M}-desk.pt').write_text('x')                     # copied from the Mac
    (tmp_path / f'{M}_320_fp16_ncnn_model').mkdir()
    stamp_path(tmp_path, M).write_text(json.dumps(['old']))
    with pytest.raises(RuntimeError, match='other words'):
        clear_if_changed(tmp_path, M, ['new'], can_make=False)
    assert (tmp_path / f'{M}-desk.pt').exists() and (tmp_path / f'{M}_320_fp16_ncnn_model').exists()


def test_without_clip_same_words_are_fine(tmp_path):
    (tmp_path / f'{M}-desk.pt').write_text('x')
    stamp_path(tmp_path, M).write_text(json.dumps(['a']))
    assert clear_if_changed(tmp_path, M, ['a'], can_make=False) is False
