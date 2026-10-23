"""
Getting each combination's model file: download the .pt, convert it with
Ultralytics (ncnn, onnx), or shrink the fp32 ONNX model to int8 with onnxruntime.

Each file is made under a temporary name and renamed at the end, so a crash
halfway never leaves a broken model that looks finished.

yoloe models get the desk words built in first (see words.py); that and their
conversions each run in a separate process.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from . import words as desk_words
from .matrix import Combo


def size_mb(path: Path) -> float:
    files = [path] if path.is_file() else [p for p in path.rglob('*') if p.is_file()]
    return sum(p.stat().st_size for p in files) / (1024 * 1024)


def ensure_model(combo: Combo, models_dir: Path, log: Callable[[str], None] = print,
                 words: list[str] | None = None) -> Path:
    """`words`: build these into a yoloe model (its .pt becomes <model>-desk.pt)."""
    if words and combo.fmt == 'pt':
        return _words_model(combo, models_dir, log, words)
    target = combo.model_path(models_dir)
    if target.exists():
        return target
    models_dir.mkdir(parents=True, exist_ok=True)

    if combo.fmt == 'pt':
        from ultralytics.utils.downloads import attempt_download_asset
        log(f'  downloading {target.name}')
        got = Path(attempt_download_asset(str(target)))
        if not got.is_file():
            raise FileNotFoundError(f'could not download {target.name}')
        return got

    if combo.fmt == 'onnx' and combo.precision == 'int8':
        fp32 = ensure_model(replace(combo, precision='fp32'), models_dir, log, words)
        log(f'  making {target.name} (8-bit) from {fp32.name}')
        return _quantize_int8(fp32, target)

    pt = ensure_model(Combo(combo.model, combo.image_size, 'pt', 'fp32'), models_dir, log, words)
    log(f'  converting {pt.name} to {combo.fmt} {combo.precision} at {combo.image_size} px')
    if words:
        _in_own_process(['export', str(pt), combo.fmt, str(combo.image_size),
                         '1' if combo.precision == 'fp16' else '0', str(target)], models_dir)
        return target
    from ultralytics import YOLO
    exported = Path(YOLO(str(pt)).export(format=combo.fmt, imgsz=combo.image_size,
                                         half=combo.precision == 'fp16', device='cpu', verbose=False))
    tmp = target.with_name(target.name + '.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.move(str(exported), str(tmp))
    tmp.rename(target)
    return target


def _words_model(combo: Combo, models_dir: Path, log: Callable[[str], None], words: list[str]) -> Path:
    target = desk_words.words_model_path(models_dir, combo.model)
    if target.exists():
        return target
    base = ensure_model(Combo(combo.model, combo.image_size, 'pt', 'fp32'), models_dir, log)
    log(f'  building {len(words)} words into {base.name}')
    words_file = desk_words.stamp_path(models_dir, combo.model).with_suffix('.tmp.json')
    words_file.write_text(json.dumps(words))
    try:
        _in_own_process(['make', str(base), str(words_file), str(target)], models_dir)
    finally:
        words_file.unlink(missing_ok=True)
    desk_words.stamp_path(models_dir, combo.model).write_text(json.dumps(words))
    return target


def _in_own_process(args: list[str], models_dir: Path) -> None:
    # In the models folder, so the text model yoloe downloads for its words lands there too
    proc = subprocess.run([sys.executable, '-m', 'deskbench.words', *args], cwd=models_dir,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:] or f'exit code {proc.returncode}')


def _quantize_int8(fp32: Path, target: Path) -> Path:
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    tmp = target.with_suffix('.tmp.onnx')
    quantize_dynamic(str(fp32), str(tmp), weight_type=QuantType.QUInt8)
    # Ultralytics reads the class names and picture size from the model's metadata; the quantizer drops it.
    quantized, original = onnx.load(str(tmp)), onnx.load(str(fp32))
    del quantized.metadata_props[:]
    quantized.metadata_props.extend(original.metadata_props)
    onnx.save(quantized, str(tmp))
    tmp.replace(target)
    return target
