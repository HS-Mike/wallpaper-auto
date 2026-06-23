"""Tests for base_resource.py — BaseResource abstract class."""

import pytest

from wallpaper_auto.resource.base_resource import BaseResource


class MockResource(BaseResource):
    """Concrete BaseResource subclass for testing abstract methods."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestBaseResource:
    def test_abstract_methods(self):
        """BaseResource cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseResource.__new__(BaseResource)  # type: ignore[no-untyped-call]
