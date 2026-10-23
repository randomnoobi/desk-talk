"""Object detection on this computer (a Detector, desktalk/ports.py): UltralyticsDetector."""

from desktalk.adapters.detector.ultralytics_detector import UltralyticsDetector, load_yolo, pick_device

__all__ = ['UltralyticsDetector', 'load_yolo', 'pick_device']
