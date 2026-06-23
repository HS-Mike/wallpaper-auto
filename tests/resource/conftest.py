from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.models import ConfigModel
from wallpaper_auto.resource.base_resource import BaseResource


@pytest.fixture
def mock_screen_size():
    with patch(
        "wallpaper_auto.resource.static_wallpaper.get_screen_size", return_value=(1920, 1080)
    ):
        yield


@pytest.fixture(autouse=True)
def _auto_config_store():
    """Ensure ConfigStore is available for every test that creates StaticWallpaper."""
    if not ConfigStore.has_instance():
        ConfigStore.clear_instance()
        store = ConfigStore()
        store.config = ConfigModel(
            resource={"a": {"name": "static_wallpaper", "config": {"path": "dummy"}}},
            trigger=[],
            rule=[],
            fallback="a",
        )
    yield


@pytest.fixture
def mock_mount_deps():
    with (
        patch("wallpaper_auto.resource.static_wallpaper.set_wallpaper") as mock_set,
        patch(
            "wallpaper_auto.resource.static_wallpaper.get_current_wallpaper",
            return_value="C:\\original.jpg",
        ),
        patch(
            "wallpaper_auto.resource.static_wallpaper.get_screen_size",
            return_value=(1920, 1080),
        ),
    ):
        yield mock_set


@pytest.fixture
def config_store_with_cache(tmp_path):
    """Set up a ConfigStore with a cache directory for tests that need caching."""
    ConfigStore.clear_instance()
    cache_dir = tmp_path / "wallpaper_cache"
    cache_dir.mkdir()
    store = ConfigStore()
    store.config = ConfigModel(
        resource={"a": {"name": "static_wallpaper", "config": {"path": "dummy"}}},
        trigger=[],
        rule=[],
        fallback="a",
        cache=str(cache_dir),
    )
    yield
    ConfigStore.clear_instance()


# ── Shared fixtures (patch resource_carousel for ResourceCarousel tests) ──


@pytest.fixture
def mock_carousel_deps():
    """Patch ResourceCarousel's own wallpaper reads (not sub-resource calls)."""
    with (
        patch(
            "wallpaper_auto.resource.resource_carousel.get_current_wallpaper",
            return_value="C:\\original.jpg",
        ),
        patch(
            "wallpaper_auto.resource.resource_carousel.get_current_wallpaper_style",
            return_value=("10", "0"),
        ),
    ):
        yield


@pytest.fixture
def mock_sub_resources():
    """Create 3 mock BaseResource instances for carousel testing."""
    return [MagicMock(spec=BaseResource) for _ in range(3)]
