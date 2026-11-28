"""
Where pictures come from (each one is a FrameSource, desktalk/ports.py):

    PiFrameSource     the Raspberry Pi sends pictures (and what its detector found) to /api/pi/frame
    WebcamSource      a webcam plugged into this computer (OpenCV)

make_pair_key() gives the key the Pi must send with each picture.
"""

from desktalk.adapters.camera.pairing import make_pair_key
from desktalk.adapters.camera.pi_source import PiFrameSource
from desktalk.adapters.camera.webcam_source import WebcamSource, open_webcam

__all__ = ['PiFrameSource', 'WebcamSource', 'make_pair_key', 'open_webcam']
