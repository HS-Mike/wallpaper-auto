"""
Shared Windows wallpaper utilities.

Provides Windows API functions for wallpaper management, the WallpaperStyle
enum, and image compression helpers used by multiple resource types.
"""

import ctypes
import hashlib
import logging
import os
from enum import Enum
from os import PathLike

import win32api
import win32con
import win32gui
from PIL import Image

logger = logging.getLogger(__name__)

logging.getLogger("PIL").setLevel(logging.WARNING)


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


def get_screen_size() -> tuple[int, int]:
    """Get current primary screen resolution in pixels (width, height)."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    dc = user32.GetDC(0)
    width: int = gdi32.GetDeviceCaps(dc, 118)
    height: int = gdi32.GetDeviceCaps(dc, 117)
    user32.ReleaseDC(0, dc)
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
        win32con.HKEY_CURRENT_USER,  # type: ignore
        "Control Panel\\Desktop",
        0,
        win32con.KEY_QUERY_VALUE,  # type: ignore
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

    key = win32api.RegOpenKeyEx(
        win32con.HKEY_CURRENT_USER,  # type: ignore[arg-type,attr-defined]
        "Control Panel\\Desktop",
        0,
        win32con.KEY_SET_VALUE,  # type: ignore[attr-defined]
    )
    win32api.RegSetValueEx(key, "WallpaperStyle", 0, win32con.REG_SZ, wallpaper_style)
    win32api.RegSetValueEx(key, "TileWallpaper", 0, win32con.REG_SZ, tile_wallpaper)
    win32api.RegCloseKey(key)

    win32gui.SystemParametersInfo(win32con.SPI_SETDESKWALLPAPER, abs_path, 3)


def check_need_cache(
    image_path: str,
    screen_size: tuple[int, int],
    allow_compress: bool,
) -> bool:
    """Check if the image is large enough to need compression caching."""
    if not allow_compress:
        return False
    with Image.open(image_path) as img:
        return img.width > screen_size[0] * 1.2 or img.height > screen_size[1] * 1.2


def get_cache_key(image_path: str, target_size: tuple[int, int]) -> str:
    """Generate a cache key based on image path, mtime, target size, and format."""
    with Image.open(image_path) as img:
        fmt = img.format or "PNG"
    mtime = str(os.path.getmtime(image_path))
    key_str = f"{image_path}_{mtime}_{target_size[0]}x{target_size[1]}_{fmt}"
    return hashlib.md5(key_str.encode()).hexdigest()


def get_compress_cached_path(
    image_path: str,
    screen_size: tuple[int, int],
    cache_dir: str,
) -> str:
    """
    Get or create a cached (compressed) version of the image.

    Returns the path to the cached file, creating it if it doesn't exist.
    The cached image is resized to fit within screen dimensions.
    Uses the same format as the original image for the cached file.
    """
    with Image.open(image_path) as img:
        ext = img.format.lower() if img.format else "png"
        if img.width == 0 or img.height == 0:
            raise ValueError(f"Invalid image: {image_path}")
        cache_key = get_cache_key(image_path, screen_size)
        cache_path = os.path.join(cache_dir, f"{cache_key}.{ext}")

        if os.path.exists(cache_path):
            return cache_path

        scale = max(screen_size[0] / img.width, screen_size[1] / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=3.0)
        ext_upper = ext.upper()
        img.save(
            cache_path,
            ext_upper if ext_upper in ("PNG", "JPEG", "JPG") else "PNG",
        )
    return cache_path
