"""
Static image wallpaper resource.

Mounts a single image file as the Windows desktop wallpaper with configurable
scaling style (fill, fit, stretch, center, tile).

In some situations, windows fail to load wallpaper if it is too large.
Optionally compresses large images and caches the result for performance.

This module also defines :class:`CachedResource`, an intermediate base class
for resources that need a cache directory (used by ``StaticWallpaper``).
"""

import atexit
import logging
import os
import shutil
import tempfile
import threading
import uuid
from os import PathLike

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


_created_temp_dirs: list[str] = []
_lock = threading.Lock()


def _cleanup_temp_dirs() -> None:
    """Remove all auto-created temp cache directories on process exit."""
    with _lock:
        dirs = _created_temp_dirs.copy()
        _created_temp_dirs.clear()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_temp_dirs)


class CachedResource(BaseResource):
    """
    Intermediate base for resources that need a cache directory.

    Each instance is allocated either a user-specified cache directory or
    an auto-created temporary directory.  The cache directory is guaranteed
    to exist after ``__init__`` returns.

    Auto-created temp directories are cleaned up on process exit.  User-
    specified directories are never removed automatically.
    """

    def __init__(self, cache_dir: str | None = None):
        if cache_dir is not None:
            # User-specified path — use directly, no exit cleanup
            self._cache_dir = cache_dir
        else:
            # Auto temp dir — cleaned up on exit
            dir_name = f"wallpaper_automator_{uuid.uuid4().hex}"
            self._cache_dir = os.path.join(tempfile.gettempdir(), dir_name)
            with _lock:
                _created_temp_dirs.append(self._cache_dir)
        os.makedirs(self._cache_dir, exist_ok=True)

    @property
    def cache_dir(self) -> str:
        """Path to this instance's cache directory."""
        return self._cache_dir


class StaticWallpaper(CachedResource):
    def __init__(
        self,
        path: PathLike[str] | str,
        style: WallpaperStyle | str = WallpaperStyle.FILL,
        allow_compress: bool = True,
        restore: bool = False,
        cache_dir: str | None = None,
    ):
        self.image_path = str(path)
        if isinstance(style, str):
            style = WallpaperStyle[style.upper()]
        self.style = style
        self.allow_compress = allow_compress
        self.restore = restore
        self._screen_size = get_screen_size()
        self._need_cache: bool = self._check_need_cache()
        super().__init__(cache_dir=cache_dir)
        self._compress_path: str | None = None
        self._original_wallpaper: str | None = None
        self._original_style: tuple[str, str] | None = None

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
        logger.debug(
            "origin wallpaper: %s, style: %s",
            self._original_wallpaper,
            self._original_style,
        )
        if self._need_cache:
            if self._compress_path is None:
                self._compress_path = self._get_compress_cached_path()
                logger.info("static wallpaper cache: %s", self._compress_path)
            image_path = self._compress_path
        else:
            image_path = self.image_path
        set_wallpaper(image_path, self.style.value)
        logger.debug("mount wallpaper: %s", image_path)

    def demount(self) -> None:
        """Restore the original wallpaper and style.

        When *restore* is ``False`` (set at init time), the original wallpaper
        is *not* restored and the current wallpaper remains in place.
        """
        if self.restore and self._original_wallpaper and self._original_style:
            set_wallpaper(self._original_wallpaper, self._original_style)
            logger.debug(
                "restore origin wallpaper: %s, style: %s",
                self._original_wallpaper,
                self._original_style,
            )
            self._original_wallpaper = None
            self._original_style = None
