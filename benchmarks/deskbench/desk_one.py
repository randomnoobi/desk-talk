"""
Runs one combination over the desk pictures and writes down what it found. Runs in its
own process (python -m deskbench.desk_one spec.json out.json), like run_one; the
scoring happens afterwards, in desk_score.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run(spec: dict) -> dict:
    from ultralytics import YOLO

    model = YOLO(spec['model_path'], task=spec['task'])
    detections = {}
    for pid, path in spec['pictures']:
        r = model.predict(path, imgsz=spec['image_size'], conf=spec['floor'], device=spec['device'], verbose=False)[0]
        boxes = zip(r.boxes.cls, r.boxes.conf, strict=True)
        detections[pid] = [[r.names[int(c)], round(float(p), 4)] for c, p in boxes]
    return {'detections': detections, 'names': list(model.names.values())}


def main(argv: list[str]) -> None:
    spec = json.loads(Path(argv[1]).read_text())
    Path(argv[2]).write_text(json.dumps(run(spec)))


if __name__ == '__main__':
    main(sys.argv)
