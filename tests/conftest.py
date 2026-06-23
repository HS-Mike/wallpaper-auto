"""Shared fixtures for all wallpaper-auto tests."""

import pytest

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.models import ConfigModel


@pytest.fixture(autouse=True)
def _reset_config_store() -> None:
    """Reset the ConfigStore singleton and create a default instance."""
    ConfigStore.clear_instance()
    store = ConfigStore()
    store.config = ConfigModel(
        resource={"a": {"name": "static_wallpaper", "config": {"path": "dummy"}}},
        trigger=[],
        rule=[],
        fallback="a",
    )
