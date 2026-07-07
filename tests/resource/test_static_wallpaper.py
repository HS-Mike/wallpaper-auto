"""Tests for static_wallpaper.py — StaticWallpaper mount/demount."""

from unittest.mock import MagicMock
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.static_wallpaper import StaticWallpaper, WallpaperStyle as SWWallpaperStyle

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


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
    def test_mount_calls_plot_canvas(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.FILL)
        wp._bind_monitor_device_path(_DEVICE_PATH)
        mock_plot = MagicMock()
        wp._bind_plot_canvas(mock_plot)

        wp.mount()

        mock_plot.assert_called_once_with(
            _DEVICE_PATH, SWWallpaperStyle.FILL, Path(img_path), False
        )

    def test_mount_raises_when_plot_canvas_not_bound(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=SWWallpaperStyle.FILL)
        wp._bind_monitor_device_path(_DEVICE_PATH)

        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
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
