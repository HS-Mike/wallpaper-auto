"""
Static image wallpaper resource.

Mounts a single image file as the Windows desktop wallpaper with configurable
scaling style (fill, fit, stretch, center, tile).

In some situations, windows fail to load wallpaper if it is too large.
Optionally compresses large images and caches the result for performance.
"""
import logging
from os import PathLike
from typing import Optional

from .base_resource import BaseResource
from .wallpaper_utils import (
    WallpaperStyle,
    check_need_cache,
    get_cache_key,
    get_compress_cached_path,
    get_current_wallpaper,
    get_current_wallpaper_style,
    get_screen_size,
    set_wallpaper,
)

logger = logging.getLogger(__name__)


class StaticWallpaper(BaseResource):

    def __init__(
        self,
        path: PathLike | str,
        style: WallpaperStyle | str = WallpaperStyle.FILL,
        allow_compress: bool = True,
    ):
        self.image_path = str(path)
        if isinstance(style, str):
            style = WallpaperStyle[style.upper()]
        self.style = style
        self.allow_compress = allow_compress
        self._screen_size = get_screen_size()
        self._need_cache: bool = self._check_need_cache()
        super().__init__(temp_dir=self._need_cache)
        if self._need_cache is True:
            self._compress_path = self._get_compress_cached_path()
            logger.info(f"compress {self.image_path} to {self._compress_path}")
        else:
            self._compress_path = None
        self._original_wallpaper: Optional[str] = None
        self._original_style: Optional[tuple[str, str]] = None

    def _check_need_cache(self) -> bool:
        """Check if the image is large enough to need compression caching."""
        return check_need_cache(self.image_path, self._screen_size, self.allow_compress)

    def _get_cache_key(self, target_size: tuple[int, int]) -> str:
        """Generate a cache key based on image path, mtime, target size, and format."""
        return get_cache_key(self.image_path, target_size)

    def _get_compress_cached_path(self) -> str:
        """
        Get or create a cached (compressed) version of the image.

        Returns the path to the cached file, creating it if it doesn't exist.
        The cached image is resized to fit within screen dimensions.
        Uses the same format as the original image for the cached file.
        """
        return get_compress_cached_path(self.image_path, self._screen_size, self.cache_dir)

    def mount(self) -> None:
        """
        Apply the static image as the desktop wallpaper.

        Saves the current wallpaper path and style before replacing them.
        Uses compressed cache if the image is large and allow_compress is True.
        """
        self._original_wallpaper = get_current_wallpaper()
        self._original_style = get_current_wallpaper_style()
        logger.debug(f"origin wallpaper: {self._original_wallpaper}, style: {self._original_style}")
        if self._need_cache is True:
            assert self._compress_path
            image_path = self._compress_path
        else:
            image_path = self.image_path
        set_wallpaper(image_path, self.style.value)
        logger.debug(f"mount wallpaper: {image_path}")

    def demount(self) -> None:
        """Restore the original wallpaper and style."""
        if self._original_wallpaper and self._original_style:
            set_wallpaper(self._original_wallpaper, self._original_style)
            logger.debug(
                "restore origin wallpaper: %s, style: %s",
                self._original_wallpaper,
                self._original_style,
            )
