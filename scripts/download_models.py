#!/usr/bin/env python3
"""
Download YOLO model files into models/.

    python scripts/download_models.py            # the model in config/settings.yaml (detector.model)
    python scripts/download_models.py yolo26l-objv1-150.pt
    python scripts/download_models.py --list     # every object the model can detect
    python scripts/download_models.py --test     # time it on a sample picture

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


def main():
    from desktalk.settings import load_settings
    from desktalk.vision.detector import ensure_model_file, pick_device

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('models', nargs='*', help='model file names (default: detector.model from the settings)')
    p.add_argument('--list', action='store_true', help='print the objects the model knows')
    p.add_argument('--test', action='store_true', help='run the model once on a sample picture')
    a = p.parse_args()
    settings = load_settings()
    models = a.models or [settings.detector.model]
    for m in models:
        print(f'→ {m}')
        path = ensure_model_file(m)
        print(f'  ready: {path} ({path.stat().st_size / 1e6:.0f} MB)')
        if a.list or a.test:
            from ultralytics import YOLO
            model = YOLO(str(path))
            if a.list:
                names = sorted(model.names.values())
                print(f'  {len(names)} objects: ' + ', '.join(names))
            if a.test:
                from ultralytics.utils import ASSETS
                img = str(ASSETS / 'bus.jpg')
                dev = pick_device(settings.detector.get('device', 'auto'))
                model.predict(img, verbose=False, device=dev)
                t = time.time()
                r = model.predict(img, verbose=False, device=dev, conf=0.35)[0]
                ms = (time.time() - t) * 1000
                found = sorted({r.names[int(c)] for c in r.boxes.cls})
                print(f'  test picture on {dev}: {ms:.0f} ms, found {", ".join(found)}')
                print(f'  at 3 pictures a second it needs under 333 ms: {"OK" if ms < 333 else "too slow, try a smaller model"}')


if __name__ == '__main__':
    main()
