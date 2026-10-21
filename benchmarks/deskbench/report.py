"""
benchmarks/results/README.md: the latest run on each device, the best pick, and the history.

The best pick on a device is the fastest combination, in one of the pick_formats (the formats the Pi's
detector service can run), that
  - finishes 95% of pictures within 1/camera_fps seconds (fast enough for the app), and
  - reaches at least min_accuracy_share of the best accuracy (mAP50-95) seen in those formats on that device.
Without accuracy numbers, it's just the fastest one that is fast enough.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_per_combo(rows: list[dict]) -> list[dict]:
    """A resumed run can have a failed row and a later ok row for the same combination; keep the last."""
    by_combo = {}
    for r in rows:
        by_combo[r['combo']] = r
    return list(by_combo.values())


def runs_by_device(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        out[r['device']][r['run_id']].append(r)
    return out


def pick_best(rows: list[dict], camera_fps: float, min_accuracy_share: float,
              pick_formats: Sequence[str] | None = None) -> dict | None:
    """pick_formats: only these formats can be picked (None = any)."""
    ok = [r for r in rows if r.get('status') == 'ok' and _num(r.get('fps')) and _num(r.get('total_ms_p95'))
          and (pick_formats is None or r.get('format') in pick_formats)]
    budget_ms = 1000 / camera_fps
    fast = [r for r in ok if _num(r['total_ms_p95']) <= budget_ms]
    accs = [_num(r.get('map50_95')) for r in ok if _num(r.get('map50_95')) is not None]
    if accs:
        need = max(accs) * min_accuracy_share
        fast = [r for r in fast if (_num(r.get('map50_95')) or 0) >= need]
    return max(fast, key=lambda r: _num(r['fps']), default=None)


def _cell(value, digits=1) -> str:
    n = _num(value)
    if n is None:
        return value or '-'
    return f'{n:.{digits}f}'


TABLE = [('combo', 'combination', None), ('fps', 'pictures/s', 1), ('inference_ms_p50', 'inference p50 ms', 0),
         ('inference_ms_p95', 'inference p95 ms', 0), ('total_ms_p95', 'total p95 ms', 0),
         ('cpu_percent', 'CPU %', 0), ('peak_memory_mb', 'peak MB', 0), ('temp_max_c', 'max °C', 0),
         ('map50_95', 'mAP50-95', 3), ('map50', 'mAP50', 3), ('detections_per_picture', 'found/picture', 1),
         ('model_size_mb', 'model MB', 1)]


def _formats(pick_formats: Sequence[str] | None) -> str:
    return ' or '.join(pick_formats) if pick_formats else 'any format'


def _device_section(device: str, run_id: str, rows: list[dict], camera_fps: float, share: float,
                    pick_formats: Sequence[str] | None) -> list[str]:
    rows = _latest_per_combo(rows)
    first = rows[0]
    best = pick_best(rows, camera_fps, share, pick_formats)
    lines = [f'## {device}', '',
             f'Latest run `{run_id}` on **{first.get("hardware") or "?"}**, '
             f'commit `{(first.get("commit") or "?")[:10]}`.', '']
    if best:
        lines.append(f'**Best pick: `{best["combo"]}`** ({_cell(best["fps"])} pictures/s, '
                     f'p95 {_cell(best["total_ms_p95"], 0)} ms, mAP50-95 {_cell(best.get("map50_95"), 3)})')
    else:
        lines.append(f'**No pick:** nothing in {_formats(pick_formats)} finished 95% of pictures within '
                     f'{1000 / camera_fps:.0f} ms with at least {share:.0%} of the best accuracy.')
    lines += ['', '| ' + ' | '.join(h for _, h, _ in TABLE) + ' |', '|' + '---|' * len(TABLE)]
    ok = sorted((r for r in rows if r.get('status') == 'ok'), key=lambda r: -(_num(r.get('fps')) or 0))
    for r in ok:
        cells = [f'`{r["combo"]}`' if k == 'combo' else _cell(r.get(k), d) for k, _, d in TABLE]
        lines.append('| ' + ' | '.join(cells) + ' |')
    failed = [r for r in rows if r.get('status') != 'ok']
    if failed:
        lines += ['', 'These failed:', '']
        lines += [f'- `{r["combo"]}`: {r.get("status")}: {r.get("error") or ""}' for r in failed]
    return lines + ['']


def render(rows: list[dict], camera_fps: float, min_accuracy_share: float,
           pick_formats: Sequence[str] | None = None) -> str:
    lines = ['# YOLO benchmark results', '',
             'Made by `deskbench report` from the CSV files in this folder. Don\'t edit by hand.', '',
             f'Best pick = the fastest combination in {_formats(pick_formats)} (what the Pi\'s detector service '
             f'runs) that finishes 95% of pictures within {1000 / camera_fps:.0f} ms (the app takes '
             f'{camera_fps:g} pictures a second) and reaches at least {min_accuracy_share:.0%} of the best accuracy '
             '(mAP50-95 on 128 COCO photos) in those formats on that device.', '']
    by_device = runs_by_device(rows)
    history = []
    for device in sorted(by_device):
        runs = by_device[device]
        latest = max(runs)
        lines += _device_section(device, latest, runs[latest], camera_fps, min_accuracy_share, pick_formats)
        for run_id in sorted(runs, reverse=True):
            rs = _latest_per_combo(runs[run_id])
            best = pick_best(rs, camera_fps, min_accuracy_share, pick_formats)
            n_ok = sum(r.get('status') == 'ok' for r in rs)
            history.append(f'| `{run_id}` | {device} | {rs[0].get("hardware") or "?"} | '
                           f'`{(rs[0].get("commit") or "?")[:10]}` | {n_ok} / {len(rs) - n_ok} | '
                           f'{"`" + best["combo"] + "`" if best else "-"} |')
    lines += ['## History', '', '| run | device | hardware | commit | ok / failed | best pick |',
              '|---|---|---|---|---|---|', *history, '']
    return '\n'.join(lines)
