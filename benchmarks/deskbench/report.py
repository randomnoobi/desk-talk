"""
benchmarks/results/README.md: the three picks to try on the desk, the desk test, each
device's results (all its runs merged, newest row per combination), the best pick per
device, and the history.

The three picks: of the combinations fast enough on the Pi, the three different models
with the highest desk score (see benchmarks/desk-test-design.md).

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


def merged_rows(runs: dict[str, list[dict]]) -> list[dict]:
    """Several runs together (a second run can add new models): the newest ok row of each combination,
    or its newest failed row if it never worked."""
    rows = [r for run_id in sorted(runs) for r in runs[run_id]]
    ok = _latest_per_combo([r for r in rows if r.get('status') == 'ok'])
    done = {r['combo'] for r in ok}
    return ok + [r for r in _latest_per_combo(rows) if r['combo'] not in done]


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


def pick_three(desk_rows: list[dict], device_rows: list[dict], camera_fps: float) -> list[dict]:
    speed = {r['combo']: r for r in device_rows
             if r.get('status') == 'ok' and _num(r.get('fps')) and _num(r.get('total_ms_p95'))}
    budget_ms = 1000 / camera_fps
    best: dict[str, dict] = {}
    for d in desk_rows:
        s = speed.get(d['combo'])
        if d.get('status') != 'ok' or _num(d.get('desk_score')) is None or not s or _num(s['total_ms_p95']) > budget_ms:
            continue
        row = {**d, 'fps': s['fps'], 'total_ms_p95': s['total_ms_p95']}
        old = best.get(d['model'])
        if old is None or _rank(row) > _rank(old):
            best[d['model']] = row
    return sorted(best.values(), key=_rank, reverse=True)[:3]


def _rank(r: dict) -> tuple[float, float]:
    return _num(r['desk_score']), _num(r['fps'])


def _cell(value, digits=1) -> str:
    n = _num(value)
    if n is None:
        return value or '-'
    return f'{n:.{digits}f}'


TABLE = [('combo', 'combination', None), ('fps', 'pictures/s', 1), ('inference_ms_p50', 'inference p50 ms', 0),
         ('inference_ms_p95', 'inference p95 ms', 0), ('total_ms_p95', 'total p95 ms', 0),
         ('cpu_percent', 'CPU %', 0), ('peak_memory_mb', 'peak MB', 0), ('temp_max_c', 'max °C', 0),
         ('cpu_mhz_min', 'lowest MHz', 0),
         ('map50_95', 'mAP50-95', 3), ('map50', 'mAP50', 3), ('detections_per_picture', 'found/picture', 1),
         ('model_size_mb', 'model MB', 1)]


def _formats(pick_formats: Sequence[str] | None) -> str:
    return ' or '.join(pick_formats) if pick_formats else 'any format'


def _device_section(device: str, runs: dict[str, list[dict]], camera_fps: float, share: float,
                    pick_formats: Sequence[str] | None) -> list[str]:
    rows = merged_rows(runs)
    first = runs[max(runs)][0]
    best = pick_best(rows, camera_fps, share, pick_formats)
    lines = [f'## {device}', '',
             f'Runs {", ".join(f"`{r}`" for r in sorted(runs))} (newest row of each combination) on '
             f'**{first.get("hardware") or "?"}**, latest commit `{(first.get("commit") or "?")[:10]}`.', '']
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


def _f(value, digits=2) -> str:
    n = _num(value)
    return '-' if n is None else f'{n:.{digits}f}'


def _kind_scores(row: dict, kinds: list[str]) -> list[tuple[str, float]]:
    return [(k, _num(row.get(f'score_{_slug(k)}'))) for k in kinds if _num(row.get(f'score_{_slug(k)}')) is not None]


def _slug(name: str) -> str:
    return name.lower().replace(' ', '_')


def _picks_section(desk_rows: list[dict], pick_rows: list[dict], camera_fps: float, info: dict) -> list[str]:
    device = 'Pi' if info['pick_device'] == 'pi' else info['pick_device']      # results call it pi
    lines = ['## Three picks to try', '',
             f'Of the combinations that finish 95% of pictures within {1000 / camera_fps:.0f} ms on the {device}, '
             'the three different models with the highest desk score. Try each on your desk.', '']
    timed = {r['model'] for r in pick_rows}
    untimed = sorted({r['model'] for r in desk_rows} - timed)
    picks = pick_three(desk_rows, pick_rows, camera_fps)
    if not picks:
        lines.append(f'No picks yet: nothing has both a desk score and a fast enough {device} speed.')
    for i, p in enumerate(picks, 1):
        scores = sorted(_kind_scores(p, info['kinds']), key=lambda ks: -ks[1])
        your = _num(p.get('your_desk_score'))
        model_file = f'{p["model"]}-desk.pt' if p.get('kind') in info['words_kinds'] else f'{p["model"]}.pt'
        lines += [f'### {i}. `{p["model"]}` at {p["image_size"]} ({p["format"]} {p["precision"]})', '',
                  f'- Desk score **{_f(p["desk_score"])}** (your desk {_f(your)}). On the {device}: '
                  f'{_cell(p["fps"])} pictures/s, 95% within {_cell(p["total_ms_p95"], 0)} ms.',
                  f'- Best at: {", ".join(f"{k} ({v:.2f})" for k, v in scores[:2])}. '
                  f'Worst at: {", ".join(f"{k} ({v:.2f})" for k, v in scores[-2:][::-1])}.']
        if your is not None and your < _num(p['desk_score']) - info['gap']:
            lines.append(f'- **Does worse on your desk**: {your:.2f} on your photos against '
                         f'{_num(p["desk_score"]):.2f} on the public ones.')
        lines += ['- To try it, put this in `config/settings.local.yaml` (your own file):', '',
                  '  ```yaml', '  detector:', f'    model: models/bench/{model_file}',
                  f'    image_size: {p["image_size"]}', '  ```', '']
    if picks:
        kinds, scenes = info['kinds'], info['scenes']
        lines += ['Each pick\'s own score for every kind and scene:', '',
                  '| pick | ' + ' | '.join(kinds + scenes) + ' |', '|' + '---|' * (1 + len(kinds) + len(scenes))]
        lines += [f'| {i}. `{p["combo"]}` | '
                  + ' | '.join([_f(p.get(f'score_{_slug(k)}')) for k in kinds]
                               + [_f(p.get(f'scene_{_slug(s)}')) for s in scenes]) + ' |'
                  for i, p in enumerate(picks, 1)]
        lines.append('')
    if untimed:
        lines += ([] if lines[-1] == '' else ['']) + [f'Not timed on the {device} yet, so not in the picks: '
                                                      + ', '.join(f'`{m}`' for m in untimed) + '.', '']
    return lines if lines[-1] == '' else lines + ['']


def _desk_section(desk_rows: list[dict], info: dict) -> list[str]:
    kinds, scenes = info['kinds'], info['scenes']
    ok = [r for r in desk_rows if r.get('status') == 'ok']
    best = {}
    for r in ok:
        if r['model'] not in best or (_num(r.get('desk_score')) or 0) > (_num(best[r['model']].get('desk_score')) or 0):
            best[r['model']] = r
    order = sorted(best.values(), key=lambda r: -(_num(r.get('desk_score')) or 0))
    rules = ', '.join(f'{k} {v:g}' for k, v in info['thresholds'].items())
    lines = ['## Desk test', '',
             f'Runs {", ".join(f"`{r}`" for r in info["runs"])} (newest ok row of each combination) on '
             f'**{info["hardware"]}**: {info["pictures"]} public desk photos and '
             f'{info["your_photos"]} of your own. A kind\'s score = found rate × (1 − false alarms); the desk score '
             'is the average over the kinds. "Found" uses the app\'s own rule for how sure a model must be '
             f'({rules}); "at {info["low"]:g}" counts weaker finds too.', '',
             'Each model\'s best combination:', '',
             '| model | combination | desk score | at ' + f'{info["low"]:g}' + ' | your desk | '
             + ' | '.join(kinds) + ' |', '|' + '---|' * (5 + len(kinds))]
    for r in order:
        lines.append(f'| `{r["model"]}` | `{r["combo"]}` | {_f(r.get("desk_score"))} | {_f(r.get("desk_score_low"))} | '
                     f'{_f(r.get("your_desk_score"))} | '
                     + ' | '.join(_f(r.get(f'score_{_slug(k)}')) for k in kinds) + ' |')
    lines += ['', f'By scene (a scene needs {info["min_photos"]} photos of a kind to score it, else "-"):', '',
              '| model | ' + ' | '.join(scenes) + ' |', '|' + '---|' * (1 + len(scenes))]
    for r in order:
        lines.append(f'| `{r["model"]}` | ' + ' | '.join(_f(r.get(f'scene_{_slug(s)}')) for s in scenes) + ' |')
    counts = ', '.join(f'{k} {t} / {n}' + (f' (only {t} photos)' if t < info['few'] else '')
                       for k, (t, n) in info['counts'].items())
    weak = []
    for k in kinds:
        top = max(order, key=lambda r: _num(r.get(f'score_{_slug(k)}')) or 0, default=None)
        best_score = _num(top.get(f'score_{_slug(k)}')) if top else None
        if best_score is not None and best_score < info['weak']:
            weak.append(f'- Every model is weak at {k}: the best is {best_score:.2f} (`{top["model"]}`).')
    lines += ['', f'Public photos per kind (there / not there): {counts}.', '', *weak,
              '- Trash is a judgment call: no list of names has "trash", only pieces of it (cans, tissues, napkins).',
              '- On the public photos, "AirPods" means any earphones or headset. For AirPods, trash and some foods, '
              'a photo marked "not there" may still have one nobody labeled.',
              '- The app has no trash agent, so trash a yoloe model finds would join the chat as a new, '
              'unknown object.',
              '- Your own photos are a check, not the ranking: a few pictures, one evening, one angle.'
              if info['your_photos'] else
              '- No photos of your own desk (`data/bench/desk-photos/labels.yaml`), so "your desk" is empty.', '']
    return lines


def render(rows: list[dict], camera_fps: float, min_accuracy_share: float, desk_rows=(),
           desk_info: dict | None = None, pick_formats: Sequence[str] | None = None) -> str:
    lines = ['# YOLO benchmark results', '',
             'Made by `deskbench report` from the CSV files in this folder. Don\'t edit by hand.', '',
             f'Best pick = the fastest combination in {_formats(pick_formats)} (what the Pi\'s detector service '
             f'runs) that finishes 95% of pictures within {1000 / camera_fps:.0f} ms (the app takes '
             f'{camera_fps:g} pictures a second) and reaches at least {min_accuracy_share:.0%} of the best accuracy '
             '(mAP50-95 on 128 COCO photos) in those formats on that device.', '']
    by_device = runs_by_device(rows)
    if desk_rows and desk_info:
        pick_rows = merged_rows(by_device[desk_info['pick_device']]) if desk_info['pick_device'] in by_device else []
        lines += _picks_section(list(desk_rows), pick_rows, camera_fps, desk_info)
        lines += _desk_section(list(desk_rows), desk_info)
    history = []
    for device in sorted(by_device):
        runs = by_device[device]
        lines += _device_section(device, runs, camera_fps, min_accuracy_share, pick_formats)
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
