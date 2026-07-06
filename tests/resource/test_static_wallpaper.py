"""Tests for static_wallpaper.py — StaticWallpaper mount/demount."""

from unittest.mock import MagicMock, call, patch
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
from wallpaper_auto.resource.static_wallpaper import WallpaperStyle as SWWallpaperStyle
from wallpaper_auto.resource.wallpaper_utils import (
    WallpaperStyle,
    get_current_wallpaper,
    get_screen_size,
    set_wallpaper,
)

_DEVICE_PATH = r"\\?\DISPLAY#TEST#{test-device}"


class TestWallpaperStyle:
    def test_fill_value(self):
        assert WallpaperStyle.FILL.value == ("10", "0")

    def test_fit_value(self):
        assert WallpaperStyle.FIT.value == ("6", "0")

    def test_stretch_value(self):
        assert WallpaperStyle.STRETCH.value == ("2", "0")

    def test_center_value(self):
        assert WallpaperStyle.CENTER.value == ("0", "0")

    def test_tile_value(self):
        assert WallpaperStyle.TILE.value == ("0", "1")

    def test_from_string_upper(self):
        assert WallpaperStyle["FILL"] is WallpaperStyle.FILL
        assert WallpaperStyle["TILE"] is WallpaperStyle.TILE

    def test_from_string_lower_raises(self):
        with pytest.raises(KeyError):
            _ = WallpaperStyle["fill"]


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

    def test_mount_with_string_style(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style="center")
        wp._bind_monitor_device_path(_DEVICE_PATH)
        wp._bind_plot_canvas(MagicMock())

        wp.mount()  # should not raise

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


class TestGetScreenSize:
    def test_returns_width_height(self):
        with patch("wallpaper_auto.resource.wallpaper_utils.ctypes.windll") as mock_windll:
            mock_windll.gdi32.GetDeviceCaps.side_effect = [1920, 1080]
            w, h = get_screen_size()
            assert w == 1920
            assert h == 1080
            mock_windll.user32.GetDC.assert_called_once_with(0)
            assert mock_windll.gdi32.GetDeviceCaps.call_args_list == [
                call(mock_windll.user32.GetDC.return_value, 118),
                call(mock_windll.user32.GetDC.return_value, 117),
            ]
            mock_windll.user32.ReleaseDC.assert_called_once_with(
                0, mock_windll.user32.GetDC.return_value
            )

    def test_releases_dc_on_success(self):
        with patch("wallpaper_auto.resource.wallpaper_utils.ctypes.windll") as mock_windll:
            mock_windll.gdi32.GetDeviceCaps.side_effect = [1920, 1080]
            get_screen_size()
            mock_windll.user32.ReleaseDC.assert_called_once()


class TestGetCurrentWallpaper:
    def test_returns_wallpaper_path(self):
        with (
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegQueryValueEx",
                return_value=("C:\\wallpaper.jpg", 1),
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey"),
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
        ):
            result = get_current_wallpaper()
            assert result == "C:\\wallpaper.jpg"

    def test_returns_wallpaper_path_closes_key(self):
        with (
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegQueryValueEx",
                return_value=("C:\\wallpaper.jpg", 1),
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey") as mock_close,
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
        ):
            result = get_current_wallpaper()
            assert result == "C:\\wallpaper.jpg"
            mock_close.assert_called_once_with(999)

    def test_closes_key_after_query(self):
        with (
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey") as mock_close,
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
        ):
            with patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegQueryValueEx",
                return_value=("C:\\wallpaper.jpg", 1),
            ):
                get_current_wallpaper()
            mock_close.assert_called_once_with(999)


class TestSetWallpaper:
    def test_sets_wallpaper_and_style(self, tmp_path):
        with (
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32gui.SystemParametersInfo"
            ) as mock_spi,
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegSetValueEx") as mock_set,
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey") as mock_close,
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.os.path.exists", return_value=True),
        ):
            img_path = tmp_path / "test.png"
            img_path.write_bytes(b"fake")

            set_wallpaper(str(img_path), WallpaperStyle.STRETCH.value)

            assert mock_set.call_args_list == [
                call(999, "WallpaperStyle", 0, 1, "2"),
                call(999, "TileWallpaper", 0, 1, "0"),
            ]
            mock_close.assert_called_once_with(999)
            mock_spi.assert_called_once()

    def test_converts_path_to_abs_backslashes(self):
        with (
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32gui.SystemParametersInfo"
            ) as mock_spi,
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegSetValueEx"),
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey"),
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.os.path.exists", return_value=True),
        ):
            set_wallpaper("some/relative/path.jpg", WallpaperStyle.FILL.value)

            call_arg = mock_spi.call_args[0][1]
            assert "\\" in call_arg
            assert "/" not in call_arg

    def test_raises_when_file_not_found(self):
        with patch("wallpaper_auto.resource.wallpaper_utils.os.path.exists", return_value=False):
            with pytest.raises(FileNotFoundError, match="not exist"):
                set_wallpaper("nonexistent.jpg", WallpaperStyle.FILL.value)

    def test_sets_tile_wallpaper_style(self, tmp_path):
        with (
            patch("wallpaper_auto.resource.wallpaper_utils.win32gui.SystemParametersInfo"),
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegSetValueEx") as mock_set,
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey"),
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.os.path.exists", return_value=True),
        ):
            img_path = tmp_path / "test.png"
            img_path.write_bytes(b"fake")

            set_wallpaper(str(img_path), WallpaperStyle.TILE.value)

            assert mock_set.call_args_list == [
                call(999, "WallpaperStyle", 0, 1, "0"),
                call(999, "TileWallpaper", 0, 1, "1"),
            ]


class TestStaticWallpaperEdgeCases:
    def test_unknown_string_style_raises_keyerror(self, tmp_path):
        """Passing an invalid style string should raise KeyError"""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        with pytest.raises(KeyError):
            StaticWallpaper(path=str(img_path), style="invalid_style")


class TestCompressImage:
    """Tests for ``compress_image`` error paths."""

    def test_raises_when_cache_already_exists(self, tmp_path):
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        save_path = tmp_path / "cache.png"
        save_path.write_text("")

        with pytest.raises(FileExistsError, match="Cache already exists"):
            compress_image(str(img_path), (1920, 1080), str(save_path))

    def test_raises_on_zero_dimension_image(self, tmp_path):
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)

        with patch("PIL.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img.width = 0
            mock_img.height = 100
            mock_open.return_value.__enter__.return_value = mock_img

            with pytest.raises(ValueError, match="Invalid image"):
                compress_image(str(img_path), (1920, 1080), str(tmp_path / "out.png"))

    def test_compress_png_keeps_png_format(self, tmp_path):
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.png"
        Image.new("RGB", (200, 100), (10, 20, 30)).save(img_path)
        save_path = tmp_path / "out.png"
        compress_image(str(img_path), (100, 100), str(save_path))
        assert save_path.exists()
        # Output format inferred from input — should be PNG.
        with Image.open(save_path) as result:
            assert result.format == "PNG"

    def test_compress_jpeg_keeps_jpeg_format(self, tmp_path):
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (200, 100), (10, 20, 30)).save(img_path, format="JPEG")
        save_path = tmp_path / "out.jpg"
        compress_image(str(img_path), (100, 100), str(save_path))
        assert save_path.exists()

    def test_compress_unknown_format_falls_back_to_png(self, tmp_path):
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.tiff"
        # Save as TIFF (not in the allowed list).
        Image.new("RGB", (50, 50)).save(img_path, format="TIFF")
        save_path = tmp_path / "out.png"
        compress_image(str(img_path), (50, 50), str(save_path))
        # Output is PNG (fallback).
        with Image.open(save_path) as result:
            assert result.format == "PNG"


class TestGetCurrentWallpaperStyle:
    def test_returns_style_tuple(self):
        from wallpaper_auto.resource.wallpaper_utils import get_current_wallpaper_style

        with (
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegQueryValueEx",
                side_effect=[("10", 1), ("0", 1)],
            ),
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey"),
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
        ):
            result = get_current_wallpaper_style()

        assert result == ("10", "0")

    def test_closes_key_after_query(self):
        from wallpaper_auto.resource.wallpaper_utils import get_current_wallpaper_style

        with (
            patch("wallpaper_auto.resource.wallpaper_utils.win32api.RegCloseKey") as mock_close,
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegOpenKeyEx",
                return_value=999,
            ),
            patch(
                "wallpaper_auto.resource.wallpaper_utils.win32api.RegQueryValueEx",
                side_effect=[("6", 1), ("0", 1)],
            ),
        ):
            get_current_wallpaper_style()
        mock_close.assert_called_once_with(999)
