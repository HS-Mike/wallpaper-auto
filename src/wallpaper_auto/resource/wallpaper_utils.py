"""
Shared Windows wallpaper utilities.

Provides Windows API functions for wallpaper management, the WallpaperStyle
enum, and image compression helpers used by multiple resource types.
"""

import ctypes
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
        win32con.HKEY_CURRENT_USER,
        "Control Panel\\Desktop",
        0,
        win32con.KEY_QUERY_VALUE,
    )
    try:
        value, _ = win32api.RegQueryValueEx(key, "Wallpaper")
        return str(value)
    finally:
        win32api.RegCloseKey(key)


def get_current_wallpaper_style() -> tuple[str, str]:
    """Get current wallpaper scaling style from registry."""
    key = win32api.RegOpenKeyEx(
        win32con.HKEY_CURRENT_USER,
        "Control Panel\\Desktop",
        0,
        win32con.KEY_QUERY_VALUE,
    )
    try:
        wallpaper_style, _ = win32api.RegQueryValueEx(key, "WallpaperStyle")
        tile_wallpaper, _ = win32api.RegQueryValueEx(key, "TileWallpaper")
        style_tuple = (str(wallpaper_style), str(tile_wallpaper))
        return style_tuple
    finally:
        win32api.RegCloseKey(key)


def set_wallpaper(image_path: PathLike[str] | str, style: tuple[str, str]) -> None:
    """
    Set wallpaper via registry and SystemParametersInfo
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"{image_path} is not exist")

    abs_path = os.path.abspath(image_path).replace("/", "\\")
    wallpaper_style, tile_wallpaper = style

    key = win32api.RegOpenKeyEx(
        win32con.HKEY_CURRENT_USER,
        "Control Panel\\Desktop",
        0,
        win32con.KEY_SET_VALUE,
    )
    win32api.RegSetValueEx(key, "WallpaperStyle", 0, win32con.REG_SZ, wallpaper_style)
    win32api.RegSetValueEx(key, "TileWallpaper", 0, win32con.REG_SZ, tile_wallpaper)
    win32api.RegCloseKey(key)

    win32gui.SystemParametersInfo(win32con.SPI_SETDESKWALLPAPER, abs_path, 3)


def compress_image(
    image_path: str,
    screen_size: tuple[int, int],
    save_path: str,
) -> None:
    """
    Get or create a cached (compressed) version of the image.

    Returns *save_path*, creating the compressed file there if it doesn't already
    exist. The cached image is resized to fit within screen dimensions and saved
    using the same format as the original image.
    """
    if os.path.exists(save_path):
        raise FileExistsError(f"Cache already exists: {save_path}")

    with Image.open(image_path) as img:
        if img.width == 0 or img.height == 0:
            raise ValueError(f"Invalid image: {image_path}")
        scale = max(screen_size[0] / img.width, screen_size[1] / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=3.0)
        ext = (img.format or "png").lower()

    ext_upper = ext.upper()
    img_resized.save(
        save_path,
        ext_upper if ext_upper in ("PNG", "JPEG", "JPG") else "PNG",
    )
