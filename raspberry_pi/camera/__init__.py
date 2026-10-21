"""
Desk Talk's camera program: runs on the Raspberry Pi's own Python 3 (3.11 or newer).

    python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key AbC123xY

(run it in the desk-talk folder; server.py prints the exact command, with your computer's address and key)

Standard library only, plus picamera2, cv2 and yaml from apt (imported inside the function that
uses them), plus desktalk_shared straight from this repo's shared/ folder.
"""

import sys
from pathlib import Path

# desktalk_shared (the upload format, the detector's API) comes straight from this repo's shared/ folder
SHARED = str(Path(__file__).resolve().parents[2] / 'shared')
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
