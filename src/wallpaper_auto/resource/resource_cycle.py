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
from typing import Any

from .base_resource import BaseResource

logger = logging.getLogger(__name__)


class ResourceCycle(BaseResource):
    """
    A wallpaper resource that cycles through a list of sub-resources.

    On mount, starts a background thread that transitions to the next
    sub-resource every ``interval`` seconds. On demount, stops the thread
    and demounts the currently active sub-resource.
    """

    def __init__(
        self,
        resources: list[BaseResource | dict[str, Any]],
        interval: float = 300,
        random: bool = False,
    ) -> None:
        """Initialize the resource cycle.

        Args:
            resources: Sub-resources to cycle through. Each element is either
                a ``BaseResource`` instance or a ``dict`` matching the
                ``ResourceConfig`` schema (``{"name": ..., "config": ...}``).
            interval: Seconds between automatic switches (default 300).
            random: If True, pick resources in random order; otherwise sequential.

        Raises:
            TypeError: If any element of ``resources`` is neither a
                ``BaseResource`` instance nor a ``dict``.
            ValueError: If ``resources`` is empty, ``interval`` is not positive,
                or a ``dict`` references an unknown resource type.
        """
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

        # Threading state
        self._stop_event = threading.Event()
        self._cycling_thread: threading.Thread | None = None
        self._index = 0

        self.mounted_resource: BaseResource | None = None

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

    def _cycling_loop(self) -> None:
        """Rotate sub-resources until the stop event is set.

        Demounts the current sub-resource, advances to the next index, mounts
        the next sub-resource, and requests a composite. Exits (and demounts
        the active sub-resource) when the stop event is set.
        """
        assert self.mounted_resource is not None
        assert self.display is not None

        while not self._stop_event.wait(timeout=self.interval):
            self.mounted_resource.demount()
            self._advance_index()
            self.mounted_resource = self._resources[self._index]
            self.mounted_resource._bind_display(self.display)
            self.mounted_resource.mount()
            self.plot_canvas()
        self.mounted_resource.demount()
        logger.debug("resource cycle cycling thread exit")

    def mount(self) -> None:
        """Start cycling sub-resources on a background thread.

        Advances to the next index, mounts that sub-resource synchronously,
        then starts a daemon thread that rotates to the next sub-resource
        every ``interval`` seconds until :meth:`demount` is called.
        """
        self._stop_event.clear()

        self._advance_index()
        self.mounted_resource = self._resources[self._index]
        assert self.display is not None
        self.mounted_resource._bind_display(self.display)
        self.mounted_resource.mount()

        self._cycling_thread = threading.Thread(target=self._cycling_loop, daemon=True)
        self._cycling_thread.start()

    def demount(self) -> None:
        """Stop cycling and release the mounted sub-resource.

        Signals the cycling thread to exit and joins it, allowing the thread
        to demount the currently active sub-resource before it finishes.
        """
        if self._cycling_thread is not None:
            self._stop_event.set()
            self._cycling_thread.join(timeout=3.0)
            self._cycling_thread = None
