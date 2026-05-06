"""
Resource manager.

Manages wallpaper resource lifecycle (mount/demount) at runtime.
Handles registration of built-in and custom resource types.
"""
import logging
import threading

from .resource.base_resource import BaseResource
from .resource.dynamic_wallpaper import DynamicWallpaper
from .resource.static_wallpaper import StaticWallpaper
from .models import ResourceConfig


logger = logging.getLogger(__name__)



_BUILTIN_RESOURCES: dict[str, type[BaseResource]] = {
    "static_wallpaper": StaticWallpaper,
    "dynamic_wallpaper": DynamicWallpaper,
}



class ResourceManager:
    """Wallpaper resource manager, responsible for runtime mount/unmount and init resources from parsed config."""
    _support_resources = _BUILTIN_RESOURCES.copy()

    def __init__(self):
        self._mutex = threading.Lock()
        self._resource_objects: dict[str, BaseResource] = {}
        self._active_resource_id: str | None = None
    
    @classmethod
    def register_resource(cls, resource_name: str, resource: type[BaseResource]) -> None:
        """
        Register a custom resource class.
        Register the subclass before starting WallpaperAutomator.
        """
        if not issubclass(resource, BaseResource):
            raise ValueError("resource cls must inherit from BaseResource")
        cls._support_resources[resource_name] = resource

    def init(self, resource_configs: dict[str, ResourceConfig]) -> None:
        """Initialize resources from resource config dictionary."""
        with self._mutex:
            self._resource_objects.clear()
            self._active_resource_id = None
            for resource_id, resource_data in resource_configs.items():
                self._resource_objects[resource_id] = self._init_resource(resource_data)

    def _init_resource(self, data: ResourceConfig) -> BaseResource:
        """Lockup resource class and init the instance. Params in parsed config are passed to init as kwargs."""
        resource_cls = self._support_resources.get(data.name)
        if resource_cls is None:
            raise ValueError(f"Unknown resource type: {data.name}")
        return resource_cls(**data.config)

    @property
    def resource_ids(self) -> list[str]:
        """Return all wallpaper resource IDs."""
        with self._mutex:
            return list(self._resource_objects.keys())
        
    @property
    def active_resource_id(self) -> str | None:
        """Return current mounted resource id. Return None if no resource is activated."""
        with self._mutex:
            return self._active_resource_id

    def mount(self, resource_id: str) -> None:
        """Activate the specified resource (automatically demount current one)."""
        with self._mutex:
            if resource_id not in self._resource_objects:
                raise KeyError(f"Resource not found: {resource_id}")
            self._resource_objects[resource_id].mount()
            self._active_resource_id = resource_id

    def demount(self) -> None:
        """Unmount the currently active resource."""
        with self._mutex:
            if self._active_resource_id is not None:
                self._resource_objects[self._active_resource_id].demount()
                self._active_resource_id = None
