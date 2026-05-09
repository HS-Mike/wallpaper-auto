"""Tests for base_resource.py — BaseResource abstract class and CachedResource lifecycle."""

import os
import shutil

import pytest

from wallpaper_automator.resource.base_resource import BaseResource
from wallpaper_automator.resource.static_wallpaper import CachedResource


class MockCached(CachedResource):
    """Concrete CachedResource subclass for testing."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestBaseResource:
    def test_abstract_methods(self):
        """BaseResource cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseResource.__new__(BaseResource)  # type: ignore[no-untyped-call]

    def test_cached_resource_creates_temp_dir(self):
        """CachedResource with no cache_dir creates an auto temp dir."""
        res = MockCached()
        path = res.cache_dir
        try:
            assert os.path.exists(path)
            assert os.path.isdir(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_cached_resource_uses_custom_dir(self, tmp_path):
        """CachedResource with cache_dir uses the specified path."""
        custom = str(tmp_path / "my_cache")
        res = MockCached(cache_dir=custom)
        assert res.cache_dir == custom
        assert os.path.exists(custom)
        assert os.path.isdir(custom)

    def test_cached_resource_unique_temp_dirs(self):
        """Each instance without cache_dir gets a unique temp dir."""
        res1 = MockCached()
        res2 = MockCached()
        try:
            assert res1.cache_dir != res2.cache_dir
        finally:
            shutil.rmtree(res1.cache_dir, ignore_errors=True)
            shutil.rmtree(res2.cache_dir, ignore_errors=True)
