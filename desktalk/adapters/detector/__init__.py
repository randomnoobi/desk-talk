"""Object detection (a Detector, desktalk/ports.py): UltralyticsDetector (YOLO in this app) or NoDetector (none)."""

from desktalk.adapters.detector.no_detector import NoDetector
from desktalk.adapters.detector.ultralytics_detector import UltralyticsDetector, load_yolo, pick_device

__all__ = ['NoDetector', 'UltralyticsDetector', 'load_yolo', 'pick_device']
