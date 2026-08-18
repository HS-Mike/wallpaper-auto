"""Tests for DisplayManager — wallpaper lifecycle, canvas compositing, and hotplug detection."""

import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from wallpaper_auto.config_store import ConfigStore
from wallpaper_auto.display_manager import DisplayManager, DisplayState
from wallpaper_auto.models import CacheConfig, CacheResizeConfig, ConfigModel, ResourceConfig
from wallpaper_auto.resource.base_resource import BaseResource
from wallpaper_auto.resource.static_wallpaper import StaticWallpaper
from wallpaper_auto.util.display_utils import (
    LUID,
    DisplayCapability,
    DisplayId,
    DisplayInfo,
)
from wallpaper_auto.util.wallpaper_util import WallpaperStyle

_DEVICE_A = r"\\?\DISPLAY#TEST#{device-a}"
_DEVICE_B = r"\\?\DISPLAY#TEST#{device-b}"

_DEFAULT_CAPABILITY = DisplayCapability(
    scale=(100, 125, 150),
    reference_scale=100,
    resolution=((100, 100), (50, 50)),
)


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
        cache=CacheConfig(path=str(tmp_path)),
    )
    yield tmp_path


def _make_display(device_path: str, x: int, y: int, w: int, h: int) -> DisplayInfo:
    return DisplayInfo(
        device_name="\\\\.\\DISPLAY1",
        model="test",
        source_resolution=(w, h),
        position=(x, y),
        target_resolution=(w, h),
        adapter_id=LUID(1, 2),
        source_id=0,
        scale=100,
        monitor_device_path=device_path,
    )


def _did(device_path: str, w: int = 100, h: int = 100) -> DisplayId:
    """The DisplayId a display with *device_path* gets under ``_make_display`` defaults."""
    return _make_display(device_path, 0, 0, w, h).display_id


def _make_resource(**overrides) -> MagicMock:
    r = MagicMock(spec=BaseResource)
    r.mount = MagicMock()
    r.demount = MagicMock()
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _put_canvas(
    dm: DisplayManager,
    device: str,
    style: WallpaperStyle,
    img: Path | Image.Image,
    w: int = 100,
    h: int = 100,
) -> None:
    """Register *device* with a buffered canvas entry (for plot tests).

    *w* and *h* control the registered display's resolution so the display_id
    matches the corresponding ``_make_display(device, ..., w, h)`` used to
    patch ``get_display_info``.
    """
    display_id = _did(device, w, h)
    if display_id in dm._displays:
        dm._displays[display_id].canvas = (style, img)
    else:
        dm._displays[display_id] = DisplayState(
            display_id=display_id,
            display_info=_make_display(device, 0, 0, w, h),
            capability=_DEFAULT_CAPABILITY,
            resource=_make_resource(),
            is_patch=True,
            original_resolution=(w, h),
            original_scale=100,
            canvas=(style, img),
        )


def _add_display(
    dm: DisplayManager,
    device_path: str,
    style: WallpaperStyle = WallpaperStyle.FILL,
    wp_path: Path = Path("C:/orig.jpg"),
    display_info: DisplayInfo | None = None,
    capability: DisplayCapability | None = _DEFAULT_CAPABILITY,
) -> DisplayId:
    """Register *device_path* via :meth:`add_display` and return its DisplayId.

    *capability* is returned by the patched ``get_display_capability`` and
    defaults to ``_DEFAULT_CAPABILITY`` (``add_display`` asserts it is non-None).
    """
    info = display_info if display_info is not None else _make_display(device_path, 0, 0, 100, 100)
    with (
        patch(
            "wallpaper_auto.display_manager.get_display_resolution",
            return_value=info.source_resolution,
        ),
        patch("wallpaper_auto.display_manager.get_display_scale", return_value=info.scale),
        patch("wallpaper_auto.display_manager.get_display_capability", return_value=capability),
        patch("wallpaper_auto.display_manager.get_wallpaper_style", return_value=style),
        patch("wallpaper_auto.display_manager.get_wallpaper", return_value=wp_path),
    ):
        return dm.add_display(info)


def _mock_com_session(mock_session: MagicMock) -> None:
    mock_session.return_value.__enter__ = MagicMock()
    mock_session.return_value.__exit__ = MagicMock(return_value=False)


def _latest_composite() -> Path:
    """Return the most recently-written composite path under cache_path/composite/."""
    composite_dir = ConfigStore.instance.cache_path / "composite"
    files = sorted(composite_dir.glob("_composite_*.png"))
    assert files, "no composite file written"
    return files[-1]


class TestDisplayManager:
    """Constructor, start/stop, and active_display_ids."""

    def test_init_creates_empty_state(self):
        dm = DisplayManager()
        assert dm._displays == {}
        assert dm._wallpaper_applied is False

    def test_empty_display_ids_initially(self):
        assert DisplayManager().active_display_ids == set()

    def test_active_display_ids_after_add(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        _add_display(dm, _DEVICE_B)
        assert dm.active_display_ids == {_did(_DEVICE_A), _did(_DEVICE_B)}

    def test_start_adds_each_display(self):
        dm = DisplayManager()
        displays = [
            _make_display(_DEVICE_A, 0, 0, 100, 100),
            _make_display(_DEVICE_B, 100, 0, 100, 100),
        ]
        style_patch = patch(
            "wallpaper_auto.display_manager.get_wallpaper_style",
            return_value=WallpaperStyle.FILL,
        )
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=(100, 100),
            ),
            patch("wallpaper_auto.display_manager.get_display_scale", return_value=100),
            patch(
                "wallpaper_auto.display_manager.get_display_capability",
                return_value=_DEFAULT_CAPABILITY,
            ),
            style_patch,
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            dm.start()
        assert set(dm._displays.keys()) == {_did(_DEVICE_A), _did(_DEVICE_B)}

    def test_start_returns_early_when_display_query_fails(self):
        """A transient display-query failure leaves the manager with no displays."""
        dm = DisplayManager()
        with patch("wallpaper_auto.display_manager.get_display_info", return_value=None):
            dm.start()
        assert dm._displays == {}

    @pytest.mark.usefixtures("_config_store_with_cache")
    def test_black_wallpaper_path_creates_and_reuses(self):
        """The pure-black wallpaper is created on first use, then reused."""
        dm = DisplayManager()
        cache_dir = ConfigStore.instance.cache_path
        first = dm._black_wallpaper_path()
        assert first == cache_dir / "_black.png"
        assert first.is_file()
        with Image.open(first) as img:
            assert img.size == (1, 1)
        assert dm._black_wallpaper_path() == first

    def test_stop_without_displays_is_noop(self):
        dm = DisplayManager()
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set,
        ):
            _mock_com_session(mock_session)
            dm.stop()
        mock_set.assert_not_called()

    def test_stop_sets_wallpaper_and_style(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 100, 100)],
            ),
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            _mock_com_session(mock_session)
            mock_static.return_value = _make_resource()
            dm.stop()
        mock_set_wp.assert_called_once_with(_DEVICE_A, Path("C:/orig.jpg"))
        mock_set_style.assert_called_once_with(WallpaperStyle.FILL)

    def test_start_raises_if_wallpaper_already_applied(self):
        """A composite applied before start() is an invalid lifecycle state."""
        dm = DisplayManager()
        dm._wallpaper_applied = True
        with (
            pytest.raises(RuntimeError, match="before start"),
            patch("wallpaper_auto.display_manager.get_display_info"),
        ):
            dm.start()

    def test_stop_clears_wallpaper_applied(self):
        dm = DisplayManager()
        dm._wallpaper_applied = True
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            _mock_com_session(mock_session)
            dm.stop()
        assert dm._wallpaper_applied is False

    def test_stop_restores_genuine_and_blacks_hotplugged(self):
        """A genuine display is restored; a hotplugged display is filled black."""
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)  # startup: genuine original wallpaper record
        dm.update_resource(_did(_DEVICE_A), _make_resource())
        dm._wallpaper_applied = True
        _add_display(dm, _DEVICE_B)  # hotplugged: no original wallpaper record
        dm.update_resource(_did(_DEVICE_B), _make_resource())
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[
                    _make_display(_DEVICE_A, 0, 0, 100, 100),
                    _make_display(_DEVICE_B, 100, 0, 100, 100),
                ],
            ),
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
            patch.object(dm, "_black_wallpaper_path", return_value=Path("C:/black.png")),
        ):
            _mock_com_session(mock_session)
            mock_static.return_value = _make_resource()
            dm.stop()
        # A's genuine original is restored; B (no original wallpaper) is filled black instead.
        assert mock_set_wp.call_count == 2
        assert mock_set_wp.call_args_list[0].args[0] == _DEVICE_A
        assert mock_set_wp.call_args_list[1].args[0] == _DEVICE_B
        assert mock_set_style.call_args_list[0].args[0] is WallpaperStyle.FILL
        assert mock_set_style.call_args_list[1].args[0] is WallpaperStyle.FILL

    def test_stop_blacks_hotplugged_patch(self):
        """A hotplugged patch at stop() is dropped and filled black.

        A display added after the app's composite has no genuine original wallpaper;
        stopping must drop it (not crash) and fill it with pure black instead of
        leaving it on the app's composite.
        """
        dm = DisplayManager()
        dm._wallpaper_applied = True
        _add_display(dm, _DEVICE_A)  # hotplugged patch, never got a resource
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style") as mock_set_style,
            patch.object(dm, "_black_wallpaper_path", return_value=Path("C:/black.png")),
        ):
            _mock_com_session(mock_session)
            dm.stop()
        assert _did(_DEVICE_A) not in dm._displays
        mock_set_wp.assert_called_once_with(_DEVICE_A, Path("C:/black.png"))
        mock_set_style.assert_called_once_with(WallpaperStyle.FILL)

    def test_stop_tolerates_wallpaper_com_failure(self):
        """An OSError from set_wallpaper during stop() is logged, not raised."""
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 100, 100)],
            ),
            patch(
                "wallpaper_auto.display_manager.set_wallpaper",
                side_effect=OSError("display disconnected"),
            ) as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            patch("wallpaper_auto.display_manager.StaticWallpaper"),
        ):
            _mock_com_session(mock_session)
            dm.stop()  # must not raise
        assert mock_set_wp.call_count >= 1


class TestDisplayManagerAddRemove:
    """add_display, remove_display, update_resource, update_canvas."""

    def test_add_creates_restore_and_active_resource(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A, style=WallpaperStyle.STRETCH)
        state = dm._displays[_did(_DEVICE_A)]
        assert state.original_wallpaper == (WallpaperStyle.STRETCH, Path("C:/orig.jpg"))
        assert isinstance(state.resource, StaticWallpaper)
        assert state.resource.display is not None
        assert state.resource.display.monitor_device_path == _DEVICE_A
        assert state.is_patch is True

    def test_add_is_idempotent(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=(100, 100),
            ),
            patch("wallpaper_auto.display_manager.get_display_scale", return_value=100),
            patch(
                "wallpaper_auto.display_manager.get_display_capability",
                return_value=_DEFAULT_CAPABILITY,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ) as mock_style,
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ) as mock_wp,
        ):
            dm.add_display(_make_display(_DEVICE_A, 0, 0, 100, 100))
            first = dm._displays[_did(_DEVICE_A)]
            dm.add_display(_make_display(_DEVICE_A, 0, 0, 100, 100))
            second = dm._displays[_did(_DEVICE_A)]
        assert first is second
        assert mock_wp.call_count == 1
        assert mock_style.call_count == 1

    def test_add_raises_when_resolution_query_fails(self):
        """A None resolution from the OS is a genuine error and is raised."""
        dm = DisplayManager()
        info = _make_display(_DEVICE_A, 0, 0, 100, 100)
        with patch("wallpaper_auto.display_manager.get_display_resolution", return_value=None):
            with pytest.raises(RuntimeError, match="display resolution request fail"):
                dm.add_display(info)
        assert _did(_DEVICE_A) not in dm._displays

    def test_add_raises_when_scale_query_fails(self):
        """A None scale from the OS is a genuine error and is raised."""
        dm = DisplayManager()
        info = _make_display(_DEVICE_A, 0, 0, 100, 100)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=(100, 100),
            ),
            patch("wallpaper_auto.display_manager.get_display_scale", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="display scale request fail"):
                dm.add_display(info)
        assert _did(_DEVICE_A) not in dm._displays

    def test_remove_demounts_active_and_remounts_restore(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 100, 100)],
            ),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(_did(_DEVICE_A))
        custom.demount.assert_called_once()
        mock_static.assert_called_once()
        assert dm._displays[_did(_DEVICE_A)].is_patch is True

    def test_remove_without_restore_leaves_resource_mounted(self):
        """restore_original=False drops the display but keeps the resource mounted.

        Used by ``stop`` for the at_shutdown target: the current wallpaper
        survives, so no restore patch is mounted and the resource is not
        demounted.
        """
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 100, 100)],
            ),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            dm.remove_display(_did(_DEVICE_A), restore_original=False)
        assert _did(_DEVICE_A) not in dm._displays
        custom.demount.assert_not_called()
        mock_static.assert_not_called()

    def test_remove_drops_unplugged_display_and_demounts_resource(self):
        """A display with an original wallpaper record no longer connected is dropped."""
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[],
            ),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(_did(_DEVICE_A))
        assert _did(_DEVICE_A) not in dm._displays
        custom.demount.assert_called_once()
        mock_static.assert_not_called()

    def test_remove_keeps_patch_when_display_query_fails(self):
        """An unqueryable topology keeps the conservative restore path."""
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=None,
            ),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(_did(_DEVICE_A))
        custom.demount.assert_called_once()
        mock_static.assert_called_once()
        assert dm._displays[_did(_DEVICE_A)].is_patch is True

    def test_remove_nonexistent_display_raises_key_error(self):
        dm = DisplayManager()
        with pytest.raises(KeyError):
            dm.remove_display(_did(_DEVICE_A))

    def test_remove_already_patched_display_is_dropped(self):
        """A patch with a genuine original wallpaper record is dropped, not raised on.

        Regression: a display added while ``_wallpaper_applied`` is False keeps
        an original wallpaper record but never gets a resource; when such a
        display is unplugged ``remove_display`` must drop it rather than crash.
        """
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        assert dm._displays[_did(_DEVICE_A)].original_wallpaper is not None
        dm.remove_display(_did(_DEVICE_A))
        assert _did(_DEVICE_A) not in dm._displays

    def test_update_swaps_active_resource(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        prev = dm._displays[_did(_DEVICE_A)].resource
        with patch.object(prev, "demount") as mock_demount:
            new_resource = _make_resource()
            dm.update_resource(_did(_DEVICE_A), new_resource)
            mock_demount.assert_called_once()
        assert dm._displays[_did(_DEVICE_A)].resource is new_resource
        assert dm._displays[_did(_DEVICE_A)].is_patch is False
        new_resource.mount.assert_called_once()

    def test_update_nonexistent_display_raises_key_error(self):
        dm = DisplayManager()
        with pytest.raises(KeyError):
            dm.update_resource(_did(_DEVICE_A), _make_resource())

    def test_update_canvas_stores_entry(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        img = Image.new("RGB", (10, 10))
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.update_canvas(_did(_DEVICE_A), WallpaperStyle.FILL, img)
        assert dm._displays[_did(_DEVICE_A)].canvas == (WallpaperStyle.FILL, img)
        mock_plot.assert_not_called()

    def test_update_canvas_overwrites_existing_entry(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        img_a = Image.new("RGB", (10, 10), (255, 0, 0))
        img_b = Image.new("RGB", (10, 10), (0, 255, 0))
        dm.update_canvas(_did(_DEVICE_A), WallpaperStyle.FILL, img_a)
        assert dm._displays[_did(_DEVICE_A)].canvas == (WallpaperStyle.FILL, img_a)
        dm.update_canvas(_did(_DEVICE_A), WallpaperStyle.FIT, img_b)
        assert dm._displays[_did(_DEVICE_A)].canvas == (WallpaperStyle.FIT, img_b)

    def test_add_display_after_wallpaper_applied_not_recorded(self):
        dm = DisplayManager()
        dm._wallpaper_applied = True
        _add_display(dm, _DEVICE_A)
        state = dm._displays[_did(_DEVICE_A)]
        assert state.original_wallpaper is None
        assert state.is_patch is True

    def test_remove_display_hotplugged_resource_does_not_mount_patch(self):
        dm = DisplayManager()
        dm._wallpaper_applied = True
        _add_display(dm, _DEVICE_A)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_A), custom)
        with patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static:
            dm.remove_display(_did(_DEVICE_A))
        custom.demount.assert_called_once()
        mock_static.assert_not_called()
        assert _did(_DEVICE_A) not in dm._displays

    def test_update_canvas_after_remove_raises_key_error(self):
        """update_canvas on a display popped by remove_display raises KeyError.

        The source removed the stale-update tolerance: ``update_canvas`` now
        indexes ``_displays`` directly, so buffering for a removed display is a
        genuine error rather than a silently dropped write.
        """
        dm = DisplayManager()
        dm._wallpaper_applied = True
        _add_display(dm, _DEVICE_A)  # hotplugged patch
        dm.remove_display(_did(_DEVICE_A))  # pops the display
        with pytest.raises(KeyError):
            dm.update_canvas(_did(_DEVICE_A), WallpaperStyle.FILL, Image.new("RGB", (10, 10)))


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

    def test_plot_canvas_sets_wallpaper_applied(self):
        dm = DisplayManager()
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (10, 10)), w=10, h=10)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 10, 10)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert dm._wallpaper_applied is True

    def test_no_matching_displays_returns_early(self):
        dm = DisplayManager()
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (10, 10)))
        unrelated = _make_display(_DEVICE_B, 0, 0, 100, 100)
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[unrelated]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
        ):
            dm.plot_canvas()
        mock_session.assert_not_called()

    def test_skips_composite_when_display_query_fails(self):
        """get_display_info returning None (query failure) skips the composite."""
        dm = DisplayManager()
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (10, 10)))
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=None),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
        ):
            dm.plot_canvas()
        mock_session.assert_not_called()
        mock_set_wp.assert_not_called()

    def test_multi_monitor_composite_writes_and_applies(self):
        dm = DisplayManager()
        img_a = Image.new("RGB", (50, 50), (255, 0, 0))
        img_b = Image.new("RGB", (50, 50), (0, 255, 0))
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, img_a, w=50, h=50)
        _put_canvas(dm, _DEVICE_B, WallpaperStyle.FILL, img_b, w=50, h=50)
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
        rendered = Image.open(_latest_composite())
        assert rendered.size == (100, 50)
        mock_set_wp.assert_called_once()
        mock_set_style.assert_called_once()
        assert mock_set_style.call_args.args[0] is WallpaperStyle.SPAN

    def test_span_entry_replaces_entire_composite(self):
        dm = DisplayManager()
        _put_canvas(
            dm, _DEVICE_A, WallpaperStyle.SPAN, Image.new("RGB", (40, 40), (10, 20, 30)), w=40, h=40
        )
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        rendered = Image.open(_latest_composite())
        assert rendered.size == (40, 40)

    def test_span_entry_with_path_loads_from_disk(self):
        dm = DisplayManager()
        full_path = ConfigStore.instance.cache_path / "span.png"
        Image.new("RGB", (40, 40), (10, 20, 30)).save(full_path)
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.SPAN, full_path, w=40, h=40)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert _latest_composite().exists()

    def test_center_style_path_buffer_loads_from_disk(self):
        """CENTER buffers that are Paths are opened directly, bypassing the cache."""
        dm = DisplayManager()
        src = ConfigStore.instance.cache_path / "src.png"
        Image.new("RGB", (20, 20), (10, 20, 30)).save(src)
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.CENTER, src, w=40, h=40)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert _latest_composite().exists()

    def test_plot_creates_cache_dir_when_missing(self, tmp_path):
        """plot_canvas lazily creates the cache directory when it is absent."""
        fresh_dir = tmp_path / "fresh"
        ConfigStore.clear_instance()
        store = ConfigStore()
        store.config = ConfigModel(
            resource={"a": ResourceConfig(name="static_wallpaper", config={"path": "dummy"})},
            trigger=[],
            rule=[],
            fallback_target="a",
            cache=CacheConfig(path=str(fresh_dir)),
        )

        dm = DisplayManager()
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (10, 10)), w=10, h=10)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 10, 10)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert fresh_dir.exists()
        assert (fresh_dir / "composite").exists()

    def test_gc_old_composites_tolerates_unlink_failure(self, tmp_path):
        """A composite the OS is still reading cannot be deleted; the failure is logged."""
        dm = DisplayManager()
        composite_dir = tmp_path / "composite"
        composite_dir.mkdir()
        stale = composite_dir / "_composite_1.png"
        stale.write_bytes(b"stale")
        with (
            patch.object(Path, "unlink", side_effect=OSError("file in use")),
            patch("wallpaper_auto.display_manager.logger") as mock_logger,
        ):
            dm._gc_old_composites(composite_dir)
        mock_logger.warning.assert_called_once()
        assert stale.exists()

    def test_span_overrides_non_span_entries(self):
        """SPAN entry in buffer replaces all other entries."""
        dm = DisplayManager()
        span_img = Image.new("RGB", (100, 50), (10, 20, 30))
        fill_img = Image.new("RGB", (30, 30), (255, 0, 0))
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.SPAN, span_img, w=100, h=50)
        _put_canvas(dm, _DEVICE_B, WallpaperStyle.FILL, fill_img, w=30, h=30)
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
        rendered = Image.open(_latest_composite())
        # SPAN fills entire 130x50 canvas, ignoring the FILL entry.
        assert rendered.size == (130, 50)

    def test_buffer_entry_with_unknown_display_is_skipped(self):
        dm = DisplayManager()
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (30, 30)), w=30, h=30)
        _put_canvas(dm, _DEVICE_B, WallpaperStyle.FILL, Image.new("RGB", (30, 30)), w=30, h=30)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 30, 30)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()
        assert _latest_composite().exists()

    def test_path_buffers_are_loaded_from_disk(self):
        dm = DisplayManager()
        cache = ConfigStore.instance.cache_path
        path_a = cache / "a.png"
        path_b = cache / "b.png"
        Image.new("RGB", (40, 40), (200, 0, 0)).save(path_a)
        Image.new("RGB", (40, 40), (0, 200, 0)).save(path_b)
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, path_a, w=40, h=40)
        _put_canvas(dm, _DEVICE_B, WallpaperStyle.FILL, path_b, w=40, h=40)
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
        assert _latest_composite().exists()

    def test_missing_path_buffer_returns_blank_image(self):
        """Missing source file should not crash — cache returns a blank fallback."""
        dm = DisplayManager()
        dm.init_cache(
            ConfigStore.instance.cache_path,
            resize_enabled=True,
            max_size_bytes=200 * 1024 * 1024,
            evict_ratio=0.9,
        )
        missing = ConfigStore.instance.cache_path / "nonexistent.png"
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, missing, w=40, h=40)
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_info",
                return_value=[_make_display(_DEVICE_A, 0, 0, 40, 40)],
            ),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper") as mock_set_wp,
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()  # should not raise
        mock_set_wp.assert_called_once()

    def test_concurrent_plot_and_buffer_updates_are_thread_safe(self):
        """plot_canvas and update_canvas must be safe under concurrency.

        Regression test: a ResourceCycle cycling thread buffers updates while the
        worker loop composites. Without the internal lock this raced on the
        buffer snapshot (``RuntimeError``) and on the ``_composite.png`` file
        (torn writes).
        """
        dm = DisplayManager()
        img = Image.new("RGB", (50, 50), (255, 0, 0))
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, img, w=50, h=50)
        _put_canvas(dm, _DEVICE_B, WallpaperStyle.FILL, Image.new("RGB", (10, 10)), w=10, h=10)
        displays = [_make_display(_DEVICE_A, 0, 0, 50, 50)]

        errors: list[BaseException] = []

        def buffer_updater() -> None:
            # Update a buffer entry concurrently with compositing snapshots.
            for _ in range(50):
                try:
                    dm.update_canvas(
                        _did(_DEVICE_B, 10, 10), WallpaperStyle.FILL, Image.new("RGB", (10, 10))
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        def plotter() -> None:
            with (
                patch(
                    "wallpaper_auto.display_manager.get_display_info",
                    return_value=displays,
                ),
                patch("wallpaper_auto.display_manager.com_session") as mock_session,
                patch("wallpaper_auto.display_manager.set_wallpaper"),
                patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            ):
                _mock_com_session(mock_session)
                for _ in range(50):
                    try:
                        dm.plot_canvas()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)

        threads = [
            threading.Thread(target=buffer_updater),
            threading.Thread(target=plotter),
            threading.Thread(target=plotter),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        composite = Image.open(_latest_composite())
        assert composite.size == (50, 50)

    def test_concurrent_hotplug_and_plot_are_thread_safe(self):
        """Structural mutation + compositing + canvas updates must not race.

        Regression: without the unified ``_lock``, ``add_display``/``remove_display``
        mutating ``_displays`` while ``plot_canvas`` iterates it would hit
        "dictionary changed size during iteration", and ``update_canvas`` could
        race ``remove_display``.
        """
        dm = DisplayManager()
        dm._wallpaper_applied = True
        _put_canvas(
            dm, _DEVICE_A, WallpaperStyle.FILL, Image.new("RGB", (50, 50), (255, 0, 0)), w=50, h=50
        )
        displays = [_make_display(_DEVICE_A, 0, 0, 50, 50)]
        errors: list[BaseException] = []

        def plotter() -> None:
            with (
                patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
                patch("wallpaper_auto.display_manager.com_session") as mock_session,
                patch("wallpaper_auto.display_manager.set_wallpaper"),
                patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            ):
                _mock_com_session(mock_session)
                for _ in range(50):
                    try:
                        dm.plot_canvas()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)

        def hotplugger() -> None:
            for i in range(30):
                device = f"{_DEVICE_A}#{i}"
                try:
                    with (
                        patch(
                            "wallpaper_auto.display_manager.get_display_resolution",
                            return_value=(100, 100),
                        ),
                        patch(
                            "wallpaper_auto.display_manager.get_display_scale",
                            return_value=100,
                        ),
                        patch(
                            "wallpaper_auto.display_manager.get_display_capability",
                            return_value=_DEFAULT_CAPABILITY,
                        ),
                        patch(
                            "wallpaper_auto.display_manager.get_wallpaper_style",
                            return_value=WallpaperStyle.FILL,
                        ),
                        patch(
                            "wallpaper_auto.display_manager.get_wallpaper",
                            return_value=Path("C:/orig.jpg"),
                        ),
                    ):
                        dm.add_display(_make_display(device, 0, 0, 100, 100))
                    dm.remove_display(_did(device))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        def buffer_updater() -> None:
            for _ in range(50):
                try:
                    dm.update_canvas(
                        _did(_DEVICE_A, 50, 50), WallpaperStyle.FILL, Image.new("RGB", (10, 10))
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        threads = [
            threading.Thread(target=plotter),
            threading.Thread(target=hotplugger),
            threading.Thread(target=buffer_updater),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        composite = Image.open(_latest_composite())
        assert composite.size == (50, 50)


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
            rendered, _, _ = DisplayManager._render_image_for_region(img, style, 20, 20)
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
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=(100, 100),
            ),
            patch("wallpaper_auto.display_manager.get_display_scale", return_value=100),
            patch(
                "wallpaper_auto.display_manager.get_display_capability",
                return_value=_DEFAULT_CAPABILITY,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            result = dm.update_display()
        assert result is not None
        assert _did(_DEVICE_B) in dm._displays
        assert {d.monitor_device_path for d in result} == {_DEVICE_A, _DEVICE_B}

    def test_unplugged_display_is_removed(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        _add_display(dm, _DEVICE_B)
        custom = _make_resource()
        dm.update_resource(_did(_DEVICE_B), custom)
        displays = [_make_display(_DEVICE_A, 0, 0, 100, 100)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
        ):
            mock_static.return_value = _make_resource()
            result = dm.update_display()
        assert result is not None
        # An unplugged display with a resource + original wallpaper record is
        # dropped, not kept as a patch; its resource is demounted and no patch
        # is mounted.
        assert _did(_DEVICE_B) not in dm._displays
        custom.demount.assert_called_once()
        mock_static.assert_not_called()
        assert [d.monitor_device_path for d in result] == [_DEVICE_A]

    def test_no_change_returns_current_displays(self):
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        displays = [_make_display(_DEVICE_A, 0, 0, 100, 100)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            result = dm.update_display()
        assert result is not None
        assert [d.monitor_device_path for d in result] == [_DEVICE_A]

    def test_query_failure_returns_none_and_leaves_state(self):
        """get_display_info returning None → sync skipped, existing displays untouched."""
        dm = DisplayManager()
        _add_display(dm, _DEVICE_A)
        with patch("wallpaper_auto.display_manager.get_display_info", return_value=None):
            result = dm.update_display()
        assert result is None
        assert dm.active_display_ids == {_did(_DEVICE_A)}


@pytest.mark.usefixtures("_config_store_with_cache")
class TestDisplayManagerCacheIntegration:
    """ImageCompressionCache integration with plot_canvas."""

    def test_cache_used_for_path_buffers(self, tmp_path: Path):
        """Path-based buffer entries should go through the cache."""
        dm = DisplayManager()
        dm.init_cache(
            tmp_path, resize_enabled=True, max_size_bytes=200 * 1024 * 1024, evict_ratio=0.9
        )
        cache_dir = tmp_path / "resized"

        src = tmp_path / "wallpaper.png"
        Image.new("RGB", (50, 50), (200, 100, 50)).save(src)
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, src, w=50, h=50)

        displays = [_make_display(_DEVICE_A, 0, 0, 50, 50)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()

        # The cache should have created the resized dir with one entry.
        assert cache_dir.is_dir()
        png_files = list(cache_dir.glob("*.png"))
        assert len(png_files) >= 1

        # Access count should be 1 after the first plot.
        cache = dm._image_cache
        assert cache is not None
        filename = next(iter(cache._entries))
        assert cache._entries[filename]["access_count"] == 1

        # Second call — cache hit, access_count incremented.
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()

        assert cache._entries[filename]["access_count"] == 2


@pytest.fixture
def _config_store_cache_disabled(tmp_path: Path):
    """Provide a ConfigStore singleton with the resized-image cache disabled."""
    ConfigStore.clear_instance()
    store = ConfigStore()
    store.config = ConfigModel(
        resource={"a": ResourceConfig(name="static_wallpaper", config={"path": "dummy"})},
        trigger=[],
        rule=[],
        fallback_target="a",
        cache=CacheConfig(path=str(tmp_path), resize=CacheResizeConfig(enabled=False)),
    )
    yield tmp_path


@pytest.mark.usefixtures("_config_store_cache_disabled")
class TestDisplayManagerCacheDisabled:
    """plot_canvas with the resized-image cache disabled in config."""

    def test_resize_cache_not_created_when_disabled(self, tmp_path: Path):
        dm = DisplayManager()
        dm.init_cache(
            tmp_path, resize_enabled=False, max_size_bytes=200 * 1024 * 1024, evict_ratio=0.9
        )
        src = tmp_path / "wallpaper.png"
        Image.new("RGB", (50, 50), (200, 100, 50)).save(src)
        _put_canvas(dm, _DEVICE_A, WallpaperStyle.FILL, src, w=50, h=50)

        displays = [_make_display(_DEVICE_A, 0, 0, 50, 50)]
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=displays),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
        ):
            _mock_com_session(mock_session)
            dm.plot_canvas()

        # No ImageCompressionCache should be created, and no resized dir written.
        assert dm._image_cache is None
        assert not (tmp_path / "resized").exists()
        # The composite is still produced from the raw image.
        composite_dir = tmp_path / "composite"
        assert composite_dir.is_dir()
        composite_files = list(composite_dir.glob("_composite_*.png"))
        assert composite_files, "no composite file written"
        composite = Image.open(composite_files[0])
        assert composite.size == (50, 50)
        assert composite.getpixel((25, 25)) == (200, 100, 50)


_FULL_HD_CAPABILITY = DisplayCapability(
    scale=(100, 125, 150),
    reference_scale=100,
    resolution=((1920, 1080), (1280, 720)),
)


def _add_with_capability(dm: DisplayManager) -> tuple[DisplayId, DisplayInfo]:
    """Add *_DEVICE_A* with a LUID/adapter identity and a cached capability."""
    info = replace(
        _make_display(_DEVICE_A, 0, 0, 1920, 1080),
        adapter_id=LUID(1, 2),
        source_id=3,
    )
    with (
        patch(
            "wallpaper_auto.display_manager.get_display_capability",
            return_value=_FULL_HD_CAPABILITY,
        ),
        patch(
            "wallpaper_auto.display_manager.get_display_resolution",
            return_value=(1920, 1080),
        ),
        patch(
            "wallpaper_auto.display_manager.get_display_scale",
            return_value=100,
        ),
        patch(
            "wallpaper_auto.display_manager.get_wallpaper_style",
            return_value=WallpaperStyle.FILL,
        ),
        patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
    ):
        return dm.add_display(info), info


class TestDisplayManagerResolution:
    """Per-display resolution setters, snapping, and restore."""

    def test_add_display_records_metadata_and_caches_capability(self):
        dm = DisplayManager()
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_capability",
                return_value=_FULL_HD_CAPABILITY,
            ) as mock_cap,
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=(1920, 1080),
            ),
            patch(
                "wallpaper_auto.display_manager.get_display_scale",
                return_value=100,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch("wallpaper_auto.display_manager.get_wallpaper", return_value=Path("C:/orig.jpg")),
        ):
            did = dm.add_display(
                replace(
                    _make_display(_DEVICE_A, 0, 0, 1920, 1080),
                    adapter_id=LUID(1, 2),
                    source_id=3,
                )
            )
        mock_cap.assert_called_once()
        assert (
            did
            == replace(
                _make_display(_DEVICE_A, 0, 0, 1920, 1080),
                adapter_id=LUID(1, 2),
                source_id=3,
            ).display_id
        )
        state = dm._displays[did]
        assert state.display_info.device_name == "\\\\.\\DISPLAY1"
        assert (state.display_info.adapter_id.LowPart, state.display_info.adapter_id.HighPart) == (
            1,
            2,
        )
        assert state.display_info.source_id == 3
        assert state.capability == _FULL_HD_CAPABILITY
        assert state.original_resolution == (1920, 1080)
        assert state.original_scale == 100
        assert state.current_resolution is None
        assert state.current_scale is None

    def test_set_display_resolution_snaps_and_calls_setter(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        with patch(
            "wallpaper_auto.display_manager.set_display_resolution", return_value=True
        ) as mock_res:
            assert dm.set_display_resolution(did, 1921, 1080) is True
            assert dm.set_display_resolution(did, 1920, 1080) is True  # idempotent
        mock_res.assert_called_once_with("\\\\.\\DISPLAY1", 1920, 1080)
        assert dm._displays[did].current_resolution == (1920, 1080)

    def test_set_display_resolution_unknown_display_raises_key_error(self):
        """An unknown display_id raises KeyError rather than returning False."""
        dm = DisplayManager()
        with pytest.raises(KeyError):
            dm.set_display_resolution(DisplayId(999999), 800, 600)

    def test_remove_connected_display_restores_resolution(self):
        dm = DisplayManager()
        did, info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch(
            "wallpaper_auto.display_manager.set_display_resolution", return_value=True
        ) as mock_res:
            assert dm.set_display_resolution(did, 800, 600) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[info]),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
            patch(
                "wallpaper_auto.display_manager.set_display_resolution", return_value=True
            ) as mock_res,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(did)
        # Resolution reverted to the original 1920x1080.
        assert mock_res.call_args.args == ("\\\\.\\DISPLAY1", 1920, 1080)
        assert mock_res.call_args.kwargs == {}
        state = dm._displays[did]
        assert state.current_resolution is None

    def test_remove_unplugged_display_does_not_restore_resolution(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch("wallpaper_auto.display_manager.set_display_resolution", return_value=True):
            assert dm.set_display_resolution(did, 800, 600) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[]),
            patch("wallpaper_auto.display_manager.set_display_resolution") as mock_res,
        ):
            dm.remove_display(did)
        assert did not in dm._displays
        mock_res.assert_not_called()

    def test_stop_restores_resolution(self):
        dm = DisplayManager()
        did, info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch("wallpaper_auto.display_manager.set_display_resolution", return_value=True):
            assert dm.set_display_resolution(did, 800, 600) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[info]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
            patch(
                "wallpaper_auto.display_manager.set_display_resolution", return_value=True
            ) as mock_res,
        ):
            _mock_com_session(mock_session)
            mock_static.return_value = _make_resource()
            dm.stop()
        assert mock_res.call_args.args == ("\\\\.\\DISPLAY1", 1920, 1080)

    def test_restore_resolution_is_idempotent(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        state = dm._displays[did]
        with patch(
            "wallpaper_auto.display_manager.set_display_resolution", return_value=True
        ) as mock_res:
            assert dm.set_display_resolution(did, 800, 600) is True
            assert mock_res.call_count == 1
            dm._restore_resolution(state)
            assert mock_res.call_count == 2
            assert state.current_resolution is None
            dm._restore_resolution(state)
            assert mock_res.call_count == 2  # second pass is a no-op

    def test_restore_resolution_keeps_target_on_failure(self):
        """A failed restore keeps current_resolution so a later pass retries."""
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        state = dm._displays[did]
        with patch("wallpaper_auto.display_manager.set_display_resolution", return_value=True):
            assert dm.set_display_resolution(did, 800, 600) is True
        assert state.current_resolution is not None
        with patch("wallpaper_auto.display_manager.set_display_resolution", return_value=False):
            dm._restore_resolution(state)
        assert state.current_resolution is not None  # kept for a later retry


class TestDisplayManagerScale:
    """Per-display DPI scale setters, snapping, and restore."""

    def test_set_display_scale_snaps_and_computes_step(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        with patch(
            "wallpaper_auto.display_manager.set_display_scale", return_value=True
        ) as mock_scale:
            assert dm.set_display_scale(did, 140) is True
        args = mock_scale.call_args.args
        assert args[0] == "\\\\.\\DISPLAY1"
        assert (args[1].LowPart, args[1].HighPart) == (1, 2)
        assert args[2] == 3
        assert args[3] == 2  # 100% → 150% is +2 relative steps
        assert dm._displays[did].current_scale == 150

    def test_set_display_scale_idempotent(self):
        """Re-applying the current target scale returns True without a setter call."""
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        with patch(
            "wallpaper_auto.display_manager.set_display_scale", return_value=True
        ) as mock_scale:
            assert dm.set_display_scale(did, 150) is True
            assert mock_scale.call_count == 1
            assert dm.set_display_scale(did, 150) is True  # idempotent short-circuit
            assert mock_scale.call_count == 1

    def test_set_display_scale_resolves_relative_to_reference(self):
        cap = DisplayCapability(
            scale=(100, 125, 150), reference_scale=125, resolution=((1920, 1080),)
        )
        dm = DisplayManager()
        info = replace(
            _make_display(_DEVICE_A, 0, 0, 1920, 1080),
            adapter_id=LUID(1, 2),
            source_id=3,
        )
        with (
            patch(
                "wallpaper_auto.display_manager.get_display_resolution",
                return_value=info.source_resolution,
            ),
            patch("wallpaper_auto.display_manager.get_display_scale", return_value=info.scale),
            patch("wallpaper_auto.display_manager.get_display_capability", return_value=cap),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper_style",
                return_value=WallpaperStyle.FILL,
            ),
            patch(
                "wallpaper_auto.display_manager.get_wallpaper",
                return_value=Path("C:/orig.jpg"),
            ),
        ):
            did = dm.add_display(info)
        with patch(
            "wallpaper_auto.display_manager.set_display_scale", return_value=True
        ) as mock_scale:
            assert dm.set_display_scale(did, 150) is True
            assert dm.set_display_scale(did, 100) is True
        args = mock_scale.call_args_list
        # 150 is +1 step from the 125% reference; 100 is -1 step.
        assert args[0].args[3] == 1
        assert args[1].args[3] == -1
        assert dm._displays[did].current_scale == 100  # last applied

    def test_remove_connected_display_restores_scale(self):
        dm = DisplayManager()
        did, info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch(
            "wallpaper_auto.display_manager.set_display_scale", return_value=True
        ) as mock_scale:
            assert dm.set_display_scale(did, 200) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[info]),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
            patch(
                "wallpaper_auto.display_manager.set_display_scale", return_value=True
            ) as mock_scale,
        ):
            mock_static.return_value = _make_resource()
            dm.remove_display(did)
        # Scale back to reference (0 relative step).
        scale_args = mock_scale.call_args.args
        assert scale_args[0] == "\\\\.\\DISPLAY1"
        assert (scale_args[1].LowPart, scale_args[1].HighPart) == (1, 2)
        assert scale_args[2] == 3
        assert scale_args[3] == 0
        state = dm._displays[did]
        assert state.current_scale is None

    def test_remove_unplugged_display_does_not_restore_scale(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch("wallpaper_auto.display_manager.set_display_scale", return_value=True):
            assert dm.set_display_scale(did, 200) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[]),
            patch("wallpaper_auto.display_manager.set_display_scale") as mock_scale,
        ):
            dm.remove_display(did)
        assert did not in dm._displays
        mock_scale.assert_not_called()

    def test_stop_restores_scale(self):
        dm = DisplayManager()
        did, info = _add_with_capability(dm)
        dm.update_resource(did, _make_resource())
        with patch("wallpaper_auto.display_manager.set_display_scale", return_value=True):
            assert dm.set_display_scale(did, 200) is True
        with (
            patch("wallpaper_auto.display_manager.get_display_info", return_value=[info]),
            patch("wallpaper_auto.display_manager.com_session") as mock_session,
            patch("wallpaper_auto.display_manager.set_wallpaper"),
            patch("wallpaper_auto.display_manager.set_wallpaper_style"),
            patch("wallpaper_auto.display_manager.StaticWallpaper") as mock_static,
            patch(
                "wallpaper_auto.display_manager.set_display_scale", return_value=True
            ) as mock_scale,
        ):
            _mock_com_session(mock_session)
            mock_static.return_value = _make_resource()
            dm.stop()
        scale_args = mock_scale.call_args.args
        assert scale_args[0] == "\\\\.\\DISPLAY1"
        assert (scale_args[1].LowPart, scale_args[1].HighPart) == (1, 2)
        assert scale_args[2] == 3
        assert scale_args[3] == 0

    def test_restore_scale_is_idempotent(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        state = dm._displays[did]
        with patch(
            "wallpaper_auto.display_manager.set_display_scale", return_value=True
        ) as mock_scale:
            assert dm.set_display_scale(did, 200) is True
            assert mock_scale.call_count == 1
            dm._restore_scale(state)
            assert mock_scale.call_count == 2
            assert state.current_scale is None
            dm._restore_scale(state)
            assert mock_scale.call_count == 2  # second pass is a no-op

    def test_restore_scale_keeps_target_on_failure(self):
        """A failed restore keeps current_scale so a later pass retries."""
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        state = dm._displays[did]
        with patch("wallpaper_auto.display_manager.set_display_scale", return_value=True):
            assert dm.set_display_scale(did, 150) is True
        assert state.current_scale is not None
        with patch("wallpaper_auto.display_manager.set_display_scale", return_value=False):
            dm._restore_scale(state)
        assert state.current_scale is not None  # kept for a later retry


class TestDisplayManagerScene:
    """update_display_scene records res/scale + mounts; apply_display_scene applies + plots."""

    def test_update_display_scene_records_and_mounts_resource(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        resource = _make_resource()
        with (
            patch("wallpaper_auto.display_manager.set_display_resolution") as mock_res,
            patch("wallpaper_auto.display_manager.set_display_scale") as mock_scale,
        ):
            dm.update_display_scene(did, resource, (1920, 1080), 150)
        mock_res.assert_not_called()
        mock_scale.assert_not_called()
        assert dm._displays[did].pending == ((1920, 1080), 150)
        resource.mount.assert_called_once()

    def test_apply_display_scene_sets_resolution_scale_and_plots(self):
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        resource = _make_resource()
        dm.update_display_scene(did, resource, (1920, 1080), 150)
        with (
            patch.object(dm, "plot_canvas") as mock_plot,
            patch("wallpaper_auto.display_manager.set_display_resolution", return_value=True),
            patch("wallpaper_auto.display_manager.set_display_scale", return_value=True),
        ):
            dm.apply_display_scene()
        mock_plot.assert_called_once()
        assert dm._displays[did].pending is None
        assert dm._displays[did].current_resolution == (1920, 1080)
        assert dm._displays[did].current_scale == 150

    def test_apply_display_scene_restores_original_when_none(self):
        """A recorded (None, None) scene reverts res/scale to the originals."""
        dm = DisplayManager()
        did, _info = _add_with_capability(dm)
        resource = _make_resource()
        # First apply a non-original resolution/scale.
        with patch.object(dm, "plot_canvas"):
            dm.update_display_scene(did, resource, (1280, 720), 125)
            with (
                patch("wallpaper_auto.display_manager.set_display_resolution", return_value=True),
                patch("wallpaper_auto.display_manager.set_display_scale", return_value=True),
            ):
                dm.apply_display_scene()
        assert dm._displays[did].current_resolution == (1280, 720)
        assert dm._displays[did].current_scale == 125
        # Then a scene that restores both to their originals (1920x1080 @100).
        with (
            patch.object(dm, "plot_canvas"),
            patch(
                "wallpaper_auto.display_manager.set_display_resolution",
                return_value=True,
            ) as mock_res,
            patch(
                "wallpaper_auto.display_manager.set_display_scale",
                return_value=True,
            ) as mock_scale,
        ):
            dm.update_display_scene(did, resource, None, None)
            dm.apply_display_scene()
        mock_res.assert_called_once_with("\\\\.\\DISPLAY1", 1920, 1080)
        mock_scale.assert_called_once()  # relative step 0 back to the reference scale
        assert dm._displays[did].current_resolution is None
        assert dm._displays[did].current_scale is None

    def test_apply_display_scene_with_no_pending_still_plots(self):
        """apply_display_scene always ends with plot_canvas, even with nothing pending."""
        dm = DisplayManager()
        with patch.object(dm, "plot_canvas") as mock_plot:
            dm.apply_display_scene()
        mock_plot.assert_called_once()
