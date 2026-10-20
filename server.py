"""
Desk Talk: start everything on this computer.

    python server.py

Then open http://localhost:5000. Settings are in config/settings.yaml.
The Raspberry Pi side is raspberry_pi/camera_node.py (the command to run on the Pi is
printed when this starts).
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

if __name__ == '__main__':
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    from desktalk.app import main
    main()
