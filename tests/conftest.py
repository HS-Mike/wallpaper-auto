"""Shared fixtures for all wallpaper-auto tests."""

import pytest

from wallpaper_auto.config_store import ConfigStore


@pytest.fixture(autouse=True)
def _reset_config_store() -> None:
    """Reset the ConfigStore singleton before each test."""
    ConfigStore.clear_instance()
