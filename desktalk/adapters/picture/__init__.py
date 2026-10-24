"""
What Desk Talk does to pictures, with OpenCV (each one implements an interface in desktalk/ports.py):

    OpenCvPictureTools  read and write JPEGs, shrink, gray, crop, and the numbers Lens checks (PictureTools)
    DeskPainter         the live view with boxes and the desk overlays (LiveViewPainter)
"""

from desktalk.adapters.picture.opencv_tools import OpenCvPictureTools
from desktalk.adapters.picture.painter import DeskPainter

__all__ = ['DeskPainter', 'OpenCvPictureTools']
