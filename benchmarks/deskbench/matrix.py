"""Every combination of model, picture size, format and precision to try."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Precisions each format can do (fp16 only for ncnn, int8 only for onnx).
ALLOWED = {'pt': {'fp32'}, 'ncnn': {'fp32', 'fp16'}, 'onnx': {'fp32', 'int8'}}


@dataclass(frozen=True)
class Combo:
    model: str
    image_size: int
    fmt: str
    precision: str

    @property
    def key(self) -> str:
        return f'{self.model}-{self.image_size}-{self.fmt}-{self.precision}'

    def model_path(self, models_dir: Path) -> Path:
        """Where this combination's model file (or ncnn folder) lives."""
        if self.fmt == 'pt':
            return models_dir / f'{self.model}.pt'
        if self.fmt == 'ncnn':
            # Ultralytics recognises an ncnn model by the "_ncnn_model" ending.
            return models_dir / f'{self.model}_{self.image_size}_{self.precision}_ncnn_model'
        return models_dir / f'{self.model}_{self.image_size}_{self.precision}.{self.fmt}'


NAMES = ('coco', 'objects365', 'words')     # which names a kind of model uses (see desk.kinds in the config)


def kind_of(cfg: dict, model: str) -> tuple[str, dict]:
    """Which kind (yolo11, yoloe-26, ...) a model belongs to, and that kind's settings."""
    for name, kind in cfg['model_kinds'].items():
        if model in kind['models']:
            return name, kind
    raise KeyError(f'{model} is not in model_kinds')


def _check(name: str, kind: dict) -> None:
    if kind['names'] not in NAMES:
        raise ValueError(f'{name}: names must be one of: {", ".join(NAMES)}')
    for fmt, precisions in kind['formats'].items():
        if fmt not in ALLOWED:
            raise ValueError(f'Unknown format "{fmt}". Use one of: {", ".join(ALLOWED)}')
        bad = set(precisions) - ALLOWED[fmt]
        if bad:
            raise ValueError(f'{fmt} can\'t do {", ".join(sorted(bad))}. It can do: {", ".join(sorted(ALLOWED[fmt]))}')


def build_combos(cfg: dict, only_models: list[str] | None = None, only_kinds: list[str] | None = None) -> list[Combo]:
    combos = []
    for name, kind in cfg['model_kinds'].items():
        _check(name, kind)
        if only_kinds and name not in only_kinds:
            continue
        combos += [Combo(m, int(size), fmt, prec)
                   for m in kind['models'] if not only_models or m in only_models
                   for size in cfg['image_sizes']
                   for fmt, precisions in kind['formats'].items()
                   for prec in precisions]
    return combos
