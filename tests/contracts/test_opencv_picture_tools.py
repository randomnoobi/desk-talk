"""The real picture tools (OpenCV) do what every PictureTools must do."""

from __future__ import annotations

import pytest

from desktalk.adapters.picture.opencv_tools import OpenCvPictureTools
from desktalk.ports import PictureTools
from tests.contracts.picture_tools_contract import PictureToolsContract


class TestOpenCvPictureToolsPassContract(PictureToolsContract):
    @pytest.fixture
    def tools(self):
        return OpenCvPictureTools()


def test_opencv_picture_tools_are_picture_tools():
    assert isinstance(OpenCvPictureTools(), PictureTools)
