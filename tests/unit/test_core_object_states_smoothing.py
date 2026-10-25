"""The two filters that turn confidence numbers into a steady yes / no."""

import pytest

from desktalk.core.object_states.smoothing import Hysteresis, MajorityWindow


def _run_hysteresis(values, on=0.5, off=0.4):
    line = Hysteresis(on=on, off=off)
    return [line.update(v) for v in values]


def test_hysteresis_holds_through_wobble_around_the_line():
    # Starts on at 0.52, then stays on until it drops below the low line.
    assert _run_hysteresis([0.52, 0.48, 0.51, 0.47, 0.53]) == [True] * 5


def test_hysteresis_needs_the_high_line_to_turn_on():
    assert _run_hysteresis([0.45, 0.48, 0.5, 0.39, None]) == [False, False, True, False, False]


def _run_window(seen_list, size=5, share=0.5):
    window = MajorityWindow(size=size, vote_share=share)
    return [window.add(seen, None, None)[0] for seen in seen_list]


def test_majority_window_ignores_one_missing_picture():
    seen = [True, True, False, True, False, False]
    assert _run_window(seen) == [True, True, True, True, True, False]


def test_vote_share_is_used():
    # 3 of 4 is 0.75 (more than 0.7); 3 of 5 is 0.6 (not).
    seen = [True, True, True, False, False]
    assert _run_window(seen, size=5, share=0.7) == [True, True, True, True, False]


def test_majority_window_center_uses_seen_pictures_only():
    window = MajorityWindow(size=3, vote_share=0.5)
    window.add(True, (0.2, 0.2), 0.6)
    window.add(False, None, None)
    _, center, conf = window.add(True, (0.4, 0.4), 0.8)
    assert center == pytest.approx((0.3, 0.3))
    assert conf == pytest.approx(0.7)


def test_majority_window_has_no_center_when_nothing_was_seen():
    window = MajorityWindow(size=3, vote_share=0.5)
    assert window.add(False, None, None) == (False, None, None)


def test_votes_count_the_window():
    window = MajorityWindow(size=5, vote_share=0.5)
    for seen in (True, False, True):
        window.add(seen, None, None)
    assert window.votes() == (2, 3)


def test_votes_only_count_the_last_pictures():
    window = MajorityWindow(size=2, vote_share=0.5)
    for seen in (True, True, False):
        window.add(seen, None, None)
    assert window.votes() == (1, 2)
