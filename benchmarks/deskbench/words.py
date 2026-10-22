"""
yoloe models find whatever words they're given. Here the words (Sam's desk things, from
desk.kinds in config/benchmark.yaml) are built into a model file once, so it loads and
converts like any other model, and the app can use it too.

Building the words in needs the `clip` package (Mac only: uv sync --group words). Each
step runs in its own process (python -m deskbench.words ...): Ultralytics can only
convert a yoloe model once per process, a second try fails.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

NO_CLIP = ('yoloe needs its words made on the Mac: cd benchmarks && uv sync --group words && '
           'uv run deskbench prepare --kinds yoloe-26, then copy models/bench to the Pi')


def desk_words(desk_cfg: dict) -> list[str]:
    """Every desk kind's words, then the extra words, lowercase and without repeats."""
    words = [w for kind in desk_cfg['kinds'].values() for w in kind.get('words') or []]
    words += desk_cfg.get('yoloe_extra_words') or []
    return list(dict.fromkeys(w.lower() for w in words))


def words_model_path(models_dir: Path, model: str) -> Path:
    return models_dir / f'{model}-desk.pt'


def stamp_path(models_dir: Path, model: str) -> Path:
    """Lists the words a yoloe model was made with, so changed words make it again."""
    return models_dir / f'{model}-desk.words.json'


def can_make_words() -> bool:
    """Only where the `clip` package is installed (the Mac, with uv sync --group words)."""
    return importlib.util.find_spec('clip') is not None


def clear_if_changed(models_dir: Path, model: str, words: list[str], can_make: bool = True) -> bool:
    """Delete a yoloe model's files (and everything converted from it) if they were made with other words.
    Where they can't be made again (the Pi), stop instead of deleting the copies from the Mac."""
    stamp = stamp_path(models_dir, model)
    try:
        if json.loads(stamp.read_text()) == words:
            return False
    except (OSError, ValueError):
        pass
    if not can_make and words_model_path(models_dir, model).exists():
        raise RuntimeError(f'{model} was made with other words than desk.kinds in this config. Use the same '
                           f'config as the Mac (git pull), or make the models again on the Mac and copy them over.')
    for path in [words_model_path(models_dir, model), stamp, *models_dir.glob(f'{model}_*_ncnn_model'),
                 *models_dir.glob(f'{model}_*.onnx')]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return True


def _make(base: str, words_file: str, target: str) -> None:
    try:
        import clip  # noqa: F401  (Ultralytics' yoloe needs it to read the words)
    except ImportError:
        sys.exit(NO_CLIP)
    from ultralytics import YOLOE
    words = json.loads(Path(words_file).read_text())
    model = YOLOE(base)
    model.set_classes(words, model.get_text_pe(words))
    tmp = Path(target).with_suffix('.tmp.pt')
    model.save(str(tmp))
    tmp.replace(target)


def _export(words_pt: str, fmt: str, image_size: str, half: str, target: str) -> None:
    from ultralytics import YOLOE
    exported = Path(YOLOE(words_pt).export(format=fmt, imgsz=int(image_size), half=half == '1',
                                           device='cpu', verbose=False))
    tmp = Path(target + '.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.move(str(exported), str(tmp))
    tmp.rename(target)


if __name__ == '__main__':
    {'make': _make, 'export': _export}[sys.argv[1]](*sys.argv[2:])
