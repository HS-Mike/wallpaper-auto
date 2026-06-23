"""
Static image wallpaper resource.

Mounts a single image file as the Windows desktop wallpaper with configurable
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
from .base_resource import BaseResource
from .wallpaper_utils import (
    WallpaperStyle,
    compress_image,
    get_current_wallpaper,
    get_current_wallpaper_style,
    get_screen_size,
    set_wallpaper,
)

logger = logging.getLogger(__name__)


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

        self._original_wallpaper: Path | None = None
        self._original_style: tuple[str, str] | None = None

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

    def mount(self) -> None:
        """
        Apply the static image as the desktop wallpaper.

        Calls :meth:`prepare_wallpaper` first to ensure ``self.mount_path``
        is set (with cache if needed), then saves the current wallpaper path
        and style before replacing them.
        """
        self.prepare_wallpaper()
        self._original_wallpaper = Path(get_current_wallpaper())
        self._original_style = get_current_wallpaper_style()
        logger.debug(
            "origin wallpaper: %s, style: %s",
            self._original_wallpaper,
            self._original_style,
        )
        set_wallpaper(self.mount_path, self.style.value)
        logger.debug("mount wallpaper: %s", self.mount_path)

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
