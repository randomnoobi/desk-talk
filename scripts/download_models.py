#!/usr/bin/env python3
"""
Download YOLO model files into models/.

    uv run python scripts/download_models.py            # the model in config/settings.yaml (detector.model)
    uv run python scripts/download_models.py yolo26l-objv1-150.pt
    uv run python scripts/download_models.py --list     # every object the model can detect
    uv run python scripts/download_models.py --test     # time it on a sample picture

Recommended models (Ultralytics YOLO26):
    yolo26m-objv1-150.pt   365 kinds of objects (Objects365): earphones, keys, pens, notepaper... (default)
    yolo26s-objv1-150.pt   same objects, faster, a bit less accurate
    yolo26l-objv1-150.pt   same objects, slower, most accurate
    yolo26m.pt             80 everyday objects (COCO)
    yolo26n.pt             the small one Desk Talk used before
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MB = 1024 * 1024        # bytes, as everywhere else in Desk Talk


def model_path(config, name: str) -> Path:
    """A bare file name goes next to the model in the settings (models/); a path is used as written."""
    if Path(name).parent == Path('.'):
        return config.resolve(config.detector.model).parent / name
    return config.resolve(name)


def main():
    import cv2

    from desktalk.adapters.detector import UltralyticsDetector, load_yolo
    from desktalk.config import load_config

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('models', nargs='*', help='model file names (default: detector.model from the settings)')
    p.add_argument('--list', action='store_true', help='print the objects the model knows')
    p.add_argument('--test', action='store_true', help='run the model once on a sample picture')
    a = p.parse_args()
    config = load_config()
    for m in a.models or [config.detector.model]:
        path = model_path(config, m)
        print(f'→ {m}')
        model = load_yolo(path)                      # downloads it the first time
        path = Path(getattr(model, 'ckpt_path', None) or path)
        print(f'  ready: {path} ({path.stat().st_size / MB:.0f} MB)')
        if a.list:
            names = sorted(model.names.values())
            print(f'  {len(names)} objects: ' + ', '.join(names))
        if a.test:
            from ultralytics.utils import ASSETS
            s = config.detector
            detector = UltralyticsDetector(path, s, end2end=s.end2end, rect=s.rect, load_model=lambda _, m=model: m)
            picture = cv2.imread(str(ASSETS / 'bus.jpg'))
            detector.warmup()
            t = time.perf_counter()
            found = sorted({d.label for d in detector.detect(picture)})
            ms = (time.perf_counter() - t) * 1000
            budget_ms = 1000 / config.camera.fps
            print(f'  test picture with {detector.label}: {ms:.0f} ms, found {", ".join(found)}')
            print(f'  at {config.camera.fps:g} pictures a second it needs under {budget_ms:.0f} ms: '
                  f'{"OK" if ms < budget_ms else "too slow, try a smaller model"}')


if __name__ == '__main__':
    main()
