from unittest.mock import MagicMock, patch

import pytest

from wallpaper_automator.resource.base_resource import BaseResource


@pytest.fixture
def mock_screen_size():
    with patch(
        "wallpaper_automator.resource.static_wallpaper.get_screen_size", return_value=(1920, 1080)
    ):
        yield


@pytest.fixture
def mock_mount_deps():
    with (
        patch("wallpaper_automator.resource.static_wallpaper.set_wallpaper") as mock_set,
        patch(
            "wallpaper_automator.resource.static_wallpaper.get_current_wallpaper",
            return_value="C:\\original.jpg",
        ),
        patch(
            "wallpaper_automator.resource.static_wallpaper.get_screen_size",
            return_value=(1920, 1080),
        ),
    ):
        yield mock_set


# ── Shared fixtures (patch resource_carousel for ResourceCarousel tests) ──


@pytest.fixture
def mock_carousel_deps():
    """Patch ResourceCarousel's own wallpaper reads (not sub-resource calls)."""
    with (
        patch(
            "wallpaper_automator.resource.resource_carousel.get_current_wallpaper",
            return_value="C:\\original.jpg",
        ),
        patch(
            "wallpaper_automator.resource.resource_carousel.get_current_wallpaper_style",
            return_value=("10", "0"),
        ),
    ):
        yield


@pytest.fixture
def mock_sub_resources():
    """Create 3 mock BaseResource instances for carousel testing."""
    return [MagicMock(spec=BaseResource) for _ in range(3)]
