#!/usr/bin/env python3
"""
Show a webcam on this computer in a window, to check it works and where it points.

    uv run python scripts/camera_preview.py              # the webcam in config/settings.yaml (camera.webcam_indices)
    uv run python scripts/camera_preview.py --index 1    # another webcam

Press q to quit, s to save a picture in this folder.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import cv2

    from desktalk.config import load_config

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--index', type=int, help='which webcam (default: the first in camera.webcam_indices)')
    a = p.parse_args()
    camera = load_config().camera
    index = camera.webcam_indices[0] if a.index is None else a.index
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit(f'Could not open webcam {index}. Try --index 1 or 2.')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera.webcam_width)        # not every webcam takes this size
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera.webcam_height)
    print('Press q to quit, s to save a picture.')
    while True:
        ok, picture = cap.read()
        if not ok:
            print('Could not read a picture from the webcam; stopping.')
            break
        cv2.imshow('Camera preview', picture)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        if key == ord('s'):
            name = f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(name, picture)
            print(f'Saved {name}')
    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
