"""
Where pictures come from (each one is a FrameSource, desktalk/ports.py):

    PiFrameSource     the Raspberry Pi sends pictures (and what its detector found) to /api/pi/frame
    PhoneFrameSource  the phone page sends pictures to /api/phone/frame (HTTPS)
    WebcamSource      a webcam plugged into this computer (OpenCV)

make_pair_key() gives the key the Pi and the phone must send with each picture.
"""

from desktalk.adapters.camera.pairing import make_pair_key
from desktalk.adapters.camera.phone_source import PhoneFrameSource
from desktalk.adapters.camera.pi_source import PiFrameSource
from desktalk.adapters.camera.webcam_source import WebcamSource, open_webcam

__all__ = ['PhoneFrameSource', 'PiFrameSource', 'WebcamSource', 'make_pair_key', 'open_webcam']
