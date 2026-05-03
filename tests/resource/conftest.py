from unittest.mock import patch

import pytest


@pytest.fixture
def mock_screen_size():
    with patch("wallpaper_automator.resource.static_wallpaper.get_screen_size", return_value=(1920, 1080)):
        yield


@pytest.fixture
def mock_mount_deps():
    with (
        patch("wallpaper_automator.resource.static_wallpaper.set_wallpaper") as mock_set,
        patch("wallpaper_automator.resource.static_wallpaper.get_current_wallpaper", return_value="C:\\original.jpg"),
        patch("wallpaper_automator.resource.static_wallpaper.get_screen_size", return_value=(1920, 1080)),
    ):
        yield mock_set


# ── Shared fixtures (patch dynamic_wallpaper for DynamicWallpaper tests) ───

@pytest.fixture
def mock_utils_screen_size():
    with patch("wallpaper_automator.resource.dynamic_wallpaper.get_screen_size", return_value=(1920, 1080)):
        yield


@pytest.fixture
def mock_utils_mount_deps():
    with (
        patch("wallpaper_automator.resource.dynamic_wallpaper.set_wallpaper") as mock_set,
        patch("wallpaper_automator.resource.dynamic_wallpaper.get_current_wallpaper", return_value="C:\\original.jpg"),
        patch("wallpaper_automator.resource.dynamic_wallpaper.get_screen_size", return_value=(1920, 1080)),
    ):
        yield mock_set
