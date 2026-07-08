"""Tests for resource_manager.py — ResourceManager static API."""

from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.models import ResourceConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource_manager import _BUILTIN_RESOURCES, ResourceManager


_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


def _mock_resource():
    """Create a MagicMock that conforms to the BaseResource interface."""
    return MagicMock(spec=BaseResource)


class TestResourceManagerInit:
    """ResourceManager class-level defaults"""

    def test_builtin_resources_are_registered(self):
        """The class-level _support_resources dict contains all built-in resources."""
        builtin_keys = set(_BUILTIN_RESOURCES.keys())
        assert ResourceManager._support_resources.keys() >= builtin_keys

    def test_class_support_resources_is_copy_of_builtin(self):
        """_support_resources is a separate copy, not the same dict object."""
        assert ResourceManager._support_resources is not _BUILTIN_RESOURCES


class TestResourceManagerRegisterResource:
    """ResourceManager.register_resource class method.

    Registers a resource class by name so it can be instantiated later.
    The class must be BaseResource or a subclass thereof.
    """

    @pytest.mark.parametrize(
        "resource_cls",
        [
            BaseResource,
            type(
                "CustomResource",
                (BaseResource,),
                {"mount": lambda s: None, "demount": lambda s: None},
            ),
        ],
    )
    def test_register_valid_class_succeeds(self, resource_cls):
        """BaseResource and its subclasses can be registered."""
        with patch.dict(ResourceManager._support_resources, clear=False):
            ResourceManager.register_resource("test", resource_cls)
            assert ResourceManager._support_resources["test"] is resource_cls

    def test_register_non_class_type_error(self):
        """A non-class value raises TypeError from issubclass."""
        with pytest.raises(TypeError):
            ResourceManager.register_resource("bad", "not_a_class")  # type: ignore

    def test_register_non_subclass_class_error(self):
        """A class that does not inherit from BaseResource raises ValueError."""
        with pytest.raises(ValueError, match="resource cls must inherit from BaseResource"):
            ResourceManager.register_resource("bad", object)  # type: ignore


class TestResourceManagerEvaluateTarget:
    """ResourceManager.evaluate_target() — resolving targets to per-display resources."""

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_evaluate_target_resource_creates_per_display_instances(
        self, mock_cs, mock_get_display, tmp_path
    ):
        """A resource target creates one instance per connected display."""
        from PIL import Image

        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)

        from wallpaper_auto.util.display_utils import DisplayInfo

        display_a = DisplayInfo(
            monitor_device_path=r"\\?\DISPLAY#A#{path-a}",
            model="Monitor A",
            source_resolution=(1920, 1080),
            position=(0, 0),
            target_resolution=(1920, 1080),
        )
        display_b = DisplayInfo(
            monitor_device_path=r"\\?\DISPLAY#B#{path-b}",
            model="Monitor B",
            source_resolution=(1920, 1080),
            position=(1920, 0),
            target_resolution=(1920, 1080),
        )
        mock_get_display.return_value = [display_a, display_b]

        cfg = ResourceConfig(name="static_wallpaper", config={"path": str(img_path), "style": "fill"})
        mock_cs.instance.resource = {"wp1": cfg}

        result = ResourceManager.evaluate_target("wp1")

        assert len(result) == 2
        assert r"\\?\DISPLAY#A#{path-a}" in result
        assert r"\\?\DISPLAY#B#{path-b}" in result

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_evaluate_target_unknown_raises(self, mock_cs, mock_get_display):
        """An unknown target name raises ValueError."""
        mock_get_display.return_value = []
        mock_cs.instance.resource = {}
        mock_cs.instance.scene = {}

        with pytest.raises(ValueError, match="target unknown not found"):
            ResourceManager.evaluate_target("unknown")

    @patch("wallpaper_auto.resource_manager.get_display_info")
    @patch("wallpaper_auto.resource_manager.ConfigStore")
    def test_evaluate_target_scene_resolves_per_display(
        self, mock_cs, mock_get_display, tmp_path
    ):
        """A scene target uses the scene binding to map each display to a resource."""
        from PIL import Image

        from wallpaper_auto.models import SceneBinding
        from wallpaper_auto.util.display_utils import DisplayInfo

        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)

        display_a = DisplayInfo(
            monitor_device_path=r"\\?\DISPLAY#A#{path-a}",
            model="Dell U27",
            source_resolution=(1920, 1080),
            position=(0, 0),
            target_resolution=(1920, 1080),
        )
        mock_get_display.return_value = [display_a]

        cfg = ResourceConfig(
            name="static_wallpaper", config={"path": str(img_path), "style": "fill"}
        )
        mock_cs.instance.resource = {"wp1": cfg}
        mock_cs.instance.scene = {
            "office": [SceneBinding(display_model="Dell.*", resource="wp1")]
        }

        result = ResourceManager.evaluate_target("office")

        assert r"\\?\DISPLAY#A#{path-a}" in result


class TestResourceManagerEvaluateScene:
    """ResourceManager.evaluate_scene() — mapping displays to resources by model."""

    def test_evaluate_scene_matches_by_model_pattern(self):
        from wallpaper_auto.models import SceneBinding
        from wallpaper_auto.util.display_utils import DisplayInfo

        binding = SceneBinding(display_model="Dell.*", resource="dell_wp")
        display = DisplayInfo(
            monitor_device_path=r"\\?\DISPLAY#1#{path}",
            model="Dell U2719D",
            source_resolution=(1920, 1080),
            position=(0, 0),
            target_resolution=(1920, 1080),
        )
        result = ResourceManager.evaluate_scene([binding], [display])
        assert result == {r"\\?\DISPLAY#1#{path}": "dell_wp"}

    def test_evaluate_scene_no_match_returns_empty(self):
        from wallpaper_auto.models import SceneBinding
        from wallpaper_auto.util.display_utils import DisplayInfo

        binding = SceneBinding(display_model="NonExistent", resource="wp")
        display = DisplayInfo(
            monitor_device_path=r"\\?\DISPLAY#1#{path}",
            model="Dell U2719D",
            source_resolution=(1920, 1080),
            position=(0, 0),
            target_resolution=(1920, 1080),
        )
        result = ResourceManager.evaluate_scene([binding], [display])
        assert result == {}
