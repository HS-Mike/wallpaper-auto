"""
Base resource abstract class.

This module defines the abstract base class for all wallpaper resource types.

All resource classes must inherit from :class:`BaseResource` and implement
the :meth:`~BaseResource.mount` / :meth:`~BaseResource.demount` lifecycle.

Each resource is bound to a monitor (via :meth:`_bind_monitor_device_path`) and
buffers wallpaper through a class-wide :class:`UpdateCanvasProtocol` callback
(:meth:`update_canvas`).  Dynamic resources request a composite through the
class-wide :class:`PlotCanvasProtocol` callback (:meth:`plot_canvas`); both
callbacks are registered once by the ``WallpaperController``.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from PIL import Image

from ..util.wallpaper_util import WallpaperStyle


class UpdateCanvasProtocol(Protocol):
    """Callable that buffers a wallpaper image on a specific monitor.

    Args:
        monitor_device_path: Unique device path of the target monitor.
        style: Wallpaper fit/style (fill, fit, stretch, etc.).
        image: The image path or PIL image to display.
    """

    def __call__(
        self,
        monitor_device_path: str,
        style: WallpaperStyle,
        image: Path | Image.Image,
    ) -> None: ...


class PlotCanvasProtocol(Protocol):
    """Callable that requests a composite of the buffered canvas."""

    def __call__(self) -> object: ...


class BaseResource(ABC):
    """
    Abstract base class for wallpaper resources.

    Each resource is bound to a specific monitor (via *monitor_device_id*)
    and buffers wallpaper through an :class:`UpdateCanvasProtocol` callable.

    ``mount()`` and ``demount()`` are lifecycle notifications — they signal
    the start and end of this resource's active window.  A resource is
    **single-use**: mount once, demount once.  Re-mounting the same
    instance is not expected.

    ``update_canvas()`` may be called at any point after ``mount()`` and
    before ``demount()`` (not only during ``mount()`` itself), enabling
    dynamic wallpaper updates such as cycling or animation.  Buffering via
    :meth:`update_canvas` does not apply the wallpaper by itself — call
    :meth:`plot_canvas` to ask the system to composite the buffered canvas.

    Subclasses must override :meth:`mount` and :meth:`demount`.
    """

    #: Class-wide buffer callback, registered once by the ``DisplayManager``
    #: (it is uniform across all resource instances).
    _update_canvas: UpdateCanvasProtocol | None = None

    #: Class-wide composite-request callback, registered once by the
    #: ``WallpaperController`` (it is uniform across all resource instances).
    _plot_canvas: PlotCanvasProtocol | None = None

    @classmethod
    def register_update_canvas(cls, update_canvas: UpdateCanvasProtocol) -> None:
        """Register the class-wide buffer callback.

        The callback is the same for every resource (it buffers the image into
        the ``DisplayManager`` canvas), so it is bound once at class level
        rather than per instance.

        Pass a bound method or callable object — a plain function stored as a
        class attribute would be bound as a method when accessed on an instance.
        """
        cls._update_canvas = update_canvas

    @classmethod
    def register_plot_canvas(cls, plot_canvas: PlotCanvasProtocol) -> None:
        """Register the class-wide composite-request callback.

        The callback is the same for every resource (it enqueues a
        ``PlotCanvasTask`` on the controller's worker loop), so it is bound
        once at class level rather than per instance.

        Pass a bound method or callable object — a plain function stored as a
        class attribute would be bound as a method when accessed on an instance.
        """
        cls._plot_canvas = plot_canvas

    def __init__(self) -> None:
        self.monitor_device_path: str | None = None

    def _bind_monitor_device_path(self, monitor_device_path: str) -> None:
        if self.monitor_device_path is not None:
            raise RuntimeError("monitor_device_path already bound")
        self.monitor_device_path = monitor_device_path

    def update_canvas(
        self,
        style: WallpaperStyle,
        image: Path | Image.Image,
    ) -> None:
        """
        Buffer a wallpaper image for the target monitor.

        Args:
            style: Wallpaper fit/style (fill, fit, stretch, etc.).
            image: The image path or PIL image to display.
        """
        if self._update_canvas is None:
            raise RuntimeError("update_canvas not bound")
        if self.monitor_device_path is None:
            raise RuntimeError("monitor_device_path not bound")
        self._update_canvas(self.monitor_device_path, style, image)

    def plot_canvas(self) -> None:
        """Ask the wallpaper system to composite the buffered canvas.

        Buffering via :meth:`update_canvas` alone does not apply the wallpaper;
        dynamic resources (e.g. a ``ResourceCycle``) call this to request a
        composite.  In the running app this defers to the controller's worker
        loop rather than compositing synchronously from the calling thread.
        """
        if self._plot_canvas is None:
            raise RuntimeError("plot_canvas not bound")
        self._plot_canvas()

    @abstractmethod
    def mount(self) -> None:
        """
        Lifecycle notification: this resource is now active.

        Subclasses should call :meth:`update_canvas` (or defer to a background
        thread) to buffer wallpaper::

            self.update_canvas(style=..., image=...)

        and call :meth:`plot_canvas` when a composite is wanted, rather than
        setting wallpaper directly, so the caller can control composition and
        batching across multiple displays.

        Note that :meth:`update_canvas` may be called any time after
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
