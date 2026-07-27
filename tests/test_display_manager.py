"""Tests for DisplayManager — per-monitor wallpaper lifecycle, canvas compositing, and hotplug detection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.display_manager import DisplayManager
from wallpaper_auto.models import ConfigModel, ResourceConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
from wallpaper_auto.util.display_utils import DisplayInfo
from wallpaper_auto.util.wallpaper_util import WallpaperStyle

_DEVICE_A = r"\\?\DISPLAY#TEST#{device-a}"
_DEVICE_B = r"\\?\DISPLAY#TEST#{device-b}"


@pytest.fixture
def _config_store_with_cache(tmp_path: Path):
    """Provide a ConfigStore singleton pointing its cache at tmp_path."""
    ConfigStore.clear_instance()
    store = ConfigStore()
    store.config = ConfigModel(
        resource={"a": ResourceConfig(name="static_wallpaper", config={"path": "dummy"})},
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


def _make_resource(**overrides) -> MagicMock:
    r = MagicMock(spec=BaseResource)
    r._bind_plot_canvas = MagicMock()
    r.mount = MagicMock()
    r.demount = MagicMock()
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _add_display(
    dm: DisplayManager,
    device_path: str,
    style: WallpaperStyle = WallpaperStyle.FILL,
    wp_path: Path = Path("C:/orig.jpg"),
) -> None:
    with (
        patch("wallpaper_auto.display_manager.get_wallpaper_style", return_value=style),
        patch("wallpaper_auto.display_manager.get_wallpaper", return_value=wp_path),
    ):
        dm.add_display(device_path)


def _mock_com_session(mock_session: MagicMock) -> None:
    mock_session.return_value.__enter__ = MagicMock()
    mock_session.return_value.__exit__ = MagicMock(return_value=False)


class TestDisplayManager:
    """Constructor, start/stop, and active_monitor_device_path."""

    def test_init_creates_empty_state(self):
        dm = DisplayManager()
        assert dm._restore_wallpaper == {}
        assert dm._display_resource_map == {}
        assert dm._canvas_buffer == {}

    def test_empty_monitor_device_path_initially(self):
        assert DisplayManager().active_monitor_device_path == set()

    def test_active_monitor_device_path_after_add(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        _add_display(dm, _DEVICE_B)
        assert dm.active_monitor_device_path == {_DEVICE_A, _DEVICE_B}

    def test_start_adds_each_display(self):
        dm = DisplayManager()
        displays = [
            _make_display(_DEVICE_A, 0, 0, 100, 100),
            _make_display(_DEVICE_B, 100, 0, 100, 100),
        ]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.get_wallpaper_style", return_value=WallpaperStyle.FILL),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            dm.start()
        assert set(dm._display_resource_map.keys()) == {_DEVICE_A, _DEVICE_B}

    def test_stop_without_displays_is_noop(self):
        dm = DisplayManager()
        with patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set:
            dm.stop(restore=False)
        mock_set.assert_not_called()

    def test_stop_restore_true_sets_wallpaper_and_style(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_DEVICE_A, custom)
        with (
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.stop(restore=True)
        mock_set_wp.assert_called_once_with(_DEVICE_A, Path("C:/orig.jpg"))
        mock_set_style.assert_called_once_with(WallpaperStyle.FILL)

    def test_stop_restore_false_does_not_set_wallpaper(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_DEVICE_A, custom)
        with (
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.stop(restore=False)
        mock_set_wp.assert_not_called()
        mock_set_style.assert_not_called()


class TestDisplayManagerAddRemove:
    """add_display, remove_display, update_resource, update_canvas_buffer."""

    def test_add_creates_restore_and_active_resource(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A, style=WallpaperStyle.STRETCH)
        assert dm._restore_wallpaper[_DEVICE_A] == (WallpaperStyle.STRETCH, Path("C:/orig.jpg"))
        assert isinstance(dm._display_resource_map[_DEVICE_A], StaticWallpaper)
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
        assert mock_wp.call_count == 1
        assert mock_style.call_count == 1

    def test_remove_demounts_active_and_remounts_restore(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_DEVICE_A, custom)
        with (
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(_DEVICE_A)
        custom.demount.assert_called_once()
        mock_static.assert_called_once()
        assert dm._display_resource_is_patch[_DEVICE_A] is True

    def test_remove_nonexistent_display_raises_key_error(self):
        dm = DisplayManager()
        with pytest.raises(KeyError):
            dm.remove_display(_DEVICE_A)

    def test_remove_already_patched_display_raises_value_error(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        with pytest.raises(ValueError, match="do not have a resource"):
            dm.remove_display(_DEVICE_A)

    def test_update_swaps_active_resource(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        prev = dm._display_resource_map[_DEVICE_A]
        with patch.object(prev, "demount") as mock_demount:
            new_resource = _make_resource()
            dm.update_resource(_DEVICE_A, new_resource)
            mock_demount.assert_called_once()
        assert dm._display_resource_map[_DEVICE_A] is new_resource
        assert dm._display_resource_is_patch[_DEVICE_A] is False
        new_resource._bind_plot_canvas.assert_called_once()
        new_resource.mount.assert_called_once()

    def test_update_nonexistent_display_raises_key_error(self):
        dm = DisplayManager()
        with pytest.raises(KeyError):
            dm.update_resource(_DEVICE_A, _make_resource())

    def test_update_canvas_buffer_stores_entry(self):
        dm = DisplayManager()
        img = Image.new("RGB", (10, 10))
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FILL, img, immediate_update=False)
        assert dm._canvas_buffer[_DEVICE_A] == (WallpaperStyle.FILL, img)
        mock_plot.assert_not_called()

    def test_update_canvas_buffer_immediate_triggers_plot(self):
        dm = DisplayManager()
        img = Image.new("RGB", (10, 10))
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FILL, img, immediate_update=True)
        mock_plot.assert_called_once()

    def test_update_canvas_buffer_overwrites_existing_entry(self):
        dm = DisplayManager()
        img_a = Image.new("RGB", (10, 10), (255, 0, 0))
        img_b = Image.new("RGB", (10, 10), (0, 255, 0))
        dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FILL, img_a)
        dm.update_canvas_buffer(_DEVICE_A, WallpaperStyle.FIT, img_b)
        assert dm._canvas_buffer[_DEVICE_A] == (WallpaperStyle.FIT, img_b)


@pytest.mark.usefixtures("_config_store_with_cache")
class TestDisplayManagerPlotCanvas:
    """plot_canvas composites the buffer and applies it as a spanned wallpaper."""

    def test_empty_buffer_is_noop(self):
        dm = DisplayManager()
        with (
            patch("wallpaper_auto.display_manager.get_display_info") as mock_info,
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            dm.plot_canvas()
        mock_info.assert_not_called()
        mock_session.assert_not_called()

    def test_no_matching_displays_returns_early(self):
        dm = DisplayManager()
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, Image.new("RGB", (10, 10)))
        unrelated = _make_display(_DEVICE_B, 0, 0, 100, 100)
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[unrelated]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            dm.plot_canvas()
        mock_session.assert_not_called()

    def test_multi_monitor_composite_writes_and_applies(self):
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
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        rendered = Image.open(ConfigStore.instance.cache_path / "_composite.png")
        assert rendered.size == (100, 50)
        mock_set_wp.assert_called_once()
        mock_set_style.assert_called_once()
        assert mock_set_style.call_args.args[0] is WallpaperStyle.SPAN

    def test_span_entry_replaces_entire_composite(self):
        dm = DisplayManager()
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.SPAN, Image.new("RGB", (40, 40), (10, 20, 30)))
        with (
            patch("wallpaper_auto.display_manager.get_display_info",
                  return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        rendered = Image.open(ConfigStore.instance.cache_path / "_composite.png")
        assert rendered.size == (40, 40)

    def test_span_entry_with_path_loads_from_disk(self):
        dm = DisplayManager()
        full_path = ConfigStore.instance.cache_path / "span.png"
        Image.new("RGB", (40, 40), (10, 20, 30)).save(full_path)
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.SPAN, full_path)
        with (
            patch("wallpaper_auto.display_manager.get_display_info",
                  return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert (ConfigStore.instance.cache_path / "_composite.png").exists()

    def test_span_overrides_non_span_entries(self):
        """SPAN entry in buffer replaces all other entries."""
        dm = DisplayManager()
        span_img = Image.new("RGB", (100, 50), (10, 20, 30))
        fill_img = Image.new("RGB", (30, 30), (255, 0, 0))
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.SPAN, span_img)
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, fill_img)
        displays = [
            _make_display(_DEVICE_A, 0, 0, 100, 50),
            _make_display(_DEVICE_B, 100, 0, 30, 30),
        ]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        rendered = Image.open(ConfigStore.instance.cache_path / "_composite.png")
        # SPAN fills entire 130x50 canvas, ignoring the FILL entry.
        assert rendered.size == (130, 50)

    def test_buffer_entry_with_unknown_display_is_skipped(self):
        dm = DisplayManager()
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, Image.new("RGB", (30, 30)))
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, Image.new("RGB", (30, 30)))
        with (
            patch("wallpaper_auto.display_manager.get_display_info",
                  return_value=[_make_display(_DEVICE_A, 0, 0, 30, 30)]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert (ConfigStore.instance.cache_path / "_composite.png").exists()

    def test_path_buffers_are_loaded_from_disk(self):
        dm = DisplayManager()
        cache = ConfigStore.instance.cache_path
        path_a = cache / "a.png"
        path_b = cache / "b.png"
        Image.new("RGB", (40, 40), (200, 0, 0)).save(path_a)
        Image.new("RGB", (40, 40), (0, 200, 0)).save(path_b)
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, path_a)
        dm._canvas_buffer[_DEVICE_B] = (WallpaperStyle.FILL, path_b)
        displays = [
            _make_display(_DEVICE_A, 0, 0, 40, 40),
            _make_display(_DEVICE_B, 40, 0, 40, 40),
        ]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert (cache / "_composite.png").exists()

    def test_missing_path_buffer_raises_file_not_found(self):
        dm = DisplayManager()
        missing = ConfigStore.instance.cache_path / "nonexistent.png"
        dm._canvas_buffer[_DEVICE_A] = (WallpaperStyle.FILL, missing)
        with (
            patch("wallpaper_auto.display_manager.get_display_info",
                  return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            _mock_com_session(mock_session)
            with pytest.raises(FileNotFoundError):
                dm.plot_canvas()


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
        img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
        rendered, _, _ = DisplayManager._render_image_for_region(
            img, WallpaperStyle.STRETCH, 10, 10
        )
        assert rendered.mode == "RGB"
        # 128/255 alpha over black → expected pixel value is (128, 0, 0).
        assert rendered.getpixel((0, 0)) == (128, 0, 0)

    def test_rgba_with_non_stretch_styles(self):
        for style in (WallpaperStyle.FILL, WallpaperStyle.FIT, WallpaperStyle.CENTER):
            img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
            rendered, _, _ = DisplayManager._render_image_for_region(
                img, style, 20, 20
            )
            assert rendered.mode == "RGB"
            assert rendered.getpixel((0, 0)) == (128, 0, 0)

    def test_non_rgb_mode_converted_to_rgb(self):
        img = Image.new("L", (10, 10), 128)
        rendered, _, _ = DisplayManager._render_image_for_region(
            img, WallpaperStyle.STRETCH, 10, 10
        )
        assert rendered.mode == "RGB"

    def test_fill_crops_center(self):
        img = Image.new("RGB", (200, 100), (255, 0, 0))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.FILL, 100, 100
        )
        assert rendered.size == (100, 100)
        assert (ox, oy) == (0, 0)

    def test_fit_returns_positive_offset(self):
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
        img = Image.new("RGB", (200, 100), (1, 2, 3))
        rendered, ox, oy = DisplayManager._render_image_for_region(
            img, WallpaperStyle.SPAN, 100, 100
        )
        assert rendered.size == (100, 100)
        assert (ox, oy) == (0, 0)


class TestDisplayManagerUpdateDisplay:
    """update_display hotplug detection."""

    def test_plugged_display_is_added(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        displays = [
            _make_display(_DEVICE_A, 0, 0, 100, 100),
            _make_display(_DEVICE_B, 100, 0, 100, 100),
        ]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.get_wallpaper_style", return_value=WallpaperStyle.FILL),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            result = dm.update_display()
        assert _DEVICE_B in dm._display_resource_map
        assert {d.monitor_device_path for d in result} == {_DEVICE_A, _DEVICE_B}

    def test_unplugged_display_is_removed(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        _add_display(dm, _DEVICE_B)
        custom = _make_resource()
        dm.update_resource(_DEVICE_B, custom)
        displays = [_make_display(_DEVICE_A, 0, 0, 100, 100)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            result = dm.update_display()
        # remove_display replaces the resource with a patch; the display stays in the map.
        assert dm._display_resource_is_patch[_DEVICE_B] is True
        assert [d.monitor_device_path for d in result] == [_DEVICE_A]

    def test_no_change_returns_current_displays(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        displays = [_make_display(_DEVICE_A, 0, 0, 100, 100)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.get_wallpaper_style", return_value=WallpaperStyle.FILL),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            result = dm.update_display()
        assert [d.monitor_device_path for d in result] == [_DEVICE_A]
