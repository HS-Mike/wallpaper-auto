"""
Dynamic wallpaper resource that cycles through multiple images.

Mounts and cycles through a collection of images on a configurable interval,
optionally in random order. Each image can be independently compressed and
cached like the static wallpaper resource.
"""

import logging
import random
import threading
from os import PathLike
from typing import Optional

from .base_resource import BaseResource
from .wallpaper_utils import (
    WallpaperStyle,
    check_need_cache,
    get_compress_cached_path,
    get_current_wallpaper,
    get_current_wallpaper_style,
    get_screen_size,
    set_wallpaper,
)

logger = logging.getLogger(__name__)


class DynamicWallpaper(BaseResource):
    """
    A wallpaper resource that cycles through multiple images.

    On mount, saves the current wallpaper, applies the first image, and starts
    a background thread that advances to the next image every *interval*
    seconds. On demount, stops the thread and restores the original wallpaper.

    Args:
        paths: One or more image file paths to cycle through.
        style: Wallpaper scaling style (enum or uppercase string).
        interval: Seconds between automatic image switches (default 300).
        random: If True, pick images in random order; otherwise sequential.
        allow_compress: Whether to compress oversized images (default True).

    Raises:
        ValueError: If *paths* is empty.
    """

    def __init__(
        self,
        paths: list[PathLike | str],
        style: WallpaperStyle | str = WallpaperStyle.FILL,
        interval: int = 300,
        random: bool = False,
        allow_compress: bool = True,
    ):
        self.paths = [str(p) for p in paths]
        if not self.paths:
            raise ValueError("At least one path is required")

        if isinstance(style, str):
            style = WallpaperStyle[style.upper()]
        self.style = style
        self.interval = interval
        self.random = random
        self.allow_compress = allow_compress
        self._screen_size = get_screen_size()

        super().__init__(temp_dir=self.allow_compress)

        # Lazily populated compressed-path dict: original → compressed (or identity)
        self._compressed_paths: dict[str, str] = {}

        # Threading state
        self._stop_event = threading.Event()
        self._cycling_thread: Optional[threading.Thread] = None
        self._index = 0
        self._cycle_lock = threading.Lock()

        # Original wallpaper tracking
        self._original_wallpaper: Optional[str] = None
        self._original_style: Optional[tuple[str, str]] = None

    # ---- Private helpers ---------------------------------------------------

    def _get_current_image_path(self) -> str:
        """Return the compressed (or original) path for the current index."""
        return self._get_compressed(self.paths[self._index])

    def _get_compressed(self, original_path: str) -> str:
        """Get compressed path for an image, compressing lazily on first access."""
        if original_path not in self._compressed_paths:
            if check_need_cache(original_path, self._screen_size, self.allow_compress):
                cached = get_compress_cached_path(original_path, self._screen_size, self.cache_dir)
                logger.info("compress %s to %s", original_path, cached)
                self._compressed_paths[original_path] = cached
            else:
                self._compressed_paths[original_path] = original_path
        return self._compressed_paths[original_path]

    def _advance_index(self) -> None:
        """Move to the next image index (sequential or random)."""
        with self._cycle_lock:
            if self.random:
                self._index = random.randrange(len(self.paths))
            else:
                self._index = (self._index + 1) % len(self.paths)

    def _apply_current(self) -> None:
        """Set the wallpaper to the image at the current index."""
        image_path = self._get_current_image_path()
        set_wallpaper(image_path, self.style.value)
        logger.debug("dynamic wallpaper cycled to: %s", image_path)

    def _cycling_loop(self) -> None:
        """Background thread: cycle wallpaper at the configured interval."""
        logger.debug("dynamic wallpaper cycling thread start")
        while not self._stop_event.wait(timeout=self.interval):
            self._advance_index()
            self._apply_current()
        logger.debug("dynamic wallpaper cycling thread exit")

    # ---- Lifecycle ---------------------------------------------------------

    def mount(self) -> None:
        """
        Start the wallpaper cycling.

        Saves the current wallpaper path and style, applies the first image,
        then launches a daemon thread that cycles through images every
        *interval* seconds.
        """
        self._original_wallpaper = get_current_wallpaper()
        self._original_style = get_current_wallpaper_style()
        logger.debug(
            "origin wallpaper: %s, style: %s",
            self._original_wallpaper,
            self._original_style,
        )

        # Apply the first (current) image
        self._apply_current()

        # Start the cycling thread
        self._stop_event.clear()
        self._cycling_thread = threading.Thread(target=self._cycling_loop, daemon=True)
        self._cycling_thread.start()

    def demount(self) -> None:
        """
        Stop cycling and restore the original wallpaper.

        Signals the cycling thread to exit, waits for it to finish, and
        restores the wallpaper that was active before mount() was called.
        Safe to call without a prior mount (no-op).
        """
        if self._cycling_thread is not None:
            self._stop_event.set()
            self._cycling_thread.join(timeout=5.0)
            self._cycling_thread = None

        if self._original_wallpaper and self._original_style:
            set_wallpaper(self._original_wallpaper, self._original_style)
            logger.debug(
                "restore origin wallpaper: %s, style: %s",
                self._original_wallpaper,
                self._original_style,
            )
            self._original_wallpaper = None
            self._original_style = None
