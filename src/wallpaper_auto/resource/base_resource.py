"""
Base resource abstract class.

This module defines the abstract base class for all wallpaper resource types.

All resource classes must inherit from :class:`BaseResource` and implement
the :meth:`~BaseResource.mount` / :meth:`~BaseResource.demount` lifecycle.

Resources receive a ``monitor_device_id`` at construction and render
wallpaper through a :class:`PlotCanvasProtocol` callable passed to
:meth:`mount`.  Origin wallpaper state can be saved via
:meth:`record_origin` and restored via :meth:`restore_origin`.
"""
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Protocol

from ..util.wallpaper_util import get_wallpaper, get_wallpaper_style, set_wallpaper, set_wallpaper_style, WallpaperStyle


class PlotCanvasProtocol(Protocol):
    """Callable that renders a wallpaper image on a specific monitor.

    Args:
        style: Wallpaper fit/style (fill, fit, stretch, etc.).
        image: The PIL image to display.
        immediate_update: If True, apply the change immediately
            (e.g. skip batching).  Defaults to True.
    """

    def __call__(self, style: WallpaperStyle, image_path: Path, immediate_update: bool = True) -> None: ...


class BaseResource(ABC):
    """
    Abstract base class for wallpaper resources.

    Each resource is bound to a specific monitor (via *monitor_device_id*)
    and renders wallpaper through a ``PlotCanvasProtocol`` callable.

    Subclasses must override :meth:`mount` and :meth:`demount`.
    """

    def __init__(self):
        """
        Args:
            monitor_device_id: Unique device path of the target monitor
                (e.g. ``MONITOR\...``).  Used for per-display wallpaper
                operations via the COM ``IDesktopWallpaper`` API.
        """
        self.monitor_device_path: str | None = None

    def bind_monitor_device_path(self, monitor_device_path: str):
        if self.monitor_device_path is not None:
            raise RuntimeError("monitor_device_path already bound")
        self.monitor_device_path = monitor_device_path


    @abstractmethod
    def mount(self, plot_canvas: PlotCanvasProtocol) -> None:
        """
        Prepare and render the wallpaper resource.

        Subclasses receive a *plot_canvas* callable that performs the actual
        wallpaper update.  Implementations should call::

            plot_canvas(style=..., image=..., immediate_update=True)

        rather than setting wallpaper directly, so the caller can control
        composition and batching across multiple displays.

        The wallpaper system calls :meth:`mount` before applying a wallpaper
        and :meth:`demount` after switching away.

        Args:
            plot_canvas: Callback that applies a styled image to the
                target monitor.  Accepts a :class:`WallpaperStyle`, a
                :class:`PIL.Image`, and an optional *immediate_update*
                flag.
        """
        ...

    @abstractmethod
    def demount(self) -> None:
        """
        Release and clean up the wallpaper resource.

        Subclasses implement this to release any resources held during
        the mount phase.

        The wallpaper system guarantees that demount() is always called
        after mount(), even if an error occurs during wallpaper application.
        """
        ...
