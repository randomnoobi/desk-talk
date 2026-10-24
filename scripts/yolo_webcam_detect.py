#!/usr/bin/env python3
"""
Run Desk Talk's object detection on a webcam on this computer and show the boxes in a window.
The model, confidence and webcam come from config/settings.yaml (detector: and camera:).

    uv run python scripts/yolo_webcam_detect.py
    uv run python scripts/yolo_webcam_detect.py --index 1    # another webcam

Press q to quit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import cv2

    from desktalk.adapters.detector import UltralyticsDetector
    from desktalk.adapters.picture.painter import Pen
    from desktalk.config import load_config

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--index', type=int, help='which webcam (default: the first in camera.webcam_indices)')
    a = p.parse_args()
    config = load_config()
    s = config.detector
    detector = UltralyticsDetector(config.resolve(s.model), s, end2end=s.end2end, rect=s.rect)
    print(f'Detector: {detector.label}')
    index = config.camera.webcam_indices[0] if a.index is None else a.index
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit(f'Could not open webcam {index}. Try --index 1 or 2.')
    pen = Pen(config.drawing)
    print('Press q to quit.')
    while True:
        ok, picture = cap.read()
        if not ok:
            break
        cv2.imshow('Desk Talk detection', pen.boxes(picture, detector.detect(picture)))
        if (cv2.waitKey(1) & 0xFF) == ord('q'):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
