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
    """Per-display wallpaper lifecycle — mount/demount per monitor and composite canvas."""

    def __init__(self) -> None:
        self._restore_wallpaper: dict[str, BaseResource] = {}
        self._display_resource_map: dict[str, BaseResource]  = {}
        self._canvas_buffer: dict[str, tuple[WallpaperStyle, Path | Image.Image]] = {}

    def start(self) -> None:
        # record original wallpaper status
        curr_display_info: list[DisplayInfo] = get_display_info()
        for i in curr_display_info:
            self.add_display(i.monitor_device_path)

    def stop(self) -> None:
        # restore original wallpaper status
        curr_display_info: list[DisplayInfo] = get_display_info()
        for i in curr_display_info:
            self.remove_display(i.monitor_device_path)

    @property
    def active_monitor_device_path(self) -> set[str]:
        return set(self._display_resource_map.keys())

    def add_display(self, monitor_device_path: str) -> None:
        if monitor_device_path in self._display_resource_map:
            return
        style = get_wallpaper_style()
        image_path = get_wallpaper(monitor_device_path)
        restore_res = StaticWallpaper(style, image_path)
        restore_res._bind_monitor_device_path(monitor_device_path)
        restore_res._bind_plot_canvas(self.update_canvas_buffer)
        self._restore_wallpaper[monitor_device_path] = restore_res
        self._display_resource_map[monitor_device_path] = restore_res

    def remove_display(self, monitor_device_path: str) -> None:
        res = self._display_resource_map.pop(monitor_device_path)
        res.demount()
        res._unbind_plot_canvas()
        original_res = self._restore_wallpaper.pop(monitor_device_path)
        original_res._bind_plot_canvas(self.update_canvas_buffer)
        original_res.mount()

    def update_resource(self, monitor_device_path: str, resource: BaseResource) -> None:
        prev_resource = self._display_resource_map.pop(monitor_device_path)
        prev_resource.demount()
        prev_resource._unbind_plot_canvas()
        self._display_resource_map[monitor_device_path] = resource
        resource._bind_plot_canvas(self.update_canvas_buffer)
        resource.mount()

    def update_canvas_buffer(
        self,
        monitor_device_path: str,
        style: WallpaperStyle,
        image: Path | Image.Image,
        immediate_update: bool = False,
    ) -> None:
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

        # If any resource specifies SPAN, it replaces the entire composite.
        span_entry = next(
            ((mid, path) for mid, (style, path) in buffer.items() if style == WallpaperStyle.SPAN),
            None,
        )
        if span_entry:
            _, img = span_entry
            if isinstance(img, Path):
                img = Image.open(img)
            rendered, _, _ = DisplayManager._render_image_for_region(
                img, WallpaperStyle.FILL, canvas_w, canvas_h,
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

                if isinstance(img, Path):
                    img = Image.open(img)
                rendered, offset_x, offset_y = DisplayManager._render_image_for_region(
                    img, style, region_w, region_h,
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
            return img.resize((region_w, region_h), Image.Resampling.LANCZOS, reducing_gap=3), 0, 0

        if style == WallpaperStyle.FILL:
            scale = max(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=3)
            x = (new_w - region_w) // 2
            y = (new_h - region_h) // 2
            return resized.crop((x, y, x + region_w, y + region_h)), 0, 0

        if style == WallpaperStyle.FIT:
            scale = min(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS, reducing_gap=3)
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
