"""
Resource manager.

Initializes and resolves wallpaper resources from config.
Handles registration of built-in and custom resource types.
No longer manages mount/demount lifecycle — resources are
bound to displays and mounted by the wallpaper controller.
"""

import logging
import re
import threading

from .models import ResourceConfig, SceneBinding
from .resource.base_resource import BaseResource
from .config_store import ConfigStore
from .resource.resource_carousel import ResourceCarousel
from .resource.static_wallpaper import StaticWallpaper
from .util.display_utils import get_display_info, DisplayInfo

logger = logging.getLogger(__name__)


_BUILTIN_RESOURCES: dict[str, type[BaseResource]] = {
    "static_wallpaper": StaticWallpaper,
    "resource_carousel": ResourceCarousel,
}


class ResourceManager:
    """Resource initializer — resolves config targets into per-display resource instances."""

    _support_resources = _BUILTIN_RESOURCES.copy()
    
    @classmethod
    def register_resource(cls, resource_name: str, resource: type[BaseResource]) -> None:
        """
        Register a custom resource class.
        Register the subclass before starting the controller.
        """
        if not issubclass(resource, BaseResource):
            raise ValueError("resource cls must inherit from BaseResource")
        cls._support_resources[resource_name] = resource

    @staticmethod
    def evaluate_target(target: str) -> dict[str, BaseResource]:
        """
        Resolve a target (resource or scene name) into per-display resource objects.

        Looks up *target* in the config: if it is a resource, every connected
        display gets its own instance of that resource. If it is a scene, the
        :meth:`evaluate_scene` mapping determines which resource each display gets.

        Args:
            target: Resource or scene name from the config.

        Returns:
            A dict mapping each monitor device path to its ``BaseResource`` instance.

        Raises:
            ValueError: *target* is neither a resource nor a scene in the config.
        """
        display_info: list[DisplayInfo] = get_display_info()
        if target in ConfigStore.instance.resource:
            resource_cfg: ResourceConfig = ConfigStore.instance.resource[target]
            res = {}
            for i in display_info:
                resource_obj = _BUILTIN_RESOURCES[resource_cfg.name](**resource_cfg.config)
                resource_obj._bind_monitor_device_path(i.monitor_device_path)
                res[i.monitor_device_path] = resource_obj
            return res
        elif target in ConfigStore.instance.scene:
            scene_cfg: list[SceneBinding] = ConfigStore.instance.scene[target]
            scene_map: dict[str, str] = ResourceManager.evaluate_scene(scene_cfg, display_info)
            res = {}
            for monitor_device_path, resource_id in scene_map.items():
                resource_cfg: ResourceConfig = ConfigStore.instance.resource[resource_id]
                resource_obj = _BUILTIN_RESOURCES[resource_cfg.name](**resource_cfg.config)
                resource_obj._bind_monitor_device_path(monitor_device_path)
                res[monitor_device_path] = resource_obj
            return res
        else:
            raise ValueError(f"target {target} not found in resource or scene config")

    @staticmethod
    def evaluate_scene(scene: list[SceneBinding], display_info: list[DisplayInfo]) -> dict[str, str]:
        """
        Evaluate a scene and return a mapping of monitor device path to resource ID.

        ``binding.display_model`` is interpreted as a regular expression pattern
        matched against the display model name via :func:`re.search`.

        Args:
            scene: List of SceneBinding objects defining the scene.
            display_info: List of DisplayInfo objects representing connected displays.

        Returns:
            A dictionary mapping monitor device paths to resource IDs.
        """
        result = {}
        for binding in scene:
            pattern = re.compile(binding.display_model)
            for display in display_info:
                if display.model and pattern.search(display.model):
                    result[display.monitor_device_path] = binding.resource
                    break
        return result
