"""Tests for base_resource.py — BaseResource abstract class."""

from pathlib import Path

import pytest
from PIL import Image

from wallpaper_auto.resource.base_resource import (
    BaseResource,
    PlotCanvasProtocol,
)
from wallpaper_auto.util.wallpaper_util import WallpaperStyle


def _noop_canvas(
    monitor_device_path: str,
    style: WallpaperStyle,
    image: Path | Image.Image,
    immediate_update: bool = False,
) -> None:
    """Canvas stub that does nothing — matches PlotCanvasProtocol exactly."""


class MockResource(BaseResource):
    """Concrete BaseResource subclass for testing abstract methods."""

    def mount(self) -> None:
        pass

    def demount(self) -> None:
        pass


class TestMockResource:
    """MockResource fulfils the BaseResource contract."""

    def test_mount_exists(self) -> None:
        """mount() is callable and does not raise."""
        MockResource().mount()

    def test_demount_exists(self) -> None:
        """demount() is callable and does not raise."""
        MockResource().demount()

    def test_is_concrete(self) -> None:
        """MockResource can be instantiated (abstract methods are implemented)."""
        MockResource()


class TestInitState:
    """Initial state after construction."""

    def test_monitor_path_is_none(self) -> None:
        assert MockResource().monitor_device_path is None

    def test_plot_canvas_is_none(self) -> None:
        assert MockResource()._plot_canvas is None


class TestBindMonitorDevicePath:
    def test_bind_sets_path(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        assert r.monitor_device_path == "MONITOR\\1"

    def test_rebind_raises(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="monitor_device_path already bound"):
            r._bind_monitor_device_path("MONITOR\\2")

    def test_bind_none_path(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        r.monitor_device_path = None
        # After resetting manually, a new bind succeeds
        r._bind_monitor_device_path("MONITOR\\2")
        assert r.monitor_device_path == "MONITOR\\2"


class TestBindPlotCanvas:
    def test_bind_sets_canvas(self) -> None:
        r = MockResource()
        canvas: PlotCanvasProtocol = _noop_canvas
        r._bind_plot_canvas(canvas)
        assert r._plot_canvas is canvas

    def test_rebind_raises(self) -> None:
        r = MockResource()
        r._bind_plot_canvas(_noop_canvas)
        with pytest.raises(RuntimeError, match="plot_canvas already bound"):
            r._bind_plot_canvas(_noop_canvas)


class TestBindUnbindCycle:
    """Bind → unbind → rebind cycle for plot_canvas."""

    def test_bind_unbind_rebind(self) -> None:
        r = MockResource()

        def _c1(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            return None

        def _c2(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            return None

        r._bind_plot_canvas(_c1)
        assert r._plot_canvas is _c1
        r._unbind_plot_canvas()
        assert r._plot_canvas is None

        r._bind_plot_canvas(_c2)
        assert r._plot_canvas is _c2
        r._unbind_plot_canvas()
        assert r._plot_canvas is None


class TestUnbindPlotCanvas:
    def test_unbind_clears_canvas(self) -> None:
        r = MockResource()
        r._bind_plot_canvas(_noop_canvas)
        r._unbind_plot_canvas()
        assert r._plot_canvas is None

    def test_unbind_without_bind_raises(self) -> None:
        r = MockResource()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r._unbind_plot_canvas()

    def test_double_unbind_raises(self) -> None:
        r = MockResource()
        r._bind_plot_canvas(_noop_canvas)
        r._unbind_plot_canvas()
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r._unbind_plot_canvas()


class TestPlotCanvas:
    def test_dispatches_to_bound_canvas(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured: list[tuple[object, ...]] = []

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            captured.append((monitor_device_path, style, image, immediate_update))

        r._bind_plot_canvas(canvas)
        img = Image.new("RGB", (4, 4))
        r.plot_canvas(WallpaperStyle.FILL, img, immediate_update=True)

        assert captured == [("MONITOR\\1", WallpaperStyle.FILL, img, True)]

    def test_default_immediate_update_is_false(self) -> None:
        """immediate_update defaults to False when omitted."""
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured: list[bool] = []

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            captured.append(immediate_update)

        r._bind_plot_canvas(canvas)
        r.plot_canvas(WallpaperStyle.FIT, Image.new("RGB", (4, 4)))
        assert captured == [False]

    def test_with_path_image(self) -> None:
        """plot_canvas accepts a pathlib.Path as image argument."""
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        captured: list[object] = []

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            captured.append(image)

        r._bind_plot_canvas(canvas)
        path = Path("/tmp/wallpaper.png")
        r.plot_canvas(WallpaperStyle.STRETCH, path)
        assert captured == [path]
        assert isinstance(captured[0], Path)

    def test_propagates_canvas_exception(self) -> None:
        """Exceptions from the canvas callable propagate to the caller."""
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")

        def canvas(
            monitor_device_path: str,
            style: WallpaperStyle,
            image: Path | Image.Image,
            immediate_update: bool = False,
        ) -> None:
            raise RuntimeError("canvas failure")

        r._bind_plot_canvas(canvas)
        with pytest.raises(RuntimeError, match="canvas failure"):
            r.plot_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_canvas_unbound(self) -> None:
        r = MockResource()
        r._bind_monitor_device_path("MONITOR\\1")
        with pytest.raises(RuntimeError, match="plot_canvas not bound"):
            r.plot_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))

    def test_raises_when_monitor_path_unbound(self) -> None:
        r = MockResource()
        r._bind_plot_canvas(_noop_canvas)
        with pytest.raises(RuntimeError, match="monitor_device_path not bound"):
            r.plot_canvas(WallpaperStyle.FILL, Image.new("RGB", (4, 4)))
