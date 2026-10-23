"""Builds the detector service from its settings: the only place that knows all the real parts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from desktalk_detector.app import create_app
from desktalk_detector.manager import ModelManager
from desktalk_detector.model_folder import ModelFolder, ultralytics_yolo
from desktalk_detector.settings import ServiceSettings


def build_app(settings: ServiceSettings, yolo: Callable[..., Any] = ultralytics_yolo) -> FastAPI:
    manager = ModelManager(ModelFolder(settings.models_dir, yolo), retry_first_sec=settings.retry_first_sec,
                           retry_max_sec=settings.retry_max_sec)
    return create_app(manager, max_picture_bytes=settings.max_picture_bytes)
