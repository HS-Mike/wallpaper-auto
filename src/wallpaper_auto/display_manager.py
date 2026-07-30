"""
Per-display wallpaper manager.

Tracks per-monitor wallpaper state across multiple displays,
composites per-monitor images into a spanned wallpaper, and applies
it via the COM IDesktopWallpaper API.
"""

import logging
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


class DisplayManager:
    """Per-display wallpaper lifecycle — mount/demount per monitor and composite canvas.

    Since ``IDesktopWallpaper`` has a single global ``WallpaperStyle``,
    per-monitor styles are achieved by compositing each monitor's image
    into a spanned wallpaper canvas and applying it with ``SPAN`` style.

    The "patch" mechanism tracks whether a display is showing its original
    (pre-app) wallpaper (``is_patch == True``) or a resource the app set.
    This ensures original wallpapers are restored when the app stops or
    a display is removed.
    """

    def __init__(self) -> None:
        self._restore_wallpaper: dict[str, tuple[WallpaperStyle, Path]] = {}
        self._display_resource_map: dict[str, BaseResource] = {}
        self._display_resource_is_patch: dict[str, bool] = {}
        self._canvas_buffer: dict[str, tuple[WallpaperStyle, Path | Image.Image]] = {}
        self._image_cache: ImageCompressionCache | None = None

    def start(self) -> None:
        """Record original wallpaper for all connected displays and register as patches."""
        curr_display_info: list[DisplayInfo] = get_display_info()
        for i in curr_display_info:
            self.add_display(i.monitor_device_path)

    def stop(self, restore: bool) -> None:
        """Revert all displays to original wallpapers and optionally restore global style.

        Args:
            restore: If True, also restore the original ``WallpaperStyle``
                and per-monitor images via the COM API.
        """
        # restore original wallpaper status
        for i in self._display_resource_map:
            self.remove_display(i)
        if restore is True:
            for device_path, (style, image_path) in self._restore_wallpaper.items():
                set_wallpaper_style(style)
                set_wallpaper(device_path, image_path)

    def update_display(self) -> list[DisplayInfo]:
        """Detect monitor hotplug events and add/remove displays accordingly.

        Returns:
            Current display info list for all connected monitors.
        """
        curr_display_info = get_display_info()
        curr_monitor_device_path = {i.monitor_device_path for i in curr_display_info}
        active_monitor_device_path = self.active_monitor_device_path
        plugged_display = curr_monitor_device_path - active_monitor_device_path
        unplugged_display = active_monitor_device_path - curr_monitor_device_path
        for i in plugged_display:
            self.add_display(i)
        for i in unplugged_display:
            self.remove_display(i)
        return curr_display_info

    @property
    def active_monitor_device_path(self) -> set[str]:
        """Return the set of monitor device paths currently tracked by the manager."""
        return set(self._display_resource_map.keys())

    def add_display(self, monitor_device_path: str) -> None:
        """Register a newly connected display and save its original wallpaper.

        Creates a patch ``StaticWallpaper`` with the current wallpaper and
        style, binds it to the display, and stores it for later restoration.

        Args:
            monitor_device_path: Unique device path of the monitor.
        """
        if monitor_device_path in self._display_resource_map:
            return
        style = get_wallpaper_style()
        image_path = get_wallpaper(monitor_device_path)
        restore_res = StaticWallpaper(style, image_path)
        restore_res._bind_monitor_device_path(monitor_device_path)
        restore_res._bind_plot_canvas(self.update_canvas_buffer)
        self._restore_wallpaper[monitor_device_path] = (style, image_path)
        self._display_resource_map[monitor_device_path] = restore_res
        self._display_resource_is_patch[monitor_device_path] = True

    def remove_display(self, monitor_device_path: str) -> None:
        """Revert a display back to its original wallpaper.

        Demounts the active resource, replaces it with a patch
        ``StaticWallpaper`` of the original wallpaper, and re-applies it.

        Args:
            monitor_device_path: Unique device path of the monitor.

        Raises:
            ValueError: If the display is already a patch (no active
                resource to remove).
        """
        is_patch = self._display_resource_is_patch[monitor_device_path]
        if is_patch is True:
            raise ValueError(
                f"Display (monitor_device_path: {monitor_device_path}) "
                "does not have a resource"
            )
        res = self._display_resource_map[monitor_device_path]
        res.demount()
        res._unbind_plot_canvas()
        orig_patch_res = StaticWallpaper(*self._restore_wallpaper[monitor_device_path])
        orig_patch_res._bind_monitor_device_path(monitor_device_path)
        self._display_resource_map[monitor_device_path] = orig_patch_res
        self._display_resource_is_patch[monitor_device_path] = True
        orig_patch_res._bind_plot_canvas(self.update_canvas_buffer)
        orig_patch_res.mount()

    def update_resource(self, monitor_device_path: str, resource: BaseResource) -> None:
        """Replace the active resource on a display with a new one.

        Demounts the previous resource, binds the new one, and mounts it.

        Args:
            monitor_device_path: Unique device path of the monitor.
            resource: The new resource to activate.
        """
        prev_resource = self._display_resource_map[monitor_device_path]
        if prev_resource is not None:
            prev_resource.demount()
            prev_resource._unbind_plot_canvas()
        self._display_resource_map[monitor_device_path] = resource
        self._display_resource_is_patch[monitor_device_path] = False
        resource._bind_plot_canvas(self.update_canvas_buffer)
        resource.mount()

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

    def update_canvas_buffer(
        self,
        monitor_device_path: str,
        style: WallpaperStyle,
        image: Path | Image.Image,
        immediate_update: bool = False,
    ) -> None:
        """Buffer a per-monitor wallpaper entry for the next composite.

        Args:
            monitor_device_path: Unique device path of the monitor.
            style: Wallpaper fit/style for this monitor.
            image: Image path or PIL Image to display.
            immediate_update: If True, trigger ``plot_canvas()`` immediately.
        """
        self._canvas_buffer[monitor_device_path] = (style, image)
        if immediate_update is True:
            self.plot_canvas()

    def plot_canvas(self) -> None:
        """Composite buffered per-monitor images into a spanned wallpaper and apply it.

        Reads ``self._canvas_buffer`` (``{monitor_id: (style, image)}``),
        renders each image into its monitor's region on the virtual desktop
        according to its style, then applies the composite as a spanned
        wallpaper via the COM ``IDesktopWallpaper`` API.
        """
        buffer = dict(self._canvas_buffer)
        if not buffer:
            return

        displays = get_display_info()

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

        # Lazy-init the image compression cache.
        if self._image_cache is None and ConfigStore.has_instance():
            self._image_cache = ImageCompressionCache(ConfigStore.instance.cache_path)

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
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_path = cache_dir / "_composite.png"
        canvas.save(temp_path, "PNG")

        # Apply as spanned wallpaper.
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
