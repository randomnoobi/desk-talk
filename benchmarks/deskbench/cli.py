"""
deskbench: time YOLO models on this computer or on the Raspberry Pi.

    uv run deskbench run                 # every combination in config/benchmark.yaml
    uv run deskbench run --quick         # a few pictures each, just to check everything works
    uv run deskbench run --models yolo11n --resume benchmarks/results/pi/<run>.csv
    uv run deskbench prepare             # only download and convert the models
    uv run deskbench report              # rebuild benchmarks/results/README.md
    uv run deskbench desk prepare        # choose the desk photos, download them
    uv run deskbench desk run            # score every combination on the desk photos
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import desk_data, models, probes, report, results
from .desk_score import app_thresholds, found_kinds, missing_names, name_map, scene_scores, score
from .desk_set import SCENES, build_desk_set, read_your_desk
from .matrix import build_combos, kind_of
from .measure import wait_until_cool
from .runner import run_all
from .words import can_make_words, clear_if_changed, desk_words

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / 'config' / 'benchmark.yaml'
PICTURE_TYPES = ('*.jpg', '*.jpeg', '*.png')


def load_config(path: Path, quick: bool = False) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    if quick:
        q = cfg['quick']
        cfg['speed'].update(warmup_pictures=q['warmup_pictures'], timed_pictures=q['timed_pictures'])
        cfg['accuracy']['share_of_pictures'] = q['accuracy_share_of_pictures']
    return cfg


def setup_ultralytics(data_dir: Path, models_dir: Path) -> None:
    """Keep Ultralytics' settings, downloads and run folders inside data/bench, and switch off its usage reports."""
    os.environ.setdefault('YOLO_CONFIG_DIR', str(data_dir / 'ultralytics'))
    os.environ.setdefault('MPLCONFIGDIR', str(data_dir / 'matplotlib'))
    os.environ['YOLO_AUTOINSTALL'] = 'false'    # a missing package should fail, not get pip-installed mid-run
    from ultralytics import settings
    settings.update({'datasets_dir': str(data_dir / 'datasets'), 'runs_dir': str(data_dir / 'runs'),
                     'weights_dir': str(models_dir), 'sync': False})


def coco_pictures(cfg: dict) -> list[Path]:
    from ultralytics.data.utils import check_det_dataset
    val = check_det_dataset(cfg['accuracy']['dataset'])['val']
    folder = Path(val[0] if isinstance(val, list) else val)
    return sorted(p for t in PICTURE_TYPES for p in folder.glob(t))


def speed_pictures(cfg: dict) -> list[Path]:
    folder = cfg['speed'].get('pictures_folder')
    if folder:
        return sorted(p for t in PICTURE_TYPES for p in (REPO / folder).glob(t))
    return coco_pictures(cfg)


def accuracy_dataset(cfg: dict, data_dir: Path) -> str:
    """The dataset to check accuracy on: all of it, or the first share of its pictures."""
    share = float(cfg['accuracy']['share_of_pictures'])
    if share >= 1:
        return cfg['accuracy']['dataset']
    from ultralytics.data.utils import check_det_dataset
    d = check_det_dataset(cfg['accuracy']['dataset'])
    pictures = coco_pictures(cfg)
    pictures = pictures[:max(1, round(len(pictures) * share))]
    data_dir.mkdir(parents=True, exist_ok=True)
    listing = data_dir / 'accuracy-pictures.txt'
    listing.write_text('\n'.join(str(p) for p in pictures) + '\n')
    subset = data_dir / 'accuracy-subset.yaml'
    subset.write_text(yaml.safe_dump({'path': str(data_dir), 'train': str(listing), 'val': str(listing),
                                      'names': d['names']}))
    return str(subset)


def words_for(cfg: dict, model: str) -> list[str] | None:
    """The words to build into a model: only for kinds that use words (yoloe)."""
    return desk_words(cfg['desk']) if kind_of(cfg, model)[1]['names'] == 'words' else None


def clear_changed_words(cfg: dict, combos: list, models_dir: Path) -> None:
    for model in dict.fromkeys(c.model for c in combos):
        words = words_for(cfg, model)
        if words and clear_if_changed(models_dir, model, words, can_make_words()):
            print(f'{model}: the desk words changed (or it is new), so it will be made again')


def make_run_combo(cfg: dict, models_dir: Path, data_dir: Path, pictures: list[Path], log):
    speed, acc = cfg['speed'], cfg['accuracy']
    dataset = accuracy_dataset(cfg, data_dir) if acc['enabled'] else None

    def run_combo(combo) -> dict:
        path = models.ensure_model(combo, models_dir, log, words_for(cfg, combo.model))
        _, kind = kind_of(cfg, combo.model)
        # The COCO accuracy check only means something for models that use COCO's 80 names
        check = dataset if kind['names'] == 'coco' else None
        spec = {'model_path': str(path), 'format': combo.fmt, 'image_size': combo.image_size,
                'task': kind.get('task', 'detect'),
                'pictures': [str(p) for p in pictures], 'warmup': speed['warmup_pictures'],
                'timed': speed['timed_pictures'], 'confidence': speed['confidence'],
                'torch_device': speed['torch_device'], 'sample_every_sec': speed['sample_every_sec'],
                'accuracy': {'dataset': check, 'runs_dir': str(data_dir / 'runs')} if check else None}
        with tempfile.TemporaryDirectory() as tmp:
            spec_path, out_path = Path(tmp) / 'spec.json', Path(tmp) / 'out.json'
            spec_path.write_text(json.dumps(spec))
            proc = subprocess.run([sys.executable, '-m', 'deskbench.run_one', str(spec_path), str(out_path)],
                                  capture_output=True, text=True, timeout=cfg['combination_timeout_sec'])
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(f'exit code {proc.returncode}: {proc.stderr.strip()[-400:]}')
            out = json.loads(out_path.read_text())
        out['model_size_mb'] = models.size_mb(path)
        return out

    return run_combo


def desk_rows(results_dir: Path) -> tuple[list[dict], list[str]]:
    """Every desk run together (e.g. a later run of only the yoloe models): newest ok row per combination."""
    runs = {p.stem: results.read_rows(p) for p in sorted((results_dir / 'desk').glob('*.csv'))}
    return report.merged_rows(runs), sorted(runs)


def desk_report(cfg: dict, results_dir: Path) -> tuple[list[dict], dict | None]:
    """The desk runs' rows, and what the report says around them (None before the first desk run)."""
    desk = cfg['desk']
    rows, runs = desk_rows(results_dir)
    set_file = REPO / desk['set_file']
    if not rows or not set_file.exists():
        return [], None
    pictures = json.loads(set_file.read_text())['pictures']
    info = {'runs': runs, 'hardware': rows[0].get('hardware') or '?',
            'kinds': list(desk['kinds']), 'scenes': SCENES, 'pictures': len(pictures),
            'counts': {k: (sum(p['answers'][k] == 'there' for p in pictures),
                           sum(p['answers'][k] == 'not there' for p in pictures)) for k in desk['kinds']},
            'your_photos': len(read_your_desk(REPO / desk['your_photos'], list(desk['kinds']))),
            'gap': desk['your_desk_gap'], 'few': desk['few_photos_warning'], 'low': desk['low_confidence'],
            'thresholds': app_thresholds(REPO / desk['app_settings'], REPO / desk['app_agents'], desk['kinds']),
            'words_kinds': [k for k, v in cfg['model_kinds'].items() if v['names'] == 'words'],
            'pick_device': cfg['report']['pick_device'], 'weak': desk['weak_score'],
            'min_photos': desk['min_photos_per_cell']}
    return rows, info


def write_report(cfg: dict, results_dir: Path) -> Path:
    rows = results.all_rows(results_dir)
    desk_rows, desk_info = desk_report(cfg, results_dir)
    path = results_dir / 'README.md'
    path.write_text(report.render(rows, cfg['report']['camera_fps'], cfg['report']['min_accuracy_share'],
                                  desk_rows, desk_info), encoding='utf-8')
    return path


def cmd_run(args, cfg: dict, paths: dict) -> None:
    device = args.device or probes.device_label()
    results_dir = paths['data'] / 'quick-results' if args.quick else paths['results']
    if args.resume:
        csv_path = Path(args.resume).resolve()
        run_id = csv_path.stem
    else:
        run_id = f'{datetime.now(UTC):%Y-%m-%dT%H%MZ}_{device}'
        csv_path = results_dir / device / f'{run_id}.csv'

    env = probes.environment(REPO)
    info = csv_path.with_suffix('.json')
    if not info.exists():
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(json.dumps({'run_id': run_id, 'device': device, **env, 'config': cfg}, indent=2))

    combos = build_combos(cfg, args.models, args.kinds)
    clear_changed_words(cfg, combos, paths['models'])
    pictures = speed_pictures(cfg)
    print(f'{len(combos)} combinations on {env["hardware"]} ({device}), {len(pictures)} pictures')
    print(f'Results: {csv_path}')
    cd = cfg['cooldown']
    run_all(combos, make_run_combo(cfg, paths['models'], paths['data'], pictures, print), csv_path,
            base={'run_id': run_id, 'device': device, 'hardware': env['hardware'], 'commit': env['commit']},
            cooldown=lambda: wait_until_cool(probes.read_cpu_temp, cd['start_below_c'], cd['max_wait_sec'],
                                             cd['check_every_sec']))
    print(f'Report: {write_report(cfg, results_dir)}')


def cmd_prepare(args, cfg: dict, paths: dict) -> None:
    speed_pictures(cfg)                           # downloads the COCO photos
    failed = 0
    combos = build_combos(cfg, args.models, args.kinds)
    clear_changed_words(cfg, combos, paths['models'])
    for combo in combos:
        try:
            models.ensure_model(combo, paths['models'], words=words_for(cfg, combo.model))
        except Exception as exc:
            failed += 1
            print(f'{combo.key}: FAILED {type(exc).__name__}: {exc}')
    print(f'Models are in {paths["models"]} ({failed} failed)')


def cmd_desk_prepare(args, cfg: dict, paths: dict) -> None:
    desk = cfg['desk']
    set_file = REPO / desk['set_file']
    if args.keep_set:
        pictures = json.loads(set_file.read_text())['pictures']
    else:
        labels_dir = paths['data'] / 'desk'
        lvis = desk_data.lvis_val(labels_dir, desk['downloads'])
        known = {c['name'] for c in lvis['categories']}
        missing = sorted({n for k in desk['kinds'].values() for n in k['lvis']} - known)
        if missing:
            raise ValueError(f'Not LVIS names (fix desk.kinds in the config): {", ".join(missing)}')
        pictures = build_desk_set(lvis, desk_data.coco_val_labels(labels_dir, desk['downloads']), desk)
        set_file.parent.mkdir(parents=True, exist_ok=True)
        set_file.write_text(json.dumps({'made_from': 'LVIS v1 val + COCO val2017 labels (val2017 photos only)',
                                        'kinds': list(desk['kinds']), 'pictures': pictures}, indent=1))
        print(f'{len(pictures)} pictures chosen: {set_file}')
    scenes = Counter(s for p in pictures for s in p['scenes'])
    print('Pictures per scene: ' + ', '.join(f'{s} {scenes[s]}' for s in SCENES))
    for kind in desk['kinds']:
        answers = Counter(p['answers'][kind] for p in pictures)
        print(f'  {kind}: there {answers["there"]}, not there {answers["not there"]}, unknown {answers["unknown"]}')
    failed = desk_data.download_photos(pictures, REPO / desk['photos'], desk['downloads'])
    print(f'Photos are in {REPO / desk["photos"]} ({failed} could not be fetched)')


def public_pictures(pictures: list[dict], folder: Path, log=print) -> list[dict]:
    """The desk set's photos that are on this computer, with their paths."""
    here = [{**p, 'path': folder / f'{p["id"]}.jpg'} for p in pictures]
    here = [p for p in here if p['path'].exists()]
    if not here:
        raise RuntimeError(f'No desk photos in {folder}. Run: uv run deskbench desk prepare --keep-set')
    if len(here) < len(pictures):
        log(f'Only {len(here)} of {len(pictures)} desk photos are here; run: uv run deskbench desk prepare --keep-set')
    return here


def your_pictures(pictures: list[dict], log=print) -> list[dict]:
    """Sam's own photos listed in labels.yaml that are really there (a typo shouldn't fail every combination)."""
    missing = [p['id'] for p in pictures if not p['path'].exists()]
    if missing:
        log(f'Not found, so left out: {", ".join(missing)} (listed in labels.yaml)')
    return [p for p in pictures if p['path'].exists()]


def desk_row(kind: dict, detections: dict, pictures: list[dict], your_pictures: list[dict], desk_cfg: dict,
             thresholds: dict[str, float]) -> dict:
    """Score one combination's detections ({picture id: [[name, confidence], ...]}) on the desk pictures."""
    kinds = list(desk_cfg['kinds'])
    names = name_map(desk_cfg['kinds'], kind['names'])
    nameable = set(names.values())

    def found(pics, threshold):
        return {p['id']: found_kinds(detections.get(str(p['id']), []), names, threshold) for p in pics}

    s = score(pictures, found(pictures, thresholds), kinds, nameable)
    low = score(pictures, found(pictures, desk_cfg['low_confidence']), kinds, nameable)
    row = {'pictures': len(pictures), 'desk_score': s['desk_score'], 'desk_score_low': low['desk_score'],
           'your_desk_score': score(your_pictures, found(your_pictures, thresholds), kinds, nameable)['desk_score']}
    for k, v in s['kinds'].items():
        k = results.slug(k)
        row.update({f'score_{k}': v['score'], f'found_{k}': v['found_rate'], f'false_{k}': v['false_alarms'],
                    f'there_{k}': v['there']})
    scenes = scene_scores(pictures, found(pictures, thresholds), kinds, nameable, desk_cfg['min_photos_per_cell'])
    row.update({f'scene_{results.slug(name)}': v for name, v in scenes.items()})
    return row


def cmd_desk_run(args, cfg: dict, paths: dict) -> None:
    desk = cfg['desk']
    device = args.device or probes.device_label()
    if args.resume:
        csv_path = Path(args.resume).resolve()
        run_id = csv_path.stem
    else:
        run_id = f'{datetime.now(UTC):%Y-%m-%dT%H%MZ}_{device}'
        csv_path = paths['results'] / 'desk' / f'{run_id}.csv'
    pictures = public_pictures(json.loads((REPO / desk['set_file']).read_text())['pictures'], REPO / desk['photos'])
    yours = your_pictures(read_your_desk(REPO / desk['your_photos'], list(desk['kinds'])))
    thresholds = app_thresholds(REPO / desk['app_settings'], REPO / desk['app_agents'], desk['kinds'])
    combos = build_combos(cfg, args.models, args.kinds)
    env = probes.environment(REPO)
    info = csv_path.with_suffix('.json')            # written only once the pictures are known to be there
    if not info.exists():
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text(json.dumps({'run_id': run_id, 'device': device, **env, 'config': cfg}, indent=2))
    clear_changed_words(cfg, combos, paths['models'])
    saved = paths['data'] / 'desk' / 'detections' / run_id
    saved.mkdir(parents=True, exist_ok=True)
    print(f'{len(combos)} combinations, {len(pictures)} desk photos + {len(yours)} of your own')
    print(f'Results: {csv_path}')

    warned = set()

    def run_combo(combo) -> dict:
        kind_name, kind = kind_of(cfg, combo.model)
        path = models.ensure_model(combo, paths['models'], print, words_for(cfg, combo.model))
        spec = {'model_path': str(path), 'task': kind.get('task', 'detect'), 'image_size': combo.image_size,
                'floor': desk['low_confidence'], 'device': cfg['speed']['torch_device'] if combo.fmt == 'pt' else 'cpu',
                'pictures': [[str(p['id']), str(p['path'])] for p in pictures + yours]}
        with tempfile.TemporaryDirectory() as tmp:
            spec_path, out_path = Path(tmp) / 'spec.json', Path(tmp) / 'out.json'
            spec_path.write_text(json.dumps(spec))
            proc = subprocess.run([sys.executable, '-m', 'deskbench.desk_one', str(spec_path), str(out_path)],
                                  capture_output=True, text=True, timeout=cfg['combination_timeout_sec'])
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(f'exit code {proc.returncode}: {proc.stderr.strip()[-400:]}')
            out = json.loads(out_path.read_text())
        detections = out['detections']
        missing = missing_names(desk['kinds'], kind['names'], out['names'])
        if missing and combo.model not in warned:
            warned.add(combo.model)
            print(f'  {combo.model} has no names {", ".join(missing)} (fix desk.kinds in the config)')
        (saved / f'{combo.key}.json').write_text(json.dumps(detections))
        return {'kind': kind_name, **desk_row(kind, detections, pictures, yours, desk, thresholds)}

    def describe(row: dict) -> str:
        def f(v):
            return '-' if v is None else f'{v:.2f}'
        return (f'desk score {f(row.get("desk_score"))} (at {desk["low_confidence"]}: '
                f'{f(row.get("desk_score_low"))}), your desk {f(row.get("your_desk_score"))}')

    run_all(combos, run_combo, csv_path, base={'run_id': run_id, 'device': device, 'hardware': env['hardware'],
                                                'commit': env['commit']},
            cooldown=lambda: {}, columns=results.desk_columns(list(desk['kinds']), SCENES), describe=describe)
    print(f'Report: {write_report(cfg, paths["results"])}')


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog='deskbench', description='Time YOLO models on this computer or the Pi.')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='time every combination and save the results')
    run.add_argument('--device', help='name for this machine in the results (default: pi or laptop, guessed)')
    run.add_argument('--models', nargs='+', help='only these models, e.g. yolo11n yolo11s')
    run.add_argument('--kinds', nargs='+', help='only these kinds of models, e.g. yolo26 yoloe-26')
    run.add_argument('--resume', help='carry on with an earlier run: path to its .csv')
    run.add_argument('--quick', action='store_true', help='a few pictures each; results go to data/bench, not git')
    prep = sub.add_parser('prepare', help='only download and convert the models')
    prep.add_argument('--models', nargs='+')
    prep.add_argument('--kinds', nargs='+')
    sub.add_parser('report', help='rebuild benchmarks/results/README.md')
    desk = sub.add_parser('desk', help='the desk test: how well each model finds Sam\'s desk things')
    dsub = desk.add_subparsers(dest='desk_command', required=True)
    dprep = dsub.add_parser('prepare', help='choose the desk photos and download them')
    dprep.add_argument('--keep-set', action='store_true', help='only download the photos of the existing set')
    drun = dsub.add_parser('run', help='score every combination on the desk photos (on the Mac)')
    drun.add_argument('--device', help='name for this machine in the results (default: pi or laptop, guessed)')
    drun.add_argument('--models', nargs='+', help='only these models')
    drun.add_argument('--kinds', nargs='+', help='only these kinds of models, e.g. yolo26 yoloe-26')
    drun.add_argument('--resume', help='carry on with an earlier desk run: path to its .csv')
    args = p.parse_args(argv)

    cfg = load_config(args.config, quick=getattr(args, 'quick', False))
    paths = {k: REPO / v for k, v in cfg['paths'].items()}
    if args.command == 'report':
        print(write_report(cfg, paths['results']))
        return
    setup_ultralytics(paths['data'], paths['models'])
    if args.command == 'desk':
        {'prepare': cmd_desk_prepare, 'run': cmd_desk_run}[args.desk_command](args, cfg, paths)
        return
    {'run': cmd_run, 'prepare': cmd_prepare}[args.command](args, cfg, paths)


if __name__ == '__main__':
    main()
