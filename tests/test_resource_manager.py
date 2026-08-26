"""Tests for resource_manager.py — ResourceManager static API."""

from unittest.mock import patch

import pytest

from wallpaper_auto.models import ResourceConfig, SceneBinding, SceneConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import _BUILTIN_RESOURCES, DisplayScene, ResourceManager
from wallpaper_auto.util.display_util import LUID, DisplayInfo


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
        adapter_id=LUID(1, 2),
        source_id=3,
        scale=100,
    )


class TestResourceManagerInit:
    """ResourceManager class-level defaults."""

    def test_builtin_resources_are_registered(self):
        builtin_keys = set(_BUILTIN_RESOURCES.keys())
        assert ResourceManager._support_resources.keys() >= builtin_keys

    def test_support_resources_are_a_copy_of_builtin(self):
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

    def test_register_non_subclass_raises(self):
        with patch.dict(ResourceManager._support_resources, clear=False):
            with pytest.raises(ValueError, match="resource cls must inherit from BaseResource"):
                ResourceManager.register_resource("bad", object)  # type: ignore


class TestResourceManagerEvaluateTarget:
    """ResourceManager.evaluate_target() — resolving targets to per-display resources."""

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_resource_creates_per_display_instance(self, mock_cs, tmp_path):
        displays = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", x=0),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B", x=1920),
        ]
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1", displays)

        assert len(result) == 2
        assert all(isinstance(v, DisplayScene) for v in result)
        assert all(isinstance(v.resource, BaseResource) for v in result)
        assert {v.display_id for v in result} == {d.display_id for d in displays}

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_unknown_target_raises(self, mock_cs):
        mock_cs.instance.resource = {}
        mock_cs.instance.scene = {}

        with pytest.raises(ValueError, match="target unknown not found"):
            ResourceManager.evaluate_target("unknown", [])

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_scene_resolves_per_display(self, mock_cs, tmp_path):
        display = _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Dell U27")
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": str(tmp_path / "test.png"), "style": "fill"},
            )
        }
        mock_cs.instance.scene = {
            "office": SceneConfig(
                bindings=[SceneBinding(match_display_model="Dell.*", resource="wp1")]
            )
        }

        result = ResourceManager.evaluate_target("office", [display])

        assert any(v.display_id == display.display_id for v in result)

    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_no_displays_returns_empty(self, mock_cs):
        mock_cs.instance.resource = {
            "wp1": ResourceConfig(
                name="static_wallpaper",
                config={"path": "/fake/path.png", "style": "fill"},
            )
        }

        result = ResourceManager.evaluate_target("wp1", [])

        assert result == []


class TestResourceManagerEvaluateScene:
    """ResourceManager.evaluate_scene() — mapping displays to resources by model."""

    def test_matches_by_model_pattern(self):
        binding = SceneBinding(match_display_model="Dell.*", resource="dell_wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {display.display_id: binding}

    def test_no_match_returns_empty(self):
        binding = SceneBinding(display_model="NonExistent", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model="Dell U2719D")

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_none_model_is_skipped(self):
        binding = SceneBinding(match_display_model=".*", resource="wp")
        display = _make_display_info(r"\\?\DISPLAY#1#{path}", model=None)  # type: ignore[arg-type]

        result = ResourceManager.evaluate_scene([binding], [display])

        assert result == {}

    def test_multiple_displays_first_match_wins(self):
        binding = SceneBinding(match_display_model="Monitor", resource="wp")
        displays = [
            _make_display_info(r"\\?\DISPLAY#A#{path-a}", model="Monitor A"),
            _make_display_info(r"\\?\DISPLAY#B#{path-b}", model="Monitor B"),
        ]

        result = ResourceManager.evaluate_scene([binding], displays)

        assert result == {displays[0].display_id: binding}
