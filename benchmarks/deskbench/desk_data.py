"""
Downloads for the desk test: the LVIS labels (64 MB, once), COCO's own val2017 labels
(48 MB, once), and the chosen photos one by one (not the 20 GB of all COCO photos).
Everything goes to data/bench/desk, which isn't in git.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

import yaml


def _download(url: str, target: Path, how: dict) -> None:
    """how: desk.downloads from the config (timeout_sec, tries)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.tmp')
    for attempt in range(1, how['tries'] + 1):
        try:
            with urllib.request.urlopen(url, timeout=how['timeout_sec']) as r, open(tmp, 'wb') as f:
                shutil.copyfileobj(r, f)
            tmp.replace(target)
            return
        except OSError:
            tmp.unlink(missing_ok=True)
            if attempt == how['tries']:
                raise


def lvis_val(folder: Path, how: dict) -> dict:
    path = folder / 'lvis_v1_val.json'
    if not path.exists():
        print(f'downloading the LVIS labels to {folder} (64 MB, once)')
        zipped = folder / 'lvis_v1_val.json.zip'
        _download(how['lvis_labels'], zipped, how)
        with zipfile.ZipFile(zipped) as z:
            z.extract('lvis_v1_val.json', folder)
        zipped.unlink()
    return json.loads(path.read_text())


def coco_val_labels(folder: Path, how: dict) -> dict[int, list[tuple[str, list[float]]]]:
    """COCO's own val2017 labels: picture id → [(name, [x, y, w, h] as shares of the picture)]."""
    zipped = folder / 'coco2017labels.zip'
    if not zipped.exists():
        print(f'downloading the COCO labels to {folder} (48 MB, once)')
        _download(how['coco_labels'], zipped, how)
    import ultralytics
    names = yaml.safe_load((Path(ultralytics.__file__).parent / 'cfg/datasets/coco.yaml').read_text())['names']
    labels = {}
    with zipfile.ZipFile(zipped) as z:
        for member in z.namelist():
            if '/labels/val2017/' not in member or not member.endswith('.txt'):
                continue
            rows = [line.split() for line in z.read(member).decode().splitlines() if line.strip()]
            labels[int(Path(member).stem)] = [
                (names[int(r[0])], [float(r[1]) - float(r[3]) / 2, float(r[2]) - float(r[4]) / 2,
                                    float(r[3]), float(r[4])]) for r in rows]
    return labels


def download_photos(pictures: list[dict], folder: Path, how: dict, log: Callable[[str], None] = print) -> int:
    """Fetch the photos that aren't here yet. Returns how many couldn't be fetched."""
    failed = 0
    for p in pictures:
        target = folder / f'{p["id"]}.jpg'
        if target.exists():
            continue
        try:
            _download(p['url'], target, how)
        except OSError as exc:
            failed += 1
            log(f'  could not fetch {p["url"]}: {exc}')
    return failed
