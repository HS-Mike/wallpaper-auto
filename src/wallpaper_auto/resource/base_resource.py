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

from ..util.wallpaper_util import get_wallpaper, get_wallpaper_style, set_wallpaper, set_wallpaper_style, WallpaperStyle


class PlotCanvasProtocol(Protocol):
    """Callable that renders a wallpaper image on a specific monitor.

    Args:
        style: Wallpaper fit/style (fill, fit, stretch, etc.).
        image: The PIL image to display.
        immediate_update: If True, apply the change immediately
            (e.g. skip batching).  Defaults to True.
    """

    def __call__(self, style: WallpaperStyle, image: Image.Image, immediate_update: bool = True) -> None: ...


class BaseResource(ABC):
    """
    Abstract base class for wallpaper resources.

    Each resource is bound to a specific monitor (via *monitor_device_id*)
    and renders wallpaper through a ``PlotCanvasProtocol`` callable.

    Subclasses must override :meth:`mount` and :meth:`demount`.
    """

    def __init__(self, monitor_device_id: str):
        """
        Args:
            monitor_device_id: Unique device path of the target monitor
                (e.g. ``MONITOR\...``).  Used for per-display wallpaper
                operations via the COM ``IDesktopWallpaper`` API.
        """
        self.monitor_device_id: str = monitor_device_id
        self.original_wallpaper_style: WallpaperStyle | None = None
        self.original_wallpaper_path: Path | None = None

    def record_origin(self):
        """Save the current wallpaper style and per-monitor wallpaper path.

        Call before :meth:`mount` to capture the state that
        :meth:`restore_origin` will later revert to.
        """
        self.original_wallpaper_style = get_wallpaper_style()
        self.original_wallpaper_path = get_wallpaper(self.monitor_device_id)

    def restore_origin(self):
        """Restore the wallpaper captured by :meth:`record_origin`.

        Raises:
            AssertionError: If :meth:`record_origin` was not called first.
        """
        assert self.original_wallpaper_style is not None, "no origin record"
        assert self.original_wallpaper_path is not None, "no origin record"
        set_wallpaper_style(self.original_wallpaper_style)
        set_wallpaper(self.monitor_device_id, self.original_wallpaper_path)


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
