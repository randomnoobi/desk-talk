"""
Times one combination. Runs in its own process (python -m deskbench.run_one spec.json out.json)
so its peak memory and CPU time aren't mixed up with any other combination's.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

from . import probes
from .measure import TempSampler, time_detections


def run(spec: dict) -> dict:
    import cv2
    from ultralytics import YOLO

    pictures = [cv2.imread(p) for p in spec['pictures']]
    pictures = [p for p in pictures if p is not None]
    device = spec['torch_device'] if spec['format'] == 'pt' else 'cpu'
    kwargs = {'imgsz': spec['image_size'], 'conf': spec['confidence'], 'device': device, 'verbose': False}

    t0 = time.perf_counter()
    model = YOLO(spec['model_path'], task='detect')
    model.predict(pictures[0], **kwargs)          # the first detection also loads the model's runtime
    out: dict = {'load_time_s': time.perf_counter() - t0}

    def predict(picture):
        r = model.predict(picture, **kwargs)[0]
        return r.speed, r.boxes.conf.tolist()

    sampler = TempSampler(probes.read_cpu_temp, spec['sample_every_sec'])
    sampler.start()
    out.update(time_detections(predict, pictures, spec['warmup'], spec['timed']))
    out.update(sampler.stop())
    out['peak_memory_mb'] = probes.peak_memory_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    out['cpu_percent'] = out['cpu_cores_used'] / (os.cpu_count() or 1) * 100
    out['throttled'] = probes.read_throttled()

    acc = spec.get('accuracy')
    if acc:
        t0 = time.perf_counter()
        m = model.val(data=acc['dataset'], imgsz=spec['image_size'], batch=1, device=device, workers=0,
                      plots=False, verbose=False, save_json=False, project=acc['runs_dir'],
                      name=Path(spec['model_path']).stem, exist_ok=True)
        out.update(map50=float(m.box.map50), map50_95=float(m.box.map), box_precision=float(m.box.mp),
                   box_recall=float(m.box.mr), accuracy_time_s=time.perf_counter() - t0)
    return out


def main(argv: list[str]) -> None:
    spec = json.loads(Path(argv[1]).read_text())
    Path(argv[2]).write_text(json.dumps(run(spec)))


if __name__ == '__main__':
    main(sys.argv)
