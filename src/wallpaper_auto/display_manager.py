"""
Per-display wallpaper manager.

Tracks per-monitor wallpaper state across multiple displays,
composites per-monitor images into a spanned wallpaper, and applies
it via the COM IDesktopWallpaper API.
"""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config_store import ConfigStore
from .image_cache import ImageCompressionCache, _resize_image
from .resource.base_resource import BaseResource
from .resource.static_wallpaper import StaticWallpaper
from .util.display_utils import DisplayInfo, get_display_info
from .util.wallpaper_util import (
    WallpaperStyle,
    com_session,
    get_wallpaper,
    get_wallpaper_style,
    set_wallpaper,
    set_wallpaper_style,
)

logger = logging.getLogger(__name__)
logging.getLogger("PIL").setLevel(logging.WARNING)


@dataclass
class DisplayState:
    """Per-display state tracked by :class:`DisplayManager`.

    Attributes:
        monitor_device_path: Unique device path of the monitor.
        resource: The active resource, or the patch ``StaticWallpaper`` when
            ``is_patch`` is True.
        is_patch: True when the display is showing its original wallpaper
            (a patch), False when it shows a resource the app set.
        restore: The display's genuine original ``(style, image_path)``,
            captured while the desktop wallpaper was still untouched by the
            app.  ``None`` for displays added after the app applied its SPAN
            composite — the original is unrecoverable (``IDesktopWallpaper``
            ignores monitorID under SPAN) and must never be restored.
        canvas: The buffered ``(style, image)`` compositing entry, or None.
    """

    monitor_device_path: str
    resource: BaseResource
    is_patch: bool
    restore: tuple[WallpaperStyle, Path] | None = None
    canvas: tuple[WallpaperStyle, Path | Image.Image] | None = None


class DisplayManager:
    """Per-display wallpaper lifecycle — mount/demount per monitor and composite canvas.

    Since ``IDesktopWallpaper`` has a single global ``WallpaperStyle``,
    per-monitor styles are achieved by compositing each monitor's image
    into a spanned wallpaper canvas and applying it with ``SPAN`` style.

    Compositing happens in two stages:
    1. **Resize** — each source image is cover-resized to fill its display
       while preserving aspect ratio (no cropping). Path-based images go
       through ``ImageCompressionCache.render()``; ``CENTER``/``TILE`` styles
       keep native pixels.
    2. **Composite** — ``_render_image_for_region`` crops each resized image
       to its display's exact resolution, centered in the monitor's
       virtual-desktop region, then the canvas is applied as a ``SPAN``
       wallpaper.

    The "patch" mechanism tracks whether a display is showing its original
    (pre-app) wallpaper (``is_patch == True``) or a resource the app set.
    This ensures original wallpapers are restored when the app stops or
    a display is removed.

    **Thread-safety:** every public method is internally synchronized by a
    single ``_lock`` (monitor pattern) — callers never rely on threading
    conventions. Two invariants keep the lock deadlock/stall-free:
      1. ``_lock`` is never held across a resource lifecycle call
         (``demount()``/``mount()``), since ``demount()`` may join a cycling
         thread blocked on the lock and ``mount()`` calls back into
         ``update_canvas()``.
      2. ``_lock`` is never held across a nested public-method call; methods
         that call other public methods compute under the lock, release it,
         then delegate.
    """

    def __init__(self) -> None:
        self._displays: dict[str, DisplayState] = {}
        self._wallpaper_applied: bool = False
        self._image_cache: ImageCompressionCache | None = None
        self._lock = threading.Lock()

    def init_cache(
        self,
        cache_path: Path,
        resize_enabled: bool,
        max_size_bytes: int,
        evict_ratio: float,
    ) -> None:
        """Create and initialize the image compression cache.

        Called from ``WallpaperController.load_config()`` alongside the
        other managers' ``init()`` methods.  When *resize_enabled* is
        false the cache is left as ``None`` and ``_resolve_cached_image``
        opens images directly.
        """
        with self._lock:
            if resize_enabled:
                cache = ImageCompressionCache()
                cache.init(
                    cache_path,
                    max_size_bytes=max_size_bytes,
                    evict_ratio=evict_ratio,
                )
                self._image_cache = cache

    def start(self) -> None:
        """Begin a fresh lifecycle: record originals for all connected displays.

        A composite must never be applied before ``start()``, so
        ``_wallpaper_applied`` must already be ``False`` here (``stop()`` clears
        it at the end of the previous lifecycle).
        """
        with self._lock:
            if self._wallpaper_applied:
                raise RuntimeError("cannot start: a composite was applied before start()")
            curr_display_info = get_display_info()
            if curr_display_info is None:
                return
            paths = [d.monitor_device_path for d in curr_display_info]
        for p in paths:
            self.add_display(p)

    def stop(self) -> None:
        """Revert all displays to their original wallpapers.

        Restores each display that has a genuine ``restore`` record (captured
        at ``start()``) and clears ``_wallpaper_applied`` for the next
        lifecycle.  Displays added after the app applied its SPAN composite
        have no restore record and are simply dropped.
        """
        with self._lock:
            monitors = list(self._displays)
        for m in monitors:
            self.remove_display(m)
        with self._lock:
            for state in self._displays.values():
                if state.restore is not None:
                    set_wallpaper_style(state.restore[0])
                    set_wallpaper(state.monitor_device_path, state.restore[1])
            self._wallpaper_applied = False

    def update_display(self) -> list[DisplayInfo] | None:
        """Detect monitor hotplug events and add/remove displays accordingly.

        Returns:
            Current display info list, or None if displays cannot be queried
            (the sync is skipped and state is left unchanged).
        """
        curr_display_info = get_display_info()
        if curr_display_info is None:
            return None
        with self._lock:
            curr = {d.monitor_device_path for d in curr_display_info}
            active = set(self._displays.keys())
            plugged = curr - active
            unplugged = active - curr
        for p in plugged:
            self.add_display(p)
        for u in unplugged:
            self.remove_display(u)
        return curr_display_info

    @property
    def active_monitor_device_path(self) -> set[str]:
        """Return the set of monitor device paths currently tracked by the manager."""
        with self._lock:
            return set(self._displays.keys())

    def add_display(self, monitor_device_path: str) -> None:
        """Register a newly connected display.

        Creates a patch ``StaticWallpaper`` and stores a genuine ``restore``
        record only while the desktop wallpaper is still untouched by the app.
        Once the app has applied its SPAN composite, ``GetWallpaper`` ignores
        the monitorID and returns the app's own composite — so no restore
        record is kept for such displays.

        Args:
            monitor_device_path: Unique device path of the monitor.
        """
        with self._lock:
            if monitor_device_path in self._displays:
                return
            style = get_wallpaper_style()
            image_path = get_wallpaper(monitor_device_path)
            patch = StaticWallpaper(style, image_path)
            patch._bind_monitor_device_path(monitor_device_path)
            self._displays[monitor_device_path] = DisplayState(
                monitor_device_path=monitor_device_path,
                resource=patch,
                is_patch=True,
                restore=(style, image_path) if not self._wallpaper_applied else None,
            )

    def remove_display(self, monitor_device_path: str) -> None:
        """Revert a display back to its original wallpaper.

        Demounts the active resource, replaces it with a patch
        ``StaticWallpaper`` of the original wallpaper, and re-applies it.
        Displays with no ``restore`` record (added after the app applied its
        composite) are dropped instead of patched — there is no genuine
        original to restore, and mounting a SPAN composite patch would corrupt
        the whole canvas.  Patch displays are always dropped: they have no
        active resource to demount, and a ``restore`` record is either a
        genuine original the patch is already showing or belongs to a display
        that was unplugged.

        Args:
            monitor_device_path: Unique device path of the monitor.
        """
        with self._lock:
            state = self._displays[monitor_device_path]
            if state.is_patch is True:
                # Patch display: nothing to demount, nothing to restore.
                self._displays.pop(monitor_device_path, None)
                return
            resource_to_demount = state.resource
            if state.restore is None:
                # Hotplugged display with a resource: drop it; a re-plug re-adds
                # via add_display().
                self._displays.pop(monitor_device_path, None)
                patch_to_mount = None
            else:
                patch = StaticWallpaper(*state.restore)
                patch._bind_monitor_device_path(monitor_device_path)
                state.resource = patch
                state.is_patch = True
                patch_to_mount = patch
        resource_to_demount.demount()  # outside lock (may join a cycling thread)
        if patch_to_mount is not None:
            patch_to_mount.mount()  # outside lock (→ update_canvas takes the lock)

    def update_resource(self, monitor_device_path: str, resource: BaseResource) -> None:
        """Replace the active resource on a display with a new one.

        Demounts the previous resource, binds the new one, and mounts it.

        Args:
            monitor_device_path: Unique device path of the monitor.
            resource: The new resource to activate.
        """
        with self._lock:
            state = self._displays[monitor_device_path]
            prev = state.resource
            state.resource = resource
            state.is_patch = False
        prev.demount()  # outside lock (may join a cycling thread)
        resource.mount()  # outside lock (→ update_canvas takes the lock)

    def _resolve_cached_image(
        self,
        img: Path | Image.Image,
        w: int,
        h: int,
    ) -> Image.Image:
        """Resolve a buffer entry through the cache, or fall back to ``Image.open``.

        If *img* is a ``Path`` and the cache is available, ``render()`` handles
        hit-or-miss transparently.  If *img* is a ``Path`` but there is no cache,
        open it directly.  Otherwise return the PIL ``Image`` as-is.
        """
        if isinstance(img, Path) and self._image_cache is not None:
            return self._image_cache.render(img, w, h)
        if isinstance(img, Path):
            return Image.open(img)
        return img

    def update_canvas(
        self,
        monitor_device_path: str,
        style: WallpaperStyle,
        image: Path | Image.Image,
    ) -> None:
        """Buffer a per-monitor wallpaper entry for the next composite.

        Registered once on :class:`BaseResource` as the class-wide buffer
        callback (see :meth:`BaseResource.register_update_canvas`).

        Args:
            monitor_device_path: Unique device path of the monitor.
            style: Wallpaper fit/style for this monitor.
            image: Image path or PIL Image to display.
        """
        with self._lock:
            state = self._displays.get(monitor_device_path)
            if state is None:
                # Display removed concurrently (e.g. remove_display on the worker
                # loop while a ResourceCycle cycling thread buffers) — drop the
                # stale update rather than raising KeyError.
                return
            state.canvas = (style, image)

    def plot_canvas(self) -> None:
        """Composite buffered per-monitor images into a spanned wallpaper and apply it.

        Reads the buffered per-monitor entries from ``self._displays``
        (``{monitor_id: (style, image)}``), renders each image into its
        monitor's region on the virtual desktop according to its style, then
        applies the composite as a spanned wallpaper via the COM
        ``IDesktopWallpaper`` API.

        Serialized by ``self._lock``: the composite reads the buffer snapshot
        and writes ``_composite.png`` under the same lock so concurrent
        ``update_canvas`` writes (e.g. from a ``ResourceCycle`` cycling thread)
        cannot corrupt the file.
        """
        with self._lock:
            self._composite_and_apply()

    def _composite_and_apply(self) -> None:
        """Composite the buffered images and apply the result as wallpaper.

        Must be called with ``self._lock`` held.
        """
        buffer = {
            s.monitor_device_path: s.canvas for s in self._displays.values() if s.canvas is not None
        }
        if not buffer:
            return

        displays = get_display_info()
        if displays is None:
            return

        # Compute the union bounding rect of all active monitors in the buffer.
        relevant = [d for d in displays if d.monitor_device_path in buffer]
        if not relevant:
            return

        min_left = min(d.position[0] for d in relevant)
        min_top = min(d.position[1] for d in relevant)
        max_right = max(d.position[0] + d.source_resolution[0] for d in relevant)
        max_bottom = max(d.position[1] + d.source_resolution[1] for d in relevant)

        canvas_w = max_right - min_left
        canvas_h = max_bottom - min_top

        canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
        display_map = {d.monitor_device_path: d for d in relevant}

        # If any resource specifies SPAN, it replaces the entire composite.
        span_entry = next(
            ((mid, path) for mid, (style, path) in buffer.items() if style == WallpaperStyle.SPAN),
            None,
        )
        if span_entry:
            _, img = span_entry
            img = self._resolve_cached_image(img, canvas_w, canvas_h)
            rendered, _, _ = DisplayManager._render_image_for_region(
                img,
                WallpaperStyle.FILL,
                canvas_w,
                canvas_h,
            )
            canvas.paste(rendered, (0, 0))
        else:
            for monitor_id, (style, img) in buffer.items():
                d = display_map.get(monitor_id)
                if d is None:
                    continue
                region_w, region_h = d.source_resolution
                canvas_x = d.position[0] - min_left
                canvas_y = d.position[1] - min_top

                # CENTER/TILE keep native pixels — bypass the cover-resize cache.
                if style in (WallpaperStyle.CENTER, WallpaperStyle.TILE):
                    if isinstance(img, Path):
                        img = Image.open(img)
                else:
                    img = self._resolve_cached_image(img, region_w, region_h)
                rendered, offset_x, offset_y = DisplayManager._render_image_for_region(
                    img,
                    style,
                    region_w,
                    region_h,
                )
                canvas.paste(rendered, (canvas_x + offset_x, canvas_y + offset_y))

        # Save to a temp file in the cache dir.
        cache_dir = ConfigStore.instance.cache_path
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created cache directory: %s", cache_dir)
        temp_path = cache_dir / "_composite.png"
        canvas.save(temp_path, "PNG")

        # Apply as spanned wallpaper. Mark the app as having claimed the desktop
        # BEFORE applying: even a partial apply leaves the wallpaper SPAN, so any
        # display captured afterwards has no genuine original to record.
        self._wallpaper_applied = True
        with com_session():
            set_wallpaper_style(WallpaperStyle.SPAN)
            set_wallpaper(None, temp_path)

    @staticmethod
    def _render_image_for_region(
        img: Image.Image,
        style: WallpaperStyle,
        region_w: int,
        region_h: int,
    ) -> tuple[Image.Image, int, int]:
        """Render *img* into a ``(region_w × region_h)`` rect using *style*.

        Returns ``(rendered_image, offset_x, offset_y)`` so the caller can
        paste at ``(canvas_x + offset_x, canvas_y + offset_y)``.  Only the
        actual image pixels are returned — styles that do not fill the full
        region (CENTER, FIT) leave the surrounding area clean.

        SPAN falls back to FILL (per-monitor spanning is not meaningful).
        Alpha-channel images are composited over a black background first.
        """
        src_w, src_h = img.size

        # Strip alpha against black background.
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        if style == WallpaperStyle.STRETCH:
            return _resize_image(img, region_w, region_h), 0, 0

        if style == WallpaperStyle.FILL:
            scale = max(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = _resize_image(img, new_w, new_h)
            x = (new_w - region_w) // 2
            y = (new_h - region_h) // 2
            return resized.crop((x, y, x + region_w, y + region_h)), 0, 0

        if style == WallpaperStyle.FIT:
            scale = min(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = _resize_image(img, new_w, new_h)
            offset_x = (region_w - new_w) // 2
            offset_y = (region_h - new_h) // 2
            return resized, offset_x, offset_y

        if style == WallpaperStyle.CENTER:
            offset_x = (region_w - src_w) // 2
            offset_y = (region_h - src_h) // 2
            return img, offset_x, offset_y

        if style == WallpaperStyle.TILE:
            canvas = Image.new("RGB", (region_w, region_h), (0, 0, 0))
            for y in range(0, region_h, src_h):
                for x in range(0, region_w, src_w):
                    canvas.paste(img, (x, y))
            return canvas, 0, 0

        # SPAN or unknown — fall back to FILL.
        return DisplayManager._render_image_for_region(img, WallpaperStyle.FILL, region_w, region_h)
