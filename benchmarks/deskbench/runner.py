"""Runs every combination in turn. One failing combination is recorded and the run goes on."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import results
from .matrix import Combo


def run_all(combos: list[Combo], run_combo: Callable[[Combo], dict], csv_path: Path, base: dict,
            cooldown: Callable[[], dict], log: Callable[[str], None] = print) -> list[dict]:
    done = results.finished_keys(csv_path)
    rows = []
    for i, combo in enumerate(combos, 1):
        label = f'[{i}/{len(combos)}] {combo.key}'
        if combo.key in done:
            log(f'{label}: already done, skipping')
            continue
        row = {**base, 'combo': combo.key, 'model': combo.model, 'image_size': combo.image_size,
               'format': combo.fmt, 'precision': combo.precision,
               'started_at': datetime.now(UTC).isoformat(timespec='seconds')}
        row.update(cooldown() or {})
        t0 = time.monotonic()
        try:
            row.update(run_combo(combo))
            row['status'] = 'ok'
        except Exception as exc:
            row['status'] = 'failed'
            row['error'] = f'{type(exc).__name__}: {exc}'[-500:]
        row['combo_time_s'] = time.monotonic() - t0
        results.append_row(csv_path, row)
        rows.append(row)
        if row['status'] == 'ok':
            log(f'{label}: {row.get("fps") or 0:.1f} pictures/s, p95 {row.get("total_ms_p95") or 0:.0f} ms, '
                f'mAP50-95 {row.get("map50_95") if row.get("map50_95") is not None else "-"}')
        else:
            log(f'{label}: FAILED {row["error"]}')
    return rows
