"""Tests for display_manager.py — DisplayManager per-monitor wallpaper compositor."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.display_manager import DisplayManager
from wallpaper_auto.models import ConfigModel
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
from wallpaper_auto.util.display_utils import DisplayInfo
from wallpaper_auto.util.wallpaper_util import WallpaperStyle

_DEVICE_A = r"\\?\DISPLAY#TEST#{device-a}"
_DEVICE_B = r"\\?\DISPLAY#TEST#{device-b}"


@pytest.fixture(autouse=True)
def _config_store_with_cache(tmp_path: Path):
    """Provide a ConfigStore singleton pointing its cache at tmp_path."""
    ConfigStore.clear_instance()
    store = ConfigStore()
    store.config = ConfigModel(
        resource={"a": {"name": "static_wallpaper", "config": {"path": "dummy"}}},
        trigger=[],
        rule=[],
        fallback_target="a",
        cache=str(tmp_path),
    )
    yield tmp_path


def _make_display(device_path: str, x: int, y: int, w: int, h: int) -> DisplayInfo:
    return DisplayInfo(
        model="test",
        source_resolution=(w, h),
        position=(x, y),
        target_resolution=(w, h),
        scale=1.0,
        monitor_device_path=device_path,
    )


class TestDisplayManagerInit:
    """Constructor and initial state."""

    def test_init_creates_empty_state(self):
        dm = DisplayManager()
        assert dm._restore_wallpaper == {}
        assert dm._display_resource_map == {}
        assert dm._canvas_buffer == {}

class TestDisplayManagerStartStop:
    """start() / stop() iterate the live display topology."""

    def test_start_adds_each_display(self):
        dm = DisplayManager()
        displays = [
            _make_display(_DEVICE_A, 0, 0, 1920, 1080),
            _make_display(_DEVICE_B, 1920, 0, 1920, 1080),
        ]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.start()

        assert set(dm._display_resource_map.keys()) == {_DEVICE_A, _DEVICE_B}

    def test_stop_removes_each_display(self):
        dm = DisplayManager()
        displays = [_make_display(_DEVICE_A, 0, 0, 1920, 1080)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.start()
            assert dm._display_resource_map

        # Convert to non-patch so remove_display can process it.
        custom = MagicMock(spec=BaseResource)
        custom._bind_plot_canvas = MagicMock()
        custom.mount = MagicMock()
        dm.update_resource(_DEVICE_A, custom)

        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = MagicMock(spec=StaticWallpaper)
            dm.stop()

        # remove_display replaces the entry with a StaticWallpaper patch.
        assert dm._display_resource_is_patch[_DEVICE_A] is True
        assert isinstance(dm._display_resource_map[_DEVICE_A], MagicMock)
        assert dm._restore_wallpaper[_DEVICE_A] == (WallpaperStyle.FILL, Path("C:/orig.jpg"))


class TestDisplayManagerActiveMonitorDevicePath:
    """active_monitor_device_path property."""

    def test_empty_initially(self):
        assert DisplayManager().active_monitor_device_path == set()

    def test_returns_set_of_bound_paths(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.add_display(_DEVICE_A)
            dm.add_display(_DEVICE_B)

        assert dm.active_monitor_device_path == {_DEVICE_A, _DEVICE_B}


class TestDisplayManagerAddDisplay:
    """add_display registers a new monitor and captures its current wallpaper."""

    def test_add_creates_restore_and_active_resource(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.STRETCH,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.add_display(_DEVICE_A)

        assert dm._restore_wallpaper[_DEVICE_A] == (WallpaperStyle.STRETCH, Path("C:/orig.jpg"))
        assert isinstance(dm._display_resource_map[_DEVICE_A], StaticWallpaper)
        # The active resource is a separate StaticWallpaper from the raw restore tuple.
        assert dm._restore_wallpaper[_DEVICE_A] is not dm._display_resource_map[_DEVICE_A]
        # The active resource is bound to the display.
        assert dm._display_resource_map[_DEVICE_A].monitor_device_path == _DEVICE_A
        assert dm._display_resource_map[_DEVICE_A]._plot_canvas is not None

    def test_add_is_idempotent(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ) as mock_style,
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ) as mock_wp,
        ):
            dm.add_display(_DEVICE_A)
            first = dm._display_resource_map[_DEVICE_A]
            dm.add_display(_DEVICE_A)
            second = dm._display_resource_map[_DEVICE_A]

        assert first is second
        # get_wallpaper / get_wallpaper_style should only have been called once.
        assert mock_wp.call_count == 1
        assert mock_style.call_count == 1


class TestDisplayManagerRemoveDisplay:
    """remove_display demounts current resource and re-mounts the captured one."""

    def test_remove_demounts_active_and_remounts_restore(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.add_display(_DEVICE_A)
        # Set up a custom (non-patch) resource first.
        custom = MagicMock(spec=BaseResource)
        custom._bind_plot_canvas = MagicMock()
        custom.mount = MagicMock()
        custom.demount = MagicMock()
        dm.update_resource(_DEVICE_A, custom)

        with (
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = MagicMock(spec=StaticWallpaper)
            dm.remove_display(_DEVICE_A)

        custom.demount.assert_called_once()
        # remove_display replaced the entry with a new StaticWallpaper patch.
        mock_static.assert_called_once()
        assert dm._display_resource_is_patch[_DEVICE_A] is True
        assert isinstance(dm._display_resource_map[_DEVICE_A], MagicMock)


class TestDisplayManagerUpdateResource:
    """update_resource swaps the active resource bound to a monitor."""

    def test_update_swaps_active_resource(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            dm.add_display(_DEVICE_A)
        prev = dm._display_resource_map[_DEVICE_A]
        with patch.object(prev, "demount") as mock_demount:
            new_resource = MagicMock(spec=BaseResource)
            new_resource._bind_plot_canvas = MagicMock()
            new_resource.mount = MagicMock()

            dm.update_resource(_DEVICE_A, new_resource)

            mock_demount.assert_called_once()
        assert prev._plot_canvas is None
        assert dm._display_resource_map[_DEVICE_A] is new_resource
        new_resource._bind_plot_canvas.assert_called_once()
        new_resource.mount.assert_called_once()


class TestDisplayManagerUpdateCanvasBuffer:
    """update_canvas_buffer stages per-monitor wallpaper state."""

    def test_stores_entry(self):
        dm = DisplayManager()
        img = Image.new("RGB", (10, 10))
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FILL, img, immediate_update=False)

        assert dm._canvas_buffer[_DEVICE_A] == (WallpaperStyle.FILL, img)
        mock_plot.assert_not_called()

    def test_immediate_update_triggers_plot_canvas(self):
        dm = DisplayManager()
        img = Image.new("RGB", (10, 10))
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FILL, img, immediate_update=True)

        mock_plot.assert_called_once()


class TestDisplayManagerPlotCanvas:
    """plot_canvas composites the buffer and applies it as a spanned wallpaper."""

    def test_empty_buffer_is_noop(self, _config_store_with_cache: Path):
        dm = DisplayManager()
        with (
            patch("wallpaper_auto.display_manager.get_display_info") as mock_info,
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            dm.plot_canvas()

        mock_info.assert_not_called()
        mock_session.assert_not_called()
        assert not (_config_store_with_cache / "_composite.png").exists()

    def test_no_matching_displays_returns_early(self, _config_store_with_cache: Path):
        dm = DisplayManager()
        img = Image.new("RGB", (10, 10))
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, img)

        # get_display_info returns displays that don't match the buffered path.
        unrelated = _make_display(_DEVICE_B, 0, 0, 100, 100)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[unrelated],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            dm.plot_canvas()

        mock_session.assert_not_called()
        assert not (_config_store_with_cache / "_composite.png").exists()

    def test_multi_monitor_composite_writes_and_applies(self, _config_store_with_cache: Path):
        dm = DisplayManager()
        img_a = Image.new("RGB", (50, 50), (255, 0, 0))
        img_b = Image.new("RGB", (50, 50), (0, 255, 0))
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, img_a)
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, img_b)

        displays = [
            _make_display(_DEVICE_A, 0, 0, 50, 50),
            _make_display(_DEVICE_B, 50, 0, 50, 50),
        ]

        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=displays,
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
        ):
            mock_session.return_value.__enter__ = MagicMock()
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            dm.plot_canvas()

        composite = _config_store_with_cache / "_composite.png"
        assert composite.exists()
        # Canvas is the union bounding box of the two displays: 100x50.
        rendered = Image.open(composite)
        assert rendered.size == (100, 50)
        # Both setters invoked inside the com_session context.
        mock_set_wp.assert_called_once()
        mock_set_style.assert_called_once()
        assert mock_set_style.call_args.args[0] is WallpaperStyle.SPAN

    def test_span_entry_replaces_entire_composite(self, _config_store_with_cache: Path):
        dm = DisplayManager()
        full_img = Image.new("RGB", (40, 40), (10, 20, 30))
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.SPAN, full_img)

        displays = [_make_display(_DEVICE_A, 0, 0, 40, 40)]

        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=displays,
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            mock_session.return_value.__enter__ = MagicMock()
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            dm.plot_canvas()

        composite = _config_store_with_cache / "_composite.png"
        assert composite.exists()
        rendered = Image.open(composite)
        assert rendered.size == (40, 40)

    def test_span_entry_with_path_loads_from_disk(self, _config_store_with_cache: Path):
        """SPAN style with a buffered Path is loaded via Image.open."""
        dm = DisplayManager()
        full_path = _config_store_with_cache / "span.png"
        Image.new("RGB", (40, 40), (10, 20, 30)).save(full_path)
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.SPAN, full_path)

        displays = [_make_display(_DEVICE_A, 0, 0, 40, 40)]

        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=displays,
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            mock_session.return_value.__enter__ = MagicMock()
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            dm.plot_canvas()

        assert (_config_store_with_cache / "_composite.png").exists()

    def test_buffer_entry_with_unknown_display_is_skipped(self, _config_store_with_cache: Path):
        """Buffer entries whose monitor_id is not in get_display_info are skipped."""
        dm = DisplayManager()
        img_known = Image.new("RGB", (30, 30))
        img_unknown = Image.new("RGB", (30, 30))
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, img_known)
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, img_unknown)

        # Only _DEVICE_A is in the live topology.
        displays = [_make_display(_DEVICE_A, 0, 0, 30, 30)]

        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=displays,
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            mock_session.return_value.__enter__ = MagicMock()
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            dm.plot_canvas()

        # Should still produce a composite from the single known display.
        assert (_config_store_with_cache / "_composite.png").exists()

    def test_path_buffers_are_loaded_from_disk(self, _config_store_with_cache: Path):
        """Buffered Path entries (vs PIL.Image) are loaded via Image.open."""
        dm = DisplayManager()
        path_a = _config_store_with_cache / "a.png"
        path_b = _config_store_with_cache / "b.png"
        Image.new("RGB", (40, 40), (200, 0, 0)).save(path_a)
        Image.new("RGB", (40, 40), (0, 200, 0)).save(path_b)
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, path_a)
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, path_b)

        displays = [
            _make_display(_DEVICE_A, 0, 0, 40, 40),
            _make_display(_DEVICE_B, 40, 0, 40, 40),
        ]
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=displays,
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            mock_session.return_value.__enter__ = MagicMock()
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            dm.plot_canvas()

        assert (_config_store_with_cache / "_composite.png").exists()


class TestDisplayManagerRenderImageForRegion:
    """Static helper covering all six style branches and image-mode conversions."""

    def test_rgb_image_no_conversion_for_stretch(self):
        img = Image.new("RGB", (100, 50), (10, 20, 30))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.STRETCH, 200, 100
        )
        assert rendered.size == (200, 100)
        assert (ox, oy) == (0, 0)

    def test_rgba_image_stripped_to_black_background(self):
        # RGBA with semi-transparent pixel: alpha-strip should expose the black bg.
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
        rendered, _, _ = DisplayManager._render_image_for_region(
            img, WallpaperStyle.STRETCH, 10, 10
        )
        assert rendered.mode == "RGB"
        # A semi-transparent red over black produces a darker red — distinct from opaque.
        opaque = Image.new("RGB", (10, 10), (255, 0, 0))
        assert rendered.tobytes() != opaque.tobytes()

    def test_non_rgb_mode_converted_to_rgb(self):
        img = Image.new("L", (10, 10), 128)
        rendered, _, _ = DisplayManager._render_image_for_region(
            img, WallpaperStyle.STRETCH, 10, 10
        )
        assert rendered.mode == "RGB"

    def test_fill_crops_center(self):
        # Source 200x100 into 100x100 region → scale = 100/200 = 0.5 → 100x50,
        # then crop centered to 100x100? That would under-fill. Re-check logic:
        # FILL: scale = max(region_w/src_w, region_h/src_h) = max(100/200, 100/100) = 1
        # → resized to (200, 100), crop center 100x100.
        img = Image.new("RGB", (200, 100), (255, 0, 0))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.FILL, 100, 100
        )
        assert rendered.size == (100, 100)
        assert (ox, oy) == (0, 0)

    def test_fit_returns_positive_offset(self):
        # Source 200x100 into 100x200 → scale = min(100/200, 200/100) = 0.5
        # → resized to (100, 50), centered in 100x200 → offset (0, 75).
        img = Image.new("RGB", (200, 100), (0, 255, 0))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.FIT, 100, 200
        )
        assert rendered.size == (100, 50)
        assert ox == 0
        assert oy == 75

    def test_center_returns_source_with_offset(self):
        img = Image.new("RGB", (40, 30), (0, 0, 255))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.CENTER, 100, 100
        )
        assert rendered.size == (40, 30)
        assert ox == 30
        assert oy == 35

    def test_tile_fills_region(self):
        img = Image.new("RGB", (10, 10), (255, 255, 0))
        rendered, ox, oy = DisplayManager._render_image_for_region(img, WallpaperStyle.TILE, 35, 25)
        assert rendered.size == (35, 25)
        assert (ox, oy) == (0, 0)

    def test_span_falls_back_to_fill(self):
        # SPAN style → recursive FILL call. Same expectation as FILL.
        img = Image.new("RGB", (200, 100), (1, 2, 3))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.SPAN, 100, 100
        )
        assert rendered.size == (100, 100)
        assert (ox, oy) == (0, 0)
