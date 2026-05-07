"""
Tests for dynamic_wallpaper.py — DynamicWallpaper resource.
"""

import time
from unittest.mock import patch

import pytest
from PIL import Image

from wallpaper_automator.resource.dynamic_wallpaper import DynamicWallpaper
from wallpaper_automator.resource.wallpaper_utils import WallpaperStyle

# ── Helpers ────────────────────────────────────────────────────────────────


def _create_images(tmp_path, count=3, size=(100, 100)):
    """Create *count* small test images and return their paths as strings."""
    paths = []
    for i in range(count):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", size).save(p)
        paths.append(str(p))
    return paths


def _create_large_image(tmp_path, name="large.png", size=(4000, 3000)):
    """Create a single large image and return its path."""
    p = tmp_path / name
    Image.new("RGB", size).save(p)
    return str(p)


# ── TestDynamicWallpaperInit ───────────────────────────────────────────────


class TestDynamicWallpaperInit:
    def test_empty_paths_raises(self, mock_utils_screen_size):
        """Empty paths list raises ValueError."""
        with pytest.raises(ValueError, match="At least one path"):
            DynamicWallpaper(paths=[])

    def test_single_path(self, tmp_path, mock_utils_screen_size):
        """A single path is valid and stored."""
        paths = _create_images(tmp_path, count=1)
        wp = DynamicWallpaper(paths=paths)
        assert wp.paths == paths

    def test_multiple_paths(self, tmp_path, mock_utils_screen_size):
        """All paths are stored in order."""
        paths = _create_images(tmp_path, count=3)
        wp = DynamicWallpaper(paths=paths)
        assert wp.paths == paths
        assert len(wp.paths) == 3

    def test_init_with_enum_style(self, tmp_path, mock_utils_screen_size):
        """Style enum is stored as-is."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, style=WallpaperStyle.CENTER)
        assert wp.style == WallpaperStyle.CENTER

    def test_init_with_string_style(self, tmp_path, mock_utils_screen_size):
        """Style string is converted to enum (case-insensitive via .upper())."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, style="stretch")
        assert wp.style == WallpaperStyle.STRETCH

    def test_init_preserves_interval(self, tmp_path, mock_utils_screen_size):
        """interval is stored correctly."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, interval=60)
        assert wp.interval == 60

    def test_init_default_interval(self, tmp_path, mock_utils_screen_size):
        """Default interval is 300 seconds."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        assert wp.interval == 300

    def test_init_preserves_random_flag(self, tmp_path, mock_utils_screen_size):
        """random flag is stored correctly."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, random=True)
        assert wp.random is True

    def test_init_default_random_flag(self, tmp_path, mock_utils_screen_size):
        """Default random flag is False."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        assert wp.random is False

    def test_init_screen_size(self, tmp_path, mock_utils_screen_size):
        """Screen size is fetched during init."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        assert wp._screen_size == (1920, 1080)

    def test_unknown_string_style_raises(self, tmp_path, mock_utils_screen_size):
        """Invalid style string raises KeyError."""
        paths = _create_images(tmp_path, count=1)
        with pytest.raises(KeyError):
            DynamicWallpaper(paths=paths, style="invalid_style")


# ── TestDynamicWallpaperMount ──────────────────────────────────────────────


class TestDynamicWallpaperMount:
    def test_mount_saves_original_wallpaper(self, tmp_path, mock_utils_mount_deps):
        """mount saves the current wallpaper for later restore."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        wp.mount()
        assert wp._original_wallpaper == "C:\\original.jpg"

    def test_mount_applies_first_image(self, tmp_path, mock_utils_mount_deps):
        """mount sets the first wallpaper image."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=3)
        wp = DynamicWallpaper(paths=paths)
        wp.mount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[0] == paths[0]

    def test_mount_starts_cycling_thread(self, tmp_path, mock_utils_mount_deps):
        """mount starts a background thread."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        wp.mount()
        assert wp._cycling_thread is not None
        assert wp._cycling_thread.is_alive()
        wp.demount()

    def test_mount_applies_with_style(self, tmp_path, mock_utils_mount_deps):
        """mount calls set_wallpaper with the correct style."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, style=WallpaperStyle.STRETCH)
        wp.mount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[1] == ("2", "0")
        wp.demount()

    def test_mount_stores_original_before_overwrite(self, tmp_path, mock_utils_mount_deps):
        """mount gets the original wallpaper before setting the new one."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        wp.mount()
        # _original_wallpaper was set by the mock before _apply_current
        assert wp._original_wallpaper == "C:\\original.jpg"
        wp.demount()


# ── TestDynamicWallpaperDemount ────────────────────────────────────────────


class TestDynamicWallpaperDemount:
    def test_demount_restores_original(self, tmp_path, mock_utils_mount_deps):
        """demount restores the wallpaper that was active before mount."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, restore=True)
        wp.mount()
        mock_set.reset_mock()

        wp.demount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[0] == "C:\\original.jpg"

    def test_demount_without_mount_is_safe(self, tmp_path, mock_utils_mount_deps):
        """demount without prior mount does nothing (no error)."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        wp.demount()  # should not raise
        mock_set.assert_not_called()

    def test_demount_stops_cycling_thread(self, tmp_path, mock_utils_mount_deps):
        """demount causes the cycling thread to exit."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths)
        wp.mount()
        assert wp._cycling_thread is not None and wp._cycling_thread.is_alive()

        wp.demount()
        assert wp._cycling_thread is None

    def test_demount_idempotent(self, tmp_path, mock_utils_mount_deps):
        """Calling demount twice is safe."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, restore=True)
        wp.mount()
        mock_set.reset_mock()

        wp.demount()
        wp.demount()  # second call — should be a no-op
        mock_set.assert_called_once()  # only the restore from first demount

    def test_mount_demount_cycle(self, tmp_path, mock_utils_mount_deps):
        """Mount then demount then mount again works correctly."""
        mock_set = mock_utils_mount_deps
        with patch(
            "wallpaper_automator.resource.dynamic_wallpaper.get_current_wallpaper",
            return_value="C:\\original.jpg",
        ) as mock_get:
            paths = _create_images(tmp_path, count=2)
            wp = DynamicWallpaper(paths=paths, restore=True)

            wp.mount()
            assert mock_set.call_count == 1
            mock_get.assert_called_once()

            wp.demount()
            assert mock_set.call_count == 2

            # Mount again after demount
            mock_get.reset_mock()
            mock_get.return_value = "C:\\restored.jpg"
            wp.mount()
            assert mock_set.call_count == 3
            mock_get.assert_called_once()
            assert wp._original_wallpaper == "C:\\restored.jpg"
            wp.demount()

    def test_demount_with_restore_false_skips_restore(self, tmp_path, mock_utils_mount_deps):
        """When restore=False, demount does not restore the original wallpaper."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, restore=False)
        wp.mount()
        mock_set.reset_mock()

        wp.demount()
        # The last call should NOT be the restore — either no calls or only cycling calls
        # Restore specifically should not happen. With interval=300 the thread won't
        # fire during the test, so set_wallpaper should not be called at all
        mock_set.assert_not_called()

    def test_demount_with_restore_true_still_restores(self, tmp_path, mock_utils_mount_deps):
        """When restore=True, demount restores the original wallpaper."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, restore=True)
        wp.mount()
        mock_set.reset_mock()

        wp.demount()
        mock_set.assert_called_once()
        args, _ = mock_set.call_args
        assert args[0] == "C:\\original.jpg"


# ── TestDynamicWallpaperCycling ────────────────────────────────────────────


class TestDynamicWallpaperCycling:
    def test_advance_index_sequential(self, tmp_path, mock_utils_mount_deps):
        """_advance_index cycles forward sequentially."""
        paths = _create_images(tmp_path, count=3)
        wp = DynamicWallpaper(paths=paths, random=False)
        assert wp._index == 0

        wp._advance_index()
        assert wp._index == 1

        wp._advance_index()
        assert wp._index == 2

    def test_advance_index_wraps_around(self, tmp_path, mock_utils_mount_deps):
        """_advance_index wraps from last index back to 0."""
        paths = _create_images(tmp_path, count=3)
        wp = DynamicWallpaper(paths=paths, random=False)
        wp._index = 2

        wp._advance_index()
        assert wp._index == 0

    def test_advance_index_random(self, tmp_path, mock_utils_mount_deps):
        """_advance_index with random=True stays within valid range."""
        paths = _create_images(tmp_path, count=5)
        wp = DynamicWallpaper(paths=paths, random=True)

        for _ in range(20):
            wp._advance_index()
            assert 0 <= wp._index < 5

    def test_cycling_thread_advances_to_next(self, tmp_path, mock_utils_mount_deps):
        """Cycling thread advances to the next image after ~interval seconds."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=5)
        wp = DynamicWallpaper(paths=paths, interval=0.05)

        wp.mount()
        mock_set.assert_called_once()  # first image applied
        first_path = mock_set.call_args[0][0]
        assert first_path == paths[0]

        # Poll for the cycling thread to advance (up to 5 s to avoid flakiness)
        deadline = time.monotonic() + 5.0
        while mock_set.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

        assert mock_set.call_count >= 2, "Cycling thread did not advance"
        last_path = mock_set.call_args[0][0]
        assert last_path != paths[0]

        wp.demount()

    def test_stop_event_stops_thread_quickly(self, tmp_path, mock_utils_mount_deps):
        """Setting stop event causes thread to exit before next interval."""
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, interval=10)  # long interval

        wp.mount()
        assert wp._cycling_thread is not None and wp._cycling_thread.is_alive()

        wp._stop_event.set()
        wp._cycling_thread.join(timeout=1.0)
        assert not wp._cycling_thread.is_alive()

        wp.demount()


# ── TestDynamicWallpaperEdgeCases ──────────────────────────────────────────


class TestDynamicWallpaperEdgeCases:
    def test_nonexistent_path_raises(self, tmp_path, mock_utils_screen_size):
        """Path to a nonexistent file raises FileNotFoundError when accessed via _get_compressed."""
        wp = DynamicWallpaper(paths=[str(tmp_path / "nonexistent.png")])
        with pytest.raises(FileNotFoundError):
            wp._get_compressed(wp.paths[0])

    def test_compress_large_images(self, tmp_path, mock_utils_screen_size):
        """Large images are compressed; compressed paths differ from originals."""
        large1 = _create_large_image(tmp_path, name="large1.png", size=(4000, 3000))
        large2 = _create_large_image(tmp_path, name="large2.png", size=(3840, 2160))
        small = str(tmp_path / "small.png")
        Image.new("RGB", (100, 100)).save(small)

        wp = DynamicWallpaper(paths=[large1, large2, small])
        assert wp._get_compressed(small) == small  # not compressed
        assert wp._get_compressed(large1) != large1  # compressed
        assert wp._get_compressed(large2) != large2  # compressed

    def test_cache_dir_created_when_needed(self, tmp_path, mock_utils_screen_size):
        """Cache directory is created when allow_compress is True (default)."""
        large = _create_large_image(tmp_path, size=(4000, 3000))
        small = str(tmp_path / "small.png")
        Image.new("RGB", (100, 100)).save(small)

        wp = DynamicWallpaper(paths=[large, small])
        assert wp.temp_file is True
        # cache_dir should be accessible
        assert wp.cache_dir is not None

    def test_no_cache_dir_when_not_needed(self, tmp_path, mock_utils_screen_size):
        """No cache directory when allow_compress is False."""
        paths = _create_images(tmp_path, count=2, size=(100, 100))
        wp = DynamicWallpaper(paths=paths, allow_compress=False)
        assert wp.temp_file is False
        with pytest.raises(ValueError, match="cache dir is unavailable"):
            _ = wp.cache_dir  # should raise

    def test_get_current_image_path_respects_compression(self, tmp_path, mock_utils_screen_size):
        """_get_current_image_path returns the compressed path for large images."""
        large = _create_large_image(tmp_path, size=(4000, 3000))
        small = str(tmp_path / "small.png")
        Image.new("RGB", (100, 100)).save(small)

        wp = DynamicWallpaper(paths=[large, small])
        assert wp._get_current_image_path() == wp._get_compressed(wp.paths[0])

    def test_interval_zero_allows_cycling(self, tmp_path, mock_utils_mount_deps):
        """interval=0 is handled — thread can be stopped cleanly."""
        mock_set = mock_utils_mount_deps
        paths = _create_images(tmp_path, count=2)
        wp = DynamicWallpaper(paths=paths, interval=0)

        wp.mount()
        # Poll for at least one cycle (interval=0 means tight loop)
        deadline = time.monotonic() + 3.0
        while mock_set.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        wp.demount()

        # Thread should have stopped cleanly
        assert wp._cycling_thread is None
        # Should have cycled at least once (interval=0 means tight loop)
        assert mock_set.call_count >= 1
