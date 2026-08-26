"""Tests for static_wallpaper.py — StaticWallpaper mount/demount."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
from wallpaper_auto.resource.static_wallpaper import WallpaperStyle as SWWallpaperStyle
from wallpaper_auto.util.display_util import LUID, DisplayInfo

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


def _make_display() -> DisplayInfo:
    """DisplayInfo with a stable identity for resource binding."""
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="U2719D",
        source_resolution=(1920, 1080),
        position=(0, 0),
        target_resolution=(1920, 1080),
        adapter_id=LUID(1, 2),
        source_id=3,
        scale=100,
        monitor_device_path=_DEVICE_PATH,
    )


class TestStaticWallpaperInit:
    def test_init_with_enum_style(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.CENTER)
        assert wp.image_path == Path(img_path)
        assert wp.style == SWWallpaperStyle.CENTER

    def test_init_with_string_style(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style="stretch")
        assert wp.style == SWWallpaperStyle.STRETCH


class TestStaticWallpaperMount:
    def test_mount_buffers_canvas(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.FILL)
        display = _make_display()
        wp._bind_display(display)
        mock_plot = MagicMock()
        wp._update_canvas = mock_plot

        wp.mount()

        mock_plot.assert_called_once_with(display.display_id, SWWallpaperStyle.FILL, Path(img_path))

    def test_mount_raises_when_canvas_unbound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(StaticWallpaper, "_update_canvas", None)
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.FILL)
        wp._bind_display(_make_display())

        with pytest.raises(RuntimeError, match="update_canvas not bound"):
            wp.mount()


class TestStaticWallpaperDemount:
    def test_demount_is_safe(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.FILL)
        wp.demount()  # should not raise


class TestStaticWallpaperEdgeCases:
    def test_unknown_string_style_raises_keyerror(self, tmp_path):
        """Passing an invalid style string should raise KeyError"""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        with pytest.raises(KeyError):
            StaticWallpaper(path=str(img_path), style="invalid_style")
