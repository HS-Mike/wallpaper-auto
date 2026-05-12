"""
Resource carousel that cycles through multiple BaseResource instances.

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
from .wallpaper_utils import (
    get_current_wallpaper,
    get_current_wallpaper_style,
    set_wallpaper,
)

logger = logging.getLogger(__name__)


class ResourceCarousel(BaseResource):
    """
    A wallpaper resource that cycles through a list of sub-resources.

    On mount, saves the current wallpaper, mounts the first sub-resource,
    and starts a background thread that advances to the next sub-resource
    every *interval* seconds. On demount, stops the thread, demounts the
    current sub-resource, and restores the original wallpaper.

    Accepts either pre-instantiated ``BaseResource`` objects (programmatic
    use) or raw config dicts (YAML).  Dicts are resolved through the
    ``ResourceManager._support_resources`` registry.

    Sub-resources should be created with ``restore=False`` (the default) so
    that :meth:`demount` does not fight with this carousel's own restore
    logic.

    Args:
        resources: Sub-resources to cycle through.  Each element is either
            a ``BaseResource`` instance or a ``dict`` matching the
            ``ResourceConfig`` schema (``{"name": ..., "config": ...}``).
        interval: Seconds between automatic switches (default 300).
        random: If True, pick resources in random order; otherwise sequential.
        restore: If True, restore the original wallpaper on demount.

    Raises:
        ValueError: If *resources* is empty.
    """

    def __init__(
        self,
        resources: list[BaseResource | dict[str, Any]],
        interval: int = 300,
        random: bool = False,
        restore: bool = False,
    ) -> None:
        # Resolve any raw dict entries through the resource registry
        self._resources: list[BaseResource] = []
        for r in resources:
            if isinstance(r, BaseResource):
                self._resources.append(r)
            elif isinstance(r, dict):
                self._resources.append(self._build_sub_resource(r))
            else:
                raise TypeError(
                    f"Expected BaseResource or dict, got {type(r).__name__}"
                )

        if not self._resources:
            raise ValueError("At least one resource is required")

        self.interval = interval
        self.random = random
        self.restore = restore

        # Threading state
        self._stop_event = threading.Event()
        self._cycling_thread: threading.Thread | None = None
        self._index = 0

        # Original wallpaper tracking
        self._original_wallpaper: str | None = None
        self._original_style: tuple[str, str] | None = None

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

    # ---- Private helpers ---------------------------------------------------

    def _advance_index(self) -> None:
        """Move to the next resource index (sequential or random)."""
        if self.random:
            self._index = random.randrange(len(self._resources))
        else:
            self._index = (self._index + 1) % len(self._resources)

    def _cycling_loop(self) -> None:
        """Background thread: cycle sub-resources at the configured interval."""
        logger.debug("resource carousel cycling thread start")
        while not self._stop_event.wait(timeout=self.interval):
            # Demount current sub-resource (no-op when restore=False)
            self._resources[self._index].demount()
            self._advance_index()
            self._resources[self._index].mount()
        logger.debug("resource carousel cycling thread exit")

    # ---- Lifecycle ---------------------------------------------------------

    def mount(self) -> None:
        """
        Start the resource cycling.

        Saves the current wallpaper path and style, mounts the first
        sub-resource, then launches a daemon thread that cycles through
        sub-resources every *interval* seconds.
        """
        self._original_wallpaper = get_current_wallpaper()
        self._original_style = get_current_wallpaper_style()
        logger.debug(
            "origin wallpaper: %s, style: %s",
            self._original_wallpaper,
            self._original_style,
        )

        # Pick the first resource (random start or index 0)
        if self.random:
            self._advance_index()
        self._resources[self._index].mount()

        # Start the cycling thread
        self._stop_event.clear()
        self._cycling_thread = threading.Thread(target=self._cycling_loop, daemon=True)
        self._cycling_thread.start()

    def demount(self) -> None:
        """
        Stop cycling and restore the original wallpaper.

        Signals the cycling thread to exit, waits for it to finish, demounts
        the current sub-resource, and restores the wallpaper that was active
        before :meth:`mount` was called.

        When *restore* is ``False`` (set at init time), the original wallpaper
        is *not* restored.

        Safe to call without a prior mount (no-op).
        """
        if self._cycling_thread is not None:
            self._stop_event.set()
            self._cycling_thread.join(timeout=5.0)
            self._cycling_thread = None

            # Demount the sub-resource that was active when cycling stopped
            self._resources[self._index].demount()

        if self.restore and self._original_wallpaper and self._original_style:
            set_wallpaper(self._original_wallpaper, self._original_style)
            logger.debug(
                "restore origin wallpaper: %s, style: %s",
                self._original_wallpaper,
                self._original_style,
            )

        self._original_wallpaper = None
        self._original_style = None
