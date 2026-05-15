from unittest.mock import patch

import pytest


@pytest.fixture
def mock_win32():
    with (
        patch("wallpaper_auto.trigger.windows_session_trigger.win32gui") as gui,
        patch("wallpaper_auto.trigger.windows_session_trigger.win32ts") as ts,
    ):
        yield gui, ts
