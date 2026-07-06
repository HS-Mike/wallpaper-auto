"""
Static image wallpaper resource.

Mounts a single image file as the desktop wallpaper with configurable
scaling style (fill, fit, stretch, center, tile).

In some situations, windows fail to load wallpaper if it is too large.
Optionally compresses large images and caches the result for performance.

The cache directory is obtained from :class:`ConfigStore` so it persists
across application restarts and is shared by all resources.
"""
import logging
from os import PathLike
from pathlib import Path


from ..util.wallpaper_util import (
    WallpaperStyle,
)
from .base_resource import BaseResource


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

    def mount(self) -> None:
        self.plot_canvas(self.style, self.image_path, immediate_update=False)

    def demount(self) -> None:
        ...