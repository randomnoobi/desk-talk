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


def build_combos(cfg: dict, only_models: list[str] | None = None) -> list[Combo]:
    for fmt, precisions in cfg['formats'].items():
        if fmt not in ALLOWED:
            raise ValueError(f'Unknown format "{fmt}". Use one of: {", ".join(ALLOWED)}')
        bad = set(precisions) - ALLOWED[fmt]
        if bad:
            raise ValueError(f'{fmt} can\'t do {", ".join(sorted(bad))}. It can do: {", ".join(sorted(ALLOWED[fmt]))}')
    models = [m for m in cfg['models'] if not only_models or m in only_models]
    return [Combo(m, int(size), fmt, prec)
            for m in models
            for size in cfg['image_sizes']
            for fmt, precisions in cfg['formats'].items()
            for prec in precisions]
