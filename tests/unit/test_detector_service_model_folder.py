"""The detector service's models folder: opens models, converting .pt to ncnn the first time."""

from __future__ import annotations

import os

import pytest

from desktalk_detector.model_folder import ModelFolder, ultralytics_yolo
from desktalk_shared.detector_api import ModelSettings
from tests.unit.detector_service_fakes import FakeYolo


def settings(**change) -> ModelSettings:
    values = {'model': 'yolo11n.pt', 'format': 'ncnn', 'image_size': 480, 'confidence': 0.35, 'half': True}
    return ModelSettings(**{**values, **change})


@pytest.fixture
def yolo():
    return FakeYolo([('cup', 0.9, (1, 1, 5, 5))])


@pytest.fixture
def folder(tmp_path, yolo):
    (tmp_path / 'yolo11n.pt').write_bytes(b'weights')
    return ModelFolder(tmp_path, yolo)


def test_pt_format_opens_the_pt_file_in_the_models_folder(folder, yolo, tmp_path):
    model = folder.open(settings(format='pt', half=False))
    assert model.path == tmp_path / 'yolo11n.pt'
    assert yolo.exports == []


def test_only_the_file_name_of_the_model_counts(folder, tmp_path):
    model = folder.open(settings(model='models/yolo11n.pt', format='pt', half=False))
    assert model.path == tmp_path / 'yolo11n.pt'


def test_ncnn_converts_the_pt_once_and_opens_the_ncnn_folder(folder, yolo, tmp_path):
    first = folder.open(settings())
    second = folder.open(settings())
    ncnn = tmp_path / 'yolo11n_480_fp16_ncnn_model'
    assert yolo.exports == [{'model': tmp_path / 'yolo11n.pt', 'format': 'ncnn', 'imgsz': 480, 'half': True,
                             'device': 'cpu'}]
    assert first.path == second.path == ncnn
    assert yolo.opened[-1] == (ncnn, 'detect')
    assert (ncnn / 'model.ncnn.param').is_file()


def test_each_picture_size_and_precision_gets_its_own_ncnn_folder(folder, yolo, tmp_path):
    a = folder.open(settings(image_size=640, half=False))
    b = folder.open(settings(image_size=480, half=True))
    assert (a.path.name, b.path.name) == ('yolo11n_640_fp32_ncnn_model', 'yolo11n_480_fp16_ncnn_model')
    assert [e['imgsz'] for e in yolo.exports] == [640, 480]


def test_an_ncnn_folder_that_is_already_there_needs_no_pt(tmp_path, yolo):
    (tmp_path / 'yolo11n_480_fp16_ncnn_model').mkdir()
    model = ModelFolder(tmp_path, yolo).open(settings())
    assert model.path == tmp_path / 'yolo11n_480_fp16_ncnn_model'
    assert yolo.exports == []


def test_a_failed_conversion_leaves_no_ncnn_folder_to_be_mistaken_for_a_good_one(folder, yolo, tmp_path):
    yolo.fail_export = 'pnnx is not installed'
    with pytest.raises(RuntimeError, match='pnnx'):
        folder.open(settings())
    assert not (tmp_path / 'yolo11n_480_fp16_ncnn_model').exists()
    yolo.fail_export = None
    assert folder.open(settings()).path == tmp_path / 'yolo11n_480_fp16_ncnn_model'
    assert len(yolo.exports) == 2


def test_a_half_moved_folder_from_an_earlier_crash_does_not_spoil_the_next_conversion(folder, tmp_path):
    leftover = tmp_path / 'yolo11n_480_fp16_ncnn_model.tmp'
    leftover.mkdir()
    (leftover / 'model.ncnn.bin').write_text('half written')
    model = folder.open(settings())
    assert (model.path / 'model.ncnn.bin').read_text() == 'model.ncnn.bin for yolo11n.pt at 480'


@pytest.mark.parametrize('name', ['yolo11n', 'yolo11n.onnx', 'yolo11n_ncnn_model'])
def test_check_wants_a_pt_file_name(folder, name):
    with pytest.raises(ValueError, match=r'\.pt'):
        folder.check(settings(model=name))


def test_remembers_the_last_model_it_was_asked_for(tmp_path, yolo):
    ModelFolder(tmp_path, yolo).remember(settings(image_size=320))
    assert ModelFolder(tmp_path, yolo).remembered() == settings(image_size=320)


def test_remembers_nothing_at_first(folder):
    assert folder.remembered() is None


def test_a_broken_memory_file_is_ignored(tmp_path, yolo):
    folder = ModelFolder(tmp_path, yolo)
    folder.remember(settings())
    for path in tmp_path.glob('*.json'):
        path.write_text('{"model": ')
    assert folder.remembered() is None


def test_the_real_yolo_never_pip_installs_anything(monkeypatch, tmp_path):
    monkeypatch.setenv('YOLO_AUTOINSTALL', 'true')
    broken = tmp_path / 'broken.pt'
    broken.write_bytes(b'not a model')                 # fails to load without any download or install
    with pytest.raises(Exception):  # noqa: B017 - whatever Ultralytics raises for a broken file
        ultralytics_yolo(str(broken))
    assert os.environ['YOLO_AUTOINSTALL'] == 'false'
