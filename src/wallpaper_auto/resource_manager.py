"""
Resource manager.

Initializes and resolves wallpaper resources from config.
Handles registration of built-in and custom resource types.
"""

import logging
import re
from dataclasses import dataclass

from .config_store import ConfigStore
from .models import ResourceConfig, SceneBinding
from .resource.base_resource import BaseResource
from .resource.resource_cycle import ResourceCycle
from .resource.static_wallpaper import StaticWallpaper
from .util.display_util import DisplayId, DisplayInfo

logger = logging.getLogger(__name__)


_BUILTIN_RESOURCES: dict[str, type[BaseResource]] = {
    "static_wallpaper": StaticWallpaper,
    "cycle": ResourceCycle,
}


@dataclass
class DisplayScene:
    """What is intended to apply to a single display.

    Bundles the target ``display_id`` with the resolved ``resource`` (an
    instance bound to that display) and the optional display
    ``resolution``/``scale`` the scene binding requested. ``None``
    resolution/scale means "leave the display at its original value".
    """

    display_id: DisplayId
    resource: BaseResource
    resolution: tuple[int, int] | None
    scale: int | None


class ResourceManager:
    """Resource initializer — resolves config targets into per-display resource instances."""

    _support_resources = _BUILTIN_RESOURCES.copy()

    @classmethod
    def register_resource(cls, resource_name: str, resource: type[BaseResource]) -> None:
        if not issubclass(resource, BaseResource):
            raise ValueError("resource cls must inherit from BaseResource")
        cls._support_resources[resource_name] = resource

    @staticmethod
    def evaluate_target(target: str, display_info: list[DisplayInfo]) -> list[DisplayScene]:
        """
        Resolve a target (resource or scene name) into per-display DisplayScene objects.

        If *target* is a resource, every connected display gets its own instance
        of that resource. If it is a scene, :meth:`evaluate_scene` determines
        which resource each display gets, and the matched binding's
        ``resolution``/``scale`` (if any) are carried on the DisplayScene.

        Args:
            target: Resource or scene name from the config.

        Returns:
            A list of ``DisplayScene`` objects — one per display the target
            matches (its ``BaseResource`` plus optional target resolution and
            scale). An empty list is returned when displays are connected but
            none match the target.

        Raises:
            ValueError: *target* is neither a resource nor a scene in the config.
        """
        res: list[DisplayScene] = []
        if target in ConfigStore.instance.resource:
            for i in display_info:
                res.append(
                    DisplayScene(
                        display_id=i.display_id,
                        resource=ResourceManager._create_resource(target, i),
                        resolution=None,
                        scale=None,
                    )
                )
            return res
        if target in ConfigStore.instance.scene:
            display_by_id = {i.display_id: i for i in display_info}
            for display_id, binding in ResourceManager.evaluate_scene(
                ConfigStore.instance.scene[target].bindings, display_info
            ).items():
                res.append(
                    DisplayScene(
                        display_id=display_id,
                        resource=ResourceManager._create_resource(
                            binding.resource, display_by_id[display_id]
                        ),
                        resolution=binding.resolution,
                        scale=binding.scale,
                    )
                )
            return res
        raise ValueError(f"target {target} not found in resource or scene config")

    @staticmethod
    def _create_resource(resource_id: str, display: DisplayInfo) -> BaseResource:
        resource_cfg: ResourceConfig = ConfigStore.instance.resource[resource_id]
        resource_obj = ResourceManager._support_resources[resource_cfg.name](**resource_cfg.config)
        resource_obj._bind_display(display)
        return resource_obj

    @staticmethod
    def evaluate_scene(
        scene: list[SceneBinding], display_info: list[DisplayInfo]
    ) -> dict[DisplayId, SceneBinding]:
        """
        Map each display to the first scene binding that matches its model.

        ``display_model`` matches exactly; ``match_display_model`` is a regular
        expression pattern matched via :func:`re.search`.

        Args:
            scene: List of SceneBinding objects defining the scene.
            display_info: List of DisplayInfo objects representing connected displays.

        Returns:
            A dict mapping display ids to the SceneBinding that matched them.
        """
        result: dict[DisplayId, SceneBinding] = {}
        for binding in scene:
            for display in display_info:
                if display.model is None:
                    continue
                if binding.display_model == display.model or (
                    binding.match_display_model
                    and re.search(binding.match_display_model, display.model)
                ):
                    result[display.display_id] = binding
                    break
        return result
