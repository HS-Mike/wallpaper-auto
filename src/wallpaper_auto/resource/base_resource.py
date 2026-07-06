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

from PIL import Image

from ..util.wallpaper_util import WallpaperStyle


class PlotCanvasProtocol(Protocol):
    """Callable that renders a wallpaper image on a specific monitor.

    Args:
        style: Wallpaper fit/style (fill, fit, stretch, etc.).
        image: The PIL image to display.
        immediate_update: If True, apply the change immediately
            (e.g. skip batching).  Defaults to False.
    """

    def __call__(self, monitor_device_path: str, style: WallpaperStyle, image: Path | Image.Image, immediate_update: bool = False) -> None: ...


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
        self._plot_canvas: PlotCanvasProtocol | None = None

    def _bind_monitor_device_path(self, monitor_device_path: str):
        if self.monitor_device_path is not None:
            raise RuntimeError("monitor_device_path already bound")
        self.monitor_device_path = monitor_device_path
    
    def _bind_plot_canvas(self, plot_canvas: PlotCanvasProtocol):
        """
        Bind a plot canvas callable to this resource.

        Args:
            plot_canvas: Callable that renders a wallpaper image on the
                target monitor.  Accepts a :class:`WallpaperStyle`, a
                :class:`PIL.Image`, and an optional *immediate_update*
                flag.
        """
        if self._plot_canvas is not None:
            raise RuntimeError("plot_canvas already bound")
        self._plot_canvas = plot_canvas
    
    def _unbind_plot_canvas(self):
        """Unbind the plot canvas callable from this resource."""
        if self._plot_canvas is None:
            raise RuntimeError("plot_canvas not bound")
        self._plot_canvas = None
    
    def plot_canvas(self, style: WallpaperStyle, image: Path | Image.Image, immediate_update: bool = False):
        """
        Render a wallpaper image on the target monitor.

        Args:
            style: Wallpaper fit/style (fill, fit, stretch, etc.).
            image: The PIL image to display.
            immediate_update: If True, apply the change immediately
                (e.g. skip batching).  Defaults to False.
        """
        if self._plot_canvas is None:
            raise RuntimeError("plot_canvas not bound")
        if self.monitor_device_path is None:
            raise RuntimeError("monitor_device_path not bound")
        self._plot_canvas(self.monitor_device_path, style, image, immediate_update)

    @abstractmethod
    def mount(self) -> None:
        """
        Prepare and render the wallpaper resource.

        Subclasses should call :meth:`plot_canvas` to set wallpaper::

            self.plot_canvas(style=..., image=..., immediate_update=True)

        rather than setting wallpaper directly, so the caller can control
        composition and batching across multiple displays.

        The wallpaper system calls :meth:`mount` before applying a wallpaper
        and :meth:`demount` after switching away.
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
