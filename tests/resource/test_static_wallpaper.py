"""Tests for static_wallpaper.py — StaticWallpaper mount/demount and caching."""

from unittest.mock import MagicMock, call, patch
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
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
        wp = StaticWallpaper(path=str(img_path), style=WallpaperStyle.CENTER)
        assert wp.image_path == Path(img_path)
        assert wp.style == WallpaperStyle.CENTER
        assert wp.allow_compress

    def test_init_with_string_style(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style="stretch")
        assert wp.style == WallpaperStyle.STRETCH

    def test_init_with_allow_compress_false(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), allow_compress=False)
        assert not wp.allow_compress

    def test_init_uses_screen_size(self, tmp_path, mock_screen_size):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        assert wp._screen_size == (1920, 1080)


class TestCheckNeedCache:
    def test_need_cache_when_image_larger(self, tmp_path, mock_screen_size):
        """Image is significantly larger than screen -> needs cache"""
        img_path = tmp_path / "large.png"
        Image.new("RGB", (3840, 2160)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        assert wp._check_need_cache()

    def test_no_cache_when_image_smaller(self, tmp_path, mock_screen_size):
        """Image is smaller than 1.2x screen size -> no cache"""
        img_path = tmp_path / "small.png"
        Image.new("RGB", (1920, 1080)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        assert not wp._check_need_cache()

    def test_no_cache_when_image_slightly_larger_within_threshold(self, tmp_path, mock_screen_size):
        """Image is within 1.2x threshold -> no cache"""
        img_path = tmp_path / "close.png"
        close_width = int(1920 * 1.19)
        close_height = int(1080 * 1.19)
        Image.new("RGB", (close_width, close_height)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        assert not wp._check_need_cache()

    def test_no_compress_flag_skips_cache(self, tmp_path, mock_screen_size):
        """allow_compress=False means no cache regardless of image size"""
        img_path = tmp_path / "large.png"
        Image.new("RGB", (3840, 2160)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), allow_compress=False)
        assert not wp._check_need_cache()


class TestStaticWallpaperPrepareCache:
    def test_cached_path_extension_matches_source(self, tmp_path, mock_screen_size, config_store_with_cache):
        """prepare_wallpaper sets mount_path inside the cache dir for large images."""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (4000, 3000)).save(img_path)

        wp = StaticWallpaper(path=str(img_path))
        wp.prepare_wallpaper()
        assert wp.mount_path is not None
        assert str(wp.mount_path).endswith(".png")
        assert str(wp.cache_dir) in str(wp.mount_path)

    def test_resize_applied_correctly(self, tmp_path, mock_screen_size, config_store_with_cache):
        """Verify the cached image dimensions match expected scaling"""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (4000, 3000)).save(img_path)

        wp = StaticWallpaper(path=str(img_path))
        wp.prepare_wallpaper()

        with Image.open(wp.mount_path) as result:
            # scale = max(1920/4000, 1080/3000) = max(0.48, 0.36) = 0.48
            # new_w = int(4000 * 0.48) = 1920
            # new_h = int(3000 * 0.48) = 1440
            assert result.width == 1920
            assert result.height == 1440

    def test_no_cache_small_image(self, tmp_path, mock_screen_size):
        """Small image does not trigger caching; mount_path is the original path."""
        img_path = tmp_path / "small.png"
        Image.new("RGB", (100, 100)).save(img_path)

        wp = StaticWallpaper(path=str(img_path))
        wp.prepare_wallpaper()
        assert wp.mount_path == wp.image_path

    def test_zero_dimension_image_raises(self, tmp_path, mock_screen_size, config_store_with_cache):
        """compress_image raises ValueError when given a zero-dimension image"""
        img_path = tmp_path / "large.png"
        Image.new("RGB", (4000, 3000)).save(img_path)

        wp = StaticWallpaper(path=str(img_path))
        # prepare_wallpaper will call compress_image on the real image (valid),
        # so we patch compress_image itself to raise.
        with patch(
            "wallpaper_auto.resource.static_wallpaper.compress_image",
            side_effect=ValueError("Invalid image: test"),
        ):
            with pytest.raises(ValueError, match="Invalid image"):
                wp.prepare_wallpaper()


class TestStaticWallpaperMount:
    def test_mount_saves_original_wallpaper(self, tmp_path, mock_mount_deps):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        wp.mount(_DEVICE_PATH)
        assert wp._original_wallpaper == Path("C:\\original.jpg")
        assert wp._monitor_device_path == _DEVICE_PATH

    def test_mount_sets_wallpaper_for_device(self, tmp_path, mock_mount_deps):
        """mount calls set_per_display_wallpaper with the device path."""
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), style=WallpaperStyle.STRETCH)
        wp.mount(_DEVICE_PATH)
        mock_set.assert_called_once_with(_DEVICE_PATH, str(img_path))

    def test_mount_with_allow_compress_false_sends_original_path(self, tmp_path, mock_mount_deps):
        """mount with allow_compress=False sends original path regardless of image size"""
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (3840, 2160)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), allow_compress=False)
        wp.mount(_DEVICE_PATH)
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[1] == str(img_path)

    def test_mount_stores_original_before_overwrite(self, tmp_path, mock_mount_deps):
        """mount calls get_wallpaper to save original before overwriting"""
        with patch(
            "wallpaper_auto.resource.static_wallpaper.get_wallpaper",
            return_value="C:\\original.jpg",
        ) as mock_get:
            img_path = tmp_path / "test.png"
            Image.new("RGB", (100, 100)).save(img_path)
            wp = StaticWallpaper(path=str(img_path))
            wp.mount(_DEVICE_PATH)
            mock_get.assert_called_once_with(_DEVICE_PATH)


class TestStaticWallpaperDemount:
    def test_demount_restores_original(self, tmp_path, mock_mount_deps):
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), restore=True)
        wp.mount(_DEVICE_PATH)
        mock_set.reset_mock()

        wp.demount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[0] == _DEVICE_PATH
        assert args[1] == "C:\\original.jpg"

    def test_demount_without_mount_is_safe(self, tmp_path, mock_mount_deps):
        """demount without prior mount does nothing (no error)"""
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        wp.demount()  # should not raise
        mock_set.assert_not_called()

    def test_mount_demount_cycle(self, tmp_path, mock_mount_deps):
        """mount then demount correctly restores original"""
        mock_set = mock_mount_deps
        with patch(
            "wallpaper_auto.resource.static_wallpaper.get_wallpaper",
            return_value="C:\\original.jpg",
        ) as mock_get:
            img_path = tmp_path / "test.png"
            Image.new("RGB", (100, 100)).save(img_path)
            wp = StaticWallpaper(path=str(img_path), restore=True)

            wp.mount(_DEVICE_PATH)
            assert mock_set.call_count == 1
            mock_get.assert_called_once_with(_DEVICE_PATH)

            wp.demount()
            assert mock_set.call_count == 2

            # mount again after demount
            mock_get.reset_mock()
            mock_get.return_value = "C:\\restored.jpg"
            wp.mount(_DEVICE_PATH)
            assert mock_set.call_count == 3
            mock_get.assert_called_once_with(_DEVICE_PATH)
            assert wp._original_wallpaper == Path("C:\\restored.jpg")

    def test_demount_with_restore_false_skips_restore(self, tmp_path, mock_mount_deps):
        """When restore=False, demount does not restore the original wallpaper."""
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), restore=False)
        wp.mount(_DEVICE_PATH)
        mock_set.reset_mock()

        wp.demount()
        mock_set.assert_not_called()

    def test_demount_with_restore_true_still_restores(self, tmp_path, mock_mount_deps):
        """When restore=True, demount restores the original wallpaper."""
        mock_set = mock_mount_deps
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path), restore=True)
        wp.mount(_DEVICE_PATH)
        mock_set.reset_mock()

        wp.demount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[1] == "C:\\original.jpg"


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
        """DC handle is always released after getting screen size"""
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
    def test_unknown_string_style_raises_keyerror(self, tmp_path, mock_screen_size):
        """Passing an invalid style string should raise KeyError"""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        with pytest.raises(KeyError):
            StaticWallpaper(path=str(img_path), style="invalid_style")

    def test_cache_dir_available_on_init(self, tmp_path):
        """cache_dir is set eagerly from ConfigStore in __init__."""
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))
        assert wp.cache_dir is not None

    def test_resize_portrait_image(self, tmp_path, mock_screen_size, config_store_with_cache):
        """A portrait image should be scaled to fill the screen correctly"""
        img_path = tmp_path / "portrait.png"
        # Portrait image large enough to exceed 1.2x threshold (1080 * 1.2 = 1296)
        Image.new("RGB", (500, 2000)).save(img_path)

        wp = StaticWallpaper(path=str(img_path))
        wp.prepare_wallpaper()
        assert wp.mount_path is not None
        with Image.open(wp.mount_path) as result:
            assert result.width == 1920
            assert result.height == 7680

    def test_nonexistent_path_fails_on_prepare(self, tmp_path, mock_screen_size, config_store_with_cache):
        """A nonexistent file raises FileNotFoundError when prepare_wallpaper is called."""
        nonexistent = str(tmp_path / "nonexistent.png")
        wp = StaticWallpaper(path=nonexistent)
        with pytest.raises(FileNotFoundError):
            wp.prepare_wallpaper()

    def test_nonexistent_cache_dir_raises(self, tmp_path, mock_screen_size):
        """prepare_wallpaper raises FileNotFoundError when cache dir does not exist."""
        from wallpaper_auto.config_store import ConfigStore
        from wallpaper_auto.models import ConfigModel

        ConfigStore.clear_instance()
        cache_dir = tmp_path / "my_cache"
        cache_dir.mkdir()
        store = ConfigStore()
        store.config = ConfigModel(
            resource={"a": {"name": "static_wallpaper", "config": {"path": "dummy"}}},
            trigger=[],
            rule=[],
            fallback="a",
            cache=str(cache_dir),
        )

        img_path = tmp_path / "test.png"
        Image.new("RGB", (4000, 3000)).save(img_path)
        wp = StaticWallpaper(path=str(img_path))

        # Delete the cache dir so prepare_wallpaper finds it missing
        cache_dir.rmdir()

        with pytest.raises(FileNotFoundError, match="cache directory"):
            wp.prepare_wallpaper()


class TestCompressImage:
    """Tests for ``compress_image`` error paths."""

    def test_raises_when_cache_already_exists(self, tmp_path):
        """compress_image raises FileExistsError when save_path already exists."""
        from wallpaper_auto.resource.wallpaper_utils import compress_image

        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100)).save(img_path)
        save_path = tmp_path / "cache.png"
        save_path.write_text("")

        with pytest.raises(FileExistsError, match="Cache already exists"):
            compress_image(str(img_path), (1920, 1080), str(save_path))

    def test_raises_on_zero_dimension_image(self, tmp_path):
        """compress_image raises ValueError when the image has zero dimensions."""
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
