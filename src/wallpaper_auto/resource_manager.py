"""
Resource manager.

Initializes and resolves wallpaper resources from config.
Handles registration of built-in and custom resource types.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import overload

from .config_store import ConfigStore
from .models import ResourceConfig, SceneBinding
from .resource.base_resource import BaseResource
from .resource.resource_cycle import ResourceCycle
from .resource.static_wallpaper import StaticWallpaper
from .resource.wallpaper_engine_resource import WallpaperEngineResource
from .util.display_util import DisplayId, DisplayInfo

logger = logging.getLogger(__name__)


_BUILTIN_RESOURCES: dict[str, type[BaseResource]] = {
    "static_wallpaper": StaticWallpaper,
    "cycle": ResourceCycle,
    "wallpaper_engine": WallpaperEngineResource,
}


ResourceTransitionKey = tuple[type[BaseResource], type[BaseResource]]
ResourceTransition = Callable[[BaseResource, BaseResource], None]


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

    _resource_transitions: dict[ResourceTransitionKey, list[ResourceTransition]] = {}

    @classmethod
    def register_resource(cls, resource_name: str, resource: type[BaseResource]) -> None:
        if not issubclass(resource, BaseResource):
            raise ValueError("resource cls must inherit from BaseResource")
        cls._support_resources[resource_name] = resource

    @overload
    @classmethod
    def register_transition(
        cls,
        old_cls: type[BaseResource],
        new_cls: type[BaseResource],
        func: ResourceTransition,
    ) -> None: ...

    @overload
    @classmethod
    def register_transition(
        cls, old_cls: type[BaseResource], new_cls: type[BaseResource]
    ) -> Callable[[ResourceTransition], ResourceTransition]: ...

    @classmethod
    def register_transition(
        cls,
        old_cls: type[BaseResource],
        new_cls: type[BaseResource],
        func: ResourceTransition | None = None,
    ) -> Callable[[ResourceTransition], ResourceTransition] | None:
        """Register a transition for an exact ``(old_cls, new_cls)`` pair.

        Two forms share this name. **Direct** — pass the callable, including a
        method of the outgoing resource class, which is invoked as
        ``old.on_handoff(new)`` because the stored function's first parameter
        binds to *old*::

            ResourceManager.register_transition(
                StaticWallpaper, ResourceCycle, StaticWallpaper.on_handoff
            )

        **Decorator** — omit the callable to get a decorator that registers the
        function and returns it unchanged::

            @ResourceManager.register_transition(StaticWallpaper, ResourceCycle)
            def release_engine(old: BaseResource, new: BaseResource) -> None: ...

        Registered functions are invoked in registration order by
        :meth:`run_resource_transitions`, before the outgoing resource is
        demounted and the incoming one is mounted. Matching is by exact type: a
        transition registered for a base class does not fire for a subclass.

        Args:
            old_cls: Exact class of the outgoing resource.
            new_cls: Exact class of the incoming resource.
            func: Callable invoked with the outgoing and incoming instances, or
                ``None`` to return a decorator instead.

        Returns:
            ``None`` when *func* is given; otherwise a decorator that registers
            the decorated function and returns it unchanged.

        Raises:
            ValueError: *old_cls* or *new_cls* is not a ``BaseResource`` subclass.
        """
        if not issubclass(old_cls, BaseResource):
            raise ValueError("old_cls must inherit from BaseResource")
        if not issubclass(new_cls, BaseResource):
            raise ValueError("new_cls must inherit from BaseResource")
        if func is None:

            def decorator(transition: ResourceTransition) -> ResourceTransition:
                cls.register_transition(old_cls, new_cls, transition)
                return transition

            return decorator
        cls._resource_transitions.setdefault((old_cls, new_cls), []).append(func)
        return None

    @classmethod
    def run_resource_transitions(cls, old: BaseResource, new: BaseResource) -> None:
        """Invoke every transition registered for ``(type(old), type(new))``.

        Runs in registration order. Called by ``DisplayManager.update_resource``
        before the outgoing resource is demounted and the incoming one is
        mounted. A pair no swap ever produces is a silent no-op.

        Args:
            old: The outgoing resource instance.
            new: The incoming resource instance.

        Raises:
            Exception: Any exception raised by a registered transition propagates
                to the caller and is never swallowed.
        """
        funcs = cls._resource_transitions.get((type(old), type(new)))
        if funcs is None:
            return
        for func in funcs:
            logger.debug(
                "resource transition %s: %s -> %s",
                getattr(func, "__name__", repr(func)),
                type(old).__name__,
                type(new).__name__,
            )
            func(old, new)

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


register_transition = ResourceManager.register_transition
