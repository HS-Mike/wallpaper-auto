"""Tests for resource_manager.py — ResourceManager static API."""

from unittest.mock import patch

import pytest

from wallpaper_auto.models import ResourceConfig, SceneBinding
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import _BUILTIN_RESOURCES, ResourceManager
from wallpaper_auto.util.display_utils import DisplayInfo


def _make_display_info(
    device_path: str,
    model: str | None = "Monitor",
    x: int = 0,
) -> DisplayInfo:
    """Create a DisplayInfo with sensible defaults for testing."""
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        monitor_device_path=device_path,
        model=model,
        source_resolution=(1920, 1080),
        position=(x, 0),
        target_resolution=(1920, 1080),
    )


class TestResourceManagerInit:
    """ResourceManager class-level defaults"""

    def test_builtin_resources_are_registered(self):
        builtin_keys = set(_BUILTIN_RESOURCES.keys())
        assert ResourceManager._support_resources.keys() >= builtin_keys

    def test_class_support_resources_is_copy_of_builtin(self):
        assert ResourceManager._support_resources is not _BUILTIN_RESOURCES


class TestResourceManagerRegisterResource:
    """ResourceManager.register_resource class method."""

    def test_register_custom_subclass_succeeds(self):
        cls = type(
            "CustomResource",
            (BaseResource,),
            {"mount": lambda s: None, "demount": lambda s: None},
        )
        with patch.dict(ResourceManager._support_resources, clear=False):
            ResourceManager.register_resource("test", cls)
            assert ResourceManager._support_resources["test"] is cls

    def test_register_non_subclass_class_error(self):
        """A class not inheriting from BaseResource raises ValueError."""
        with patch.dict(ResourceManager._support_resources, clear=False):
            with pytest.raises(ValueError, match="resource cls must inherit from BaseResource"):
                ResourceManager.register_resource("bad", object)  # type: ignore


class TestResourceManagerEvaluateTarget:
    """ResourceManager.evaluate_target() — resolving targets to per-display resources."""

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_resource_creates_per_display_instance(self, mock_cs, mock_get_display, tmp_path):
        """A resource target creates one instance per connected display."""
        mock_get_display.return_value = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", x=0),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B", x=1920),
        ]
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1")

        assert len(result) == 2
        assert r"\\?\DISPLAY#A#{path-a}" in result
        assert r"\\?\DISPLAY#B#{path-b}" in result
        assert all(isinstance(v, BaseResource) for v in result.values())

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_unknown_target_raises(self, mock_cs, mock_get_display):
        """An unknown target name raises ValueError."""
        mock_get_display.return_value = []
        mock_cs.instance.resource = {}
        mock_cs.instance.scene = {}

        with pytest.raises(ValueError, match="target unknown not found"):
            ResourceManager.evaluate_target("unknown")

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_scene_resolves_per_display(self, mock_cs, mock_get_display, tmp_path):
        """A scene target uses the scene binding to map each display to a resource."""
        mock_get_display.return_value = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Dell U27")
        ]
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }
        mock_cs.instance.scene = {"office": [SceneBinding(display_model="Dell.*", resource="wp1")]}

        result = ResourceManager.evaluate_target("office")

        assert r"\\?\DISPLAY#A#{path-a}" in result

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_no_displays_returns_empty(self, mock_cs, mock_get_display):
        """No connected displays yields an empty dict."""
        mock_get_display.return_value = []
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": "/fake/path.png", "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1")

        assert result == {}

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_query_failure_returns_none(self, mock_cs, mock_get_display):
        """get_display_info returning None yields None (caller skips the target)."""
        mock_get_display.return_value = None
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": "/fake/path.png", "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1")

        assert result is None


class TestResourceManagerEvaluateScene:
    """ResourceManager.evaluate_scene() — mapping displays to resources by model."""

    def test_matches_by_model_pattern(self):
        binding = SceneBinding(display_model="Dell.*", resource="dell_wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {r"\\?\DISPLAY#1#{path}": "dell_wp"}

    def test_no_match_returns_empty(self):
        binding = SceneBinding(display_model="NonExistent", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_none_model_is_skipped(self):
        """A display with model=None does not match any pattern."""
        binding = SceneBinding(display_model=".*", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model=None)  # type: ignore[arg-type]

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_multiple_displays_first_match_wins(self):
        """When one binding matches multiple displays, only the first wins."""
        binding = SceneBinding(display_model="Monitor", resource="wp")
        displays = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Monitor A"),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B"),
        ]

        result = ResourceManager.evaluate_scene([binding], displays)

        assert result == {r"\\?\DISPLAY#A#{path-a}": "wp"}
