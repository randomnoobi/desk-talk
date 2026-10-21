"""Percentiles and summaries of timings."""

from __future__ import annotations

import math


def percentile(values, p: float) -> float:
    """The p-th percentile, with linear interpolation between the two nearest values (like numpy's default)."""
    xs = sorted(values)
    if not xs:
        raise ValueError('no values')
    pos = (len(xs) - 1) * p / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarize(values) -> dict:
    xs = list(values)
    return {'mean': sum(xs) / len(xs), 'p50': percentile(xs, 50), 'p95': percentile(xs, 95),
            'min': min(xs), 'max': max(xs)}
