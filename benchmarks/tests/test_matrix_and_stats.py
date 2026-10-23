from pathlib import Path

import pytest

from deskbench.matrix import Combo, build_combos, kind_of
from deskbench.stats import percentile, summarize

CFG = {'image_sizes': [320, 640], 'model_kinds': {
    'a': {'models': ['yolo11n', 'yolo11s'], 'names': 'coco',
          'formats': {'pt': ['fp32'], 'ncnn': ['fp32', 'fp16'], 'onnx': ['fp32', 'int8']}},
    'b': {'models': ['yoloe-26n-seg'], 'names': 'words', 'task': 'segment', 'formats': {'ncnn': ['fp16']}}}}


def test_every_combination_is_listed_once_in_order():
    combos = build_combos(CFG)
    assert len(combos) == 2 * 2 * 5 + 1 * 2 * 1
    assert combos[0] == Combo('yolo11n', 320, 'pt', 'fp32')
    assert combos[-1] == Combo('yoloe-26n-seg', 640, 'ncnn', 'fp16')
    assert len({c.key for c in combos}) == len(combos)


def test_only_some_models():
    combos = build_combos(CFG, only_models=['yolo11s'])
    assert {c.model for c in combos} == {'yolo11s'}


def test_each_kind_gets_only_its_own_formats():
    assert {c.fmt for c in build_combos(CFG, only_kinds=['b'])} == {'ncnn'}


def test_kind_of_a_model():
    assert kind_of(CFG, 'yoloe-26n-seg')[0] == 'b'
    with pytest.raises(KeyError):
        kind_of(CFG, 'yolo99x')


@pytest.mark.parametrize('formats', [{'pt': ['fp16']}, {'onnx': ['fp16']}, {'ncnn': ['int8']}, {'tflite': ['fp32']}])
def test_precision_that_a_format_cannot_do_is_refused(formats):
    kinds = {'a': {**CFG['model_kinds']['a'], 'formats': formats}}
    with pytest.raises(ValueError):
        build_combos({**CFG, 'model_kinds': kinds})


def test_unknown_names_are_refused():
    kinds = {'a': {**CFG['model_kinds']['a'], 'names': 'imagenet'}}
    with pytest.raises(ValueError):
        build_combos({**CFG, 'model_kinds': kinds})


def test_model_file_names_say_size_and_precision():
    d = Path('m')
    assert Combo('yolo11n', 320, 'pt', 'fp32').model_path(d) == d / 'yolo11n.pt'
    assert Combo('yolo11n', 320, 'ncnn', 'fp16').model_path(d) == d / 'yolo11n_320_fp16_ncnn_model'
    assert Combo('yolo11n', 480, 'onnx', 'int8').model_path(d) == d / 'yolo11n_480_int8.onnx'


def test_percentile_matches_linear_interpolation():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 50) == pytest.approx(5.5)
    assert percentile(values, 95) == pytest.approx(9.55)
    assert percentile([7], 95) == 7


def test_percentile_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize():
    s = summarize([10, 20, 30])
    assert s == {'mean': 20, 'p50': 20, 'p95': pytest.approx(29), 'min': 10, 'max': 30}
