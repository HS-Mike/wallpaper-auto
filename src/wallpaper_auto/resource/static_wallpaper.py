"""
Static image wallpaper resource.

Mounts a single image file as the desktop wallpaper with configurable
scaling style (fill, fit, stretch, center, tile).

In some situations, windows fail to load wallpaper if it is too large.
Optionally compresses large images and caches the result for performance.

The cache directory is obtained from :class:`ConfigStore` so it persists
across application restarts and is shared by all resources.
"""
import hashlib
import logging
from os import PathLike
from pathlib import Path

from PIL import Image

from ..config_store import ConfigStore
from ..util.wallpaper_util import (
    WallpaperStyle,
    com_session,
    get_wallpaper,
    get_wallpaper_style,
    set_wallpaper,
    set_wallpaper_style,
)
from .base_resource import BaseResource, PlotCanvasProtocol


logger = logging.getLogger(__name__)


class StaticWallpaper(BaseResource):
    def __init__(
        self,
        style: WallpaperStyle,
        path: Path | PathLike[str] | str,
    ):
        super().__init__()
        self.image_path = Path(path)
        if isinstance(style, str):
            style = WallpaperStyle[style.upper()]
        self.style = style

        self._original_style: WallpaperStyle | None = None
        self._original_wallpaper_path: Path | None = None

    def mount(self, plot_canvas: PlotCanvasProtocol) -> None:
        if self._original_style is None and self._original_wallpaper_path is None:
            with com_session():
                self._original_style = get_wallpaper_style()
                self._original_wallpaper_path = get_wallpaper(self.monitor_device_path)
        plot_canvas(self.style, self.image_path, immediate_update=False)

    def demount(self) -> None:
        if self._original_style is not None and self._original_wallpaper_path is not None:
            with com_session():
                set_wallpaper_style(self._original_style)
                set_wallpaper(self.monitor_device_path, self._original_wallpaper_path)
