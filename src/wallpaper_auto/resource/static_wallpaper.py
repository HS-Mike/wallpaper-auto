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
    WallpaperPosition,
    com_session,
    get_wallpaper,
    get_wallpaper_position,
    set_wallpaper as set_per_display_wallpaper,
    set_wallpaper_position,
)
from .base_resource import BaseResource
from .wallpaper_utils import (
    WallpaperStyle,
    compress_image,
    get_screen_size,
)

logger = logging.getLogger(__name__)

# Map WallpaperStyle enum to IDesktopWallpaper position constants.
_STYLE_TO_DWPOS: dict[WallpaperStyle, WallpaperPosition] = {
    WallpaperStyle.CENTER: WallpaperPosition.CENTER,
    WallpaperStyle.TILE: WallpaperPosition.TILE,
    WallpaperStyle.STRETCH: WallpaperPosition.STRETCH,
    WallpaperStyle.FIT: WallpaperPosition.FIT,
    WallpaperStyle.FILL: WallpaperPosition.FILL,
}


class StaticWallpaper(BaseResource):
    def __init__(
        self,
        path: PathLike[str] | str,
        style: WallpaperStyle | str = WallpaperStyle.FILL,
        allow_compress: bool = True,
        restore: bool = False,
    ):
        self.image_path = Path(path)
        if isinstance(style, str):
            style = WallpaperStyle[style.upper()]
        self.style = style
        self.allow_compress = allow_compress
        self.restore = restore

        self._screen_size = get_screen_size()
        self.cache_dir: Path = ConfigStore.instance.cache_path
        self.mount_path: Path | None = None

        # Per-display mount tracking.
        self._monitor_device_path: str | None = None
        self._original_wallpaper: Path | None = None
        self._original_position: int | None = None

    def _cache_key(self) -> str:
        """Generate an md5 hash key for the cache file based on path, mtime, and screen size."""
        image_path = str(self.image_path)
        mtime = str(Path(image_path).stat().st_mtime)
        key_str = f"{image_path}_{mtime}_{self._screen_size[0]}x{self._screen_size[1]}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _check_need_cache(self) -> bool:
        """Check if the image is large enough to need compression caching."""
        if not self.allow_compress:
            return False
        with Image.open(self.image_path) as img:
            return img.width > self._screen_size[0] * 1.2 or img.height > self._screen_size[1] * 1.2

    def prepare_wallpaper(self) -> None:
        """Prepare the wallpaper resource.

        Ensures the cache directory exists, checks whether the image is large
        enough to need compression, and creates a cached version if needed.
        Sets ``self.mount_path`` to either the compressed cache or the original
        image path.
        """
        self._screen_size = get_screen_size()
        if not self._check_need_cache():
            self.mount_path = self.image_path
            return
        if not self.cache_dir.exists():
            raise FileNotFoundError(f"cache directory '{self.cache_dir}' does not exist")
        with Image.open(self.image_path) as img:
            ext = (img.format or "png").lower()
        cache_key = self._cache_key()
        save_path = self.cache_dir / f"{cache_key}.{ext}"
        if not save_path.exists():
            compress_image(str(self.image_path), self._screen_size, str(save_path))
        self.mount_path = save_path
        logger.info("static wallpaper cache: %s", self.mount_path)

    def mount(self, monitor_device_path: str) -> None:
        """
        Apply the image as wallpaper on the specified display.

        Calls :meth:`prepare_wallpaper` first to ensure ``self.mount_path``
        is set (with cache if needed), then saves the current wallpaper
        before replacing it via the COM per-display API.
        """
        self.prepare_wallpaper()
        self._monitor_device_path = monitor_device_path

        with com_session():
            orig = get_wallpaper(monitor_device_path)
            orig_pos = get_wallpaper_position(monitor_device_path)
            self._original_wallpaper = Path(orig) if orig else None
            self._original_position = orig_pos

            assert self.mount_path is not None
            dwpos = _STYLE_TO_DWPOS[self.style]
            set_per_display_wallpaper(monitor_device_path, str(self.mount_path))
            set_wallpaper_position(monitor_device_path, dwpos)

            logger.info(
                "static_wallpaper: '%s' <- %s (style=%s)",
                monitor_device_path,
                self.mount_path,
                self.style.name,
            )

    def demount(self) -> None:
        """Restore the original wallpaper for the display mounted on.

        When *restore* is ``False`` (set at init time), no-op.
        """
        if not self.restore or self._original_wallpaper is None:
            return
        assert self._monitor_device_path is not None
        assert self._original_position is not None

        with com_session():
            set_per_display_wallpaper(
                self._monitor_device_path, str(self._original_wallpaper)
            )
            set_wallpaper_position(
                self._monitor_device_path, self._original_position
            )
            logger.info(
                "static_wallpaper: restore '%s' <- %s",
                self._monitor_device_path,
                self._original_wallpaper,
            )

        self._original_wallpaper = None
        self._original_position = None
        self._monitor_device_path = None
