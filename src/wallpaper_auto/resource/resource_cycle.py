"""
Resource cycle that cycles through multiple BaseResource instances.

Mounts and cycles through a collection of sub-resources on a configurable
interval, optionally in random order. Each sub-resource handles its own
mount/demount lifecycle independently.
"""

from __future__ import annotations

import logging
import random
import threading
from pathlib import Path
from typing import Any

from PIL import Image

from ..util.wallpaper_util import WallpaperStyle
from .base_resource import BaseResource, PlotCanvasProtocol

logger = logging.getLogger(__name__)


class ResourceCycle(BaseResource):
    """
    A wallpaper resource that cycles through a list of sub-resources.

    On mount, starts a background thread that transitions to the next
    sub-resource every *interval* seconds. On demount, stops the thread
    and cleans up sub-resources.

    Sub-resources should be created with ``restore=False`` (the default)
    so that individual demount calls do not interfere with the cycle's
    lifecycle management.

    Args:
        resources: Sub-resources to cycle through.  Each element is either
            a ``BaseResource`` instance or a ``dict`` matching the
            ``ResourceConfig`` schema (``{"name": ..., "config": ...}``).
        interval: Seconds between automatic switches (default 300).
        random: If True, pick resources in random order; otherwise sequential.
        restore: If True, restore the original wallpaper on demount.

    Raises:
        ValueError: If *resources* is empty or *interval* is not positive.
    """

    def __init__(
        self,
        resources: list[BaseResource | dict[str, Any]],
        interval: float = 300,
        random: bool = False,
        restore: bool = False,
    ) -> None:
        super().__init__()
        # Resolve any raw dict entries through the resource registry
        self._resources: list[BaseResource] = []
        for r in resources:
            if isinstance(r, BaseResource):
                self._resources.append(r)
            elif isinstance(r, dict):
                self._resources.append(self._build_sub_resource(r))
            else:
                raise TypeError(f"Expected BaseResource or dict, got {type(r).__name__}")

        if not self._resources:
            raise ValueError("At least one resource is required")

        if interval <= 0:
            raise ValueError(f"interval must be a positive float, got {interval}")

        self.interval = interval
        self.random = random
        self.restore = restore

        # Threading state
        self._stop_event = threading.Event()
        self._cycling_thread: threading.Thread | None = None
        self._index = 0

    @staticmethod
    def _build_sub_resource(raw: dict[str, Any]) -> BaseResource:
        """Instantiate a sub-resource from a raw config dict.

        Uses ``ResourceManager._support_resources`` as the registry so that
        custom resources registered via ``register_resource()`` are available.
        The import is deferred to avoid a circular dependency between this
        module and ``resource_manager``.
        """
        from ..models import ResourceConfig  # noqa: PLC0415
        from ..resource_manager import ResourceManager  # noqa: PLC0415

        rc = ResourceConfig.model_validate(raw)
        resource_cls = ResourceManager._support_resources.get(rc.name)
        if resource_cls is None:
            raise ValueError(f"Unknown resource type: {rc.name}")
        return resource_cls(**rc.config)

    def _advance_index(self) -> None:
        """Move to the next resource index (sequential or random)."""
        if self.random:
            self._index = random.randrange(len(self._resources))
        else:
            self._index = (self._index + 1) % len(self._resources)

    def get_plot_canvas_wrapper(self) -> PlotCanvasProtocol:
        assert self._plot_canvas is not None, "plot_canvas not bound"
        assert self.monitor_device_path is not None, "monitor_device_path not bound"

        def plot_canvas_wrapper(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            assert self._plot_canvas is not None, "plot_canvas not bound"
            assert self.monitor_device_path is not None, "monitor_device_path not bound"
            return self._plot_canvas(monitor_device_path, style, image, True)

        return plot_canvas_wrapper

    def _cycling_loop(self) -> None:
        logger.debug("resource cycle cycling thread start")

        plot_canvas_wrapper = self.get_plot_canvas_wrapper()

        # Pick the first resource (random start or index 0)
        if self.random:
            self._advance_index()
        r = self._resources[self._index]
        assert self.monitor_device_path is not None
        r._bind_monitor_device_path(self.monitor_device_path)
        r._bind_plot_canvas(plot_canvas_wrapper)
        r.mount()

        while not self._stop_event.wait(timeout=self.interval):
            r.demount()
            r._unbind_plot_canvas()
            self._advance_index()
            r = self._resources[self._index]
            r._bind_monitor_device_path(self.monitor_device_path)
            r._bind_plot_canvas(plot_canvas_wrapper)
            r.mount()
        r.demount()
        r._unbind_plot_canvas()
        logger.debug("resource cycle cycling thread exit")

    def mount(self) -> None:
        # Start the cycling thread
        self._stop_event.clear()
        self._cycling_thread = threading.Thread(target=self._cycling_loop, daemon=True)
        self._cycling_thread.start()

    def demount(self) -> None:
        if self._cycling_thread is not None:
            self._stop_event.set()
            self._cycling_thread.join(timeout=3.0)
            self._cycling_thread = None
