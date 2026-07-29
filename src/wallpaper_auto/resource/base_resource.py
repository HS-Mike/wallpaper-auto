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

from abc import ABC, abstractmethod
from pathlib import Path
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

    def __call__(
        self,
        monitor_device_path: str,
        style: WallpaperStyle,
        image: Path | Image.Image,
        immediate_update: bool = False,
    ) -> None: ...


class BaseResource(ABC):
    """
    Abstract base class for wallpaper resources.

    Each resource is bound to a specific monitor (via *monitor_device_id*)
    and renders wallpaper through a ``PlotCanvasProtocol`` callable.

    ``mount()`` and ``demount()`` are lifecycle notifications — they signal
    the start and end of this resource's active window.  A resource is
    **single-use**: mount once, demount once.  Re-mounting the same
    instance is not expected.

    ``plot_canvas()`` may be called at any point after ``mount()`` and
    before ``demount()`` (not only during ``mount()`` itself), enabling
    dynamic wallpaper updates such as cycling or animation.

    Subclasses must override :meth:`mount` and :meth:`demount`.
    """

    def __init__(self) -> None:
        r"""
        Args:
            monitor_device_id: Unique device path of the target monitor
                (e.g. ``MONITOR\...``).  Used for per-display wallpaper
                operations via the COM ``IDesktopWallpaper`` API.
        """
        self.monitor_device_path: str | None = None
        self._plot_canvas: PlotCanvasProtocol | None = None

    def _bind_monitor_device_path(self, monitor_device_path: str) -> None:
        if self.monitor_device_path is not None:
            raise RuntimeError("monitor_device_path already bound")
        self.monitor_device_path = monitor_device_path

    def _bind_plot_canvas(self, plot_canvas: PlotCanvasProtocol) -> None:
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

    def _unbind_plot_canvas(self) -> None:
        """Unbind the plot canvas callable from this resource."""
        if self._plot_canvas is None:
            raise RuntimeError("plot_canvas not bound")
        self._plot_canvas = None

    def plot_canvas(
        self,
        style: WallpaperStyle,
        image: Path | Image.Image,
        immediate_update: bool = False,
    ) -> None:
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
        Lifecycle notification: this resource is now active.

        Subclasses should call :meth:`plot_canvas` (or defer to a background
        thread) to set wallpaper::

            self.plot_canvas(style=..., image=..., immediate_update=True)

        rather than setting wallpaper directly, so the caller can control
        composition and batching across multiple displays.

        Note that :meth:`plot_canvas` may be called any time after
        ``mount()`` and before ``demount()`` — not only during ``mount()``
        itself — enabling subclasses to update the wallpaper dynamically
        (e.g. cycling, animations) without re-entering the lifecycle.
        """
        ...

    @abstractmethod
    def demount(self) -> None:
        """
        Lifecycle notification: this resource is no longer active.

        Subclasses implement this to release any resources held during
        the mount phase (e.g. stop background threads).

        The wallpaper system guarantees that demount() is always called
        after mount(), even if an error occurs during wallpaper application.
        """
        ...
