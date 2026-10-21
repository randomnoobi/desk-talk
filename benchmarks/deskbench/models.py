"""
Getting each combination's model file: download the .pt, convert it with
Ultralytics (ncnn, onnx), or shrink the fp32 ONNX model to int8 with onnxruntime.

Each file is made under a temporary name and renamed at the end, so a crash
halfway never leaves a broken model that looks finished.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .matrix import Combo


def size_mb(path: Path) -> float:
    files = [path] if path.is_file() else [p for p in path.rglob('*') if p.is_file()]
    return sum(p.stat().st_size for p in files) / (1024 * 1024)


def ensure_model(combo: Combo, models_dir: Path, log: Callable[[str], None] = print) -> Path:
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
        fp32 = ensure_model(replace(combo, precision='fp32'), models_dir, log)
        log(f'  making {target.name} (8-bit) from {fp32.name}')
        return _quantize_int8(fp32, target)

    pt = ensure_model(Combo(combo.model, combo.image_size, 'pt', 'fp32'), models_dir, log)
    log(f'  converting {pt.name} to {combo.fmt} {combo.precision} at {combo.image_size} px')
    from ultralytics import YOLO
    exported = Path(YOLO(str(pt)).export(format=combo.fmt, imgsz=combo.image_size,
                                         half=combo.precision == 'fp16', device='cpu', verbose=False))
    tmp = target.with_name(target.name + '.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.move(str(exported), str(tmp))
    tmp.rename(target)
    return target


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
