"""
Timing detections, watching the temperature, and waiting for the Pi to cool down.

`time_detections` doesn't know about Ultralytics: it gets a `predict(picture)`
that returns (speed, confidences), where speed is Ultralytics' per-step times in
ms ({'preprocess': .., 'inference': .., 'postprocess': ..}).
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Sequence

from .stats import summarize


def time_detections(predict: Callable, pictures: Sequence, warmup: int, timed: int,
                    clock: Callable[[], float] = time.perf_counter,
                    cpu_time: Callable[[], float] = time.process_time) -> dict:
    if not pictures:
        raise ValueError('no pictures to detect on')
    cycle = itertools.cycle(pictures)
    for _ in range(warmup):
        predict(next(cycle))

    totals, steps, confs = [], {'preprocess': [], 'inference': [], 'postprocess': []}, []
    start, cpu_start = clock(), cpu_time()
    for _ in range(timed):
        t0 = clock()
        speed, found = predict(next(cycle))
        totals.append((clock() - t0) * 1000)
        for step, values in steps.items():
            if speed.get(step) is not None:
                values.append(float(speed[step]))
        confs.extend(found)
    wall, cpu = clock() - start, cpu_time() - cpu_start

    out = {
        'pictures_timed': timed,
        'total_time_s': wall,
        'fps': timed / wall if wall else None,
        'cpu_cores_used': cpu / wall if wall else None,
        'detections_total': len(confs),
        'detections_per_picture': len(confs) / timed,
        'mean_confidence': sum(confs) / len(confs) if confs else None,
    }
    t = summarize(totals)
    out.update(total_ms_mean=t['mean'], total_ms_p50=t['p50'], total_ms_p95=t['p95'])
    if steps['inference']:
        i = summarize(steps['inference'])
        out.update(inference_ms_mean=i['mean'], inference_ms_p50=i['p50'], inference_ms_p95=i['p95'])
    for step in ('preprocess', 'postprocess'):
        if steps[step]:
            out[f'{step}_ms_mean'] = summarize(steps[step])['mean']
    return out


class TempSampler:
    """Reads the temperature in the background and keeps the first, highest and last value."""

    def __init__(self, read: Callable[[], float | None], every_sec: float):
        self.read, self.every = read, every_sec
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> None:
        t = self.read()
        if t is not None:
            self.samples.append(t)

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            self._sample()

    def start(self) -> None:
        self._sample()
        if self.samples:            # no sensor: no thread
            self._thread.start()

    def wait_for_samples(self, n: int, timeout: float = 5.0) -> None:
        end = time.monotonic() + timeout
        while len(self.samples) < n and time.monotonic() < end:
            time.sleep(self.every)

    def stop(self) -> dict:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()
        s = self.samples
        return {'temp_start_c': s[0] if s else None, 'temp_max_c': max(s) if s else None,
                'temp_end_c': s[-1] if s else None}


def wait_until_cool(read: Callable[[], float | None], below: float, max_wait: float, check_every: float,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> dict:
    """So each combination starts equally cool (a hot Pi slows itself down)."""
    start = temp = read()
    t0 = clock()
    while temp is not None and temp >= below and clock() - t0 < max_wait:
        sleep(check_every)
        temp = read()
    return {'cooldown_start_c': start, 'cooldown_wait_s': clock() - t0 if start is not None else 0.0}
