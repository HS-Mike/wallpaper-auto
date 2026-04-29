"""
Static image wallpaper resource.

Mounts a single image file as the Windows desktop wallpaper with configurable
scaling style (fill, fit, stretch, center, tile). 

In some situations, windows fail to load wallpaper if it is too large. 
Optionally compresses large images and caches the result for performance. 
"""
import os
import hashlib
import logging
from enum import Enum
from os import PathLike
from typing import Optional

import win32gui
import win32con
import win32api
from PIL import Image

from .base_resource import BaseResource


logger = logging.getLogger(__name__)


class WallpaperStyle(Enum):
    """
    Registry values for wallpaper scaling mode (combination of WallpaperStyle and TileWallpaper)

    WallpaperStyle: 0=center/tile, 2=stretch, 6=fit, 10=fill
    TileWallpaper: 0=no tile, 1=tile
    """
    FILL = ("10", "0")
    FIT = ("6", "0")
    STRETCH = ("2", "0")
    CENTER = ("0", "0")
    TILE = ("0", "1")


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
        if not self.allow_compress:
            return False
        with Image.open(self.image_path) as img:
            return img.width > self._screen_size[0] * 1.2 or img.height > self._screen_size[1] * 1.2

    def _get_cache_key(self, target_size: tuple[int, int]) -> str:
        """Generate a cache key based on image path, mtime, target size, and format."""
        with Image.open(self.image_path) as img:
            fmt = img.format or "PNG"
        mtime = str(os.path.getmtime(self.image_path))
        key_str = f"{self.image_path}_{mtime}_{target_size[0]}x{target_size[1]}_{fmt}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_compress_cached_path(self) -> str:
        """
        Get or create a cached (compressed) version of the image.

        Returns the path to the cached file, creating it if it doesn't exist.
        The cached image is resized to fit within screen dimensions.
        Uses the same format as the original image for the cached file.
        """
        with Image.open(self.image_path) as img:
            ext = img.format.lower() if img.format else "png"
            if img.width == 0 or img.height == 0:
                raise ValueError(f"Invalid image: {self.image_path}")
            cache_key = self._get_cache_key(self._screen_size)
            cache_path = os.path.join(self.cache_dir, f"{cache_key}.{ext}")

            if os.path.exists(cache_path):
                return cache_path

            scale = max(self._screen_size[0] / img.width, self._screen_size[1] / img.height)
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=3.0)
            img.save(cache_path, ext.upper() if ext.upper() in ("PNG", "JPEG", "JPG") else "PNG")
        return cache_path

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
            logger.debug(f"restore origin wallpaper: {self._original_wallpaper}, style: {self._original_style}")


def get_screen_size() -> tuple[int, int]:
    """Get current primary screen resolution in pixels (width, height)."""
    width = win32api.GetSystemMetrics(0)
    height = win32api.GetSystemMetrics(1)
    return width, height


def get_current_wallpaper() -> str:
    """Get the file path of the current desktop wallpaper from registry."""
    key = win32api.RegOpenKeyEx(
        win32con.HKEY_CURRENT_USER,  # type: ignore
        "Control Panel\\Desktop",
        0,
        win32con.KEY_QUERY_VALUE,  # type: ignore
    )
    try:
        value, _ = win32api.RegQueryValueEx(key, "Wallpaper")
        return value
    finally:
        win32api.RegCloseKey(key)


def get_current_wallpaper_style() -> tuple[str, str]:
    """Get current wallpaper scaling style from registry."""
    key = win32api.RegOpenKeyEx(
        win32con.HKEY_CURRENT_USER, # type: ignore
        "Control Panel\\Desktop",
        0,
        win32con.KEY_QUERY_VALUE, # type: ignore
    )
    try:
        wallpaper_style, _ = win32api.RegQueryValueEx(key, "WallpaperStyle")
        tile_wallpaper, _ = win32api.RegQueryValueEx(key, "TileWallpaper")
        style_tuple = (str(wallpaper_style), str(tile_wallpaper))
        return style_tuple
    finally:
        win32api.RegCloseKey(key)


def set_wallpaper(image_path: PathLike | str, style: tuple[str, str]) -> None:
    """
    Set wallpaper via registry and SystemParametersInfo
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"{image_path} is not exist")

    abs_path = os.path.abspath(image_path).replace("/", "\\")
    wallpaper_style, tile_wallpaper = style

    key = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER, "Control Panel\\Desktop", 0, win32con.KEY_SET_VALUE)  # type: ignore[arg-type,attr-defined]
    win32api.RegSetValueEx(key, "WallpaperStyle", 0, win32con.REG_SZ, wallpaper_style) 
    win32api.RegSetValueEx(key, "TileWallpaper", 0, win32con.REG_SZ, tile_wallpaper)
    win32api.RegCloseKey(key)

    win32gui.SystemParametersInfo(win32con.SPI_SETDESKWALLPAPER, abs_path, 3)
