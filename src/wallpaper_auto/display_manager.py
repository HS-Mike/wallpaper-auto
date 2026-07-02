"""
Resource manager.

Manages wallpaper resource lifecycle (mount/demount) at runtime.
Handles registration of built-in and custom resource types.
"""

import logging
import threading
from pathlib import Path

from PIL import Image

from .models import ResourceConfig
from .resource.base_resource import BaseResource
from .resource.resource_carousel import ResourceCarousel
from .resource.static_wallpaper import StaticWallpaper
from .config_store import ConfigStore
from .util.display_utils import get_display_info, DisplayInfo
from .util.wallpaper_util import (
    WallpaperStyle,
    com_session,
    get_wallpaper,
    get_wallpaper_style,
    set_wallpaper,
    set_wallpaper_style,
)

logger = logging.getLogger(__name__)


_BUILTIN_RESOURCES: dict[str, type[BaseResource]] = {
    "static_wallpaper": StaticWallpaper,
    "resource_carousel": ResourceCarousel,
}


class DisplayManager:
    """Resource lifecycle manager — mount/demount wallpapers and init from config."""

    _support_resources = _BUILTIN_RESOURCES.copy()

    def __init__(self) -> None:
        self._restore_wallpaper: dict[str, tuple[WallpaperStyle, Path]] = {}
        self._display_resoruce_map: dict[str, BaseResource | None]  = {}
        self._canvas_buffer: dict[str, tuple[WallpaperStyle, Path]] = {}
    
    def start(self):
        # record original wallpaper status
        curr_display_info: list[DisplayInfo] = get_display_info()
        for i in curr_display_info:
            self.add_new_display(i.monitor_device_path)
    
    def add_new_display(self, monitor_device_path: str):
        if monitor_device_path in self._display_resoruce_map:
            return
        style = get_wallpaper_style()
        image_path = get_wallpaper(monitor_device_path)
        self._restore_wallpaper[monitor_device_path] = (style, image_path)
        self._display_resoruce_map[monitor_device_path] = StaticWallpaper(monitor_device_path, style, image_path)
    
    def end(self):
        # restore original wallpaper status
        curr_display_info: list[DisplayInfo] = get_display_info()
        active_monitor_device_id = set(i.monitor_device_path for i in curr_display_info)
        for i, restore_info in self._restore_wallpaper.items():
            if i not in active_monitor_device_id:
                continue
            wallpaper_style, wallpaper_path = restore_info
            set_wallpaper_style(wallpaper_style)
            set_wallpaper(i, wallpaper_path)
    
    def update_resource(self, monitor_device_path: str, resource: BaseResource | None):
        curr_resource = self._display_resoruce_map.pop(monitor_device_path)
        if curr_resource is not None:
            curr_resource.demount()
        self._display_resoruce_map[monitor_device_path] = resource
        if resource is not None:
            def cb(style: WallpaperStyle, image_path: Path, immediate_update: bool = True):
                self.update_canvas(monitor_device_path, style, image_path)
            resource.mount(cb)
    
    def update_canvas(self, monitor_device_path: str, style: WallpaperStyle, image_path: Path, immediate_update: bool = True):
        self._canvas_buffer[monitor_device_path] = (style, image_path)

    def plot_canvas(self):
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
            _, span_path = span_entry
            span_img = Image.open(span_path)
            rendered, _, _ = DisplayManager._render_image_for_region(
                span_img, WallpaperStyle.FILL, canvas_w, canvas_h,
            )
            canvas.paste(rendered, (0, 0))
        else:
            for monitor_id, (style, img_path) in buffer.items():
                d = display_map.get(monitor_id)
                if d is None:
                    continue
                region_w, region_h = d.source_resolution
                canvas_x = d.position[0] - min_left
                canvas_y = d.position[1] - min_top

                img = Image.open(img_path)
                rendered, offset_x, offset_y = DisplayManager._render_image_for_region(img, style, region_w, region_h)
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
