"""python3 -m raspberry_pi.camera (in the desk-talk folder) starts the camera program. --help shows the flags."""

import sys

from raspberry_pi.camera.main import main

sys.exit(main())
