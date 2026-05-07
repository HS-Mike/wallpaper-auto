"""Tests for resource_manager.py — resource lifecycle and registration."""

from unittest.mock import MagicMock, patch

import pytest

from wallpaper_automator.models import ResourceConfig
from wallpaper_automator.resource.base_resource import BaseResource
from wallpaper_automator.resource_manager import _BUILTIN_RESOURCES, ResourceManager


@pytest.fixture
def mgr():
    """Provide a fresh ResourceManager instance for each test."""
    return ResourceManager()


def _mock_resource():
    """Create a MagicMock that conforms to the BaseResource interface."""
    return MagicMock(spec=BaseResource)


class TestResourceManagerInit:
    """ResourceManager __init__ and class-level defaults"""

    def test_init_sets_empty_state(self, mgr):
        """A new manager starts with no resource objects and no active resource."""
        assert mgr._resource_objects == {}
        assert mgr._active_resource_id is None
        assert mgr._mutex is not None

    def test_builtin_resources_are_registered(self):
        """The class-level _support_resources dict contains all built-in resources."""
        builtin_keys = set(_BUILTIN_RESOURCES.keys())
        assert ResourceManager._support_resources.keys() >= builtin_keys

    def test_class_support_resources_is_copy_of_builtin(self):
        """_support_resources is a separate copy, not the same dict object."""
        assert ResourceManager._support_resources is not _BUILTIN_RESOURCES


class TestResourceManagerRegisterResource:
    """ResourceManager.register_resource class method.

    Registers a resource class by name so it can be instantiated via init().
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


class TestResourceManagerInitResources:
    """ResourceManager.init() — creating resource instances from config"""

    def test_init_with_single_resource(self, mgr):
        """init creates a resource when given a valid config entry."""
        mock_cls = MagicMock(return_value=_mock_resource())
        with patch.object(ResourceManager, "_support_resources", {"mock_type": mock_cls}):
            mgr.init({"r1": ResourceConfig(name="mock_type", config={})})
        assert len(mgr._resource_objects) == 1

    def test_init_config_kwargs_forwarded_to_constructor(self, mgr):
        """The config dict is unpacked as **kwargs to the resource's __init__."""
        mock_cls = MagicMock(return_value=_mock_resource())
        with patch.object(ResourceManager, "_support_resources", {"mock": mock_cls}):
            mgr.init({"r1": ResourceConfig(name="mock", config={"key": "val"})})

        mock_cls.assert_called_once_with(**{"key": "val"})

    def test_init_multiple_resources(self, mgr):
        """init creates multiple resources when given multiple config entries."""
        mock_cls = MagicMock(return_value=_mock_resource())
        cfgs = {
            "r1": ResourceConfig(name="mock", config={}),
            "r2": ResourceConfig(name="mock", config={}),
        }
        with patch.object(ResourceManager, "_support_resources", {"mock": mock_cls}):
            mgr.init(cfgs)
        assert len(mgr._resource_objects) == 2

    def test_init_unknown_resource_raises(self, mgr):
        """init raises ValueError when the resource name is not registered."""
        with pytest.raises(ValueError, match="Unknown resource type: nonexistent"):
            mgr.init({"r1": ResourceConfig(name="nonexistent", config={})})

    def test_init_empty_config_dict(self, mgr):
        """init with an empty dict leaves _resource_objects empty."""
        mgr.init({})
        assert mgr._resource_objects == {}

    def test_init_clears_previous_state(self, mgr):
        """Calling init again replaces all previously registered resources."""
        mock_cls = MagicMock(return_value=_mock_resource())
        with patch.object(ResourceManager, "_support_resources", {"mock": mock_cls}):
            mgr.init({"old": ResourceConfig(name="mock", config={})})
            mgr.init({})
        assert mgr._resource_objects == {}

    def test_init_resets_active_resource_id(self, mgr):
        """init clears the active_resource_id even if one was previously set."""
        mgr._active_resource_id = "stale"
        mgr.init({})
        assert mgr._active_resource_id is None

    def test_init_with_static_wallpaper_real_image(self, mgr, tmp_path):
        """init creates a real StaticWallpaper when given a valid image path."""
        from PIL import Image

        from wallpaper_automator.resource.static_wallpaper import StaticWallpaper

        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (64, 64), color="red").save(img_path)
        mgr.init({"wp1": ResourceConfig(name="static_wallpaper", config={"path": str(img_path)})})
        assert len(mgr._resource_objects) == 1
        assert isinstance(mgr._resource_objects["wp1"], StaticWallpaper)


class TestResourceManagerMount:
    """ResourceManager.mount() — activating a resource"""

    def test_mount_calls_resource_mount_and_sets_active(self, mgr):
        """mount calls the resource's mount() method and updates active_resource_id."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}

        mgr.mount("r1")

        mock_resource.mount.assert_called_once()
        assert mgr._active_resource_id == "r1"

    def test_mount_unknown_resource_raises(self, mgr):
        """mount raises KeyError when the resource_id does not exist."""
        with pytest.raises(KeyError, match="Resource not found: missing"):
            mgr.mount("missing")

    def test_mount_twice_calls_mount_on_new_resource(self, mgr):
        """Mounting a different resource calls mount on the new one (no auto-demount)."""
        mock_a = _mock_resource()
        mock_b = _mock_resource()
        mgr._resource_objects = {"a": mock_a, "b": mock_b}

        mgr.mount("a")
        mgr.mount("b")

        mock_a.mount.assert_called_once()
        mock_a.demount.assert_not_called()
        mock_b.mount.assert_called_once()
        assert mgr._active_resource_id == "b"

    def test_mount_same_resource_twice(self, mgr):
        """Mounting the same resource twice calls mount() each time."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}

        mgr.mount("r1")
        mgr.mount("r1")

        assert mock_resource.mount.call_count == 2


class TestResourceManagerDemount:
    """ResourceManager.demount() — deactivating the current resource"""

    def test_demount_calls_resource_demount_and_clears_active(self, mgr):
        """demount calls the active resource's demount() and sets active_resource_id to None."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}
        mgr._active_resource_id = "r1"

        mgr.demount()

        mock_resource.demount.assert_called_once()
        assert mgr._active_resource_id is None

    def test_demount_with_no_active_resource_is_noop(self, mgr):
        """demount when no resource is active does nothing (no error)."""
        mgr.demount()

    def test_demount_twice_is_idempotent(self, mgr):
        """Calling demount multiple times does not raise and stays in demounted state."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}
        mgr._active_resource_id = "r1"

        mgr.demount()
        mgr.demount()

        mock_resource.demount.assert_called_once()
        assert mgr._active_resource_id is None


class TestResourceManagerProperties:
    """ResourceManager.resource_ids and .active_resource_id"""

    def test_resource_ids_returns_keys(self, mgr):
        """resource_ids returns the keys of _resource_objects."""
        mgr._resource_objects = {"a": MagicMock(), "b": MagicMock()}
        ids = mgr.resource_ids
        assert sorted(ids) == ["a", "b"]

    def test_resource_ids_empty_when_no_resources(self, mgr):
        """resource_ids returns an empty list when no resources exist."""
        assert mgr.resource_ids == []

    def test_active_resource_id_returns_none_initially(self, mgr):
        """active_resource_id is None when no resource has been mounted."""
        assert mgr.active_resource_id is None

    def test_active_resource_id_after_mount(self, mgr):
        """active_resource_id returns the currently mounted resource id."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}
        mgr.mount("r1")
        assert mgr.active_resource_id == "r1"

    def test_active_resource_id_after_demount(self, mgr):
        """active_resource_id returns None after demount."""
        mock_resource = _mock_resource()
        mgr._resource_objects = {"r1": mock_resource}
        mgr.mount("r1")
        mgr.demount()
        assert mgr.active_resource_id is None
