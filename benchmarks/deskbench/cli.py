"""
deskbench: time YOLO models on this computer or on the Raspberry Pi.

    uv run deskbench run                 # every combination in config/benchmark.yaml
    uv run deskbench run --quick         # a few pictures each, just to check everything works
    uv run deskbench run --models yolo11n --resume benchmarks/results/pi/<run>.csv
    uv run deskbench prepare             # only download and convert the models
    uv run deskbench report              # rebuild benchmarks/results/README.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import models, probes, report, results
from .matrix import build_combos
from .measure import wait_until_cool
from .runner import run_all

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


def make_run_combo(cfg: dict, models_dir: Path, data_dir: Path, pictures: list[Path], log):
    speed, acc = cfg['speed'], cfg['accuracy']
    dataset = accuracy_dataset(cfg, data_dir) if acc['enabled'] else None

    def run_combo(combo) -> dict:
        path = models.ensure_model(combo, models_dir, log)
        spec = {'model_path': str(path), 'format': combo.fmt, 'image_size': combo.image_size,
                'pictures': [str(p) for p in pictures], 'warmup': speed['warmup_pictures'],
                'timed': speed['timed_pictures'], 'confidence': speed['confidence'],
                'torch_device': speed['torch_device'], 'sample_every_sec': speed['sample_every_sec'],
                'accuracy': {'dataset': dataset, 'runs_dir': str(data_dir / 'runs')} if dataset else None}
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


def write_report(cfg: dict, results_dir: Path) -> Path:
    rows = results.all_rows(results_dir)
    path = results_dir / 'README.md'
    r = cfg['report']
    path.write_text(report.render(rows, r['camera_fps'], r['min_accuracy_share'], r['pick_formats']),
                    encoding='utf-8')
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

    combos = build_combos(cfg, args.models)
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
    for combo in build_combos(cfg, args.models):
        try:
            models.ensure_model(combo, paths['models'])
        except Exception as exc:
            failed += 1
            print(f'{combo.key}: FAILED {type(exc).__name__}: {exc}')
    print(f'Models are in {paths["models"]} ({failed} failed)')


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog='deskbench', description='Time YOLO models on this computer or the Pi.')
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='time every combination and save the results')
    run.add_argument('--device', help='name for this machine in the results (default: pi or laptop, guessed)')
    run.add_argument('--models', nargs='+', help='only these models, e.g. yolo11n yolo11s')
    run.add_argument('--resume', help='carry on with an earlier run: path to its .csv')
    run.add_argument('--quick', action='store_true', help='a few pictures each; results go to data/bench, not git')
    prep = sub.add_parser('prepare', help='only download and convert the models')
    prep.add_argument('--models', nargs='+')
    sub.add_parser('report', help='rebuild benchmarks/results/README.md')
    args = p.parse_args(argv)

    cfg = load_config(args.config, quick=getattr(args, 'quick', False))
    paths = {k: REPO / v for k, v in cfg['paths'].items()}
    if args.command == 'report':
        print(write_report(cfg, paths['results']))
        return
    setup_ultralytics(paths['data'], paths['models'])
    {'run': cmd_run, 'prepare': cmd_prepare}[args.command](args, cfg, paths)


if __name__ == '__main__':
    main()
