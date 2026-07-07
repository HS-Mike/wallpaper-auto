from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.models import ConfigModel
from wallpaper_auto.resource.base_resource import BaseResource


_TEST_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


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
            fallback_target="a",
        )
    yield


@pytest.fixture
def mock_sub_resources():
    """Create 3 mock BaseResource instances for resource cycle testing."""
    return [MagicMock(spec=BaseResource) for _ in range(3)]
