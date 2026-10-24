"""
What Desk Talk does to pictures, with OpenCV (each one implements an interface in desktalk/ports.py):

    OpenCvPictureTools  read and write JPEGs, shrink, gray, crop, and the numbers Lens checks (PictureTools)
"""

from desktalk.adapters.picture.opencv_tools import OpenCvPictureTools

__all__ = ['OpenCvPictureTools']
