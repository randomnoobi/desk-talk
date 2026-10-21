import itertools

import pytest

from deskbench.measure import TempSampler, time_detections, wait_until_cool


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_times_only_the_timed_pictures_and_counts_detections():
    clock, cpu = FakeClock(), FakeClock()
    calls = []

    def predict(picture):
        calls.append(picture)
        clock.t += 0.1          # 100 ms per picture
        cpu.t += 0.2            # two cores busy
        return {'preprocess': 1.0, 'inference': 80.0, 'postprocess': 2.0}, [0.9, 0.5]

    out = time_detections(predict, ['a', 'b'], warmup=3, timed=4, clock=clock, cpu_time=cpu)
    assert calls == ['a', 'b', 'a', 'b', 'a', 'b', 'a']     # 3 warm-up, then 4 timed, pictures reused
    assert out['pictures_timed'] == 4
    assert out['total_time_s'] == pytest.approx(0.4)
    assert out['fps'] == pytest.approx(10)
    assert out['total_ms_p50'] == pytest.approx(100)
    assert out['inference_ms_p95'] == pytest.approx(80)
    assert out['cpu_cores_used'] == pytest.approx(2)
    assert out['detections_total'] == 8
    assert out['detections_per_picture'] == pytest.approx(2)
    assert out['mean_confidence'] == pytest.approx(0.7)


def test_no_detections_at_all():
    out = time_detections(lambda p: ({'inference': 5.0}, []), ['a'], warmup=0, timed=2)
    assert out['detections_total'] == 0
    assert out['mean_confidence'] is None


def test_needs_pictures():
    with pytest.raises(ValueError):
        time_detections(lambda p: ({}, []), [], warmup=0, timed=1)


def test_temp_sampler_keeps_the_highest():
    temps = iter([50.0, 61.0, 55.0])
    s = TempSampler(lambda: next(temps, 55.0), every_sec=0.001)
    s.start()
    s.wait_for_samples(3)
    out = s.stop()
    assert out['temp_max_c'] == 61.0
    assert out['temp_start_c'] == 50.0


def test_temp_sampler_without_a_sensor():
    s = TempSampler(lambda: None, every_sec=0.001)
    s.start()
    assert s.stop() == {'temp_start_c': None, 'temp_max_c': None, 'temp_end_c': None}


def test_cooldown_waits_until_cool():
    temps = iter([70.0, 65.0, 58.0])
    clock = FakeClock()
    slept = []

    def sleep(sec):
        slept.append(sec)
        clock.t += sec

    out = wait_until_cool(lambda: next(temps), below=60, max_wait=300, check_every=5, sleep=sleep, clock=clock)
    assert out == {'cooldown_start_c': 70.0, 'cooldown_wait_s': 10.0}


def test_cooldown_gives_up_after_max_wait():
    clock = FakeClock()

    def sleep(sec):
        clock.t += sec

    out = wait_until_cool(itertools.repeat(80.0).__next__, below=60, max_wait=12, check_every=5, sleep=sleep,
                          clock=clock)
    assert out['cooldown_wait_s'] == pytest.approx(15)


def test_cooldown_skipped_without_a_sensor():
    out = wait_until_cool(lambda: None, below=60, max_wait=300, check_every=5, sleep=lambda s: 1 / 0)
    assert out == {'cooldown_start_c': None, 'cooldown_wait_s': 0.0}
