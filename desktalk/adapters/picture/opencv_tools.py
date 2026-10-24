"""The picture tools core uses (desktalk.ports.PictureTools), done with OpenCV. Core never imports cv2 itself."""

from __future__ import annotations

import math

import cv2
import numpy as np

from desktalk.domain import Box, Picture, Size


class OpenCvPictureTools:
    """Pictures are numpy arrays: BGR colour (height x width x 3) or one gray channel (height x width)."""

    def decode_jpeg(self, jpeg: bytes, *, gray: bool = False) -> Picture | None:
        if not jpeg:
            return None
        flag = cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR
        return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), flag)

    def encode_jpeg(self, picture: Picture, quality: int) -> bytes | None:
        if picture is None or picture.size == 0:
            return None
        ok, data = cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return data.tobytes() if ok else None

    def size(self, picture: Picture) -> Size:
        height, width = picture.shape[:2]
        return Size(width=int(width), height=int(height))

    def shrink(self, picture: Picture, width: int, height: int | None = None) -> Picture:
        h, w = picture.shape[:2]
        if height is None:
            height = max(1, round(h * width / max(1, w)))
        return cv2.resize(picture, (width, height), interpolation=cv2.INTER_AREA)

    def to_gray(self, picture: Picture) -> Picture:
        return picture if picture.ndim == 2 else cv2.cvtColor(picture, cv2.COLOR_BGR2GRAY)

    def crop(self, picture: Picture, box: Box) -> Picture:
        h, w = picture.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(w, int(box[2])), min(h, int(box[3]))
        return picture[y1:y2, x1:x2]

    def brightness(self, gray: Picture) -> float:
        return float(gray.mean())

    def glare(self, gray: Picture, white_level: float) -> float:
        return float((gray >= white_level).mean())

    def difference(self, a: Picture, b: Picture) -> float:
        if a.shape != b.shape:
            return math.inf
        return float(cv2.absdiff(a, b).mean())

    def sharpness(self, gray: Picture) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
