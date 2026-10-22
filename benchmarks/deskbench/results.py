"""
The results file: one CSV per run, one row per combination.

Rows are written as soon as each combination finishes, so a crash or a reboot
loses at most the one that was running, and `--resume` carries on from there.
"""

from __future__ import annotations

import csv
from pathlib import Path

COLUMNS = [
    # which run, which machine, which combination
    'run_id', 'device', 'hardware', 'commit', 'combo', 'model', 'image_size', 'format', 'precision',
    'status', 'error', 'started_at',
    # speed
    'pictures_timed', 'total_time_s', 'fps',
    'inference_ms_mean', 'inference_ms_p50', 'inference_ms_p95',
    'total_ms_mean', 'total_ms_p50', 'total_ms_p95', 'preprocess_ms_mean', 'postprocess_ms_mean',
    'load_time_s', 'model_size_mb',
    # machine load
    'cpu_cores_used', 'cpu_percent', 'peak_memory_mb',
    'temp_start_c', 'temp_max_c', 'temp_end_c', 'cpu_mhz_min', 'cpu_mhz_top', 'cooldown_start_c', 'cooldown_wait_s',
    # what it found
    'detections_total', 'detections_per_picture', 'mean_confidence',
    # accuracy on the COCO photos
    'map50', 'map50_95', 'box_precision', 'box_recall', 'accuracy_time_s',
    'combo_time_s',
]


def slug(name: str) -> str:
    """'water bottle' → 'water_bottle', for column names."""
    return name.lower().replace(' ', '_')


def desk_columns(kinds: list[str], scenes: list[str]) -> list[str]:
    """The desk test's results file: one row per combination, with each kind's and scene's score."""
    cols = ['run_id', 'device', 'hardware', 'commit', 'combo', 'model', 'kind', 'image_size', 'format',
            'precision', 'status', 'error', 'started_at', 'pictures', 'desk_score', 'desk_score_low',
            'your_desk_score']
    for k in map(slug, kinds):
        cols += [f'score_{k}', f'found_{k}', f'false_{k}', f'there_{k}']
    return cols + [f'scene_{slug(s)}' for s in scenes] + ['combo_time_s']


def _fmt(value) -> str:
    if value is None:
        return ''
    if isinstance(value, float):
        return f'{value:.4g}' if abs(value) < 1000 else f'{value:.1f}'
    return str(value)


def append_row(path: Path, row: dict, columns: list[str] = COLUMNS) -> None:
    unknown = set(row) - set(columns)
    if unknown:
        raise KeyError(f'not a results column: {", ".join(sorted(unknown))}')
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    # Carrying on with a file made before the columns changed: keep that file's columns, so rows stay lined up
    if not new:
        with open(path, newline='', encoding='utf-8') as f:
                columns = next(csv.reader(f), columns)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerow({k: _fmt(row.get(k)) for k in columns})
        f.flush()


def read_rows(path: Path) -> list[dict]:
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def finished_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {r['combo'] for r in read_rows(path) if r.get('status') == 'ok'}


def all_rows(results_dir: Path, skip: tuple[str, ...] = ('desk',)) -> list[dict]:
    """The speed results of every device (the desk test's own folder is left out)."""
    rows = []
    for path in sorted(p for p in results_dir.glob('*/*.csv') if p.parent.name not in skip):
        rows += read_rows(path)
    return rows
